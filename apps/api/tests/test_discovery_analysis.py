"""Manufactured transport observations, not real prices or historical evidence.

The provider labels exercise the verified-data contract entirely offline.
"""

from copy import deepcopy
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.discovery.analysis import analyze_discovery_stock
from app.strategy.intraday import EntryGates, MarketSession


KST = ZoneInfo("Asia/Seoul")
AS_OF = datetime(2026, 10, 12, 9, 30, 2, tzinfo=KST)
PREVIOUS_DAY = "2026-10-09"


def at(hour, minute=0, second=0):
    return datetime(2026, 10, 12, hour, minute, second, tzinfo=KST)


def quote(**changes):
    result = {"price": 10_050, "quote_at": AS_OF, "first_seen_at": AS_OF,
              "status": "LIVE", "source": "toss", "venue": "CONSOLIDATED"}
    result.update(changes)
    return result


def daily(count=30):
    # A weekday test calendar deliberately does not claim exchange holidays.
    days, cursor = [], date.fromisoformat(PREVIOUS_DAY)
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    rows = []
    for index, day in enumerate(days):
        close = 10_000 + index % 4 * 20
        opening = rows[-1]["close"] if rows else close
        rows.append({"time": day.isoformat(), "open": opening, "high": max(opening, close) + 20,
                     "low": min(opening, close) - 20, "close": close, "volume": 10_000 + index * 100,
                     "tradedValue": 120_000_000, "source": "toss", "venue": "integrated",
                     "integrated": True, "status": "CLOSED", "first_seen_at": at(8, 55)})
    return rows


def minute(end, price=10_050, **changes):
    row = {"time": end.isoformat(), "end": end, "first_seen_at": end + timedelta(seconds=1),
           "open": price, "high": price + 10, "low": price - 10, "close": price + 5,
           "volume": 1000, "source": "toss", "venue": "integrated", "integrated": True,
           "status": "CLOSED", "timestamp_semantics": "END"}
    row.update(changes)
    return row


def minutes(count=30):
    return [minute(at(9) + timedelta(minutes=index + 1), 10_030 + index % 5 * 4)
            for index in range(count)]


def calendar():
    return [MarketSession("OFFLINE_KRX_REGULAR", at(9), at(15, 30), "CONSOLIDATED")]


def analyze(**changes):
    values = {"symbol": "OFFLINE_ONLY", "name": "제조한 단위 테스트 관측", "as_of": AS_OF,
              "quote": quote(), "daily_candles": daily(), "minute_candles": minutes(),
              "sessions": calendar(), "previous_business_day": PREVIOUS_DAY,
              "daily_first_seen_at": at(8, 55), "minute_first_seen_at": AS_OF}
    values.update(changes)
    return analyze_discovery_stock(**values)


def test_quote_backed_unregistered_stock_preserves_unknown_flow_and_blocks_ready():
    result = analyze(theme_context=None, investor_netbuy=None, entry_gates=None)
    assert result["symbol"] == "OFFLINE_ONLY"
    assert result["source"] == "toss"
    assert result["flow"] is None
    assert result["recommendation_kind"] == "THEME_WATCH"
    assert result["orders_enabled"] is False
    assert result["earlyEntry"]["eligible"] is False
    assert result["earlyEntry"]["state"] != "EARLY_ENTRY_READY"


def test_previous_day_chart_bars_do_not_supply_recovery_to_fresh_quote():
    previous = AS_OF.replace(day=9, hour=9, minute=0, second=0)
    old_minutes = [minute(previous + timedelta(minutes=index + 1)) for index in range(30)]
    result = analyze(minute_candles=old_minutes,
        sessions=[MarketSession('OFFLINE_PREVIOUS', previous, previous.replace(hour=15, minute=30)), *calendar()])
    assert len(result['chart']['five_minute']) == 6
    assert result['chart']['five_minute'][-1]['end'] == previous.replace(minute=30).isoformat()
    assert result['earlyEntry']['confirmation_bar'] is None
    assert result['earlyEntry'].get('completed_5m_bar') is False
    assert result['earlyEntry']['eligible'] is False
    assert result["technical"]["ma5"] is not None
    assert result["technical"]["ma20"] is not None
    assert all(value is None for value in result["earlyEntry"].get("gates", {}).values())


