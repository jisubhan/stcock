import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from app.brokers.toss.documented import (
    CONTRACT_PATH, OFFICIAL_ORIGIN, ContractMismatch, OfficialTossClient,
    TossAPIError, run_probe, save_probe, validate_official_response,
)

ENV = {"TOSS_CLIENT_ID": "test-client", "TOSS_CLIENT_SECRET": "test-secret"}
TOKEN = {"access_token": "test-token", "token_type": "Bearer", "expires_in": 86400}
PRICE = {"symbol": "005930", "timestamp": None, "lastPrice": "72000", "currency": "KRW"}


def run(coro):
    return asyncio.run(coro)


def candle(timestamp="2026-10-07T08:50:00+09:00", volume="15200"):
    return {"timestamp": timestamp, "openPrice": "72000", "highPrice": "72100",
            "lowPrice": "71900", "closePrice": "72050", "volume": volume, "currency": "KRW"}


def stock(nxt=True):
    return {"symbol": "005930", "name": "삼성전자", "englishName": "Samsung",
            "isinCode": "KR7005930003", "market": "KOSPI", "securityType": "STOCK",
            "isCommonShare": True, "status": "ACTIVE", "currency": "KRW", "sharesOutstanding": "100",
            "listDate": None, "delistDate": None, "leverageFactor": None,
            "koreanMarketDetail": {"liquidationTrading": False, "nxtSupported": nxt,
                                   "krxTradingSuspended": False, "nxtTradingSuspended": None}}


def calendar():
    def day(date):
        return {"date": date, "integrated": {
            "preMarket": {"startTime": date + "T08:00:00+09:00", "endTime": date + "T09:00:00+09:00"},
            "regularMarket": None,
            "afterMarket": {"startTime": date + "T15:30:00+09:00", "endTime": date + "T20:00:00+09:00"},
        }}
    return {"today": day("2026-10-07"), "previousBusinessDay": day("2026-10-06"),
            "nextBusinessDay": day("2026-10-08")}


def make_client(handler, **kwargs):
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    return OfficialTossClient(env=ENV, client=http_client, **kwargs)


def test_exact_oauth_form_and_one_token_shared_by_concurrent_reads():
    seen = []
    def handler(request):
        seen.append(request)
        assert str(request.url).startswith(OFFICIAL_ORIGIN + "/")
        if request.url.path == "/oauth2/token":
            assert request.method == "POST"
            assert request.headers["content-type"] == "application/x-www-form-urlencoded"
            assert parse_qs(request.content.decode()) == {
                "grant_type": ["client_credentials"], "client_id": ["test-client"],
                "client_secret": ["test-secret"],
            }
            assert "authorization" not in request.headers
            return httpx.Response(200, json=TOKEN)
        assert request.method == "GET"
        assert request.headers["authorization"] == "Bearer test-token"
        return httpx.Response(200, json={"result": [PRICE]})
    client = make_client(handler)
    async def reads():
        values = await asyncio.gather(*(client.prices(["005930"]) for _ in range(8)))
        assert all(value == [PRICE] for value in values)
    run(reads())
    assert len([r for r in seen if r.method == "POST"]) == 1


def test_token_renews_only_on_expiry_or_first_401():
    mint = 0
    gets = 0
    def handler(request):
        nonlocal mint, gets
        if request.method == "POST":
            mint += 1
            return httpx.Response(200, json={**TOKEN, "access_token": f"test-token-{mint}"})
        gets += 1
        if gets == 2:
            return httpx.Response(401, json={"error": {"code": "token-revoked"}})
        return httpx.Response(200, json={"result": [PRICE]})
    client = make_client(handler)
    async def reads():
        await client.prices(["005930"])
        assert mint == 1
        await client.prices(["005930"])
        assert mint == 2
        await client.prices(["005930"])
        assert mint == 2
        client._expires_at = 0
        await client.prices(["005930"])
        assert mint == 3
    run(reads())


