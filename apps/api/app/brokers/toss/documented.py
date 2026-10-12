"""Verified official Toss Open API: OAuth and market-data reads only.

Paths and response schemas are copied from the current official OpenAPI
document. Tokens remain in this process; issuing a new token revokes the
previous token for the same client, so this client must be shared by callers.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date as Date, datetime, time as Time, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ...settings import runtime_dir
from ..base import BrokerUnavailable

OFFICIAL_ORIGIN = "https://openapi.tossinvest.com"
VERIFIED_DOCUMENT_VERSIONS = frozenset({"1.2.19", "1.2.24"})
CONTRACT_PATH = Path(__file__).resolve().parents[5] / "config" / "toss-openapi-contract.json"
FAQ_URL = "https://openapi.tossinvest.com/openapi-docs/faq.md"
_SYMBOL = re.compile(r"^[A-Za-z0-9.\-]+$")
_SAFE_CODES = frozenset({
    "invalid_request", "invalid_client", "invalid_grant", "unauthorized_client",
    "unsupported_grant_type", "access_denied", "server_error", "temporarily_unavailable",
    "token-revoked", "token-expired", "stock-not-found", "rate-limit-exceeded",
})
_READ_PATHS = frozenset({
    "/api/v1/prices", "/api/v1/candles", "/api/v1/stocks", "/api/v1/rankings",
    "/api/v1/market-calendar/KR", "/api/v1/market-calendar/US",
    "/api/v1/market-indicators/prices",
})
_DYNAMIC_READ_PATH = re.compile(
    r"^/api/v1/(?:stocks/[A-Za-z0-9.\-]+/(?:investor-trading|program-trades)"
    r"|market-indicators/(?:KOSPI|KOSDAQ)/candles)$")


class TossAPIError(BrokerUnavailable):
    """Only fixed, sanitized fields may leave the HTTP boundary."""

    def __init__(self, condition: str, http_status: int | None = None,
                 error_code: str | None = None, schema_status: str = "not_checked") -> None:
        self.condition = condition
        self.http_status = http_status
        self.error_code = error_code if error_code in _SAFE_CODES else None
        self.schema_status = schema_status
        super().__init__("Toss 읽기 전용 API 확인 실패: " + condition)

    def safe_dict(self) -> dict[str, Any]:
        return {"status": "failed", "condition": self.condition, "http_status": self.http_status,
                "error_code": self.error_code, "schema_status": self.schema_status}


class ContractMismatch(ValueError):
    """A fixed message avoids exposing a rejected upstream value."""

    def __init__(self) -> None:
        super().__init__("Official response contract mismatch")


def validate_official_response(value: Any, schema: dict[str, Any], contract: dict[str, Any]) -> None:
    """Validate the JSON Schema subset used by the saved OpenAPI 3.1 contract."""
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/components/schemas/"):
            raise ContractMismatch()
        try:
            target = contract["components"]["schemas"][ref.split("/")[-1]]
        except (KeyError, TypeError):
            raise ContractMismatch() from None
        validate_official_response(value, target, contract)
    for branch in schema.get("allOf", []):
        validate_official_response(value, branch, contract)
    for keyword in ("oneOf", "anyOf"):
        if keyword in schema:
            matches = 0
            for branch in schema[keyword]:
                try:
                    validate_official_response(value, branch, contract)
                    matches += 1
                except ContractMismatch:
                    pass
            if (keyword == "oneOf" and matches != 1) or (keyword == "anyOf" and matches == 0):
                raise ContractMismatch()
    expected = schema.get("type")
    kinds = expected if isinstance(expected, list) else [expected] if expected else []
    if schema.get("nullable") and "null" not in kinds:
        kinds.append("null")
    def is_kind(kind: str) -> bool:
        return {
            "object": isinstance(value, dict), "array": isinstance(value, list),
            "string": isinstance(value, str), "boolean": isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool)
                      and (isinstance(value, int) or math.isfinite(value)),
            "null": value is None,
        }.get(kind, False)
    if kinds and not any(is_kind(kind) for kind in kinds):
        raise ContractMismatch()
    if "enum" in schema and value not in schema["enum"]:
        raise ContractMismatch()
    if "const" in schema and value != schema["const"]:
        raise ContractMismatch()
    if isinstance(value, dict):
        if any(key not in value for key in schema.get("required", [])):
            raise ContractMismatch()
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                validate_official_response(item, properties[key], contract)
            elif schema.get("additionalProperties") is False:
                raise ContractMismatch()
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_official_response(item, schema["additionalProperties"], contract)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            raise ContractMismatch()
        for item in value:
            validate_official_response(item, schema.get("items", {}), contract)
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", math.inf):
            raise ContractMismatch()
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ContractMismatch()
        try:
            if schema.get("format") == "decimal":
                if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value) or not Decimal(value).is_finite():
                    raise ContractMismatch()
            elif schema.get("format") == "date-time":
                if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
                    raise ContractMismatch()
            elif schema.get("format") == "date":
                if Date.fromisoformat(value).isoformat() != value:
                    raise ContractMismatch()
        except (ValueError, InvalidOperation, OverflowError):
            raise ContractMismatch() from None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ContractMismatch()
        if schema.get("format") in ("int32", "int64"):
            bits = 32 if schema["format"] == "int32" else 64
            if not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
                raise ContractMismatch()
        for key, fail in (("minimum", lambda x: value < x), ("maximum", lambda x: value > x),
                          ("exclusiveMinimum", lambda x: value <= x), ("exclusiveMaximum", lambda x: value >= x)):
            if key in schema and fail(schema[key]):
                raise ContractMismatch()


def _safe_http_error(response: httpx.Response) -> TossAPIError:
    code = None
    condition = "http_error"
    try:
        body = response.json()
        if isinstance(body, dict):
            candidate = body.get("error")
            if isinstance(candidate, dict):
                candidate = candidate.get("code")
            if isinstance(candidate, str) and candidate in _SAFE_CODES:
                code = candidate
            # Do not retain or quote descriptions; match only the known fixed phrase.
            if response.status_code == 403 and body.get("error_description") == "IP address not allowed":
                condition = "ip_not_allowed"
    except (ValueError, TypeError):
        pass
    return TossAPIError(condition, response.status_code, code)


class OfficialTossClient:
    """A single in-memory OAuth owner; no account or order endpoint is exposed."""

    def __init__(self, env: Mapping[str, str] | None = None,
                 client: httpx.AsyncClient | None = None, contract_path: Path | None = None,
                 max_http_calls: int | None = None, probe_retry_budget: int | None = None) -> None:
        source = os.environ if env is None else env
        self._credentials = {key: source.get(key, "") for key in ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET")}
        self.client = client or httpx.AsyncClient(timeout=20.0, verify=True, follow_redirects=False)
        self._owns_client = client is None
        try:
            self.contract = json.loads((contract_path or CONTRACT_PATH).read_text(encoding="utf-8"))
            if (self.contract["origin"] != OFFICIAL_ORIGIN
                    or self.contract["document_version"] not in VERIFIED_DOCUMENT_VERSIONS):
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError):
            raise TossAPIError("official_contract_unavailable") from None
        self._token = ""
        self._expires_at = 0.0
        self._auth_lock = asyncio.Lock()
        self._auth_failure: TossAPIError | None = None
        self.auth_status: dict[str, Any] = {"status": "not_checked"}
        self.calls = 0
        self.max_http_calls = max_http_calls
        self._probe_retries = probe_retry_budget

    @property
    def credentials_present(self) -> dict[str, bool]:
        return {name: bool(value) for name, value in self._credentials.items()}

    def reset_auth_failure(self) -> None:
        """Permit an explicitly requested retry after an IP/configuration fix.

        This clears only the prior failure latch; it does not issue or discard
        a valid token. Callers should invoke it after a meaningful state change.
        """
        self._auth_failure = None
        if not self._token:
            self.auth_status = {"status": "not_checked"}

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        allowed = (method == "POST" and path == "/oauth2/token") or (
            method == "GET" and (path in _READ_PATHS or _DYNAMIC_READ_PATH.fullmatch(path) is not None))
        if not allowed or any(x in path for x in ("..", "?", "#")):
            raise TossAPIError("unsupported_endpoint")
        for attempt in range(2):
            if self.max_http_calls is not None and self.calls >= self.max_http_calls:
                raise TossAPIError("call_budget")
            self.calls += 1
            try:
                response = await self.client.request(method, OFFICIAL_ORIGIN + path,
                                                     follow_redirects=False, **kwargs)
            except httpx.HTTPError:
                raise TossAPIError("network_error") from None
            if response.status_code != 429 or attempt or self._probe_retries == 0:
                return response
            if self._probe_retries is not None:
                self._probe_retries -= 1
            try:
                delay = float(response.headers.get("Retry-After", "1"))
                delay = max(0.0, min(5.0, delay)) if math.isfinite(delay) else 1.0
            except ValueError:
                delay = 1.0
            await asyncio.sleep(delay)
        raise TossAPIError("rate_limit")

    def _validate(self, body: Any, path: str) -> None:
        try:
            validate_official_response(body, self.contract["responses"][path], self.contract)
        except (ContractMismatch, KeyError, TypeError):
            raise TossAPIError("schema_mismatch", 200, schema_status="failed") from None

    async def authenticate(self) -> dict[str, Any]:
        async with self._auth_lock:
            await self._authenticate_locked()
        return dict(self.auth_status)

    async def _authenticate_locked(self) -> None:
        if self._auth_failure:
            raise self._auth_failure
        if self._token and time.monotonic() < self._expires_at:
            return
        if not all(self.credentials_present.values()):
            self._auth_failure = TossAPIError("missing_credentials")
            self.auth_status = self._auth_failure.safe_dict()
            raise self._auth_failure
        try:
            response = await self._send("POST", "/oauth2/token", data={
                "grant_type": "client_credentials", "client_id": self._credentials["TOSS_CLIENT_ID"],
                "client_secret": self._credentials["TOSS_CLIENT_SECRET"],
            })
            if response.status_code != 200:
                raise _safe_http_error(response)
            try:
                body = response.json()
            except ValueError:
                raise TossAPIError("schema_mismatch", 200, schema_status="failed") from None
            self._validate(body, "/oauth2/token")
            if not body["access_token"] or body["expires_in"] <= 0:
                raise TossAPIError("schema_mismatch", 200, schema_status="failed")
            self._token = body["access_token"]
            lifetime = body["expires_in"]
            self._expires_at = time.monotonic() + lifetime - min(30.0, lifetime / 10)
            self.auth_status = {"status": "passed", "http_status": 200, "schema_status": "passed"}
        except TossAPIError as error:
            # A failed OAuth request, especially IP 403, is not retried unchanged by other calls.
            self._auth_failure = error
            self.auth_status = error.safe_dict()
            raise

    async def _renew_after_401(self, stale_token: str) -> None:
        async with self._auth_lock:
            if self._token == stale_token:
                self._token = ""
                self._expires_at = 0.0
            await self._authenticate_locked()

    async def _get(self, path: str, params: dict[str, Any], contract_path: str | None = None) -> Any:
        schema_path = contract_path or path
        if schema_path not in self.contract["responses"] or schema_path == "/oauth2/token":
            raise TossAPIError("unsupported_endpoint")
        await self.authenticate()
        for attempt in range(2):
            token = self._token
            response = await self._send("GET", path, params=params, headers={"Authorization": "Bearer " + token})
            if response.status_code == 401 and attempt == 0:
                await self._renew_after_401(token)
                continue
            if response.status_code != 200:
                raise _safe_http_error(response)
            try:
                body = response.json()
            except ValueError:
                raise TossAPIError("schema_mismatch", 200, schema_status="failed") from None
            self._validate(body, schema_path)
            return body["result"]
        raise TossAPIError("authentication_rejected", 401)

    @staticmethod
    def _symbols(symbols: Sequence[str]) -> str:
        if isinstance(symbols, str) or not symbols or len(symbols) > 100 or any(
                not isinstance(s, str) or _SYMBOL.fullmatch(s) is None for s in symbols):
            raise TossAPIError("invalid_symbols")
        return ",".join(symbols)

    @staticmethod
    def _count(count: int, maximum: int) -> int:
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= maximum:
            raise TossAPIError("invalid_count")
        return count

    @staticmethod
    def _market(market: str) -> str:
        if market not in ("KR", "US"):
            raise TossAPIError("invalid_market")
        return market

    @staticmethod
    def _before(before: str | None) -> dict[str, str]:
        if before is None:
            return {}
        try:
            if not isinstance(before, str) or datetime.fromisoformat(before.replace("Z", "+00:00")).tzinfo is None:
                raise ValueError()
        except ValueError:
            raise TossAPIError("invalid_before") from None
        return {"before": before}

    async def prices(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        return await self._get("/api/v1/prices", {"symbols": self._symbols(symbols)})

    async def candles(self, symbol: str, interval: str, count: int = 200,
                      before: str | None = None) -> list[dict[str, Any]]:
        if interval not in ("1m", "1d"):
            raise TossAPIError("invalid_interval")
        result = await self._get("/api/v1/candles", {"symbol": self._symbols([symbol]), "interval": interval,
                                 "count": self._count(count, 200), "adjusted": "true", **self._before(before)})
        return result["candles"]

    async def stock_master(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        return await self._get("/api/v1/stocks", {"symbols": self._symbols(symbols)})

    async def rankings(self, market: str = "KR", count: int = 100) -> list[dict[str, Any]]:
        result = await self.rankings_snapshot(market, count)
        return result["rankings"]

    async def rankings_snapshot(self, market: str = "KR", count: int = 100) -> dict[str, Any]:
        """Return validated rows plus the provider's nullable rankedAt time."""
        result = await self._get("/api/v1/rankings", {"type": "MARKET_TRADING_AMOUNT",
                                "marketCountry": self._market(market), "duration": "1d",
                                "count": self._count(count, 100)})
        # Preserve the official as-of time for exact interval-turnover analysis.
        # A receipt timestamp must never replace nullable rankedAt.
        self.last_ranking_result = result
        return result

    async def _symbol_records(self, symbol: str, endpoint: str, count: int, until: str | None) -> list[dict[str, Any]]:
        symbol = self._symbols([symbol])
        params: dict[str, Any] = {"count": self._count(count, 100)}
        if until is not None:
            try:
                if not isinstance(until, str) or Date.fromisoformat(until).isoformat() != until:
                    raise ValueError()
            except ValueError:
                raise TossAPIError("invalid_until") from None
            params["until"] = until
        result = await self._get(f"/api/v1/stocks/{symbol}/{endpoint}", params,
                                 f"/api/v1/stocks/{{symbol}}/{endpoint}")
        return result["records"]

    async def investor(self, symbol: str, count: int = 100, until: str | None = None) -> list[dict[str, Any]]:
        return await self._symbol_records(symbol, "investor-trading", count, until)

    async def program(self, symbol: str, count: int = 100, until: str | None = None) -> list[dict[str, Any]]:
        return await self._symbol_records(symbol, "program-trades", count, until)

    async def calendar(self, market: str = "KR", date: str | None = None) -> dict[str, Any]:
        params = {}
        if date is not None:
            try:
                if not isinstance(date, str) or Date.fromisoformat(date).isoformat() != date:
                    raise ValueError()
            except ValueError:
                raise TossAPIError("invalid_date") from None
            params["date"] = date
        return await self._get("/api/v1/market-calendar/" + self._market(market), params)

    async def index_prices(self, symbols: Sequence[str] = ("KOSPI", "KOSDAQ")) -> list[dict[str, Any]]:
        if not symbols or any(s not in ("KOSPI", "KOSDAQ") for s in symbols):
            raise TossAPIError("invalid_index")
        return await self._get("/api/v1/market-indicators/prices", {"symbols": ",".join(symbols)})

    async def index_candles(self, symbol: str, interval: str = "1d", count: int = 200,
                            before: str | None = None) -> list[dict[str, Any]]:
        if symbol not in ("KOSPI", "KOSDAQ"):
            raise TossAPIError("invalid_index")
        if interval not in ("1m", "1d"):
            raise TossAPIError("invalid_interval")
        result = await self._get(f"/api/v1/market-indicators/{symbol}/candles",
                                 {"interval": interval, "count": self._count(count, 200), **self._before(before)},
                                 "/api/v1/market-indicators/{symbol}/candles")
        return result["candles"]

    # Explicit aliases used by the normalized market-data adapter.
    get_prices = prices
    get_candles = candles
    get_stock_master = stock_master
    get_rankings = rankings
    get_investor_trading = investor
    get_program_trades = program
    get_market_calendar = calendar
    get_index_prices = index_prices
    get_index_candles = index_candles

    async def aclose(self) -> None:
        self._token = ""
        self._expires_at = 0.0
        if self._owns_client:
            await self.client.aclose()