def test_inputs_are_not_modified_to_force_legacy_analysis():
    day_rows, minute_rows, current_quote = daily(), minutes(), quote()
    before = deepcopy((day_rows, minute_rows, current_quote))
    result = analyze(daily_candles=day_rows, minute_candles=minute_rows, quote=current_quote)
    assert (day_rows, minute_rows, current_quote) == before
    assert result["flow"] is None
    assert "flow" not in current_quote
    assert result["orders_enabled"] is False


def test_future_daily_price_poison_is_ignored_before_parsing():
    original = analyze()
    future = {"time": "2026-10-13", "first_seen_at": AS_OF + timedelta(days=1),
              "status": "CLOSED", "open": "FUTURE_NOT_AVAILABLE", "high": "FUTURE_NOT_AVAILABLE",
              "low": "FUTURE_NOT_AVAILABLE", "close": "FUTURE_NOT_AVAILABLE", "volume": "UNKNOWN",
              "source": "toss", "venue": "integrated", "integrated": True}
    changed = analyze(daily_candles=[*daily(), future])
    assert changed["technical"] == original["technical"]
    assert changed["chart"]["daily"] == original["chart"]["daily"]
    assert changed["earlyEntry"] == original["earlyEntry"]
    assert changed["watch_score"] == original["watch_score"]


def test_late_received_daily_revision_cannot_change_current_levels():
    original = analyze()
    revision = {**daily()[-1], "first_seen_at": AS_OF + timedelta(seconds=1),
                "open": "UNSEEN", "high": "UNSEEN", "low": "UNSEEN", "close": "UNSEEN"}
    changed = analyze(daily_candles=[*daily(), revision])
    assert changed["technical"] == original["technical"]
    assert changed["chart"]["daily"] == original["chart"]["daily"]
    assert changed["watch_score"] == original["watch_score"]


def test_current_partial_daily_candle_is_preview_without_ma_or_box_contamination():
    original = analyze()
    partial = {**daily()[-1], "time": "2026-10-12", "first_seen_at": AS_OF,
               "status": "PARTIAL", "open": 100_000, "high": 110_000,
               "low": 90_000, "close": 105_000, "volume": 1_000_000}
    changed = analyze(daily_candles=[*daily(), partial])
    assert changed["technical"] == original["technical"]
    assert changed["earlyEntry"].get("MA5_DAILY") == original["earlyEntry"].get("MA5_DAILY")
    visible = next(row for row in changed["chart"]["daily"] if row["time"][:10] == "2026-10-12")
    assert visible["status"] == "PARTIAL"
    assert visible.get("ma5") is None
    assert changed["earlyEntry"]["eligible"] is False


def test_current_date_claimed_closed_daily_is_not_used_before_official_day_cutoff():
    original = analyze()
    claimed_closed = {**daily()[-1], "time": "2026-10-12", "first_seen_at": AS_OF,
                      "status": "CLOSED", "open": 100_000, "high": 110_000,
                      "low": 90_000, "close": 105_000}
    changed = analyze(daily_candles=[*daily(), claimed_closed])
    assert changed["technical"] == original["technical"]
    assert changed["earlyEntry"]["eligible"] is False


@pytest.mark.parametrize("count", [0, 5, 19])
def test_less_than_twenty_completed_daily_candles_remains_pending(count):
    result = analyze(daily_candles=daily(count))
    assert result["technical"]["ma5"] is None
    assert result["technical"]["ma20"] is None
    assert result["earlyEntry"]["eligible"] is False
    assert result["earlyEntry"]["state"] != "EARLY_ENTRY_READY"
    assert result["orders_enabled"] is False


def test_previous_close_uses_exact_previous_business_day_not_second_last_row():
    rows = daily()
    rows[-1] = {**rows[-1], "open": 10_080, "high": 10_200, "low": 10_050, "close": 10_150}
    rows[-2] = {**rows[-2], "open": 10_010, "high": 10_060, "low": 10_000, "close": 10_030}
    result = analyze(daily_candles=rows)
    assert result["technical"]["previousClose"] == 10_150
    assert result["technical"]["previousClose"] != rows[-2]["close"]


