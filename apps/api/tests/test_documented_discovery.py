"""Offline discovery-contract regressions; all HTTP responses are invented."""
import asyncio
import json
from copy import deepcopy

import httpx
import pytest

from app.brokers.toss.documented import CONTRACT_PATH, OFFICIAL_ORIGIN, OfficialTossClient, TossAPIError


ENV = {"TOSS_CLIENT_ID": "discovery-test-client", "TOSS_CLIENT_SECRET": "discovery-test-secret"}
TOKEN = {"access_token": "discovery-test-token", "token_type": "Bearer", "expires_in": 86400}
SECTORS = [
    {"sectorId": "TEST-ROOT", "name": "합성 분류", "depth": 0, "parentSectorId": None},
    {"sectorId": "TEST_CHILD", "name": "합성 하위 분류", "depth": 1, "parentSectorId": "TEST-ROOT"},
]
SECTOR_DETAIL = {**SECTORS[0], "summary": None, "performance": {"changeRates": [
    {"duration": period, "changeRate": None} for period in ("1d", "1w", "1mo", "3mo", "6mo", "1y")
]}}
SECTOR_STOCK = {"symbol": "0101N0", "name": "합성 구성 종목", "volume": None,
                "tradingAmount": "123456789.12", "marketCapitalization": None, "currency": "KRW"}
SECTOR_RANK = {"rank": 1, "sectorId": "TEST-ROOT", "name": "합성 분류", "changeRate": "0.0125",
               "tradingAmount": None, "marketCapitalization": None, "currency": "KRW"}


def listed(symbol="0101N0"):
    return {"symbol": symbol, "name": "합성 종목", "securityType": "STOCK",
            "isCommonShare": True, "isinCode": "TEST-ISIN"}


def run(operation):
    return asyncio.run(operation)


def make_client(handler):
    return OfficialTossClient(env=ENV, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_catalog_returns_entire_filtered_array_and_exact_nonpaginated_query():
    rows = [listed(f"{index:06d}") for index in range(250)] + [listed()]
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN)
        assert request.method == "GET" and request.url.path == "/api/v1/stocks/all"
        assert dict(request.url.params) == {
            "market": "KOSDAQ", "status": "ACTIVE", "securityType": "STOCK", "commonShare": "true",
        }
        return httpx.Response(200, json={"result": rows})

    result = run(make_client(handler).stock_universe("KOSDAQ", security_type="STOCK", common_share=True))
    assert result == rows and len(result) == 251
    assert result[-1]["symbol"] == "0101N0"  # KR codes are not necessarily digits.
    assert not any(key in result[0] for key in ("timestamp", "market", "nxtSupported"))
    assert len(requests) == 2


@pytest.mark.parametrize("common_share,expected", [(None, None), (False, "false")])
def test_optional_catalog_filters_are_omitted_or_strict_boolean(common_share, expected):
    def handler(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN)
        query = dict(request.url.params)
        assert query["market"] == "KR_ETC" and query["status"] == "SCHEDULED"
        assert "securityType" not in query and "count" not in query and "cursor" not in query
        if expected is None:
            assert "commonShare" not in query
        else:
            assert query["commonShare"] == expected
        return httpx.Response(200, json={"result": []})

    assert run(make_client(handler).stock_universe("KR_ETC", status="SCHEDULED", common_share=common_share)) == []


