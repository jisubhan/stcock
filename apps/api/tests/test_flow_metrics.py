"""Point-in-time and measurement regressions for the v1.2 opening-flow radar.

All observations in this file are constructed fixtures, never live API evidence.
"""
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from app.radar.models import (
    KST,
    FlowObservation,
    HistoricalFlowObservation,
    InvestorNetBuyObservation,
    ThemeRelation,
)


AS_OF = datetime(2026, 10, 12, 9, 10, tzinfo=KST)
SESSION_START = AS_OF.replace(minute=0)
SYMBOLS = ["000001", "000002", "000003"]
SOURCE_URL = "https://example.test/constructed-market-observations"


def flow(symbol=SYMBOLS[0], turnover=100, **overrides):
    values = {
        "symbol": symbol,
        "observed_at": AS_OF,
        "first_seen_at": AS_OF,
        "interval_start": AS_OF - timedelta(minutes=5),
        "interval_end": AS_OF,
        "session_id": "KRX_REGULAR",
        "venue": "CONSOLIDATED",
        "turnover_krw": turnover,
        "price_open": 100,
        "price_close": 105,
        "quote_at": AS_OF,
        "provider": "constructed_test_source",
        "source_url": SOURCE_URL,
        "mode": "live",
        "bar_status": "COMPLETE",
    }
    values.update(overrides)
    return FlowObservation(**values)


def relation(symbol=SYMBOLS[0], theme="alpha", **overrides):
    values = {
        "theme_id": theme,
        "theme_name": theme.title(),
        "symbol": symbol,
        "weight": 1,
        "relationship": "DIRECT_VERIFIED",
        "effective_from": AS_OF - timedelta(days=30),
        "first_seen_at": AS_OF - timedelta(days=30),
        "source_url": SOURCE_URL,
    }
    values.update(overrides)
    return ThemeRelation(**values)


def prior_sessions(count=10):
    days = []
    day = AS_OF.date()
    while len(days) < count:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    return days


def historical(symbols=SYMBOLS, count=10, turnover=50, **overrides):
    observations = []
    for symbol in symbols:
        for day in prior_sessions(count):
            stamp = AS_OF.replace(year=day.year, month=day.month, day=day.day)
            values = {
                "symbol": symbol,
                "session_id": "KRX_REGULAR",
                "session_date": day,
                "minute_from_session": 10,
                "window_minutes": 5,
                "turnover_krw": turnover,
                "observed_at": stamp,
                "first_seen_at": stamp,
                "venue": "CONSOLIDATED",
                "provider": "constructed_test_source",
                "source_url": SOURCE_URL,
                "mode": "live",
            }
            values.update(overrides)
            observations.append(HistoricalFlowObservation(**values))
    return observations


def investor(symbol=SYMBOLS[0], **overrides):
    values = {
        "symbol": symbol,
        "observed_at": AS_OF - timedelta(minutes=1),
        "first_seen_at": AS_OF - timedelta(minutes=1),
        "venue": "CONSOLIDATED",
        "unit": "SHARES",
        "values": {"foreign": 10, "institution": None},
        "provider": "constructed_test_source",
        "source_url": SOURCE_URL,
        "quality": "DELAYED",
        "mode": "live",
    }
    values.update(overrides)
    return InvestorNetBuyObservation(**values)


def snapshots(observations=None, relations=None, history=None, **overrides):
    # Kept in one place so the test cases describe measurements, not plumbing.
    from app.radar.flow import compute_theme_flows

    values = {
        "observations": [flow(symbol) for symbol in SYMBOLS] if observations is None else observations,
        "relations": [relation(symbol) for symbol in SYMBOLS] if relations is None else relations,
        "history": historical() if history is None else history,
        "as_of": AS_OF,
        "session_id": "KRX_REGULAR",
        "session_start": SESSION_START,
        "window_minutes": 5,
        "mode": "live",
    }
    values.update(overrides)
    return compute_theme_flows(**values)


def alpha(**kwargs):
    return next(item for item in snapshots(**kwargs) if item.theme_id == "alpha")