def test_oauth_ip_block_sanitized_and_not_retried_or_forged(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(403, json={"error": "access_denied", "error_description": "IP address not allowed",
                                        "untrusted": "test-secret test-token test-client"})
    client = make_client(handler)
    report = run(run_probe(client))
    assert report.authentication == {"status": "failed", "condition": "ip_not_allowed", "http_status": 403,
                                     "error_code": "access_denied", "schema_status": "not_checked"}
    assert report.calls == 1 and len(calls) == 1
    assert report.samples == {} and report.venue["verified"] is False
    with pytest.raises(TossAPIError):
        run(client.prices(["005930"]))
    assert len(calls) == 1
    client.reset_auth_failure()
    assert len(calls) == 1
    assert client.auth_status == {"status": "not_checked"}
    path = save_probe(report)
    saved = path.read_text() + (path.parent / "toss-venue-evidence.json").read_text()
    assert all(secret not in saved for secret in ("test-secret", "test-token", "test-client", "error_description"))


def test_unknown_upstream_error_description_and_network_exception_are_not_leaked():
    client = make_client(lambda r: httpx.Response(403, json={"error": "test-secret", "error_description": "test-secret"}))
    report = run(run_probe(client))
    assert report.authentication["condition"] == "http_error"
    assert report.authentication["error_code"] is None
    assert "test-secret" not in json.dumps(report.to_dict())
    def failing(request):
        raise httpx.ConnectError("test-secret echoed in network exception", request=request)
    report = run(run_probe(make_client(failing)))
    assert report.authentication["condition"] == "network_error"
    assert "test-secret" not in json.dumps(report.to_dict())


@pytest.mark.parametrize("token", [
    {"result": TOKEN}, {**TOKEN, "token_type": "bearer"}, {**TOKEN, "expires_in": "86400"},
    {**TOKEN, "expires_in": 0}, {**TOKEN, "access_token": ""}, {**TOKEN, "expires_in": 10 ** 100},
])
def test_oauth_response_must_match_current_root_contract(token):
    report = run(run_probe(make_client(lambda request: httpx.Response(200, json=token))))
    assert report.authentication["condition"] == "schema_mismatch"
    assert report.calls == 1


def test_redirects_never_forward_credentials_or_bearer_token():
    seen = []
    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN)
        return httpx.Response(302, headers={"Location": "https://example.test/test-secret"})
    client = make_client(handler)
    with pytest.raises(TossAPIError) as error:
        run(client.prices(["005930"]))
    assert error.value.http_status == 302
    assert len(seen) == 2 and all(url.startswith(OFFICIAL_ORIGIN) for url in seen)


def test_read_only_origin_and_query_guards_are_enforced_before_http():
    calls = []
    client = make_client(lambda r: calls.append(r))
    for operation in (client.prices(["005930/../orders"]), client.candles("005930", "5m"),
                      client.candles("005930", "1d", 201), client.calendar("CN"),
                      client.index_prices(["KOSPI,http://example.test"]),
                      client._send("POST", "/api/v1/orders"),
                      client._send("GET", "https://example.test")):
        with pytest.raises(TossAPIError):
            run(operation)
    assert calls == []


def test_current_ref_allof_oneof_nullable_contracts_and_malformed_payloads():
    contract = json.loads(CONTRACT_PATH.read_text())
    prices = contract["responses"]["/api/v1/prices"]
    validate_official_response({"result": [PRICE]}, prices, contract)
    stocks = contract["responses"]["/api/v1/stocks"]
    validate_official_response({"result": [stock()]}, stocks, contract)
    validate_official_response({"result": [{**stock(), "koreanMarketDetail": None}]}, stocks, contract)
    candles = contract["responses"]["/api/v1/candles"]
    validate_official_response({"result": {"candles": [candle()], "nextBefore": None}}, candles, contract)
    for schema, body in (
        (prices, {"result": [{**PRICE, "lastPrice": "NaN"}]}),
        (prices, {"result": [{**PRICE, "lastPrice": 72000}]}),
        (prices, {"result": [{**PRICE, "timestamp": "2026-10-07T09:00:00"}]}),
        (prices, {"result": [{"symbol": "005930"}]}),
        (candles, {"result": [candle()]}),
        (stocks, {"result": [{**stock(), "koreanMarketDetail": {"nxtSupported": True}}]}),
    ):
        with pytest.raises(ContractMismatch):
            validate_official_response(body, schema, contract)