def test_sector_methods_preserve_hierarchy_nullable_values_and_absent_asof():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "openapi.tossinvest.com"
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN)
        assert request.method == "GET" and request.headers["authorization"] == "Bearer discovery-test-token"
        path = request.url.path
        if path == "/api/v1/sectors":
            assert dict(request.url.params) == {}
            return httpx.Response(200, json={"result": {"sectors": SECTORS}})
        assert request.url.params["marketCountry"] == "US"
        if path == "/api/v1/sectors/TEST-ROOT":
            assert dict(request.url.params) == {"marketCountry": "US"}
            return httpx.Response(200, json={"result": SECTOR_DETAIL})
        if path == "/api/v1/sectors/TEST_CHILD/stocks":
            assert dict(request.url.params) == {"marketCountry": "US"}
            return httpx.Response(200, json={"result": {"stocks": [SECTOR_STOCK]}})
        assert path == "/api/v1/sectors/rankings"
        assert dict(request.url.params) == {"marketCountry": "US", "type": "TOP_GAINERS", "duration": "1w"}
        return httpx.Response(200, json={"result": {"rankings": [SECTOR_RANK]}})

    client = make_client(handler)

    async def reads():
        assert await client.sectors() == SECTORS
        assert await client.sector_detail("TEST-ROOT", "US") == SECTOR_DETAIL
        assert await client.sector_stocks("TEST_CHILD", "US") == [SECTOR_STOCK]
        assert await client.sector_rankings("US", "TOP_GAINERS", "1w") == [SECTOR_RANK]

    run(reads())
    assert len([request for request in requests if request.method == "POST"]) == 1
    assert len(requests) == 5
    assert SECTOR_STOCK["volume"] is None and SECTOR_STOCK["tradingAmount"] == "123456789.12"
    assert not any(key in SECTOR_STOCK for key in ("timestamp", "rankedAt", "lastPrice", "basePrice"))


def test_stock_rankings_preserve_default_and_support_valid_distinct_gainers():
    queries = []

    def handler(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN)
        queries.append(dict(request.url.params))
        return httpx.Response(200, json={"result": {"rankings": [], "rankedAt": None}})

    client = make_client(handler)

    async def reads():
        assert await client.rankings() == []
        assert await client.rankings("KR", 35, ranking_type="TOP_GAINERS", duration="1d") == []
        result = await client.rankings_snapshot("US", ranking_type="MARKET_TRADING_VOLUME", duration="realtime")
        assert result["rankedAt"] is None and client.last_ranking_result["rankedAt"] is None

    run(reads())
    assert queries == [
        {"type": "MARKET_TRADING_AMOUNT", "marketCountry": "KR", "duration": "1d", "count": "100"},
        {"type": "TOP_GAINERS", "marketCountry": "KR", "duration": "1d", "count": "35"},
        {"type": "MARKET_TRADING_VOLUME", "marketCountry": "US", "duration": "realtime", "count": "100"},
    ]


@pytest.mark.parametrize("operation,condition", [
    (lambda c: c.stock_universe("KR"), "invalid_stock_market"),
    (lambda c: c.stock_universe(["KOSPI"]), "invalid_stock_market"),
    (lambda c: c.stock_universe("KOSPI", status="UNKNOWN"), "invalid_stock_status"),
    (lambda c: c.stock_universe("KOSPI", status=None), "invalid_stock_status"),
    (lambda c: c.stock_universe("KOSPI", security_type="BOND"), "invalid_security_type"),
    (lambda c: c.stock_universe("KOSPI", common_share="true"), "invalid_common_share"),
    (lambda c: c.stock_universe("KOSPI", common_share=1), "invalid_common_share"),
    (lambda c: c.sector_detail("../orders"), "invalid_sector_id"),
    (lambda c: c.sector_detail("sector?token=value"), "invalid_sector_id"),
    (lambda c: c.sector_detail("x" * 41), "invalid_sector_id"),
    (lambda c: c.sector_detail(None), "invalid_sector_id"),
    (lambda c: c.sector_stocks("good", "CN"), "invalid_market"),
    (lambda c: c.sector_rankings(ranking_type="TOP_LOSERS"), "invalid_ranking_type"),
    (lambda c: c.sector_rankings(duration="realtime"), "invalid_ranking_duration"),
    (lambda c: c.sector_rankings(duration=[]), "invalid_ranking_duration"),
    (lambda c: c.rankings(ranking_type="arbitrary"), "invalid_ranking_type"),
    (lambda c: c.rankings(ranking_type="TOP_GAINERS", duration="realtime"), "invalid_ranking_duration"),
    (lambda c: c.rankings(ranking_type="TOP_LOSERS", duration="realtime"), "invalid_ranking_duration"),
    (lambda c: c.rankings(count=101), "invalid_count"),
    (lambda c: c.prices(["005930"] * 201), "invalid_symbols"),
    (lambda c: c.stock_master(["005930"] * 201), "invalid_symbols"),
])
def test_discovery_argument_guards_fail_before_authentication(operation, condition):
    requests = []
    client = make_client(lambda request: requests.append(request))
    with pytest.raises(TossAPIError) as error:
        run(operation(client))
    assert error.value.condition == condition
    assert requests == []


