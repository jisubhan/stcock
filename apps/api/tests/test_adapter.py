"""Offline transport tests; no fixture claims to be a real Toss response."""

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from app.brokers import BrokerUnavailable, Candle, TossMarketData
from app.brokers.toss.schemas import ApiManifest, ResponseSchema, validate_response


def manifest_dict():
    number = {"type": "number"}
    text = {"type": "string"}
    quote_fields = {"code": text, "last": number, "change": number, "qty": number,
                    "value": number, "ts": text}
    candle_fields = {"ts": text, "o": number, "h": number, "l": number,
                     "c": number, "qty": number}

    def endpoint(path, fields=None, mapping=None):
        schema = {"type": "object", "properties": {"rows": {"type": "array", "items":
                  {"type": "object", "required": list(fields or {}), "properties": fields or {}}}},
                  "required": ["rows"]}
        return {"path": path, "data_path": "rows", "response_schema": schema,
                "field_map": mapping or {}, "rate_group": "quotes", "rate_reset_format": "seconds"}

    return {
        "verified": True, "document_url": "https://example.test/verified-docs",
        "verified_at": "2026-10-07T00:00:00+09:00", "base_url": "https://example.test",
        "venue": {"verified": True, "price_basis": "integrated", "daily_candle_basis": "integrated",
                  "includes_nxt": True, "evidence": {key: "offline-test-only" for key in
                  ("quote_venue", "daily_ohlc", "nxt_premarket", "nxt_aftermarket")}},
        "oauth": {"path": "/test-token", "encoding": "json",
                  "payload": {"id": "${TOSS_CLIENT_ID}", "secret": "${TOSS_CLIENT_SECRET}"},
                  "refresh_payload": {"refresh": "${REFRESH_TOKEN}"},
                  "access_token_path": "access", "expires_in_path": "expires",
                  "refresh_token_path": "refresh", "auth_header": "Authorization", "auth_prefix": "Bearer ",
                  "response_schema": {"type": "object", "required": ["access", "expires"],
                  "properties": {"access": text, "expires": number, "refresh": text}}},
        "endpoints": {
            "prices": endpoint("/test-quotes", quote_fields,
                               {"symbol": "code", "price": "last", "change_pct": "change",
                                "volume": "qty", "traded_value": "value", "timestamp": "ts"}),
            "candles": endpoint("/test-candles/{symbol}", candle_fields,
                                {"timestamp": "ts", "open": "o", "high": "h", "low": "l",
                                 "close": "c", "volume": "qty"}),
            "investor_trading": endpoint("/test-flow"),
            "rankings": endpoint("/test-ranking"),
            "market_calendar": endpoint("/test-calendar"),
        },
    }


QUOTE = {"code": "005930", "last": 100.0, "change": 1.2, "qty": 200,
         "value": 20_000, "ts": "2026-10-07T09:00:00+09:00"}


def test_missing_manifest_and_credentials_block_network():
    calls = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request)))
    adapter = TossMarketData(env={}, client=client)
    status = adapter.readiness()
    assert not status["ready"]
    assert status["credentials"] == {"TOSS_CLIENT_ID": False, "TOSS_CLIENT_SECRET": False, "TOSS_ACCESS_TOKEN": False}
    assert status["live_orders_enabled"] is False
    with pytest.raises(BrokerUnavailable):
        asyncio.run(adapter.get_prices(["005930"]))
    assert not calls
    asyncio.run(client.aclose())


@pytest.mark.parametrize("change", ["verification", "nxt", "evidence", "schema"])
def test_incomplete_manifest_stays_blocked(change):
    data = manifest_dict()
    if change == "verification":
        data["verified"] = False
    elif change == "nxt":
        data["venue"]["daily_candle_basis"] = "krx_only"
    elif change == "evidence":
        data["venue"]["evidence"].pop("nxt_aftermarket")
    else:
        data["endpoints"]["prices"]["field_map"].pop("timestamp")
    adapter = TossMarketData(ApiManifest.model_validate(data), env={"TOSS_ACCESS_TOKEN": "offline"})
    assert not adapter.readiness()["ready"]
    asyncio.run(adapter.aclose())


