"""Offline point-in-time, scope and cache regressions for market discovery."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json

import pytest

from app.radar.models import KST
from app.settings import runtime_dir


AS_OF = datetime(2026, 10, 12, 9, 10, tzinfo=KST)
SYMBOLS = ["000001", "0101N0", "999999"]


def stock(symbol=SYMBOLS[0], name=None, **overrides):
    values = {"symbol": symbol, "name": name or "Constructed " + symbol,
              "securityType": "STOCK", "isCommonShare": True,
              "isinCode": "KR" + symbol + "0000"}
    values.update(overrides)
    return values


def quote(symbol=SYMBOLS[0], **overrides):
    values = {"symbol": symbol, "lastPrice": "100", "currency": "KRW",
              "timestamp": AS_OF.isoformat()}
    values.update(overrides)
    return values


def sector(sector_id="test-sector", **overrides):
    values = {"sectorId": sector_id, "name": "Constructed provider sector",
              "depth": 0, "parentSectorId": None}
    values.update(overrides)
    return values


def membership_stock(symbol=SYMBOLS[0], **overrides):
    values = {"symbol": symbol, "name": "Constructed " + symbol, "volume": "100",
              "tradingAmount": "10000", "marketCapitalization": "1000000", "currency": "KRW"}
    values.update(overrides)
    return values


def candle(stamp, **overrides):
    values = {"timestamp": stamp.isoformat(), "openPrice": "100", "highPrice": "105",
              "lowPrice": "99", "closePrice": "103", "volume": "1000", "currency": "KRW"}
    values.update(overrides)
    return values


def calendar():
    return {"today": {"date": AS_OF.date().isoformat(), "integrated": {"regularMarket": {
        "startTime": AS_OF.replace(minute=0).isoformat(),
        "endTime": AS_OF.replace(hour=15, minute=30).isoformat(),
    }}}}


def by_symbol(snapshot):
    return {row["symbol"]: row for row in snapshot["stocks"]}


@pytest.fixture
def discovery(monkeypatch):
    from app.discovery import storage

    monkeypatch.setenv("TOSS_DATA_MODE", "live")
    (runtime_dir() / "toss-probe.json").write_text(json.dumps({
        "authentication": {"status": "passed"}, "venue": {"verified": True},
    }))
    return storage


def test_market_quote_warehouse_accepts_alphanumeric_korean_symbols(discovery):
    result = discovery.save_market_quotes([quote("0101N0")], AS_OF, expected_symbols=["0101N0"])
    assert result["stored"] == 1
    assert result["rejected"] == 0


@pytest.mark.parametrize("invalid", [
    {"timestamp": (AS_OF + timedelta(seconds=1)).isoformat()},
    {"timestamp": "2026-10-12T09:10:00"},
    {"lastPrice": "0"}, {"lastPrice": "-1"}, {"lastPrice": "nan"},
    {"lastPrice": "inf"}, {"lastPrice": True}, {"currency": "USD"},
])
def test_invalid_or_future_market_quote_is_rejected(discovery, invalid):
    result = discovery.save_market_quotes([quote(**invalid)], AS_OF, expected_symbols=[SYMBOLS[0]])
    assert result["stored"] == 0
    assert result["rejected"] == 1


def test_market_quote_warehouse_does_not_change_monitored_quotes_health(discovery):
    from app.radar.storage import read_provider_health, save_provider_health

    monitored_health = {"status": "PARTIAL", "requested_symbols": ["005930"],
                        "coverage": {"symbols_expected": 200}}
    save_provider_health("TOSS_QUOTES", monitored_health, AS_OF)
    discovery.save_market_quotes([quote(symbol) for symbol in SYMBOLS], AS_OF, expected_symbols=SYMBOLS)
    assert read_provider_health()["TOSS_QUOTES"] == monitored_health


def test_market_quote_warehouse_cannot_replace_two_hundred_symbol_watch_cache(discovery):
    from app.radar.quotes import quotes_snapshot, save_quotes
    from app.radar.storage import save_state

    watch_symbols = [f"{index:06d}" for index in range(200)]
    save_state("calendar:" + AS_OF.date().isoformat(), calendar(), AS_OF)
    save_quotes([quote(symbol, lastPrice="777") for symbol in watch_symbols], AS_OF, watch_symbols)
    full_scan_symbols = watch_symbols + ["0101N0", "999999"]
    discovery.save_market_quotes([quote(symbol, lastPrice="100") for symbol in full_scan_symbols],
                                 AS_OF, expected_symbols=full_scan_symbols)
    watch = quotes_snapshot(AS_OF)
    assert watch["coverage"]["symbols_expected"] == 200
    assert {row["symbol"] for row in watch["items"]} == set(watch_symbols)
    assert all(row["price"] == 777 for row in watch["items"])


def test_unknown_symbol_cannot_be_queued_without_catalogue_or_observed_ranking(discovery):
    result = discovery.enqueue_analysis("0101N0", priority=100, as_of=AS_OF)
    assert result["status"] == "UNAVAILABLE"
    assert discovery.pending_analysis(limit=10) == []


def test_analysis_queue_is_idempotent_and_prioritizes_selected_symbol(discovery):
    discovery.save_catalog([stock(symbol) for symbol in SYMBOLS], AS_OF, market="KOSDAQ")
    discovery.enqueue_analysis(SYMBOLS[0], priority=10, as_of=AS_OF)
    discovery.enqueue_analysis(SYMBOLS[1], priority=100, as_of=AS_OF)
    discovery.enqueue_analysis(SYMBOLS[1], priority=100, as_of=AS_OF + timedelta(seconds=1))
    pending = discovery.pending_analysis(limit=10)
    assert len(pending) == 2
    assert [item["symbol"] for item in pending] == [SYMBOLS[1], SYMBOLS[0]]
    assert pending[0]["priority"] >= pending[1]["priority"]
    assert len(discovery.pending_analysis(limit=1)) == 1


def test_finishing_analysis_removes_job_from_pending_without_duplicate_requeue(discovery):
    discovery.save_catalog([stock()], AS_OF, market="KOSPI")
    discovery.enqueue_analysis(SYMBOLS[0], as_of=AS_OF)
    discovery.save_analysis_inputs(SYMBOLS[0], [candle(AS_OF - timedelta(days=1))],
                                   [candle(AS_OF - timedelta(minutes=1))], AS_OF, AS_OF)
    discovery.finish_analysis(SYMBOLS[0], status="READY")
    assert discovery.pending_analysis(limit=10) == []
    assert discovery.enqueue_analysis(SYMBOLS[0], as_of=AS_OF)["status"] == "READY"


def test_analysis_inputs_do_not_import_later_receipts_into_past_queries(discovery):
    daily = [candle(AS_OF - timedelta(days=1))]
    minute = [candle(AS_OF - timedelta(minutes=1))]
    later_receipt = AS_OF + timedelta(seconds=10)
    discovery.save_analysis_inputs(SYMBOLS[0], daily, minute, AS_OF, later_receipt,
                                   source_urls=["https://example.test/constructed-candles"])
    before = discovery.read_analysis_inputs(SYMBOLS[0], AS_OF + timedelta(seconds=5))
    assert before is None or not before.get("minute")
    available = discovery.read_analysis_inputs(SYMBOLS[0], later_receipt)
    assert available["daily"] == daily
    assert available["minute"] == minute


def test_current_hot_symbols_do_not_import_newly_discovered_symbols_into_past(discovery):
    later = AS_OF + timedelta(minutes=1)
    discovery.save_global_rankings({"rankedAt": later.isoformat(), "rankings": [
        {"symbol": "0101N0", "tradingAmount": "10000", "tradingVolume": "100",
         "price": {"lastPrice": "100", "basePrice": "90"}},
    ]}, later)
    assert "0101N0" not in discovery.current_hot_symbols(as_of=AS_OF)
    assert "0101N0" in discovery.current_hot_symbols(as_of=later)


def test_observed_global_ranking_can_queue_uncatalogued_selected_symbol(discovery):
    discovery.save_global_rankings({"rankedAt": AS_OF.isoformat(), "rankings": [{"symbol": "0101N0"}]}, AS_OF)
    result = discovery.enqueue_analysis("0101N0", priority=100, as_of=AS_OF)
    assert result["status"] == "QUEUED"
    assert [row["symbol"] for row in discovery.pending_analysis()] == ["0101N0"]


def test_analysis_input_revision_keeps_actual_earlier_candles_immutable(discovery):
    daily = [candle(AS_OF - timedelta(days=1))]
    minute = [candle(AS_OF - timedelta(minutes=1))]
    discovery.save_analysis_inputs(SYMBOLS[0], daily, minute, AS_OF, AS_OF)
    later = AS_OF + timedelta(seconds=10)
    revised_daily = [candle(AS_OF - timedelta(days=1), closePrice="104")]
    discovery.save_analysis_inputs(SYMBOLS[0], revised_daily, minute, later, later)
    assert discovery.read_analysis_inputs(SYMBOLS[0], AS_OF + timedelta(seconds=5))["daily"] == daily
    assert discovery.read_analysis_inputs(SYMBOLS[0], later)["daily"] == revised_daily


def test_catalogue_revision_keeps_original_name_and_new_symbol_out_of_past(discovery):
    discovery.save_catalog([stock(name="Original name")], AS_OF, market="KOSPI")
    later = AS_OF + timedelta(minutes=1)
    discovery.save_catalog([stock(name="Revised name"), stock("0101N0")], later, market="KOSPI")
    old = by_symbol(discovery.discovery_snapshot(AS_OF + timedelta(seconds=30)))
    new = by_symbol(discovery.discovery_snapshot(later))
    assert set(old) == {SYMBOLS[0]}
    assert old[SYMBOLS[0]]["name"] == "Original name"
    assert old[SYMBOLS[0]]["first_seen_at"] == AS_OF.isoformat()
    assert set(new) == {SYMBOLS[0], "0101N0"}
    assert new[SYMBOLS[0]]["name"] == "Revised name"
    assert new["0101N0"]["first_seen_at"] == later.isoformat()


def test_membership_first_seen_never_classifies_earlier_stock_snapshot(discovery):
    discovery.save_catalog([stock()], AS_OF, market="KOSPI")
    later = AS_OF + timedelta(minutes=1)
    discovery.save_sectors([sector()], later)
    discovery.save_memberships("test-sector", [membership_stock()], later)
    old = discovery.discovery_snapshot(AS_OF + timedelta(seconds=30))
    new = discovery.discovery_snapshot(later)
    assert old["memberships"] == []
    assert old["sectors"] == []
    assert by_symbol(old)[SYMBOLS[0]]["sector_ids"] == []
    assert by_symbol(old)[SYMBOLS[0]]["classified"] is False
    assert by_symbol(new)[SYMBOLS[0]]["sector_ids"] == ["test-sector"]
    assert by_symbol(new)[SYMBOLS[0]]["theme_relationship"] == "OFFICIAL_SECTOR_MEMBER"
    assert new["memberships"][0]["first_seen_at"] == later.isoformat()
    assert new["memberships"][0]["provider_at"] is None
    assert new["memberships"][0]["quality"] == "ANALYSIS_ONLY"


def test_membership_removal_preserves_old_graph_and_removes_current_relationship(discovery):
    discovery.save_catalog([stock()], AS_OF, market="KOSPI")
    discovery.save_memberships("test-sector", [membership_stock()], AS_OF)
    later = AS_OF + timedelta(minutes=1)
    discovery.save_memberships("test-sector", [], later)
    assert by_symbol(discovery.discovery_snapshot(AS_OF))[SYMBOLS[0]]["sector_ids"] == ["test-sector"]
    assert by_symbol(discovery.discovery_snapshot(later))[SYMBOLS[0]]["sector_ids"] == []
    assert discovery.discovery_snapshot(later)["memberships"] == []


def test_restored_membership_has_new_knowledge_time_without_rewriting_removed_snapshot(discovery):
    discovery.save_catalog([stock()], AS_OF, market="KOSPI")
    discovery.save_memberships("test-sector", [membership_stock()], AS_OF)
    removed_at = AS_OF + timedelta(minutes=1)
    restored_at = AS_OF + timedelta(minutes=2)
    discovery.save_memberships("test-sector", [], removed_at)
    discovery.save_memberships("test-sector", [membership_stock()], restored_at)
    removed = discovery.discovery_snapshot(removed_at)
    restored = discovery.discovery_snapshot(restored_at)
    assert removed["memberships"] == []
    assert by_symbol(removed)[SYMBOLS[0]]["sector_ids"] == []
    assert by_symbol(restored)[SYMBOLS[0]]["sector_ids"] == ["test-sector"]
    assert restored["memberships"][0]["first_seen_at"] >= restored_at.isoformat()
    assert by_symbol(discovery.discovery_snapshot(AS_OF))[SYMBOLS[0]]["sector_ids"] == ["test-sector"]


def test_dynamic_unmapped_catalogue_stock_stays_visible_without_configured_theme(discovery):
    discovery.save_catalog([stock("0101N0", name="Newly discovered company")], AS_OF, market="KR_ETC")
    discovery.save_calendar("KR", calendar(), AS_OF)
    discovery.save_market_quotes([quote("0101N0")], AS_OF, expected_symbols=["0101N0"])
    data = discovery.discovery_snapshot(AS_OF)
    found = by_symbol(data)["0101N0"]
    assert found["name"] == "Newly discovered company"
    assert found["market"] == "KR_ETC"
    assert found["quote"]["price"] == 100
    assert found["quote"]["status"] == "LIVE"
    assert found["classified"] is False
    assert found["theme_relationship"] == "UNMAPPED_STRENGTH"
    assert data["universe"]["unclassified"] == 1


def test_later_stock_master_does_not_retroactively_invent_nxt_support(discovery):
    discovery.save_catalog([stock("0101N0")], AS_OF, market="KOSDAQ")
    later = AS_OF + timedelta(seconds=10)
    discovery.save_stock_master([{"symbol": "0101N0", "name": "Observed company", "currency": "KRW",
                                 "status": "ACTIVE", "market": "KOSDAQ",
                                 "koreanMarketDetail": {"nxtSupported": True}}], later)
    assert by_symbol(discovery.discovery_snapshot(AS_OF))["0101N0"]["nxt_supported"] is None
    assert by_symbol(discovery.discovery_snapshot(later))["0101N0"]["nxt_supported"] is True


def test_sector_rankings_without_provider_time_remain_analysis_only(discovery):
    items = [{"rank": 1, "sectorId": "test-sector", "name": "Constructed sector",
              "changeRate": ".05", "tradingAmount": "100000", "marketCapitalization": "1000000", "currency": "KRW"}]
    discovery.save_sector_rankings("KR", items, AS_OF)
    discovery.save_sector_rankings("KR", items, AS_OF, duration="1w")
    data = discovery.discovery_snapshot(AS_OF)["sector_rankings"]["KR"]
    assert data["items"] == data["weekly_items"] == items
    assert data["provider_at"] is None
    assert data["quality"] == "ANALYSIS_ONLY"
    assert data["first_seen_at"] == AS_OF.isoformat()
    assert "turnover_velocity_krw_per_min" not in data


def test_partial_provider_catalogue_never_claims_all_exchange_market_coverage(discovery):
    discovery.save_catalog([stock(symbol) for symbol in SYMBOLS], AS_OF, market="KOSPI", status="PARTIAL")
    discovery.save_market_quotes([quote()], AS_OF, expected_symbols=SYMBOLS)
    coverage = discovery.discovery_snapshot(AS_OF)["universe"]
    assert coverage["status"] == "PARTIAL"
    assert coverage["expected"] == 3
    assert coverage["observed"] == 1
    assert coverage["missing"] == 2
    assert coverage["markets"] == ["KOSPI"]
    assert "Toss-provided" in coverage["scope"]
    assert "not all exchange products" in coverage["scope"]


def test_ready_catalogue_metadata_does_not_claim_missing_quotes_are_observed(discovery):
    discovery.save_catalog([stock()], AS_OF, market="KOSPI")
    discovery.save_catalog([stock("0101N0")], AS_OF, market="KOSDAQ")
    discovery.save_catalog([], AS_OF, market="KR_ETC")
    coverage = discovery.discovery_snapshot(AS_OF)["universe"]
    assert coverage["status"] == "READY"
    assert coverage["expected"] == coverage["missing"] == 2
    assert coverage["observed"] == coverage["quote_fresh"] == 0


@pytest.mark.parametrize("age,status", [(15, "LIVE"), (15.001, "STALE")])
def test_normalized_market_quote_freshness_uses_provider_clock(discovery, age, status):
    discovery.save_catalog([stock()], AS_OF, market="KOSPI")
    discovery.save_calendar("KR", calendar(), AS_OF)
    provider_at = AS_OF - timedelta(seconds=age)
    discovery.save_market_quotes([quote(timestamp=provider_at.isoformat())], AS_OF)
    actual = by_symbol(discovery.discovery_snapshot(AS_OF))[SYMBOLS[0]]["quote"]
    assert actual["price"] == 100
    assert actual["quote_at"] == provider_at.isoformat()
    assert actual["first_seen_at"] == AS_OF.isoformat()
    assert actual["status"] == status
    assert actual["latency_seconds"] == pytest.approx(age)
    assert actual["source"] == "toss"


def test_nullable_market_quote_clock_preserves_actual_price_without_live_or_latency(discovery):
    discovery.save_catalog([stock()], AS_OF, market="KOSPI")
    discovery.save_calendar("KR", calendar(), AS_OF)
    discovery.save_market_quotes([quote(timestamp=None)], AS_OF)
    actual = by_symbol(discovery.discovery_snapshot(AS_OF))[SYMBOLS[0]]["quote"]
    assert actual["price"] == 100
    assert actual["quote_at"] is None
    assert actual["latency_seconds"] is None
    assert actual["status"] == "STALE"


def test_quote_first_seen_revision_does_not_change_earlier_price(discovery):
    discovery.save_catalog([stock()], AS_OF, market="KOSPI")
    discovery.save_market_quotes([quote()], AS_OF)
    later = AS_OF + timedelta(seconds=5)
    discovery.save_market_quotes([quote(lastPrice="110")], later)
    assert by_symbol(discovery.discovery_snapshot(AS_OF))[SYMBOLS[0]]["quote"]["price"] == 100
    assert by_symbol(discovery.discovery_snapshot(later))[SYMBOLS[0]]["quote"]["price"] == 110


def test_conflicting_duplicate_quote_rows_are_both_rejected_without_last_row_winner(discovery):
    discovery.save_catalog([stock()], AS_OF, market="KOSPI")
    conflicting = [quote(), quote(lastPrice="110", timestamp=(AS_OF - timedelta(seconds=1)).isoformat())]
    result = discovery.save_market_quotes(conflicting, AS_OF, expected_symbols=[SYMBOLS[0]])
    assert result["stored"] == 0
    assert result["rejected"] == 2
    actual = by_symbol(discovery.discovery_snapshot(AS_OF))[SYMBOLS[0]]["quote"]
    assert actual["price"] is None
    assert actual["status"] == "UNAVAILABLE"


def test_scan_failure_immediately_blocks_live_price_label_without_replacing_actual(discovery):
    discovery.save_catalog([stock()], AS_OF, market="KOSPI")
    discovery.save_calendar("KR", calendar(), AS_OF)
    discovery.save_market_quotes([quote()], AS_OF)
    later = AS_OF + timedelta(seconds=1)
    discovery.save_scan_health({"status": "UNAVAILABLE", "reason": "Official scan request failed."}, later)
    actual = by_symbol(discovery.discovery_snapshot(later))[SYMBOLS[0]]["quote"]
    assert actual["price"] == 100
    assert actual["status"] == "STALE"


def test_future_global_ranking_time_is_rejected_not_replaced_with_receipt_time(discovery):
    with pytest.raises(ValueError, match="future"):
        discovery.save_global_rankings({"rankedAt": (AS_OF + timedelta(seconds=1)).isoformat(), "rankings": []}, AS_OF)


def test_null_global_ranking_time_does_not_supply_hot_turnover_candidates(discovery):
    discovery.save_global_rankings({"rankedAt": None, "rankings": [{"symbol": "0101N0"}]}, AS_OF)
    assert discovery.current_hot_symbols(as_of=AS_OF) == []
    data = discovery.discovery_snapshot(AS_OF)["global_rankings"]
    assert data["rankedAt"] is None
    assert data["rankings"] == [{"symbol": "0101N0"}]


def test_universe_only_accepts_common_active_stock_catalogue_scope(discovery):
    rows = [stock(), stock("0101N0", securityType="ETF"), stock("999999", isCommonShare=False)]
    assert discovery.save_catalog(rows, AS_OF, market="KOSPI") == 1
    assert set(by_symbol(discovery.discovery_snapshot(AS_OF))) == {SYMBOLS[0]}


def test_current_snapshot_reads_latest_cache_instead_of_scanning_observation_history(discovery, monkeypatch):
    discovery.save_catalog([stock()], AS_OF, market="KOSPI")
    for version in range(20):
        stamp = AS_OF + timedelta(seconds=version)
        discovery.save_market_quotes([quote(timestamp=stamp.isoformat(), lastPrice=str(100 + version))], stamp)
    current = AS_OF + timedelta(seconds=19)
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return current.astimezone(tz) if tz is not None else current.replace(tzinfo=None)
    monkeypatch.setattr(discovery, "datetime", FrozenDateTime)
    original_connection = discovery.connection
    queries = []
    @contextmanager
    def traced_connection():
        with original_connection() as conn:
            conn.set_trace_callback(queries.append)
            yield conn
    monkeypatch.setattr(discovery, "connection", traced_connection)
    found = by_symbol(discovery.discovery_snapshot())[SYMBOLS[0]]["quote"]
    assert found["price"] == 119
    selects = [query.lower() for query in queries if query.lstrip().upper().startswith("SELECT")]
    assert any("from discovery_latest" in query for query in selects)
    assert not any("from discovery_records" in query for query in selects)


@pytest.mark.parametrize("kind", ["catalogue", "membership"])
def test_catalogue_and_membership_writes_use_bounded_batch_connections(discovery, monkeypatch, kind):
    original_connection = discovery.connection
    calls = []
    @contextmanager
    def counted_connection():
        calls.append(1)
        with original_connection() as conn:
            yield conn
    monkeypatch.setattr(discovery, "connection", counted_connection)
    def save(size, stamp):
        symbols = [f"{index:06d}" for index in range(size)]
        if kind == "catalogue":
            discovery.save_catalog([stock(symbol) for symbol in symbols], stamp, market="KOSPI")
        else:
            discovery.save_memberships("test-sector", [membership_stock(symbol) for symbol in symbols], stamp)
    save(2, AS_OF)
    small_batch_connections = len(calls)
    calls.clear()
    save(40, AS_OF + timedelta(seconds=1))
    assert 1 <= small_batch_connections <= 2
    assert len(calls) == small_batch_connections


def test_master_name_overrides_missing_catalogue_name_with_actual_provenance(discovery):
    discovery.save_catalog([stock(name='Old catalog name')], AS_OF, market='KOSPI')
    discovery.save_stock_master([{'symbol': SYMBOLS[0], 'name': 'Actual master name', 'market': 'KOSPI'}], AS_OF)
    selected = by_symbol(discovery.discovery_snapshot(AS_OF))[SYMBOLS[0]]
    assert selected['name'] == 'Actual master name'
    assert selected['provenance']['name'].endswith('/stocks')


def test_global_ranking_bootstrap_does_not_expand_catalogue_coverage_denominator(discovery):
    discovery.save_catalog([stock()], AS_OF, market='KOSPI')
    discovery.save_global_rankings({'rankedAt': AS_OF.isoformat(), 'rankings': [{'symbol': '0101N0'}]}, AS_OF)
    snapshot = discovery.discovery_snapshot(AS_OF)
    assert set(by_symbol(snapshot)) == {SYMBOLS[0], '0101N0'}
    assert by_symbol(snapshot)['0101N0']['catalog_member'] is False
    assert snapshot['universe']['expected'] == 1
    assert snapshot['universe']['missing'] == 1
    assert snapshot['universe']['bootstrap_observed'] == 1
    assert snapshot['universe']['status'] == 'PARTIAL'


def test_day_and_week_sector_rankings_have_independent_availability_times(discovery):
    week_at = AS_OF + timedelta(seconds=10)
    discovery.save_sector_rankings('US', [{'sectorId': 'a', 'changeRate': '.01'}], AS_OF, duration='1d')
    discovery.save_sector_rankings('US', [{'sectorId': 'a', 'changeRate': '.02'}], week_at, duration='1w')
    earlier = discovery.discovery_snapshot(AS_OF)['sector_rankings']['US']
    assert earlier['weekly_items'] == []
    assert earlier['weekly_first_seen_at'] is None
    available = discovery.discovery_snapshot(week_at)['sector_rankings']['US']
    assert available['first_seen_at'] == AS_OF.isoformat()
    assert available['weekly_first_seen_at'] == week_at.isoformat()
    assert available['weekly_provider_at'] is None
    assert available['provider_at'] is None
    assert available['quality'] == 'ANALYSIS_ONLY'


def test_calendar_records_exact_documented_read_only_source(discovery):
    discovery.save_calendar('KR', calendar(), AS_OF)
    row = discovery._read_one('calendar', 'KR', AS_OF)
    assert row['source_url'] == 'https://openapi.tossinvest.com/api/v1/market-calendar/KR'


@pytest.mark.parametrize('rejected_provider', [None, (AS_OF - timedelta(minutes=1)).isoformat()])
def test_rejected_provider_regression_cannot_reappear_in_past_snapshot_after_new_revision(discovery, rejected_provider):
    discovery.save_catalog([stock()], AS_OF, market='KOSPI')
    discovery.save_market_quotes([quote(lastPrice='100')], AS_OF)
    intervening = AS_OF + timedelta(minutes=1)
    rejected = discovery.save_market_quotes([quote(timestamp=rejected_provider, lastPrice='90')], intervening)
    assert rejected['stored'] == 0
    before_future = by_symbol(discovery.discovery_snapshot(intervening))[SYMBOLS[0]]['quote']
    latest = AS_OF + timedelta(minutes=2)
    discovery.save_market_quotes([quote(timestamp=latest.isoformat(), lastPrice='110')], latest)
    after_future = by_symbol(discovery.discovery_snapshot(intervening))[SYMBOLS[0]]['quote']
    assert before_future == after_future
    assert after_future['price'] == 100


def test_sector_taxonomy_removal_and_restoration_preserve_point_in_time_graph(discovery):
    discovery.save_catalog([stock()], AS_OF, market='KOSPI')
    discovery.save_sectors([sector()], AS_OF)
    discovery.save_memberships('test-sector', [membership_stock()], AS_OF)
    removed_at, restored_at = AS_OF + timedelta(minutes=1), AS_OF + timedelta(minutes=2)
    discovery.save_sectors([], removed_at)
    assert discovery.discovery_snapshot(removed_at)['sectors'] == []
    assert discovery.discovery_snapshot(removed_at)['memberships'] == []
    assert by_symbol(discovery.discovery_snapshot(removed_at))[SYMBOLS[0]]['sector_ids'] == []
    discovery.save_sectors([sector()], restored_at)
    assert by_symbol(discovery.discovery_snapshot(restored_at))[SYMBOLS[0]]['sector_ids'] == ['test-sector']
    assert discovery.discovery_snapshot(removed_at)['memberships'] == []
    assert len(discovery.discovery_snapshot(AS_OF)['sectors']) == 1


def test_unchanged_catalogue_success_refreshes_health_without_relabeling_first_availability(discovery):
    discovery.save_catalog([stock()], AS_OF, market='KOSPI')
    next_day = AS_OF + timedelta(days=1)
    stale = discovery.discovery_snapshot(next_day)['universe']
    assert stale['catalog_status']['KOSPI'] == 'STALE'
    discovery.save_catalog([stock()], next_day, market='KOSPI')
    current = discovery.discovery_snapshot(next_day)['universe']
    assert current['catalog_first_seen_at']['KOSPI'] == AS_OF.isoformat()
    assert current['catalog_last_success_at']['KOSPI'] == next_day.isoformat()
    assert current['catalog_status']['KOSPI'] == 'READY'


def test_old_manual_chart_jobs_expire_from_hot_quotes_without_hiding_new_rankings(discovery):
    discovery.save_catalog([stock()], AS_OF, market='KOSPI')
    discovery.enqueue_analysis(SYMBOLS[0], priority=100, as_of=AS_OF)
    later = AS_OF + timedelta(minutes=16)
    discovery.save_global_rankings({'rankedAt': later.isoformat(), 'rankings': [{'symbol': '0101N0'}]}, later)
    assert discovery.current_hot_symbols(as_of=later) == ['0101N0']
    discovery.enqueue_analysis(SYMBOLS[0], priority=100, as_of=later)
    assert discovery.current_hot_symbols(as_of=later)[0] == SYMBOLS[0]


def test_unchanged_sector_rankings_keep_availability_and_separate_retrieval_time(discovery):
    rows = [{'sectorId': 'test-sector', 'changeRate': '.01'}]
    discovery.save_sector_rankings('US', rows, AS_OF)
    next_day = AS_OF + timedelta(days=1)
    discovery.save_sector_rankings('US', rows, next_day)
    before = discovery.discovery_snapshot(AS_OF)['sector_rankings']['US']
    current = discovery.discovery_snapshot(next_day)['sector_rankings']['US']
    assert before['last_success_at'] == AS_OF.isoformat()
    assert current['first_seen_at'] == AS_OF.isoformat()
    assert current['last_success_at'] == next_day.isoformat()
    assert current['provider_at'] is None
    assert current['quality'] == 'ANALYSIS_ONLY'