def test_verified_price_and_stock_detail_batch_bound_is_200():
    count = []

    def handler(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN)
        count.append(len(request.url.params["symbols"].split(",")))
        return httpx.Response(200, json={"result": []})

    client = make_client(handler)

    async def reads():
        await client.prices(["005930"] * 200)
        await client.stock_master(["0101N0"] * 200)

    run(reads())
    assert count == [200, 200]


@pytest.mark.parametrize("operation,result", [
    (lambda c: c.stock_universe("KOSPI"), [{"symbol": "005930"}]),
    (lambda c: c.sectors(), {"sectors": [{"sectorId": "TEST-ROOT", "name": "합성"}]}),
    (lambda c: c.sector_detail("TEST-ROOT"), {**SECTOR_DETAIL, "performance": {"changeRates": [{"duration": "realtime", "changeRate": None}]}}),
    (lambda c: c.sector_stocks("TEST-ROOT"), {"stocks": [{**SECTOR_STOCK, "tradingAmount": 100}]}),
    (lambda c: c.sector_stocks("TEST-ROOT"), {"stocks": [{**SECTOR_STOCK, "tradingAmount": "NaN"}]}),
    (lambda c: c.sector_rankings(), {"rankings": [{**SECTOR_RANK, "rank": True}]}),
])
def test_discovery_response_schema_rejects_incomplete_or_wrong_economic_types(operation, result):
    def handler(request):
        return httpx.Response(200, json=TOKEN if request.url.path == "/oauth2/token" else {"result": deepcopy(result)})

    with pytest.raises(TossAPIError) as error:
        run(operation(make_client(handler)))
    assert error.value.condition == "schema_mismatch"
    assert error.value.schema_status == "failed"


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/v1/sectors"), ("GET", "/api/v1/sectors/TEST-ROOT/etfs"),
    ("GET", "/api/v1/sectors/TEST-ROOT/orders"), ("GET", "/api/v1/sectors/../accounts"),
    ("GET", "/api/v1/accounts"), ("GET", "/api/v1/news"),
])
def test_discovery_whitelist_keeps_orders_accounts_and_unintegrated_routes_closed(method, path):
    requests = []
    client = make_client(lambda request: requests.append(request))
    with pytest.raises(TossAPIError) as error:
        run(client._send(method, path))
    assert error.value.condition == "unsupported_endpoint"
    assert requests == []


def test_discovery_contract_is_reference_closed_and_does_not_include_doc_examples_or_order_data():
    contract = json.loads(CONTRACT_PATH.read_text())
    assert contract["document_version"] == "1.2.24"
    assert contract["document_url"] == OFFICIAL_ORIGIN + "/openapi-docs/latest/openapi.json"
    assert contract["source_sha256"] == "7e753c21dc4b938d8f9610ce3efd6542783018977cb030e39db5a006f5b59d40"
    assert len(contract["responses"]) == 16
    new_paths = {"/api/v1/stocks/all", "/api/v1/sectors", "/api/v1/sectors/{sectorId}",
                 "/api/v1/sectors/{sectorId}/stocks", "/api/v1/sectors/rankings"}
    assert new_paths <= contract["responses"].keys()
    assert not any(token in path for path in contract["responses"] for token in ("orders", "accounts", "etfs"))

    def visit(value):
        if isinstance(value, dict):
            assert not {"example", "examples", "description"} & value.keys()
            if "$ref" in value:
                reference = value["$ref"]
                assert reference.startswith("#/components/schemas/")
                assert reference.rsplit("/", 1)[1] in contract["components"]["schemas"]
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(contract["responses"])
    visit(contract["components"])
    assert "client_secret" not in json.dumps(contract)
