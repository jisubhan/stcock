"""Offline regressions for guarded, shared-client REST turnover collection.

These constructed observations validate the collector; they are not evidence
of authenticated Toss access or live market collection.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
from types import SimpleNamespace

import pytest

from app.brokers.toss.documented import TossAPIError
from app.radar.fast_collector import FastTurnoverCollector
from app.radar.models import KST
from app.radar.storage import read_observations
from app.settings import runtime_dir


BASE = datetime(2026, 10, 12, 9, 10, tzinfo=KST)
SYMBOLS = ["000001", "000002", "000003"]
CONFIG = {
    "feature_flags": {"opening_flow_rotation": True},
    "data": {"max_live_quote_age_seconds": 15},
    "refresh": {"quotes_seconds": 5, "theme_snapshots_seconds": 60},
}


def run(coro):
    return asyncio.run(coro)


def persisted_proof(authentication=None, venue=None):
    report = {
        "authentication": {"status": "passed"} if authentication is None else authentication,
        "venue": {"verified": True} if venue is None else venue,
    }
    (runtime_dir() / "toss-probe.json").write_text(json.dumps(report))


class Clock:
    def __init__(self):
        self.now = BASE
        self.monotonic = 1_000.

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)
        self.monotonic += seconds


class MockClient:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.closed = False
        self.error = None
        self.rank_age_seconds = 0
        self.quote_age_seconds = 0
        self.currency = "KRW"
        self.omit_ranked_at = False
        self.omit_quote_timestamp = False
        self.price_hook = None
        self.rows = [{"symbol": symbol, "tradingAmount": "1000", "tradingVolume": "999999",
                      "price": {"lastPrice": "100"}} for symbol in SYMBOLS]

    async def calendar(self, market):
        self.calls.append(("calendar", market))
        return {"today": {"date": BASE.date().isoformat(), "integrated": {"regularMarket": {
            "startTime": BASE.replace(hour=9, minute=0).isoformat(),
            "endTime": BASE.replace(hour=15, minute=30).isoformat(),
        }}}}

    async def rankings_snapshot(self, market, count):
        self.calls.append(("rankings_snapshot", market, count))
        if self.error is not None:
            raise self.error
        result = {"rankings": deepcopy(self.rows)}
        if not self.omit_ranked_at:
            result["rankedAt"] = (self.clock.now - timedelta(seconds=self.rank_age_seconds)).isoformat()
        return result

    async def prices(self, symbols):
        self.calls.append(("prices", tuple(symbols)))
        if self.price_hook:
            self.price_hook()
        quotes = [{"symbol": symbol, "lastPrice": "100", "currency": self.currency}
                  for symbol in symbols]
        if not self.omit_quote_timestamp:
            for quote in quotes:
                quote["timestamp"] = (self.clock.now - timedelta(seconds=self.quote_age_seconds)).isoformat()
        return quotes

    async def aclose(self):
        self.closed = True


class MockOwner:
    def __init__(self, client):
        self.client = client
        self._blocked = False
        self.cache_calls = []
        self.cache = {}
        self.latched = []

    async def cached(self, key, ttl, fetch):
        self.cache_calls.append((key, ttl))
        if key not in self.cache:
            self.cache[key] = await fetch()
        return self.cache[key]

    def latch_auth_failure(self, error):
        self.latched.append((error.condition, error.http_status))


@pytest.fixture
def harness(monkeypatch):
    import app.radar.fast_collector as module
    import app.radar.runtime as runtime

    monkeypatch.setenv("TOSS_DATA_MODE", "live")
    persisted_proof()
    clock = Clock()
    client = MockClient(clock)
    owner = MockOwner(client)
    overview_calls = []
    calendar_calls = []
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.monotonic))
    monkeypatch.setattr(module, "read_themes", lambda: [{"memberSymbols": SYMBOLS}])
    monkeypatch.setattr(runtime, "current_catalysts", lambda **kwargs: [])
    monkeypatch.setattr(runtime, "current_overview", lambda **kwargs: overview_calls.append(kwargs))
    monkeypatch.setattr(runtime, "on_verified_calendar", lambda calendar, as_of: calendar_calls.append((calendar, as_of)), raising=False)
    collector = FastTurnoverCollector(owner, config=deepcopy(CONFIG), clock=lambda: clock.now)
    return SimpleNamespace(module=module, runtime=runtime, clock=clock, client=client,
                           owner=owner, collector=collector, overview_calls=overview_calls,
                           calendar_calls=calendar_calls)


def test_first_counter_is_partial_then_exact_provider_delta_is_observed(harness):
    first = run(harness.collector.poll_once())
    assert first["status"] == "PARTIAL"
    assert first["coverage"]["symbols_seen"] == 0
    assert first["coverage"]["fresh_quotes_seen"] == 3
    assert read_observations(BASE, "KRX_REGULAR") == []
    assert harness.overview_calls == []

    harness.clock.advance(5)
    for row in harness.client.rows:
        row["tradingAmount"] = "1175"
        row["price"]["lastPrice"] = "111"
    second = run(harness.collector.poll_once())
    observations = read_observations(harness.clock.now, "KRX_REGULAR")
    assert second["status"] == "LIVE"
    assert second["coverage"]["symbols_seen"] == 3
    assert len(observations) == 3
    assert all(item.turnover_krw == 175 for item in observations)
    assert all(item.interval_start == BASE and item.interval_end == harness.clock.now for item in observations)
    assert all(item.price_open == 100 and item.price_close == 111 for item in observations)
    assert all(item.venue == "CONSOLIDATED" and item.mode == "live" for item in observations)
    assert harness.client.calls.count(("calendar", "KR")) == 1
    assert harness.owner.cache_calls == [("calendar_kr", 300), ("calendar_kr", 300)]
    assert [stamp for _, stamp in harness.calendar_calls] == [BASE, harness.clock.now]


def test_observation_and_health_use_provider_time_not_receipt_time(harness):
    harness.client.rank_age_seconds = 2
    run(harness.collector.poll_once())
    harness.clock.advance(5)
    for row in harness.client.rows:
        row["tradingAmount"] = "1100"
    health = run(harness.collector.poll_once())
    expected_provider_time = harness.clock.now - timedelta(seconds=2)
    observations = read_observations(harness.clock.now, "KRX_REGULAR")
    assert health["last_success_at"] == expected_provider_time.isoformat()
    assert health["latency_seconds"] == 2
    assert all(item.observed_at == expected_provider_time for item in observations)
    assert all(item.first_seen_at == harness.clock.now for item in observations)
    assert all((item.interval_end - item.interval_start).total_seconds() == 5 for item in observations)


def test_missing_ranking_timestamp_never_substitutes_receipt_time(harness):
    harness.client.omit_ranked_at = True
    health = run(harness.collector.poll_once())
    assert health["status"] == "UNAVAILABLE"
    assert health["last_success_at"] is None
    assert not any(call[0] == "prices" for call in harness.client.calls)
    assert read_observations(BASE, "KRX_REGULAR") == []


@pytest.mark.parametrize("provider_age,expected_status", [(16, "STALE"), (-1, "UNAVAILABLE")])
def test_stale_or_future_ranking_provider_time_prevents_quote_fetch(harness, provider_age, expected_status):
    harness.client.rank_age_seconds = provider_age
    health = run(harness.collector.poll_once())
    assert health["status"] == expected_status
    assert not any(call[0] == "prices" for call in harness.client.calls)
    assert harness.overview_calls == []


def test_provider_and_quote_fifteen_second_boundary_is_accepted(harness):
    harness.client.rank_age_seconds = harness.client.quote_age_seconds = 15
    health = run(harness.collector.poll_once())
    assert health["status"] == "PARTIAL"
    assert health["latency_seconds"] == 15
    assert health["coverage"]["fresh_quotes_seen"] == 3


def test_provider_calendar_with_no_matching_active_session_prevents_quotes(harness):
    harness.owner.cache["calendar_kr"] = {"today": {"integrated": {"regularMarket": {
        "startTime": BASE.replace(hour=10).isoformat(), "endTime": BASE.replace(hour=15).isoformat(),
    }}}}
    health = run(harness.collector.poll_once())
    assert health["status"] == "UNAVAILABLE"
    assert not any(call[0] == "prices" for call in harness.client.calls)


def test_ranking_freshness_is_rechecked_after_slow_price_batch(harness):
    harness.client.price_hook = lambda: harness.clock.advance(16)
    health = run(harness.collector.poll_once())
    assert health["status"] == "STALE"
    assert health["latency_seconds"] == 16
    assert read_observations(harness.clock.now, "KRX_REGULAR") == []
    assert harness.overview_calls == []


@pytest.mark.parametrize("quote_age,fresh_count", [(15, 3), (15.001, 0), (-1, 0)])
def test_quote_freshness_boundary_and_future_quote_exclusion(harness, quote_age, fresh_count):
    harness.client.quote_age_seconds = quote_age
    health = run(harness.collector.poll_once())
    assert health["coverage"]["fresh_quotes_seen"] == fresh_count
    assert health["coverage"]["symbols_seen"] == 0
    assert read_observations(BASE, "KRX_REGULAR") == []


@pytest.mark.parametrize("bad_quote", ["missing_timestamp", "wrong_currency"])
def test_unverified_quote_time_or_currency_does_not_seed_counters(harness, bad_quote):
    harness.client.omit_quote_timestamp = bad_quote == "missing_timestamp"
    harness.client.currency = "USD" if bad_quote == "wrong_currency" else "KRW"
    health = run(harness.collector.poll_once())
    assert health["coverage"]["fresh_quotes_seen"] == 0
    assert read_observations(BASE, "KRX_REGULAR") == []


def test_watch_universe_and_quote_batches_stay_bounded(harness, monkeypatch):
    ranked_symbols = [f"{index:06d}" for index in range(100)]
    harness.client.rows = [{"symbol": symbol, "tradingAmount": "1000", "price": {"lastPrice": "100"}}
                           for symbol in ranked_symbols]
    registered = [f"{index:06d}" for index in range(50, 275)]
    news_symbols = [f"{index:06d}" for index in range(250, 350)]
    monkeypatch.setattr(harness.module, "read_themes", lambda: [{"memberSymbols": registered}])
    monkeypatch.setattr(harness.runtime, "current_catalysts", lambda **kwargs: [{"tickers": news_symbols}])
    health = run(harness.collector.poll_once())
    batches = [call[1] for call in harness.client.calls if call[0] == "prices"]
    requested = [symbol for batch in batches for symbol in batch]
    assert ("rankings_snapshot", "KR", 100) in harness.client.calls
    assert len(requested) == len(set(requested)) == 200
    assert all(len(batch) <= 100 for batch in batches)
    assert requested[:100] == ranked_symbols
    assert health["coverage"]["quotes_requested"] == 200
    assert "not the entire market" in health["coverage"]["scope"]


def test_extra_registered_and_news_quotes_do_not_invent_turnover(harness, monkeypatch):
    registered_extra, news_extra = "880001", "990001"
    monkeypatch.setattr(harness.module, "read_themes", lambda: [{"memberSymbols": SYMBOLS + [registered_extra]}])
    monkeypatch.setattr(harness.runtime, "current_catalysts", lambda **kwargs: [{"tickers": [news_extra]}])
    run(harness.collector.poll_once())
    harness.clock.advance(5)
    for row in harness.client.rows:
        row["tradingAmount"] = "1100"
    health = run(harness.collector.poll_once())
    observations = read_observations(harness.clock.now, "KRX_REGULAR")
    assert health["coverage"]["quotes_requested"] == 5
    assert health["coverage"]["symbols_expected"] == 3
    assert {item.symbol for item in observations} == set(SYMBOLS)


def test_explicit_demo_start_and_poll_perform_no_http(harness, monkeypatch):
    monkeypatch.setenv("TOSS_DATA_MODE", "demo")
    async def scenario():
        await harness.collector.start()
        health = await harness.collector.poll_once()
        assert harness.collector._task is None
        await harness.collector.close()
        return health
    health = run(scenario())
    assert health["status"] == "UNAVAILABLE"
    assert harness.client.calls == []
    assert not harness.client.closed


@pytest.mark.parametrize("guard", ["missing_proof", "auth_failed", "venue_unverified", "owner_blocked",
                                    "verification_in_progress", "missing_client", "missing_typed_rankings", "feature_disabled"])
def test_verification_and_owner_guards_never_make_http(harness, guard):
    if guard == "missing_proof":
        (runtime_dir() / "toss-probe.json").unlink()
    elif guard == "auth_failed":
        persisted_proof(authentication={"status": "failed", "condition": "ip_not_allowed"})
    elif guard == "venue_unverified":
        persisted_proof(venue={"verified": False})
    elif guard == "owner_blocked":
        harness.owner._blocked = True
    elif guard == "verification_in_progress":
        harness.owner._verification_in_progress = True
    elif guard == "missing_client":
        harness.owner.client = None
    elif guard == "missing_typed_rankings":
        harness.client.rankings_snapshot = None
    else:
        harness.collector.config["feature_flags"]["opening_flow_rotation"] = False
    health = run(harness.collector.poll_once())
    assert health["status"] == "UNAVAILABLE"
    assert harness.client.calls == []
    assert harness.overview_calls == []


def test_start_is_idempotent_and_close_keeps_shared_client_owned_by_ingestion(harness):
    async def scenario():
        await harness.collector.start()
        first_task = harness.collector._task
        await harness.collector.start()
        assert harness.collector._task is first_task
        await asyncio.sleep(0)
        await harness.collector.close()
        await harness.collector.close()
        assert first_task.done()
        assert harness.collector._task is None
    run(scenario())
    assert harness.owner.client is harness.client
    assert not harness.client.closed
    assert harness.client.calls.count(("rankings_snapshot", "KR", 100)) == 1


def test_start_can_wait_for_verification_and_close_without_http(harness):
    persisted_proof(venue={"verified": False})
    async def scenario():
        await harness.collector.start()
        await asyncio.sleep(0)
        await harness.collector.close()
        assert harness.collector._task is None
    run(scenario())
    assert harness.client.calls == []
    assert not harness.client.closed


def test_close_cancels_inflight_request_without_closing_owner_client(harness):
    async def scenario():
        entered = asyncio.Event()
        async def pending_rankings(*args, **kwargs):
            entered.set()
            await asyncio.Future()
        harness.client.rankings_snapshot = pending_rankings
        await harness.collector.start()
        task = harness.collector._task
        await entered.wait()
        await harness.collector.close()
        assert task.done()
        assert harness.collector._task is None
    run(scenario())
    assert not harness.client.closed


def test_ip_refusal_latches_owner_and_stops_subsequent_attempts(harness):
    harness.client.error = TossAPIError("ip_not_allowed", 403, "access_denied")
    first = run(harness.collector.poll_once())
    calls_after_failure = len(harness.client.calls)
    second = run(harness.collector.poll_once())
    assert first["status"] == second["status"] == "UNAVAILABLE"
    assert first["condition"] == "ip_not_allowed"
    assert first["http_status"] == 403
    assert harness.owner._blocked
    assert harness.owner.latched == [("ip_not_allowed", 403)]
    assert len(harness.client.calls) == calls_after_failure


def test_async_owner_auth_latch_is_awaited(harness):
    async def latch(error):
        await asyncio.sleep(0)
        harness.owner.latched.append((error.condition, error.http_status))
    harness.owner.latch_auth_failure = latch
    harness.client.error = TossAPIError("authentication_rejected", 401)
    run(harness.collector.poll_once())
    assert harness.owner.latched == [("authentication_rejected", 401)]
    assert harness.owner._blocked


@pytest.mark.parametrize("async_hook", [False, True])
def test_failing_auth_persistence_hook_keeps_in_memory_retry_latch(harness, async_hook):
    secret_marker = "DO_NOT_EXPOSE_CONSTRUCTED_AUTH_PERSISTENCE_ERROR"
    def failure(error):
        raise RuntimeError(secret_marker)
    async def async_failure(error):
        await asyncio.sleep(0)
        failure(error)
    harness.owner.latch_auth_failure = async_failure if async_hook else failure
    harness.client.error = TossAPIError("ip_not_allowed", 403)
    health = run(harness.collector.poll_once())
    calls = len(harness.client.calls)
    run(harness.collector.poll_once())
    assert harness.owner._blocked
    assert len(harness.client.calls) == calls
    assert health["status"] == "UNAVAILABLE"
    assert secret_marker not in json.dumps(health)


def test_rate_limit_backoff_suppresses_early_attempts_and_stays_bounded(harness):
    error = TossAPIError("rate_limit", 429, "rate-limit-exceeded")
    error.retry_after_seconds = 9_999
    harness.client.error = error
    first = run(harness.collector.poll_once())
    assert first["status"] == "DELAYED"
    assert first["retry_after_seconds"] == 300
    attempts = sum(call[0] == "rankings_snapshot" for call in harness.client.calls)
    harness.clock.advance(299)
    run(harness.collector.poll_once())
    assert sum(call[0] == "rankings_snapshot" for call in harness.client.calls) == attempts
    harness.clock.advance(1)
    second = run(harness.collector.poll_once())
    assert second["retry_after_seconds"] <= 300
    assert sum(call[0] == "rankings_snapshot" for call in harness.client.calls) == attempts + 1
    assert not harness.owner._blocked


def test_first_rate_limit_backoff_is_thirty_seconds_and_success_resets_it(harness):
    harness.client.error = TossAPIError("rate_limit", 429)
    health = run(harness.collector.poll_once())
    assert health["retry_after_seconds"] == 30
    harness.clock.advance(30)
    harness.client.error = None
    assert run(harness.collector.poll_once())["status"] == "PARTIAL"
    harness.clock.advance(5)
    for row in harness.client.rows:
        row["tradingAmount"] = "1100"
    assert run(harness.collector.poll_once())["status"] == "LIVE"
    assert harness.collector._failures == 0
    assert harness.collector._next_allowed == 0


def test_arbitrary_exception_text_is_absent_from_safe_health(harness):
    secret_marker = "DO_NOT_EXPOSE_CONSTRUCTED_ERROR_PAYLOAD"
    harness.client.error = RuntimeError(secret_marker)
    health = run(harness.collector.poll_once())
    assert health["status"] == "DELAYED"
    assert secret_marker not in json.dumps(health)
    copy = harness.collector.status()
    copy["reason"] = secret_marker
    assert secret_marker not in json.dumps(harness.collector.status())


def test_fresh_observations_autonomously_persist_overview_at_sixty_second_cadence(harness):
    for index, seconds in enumerate([0, 5, 5, 55]):
        harness.clock.advance(seconds)
        for row in harness.client.rows:
            row["tradingAmount"] = str(1_000 + index * 100)
        run(harness.collector.poll_once())
    assert len(harness.overview_calls) == 2
    assert harness.overview_calls[0] == {"as_of": BASE + timedelta(seconds=5), "persist": True}
    assert harness.overview_calls[1] == {"as_of": BASE + timedelta(seconds=65), "persist": True}


def test_rotation_audit_failure_does_not_backoff_successful_collection(harness, monkeypatch):
    secret_marker = "DO_NOT_EXPOSE_CONSTRUCTED_ROTATION_AUDIT_ERROR"
    def failing_audit(**kwargs):
        harness.overview_calls.append(kwargs)
        raise RuntimeError(secret_marker)
    monkeypatch.setattr(harness.runtime, "current_overview", failing_audit)
    run(harness.collector.poll_once())
    harness.clock.advance(5)
    for row in harness.client.rows:
        row["tradingAmount"] = "1100"
    health = run(harness.collector.poll_once())
    assert health["status"] == "LIVE"
    assert health["audit_status"] == "UNAVAILABLE"
    assert secret_marker not in json.dumps(health)
    assert harness.collector._failures == 0
    assert harness.collector._next_allowed == 0
    assert len(read_observations(harness.clock.now, "KRX_REGULAR")) == 3

    harness.clock.advance(5)
    for row in harness.client.rows:
        row["tradingAmount"] = "1200"
    assert run(harness.collector.poll_once())["status"] == "LIVE"
    assert len(harness.overview_calls) == 1
    monkeypatch.setattr(harness.runtime, "current_overview", lambda **kwargs: harness.overview_calls.append(kwargs))
    harness.clock.advance(55)
    for row in harness.client.rows:
        row["tradingAmount"] = "1300"
    recovered = run(harness.collector.poll_once())
    assert recovered["audit_status"] == "READY"
    assert recovered["audit_reason"] is None
    assert len(harness.overview_calls) == 2


def test_counter_reset_does_not_invent_negative_or_backfilled_turnover(harness):
    run(harness.collector.poll_once())
    harness.clock.advance(5)
    for row in harness.client.rows:
        row["tradingAmount"] = "900"
    assert run(harness.collector.poll_once())["status"] == "PARTIAL"
    assert read_observations(harness.clock.now, "KRX_REGULAR") == []
    harness.clock.advance(5)
    for row in harness.client.rows:
        row["tradingAmount"] = "950"
    health = run(harness.collector.poll_once())
    assert health["status"] == "LIVE"
    observations = read_observations(harness.clock.now, "KRX_REGULAR")
    assert all(item.turnover_krw == 50 for item in observations)
    assert all(item.interval_start == BASE + timedelta(seconds=5) for item in observations)


def test_same_provider_time_counter_revision_preserves_point_in_time_knowledge(harness):
    start = BASE.replace(minute=1)
    second = start + timedelta(minutes=1)
    next_provider_time = second + timedelta(minutes=1)
    harness.clock.now = start
    for row in harness.client.rows:
        row["tradingAmount"] = "100"
    run(harness.collector.poll_once())

    harness.clock.advance(60)
    for row in harness.client.rows:
        row["tradingAmount"] = "200"
    run(harness.collector.poll_once())

    # The provider revises its 09:02 counter ten seconds after the first version.
    harness.clock.advance(10)
    harness.client.rank_age_seconds = 10
    for row in harness.client.rows:
        row["tradingAmount"] = "250"
    run(harness.collector.poll_once())

    earlier = read_observations(second + timedelta(seconds=5), "KRX_REGULAR")
    assert len(earlier) == 3
    assert all(item.interval_start == start and item.interval_end == second for item in earlier)
    assert all(item.turnover_krw == 100 for item in earlier)
    assert all(item.first_seen_at <= second + timedelta(seconds=5) for item in earlier)

    harness.clock.advance(50)
    harness.client.rank_age_seconds = 0
    for row in harness.client.rows:
        row["tradingAmount"] = "300"
    run(harness.collector.poll_once())

    observations = read_observations(next_provider_time, "KRX_REGULAR")
    latest = {}
    for item in observations:
        key = (item.symbol, item.interval_start, item.interval_end, item.venue)
        if key not in latest or item.first_seen_at > latest[key].first_seen_at:
            latest[key] = item
    for symbol in SYMBOLS:
        revised = latest[(symbol, start, second, "CONSOLIDATED")]
        following = latest[(symbol, second, next_provider_time, "CONSOLIDATED")]
        assert revised.turnover_krw == 150
        assert revised.first_seen_at == second + timedelta(seconds=10)
        assert following.turnover_krw == 50
    # Re-querying a past time after all writes still returns the original fact.
    assert all(item.turnover_krw == 100 for item in read_observations(
        second + timedelta(seconds=5), "KRX_REGULAR"))


def test_overlapping_manual_polls_do_not_issue_duplicate_requests(harness):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        original = harness.client.rankings_snapshot
        async def slow_rankings(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        harness.client.rankings_snapshot = slow_rankings
        first = asyncio.create_task(harness.collector.poll_once())
        await entered.wait()
        await harness.collector.poll_once()
        release.set()
        await first
    run(scenario())
    assert harness.client.calls.count(("rankings_snapshot", "KR", 100)) == 1


@pytest.mark.parametrize("interval", [0, -1, float("inf"), float("nan")])
def test_polling_interval_requires_positive_finite_value(harness, interval):
    with pytest.raises(ValueError, match="positive finite"):
        FastTurnoverCollector(harness.owner, interval=interval, config=CONFIG)
