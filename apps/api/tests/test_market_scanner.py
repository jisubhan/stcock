"""Mocked whole-market scans; no authenticated provider evidence is produced."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
from types import SimpleNamespace

import pytest

from app.brokers.toss.documented import TossAPIError
from app.discovery.collector import WholeMarketScanner, normalized_candles
from app.radar.models import KST
from app.settings import runtime_dir


AS_OF = datetime(2026, 10, 12, 9, 10, tzinfo=KST)
SYMBOLS = ["000001", "0101N0", "999999"]
CONFIG = {"discovery": {"scan_batch_seconds": 2, "quote_batch_size": 100,
                         "analysis_per_cycle": 2, "membership_max_depth": 1}}


def run(coro):
    return asyncio.run(coro)


def proof(authentication=None, venue=None):
    (runtime_dir() / "toss-probe.json").write_text(json.dumps({
        "authentication": {"status": "passed"} if authentication is None else authentication,
        "venue": {"verified": True} if venue is None else venue,
    }))


def listed(symbol):
    return {"symbol": symbol, "name": "Constructed " + symbol, "securityType": "STOCK",
            "isCommonShare": True, "isinCode": "KR" + symbol + "0000"}


class Clock:
    def __init__(self):
        self.now = AS_OF
        self.monotonic = 1_000.

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)
        self.monotonic += seconds


class MockClient:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.failures = {}
        self.closed = False
        self.catalogues = {market: [listed(symbol)] for market, symbol in zip(
            ("KOSPI", "KOSDAQ", "KR_ETC"), SYMBOLS)}
        self.sector_rows = []
        self.ranking_timestamp_null = False
        self.ranking_rows = []
        self.candle_failure_currency = False
        self.candle_delay = 0

    def check(self, capability):
        error = self.failures.get(capability)
        if error is not None:
            raise error

    async def calendar(self, country):
        self.calls.append(("calendar", country))
        self.check("calendar:" + country)
        stamp = self.clock.now
        return {"today": {"date": stamp.date().isoformat(), "integrated": {"regularMarket": {
            "startTime": stamp.replace(hour=9, minute=0, second=0, microsecond=0).isoformat(),
            "endTime": stamp.replace(hour=15, minute=30, second=0, microsecond=0).isoformat(),
        }}}}

    async def stock_universe(self, market, **filters):
        self.calls.append(("stock_universe", market, filters))
        self.check("catalog:" + market)
        return deepcopy(self.catalogues.get(market, []))

    async def sectors(self):
        self.calls.append(("sectors",))
        self.check("sectors")
        return deepcopy(self.sector_rows)

    async def stock_master(self, symbols):
        self.calls.append(("stock_master", tuple(symbols)))
        self.check("master")
        return [{"symbol": symbol, "name": "Constructed " + symbol, "status": "ACTIVE",
                 "market": "KOSDAQ", "currency": "KRW", "koreanMarketDetail": {"nxtSupported": True}}
                for symbol in symbols]

    async def sector_stocks(self, sector_id, country):
        self.calls.append(("sector_stocks", sector_id, country))
        self.check("sector_members:" + sector_id)
        return [{"symbol": SYMBOLS[0], "name": "Constructed stock", "volume": "100",
                 "tradingAmount": "10000", "marketCapitalization": "1000000", "currency": "KRW"}]

    async def sector_rankings(self, country, duration="1d"):
        self.calls.append(("sector_rankings", country, duration))
        self.check("sector_ranking:" + country + ":" + duration)
        return [{"rank": 1, "sectorId": "test-sector", "name": "Constructed sector", "changeRate": ".03",
                 "tradingAmount": "10000", "marketCapitalization": "1000000", "currency": "KRW"}]

    async def rankings_snapshot(self, country, count):
        self.calls.append(("rankings_snapshot", country, count))
        self.check("global_rankings")
        return {"rankedAt": None if self.ranking_timestamp_null else self.clock.now.isoformat(),
                "rankings": deepcopy(self.ranking_rows)}

    async def prices(self, symbols):
        self.calls.append(("prices", tuple(symbols)))
        self.check("scan_quotes")
        return [{"symbol": symbol, "lastPrice": "100", "currency": "KRW",
                 "timestamp": self.clock.now.isoformat()} for symbol in symbols]

    async def candles(self, symbol, interval, count):
        self.calls.append(("candles", symbol, interval, count))
        self.check("candles:" + symbol)
        stamp = self.clock.now - timedelta(days=1) if interval == "1d" else self.clock.now.replace(second=0, microsecond=0)
        result = [{"timestamp": stamp.isoformat(), "openPrice": "100", "highPrice": "105",
                   "lowPrice": "99", "closePrice": "103", "volume": "1000",
                   "currency": "USD" if self.candle_failure_currency else "KRW"}]
        if self.candle_delay:
            self.clock.advance(self.candle_delay)
        return result

    async def authenticate(self):
        self.calls.append(("authenticate",))
        raise AssertionError("Scanner must never mint or replace the shared token")

    async def aclose(self):
        self.closed = True


class MockOwner:
    def __init__(self, client, clock):
        self.client, self.clock = client, clock
        self._blocked = False
        self._verification_in_progress = False
        self._cache = {}
        self.latched = []

    async def cached(self, key, ttl, fetch):
        cached = self._cache.get(key)
        if cached and self.clock.monotonic - cached[0] < ttl:
            return cached[1]
        result = await fetch()
        self._cache[key] = (self.clock.monotonic, result)
        return result

    def latch_auth_failure(self, error):
        self.latched.append((error.condition, error.http_status))


@pytest.fixture
def scanner(monkeypatch):
    import app.discovery.collector as module
    from app.discovery import storage

    monkeypatch.setenv("TOSS_DATA_MODE", "live")
    proof()
    clock = Clock()
    client = MockClient(clock)
    owner = MockOwner(client, clock)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.monotonic))
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now.astimezone(tz) if tz is not None else clock.now.replace(tzinfo=None)
    monkeypatch.setattr(storage, "datetime", FrozenDateTime)
    worker = WholeMarketScanner(owner, config=deepcopy(CONFIG), clock=lambda: clock.now)
    return SimpleNamespace(module=module, storage=storage, clock=clock, client=client,
                           owner=owner, worker=worker)


@pytest.mark.parametrize("guard", ["demo", "owner_blocked", "verification_in_progress", "missing_client",
                                    "failed_auth", "unverified_venue", "missing_proof"])
def test_guarded_scanner_performs_no_http_or_authentication(scanner, monkeypatch, guard):
    if guard == "demo":
        monkeypatch.setenv("TOSS_DATA_MODE", "demo")
    elif guard == "owner_blocked":
        scanner.owner._blocked = True
    elif guard == "verification_in_progress":
        scanner.owner._verification_in_progress = True
    elif guard == "missing_client":
        scanner.owner.client = None
    elif guard == "failed_auth":
        proof(authentication={"status": "failed", "condition": "ip_not_allowed"})
    elif guard == "unverified_venue":
        proof(venue={"verified": False})
    else:
        (runtime_dir() / "toss-probe.json").unlink()
    health = run(scanner.worker.poll_once())
    assert health["status"] == "UNAVAILABLE"
    assert scanner.client.calls == []


def test_all_three_active_common_stock_catalogues_preserve_alphanumeric_unmapped_stock(scanner):
    for _ in range(3):
        run(scanner.worker.poll_once())
        scanner.clock.advance(2)
    catalog_calls = [call for call in scanner.client.calls if call[0] == "stock_universe"]
    assert [call[1] for call in catalog_calls] == ["KOSPI", "KOSDAQ", "KR_ETC"]
    assert all(call[2] == {"status": "ACTIVE", "security_type": "STOCK", "common_share": True}
               for call in catalog_calls)
    snapshot = scanner.storage.discovery_snapshot(scanner.clock.now)
    assert snapshot["universe"]["status"] == "READY"
    assert {row["symbol"] for row in snapshot["stocks"]} == set(SYMBOLS)
    found = next(row for row in snapshot["stocks"] if row["symbol"] == "0101N0")
    assert found["classified"] is False
    assert found["theme_relationship"] == "UNMAPPED_STRENGTH"
    assert "not all exchange products" in snapshot["universe"]["scope"]
    assert not any(call[0] == "authenticate" for call in scanner.client.calls)
    assert scanner.owner.client is scanner.client


def test_cold_price_sweep_slices_one_hundred_symbols_and_keeps_exact_scope(scanner):
    symbols = [f"{index:06d}" for index in range(249)] + ["0101N0"]
    scanner.client.catalogues = {"KOSPI": [listed(symbol) for symbol in symbols], "KOSDAQ": [], "KR_ETC": []}
    for _ in range(3):
        health = run(scanner.worker.poll_once())
        scanner.clock.advance(2)
    price_calls = [call[1] for call in scanner.client.calls if call[0] == "prices"]
    assert [len(batch) for batch in price_calls] == [100, 100, 50]
    assert {symbol for batch in price_calls for symbol in batch} == set(symbols)
    assert health["attempted_in_sweep"] == health["sweep_symbols"] == 250
    assert health["finished_at"] is not None
    assert "does not imply all quotes are LIVE" in health["reason"]


@pytest.mark.parametrize("capability", ["catalog:KOSPI", "sectors"])
def test_generic_new_endpoint_permission_denial_does_not_block_shared_quote_client(scanner, capability):
    scanner.storage.save_catalog([listed("0101N0")], AS_OF, market="KOSDAQ")
    scanner.client.failures[capability] = TossAPIError("http_error", 403)
    if capability == "sectors":
        scanner.worker._metadata_day = AS_OF.date().isoformat()
        scanner.worker._catalog_queue = []
        scanner.worker._sectors_pending = True
    health = run(scanner.worker.poll_once())
    assert not scanner.owner._blocked
    assert health["status"] == "PARTIAL"
    assert health["capabilities"][capability]["status"] == "UNAVAILABLE"
    assert any(call[0] == "prices" for call in scanner.client.calls)
    assert not any(call[0] == "authenticate" for call in scanner.client.calls)
    assert scanner.owner.latched == []


@pytest.mark.parametrize("error", [
    TossAPIError("http_error", 401),
    TossAPIError("access_denied", 403, "access_denied"),
    TossAPIError("ip_not_allowed", 403),
])
def test_authentication_failure_latches_owner_and_stops_every_later_scan(scanner, error):
    scanner.client.failures["catalog:KOSPI"] = error
    first = run(scanner.worker.poll_once())
    calls = len(scanner.client.calls)
    second = run(scanner.worker.poll_once())
    assert first["status"] == "BLOCKED"
    assert second["status"] == "UNAVAILABLE"
    assert scanner.owner._blocked
    assert scanner.owner.latched == [(error.condition, error.http_status)]
    assert len(scanner.client.calls) == calls


def test_selected_analysis_has_priority_and_normalizes_actual_daily_and_minute_receipts(scanner):
    scanner.storage.save_catalog([listed(symbol) for symbol in SYMBOLS[:2]], AS_OF, market="KOSPI")
    scanner.storage.enqueue_analysis(SYMBOLS[0], priority=10, as_of=AS_OF)
    scanner.storage.enqueue_analysis("0101N0", priority=100, as_of=AS_OF)
    scanner.client.candle_delay = 2
    run(scanner.worker.poll_once())
    calls = [call for call in scanner.client.calls if call[0] == "candles"]
    assert calls == [("candles", "0101N0", "1d", 90), ("candles", "0101N0", "1m", 200),
                     ("candles", SYMBOLS[0], "1d", 90), ("candles", SYMBOLS[0], "1m", 200)]
    inputs = scanner.storage.read_analysis_inputs("0101N0", scanner.clock.now)
    assert inputs["status"] == "READY"
    assert inputs["daily_first_seen_at"] == (AS_OF + timedelta(seconds=2)).isoformat()
    assert inputs["minute_first_seen_at"] == (AS_OF + timedelta(seconds=4)).isoformat()
    assert all(row["source"] == "toss" and row["venue"] == "CONSOLIDATED" and row["integrated"]
               for row in inputs["daily"] + inputs["minute"])
    assert inputs["daily"][0]["close"] == inputs["minute"][0]["close"] == 103
    assert "tradedValue" not in inputs["minute"][0]
    assert scanner.storage.read_analysis_inputs("0101N0", AS_OF + timedelta(seconds=3)) is None
    assert scanner.storage.pending_analysis() == []


def test_invalid_candle_currency_fails_analysis_without_synthetic_chart(scanner):
    scanner.storage.save_catalog([listed("0101N0")], AS_OF, market="KOSDAQ")
    scanner.storage.enqueue_analysis("0101N0", as_of=AS_OF)
    scanner.client.candle_failure_currency = True
    run(scanner.worker.poll_once())
    assert scanner.storage.read_analysis_inputs("0101N0", scanner.clock.now) is None
    assert scanner.storage.pending_analysis() == []


def test_missing_ranking_provider_time_remains_nullable_and_never_hot_turnover(scanner):
    scanner.client.ranking_timestamp_null = True
    run(scanner.worker.poll_once())
    snapshot = scanner.storage.discovery_snapshot(scanner.clock.now)
    assert snapshot["global_rankings"]["rankedAt"] is None
    assert scanner.storage.current_hot_symbols(as_of=scanner.clock.now) == []
    assert snapshot["sector_rankings"]["KR"]["provider_at"] is None
    assert snapshot["sector_rankings"]["KR"]["quality"] == "ANALYSIS_ONLY"


def test_rate_limit_backoff_suppresses_only_affected_capability_without_new_auth(scanner):
    scanner.storage.save_catalog([listed("0101N0")], AS_OF, market="KOSDAQ")
    error = TossAPIError("rate_limit", 429, "rate-limit-exceeded")
    error.retry_after_seconds = 9_999
    scanner.client.failures["scan_quotes"] = error
    first = run(scanner.worker.poll_once())
    assert first["capabilities"]["scan_quotes"]["retry_after_seconds"] == 300
    assert first["capabilities"]["scan_quotes"]["status"] == "DELAYED"
    attempts = sum(call[0] == "prices" for call in scanner.client.calls)
    scanner.clock.advance(2)
    run(scanner.worker.poll_once())
    assert sum(call[0] == "prices" for call in scanner.client.calls) == attempts
    assert not scanner.owner._blocked
    assert not any(call[0] == "authenticate" for call in scanner.client.calls)
    scanner.clock.advance(298)
    run(scanner.worker.poll_once())
    assert sum(call[0] == "prices" for call in scanner.client.calls) == attempts + 1


def test_midnight_refreshes_korean_calendar_even_inside_shared_cache_ttl(scanner):
    scanner.clock.now = AS_OF.replace(hour=23, minute=59, second=59)
    run(scanner.worker.poll_once())
    scanner.clock.advance(2)
    run(scanner.worker.poll_once())
    dates = scanner.storage.discovery_snapshot(scanner.clock.now)["calendar"]
    assert dates["KR"]["today"]["date"] == scanner.clock.now.date().isoformat()
    assert scanner.client.calls.count(("calendar", "KR")) == 2
    assert scanner.worker._metadata_day == scanner.clock.now.date().isoformat()


def test_start_is_idempotent_and_close_does_not_close_or_replace_shared_client(scanner):
    async def scenario():
        await scanner.worker.start()
        task = scanner.worker._task
        await scanner.worker.start()
        assert scanner.worker._task is task
        await asyncio.sleep(0)
        await scanner.worker.close()
        await scanner.worker.close()
        assert task.done()
        assert scanner.worker._task is None
    run(scenario())
    assert scanner.owner.client is scanner.client
    assert not scanner.client.closed
    assert not any(call[0] == "authenticate" for call in scanner.client.calls)


def test_demo_start_does_not_spawn_scanning_task(scanner, monkeypatch):
    monkeypatch.setenv("TOSS_DATA_MODE", "demo")
    async def scenario():
        await scanner.worker.start()
        await scanner.worker.close()
    run(scenario())
    assert scanner.worker._task is None
    assert scanner.client.calls == []


def test_overlapping_polls_do_not_duplicate_shared_requests(scanner):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        original = scanner.client.stock_universe
        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        scanner.client.stock_universe = delayed
        first = asyncio.create_task(scanner.worker.poll_once())
        await entered.wait()
        await scanner.worker.poll_once()
        release.set()
        await first
    run(scenario())
    assert sum(call[0] == "stock_universe" for call in scanner.client.calls) == 1


def test_raw_provider_exception_text_is_not_exposed_in_scan_health(scanner):
    marker = "DO_NOT_EXPOSE_CONSTRUCTED_DISCOVERY_ERROR"
    scanner.client.failures["catalog:KOSPI"] = RuntimeError(marker)
    health = run(scanner.worker.poll_once())
    assert marker not in json.dumps(health)
    assert marker not in json.dumps(scanner.storage.discovery_snapshot(scanner.clock.now))


def test_candle_normalization_rejects_conflicting_duplicate_provider_bars():
    row = {"timestamp": AS_OF.isoformat(), "openPrice": "100", "highPrice": "105",
           "lowPrice": "99", "closePrice": "103", "volume": "1000", "currency": "KRW"}
    with pytest.raises(ValueError, match="Conflicting"):
        normalized_candles([row, {**row, "closePrice": "104"}], "1m")


@pytest.mark.parametrize("options", [{"scan_batch_seconds": .5}, {"scan_batch_seconds": float("inf")},
                                      {"quote_batch_size": 0}])
def test_scanner_rejects_invalid_unbounded_cadence(scanner, options):
    with pytest.raises(ValueError, match="bounded discovery"):
        WholeMarketScanner(scanner.owner, config={"discovery": options})


@pytest.mark.parametrize('capability', ['sectors', 'master', 'sector_members:test-sector'])
def test_temporary_metadata_failure_keeps_job_until_backoff_then_retries(scanner, capability):
    worker = scanner.worker
    worker._metadata_day = AS_OF.date().isoformat()
    worker._catalog_queue = []
    worker._sectors_pending = capability == 'sectors'
    worker._master_queue = list(SYMBOLS) if capability == 'master' else []
    worker._member_queue = ['test-sector'] if capability.startswith('sector_members:') else []
    scanner.client.failures[capability] = TossAPIError('http_error', 429)
    run(worker._metadata(scanner.client, scanner.clock.now))
    first_count = len(scanner.client.calls)
    run(worker._metadata(scanner.client, scanner.clock.now))
    assert len(scanner.client.calls) == first_count
    if capability == 'sectors':
        assert worker._sectors_pending
    elif capability == 'master':
        assert worker._master_queue == SYMBOLS
    else:
        assert worker._member_queue == ['test-sector']
    scanner.client.failures.pop(capability)
    scanner.clock.advance(31)
    run(worker._metadata(scanner.client, scanner.clock.now))
    assert len(scanner.client.calls) == first_count + 1
    assert not worker._sectors_pending if capability == 'sectors' else not worker._master_queue if capability == 'master' else not worker._member_queue


def test_actual_daily_chart_is_preserved_when_minute_capability_fails(scanner):
    scanner.storage.save_catalog([listed(SYMBOLS[0])], AS_OF, market='KOSPI')
    scanner.storage.enqueue_analysis(SYMBOLS[0], as_of=AS_OF)
    original = scanner.client.candles
    async def partial(symbol, interval, count):
        if interval == '1m':
            raise TossAPIError('http_error', 429)
        return await original(symbol, interval, count)
    scanner.client.candles = partial
    run(scanner.worker._analysis(scanner.client, AS_OF))
    saved = scanner.storage.read_analysis_inputs(SYMBOLS[0], AS_OF)
    assert saved['daily'][0]['source'] == 'toss'
    assert saved['minute'] == []
    assert saved['status'] == 'PARTIAL'
    assert saved['reason'] == 'MINUTE_UNAVAILABLE'


def test_bounded_owner_probe_pauses_both_readers_and_shutdown_does_not_restart_them(scanner, monkeypatch):
    from app.market.ingestion import IngestionService
    import app.brokers.toss.documented as documented
    events = []
    class Reader:
        def __init__(self, name):
            self.name = name
        async def close(self):
            events.append(self.name + ':close')
        async def start(self):
            events.append(self.name + ':start')
    async def scenario():
        entered = asyncio.Event()
        owner = IngestionService(scanner.client)
        owner._fast_collector, owner._market_scanner = Reader('fast'), Reader('scan')
        async def probe(client):
            assert client is scanner.client
            assert owner._verification_in_progress
            assert events == ['fast:close', 'scan:close']
            entered.set()
            await asyncio.Future()
        monkeypatch.setattr(documented, 'run_probe', probe)
        owner._task = asyncio.create_task(owner.refresh(explicit=True))
        await entered.wait()
        await owner.close()
        assert owner._task.cancelled()
        assert not owner._verification_in_progress
    run(scenario())
    assert all(not event.endswith(':start') for event in events)
    assert scanner.client.closed


@pytest.mark.parametrize('interval,future', [('1d', timedelta(days=1)), ('1m', timedelta(minutes=2))])
def test_candle_normalization_rejects_provider_future_outside_forming_bar(interval, future):
    row = {'timestamp': (AS_OF + future).isoformat(), 'openPrice': '100', 'highPrice': '105',
           'lowPrice': '99', 'closePrice': '103', 'volume': '1000', 'currency': 'KRW'}
    with pytest.raises(ValueError, match='Future'):
        normalized_candles([row], interval, as_of=AS_OF)
