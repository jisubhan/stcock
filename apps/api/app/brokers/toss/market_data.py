from __future__ import annotations

import asyncio
import os
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from ..base import BrokerUnavailable, Candle, Quote
from .auth import TokenManager
from .schemas import ApiManifest, Endpoint, field_at, validate_response


REQUIRED_ENDPOINTS = ("prices", "candles", "investor_trading", "rankings", "market_calendar")
REQUIRED_VENUE_EVIDENCE = ("quote_venue", "daily_ohlc", "nxt_premarket", "nxt_aftermarket")


@dataclass
class RateState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    blocked_until: float = 0.0
    limit: int | None = None
    remaining: int | None = None


class TossMarketData:
    """Read-only adapter that refuses undocumented or ambiguous broker data."""

    def __init__(
        self, manifest: ApiManifest | None = None, env: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None, manifest_error: str | None = None,
    ) -> None:
        source = os.environ if env is None else env
        self.env = {name: source[name] for name in ("TOSS_API_MANIFEST", "TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET", "TOSS_ACCESS_TOKEN") if name in source}
        self.manifest = manifest
        self.manifest_error = manifest_error
        self.client = client or httpx.AsyncClient(timeout=20.0, follow_redirects=False, verify=True)
        self._owns_client = client is None
        self.auth = TokenManager(manifest, self.env, self.client) if manifest else None
        self._rates: dict[str, RateState] = {}

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, client: httpx.AsyncClient | None = None) -> TossMarketData:
        actual = dict(os.environ if env is None else env)
        path = actual.get("TOSS_API_MANIFEST", "")
        if not path:
            return cls(env=actual, client=client)
        try:
            manifest = ApiManifest.model_validate_json(Path(path).read_text(encoding="utf-8"))
            return cls(manifest=manifest, env=actual, client=client)
        except (OSError, ValidationError, ValueError):
            return cls(env=actual, client=client, manifest_error="검증용 API manifest를 읽거나 스키마를 확인할 수 없습니다.")

    def readiness(self) -> dict[str, Any]:
        blockers: list[str] = []
        manifest = self.manifest
        if not manifest:
            blockers.append(self.manifest_error or "현재 공식 API 경로와 응답 스키마를 검증한 manifest가 없습니다.")
        else:
            if not manifest.verified:
                blockers.append("API 문서 및 실제 응답 스키마 검증이 완료되지 않았습니다.")
            venue = manifest.venue
            if not venue.verified or not venue.includes_nxt or venue.price_basis != "integrated" or venue.daily_candle_basis != "integrated":
                blockers.append("KRX·NXT 통합 현재가 및 일봉 의미가 검증되지 않았습니다.")
            if any(not venue.evidence.get(key, "").strip() for key in REQUIRED_VENUE_EVIDENCE):
                blockers.append("NXT 프리마켓·애프터마켓 및 시세/일봉 검증 근거가 부족합니다.")
            missing = [key for key in REQUIRED_ENDPOINTS if key not in manifest.endpoints]
            if missing:
                blockers.append("필수 조회 API 매핑이 누락되었습니다: " + ", ".join(missing))
            required_maps = {"prices": ("symbol", "price", "change_pct", "volume", "traded_value", "timestamp"),
                             "candles": ("timestamp", "open", "high", "low", "close", "volume")}
            for name, fields in required_maps.items():
                endpoint = manifest.endpoints.get(name)
                if endpoint and any(not endpoint.field_map.get(key) for key in fields):
                    blockers.append(f"{name} 정규화 필드 매핑이 부족합니다.")
        credential_names = ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET", "TOSS_ACCESS_TOKEN")
        credentials_present = {name: bool(self.env.get(name)) for name in credential_names}
        if not credentials_present["TOSS_ACCESS_TOKEN"] and not all(credentials_present[name] for name in ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET")):
            blockers.append("서버에 사용 가능한 Toss 인증 정보가 없습니다.")
        return {"ready": not blockers, "mode": "live" if not blockers else "blocked", "blockers": blockers,
                "credentials": credentials_present, "manifest_present": manifest is not None,
                "venue_verified": bool(manifest and manifest.venue.verified), "live_orders_enabled": False}

    def _ensure_ready(self) -> ApiManifest:
        readiness = self.readiness()
        if not readiness["ready"]:
            raise BrokerUnavailable(" ".join(readiness["blockers"]))
        assert self.manifest is not None
        return self.manifest

    @staticmethod
    def _retry_after(value: str | None) -> float:
        if not value:
            return 0.0
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return 0.0

    @staticmethod
    def _update_rate(response: httpx.Response, endpoint: Endpoint, state: RateState) -> None:
        for header, name in (("X-RateLimit-Limit", "limit"), ("X-RateLimit-Remaining", "remaining")):
            try:
                setattr(state, name, max(0, int(response.headers[header])))
            except (KeyError, ValueError):
                pass
        if state.remaining == 0:
            try:
                reset = float(response.headers["X-RateLimit-Reset"])
                seconds = max(0.0, reset - time.time()) if endpoint.rate_reset_format == "epoch_seconds" else max(0.0, reset)
            except (KeyError, ValueError):
                seconds = 1.0
            state.blocked_until = max(state.blocked_until, time.monotonic() + seconds)
        retry_after = TossMarketData._retry_after(response.headers.get("Retry-After"))
        if retry_after:
            state.blocked_until = max(state.blocked_until, time.monotonic() + retry_after)

    async def _request(self, name: str, parameters: Mapping[str, str] | None = None) -> Any:
        manifest = self._ensure_ready()
        endpoint = manifest.endpoints.get(name)
        if not endpoint:
            raise BrokerUnavailable(f"{name} 조회 API가 검증된 manifest에 없습니다.")
        parameters = parameters or {}
        try:
            path = endpoint.path.format_map({key: quote(value, safe="") for key, value in parameters.items()})
            query = {key: value.format_map(parameters) for key, value in endpoint.query.items()}
        except (ValueError, KeyError):
            raise BrokerUnavailable("검증된 API 요청 매핑과 호출 인자가 일치하지 않습니다.") from None
        state = self._rates.setdefault(endpoint.rate_group, RateState())
        assert self.auth is not None
        try:
            async with state.lock:
                renewed = False
                for attempt in range(4):
                    wait = state.blocked_until - time.monotonic()
                    if wait > 60:
                        raise BrokerUnavailable("Toss API 호출 제한으로 조회를 잠시 보류했습니다.")
                    if wait > 0:
                        await asyncio.sleep(wait)
                    response = await self.client.get(str(manifest.base_url).rstrip("/") + path, params=query, headers=await self.auth.header())
                    self._update_rate(response, endpoint, state)
                    if response.status_code == 401 and not renewed:
                        await self.auth.header(force_refresh=True)
                        renewed = True
                        continue
                    if response.status_code == 429:
                        backoff = min(20.0, 2 ** attempt + random.uniform(0.0, 0.8))
                        state.blocked_until = max(state.blocked_until, time.monotonic() + backoff)
                        continue
                    if response.status_code >= 400:
                        raise BrokerUnavailable(f"Toss 시세 조회 실패 (HTTP {response.status_code}).")
                    body = response.json()
                    validate_response(body, endpoint.response_schema)
                    return field_at(body, endpoint.data_path)
                raise BrokerUnavailable("Toss API 요청 제한 또는 인증 오류가 계속되어 조회를 보류했습니다.")
        except BrokerUnavailable:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise BrokerUnavailable("Toss 응답 스키마 또는 네트워크 검증에 실패했습니다.") from None

    def _mapped(self, name: str, records: Any) -> list[dict[str, Any]]:
        assert self.manifest is not None
        endpoint = self.manifest.endpoints[name]
        if not isinstance(records, list):
            raise BrokerUnavailable("시세 응답의 목록 구조가 검증된 스키마와 다릅니다.")
        try:
            return [{name: field_at(record, path) for name, path in endpoint.field_map.items()} for record in records]
        except (ValueError, KeyError, IndexError, TypeError):
            raise BrokerUnavailable("시세 필드 매핑 검증에 실패했습니다.") from None

    async def get_prices(self, symbols: list[str]) -> list[Quote]:
        if not symbols or any(not symbol.strip() for symbol in symbols):
            return []
        payload = await self._request("prices", {"symbols": ",".join(symbols)})
        try:
            return [Quote.model_validate(item) for item in self._mapped("prices", payload)]
        except ValidationError:
            raise BrokerUnavailable("현재가 정규화 스키마 검증에 실패했습니다.") from None

    async def get_candles(self, symbol: str, interval: str) -> list[Candle]:
        payload = await self._request("candles", {"symbol": symbol, "interval": interval})
        try:
            return [Candle.model_validate({"symbol": symbol, "interval": interval, **item}) for item in self._mapped("candles", payload)]
        except ValidationError:
            raise BrokerUnavailable("캔들 정규화 스키마 검증에 실패했습니다.") from None

    async def get_orderbook(self, symbol: str) -> Any:
        return await self._request("orderbook", {"symbol": symbol})

    async def get_trades(self, symbol: str) -> Any:
        return await self._request("trades", {"symbol": symbol})

    async def get_investor_trading(self, symbol: str) -> Any:
        return await self._request("investor_trading", {"symbol": symbol})

    async def get_program_trades(self, symbol: str) -> Any:
        return await self._request("program_trades", {"symbol": symbol})

    async def get_rankings(self, ranking_type: str) -> Any:
        return await self._request("rankings", {"ranking_type": ranking_type})

    async def get_market_calendar(self) -> Any:
        return await self._request("market_calendar")

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