def test_observation_rejects_naive_times_and_premature_complete_bar():
    with pytest.raises(ValidationError, match="timezone aware"):
        flow(quote_at=AS_OF.replace(tzinfo=None))
    with pytest.raises(ValidationError, match="before its end"):
        flow(observed_at=AS_OF - timedelta(seconds=1))


def test_half_open_interval_must_have_positive_duration():
    with pytest.raises(ValidationError, match=r"\[start, end\)"):
        flow(interval_start=AS_OF)


def test_t03_single_name_strength_does_not_become_broad_theme_strength():
    result = alpha(observations=[
        flow(SYMBOLS[0], turnover=1_000, price_close=120),
        flow(SYMBOLS[1], turnover=1, price_close=95),
        flow(SYMBOLS[2], turnover=1, price_close=95),
    ])
    assert result.strength_type == "single-name strength"
    assert result.breadth_up_ratio == pytest.approx(1 / 3)
    assert result.concentration_top1_ratio == pytest.approx(1_000 / 1_002)


def test_t03_three_participating_symbols_are_broad_based():
    result = alpha()
    assert result.strength_type == "broad-based strength"
    assert result.breadth_up_ratio == 1
    assert result.concentration_top1_ratio == pytest.approx(1 / 3)


def test_t05_turnover_and_price_gains_never_invent_investor_netbuy():
    result = alpha()
    assert result.turnover_krw == 300
    assert result.turnover_velocity_krw_per_min == 60
    assert result.price_momentum_5m > 0
    assert result.confirmed_investor_netbuy is None


def test_t05_real_observation_retains_unit_venue_lag_and_unknown_categories():
    records = [investor(symbol, values={"foreign": value, "institution": None})
               for symbol, value in zip(SYMBOLS, [10, -5, 20])]
    result = alpha(investor_netbuy=records)
    series = result.confirmed_investor_netbuy["series"]
    assert len(series) == 1
    assert series[0]["unit"] == "SHARES"
    assert series[0]["venue"] == "CONSOLIDATED"
    assert series[0]["values"] == {"foreign": 25, "institution": None}
    assert series[0]["quality"] == "DELAYED"
    assert series[0]["as_of"] == (AS_OF - timedelta(minutes=1)).isoformat()
    assert series[0]["source_urls"] == [SOURCE_URL]


def test_t05_investor_currency_and_share_counts_are_not_added_together():
    result = alpha(investor_netbuy=[
        investor(values={"foreign": 10}),
        investor(unit="KRW", values={"foreign": 1_000}),
    ])
    values_by_unit = {item["unit"]: item["values"]["foreign"]
                      for item in result.confirmed_investor_netbuy["series"]}
    assert values_by_unit == {"SHARES": 10, "KRW": 1_000}


@pytest.mark.parametrize("hidden", [
    {"observed_at": AS_OF + timedelta(seconds=1)},
    {"first_seen_at": AS_OF + timedelta(seconds=1)},
    {"mode": "demo"},
    {"mode": "replay"},
])
def test_t05_future_or_simulated_investor_flow_cannot_fill_live_netbuy(hidden):
    assert alpha(investor_netbuy=[investor(**hidden)]).confirmed_investor_netbuy is None


def test_t06_integrated_feed_is_not_added_to_its_component_venues():
    observations = [flow(symbol) for symbol in SYMBOLS]
    observations.extend([
        flow(SYMBOLS[0], turnover=70, venue="KRX"),
        flow(SYMBOLS[0], turnover=30, venue="NXT"),
    ])
    result = alpha(observations=observations)
    assert result.turnover_krw == 300
    assert result.raw_turnover_krw == 300
    assert result.coverage.universe_turnover_krw == 300


def test_t06_distinct_krx_and_nxt_turnover_can_be_summed_once():
    observations = [flow(symbol) for symbol in SYMBOLS[1:]]
    observations.extend([
        flow(SYMBOLS[0], turnover=70, venue="KRX"),
        flow(SYMBOLS[0], turnover=30, venue="NXT"),
    ])
    result = alpha(observations=observations)
    assert result.turnover_krw == 300
    assert result.coverage.symbols_seen == 3