def test_missing_minute_stays_partial_and_cannot_supply_ma5_recovery():
    rows = minutes()
    rows = [row for row in rows if row["end"] != at(9, 28)]
    result = analyze(minute_candles=rows)
    latest = result["chart"]["five_minute"][-1]
    assert latest["start"] == at(9, 25).isoformat()
    assert latest["end"] == at(9, 30).isoformat()
    assert latest["status"] == "PARTIAL"
    assert latest["minute_count"] == 4
    assert latest["ma5_5m"] is None
    assert result["earlyEntry"]["eligible"] is False
    assert result["earlyEntry"]["state"] != "EARLY_ENTRY_READY"


def test_five_minute_end_and_first_seen_boundaries_are_preserved_in_discovery():
    result = analyze(as_of=at(9, 30), minute_first_seen_at=at(9, 30),
                     quote=quote(quote_at=at(9, 30), first_seen_at=at(9, 30)))
    latest = result["chart"]["five_minute"][-1]
    assert latest["status"] == "PARTIAL"
    assert latest["minute_count"] == 4
    assert latest["end"] == at(9, 30).isoformat()
    assert latest["close"] == minutes()[-2]["close"]
    assert result["earlyEntry"]["eligible"] is False


def test_future_minute_price_poison_cannot_change_discovery_decision():
    original = analyze()
    future = {"time": at(9, 31).isoformat(), "end": at(9, 31), "first_seen_at": at(9, 31, 1),
              "open": "FUTURE_UNKNOWN", "high": "FUTURE_UNKNOWN", "low": "FUTURE_UNKNOWN",
              "close": "FUTURE_UNKNOWN", "volume": "FUTURE_UNKNOWN", "source": "toss",
              "venue": "integrated", "integrated": True, "status": "CLOSED"}
    changed = analyze(minute_candles=[*minutes(), future])
    assert changed["chart"]["five_minute"] == original["chart"]["five_minute"]
    assert changed["earlyEntry"] == original["earlyEntry"]
    assert changed["watch_score"] == original["watch_score"]


@pytest.mark.parametrize("field", ["quote_at", "first_seen_at"])
def test_future_quote_is_unavailable_instead_of_clock_relabelled(field):
    result = analyze(quote=quote(**{field: AS_OF + timedelta(seconds=1)}))
    assert result["quote"] is None or result["quote"]["status"] == "UNAVAILABLE"
    assert result["earlyEntry"]["eligible"] is False
    assert result["earlyEntry"]["state"] != "EARLY_ENTRY_READY"


def test_synthetic_minutes_cannot_be_mixed_into_verified_daily_history():
    rows = minutes()
    rows[-1] = {**rows[-1], "source": "synthetic"}
    result = analyze(minute_candles=rows)
    assert result["earlyEntry"]["eligible"] is False
    assert result["earlyEntry"]["state"] != "EARLY_ENTRY_READY"
    assert all(row["source"] == "toss" for row in result["chart"]["five_minute"])


def test_unknown_entry_gates_are_preserved_even_when_explicit_object_is_supplied():
    result = analyze(entry_gates=EntryGates())
    assert result["earlyEntry"]["eligible"] is False
    assert result["earlyEntry"]["state"] != "EARLY_ENTRY_READY"
    assert all(value is None for value in result["earlyEntry"].get("gates", {}).values())


def test_first_resistance_stays_structural_when_current_quote_moves_above_it():
    before = analyze(quote=quote(price=10_050))
    after = analyze(quote=quote(price=10_100))
    assert before["technical"]["ma5"] == after["technical"]["ma5"] == 10_028
    assert before["technical"]["boxHigh"] == after["technical"]["boxHigh"] == 10_080
    assert before["technical"]["firstResistance"] == after["technical"]["firstResistance"] == 10_080
    assert after["quote"]["price"] > after["technical"]["firstResistance"]
    assert after["earlyEntry"]["state"] == "WATCH_ONLY"
    assert after["earlyEntry"]["eligible"] is False
    assert "AFTER_FIRST_RESISTANCE_NO_EARLY_ENTRY" in after["earlyEntry"]["reason_codes"]


def test_conflicting_daily_versions_remain_quarantined_after_another_duplicate():
    rows = daily()
    original = rows[-1]
    conflicting = {**original, "close": original["close"] + 5, "high": original["high"] + 10}
    first = analyze(daily_candles=[*rows[:-1], original, conflicting, original])
    second = analyze(daily_candles=[*rows[:-1], original, original, conflicting])
    for result in (first, second):
        assert "CONFLICTING_DAILY_REVISION" in result["warnings"]
        assert PREVIOUS_DAY not in [row["time"] for row in result["chart"]["daily"]]
    assert first["technical"] == second["technical"]
    assert first["chart"]["daily"] == second["chart"]["daily"]