def test_official_read_method_shapes_parameters_and_newest_first_candles():
    seen = []
    latest = candle()
    older = candle("2026-10-07T08:49:00+09:00")
    def handler(request):
        seen.append(request)
        path = request.url.path
        if path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN)
        if path == "/api/v1/candles":
            assert dict(request.url.params) == {"symbol": "005930", "interval": "1m", "count": "2",
                                                "adjusted": "true", "before": "2026-10-07T08:51:00+09:00"}
            return httpx.Response(200, json={"result": {"candles": [latest, older], "nextBefore": older["timestamp"]}})
        if path == "/api/v1/rankings":
            assert request.url.params["type"] == "MARKET_TRADING_AMOUNT"
            assert request.url.params["marketCountry"] == "US"
            assert request.url.params["duration"] == "1d"
            return httpx.Response(200, json={"result": {"rankings": [], "rankedAt": None}})
        if path.endswith("investor-trading") or path.endswith("program-trades"):
            assert request.url.params["until"] == "2026-10-06"
            return httpx.Response(200, json={"result": {"records": [], "nextUntil": None}})
        if path == "/api/v1/market-calendar/US":
            assert request.url.params["date"] == "2026-10-07"
            return httpx.Response(200, json={"result": {k: {"date": "2026-10-07", "dayMarket": None,
                "preMarket": None, "regularMarket": None, "afterMarket": None}
                for k in ("today", "previousBusinessDay", "nextBusinessDay")}})
        if path.endswith("KOSPI/candles"):
            return httpx.Response(200, json={"result": {"candles": [{k: v for k, v in latest.items() if k != "currency"}]}})
        if path == "/api/v1/market-indicators/prices":
            assert request.url.params["symbols"] == "KOSPI,KOSDAQ"
            return httpx.Response(200, json={"result": [{"symbol": "KOSPI", "timestamp": None, "lastPrice": "2812.45"}]})
        return httpx.Response(200, json={"result": [stock()]})
    client = make_client(handler)
    async def reads():
        assert await client.get_candles("005930", "1m", 2, "2026-10-07T08:51:00+09:00") == [latest, older]
        assert await client.get_rankings("US") == []
        assert await client.get_investor_trading("005930", until="2026-10-06") == []
        assert await client.get_program_trades("005930", until="2026-10-06") == []
        await client.get_market_calendar("US", date="2026-10-07")
        await client.get_index_candles("KOSPI")
        await client.get_index_prices()
        await client.get_stock_master(["005930"])
    run(reads())
    assert len([r for r in seen if r.method == "POST"]) == 1