def test_t06_repeated_delivery_and_future_revision_do_not_double_count():
    original = flow(SYMBOLS[0], turnover=100, observation_id="same-interval")
    observations = [original, original.model_copy(deep=True)]
    observations.extend([flow(symbol) for symbol in SYMBOLS[1:]])
    observations.append(flow(
        SYMBOLS[0], turnover=900,
        first_seen_at=AS_OF + timedelta(seconds=1),
        observation_id="future-revision",
    ))
    assert alpha(observations=observations).turnover_krw == 300


def test_t06_investor_integrated_venue_is_not_added_again():
    result = alpha(investor_netbuy=[
        investor(values={"foreign": 100}),
        investor(venue="KRX", values={"foreign": 70}),
        investor(venue="NXT", values={"foreign": 30}),
    ])
    series = result.confirmed_investor_netbuy["series"]
    assert len(series) == 1
    assert series[0]["venue"] == "CONSOLIDATED"
    assert series[0]["values"]["foreign"] == 100


def test_latest_visible_revision_replaces_previous_interval_version():
    observations = [flow(symbol) for symbol in SYMBOLS[1:]]
    observations.extend([
        flow(SYMBOLS[0], turnover=80, first_seen_at=AS_OF - timedelta(seconds=2)),
        flow(SYMBOLS[0], turnover=100, first_seen_at=AS_OF - timedelta(seconds=1)),
    ])
    assert alpha(observations=observations).turnover_krw == 300


def test_adjacent_intervals_cover_window_without_diluting_velocity():
    observations = [flow(symbol) for symbol in SYMBOLS[1:]]
    split = AS_OF - timedelta(minutes=3)
    observations.extend([
        flow(SYMBOLS[0], turnover=40, interval_end=split, observed_at=split, first_seen_at=split),
        flow(SYMBOLS[0], turnover=60, interval_start=split),
    ])
    result = alpha(observations=observations)
    assert result.turnover_krw == 300
    assert result.turnover_velocity_krw_per_min == 60


def test_mismatched_overlapping_intervals_fail_closed():
    observations = [flow(symbol) for symbol in SYMBOLS[1:]]
    observations.extend([
        flow(SYMBOLS[0], turnover=60, interval_end=AS_OF - timedelta(minutes=2)),
        flow(SYMBOLS[0], turnover=60, interval_start=AS_OF - timedelta(minutes=3)),
    ])
    result = alpha(observations=observations)
    assert "OVERLAPPING_INTERVALS" in result.reason_codes
    assert result.turnover_krw == 200
    assert SYMBOLS[0] not in {item["symbol"] for item in result.symbol_metrics}


def test_coarse_interval_crossing_window_is_not_prorated_into_fake_turnover():
    observations = [flow(symbol) for symbol in SYMBOLS[1:]]
    observations.append(flow(SYMBOLS[0], turnover=1_000, interval_start=SESSION_START))
    result = alpha(observations=observations)
    assert "PARTIAL_WINDOW" in result.reason_codes
    assert result.turnover_krw == 200
    assert result.coverage.symbols_seen == 2


def test_t11_multitheme_attribution_preserves_raw_view_and_covered_share():
    observations = [flow(symbol, turnover=value) for symbol, value in zip(SYMBOLS, [100, 200, 300])]
    relations = [
        relation(SYMBOLS[0], "alpha", weight=.8),
        relation(SYMBOLS[0], "beta", weight=.8),
        relation(SYMBOLS[1], "alpha"),
        relation(SYMBOLS[2], "beta"),
    ]
    result = {item.theme_id: item for item in snapshots(observations=observations, relations=relations)}
    assert result["alpha"].raw_turnover_krw == 300
    assert result["beta"].raw_turnover_krw == 400
    assert result["alpha"].turnover_krw == 250
    assert result["beta"].turnover_krw == 350
    assert sum(item.covered_universe_share for item in result.values()) == pytest.approx(1)
    assert sum(item.turnover_krw for item in result.values()) == 600
    assert all("not the entire Korean market" in item.coverage.denominator_definition for item in result.values())


def test_relative_turnover_requires_ten_distinct_prior_sessions():
    insufficient = alpha(history=historical(count=9))
    assert insufficient.history_sessions == 9
    assert insufficient.relative_turnover is None
    assert "INSUFFICIENT_HISTORY" in insufficient.reason_codes
    assert not insufficient.eligible_for_live_ranking
    complete = alpha()
    assert complete.history_sessions == 10
    assert complete.relative_turnover == 2