def test_conflicting_minute_versions_remain_partial_after_another_duplicate():
    rows = minutes()
    original = rows[-1]
    conflicting = {**original, "close": original["close"] + 1}
    first = analyze(minute_candles=[*rows[:-1], original, conflicting, original])
    second = analyze(minute_candles=[*rows[:-1], original, original, conflicting])
    for result in (first, second):
        assert "CONFLICTING_MINUTE_REVISION" in result["warnings"]
        assert result["chart"]["five_minute"][-1]["status"] == "PARTIAL"
        assert result["chart"]["five_minute"][-1]["minute_count"] == 4
    assert first["chart"]["five_minute"] == second["chart"]["five_minute"]


def test_later_observed_daily_revision_can_resolve_an_older_conflict():
    rows = daily()
    original = rows[-1]
    conflicting = {**original, "close": original["close"] + 5, "high": original["high"] + 10}
    later = {**conflicting, "first_seen_at": AS_OF}
    corrected = analyze(daily_candles=[*rows[:-1], original, conflicting, later])
    latest_only = analyze(daily_candles=[*rows[:-1], later])
    assert corrected["technical"] == latest_only["technical"]
    assert corrected["chart"]["daily"] == latest_only["chart"]["daily"]
    assert corrected["technical"]["previousClose"] == later["close"]


def test_later_observed_minute_revision_can_resolve_an_older_conflict():
    rows = minutes()
    original = rows[-1]
    conflicting = {**original, "close": original["close"] + 1}
    later = {**conflicting, "first_seen_at": AS_OF}
    corrected = analyze(minute_candles=[*rows[:-1], original, conflicting, later])
    latest_only = analyze(minute_candles=[*rows[:-1], later])
    assert corrected["chart"]["five_minute"] == latest_only["chart"]["five_minute"]
    assert corrected["chart"]["five_minute"][-1]["status"] == "CLOSED"
    assert corrected["chart"]["five_minute"][-1]["close"] == later["close"]


def test_undated_receipt_does_not_turn_old_daily_dates_into_available_history():
    rows = [{key: value for key, value in row.items() if key != "first_seen_at"} for row in daily()]
    result = analyze(daily_candles=rows, daily_first_seen_at=None)
    assert result["chart"]["daily"] == []
    assert result["technical"]["ma5"] is None
    assert result["technical"]["ma20"] is None
    assert result["earlyEntry"]["eligible"] is False


def test_undated_receipt_does_not_turn_old_minute_dates_into_completed_proof():
    rows = [{key: value for key, value in row.items() if key != "first_seen_at"} for row in minutes()]
    result = analyze(minute_candles=rows, minute_first_seen_at=None)
    assert result["chart"]["minute"] == []
    assert result["chart"]["five_minute"] == []
    assert result["earlyEntry"]["eligible"] is False


def test_quote_without_provider_time_is_analysis_only_and_cannot_confirm_entry():
    result = analyze(quote=quote(quote_at=None))
    assert result["quote"]["status"] == "ANALYSIS_ONLY"
    assert result["quote"]["quote_at"] is None
    assert result["earlyEntry"]["state"] == "UNAVAILABLE"
    assert result["earlyEntry"]["eligible"] is False


def test_integrated_current_day_is_partial_while_verified_aftermarket_is_open():
    original = analyze()
    end = at(20)
    sessions = [*calendar(), MarketSession("OFFLINE_AFTERMARKET", at(15, 30), end, "CONSOLIDATED")]
    now = at(15, 35)
    claimed_closed = {**daily()[-1], "time": now.date().isoformat(), "first_seen_at": now,
                      "status": "CLOSED", "open": 100_000, "high": 110_000,
                      "low": 90_000, "close": 105_000}
    result = analyze(as_of=now, quote=quote(quote_at=now, first_seen_at=now),
                     sessions=sessions, daily_candles=[*daily(), claimed_closed])
    assert result["technical"] == original["technical"]
    today = next(row for row in result["chart"]["daily"] if row["time"] == "2026-10-12")
    assert today["status"] == "PARTIAL"
    assert today["ma5"] is None


