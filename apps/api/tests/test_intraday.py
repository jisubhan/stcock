"""Intraday regressions use invented observations, never historical market data."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.fixtures import AS_OF, stock_samples
from app.settings import StrategyConfig, read_themes
from app.strategy.engine import analyze_stock, calculate_themes
from app.strategy.intraday import (
    EarlyEntryConfig,
    MarketSession,
    MinuteCandle,
    build_five_minute_bars,
    evaluate_early_entry,
    evaluate_legacy_stock_entry,
    fixture_scenario,
)


KST = ZoneInfo("Asia/Seoul")


def at(hour, minute=0, second=0):
    return datetime(2026, 10, 8, hour, minute, second, tzinfo=KST)


def session(start=None, end=None, session_id="regular", venue="CONSOLIDATED"):
    return MarketSession(
        session_id=session_id,
        start=start or at(9),
        end=end or at(15, 30),
        venue=venue,
    )


def minute(end, price=100, volume=10, **kwargs):
    values = dict(
        end=end,
        first_seen_at=end,
        open=price,
        high=price + 2,
        low=price - 2,
        close=price + 1,
        volume=volume,
        venue="CONSOLIDATED",
        source="synthetic",
    )
    values.update(kwargs)
    return MinuteCandle(**values)


def minutes(start, count, **kwargs):
    return [minute(start + timedelta(minutes=i + 1), price=100 + i, **kwargs)
            for i in range(count)]


def test_t07_future_minutes_cannot_leak_a_five_minute_high_or_close():
    candles = minutes(at(9, 10), 5)
    candles[-1] = replace(candles[-1], high=900, close=800)

    before_close = build_five_minute_bars(candles, [session()], as_of=at(9, 14))
    assert len(before_close) == 1
    assert before_close[0].start == at(9, 10)
    assert before_close[0].end == at(9, 15)
    assert before_close[0].status == "PARTIAL"
    assert before_close[0].minute_count == 4
    assert before_close[0].high == 105
    assert before_close[0].close == 104
    assert before_close[0].ma5_5m is None

    completed = build_five_minute_bars(candles, [session()], as_of=at(9, 15))
    assert completed[0].status == "CLOSED"
    assert completed[0].minute_count == 5
    assert completed[0].high == 900
    assert completed[0].close == 800


def test_t07_late_received_minute_stays_unavailable_until_first_seen():
    candles = minutes(at(9, 10), 5)
    candles[-1] = replace(candles[-1], first_seen_at=at(9, 17), high=900, close=800)

    available = build_five_minute_bars(candles, [session()], as_of=at(9, 16))
    assert available[0].status == "PARTIAL"
    assert available[0].minute_count == 4
    assert available[0].high < 900
    assert available[0].close < 800

    arrived = build_five_minute_bars(candles, [session()], as_of=at(9, 17))
    assert arrived[0].status == "CLOSED"
    assert arrived[0].high == 900


def test_minute_end_timestamps_are_assigned_to_their_preceding_slot():
    candles = minutes(at(9), 6)
    bars = build_five_minute_bars(candles, [session()], as_of=at(9, 6))
    assert [(bar.start, bar.end, bar.minute_count, bar.status) for bar in bars] == [
        (at(9), at(9, 5), 5, "CLOSED"),
        (at(9, 5), at(9, 10), 1, "PARTIAL"),
    ]
    assert bars[0].open == 100
    assert bars[0].high == 106
    assert bars[0].low == 98
    assert bars[0].close == 105
    assert bars[0].volume == 50


def test_timezone_representations_produce_the_same_kst_aligned_bars():
    candles = minutes(at(9), 5)
    utc_candles = [replace(candle,
                           end=candle.end.astimezone(timezone.utc),
                           first_seen_at=candle.first_seen_at.astimezone(timezone.utc))
                   for candle in candles]
    kst = build_five_minute_bars(candles, [session()], as_of=at(9, 5))
    utc = build_five_minute_bars(utc_candles, [session()],
                               as_of=at(9, 5).astimezone(timezone.utc))
    assert [bar.to_dict() for bar in utc] == [bar.to_dict() for bar in kst]


def test_missing_minute_cannot_be_closed_by_the_wall_clock_or_adjacent_data():
    candles = minutes(at(9), 10)
    del candles[2]
    bars = build_five_minute_bars(candles, [session()], as_of=at(10))
    assert bars[0].status == "PARTIAL"
    assert bars[0].minute_count == 4
    assert bars[1].status == "CLOSED"
    assert all(bar.ma5_5m is None for bar in bars)


def test_same_feed_duplicate_minutes_are_counted_once():
    candles = minutes(at(9), 5)
    duplicated = [*candles, *candles]
    clean = build_five_minute_bars(candles, [session()], as_of=at(9, 5))
    bars = build_five_minute_bars(duplicated, [session()], as_of=at(9, 5))
    assert bars[0].minute_count == 5
    assert bars[0].volume == 50
    assert bars[0].to_dict() == clean[0].to_dict()


def test_consolidated_bars_do_not_double_count_krx_and_nxt_feeds():
    candles = minutes(at(9), 5)
    venue_candles = [replace(candle, venue=venue, volume=500, high=900)
                     for venue in ("KRX", "NXT") for candle in candles]
    bars = build_five_minute_bars([*candles, *venue_candles], [session()],
                                as_of=at(9, 5), venue="CONSOLIDATED")
    assert len(bars) == 1
    assert bars[0].status == "CLOSED"
    assert bars[0].volume == 50
    assert bars[0].high == 106


def test_minutes_outside_a_session_cannot_complete_a_cross_session_bar():
    sessions = [session(at(9, 2), at(9, 7), "short")]
    bars = build_five_minute_bars(minutes(at(9), 10), sessions, as_of=at(9, 10))
    assert len(bars) == 2
    assert [bar.minute_count for bar in bars] == [3, 2]
    assert all(bar.status == "PARTIAL" for bar in bars)
    assert all(bar.ma5_5m is None for bar in bars)
    assert sum(bar.volume for bar in bars) == 50


def test_ma5_requires_five_completed_contiguous_bars():
    bars = build_five_minute_bars(minutes(at(9), 26), [session()], as_of=at(9, 26))
    assert all(bar.ma5_5m is None for bar in bars[:4])
    assert bars[4].ma5_5m == pytest.approx((105 + 110 + 115 + 120 + 125) / 5)
    assert bars[5].status == "PARTIAL"
    assert bars[5].ma5_5m is None


def test_ma5_resets_across_missing_slots_and_market_session_boundaries():
    first = minutes(at(9), 20)
    after_gap = minutes(at(9, 25), 5)
    bars = build_five_minute_bars([*first, *after_gap], [session()], as_of=at(9, 30))
    assert all(bar.ma5_5m is None for bar in bars)

    sessions = [session(at(9), at(9, 20), "pre"),
                session(at(9, 20), at(9, 30), "regular")]
    boundary_bars = build_five_minute_bars(minutes(at(9), 30), sessions,
                                         as_of=at(9, 30))
    assert all(bar.ma5_5m is None for bar in boundary_bars)


def test_future_unparseable_prices_are_not_read_before_end_and_first_seen():
    candles = minutes(at(9), 5)
    future_end = {
        "end": at(9, 6), "first_seen_at": at(9, 6),
        "open": "NOT_AVAILABLE", "close": "NOT_AVAILABLE",
    }
    future_seen = {
        "end": at(9, 5), "first_seen_at": at(10),
        "open": "NOT_AVAILABLE", "close": "NOT_AVAILABLE",
    }
    bars = build_five_minute_bars([*candles, future_end, future_seen], [session()],
                                as_of=at(9, 5))
    assert len(bars) == 1
    assert bars[0].close == 105
    assert bars[0].volume == 50


def test_a_later_duplicate_revision_is_not_retroactively_available():
    candles = minutes(at(9), 5)
    revised = replace(candles[-1], first_seen_at=at(9, 7), high=900, close=800)
    rows = [revised, *reversed(candles)]
    original = build_five_minute_bars(rows, [session()], as_of=at(9, 5))
    assert original[0].close == 105
    assert original[0].volume == 50
    amended = build_five_minute_bars(rows, [session()], as_of=at(9, 7))
    assert amended[0].close == 800
    assert amended[0].minute_count == 5
    assert amended[0].volume == 50


def test_conflicting_same_time_revisions_and_mixed_sources_fail_explicitly():
    candles = minutes(at(9), 5)
    conflicting = replace(candles[-1], high=900)
    with pytest.raises(ValueError, match="Conflicting minute"):
        build_five_minute_bars([*candles, conflicting], [session()], as_of=at(9, 5))

    mixed = [replace(candles[0], source="external_fixture_feed"), *candles[1:]]
    with pytest.raises(ValueError, match="mix"):
        build_five_minute_bars(mixed, [session()], as_of=at(9, 5))


def test_a_partial_or_preobserved_minute_cannot_complete_the_five_minute_bar():
    candles = minutes(at(9), 5)
    partial = replace(candles[-1], status="PARTIAL", high=900)
    bars = build_five_minute_bars([*candles[:-1], partial], [session()], as_of=at(9, 6))
    assert bars[0].status == "PARTIAL"
    assert bars[0].minute_count == 4
    assert bars[0].high < 900
    assert bars[0].volume == 40

    preobserved = replace(candles[-1], first_seen_at=at(9, 4, 59), high=900)
    bars = build_five_minute_bars([*candles[:-1], preobserved], [session()], as_of=at(9, 6))
    assert bars[0].status == "PARTIAL"
    assert bars[0].minute_count == 4
    assert bars[0].high < 900


def test_overlapping_sessions_cannot_duplicate_or_reassign_market_minutes():
    sessions = [session(at(9), at(9, 10), "first"),
                session(at(9, 5), at(9, 15), "second")]
    with pytest.raises(ValueError, match="overlap"):
        build_five_minute_bars(minutes(at(9), 10), sessions, as_of=at(9, 10))


def test_start_timestamp_semantics_must_be_explicitly_converted():
    row = {
        "end": at(9, 1), "first_seen_at": at(9, 1),
        "open": 100, "high": 102, "low": 98, "close": 101, "volume": 10,
        "venue": "CONSOLIDATED", "source": "synthetic", "timestamp_semantics": "START",
    }
    with pytest.raises(ValueError, match="Convert START"):
        build_five_minute_bars([row], [session()], as_of=at(9, 5))


@pytest.mark.parametrize("field,value", [
    ("min_reward_risk", 1.99),
    ("max_stop_distance_pct", 2.01),
    ("no_chasing_if_to_first_resistance_rr_below", .99),
    ("max_live_quote_age_seconds", 16),
])
def test_required_risk_and_freshness_thresholds_cannot_be_loosened(field, value):
    with pytest.raises(ValueError, match="cannot be loosened"):
        EarlyEntryConfig(**{field: value})


@pytest.mark.parametrize("name", ["sk_innovation", "isupetasys"])
def test_t13_invented_oct8_recovery_is_watch_before_completion_then_ready(name):
    before = fixture_scenario(name, as_of=at(9, 25, 2))
    watch = evaluate_early_entry(before)
    assert watch["state"] == "EARLY_ENTRY_WATCH"
    assert watch["eligible"] is False
    assert watch["recovery_confirmed"] is False
    assert watch["confirmation_bar"]["end"] == at(9, 25).isoformat()
    assert all(bar.end <= before.as_of and bar.first_seen_at <= before.as_of
               for bar in before.bars)

    after = fixture_scenario(name, as_of=at(9, 30, 2))
    ready = evaluate_early_entry(after)
    assert ready["state"] == "EARLY_ENTRY_READY"
    assert ready["eligible"] is True
    assert ready["recovery_confirmed"] is True
    assert ready["confirmation_bar"]["end"] == at(9, 30).isoformat()
    assert ready["price"] < ready["first_resistance"]
    assert ready["MA5_DAILY"] != ready["MA5_5M"]
    assert ready["support_dwell_seconds"] >= 180
    assert ready["risk"]["reward_risk"] >= 2
    assert ready["risk"]["first_resistance_reward_risk"] >= 1
    assert ready["risk"]["stop_distance_pct"] <= 2
    assert ready["scenario_label"].startswith("Invented")
    assert "not historical market data" in ready["scenario_label"]
    assert ready["quality"] == "DEMO"
    assert ready["source"] == "synthetic"
    assert ready["mode"] == "demo"
    assert ready["orders_enabled"] is False


@pytest.mark.parametrize("name", ["sk_innovation", "isupetasys"])
def test_t13_full_future_history_cannot_confirm_0925_or_unseen_0930_recovery(name):
    future = fixture_scenario(name, as_of=at(9, 30, 2))
    before = fixture_scenario(name, as_of=at(9, 25, 2))
    with_future = replace(before, bars=future.bars)
    decision = evaluate_early_entry(with_future)
    assert decision["state"] == "EARLY_ENTRY_WATCH"
    assert decision["eligible"] is False
    assert decision["confirmation_bar"]["end"] == at(9, 25).isoformat()
    assert decision["recovery_confirmed"] is False

    unseen = fixture_scenario(name, as_of=at(9, 30))
    with_unseen = replace(unseen, bars=future.bars)
    decision = evaluate_early_entry(with_unseen)
    assert decision["state"] == "EARLY_ENTRY_WATCH"
    assert decision["eligible"] is False
    assert decision["confirmation_bar"]["end"] == at(9, 25).isoformat()
    assert decision["recovery_confirmed"] is False


@pytest.mark.parametrize("gate", [
    "theme_confirmed", "spread_pct", "turnover_krw_per_min", "suspended",
    "vi_active", "gap_pct", "near_price_limit",
])
def test_unknown_required_risk_gate_blocks_ready_instead_of_assuming_safety(gate):
    observation = fixture_scenario()
    unknown = replace(observation, gates=replace(observation.gates, **{gate: None}))
    decision = evaluate_early_entry(unknown)
    assert decision["state"] == "EARLY_ENTRY_WATCH"
    assert decision["eligible"] is False
    assert decision["quality"] == "UNAVAILABLE"
    assert "UNKNOWN_" + gate.upper() in decision["reason_codes"]


@pytest.mark.parametrize("gate,value,reason", [
    ("theme_confirmed", False, "THEME_NOT_CONFIRMED"),
    ("suspended", True, "TRADING_SUSPENDED"),
    ("vi_active", True, "VI_ACTIVE"),
    ("near_price_limit", True, "NEAR_PRICE_LIMIT"),
    ("spread_pct", 0.6, "SPREAD_TOO_WIDE"),
    ("turnover_krw_per_min", 1_000_000, "INSUFFICIENT_LIQUIDITY"),
    ("gap_pct", 10.1, "EXTREME_GAP"),
    ("gap_pct", -10.1, "EXTREME_GAP"),
])
def test_known_unsafe_market_conditions_block_ready(gate, value, reason):
    observation = fixture_scenario()
    unsafe = replace(observation, gates=replace(observation.gates, **{gate: value}))
    decision = evaluate_early_entry(unsafe)
    assert decision["state"] == "EARLY_ENTRY_WATCH"
    assert decision["eligible"] is False
    assert reason in decision["reason_codes"]


@pytest.mark.parametrize("gate", ["spread_pct", "turnover_krw_per_min", "gap_pct"])
@pytest.mark.parametrize("value", ["unknown", [], float("nan"), float("inf"), True])
def test_malformed_numeric_gate_is_unavailable_and_json_safe(gate, value):
    observation = fixture_scenario()
    malformed = replace(observation, gates=replace(observation.gates, **{gate: value}))
    decision = evaluate_early_entry(malformed)
    assert decision["state"] == "EARLY_ENTRY_WATCH"
    assert decision["eligible"] is False
    assert decision["quality"] == "UNAVAILABLE"
    assert "INVALID_" + gate.upper() in decision["reason_codes"]
    assert decision["gates"][gate] is None


@pytest.mark.parametrize("gate", ["theme_confirmed", "suspended", "vi_active", "near_price_limit"])
@pytest.mark.parametrize("value", ["false", 0, 1])
def test_boolean_risk_gate_cannot_accept_truthy_or_falsy_substitutes(gate, value):
    observation = fixture_scenario()
    malformed = replace(observation, gates=replace(observation.gates, **{gate: value}))
    decision = evaluate_early_entry(malformed)
    assert decision["state"] == "EARLY_ENTRY_WATCH"
    assert decision["eligible"] is False
    assert decision["quality"] == "UNAVAILABLE"
    assert "INVALID_" + gate.upper() in decision["reason_codes"]
    assert decision["gates"][gate] is None


@pytest.mark.parametrize("field,value,reason", [
    ("structural_stop", None, "STRUCTURAL_STOP_OR_TARGET_UNAVAILABLE"),
    ("structural_stop", 9_800, "STOP_DISTANCE_EXCEEDS_LIMIT"),
    ("structural_stop", 10_140, "STRUCTURAL_STOP_OR_TARGET_UNAVAILABLE"),
    ("target", None, "STRUCTURAL_STOP_OR_TARGET_UNAVAILABLE"),
    ("target", 10_200, "REWARD_RISK_BELOW_MINIMUM"),
    ("target", 10_000, "STRUCTURAL_STOP_OR_TARGET_UNAVAILABLE"),
    ("first_resistance", None, "FIRST_RESISTANCE_UNAVAILABLE"),
    ("first_resistance", 10_180, "FIRST_RESISTANCE_TOO_CLOSE_NO_CHASING"),
])
def test_structural_risk_and_near_resistance_no_chasing_are_required(field, value, reason):
    observation = fixture_scenario()
    changed = replace(observation, levels=replace(observation.levels, **{field: value}))
    decision = evaluate_early_entry(changed)
    assert decision["eligible"] is False
    assert decision["state"] != "EARLY_ENTRY_READY"
    assert reason in decision["reason_codes"]


@pytest.mark.parametrize("age,quality", [(16, "DELAYED"), (301, "STALE")])
def test_old_quote_blocks_ready_even_when_completed_recovery_is_present(age, quality):
    observation = fixture_scenario()
    old = replace(observation, quote=replace(
        observation.quote, timestamp=observation.as_of - timedelta(seconds=age)))
    decision = evaluate_early_entry(old)
    assert decision["state"] == "WATCH_ONLY"
    assert decision["quality"] == quality
    assert decision["eligible"] is False
    assert "QUOTE_TOO_OLD_OR_UNVERIFIED" in decision["reason_codes"]


@pytest.mark.parametrize("status", ["DELAYED", "STALE", "PARTIAL", "UNAVAILABLE"])
def test_nonlive_coverage_is_never_an_entry_permission(status):
    observation = fixture_scenario()
    changed = replace(observation, quote=replace(observation.quote, coverage_status=status))
    decision = evaluate_early_entry(changed)
    assert decision["eligible"] is False
    assert decision["state"] == "WATCH_ONLY"
    assert decision["quality"] in ("DELAYED", "STALE")


@pytest.mark.parametrize("field", ["timestamp", "first_seen_at"])
def test_quote_observed_after_replay_clock_is_unavailable(field):
    observation = fixture_scenario()
    changed = replace(observation, quote=replace(
        observation.quote, **{field: observation.as_of + timedelta(seconds=1)}))
    decision = evaluate_early_entry(changed)
    assert decision["state"] == "UNAVAILABLE"
    assert decision["quality"] == "UNAVAILABLE"
    assert decision["eligible"] is False
    assert "QUOTE_UNAVAILABLE_AT_AS_OF" in decision["reason_codes"]


def test_missing_quote_or_not_yet_observed_daily_levels_blocks_entry():
    observation = fixture_scenario()
    decision = evaluate_early_entry(replace(observation, quote=None))
    assert decision["state"] == "UNAVAILABLE"
    assert decision["eligible"] is False
    assert "QUOTE_UNAVAILABLE_AT_AS_OF" in decision["reason_codes"]

    late_levels = replace(observation.levels, observed_at=observation.as_of + timedelta(seconds=1))
    decision = evaluate_early_entry(replace(observation, levels=late_levels))
    assert decision["state"] == "UNAVAILABLE"
    assert decision["eligible"] is False
    assert "DAILY_LEVELS_UNAVAILABLE_AT_AS_OF" in decision["reason_codes"]


def test_synthetic_history_cannot_be_relabeled_as_live_data():
    observation = fixture_scenario()
    decision = evaluate_early_entry(replace(observation, quote=replace(
        observation.quote, coverage_status="LIVE")))
    assert decision["quality"] == "UNAVAILABLE"
    assert decision["eligible"] is False
    assert "SOURCE_QUALITY_MISMATCH" in decision["reason_codes"]

    quote = replace(observation.quote, source="unverified_fixture_feed", coverage_status="LIVE")
    bars = [replace(bar, source=quote.source) for bar in observation.bars]
    decision = evaluate_early_entry(replace(observation, quote=quote, bars=bars))
    assert decision["quality"] == "UNAVAILABLE"
    assert decision["eligible"] is False
    assert "QUOTE_SOURCE_NOT_VERIFIED" in decision["reason_codes"]


def test_a_fresh_quote_cannot_rescue_old_completed_bar_history():
    observation = fixture_scenario()
    changed = replace(observation, as_of=at(9, 35, 1),
                      quote=replace(observation.quote, timestamp=at(9, 35, 1),
                                    first_seen_at=at(9, 35, 1)))
    decision = evaluate_early_entry(changed)
    assert decision["state"] == "WATCH_ONLY"
    assert decision["quality"] == "STALE"
    assert decision["eligible"] is False
    assert "COMPLETED_5M_BAR_TOO_OLD" in decision["reason_codes"]


@pytest.mark.parametrize("field,value", [("venue", "NXT"), ("source", "other_fixture")])
def test_quote_and_confirmation_history_must_share_source_and_venue(field, value):
    observation = fixture_scenario()
    changed_bars = [replace(bar, **{field: value}) for bar in observation.bars]
    decision = evaluate_early_entry(replace(observation, bars=changed_bars))
    assert decision["state"] == "UNAVAILABLE"
    assert decision["eligible"] is False
    assert "PRICE_HISTORY_SOURCE_OR_VENUE_MISMATCH" in decision["reason_codes"]


@pytest.mark.parametrize("price", [10_300, 10_310])
def test_current_quote_at_or_above_first_resistance_is_never_early_entry(price):
    observation = fixture_scenario()
    changed = replace(observation, quote=replace(observation.quote, price=price))
    decision = evaluate_early_entry(changed)
    assert decision["state"] == "WATCH_ONLY"
    assert decision["eligible"] is False
    assert "AFTER_FIRST_RESISTANCE_NO_EARLY_ENTRY" in decision["reason_codes"]


def test_prior_completed_bar_resistance_touch_cannot_be_called_prebreakout_after_retracement():
    observation = fixture_scenario()
    touched = replace(observation.bars[0], high=observation.levels.first_resistance)
    changed = replace(observation, bars=[touched, *observation.bars[1:]])
    assert changed.quote.price < changed.levels.first_resistance
    decision = evaluate_early_entry(changed)
    assert decision["state"] == "WATCH_ONLY"
    assert decision["eligible"] is False
    assert "RESISTANCE_ALREADY_TRADED_RETEST_ONLY" in decision["reason_codes"]
    assert decision["resistance_first_observed_at"] == touched.first_seen_at.isoformat()


def test_known_partial_resistance_touch_vetoes_but_future_observation_does_not():
    observation = fixture_scenario()
    clock = at(9, 31)
    partial = replace(
        observation.bars[-1], start=at(9, 30), end=at(9, 35),
        status="PARTIAL", minute_count=1, first_seen_at=clock,
        high=observation.levels.first_resistance, ma5_5m=None,
        missing_minute_ends=tuple(at(9, minute) for minute in range(32, 36)),
    )
    changed = replace(
        observation, as_of=clock,
        quote=replace(observation.quote, timestamp=clock, first_seen_at=clock),
        bars=[*observation.bars, partial],
    )
    assert partial.end > clock
    decision = evaluate_early_entry(changed)
    assert decision["state"] == "WATCH_ONLY"
    assert decision["eligible"] is False
    assert "RESISTANCE_ALREADY_TRADED_RETEST_ONLY" in decision["reason_codes"]

    future_partial = replace(partial, first_seen_at=clock + timedelta(seconds=1))
    decision = evaluate_early_entry(replace(changed, bars=[*observation.bars, future_partial]))
    assert decision["state"] == "EARLY_ENTRY_READY"
    assert decision["eligible"] is True
    assert "RESISTANCE_ALREADY_TRADED_RETEST_ONLY" not in decision["reason_codes"]


def test_future_completed_resistance_touch_cannot_veto_a_point_in_time_entry():
    observation = fixture_scenario()
    future = replace(observation.bars[-1], start=at(9, 30), end=at(9, 35),
                     first_seen_at=at(9, 35, 2), high=observation.levels.first_resistance)
    decision = evaluate_early_entry(replace(observation, bars=[*observation.bars, future]))
    assert decision["state"] == "EARLY_ENTRY_READY"
    assert decision["eligible"] is True
    assert decision["confirmation_bar"]["end"] == at(9, 30).isoformat()


def test_breakout_requires_completed_close_above_buffer_and_does_not_authorize_entry():
    observation = fixture_scenario()
    resistance = observation.levels.first_resistance
    changed_quote = replace(observation.quote, price=resistance * 1.002)
    latest = observation.bars[-1]
    under_buffer = replace(latest, close=resistance * 1.001, high=resistance * 1.003)
    changed = replace(observation, quote=changed_quote,
                      bars=[*observation.bars[:-1], under_buffer])
    assert evaluate_early_entry(changed)["state"] == "WATCH_ONLY"

    over_buffer = replace(latest, close=resistance * 1.002, high=resistance * 1.003)
    decision = evaluate_early_entry(replace(changed, bars=[*observation.bars[:-1], over_buffer]))
    assert decision["state"] == "BREAKOUT_CONFIRMED"
    assert decision["eligible"] is False
    assert decision["orders_enabled"] is False

    partial = replace(over_buffer, status="PARTIAL")
    decision = evaluate_early_entry(replace(changed, bars=[*observation.bars[:-1], partial]))
    assert decision["state"] == "WATCH_ONLY"
    assert decision["eligible"] is False


def test_support_dwell_cannot_be_invented_from_within_the_five_minute_bar():
    observation = fixture_scenario()
    # Only 09:20-09:25 first touches this level. Its intrabar touch time is
    # unknown, so exactly 300 seconds have been proven at the 09:30 close.
    support = replace(observation.levels.supports[0], label="last closed support touch", price=10_082)
    observation = replace(observation, levels=replace(
        observation.levels, ma5_daily=None, supports=(support,), important_open_close=()))
    default = evaluate_early_entry(observation, EarlyEntryConfig(
        support_tolerance_pct=.001, min_support_dwell_seconds=300))
    assert default["state"] == "EARLY_ENTRY_READY"
    assert default["support_dwell_seconds"] == 300
    stricter = EarlyEntryConfig(support_tolerance_pct=.001, min_support_dwell_seconds=301)
    decision = evaluate_early_entry(observation, stricter)
    assert decision["state"] == "EARLY_ENTRY_WATCH"
    assert decision["eligible"] is False
    assert "OBSERVED_SUPPORT_DWELL_NOT_CONFIRMED" in decision["reason_codes"]


def test_recovery_is_not_confirmed_across_a_partial_or_session_gap():
    observation = fixture_scenario()
    partial = replace(observation.bars[-2], status="PARTIAL", minute_count=4)
    changed = replace(observation, bars=[*observation.bars[:-2], partial, observation.bars[-1]])
    decision = evaluate_early_entry(changed)
    assert decision["eligible"] is False
    assert decision["recovery_confirmed"] is False
    assert "INSUFFICIENT_CONTIGUOUS_5M_HISTORY" in decision["reason_codes"]

    new_session = replace(observation.bars[-1], session_id="new_session")
    decision = evaluate_early_entry(replace(observation, bars=[*observation.bars[:-1], new_session]))
    assert decision["eligible"] is False
    assert decision["recovery_confirmed"] is False
    assert "INSUFFICIENT_CONTIGUOUS_5M_HISTORY" in decision["reason_codes"]


@pytest.fixture
def legacy_observation():
    stocks = stock_samples()
    config = StrategyConfig()
    themes = calculate_themes(stocks, read_themes(), config)
    raw = stocks[0]
    detail = analyze_stock(raw, themes, config)
    calendar = [MarketSession(
        "DEMO_REGULAR", datetime.fromisoformat("2026-10-07T09:00:00+09:00"),
        datetime.fromisoformat("2026-10-07T15:30:00+09:00"),
    )]
    return raw, detail, calendar


def test_legacy_adapter_preserves_daily_detail_and_rejects_unknown_entry_conditions(legacy_observation):
    raw, detail, calendar = legacy_observation
    raw_before, detail_before = deepcopy(raw), deepcopy(detail)
    addition = evaluate_legacy_stock_entry(raw, detail, AS_OF, calendar, "demo")
    assert raw == raw_before
    assert detail == detail_before
    combined = {**detail, **addition}
    for key in ("state", "score", "technical", "entry", "risk", "targets", "eligible"):
        assert combined[key] == detail_before[key]
    assert detail["state"] == "ENTRY_READY"

    bars = addition["intraday5m"]
    assert len(bars) == 17
    assert all(bar["status"] == "CLOSED" and bar["minute_count"] == 5 for bar in bars[:-1])
    assert bars[-1]["start"] == "2026-10-07T10:20:00+09:00"
    assert bars[-1]["end"] == "2026-10-07T10:25:00+09:00"
    assert bars[-1]["status"] == "PARTIAL"
    assert bars[-1]["minute_count"] == 4
    assert bars[-1]["close"] == raw["intraday"][83]["close"]
    assert bars[-1]["volume"] == sum(row["volume"] for row in raw["intraday"][80:84])
    assert bars[-1]["missing_minute_ends"] == ["2026-10-07T10:25:00+09:00"]
    assert bars[-1]["ma5_5m"] is None
    decision = addition["earlyEntry"]
    assert decision["state"] == "EARLY_ENTRY_WATCH"
    assert decision["quality"] == "UNAVAILABLE"
    assert decision["eligible"] is False
    assert decision["mode"] == "demo"
    assert decision["source"] == "synthetic"
    assert decision["orders_enabled"] is False
    for gate in ("theme_confirmed", "spread_pct", "turnover_krw_per_min", "suspended",
                 "vi_active", "gap_pct", "near_price_limit"):
        assert "UNKNOWN_" + gate.upper() in decision["reason_codes"]


@pytest.mark.parametrize("field,value", [
    ("venue", None), ("venue", "SIMULATED"), ("venue", "unknown"),
    ("timestamp", None), ("timestamp", "malformed-time"), ("price", "NaN"),
])
def test_legacy_live_adapter_missing_or_malformed_venue_and_quote_are_safe(legacy_observation, field, value):
    raw, detail, calendar = legacy_observation
    # These local rows are deliberately malformed transport fixtures. No
    # broker access is involved, and none may become a live recommendation.
    raw = {**raw, "source": "toss", "venue": "CONSOLIDATED", "timestamp": AS_OF,
           "intraday": [], field: value}
    result = evaluate_legacy_stock_entry(raw, detail, AS_OF, calendar, "live")
    assert result["earlyEntry"]["state"] == "UNAVAILABLE"
    assert result["earlyEntry"]["quality"] == "UNAVAILABLE"
    assert result["earlyEntry"]["eligible"] is False
    assert result["earlyEntry"]["orders_enabled"] is False


@pytest.mark.parametrize("missing", ["venue", "timestamp", "price"])
def test_legacy_live_adapter_omitted_required_quote_fields_are_safe(legacy_observation, missing):
    raw, detail, calendar = legacy_observation
    raw = {**raw, "source": "toss", "venue": "CONSOLIDATED", "timestamp": AS_OF, "intraday": []}
    del raw[missing]
    result = evaluate_legacy_stock_entry(raw, detail, AS_OF, calendar, "live")
    assert result["earlyEntry"]["state"] == "UNAVAILABLE"
    assert result["earlyEntry"]["eligible"] is False


@pytest.mark.parametrize("broker_location", ["stock", "minute"])
def test_legacy_adapter_cannot_relabel_broker_observations_as_demo(legacy_observation, broker_location):
    raw, detail, calendar = legacy_observation
    raw = deepcopy(raw)
    if broker_location == "stock":
        raw["source"] = "toss"
    else:
        raw["intraday"][-1]["source"] = "toss"
    result = evaluate_legacy_stock_entry(raw, detail, AS_OF, calendar, "demo")
    assert result["intraday5m"] == []
    assert result["earlyEntry"]["state"] == "UNAVAILABLE"
    assert result["earlyEntry"]["quality"] == "UNAVAILABLE"
    assert result["earlyEntry"]["eligible"] is False
    assert "MALFORMED_OR_UNAVAILABLE_ENTRY_OBSERVATIONS" in result["earlyEntry"]["reason_codes"]


@pytest.mark.parametrize("wrong_config", [StrategyConfig(), {}, "legacy", object()])
def test_legacy_adapter_wrong_config_type_is_safe(legacy_observation, wrong_config):
    raw, detail, calendar = legacy_observation
    result = evaluate_legacy_stock_entry(raw, detail, AS_OF, calendar, "demo", wrong_config)
    assert result["earlyEntry"]["state"] == "UNAVAILABLE"
    assert result["earlyEntry"]["quality"] == "UNAVAILABLE"
    assert result["earlyEntry"]["eligible"] is False
    assert result["earlyEntry"]["orders_enabled"] is False