@pytest.mark.parametrize("nxt,volume,zone,expected", [
    (True, "15", "kst", True), (False, "15", "kst", False), (True, "0", "kst", False),
    (True, "15", "utc_equivalent", True), (True, "15", "utc_wrong_session", False),
    (True, "15", "usd_sessions", False), (True, "15", "invalid_ohlc_sessions", False),
    (True, "15", "late_pre", False), (True, "15", "outside_calendar", False),
])
def test_bounded_probe_venue_requires_real_session_candles_and_stock_support(nxt, volume, zone, expected):
    seen = []
    def handler(request):
        seen.append(request)
        path = request.url.path
        if path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN)
        if path == "/api/v1/prices":
            return httpx.Response(200, json={"result": [PRICE]})
        if path == "/api/v1/candles":
            before = request.url.params.get("before")
            timestamp = "2026-10-06T20:00:00+09:00" if before and "20:00" in before else "2026-10-07T08:20:00+09:00"
            if zone == "utc_equivalent":
                timestamp = "2026-10-06T11:00:00Z" if before and "20:00" in before else "2026-10-06T23:20:00Z"
            elif zone == "utc_wrong_session":
                timestamp = timestamp.replace("+09:00", "Z")
            elif zone == "late_pre" and before and "09:00" in before:
                timestamp = "2026-10-07T08:50:00+09:00"
            item = candle(timestamp, volume)
            if before and zone == "usd_sessions":
                item["currency"] = "USD"
            if before and zone == "invalid_ohlc_sessions":
                item["lowPrice"] = "80000"
            return httpx.Response(200, json={"result": {"candles": [item]}})
        if path == "/api/v1/rankings":
            return httpx.Response(200, json={"result": {"rankings": []}})
        if path == "/api/v1/market-calendar/KR":
            value = calendar()
            if zone == "outside_calendar":
                value["today"]["integrated"]["preMarket"]["startTime"] = "2026-10-07T08:21:00+09:00"
            return httpx.Response(200, json={"result": value})
        if path == "/api/v1/stocks":
            return httpx.Response(200, json={"result": [stock(nxt)]})
        return httpx.Response(200, json={"result": {"records": []}})
    report = run(run_probe(make_client(handler)))
    assert report.calls == len(seen) == 12
    assert report.venue["verified"] is expected
    assert bool(report.venue["evidence"]) is expected
    if expected:
        assert set(report.venue["evidence"]) == {"quote_venue", "daily_ohlc", "nxt_premarket", "nxt_aftermarket"}
        assert "2026-10-07T08:20:00+09:00" in report.venue["evidence"]["nxt_premarket"]
        assert "2026-10-06T20:00:00+09:00" in report.venue["evidence"]["nxt_aftermarket"]
    saved = json.dumps(report.to_dict())
    assert all(secret not in saved for secret in ("test-secret", "test-token", "test-client"))
    assert "lastPrice" not in saved and "openPrice" not in saved


def test_probe_restores_persistent_client_budget_and_preserves_valid_token():
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json=TOKEN)
        if request.url.path == "/api/v1/prices":
            return httpx.Response(200, json={"result": [PRICE]})
        if request.url.path == "/api/v1/candles":
            return httpx.Response(200, json={"result": {"candles": []}})
        if request.url.path == "/api/v1/rankings":
            return httpx.Response(200, json={"result": {"rankings": []}})
        if request.url.path == "/api/v1/market-calendar/KR":
            return httpx.Response(200, json={"result": calendar()})
        if request.url.path == "/api/v1/stocks":
            return httpx.Response(200, json={"result": [stock()]})
        return httpx.Response(200, json={"result": {"records": []}})
    client = make_client(handler)
    async def probes_and_read():
        first = await run_probe(client)
        assert first.calls == 12
        assert client.max_http_calls is None and client._probe_retries is None
        assert await client.prices(["005930"]) == [PRICE]
        second = await run_probe(client)
        assert second.calls == 11  # Existing token is reused, with no OAuth mint.
        assert client.max_http_calls is None and client._probe_retries is None
        assert client.calls == 24
    run(probes_and_read())


def test_429_probe_retry_is_global_once_with_delay_bounded_to_five(monkeypatch):
    delays = []
    async def sleep(delay):
        delays.append(delay)
    monkeypatch.setattr("app.brokers.toss.documented.asyncio.sleep", sleep)
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN)
        return httpx.Response(429, headers={"Retry-After": "1000"}, json={"error": {"code": "rate-limit-exceeded"}})
    report = run(run_probe(make_client(handler)))
    assert delays == [5.0]
    assert report.calls == len(calls) <= 12


def test_saved_contract_provenance_contains_no_request_secrets_or_order_operations():
    contract = json.loads(CONTRACT_PATH.read_text())
    assert contract["origin"] == OFFICIAL_ORIGIN and contract["document_version"] == "1.2.19"
    assert len(contract["source_sha256"]) == 64
    serialized = json.dumps(contract)
    assert "client_secret" not in serialized and "example" not in serialized
    assert not any("orders" in path or "accounts" in path or "holdings" in path for path in contract["responses"])