@pytest.fixture
def ready_observation():
    """A wholly manufactured pattern that exercises every known entry gate."""
    day_rows = daily(27)
    for index, row in enumerate(day_rows):
        if index < 20:
            opening, close, high, low, volume = 9600, 9600, 9660, 9540, 10_000
        elif index == 20:
            opening, close, high, low, volume = 10_000, 10_300, 10_460, 9990, 30_000
        else:
            close = [10_100, 10_090, 10_100, 10_110, 10_090, 10_110][index - 21]
            opening, high, low, volume = day_rows[index - 1]["close"], 10_300, 10_000, 10_000
        row.update(open=opening, close=close, high=high, low=low, volume=volume,
                   tradedValue=12_000_000_000)
    minute_rows = []
    closes = [10_100, 10_120, 10_110, 10_100, 10_090, 10_140]
    for index, close in enumerate(closes):
        opening = closes[index - 1] if index else closes[0]
        for offset in range(1, 6):
            end = at(9) + timedelta(minutes=index * 5 + offset)
            before = opening + (close - opening) * (offset - 1) / 5
            after = opening + (close - opening) * offset / 5
            minute_rows.append(minute(end, open=before, close=after,
                                      high=max(before, after) + 8, low=min(before, after) - 8,
                                      first_seen_at=end + timedelta(seconds=2)))
    flow = {"symbol": "OFFLINE_ONLY", "source": "toss", "mode": "live", "venue": "CONSOLIDATED",
            "interval_start": at(9, 29), "interval_end": at(9, 30), "observed_at": at(9, 30),
            "first_seen_at": AS_OF, "quote_at": AS_OF, "turnover_krw": 100_000_000}
    gates = {"source": "toss", "mode": "live", "first_seen_at": AS_OF,
             "theme_confirmed": True, "spread_pct": .1, "turnover_krw_per_min": 100_000_000,
             "suspended": False, "vi_active": False, "gap_pct": 1, "near_price_limit": False}
    theme = {"source": "toss", "mode": "live", "first_seen_at": at(9), "as_of": AS_OF,
             "observed_flow_confirmed": True, "score": 100}
    return {"daily_candles": day_rows, "minute_candles": minute_rows, "quote": quote(price=10_140),
            "flow_observations": [flow], "entry_gates": gates, "theme_context": theme}


def test_fully_observed_manufactured_recovery_passes_the_entry_contract(ready_observation):
    result = analyze(**ready_observation)
    assert result["technical"]["ma5"] == 10_100
    assert result["technical"]["firstResistance"] == 10_300
    assert result["technical"]["target"] == 10_460
    assert result["earlyEntry"]["state"] == "EARLY_ENTRY_READY"
    assert result["earlyEntry"]["eligible"] is True
    assert result["earlyEntry"]["risk"]["reward_risk"] == 2
    assert result["orders_enabled"] is False


@pytest.mark.parametrize("field,value", [
    ("source", "synthetic"), ("mode", "demo"), ("mode", "replay"),
    ("first_seen_at", AS_OF + timedelta(seconds=1)),
    ("as_of", AS_OF + timedelta(seconds=1)),
    ("observed_at", AS_OF + timedelta(seconds=1)),
    ("provider_at", AS_OF + timedelta(seconds=1)),
    ("as_of", "not-an-aware-time"),
])
def test_theme_provenance_and_future_context_cannot_authorize_actual_entry(ready_observation, field, value):
    values = deepcopy(ready_observation)
    values["theme_context"][field] = value
    result = analyze(**values)
    assert result["earlyEntry"]["state"] != "EARLY_ENTRY_READY"
    assert result["earlyEntry"]["eligible"] is False
    assert result["watch_score"]["factors"]["theme_context"]["value"] is None


@pytest.mark.parametrize("field,value", [
    ("source", "synthetic"), ("mode", "demo"), ("mode", "replay"),
    ("first_seen_at", AS_OF + timedelta(seconds=1)),
    ("as_of", AS_OF + timedelta(seconds=1)),
    ("observed_at", AS_OF + timedelta(seconds=1)),
    ("provider_at", AS_OF + timedelta(seconds=1)),
    ("first_seen_at", "not-an-aware-time"),
])
def test_future_or_synthetic_risk_gates_are_unknown_and_block_ready(ready_observation, field, value):
    values = deepcopy(ready_observation)
    values["entry_gates"][field] = value
    result = analyze(**values)
    assert result["earlyEntry"]["state"] != "EARLY_ENTRY_READY"
    assert result["earlyEntry"]["eligible"] is False
    assert all(value is None for value in result["earlyEntry"].get("gates", {}).values())