def test_t06_historical_integrated_values_also_replace_venue_components():
    history = historical()
    history.extend(historical(venue="KRX", turnover=35))
    history.extend(historical(venue="NXT", turnover=15))
    result = alpha(history=history)
    assert result.history_sessions == 10
    assert result.relative_turnover == 2


def test_integrated_current_cannot_use_krx_only_historical_baseline():
    result = alpha(history=historical(venue="KRX"))
    assert result.relative_turnover is None
    assert "INSUFFICIENT_HISTORY" in result.reason_codes
    assert not result.eligible_for_live_ranking
    assert all(item["relative_turnover"] is None for item in result.symbol_metrics)


def test_integrated_current_uses_both_venue_history_once_per_session():
    history = historical(venue="KRX", turnover=35)
    history.extend(historical(venue="NXT", turnover=15))
    result = alpha(history=history)
    assert result.history_sessions == 10
    assert result.relative_turnover == 2
    assert result.eligible_for_live_ranking


def test_krx_only_current_cannot_use_integrated_historical_baseline():
    result = alpha(observations=[flow(symbol, venue="KRX") for symbol in SYMBOLS])
    assert result.relative_turnover is None
    assert "INSUFFICIENT_HISTORY" in result.reason_codes
    assert not result.eligible_for_live_ranking
    assert all(item["relative_turnover"] is None for item in result.symbol_metrics)


def test_duplicate_historical_rows_do_not_create_additional_sessions():
    rows = historical(count=9)
    rows.extend(item.model_copy(deep=True) for item in rows[:3])
    result = alpha(history=rows)
    assert result.history_sessions == 9
    assert result.relative_turnover is None


@pytest.mark.parametrize("mismatch", [
    {"minute_from_session": 5},
    {"window_minutes": 10},
    {"session_id": "NXT_PREMARKET"},
    {"first_seen_at": AS_OF + timedelta(seconds=1)},
    {"observed_at": AS_OF + timedelta(seconds=1)},
    {"session_date": AS_OF.date()},
    {"mode": "demo"},
])
def test_noncomparable_or_future_history_never_supplies_relative_turnover(mismatch):
    result = alpha(history=historical(**mismatch))
    assert result.relative_turnover is None
    assert not result.eligible_for_live_ranking


@pytest.mark.parametrize("missing_time", ["first_seen_at", "observed_at"])
def test_future_flow_observations_are_excluded_at_point_in_time(missing_time):
    observations = [flow(SYMBOLS[0], **{missing_time: AS_OF + timedelta(seconds=1)})]
    observations.extend(flow(symbol) for symbol in SYMBOLS[1:])
    result = alpha(observations=observations)
    assert result.coverage.symbols_seen == 2
    assert result.turnover_krw == 200


def test_future_quote_timestamp_does_not_become_a_fresh_live_quote():
    observations = [flow(SYMBOLS[0], quote_at=AS_OF + timedelta(seconds=1))]
    observations.extend(flow(symbol) for symbol in SYMBOLS[1:])
    result = alpha(observations=observations)
    assert result.coverage.symbols_seen == 2
    assert result.turnover_krw == 200


def test_current_partial_bar_cannot_shift_completed_observation_window():
    later_clock = AS_OF + timedelta(minutes=2)
    observations = [flow(symbol, quote_at=later_clock) for symbol in SYMBOLS]
    observations.append(flow(
        SYMBOLS[0], turnover=99_999, interval_start=AS_OF,
        interval_end=AS_OF + timedelta(minutes=5),
        observed_at=later_clock, first_seen_at=later_clock,
        quote_at=later_clock, bar_status="PARTIAL",
    ))
    result = alpha(observations=observations, as_of=later_clock)
    assert result.turnover_krw == 300
    assert result.window_minutes == 5
    assert result.data_freshness["window_end"] == AS_OF.isoformat()
    assert result.relative_turnover == 2
    assert result.quality == "STALE"
    assert not result.eligible_for_live_ranking
    assert "STALE_TURNOVER" in result.reason_codes