DocumentedTossClient = OfficialTossClient


@dataclass
class ProbeReport:
    document_version: str = "1.2.24"
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    authentication: dict[str, Any] = field(default_factory=lambda: {"status": "not_checked"})
    samples: dict[str, Any] = field(default_factory=dict)
    calls: int = 0
    credentials_present: dict[str, bool] = field(default_factory=dict)
    live_orders_enabled: bool = False
    venue: dict[str, Any] = field(default_factory=lambda: {
        "verified": False, "includes_nxt": False, "price_basis": "unknown",
        "daily_candle_basis": "unknown", "evidence": {},
    })

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def save_probe(report: ProbeReport) -> Path:
    target = runtime_dir() / "toss-probe.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    venue = runtime_dir() / "toss-venue-evidence.json"
    temporary = venue.with_suffix(".tmp")
    temporary.write_text(json.dumps(report.venue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(venue)
    return target


async def run_probe(client: OfficialTossClient) -> ProbeReport:
    """At most 12 HTTP calls, one 429 retry, and no repeated failed OAuth."""
    previous_limit, previous_retries = client.max_http_calls, client._probe_retries
    client.max_http_calls = min(previous_limit, client.calls + 12) if previous_limit is not None else client.calls + 12
    client._probe_retries = 1
    try:
        return await _probe_samples(client)
    finally:
        client.max_http_calls, client._probe_retries = previous_limit, previous_retries


async def _probe_samples(client: OfficialTossClient) -> ProbeReport:
    starting_calls = client.calls
    report = ProbeReport(document_version=client.contract["document_version"],
                         credentials_present=client.credentials_present)
    try:
        report.authentication = await client.authenticate()
    except TossAPIError as error:
        report.authentication = error.safe_dict()
        report.calls = client.calls - starting_calls
        return report

    async def sample(name: str, operation: Any) -> Any:
        try:
            value = await operation()
            report.samples[name] = {"status": "passed", "http_status": 200, "schema_status": "passed",
                                    "count": len(value) if isinstance(value, list) else None}
            return value
        except TossAPIError as error:
            report.samples[name] = error.safe_dict()
            return None

    quote = await sample("kr_prices", lambda: client.prices(["005930"]))
    daily = await sample("daily_candles", lambda: client.candles("005930", "1d", 30))
    await sample("minute_candles", lambda: client.candles("005930", "1m", 200))
    await sample("kr_rankings", lambda: client.rankings("KR", 100))
    calendar = await sample("kr_calendar", lambda: client.calendar("KR"))
    await sample("investor", lambda: client.investor("005930", 10))
    await sample("program", lambda: client.program("005930", 10))
    await sample("us_prices", lambda: client.prices(["NVDA", "AAPL"]))
    master = await sample("stock_master", lambda: client.stock_master(["005930"]))
    if calendar:
        def session(day: dict[str, Any], name: str) -> dict[str, Any] | None:
            return (day.get("integrated") or {}).get(name)
        pre_day = calendar["today"] if session(calendar["today"], "preMarket") else calendar["previousBusinessDay"]
        after_day = calendar["previousBusinessDay"]
        pre_session = session(pre_day, "preMarket")
        after_session = session(after_day, "afterMarket")
        # Official `before` is inclusive; the calendar supplies each session end.
        pre = await sample("nxt_premarket", lambda: client.candles(
            "005930", "1m", 50, pre_session["endTime"])) if pre_session else None
        post = await sample("nxt_aftermarket", lambda: client.candles(
            "005930", "1m", 200, after_session["endTime"])) if after_session else None
        if not pre_session:
            report.samples["nxt_premarket"] = {"status": "not_checked", "condition": "session_closed"}
        if not after_session:
            report.samples["nxt_aftermarket"] = {"status": "not_checked", "condition": "session_closed"}
        def valid_kr_candle(candle: dict[str, Any]) -> bool:
            opening, high, low, close = (Decimal(candle[k]) for k in ("openPrice", "highPrice", "lowPrice", "closePrice"))
            return (candle["currency"] == "KRW" and min(opening, high, low, close) > 0
                    and low <= min(opening, close) <= max(opening, close) <= high
                    and Decimal(candle["volume"]) >= 0)
        def observed(candles: Any, day: str, bounds: dict[str, Any] | None, kind: str) -> list[str]:
            timestamps = []
            if not bounds:
                return timestamps
            start = datetime.fromisoformat(bounds["startTime"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(bounds["endTime"].replace("Z", "+00:00"))
            for candle in candles or []:
                at = datetime.fromisoformat(candle["timestamp"].replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Seoul"))
                extended = at.time() < Time(8, 30) if kind == "pre" else at.time() > Time(18, 0)
                if (at.date().isoformat() == day and start < at <= end and extended
                        and valid_kr_candle(candle) and Decimal(candle["volume"]) > 0):
                    timestamps.append(at.isoformat())
            return timestamps
        pre_seen = observed(pre, pre_day["date"], pre_session, "pre")
        post_seen = observed(post, after_day["date"], after_session, "post")
        supported = any(s["symbol"] == "005930" and (s.get("koreanMarketDetail") or {}).get("nxtSupported")
                        for s in master or [])
        quote_matches = any(q["symbol"] == "005930" and q["currency"] == "KRW" and Decimal(q["lastPrice"]) > 0 for q in quote or [])
        daily_matches = bool(daily) and all(valid_kr_candle(c) for c in daily)
        if quote_matches and daily_matches and supported and pre_seen and post_seen:
            report.venue = {"verified": True, "includes_nxt": True, "price_basis": "integrated",
                            "daily_candle_basis": "integrated", "evidence": {
                "quote_venue": FAQ_URL + ": domestic candles/trades KRX + NXT integrated; validated quote and NXT-supported stock.",
                "daily_ohlc": FAQ_URL + ": integrated domestic candles; validated daily OHLC schema.",
                "nxt_premarket": "Observed integrated extended-session nonzero-volume 005930 1m candle at "
                                  + pre_seen[0] + "; the API has no per-trade venue field.",
                "nxt_aftermarket": "Observed integrated extended-session nonzero-volume 005930 1m candle at "
                                   + post_seen[0] + "; the API has no per-trade venue field.",
            }}
    report.calls = client.calls - starting_calls
    return report