def test_known_risk_values_without_receipt_time_cannot_authorize_entry(ready_observation):
    values = deepcopy(ready_observation)
    del values["entry_gates"]["first_seen_at"]
    result = analyze(**values)
    assert result["earlyEntry"]["eligible"] is False
    assert all(value is None for value in result["earlyEntry"].get("gates", {}).values())


def test_visible_turnover_revision_uses_latest_receipt_regardless_of_input_order(ready_observation):
    current = ready_observation["flow_observations"][0]
    old = {**current, "first_seen_at": at(9, 30), "turnover_krw": 50_000_000}
    first = analyze(**{**ready_observation, "flow_observations": [old, current]})
    second = analyze(**{**ready_observation, "flow_observations": [current, old]})
    assert first["flow"] == second["flow"]
    assert first["flow"]["turnover_krw_per_min"] == 100_000_000
    assert first["watch_score"] == second["watch_score"]
    assert first["earlyEntry"] == second["earlyEntry"]


def test_future_turnover_revision_remains_unknown_at_current_watch_clock(ready_observation):
    current = ready_observation["flow_observations"][0]
    future = {**current, "first_seen_at": AS_OF + timedelta(seconds=1), "turnover_krw": 10**15}
    original = analyze(**ready_observation)
    changed = analyze(**{**ready_observation, "flow_observations": [future, current]})
    assert changed["flow"] == original["flow"]
    assert changed["watch_score"] == original["watch_score"]
    assert changed["earlyEntry"] == original["earlyEntry"]


@pytest.mark.parametrize("field", ["observed_at", "provider_at", "revision_available_at", "as_of"])
def test_future_minute_metadata_cannot_be_erased_into_completed_recovery(ready_observation, field):
    values = deepcopy(ready_observation)
    values["minute_candles"][-1][field] = AS_OF + timedelta(seconds=1)
    result = analyze(**values)
    assert result["chart"]["five_minute"][-1]["status"] == "PARTIAL"
    assert result["chart"]["five_minute"][-1]["minute_count"] == 4
    assert result["earlyEntry"]["eligible"] is False


@pytest.mark.parametrize("field", ["observed_at", "provider_at", "revision_available_at", "as_of"])
def test_future_quote_metadata_cannot_be_erased_into_live_quote(ready_observation, field):
    values = deepcopy(ready_observation)
    values["quote"][field] = AS_OF + timedelta(seconds=1)
    result = analyze(**values)
    assert result["quote"] is None
    assert result["earlyEntry"]["state"] == "UNAVAILABLE"
    assert result["earlyEntry"]["eligible"] is False


def test_daily_current_observation_does_not_override_a_future_provider_timestamp(ready_observation):
    values = deepcopy(ready_observation)
    values["daily_candles"][-1].update(observed_at=AS_OF, provider_at=AS_OF + timedelta(seconds=1))
    result = analyze(**values)
    assert PREVIOUS_DAY not in [row["time"] for row in result["chart"]["daily"]]
    assert result["technical"]["previousClose"] is None
    assert result["technical"]["ma5"] == 10_098


def test_future_flow_provider_time_does_not_supply_actual_liquidity(ready_observation):
    values = deepcopy(ready_observation)
    values["flow_observations"][0]["provider_at"] = AS_OF + timedelta(seconds=1)
    result = analyze(**values)
    assert result["flow"] is None
    assert result["watch_score"]["factors"]["observed_turnover"]["value"] is None
    assert result["earlyEntry"]["eligible"] is False


def test_future_netbuy_provider_time_is_unavailable_even_with_current_receipt(ready_observation):
    netbuy = {"symbol": "OFFLINE_ONLY", "source": "toss", "mode": "live", "venue": "CONSOLIDATED",
              "unit": "SHARES", "values": {"foreign": 100}, "first_seen_at": AS_OF,
              "observed_at": AS_OF, "provider_at": AS_OF + timedelta(seconds=1)}
    result = analyze(**ready_observation, investor_netbuy=netbuy)
    assert result["investor_netbuy"] is None
    assert result["quality"]["investor_netbuy"] == "UNAVAILABLE"