def test_fresh_quote_does_not_turn_old_interval_turnover_into_live_data():
    later_clock = AS_OF + timedelta(minutes=5)
    result = alpha(observations=[flow(symbol, quote_at=later_clock) for symbol in SYMBOLS], as_of=later_clock)
    assert result.turnover_krw == 300 and result.relative_turnover == 2
    assert result.data_freshness["turnover_age_seconds"] == 300
    assert result.quality == "STALE" and not result.eligible_for_live_ranking
    assert "STALE_TURNOVER" in result.reason_codes


@pytest.mark.parametrize("age,eligible", [(15, True), (15.001, False)])
def test_provider_turnover_window_freshness_boundary_is_inclusive(age, eligible):
    now = AS_OF + timedelta(seconds=age)
    result = alpha(observations=[flow(symbol, quote_at=now) for symbol in SYMBOLS], as_of=now)
    assert result.eligible_for_live_ranking is eligible


@pytest.mark.parametrize("unknown_time", ["effective_from", "first_seen_at"])
def test_future_theme_relation_cannot_reclassify_past_turnover(unknown_time):
    relations = [relation(symbol) for symbol in SYMBOLS]
    relations.append(relation(SYMBOLS[0], "future-theme", **{unknown_time: AS_OF + timedelta(seconds=1)}))
    result = snapshots(relations=relations)
    assert {item.theme_id for item in result} == {"alpha"}
    assert result[0].turnover_krw == 300


def test_t09_missing_observations_are_explicitly_unavailable():
    result = alpha(observations=[])
    assert result.quality == "UNAVAILABLE"
    assert result.coverage.symbols_seen == 0
    assert result.coverage.ratio == 0
    assert result.relative_turnover is None
    assert result.confirmed_investor_netbuy is None
    assert not result.eligible_for_live_ranking
    assert result.reason_codes


@pytest.mark.parametrize("age,expected_live", [(15, True), (15.001, False)])
def test_live_quote_freshness_boundary(age, expected_live):
    observations = [flow(symbol, quote_at=AS_OF - timedelta(seconds=age)) for symbol in SYMBOLS]
    result = alpha(observations=observations)
    assert (result.quality == "LIVE") is expected_live
    assert result.eligible_for_live_ranking is expected_live


@pytest.mark.parametrize("seen,expected_live", [(13, True), (12, False)])
def test_t09_theme_and_universe_coverage_boundary(seen, expected_live):
    symbols = [f"{index:06d}" for index in range(20)]
    result = alpha(
        observations=[flow(symbol) for symbol in symbols[:seen]],
        relations=[relation(symbol) for symbol in symbols],
        history=historical(symbols=symbols),
    )
    assert result.coverage.ratio == pytest.approx(seen / 20)
    assert result.coverage.universe_ratio == pytest.approx(seen / 20)
    assert result.eligible_for_live_ranking is expected_live


def test_high_theme_coverage_cannot_hide_low_configured_universe_coverage():
    universe = SYMBOLS + [f"9{index:05d}" for index in range(7)]
    result = alpha(relations=[relation(symbol) for symbol in SYMBOLS] + [
        relation(symbol, "unobserved-theme") for symbol in universe[3:]
    ])
    assert result.coverage.ratio == 1
    assert result.coverage.universe_ratio == .3
    assert not result.eligible_for_live_ranking


@pytest.mark.parametrize("mode,quality", [("demo", "DEMO"), ("replay", "REPLAY")])
def test_simulated_sources_keep_their_quality_label(mode, quality):
    observations = [flow(symbol, mode=mode) for symbol in SYMBOLS]
    result = alpha(observations=observations, history=historical(mode=mode), mode=mode)
    assert result.mode == mode
    assert result.quality == quality
    assert result.relative_turnover == 2


@pytest.mark.parametrize("mode", ["demo", "replay"])
def test_simulated_observations_do_not_fill_a_live_gap(mode):
    observations = [flow(symbol, mode=mode) for symbol in SYMBOLS]
    result = alpha(observations=observations)
    assert result.quality == "UNAVAILABLE"
    assert result.coverage.symbols_seen == 0
    assert not result.eligible_for_live_ranking