def test_normalized_payload_and_path_encoding():
    requests = []

    def transport(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer offline"
        if request.url.path.startswith("/test-candles"):
            return httpx.Response(200, json={"rows": [{"ts": QUOTE["ts"], "o": 99, "h": 101,
                                                      "l": 98, "c": 100, "qty": 200}]})
        return httpx.Response(200, json={"rows": [QUOTE]})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            adapter = TossMarketData(ApiManifest.model_validate(manifest_dict()), env={"TOSS_ACCESS_TOKEN": "offline"}, client=client)
            prices = await adapter.get_prices(["005930"])
            candles = await adapter.get_candles("005930", "1d")
            assert prices[0].price == 100
            assert prices[0].venue == "integrated"
            assert candles[0].integrated
            assert candles[0].interval == "1d"
    asyncio.run(check())
    assert len(requests) == 2


def test_malformed_response_is_safe_and_not_accepted():
    secret = "do-not-leak-token"

    def transport(request):
        return httpx.Response(200, json={"rows": [{**QUOTE, "last": secret}]})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            adapter = TossMarketData(ApiManifest.model_validate(manifest_dict()), env={"TOSS_ACCESS_TOKEN": secret}, client=client)
            with pytest.raises(BrokerUnavailable) as error:
                await adapter.get_prices(["005930"])
            assert secret not in str(error.value)
    asyncio.run(check())


def test_rate_limit_headers_and_429_backoff(monkeypatch):
    sleeps = []
    attempts = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    def transport(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"X-RateLimit-Limit": "10", "X-RateLimit-Remaining": "0",
                                               "X-RateLimit-Reset": "2", "Retry-After": "1"})
        return httpx.Response(200, headers={"X-RateLimit-Remaining": "9"}, json={"rows": [QUOTE]})

    monkeypatch.setattr("app.brokers.toss.market_data.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("app.brokers.toss.market_data.random.uniform", lambda low, high: 0.5)

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            adapter = TossMarketData(ApiManifest.model_validate(manifest_dict()), env={"TOSS_ACCESS_TOKEN": "offline"}, client=client)
            assert len(await adapter.get_prices(["005930"])) == 1
            assert adapter._rates["quotes"].limit == 10
            assert adapter._rates["quotes"].remaining == 9
    asyncio.run(check())
    assert len(attempts) == 2
    assert sleeps and 1.9 < sleeps[0] <= 2.0


def test_oauth_refresh_uses_verified_mapping_and_never_exposes_tokens():
    requests = []
    token_count = 0

    def transport(request):
        nonlocal token_count
        requests.append(request)
        if request.url.path == "/test-token":
            token_count += 1
            payload = json.loads(request.content)
            if token_count == 1:
                assert payload == {"id": "test-id", "secret": "test-secret"}
            else:
                assert payload == {"refresh": "refresh-test"}
            return httpx.Response(200, json={"access": f"token-{token_count}", "expires": 3600, "refresh": "refresh-test"})
        return httpx.Response(200, json={"rows": [QUOTE]})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            adapter = TossMarketData(ApiManifest.model_validate(manifest_dict()),
                                     env={"TOSS_CLIENT_ID": "test-id", "TOSS_CLIENT_SECRET": "test-secret"}, client=client)
            await adapter.get_prices(["005930"])
            adapter.auth._expires_at = 0
            await adapter.get_prices(["005930"])
            assert "test-secret" not in json.dumps(adapter.readiness())
    asyncio.run(check())
    assert token_count == 2


def test_candles_require_consistent_ohlc_and_timezone():
    value = {"symbol": "005930", "timestamp": QUOTE["ts"], "open": 99, "high": 101,
             "low": 98, "close": 100, "volume": 200, "interval": "1d"}
    assert Candle.model_validate(value).close == 100
    with pytest.raises(ValidationError):
        Candle.model_validate({**value, "high": 90})
    with pytest.raises(ValidationError):
        Candle.model_validate({**value, "timestamp": "2026-10-07T09:00:00"})


def test_schema_rejects_booleans_instead_of_numeric_values():
    with pytest.raises(ValueError):
        validate_response(True, ResponseSchema(type="number"))
