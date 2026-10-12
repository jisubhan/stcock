"""Rule-engine tests on constructed point-in-time inputs, not live evidence."""
from datetime import datetime, timedelta

import pytest

from app.radar.models import KST, ThemeFlowSnapshot
from app.radar.rotation import OpeningFlowRotationEngine


START = datetime(2026, 10, 12, 9, 0, tzinfo=KST)
URL = "https://example.test/constructed-observations"


def snapshot(theme="alpha", minute=5, turnover=300, relative=3, breadth=1, momentum=3,
             mode="live", window_end=None, quote_age=0, eligible=True, quality=None, strength="broad-based strength"):
    clock = START + timedelta(minutes=minute)
    end = window_end or clock
    return ThemeFlowSnapshot(theme_id=theme, theme_name=theme.title(), as_of=clock,
        session_id="KRX_REGULAR", window_minutes=5, mode=mode, provider=["constructed_fixture"], source_urls=[URL],
        coverage={"symbols_seen": 3, "symbols_expected": 3, "ratio": 1, "venues": ["CONSOLIDATED"],
                  "covered_universe_symbols": 12, "expected_universe_symbols": 12, "universe_ratio": 1,
                  "universe_turnover_krw": 10000},
        turnover_krw=turnover, raw_turnover_krw=turnover, turnover_velocity_krw_per_min=turnover / 5,
        raw_turnover_velocity_krw_per_min=turnover / 5, relative_turnover=relative, history_sessions=10,
        breadth_up_ratio=breadth, breadth_high_rvol_ratio=breadth, concentration_top1_ratio=1 / 3,
        price_momentum_5m=momentum, covered_universe_share=turnover / 10000, strength_type=strength,
        confidence=1, quality=quality or ("LIVE" if mode == "live" else mode.upper()),
        data_freshness={"window_end": end.isoformat(), "max_quote_age_seconds": quote_age,
                        "session_elapsed_minutes": minute, "freshness_limit_seconds": 15},
        reason_codes=[], eligible_for_live_ranking=eligible)


def evaluate(engine, rows, **kwargs):
    return engine.evaluate(rows, as_of=rows[0].as_of if rows else kwargs.pop("as_of"), **kwargs)


def established(engine=None, rows=None):
    engine = engine or OpeningFlowRotationEngine()
    rows = rows or {"alpha": 500, "beta": 350, "gamma": 200}
    evaluate(engine, [snapshot(theme, 5, turnover=value) for theme, value in rows.items()])
    result = evaluate(engine, [snapshot(theme, 10, turnover=value) for theme, value in rows.items()])
    return engine, result


def hypothesis(theme="alpha", **changes):
    return {"theme_id": theme, "theme_name": theme.title(), "score": 95, "as_of": START.isoformat(),
            "mode": "live", "source_urls": [URL], **changes}


def catalyst(theme="delta", **changes):
    return {"event_id": "constructed-domestic-event", "theme_ids": [theme], "status": "VERIFIED",
            "relationship": "DIRECT_VERIFIED", "confidence": 1, "materiality_score": 1,
            "published_at": START.isoformat(), "first_seen_at": START.isoformat(),
            "source_url": URL, "mode": "live", **changes}


def entry(theme="alpha", **changes):
    now = START + timedelta(minutes=10)
    return {"symbol": "000001", "theme_id": theme, "state": "EARLY_ENTRY_READY", "mode": "live",
            "first_seen_at": now.isoformat(), "as_of": now.isoformat(), "quote_at": now.isoformat(),
            "completed_5m_bar": True, "reward_risk": 2.2, "stop_distance_pct": 1.2,
            "rr_to_first_resistance": 1.1, "liquidity_ok": True, "trading_halted": False,
            "vi_active": False, "extreme_spread": False, **changes}


def test_confirmation_requires_two_distinct_nonoverlapping_observed_windows():
    engine = OpeningFlowRotationEngine()
    first = evaluate(engine, [snapshot()])
    assert first.live_opening_flow_top3 == []
    assert first.reason_codes == ["AWAITING_CONFIRMATION_WINDOWS"]
    duplicate = evaluate(engine, [snapshot()])
    assert duplicate.ranked_themes[0].metrics["confirmation_windows"] == 1
    repeated_data = evaluate(engine, [snapshot(minute=5 + 5 / 60, window_end=START + timedelta(minutes=5))])
    assert repeated_data.live_opening_flow_top3 == []
    overlapping = evaluate(engine, [snapshot(minute=9)])
    assert overlapping.ranked_themes[0].metrics["confirmation_windows"] == 1
    confirmed = evaluate(engine, [snapshot(minute=10)])
    assert confirmed.live_opening_flow_top3[0].metrics["confirmation_windows"] == 2


def test_long_observation_gap_resets_sustained_confirmation():
    engine = OpeningFlowRotationEngine()
    evaluate(engine, [snapshot(minute=5)])
    result = evaluate(engine, [snapshot(minute=25)])
    assert result.live_opening_flow_top3 == []
    assert result.ranked_themes[0].metrics["confirmation_windows"] == 1


def test_t04_new_domestic_theme_replaces_weak_premarket_hypothesis():
    engine = OpeningFlowRotationEngine()
    rows = [snapshot("alpha", 5, turnover=20, relative=.2, breadth=0, momentum=-1),
            snapshot("beta", 5, turnover=200), snapshot("gamma", 5, turnover=250), snapshot("delta", 5, turnover=400)]
    evaluate(engine, rows, hypotheses=[hypothesis()], catalysts=[catalyst()])
    rows = [item.model_copy(update={"as_of": START + timedelta(minutes=10),
                "data_freshness": {**item.data_freshness, "window_end": (START + timedelta(minutes=10)).isoformat()}}) for item in rows]
    result = evaluate(engine, rows, hypotheses=[hypothesis()], catalysts=[catalyst()])
    assert result.premarket_hypothesis_top3[0].theme_id == "alpha"
    assert result.premarket_hypothesis_top3[0].state == "REJECTED_FOR_TODAY"
    assert result.live_opening_flow_top3[0].theme_id == "delta"
    assert "alpha" not in [row.theme_id for row in result.live_opening_flow_top3]
    rejected = next(t for t in result.transitions if t.theme_id == "alpha")
    assert "PREMARKET_HYPOTHESIS_REJECTED" in rejected.reason_codes
    assert rejected.metrics["relative_turnover"] == .2
    assert rejected.source_urls == [URL]
    discovered = next(t for t in result.transitions if t.theme_id == "delta")
    assert "NEW_THEME_DISCOVERED" in discovered.reason_codes


def test_news_absence_does_not_exclude_measured_broad_strength():
    _, result = established()
    assert len(result.live_opening_flow_top3) == 3
    assert "UNEXPLAINED_MOMENTUM" in result.live_opening_flow_top3[0].reason_codes
    assert result.live_opening_flow_top3[0].catalyst_event_ids == []
    assert result.entry_ready_top3 == []


def test_t03_single_name_is_penalized_and_never_described_as_broad():
    engine = OpeningFlowRotationEngine()
    result = evaluate(engine, [snapshot("single", strength="single-name strength"), snapshot("broad")])
    scores = {item.theme_id: item.score for item in result.ranked_themes}
    assert scores["broad"] - scores["single"] == pytest.approx(12)
    assert next(item for item in result.ranked_themes if item.theme_id == "single").metrics["strength_type"] == "single-name strength"


def test_t09_stale_quotes_clear_observed_rank_without_reusing_hypotheses():
    engine, _ = established()
    result = evaluate(engine, [snapshot("alpha", 11, quote_age=16, eligible=False, quality="STALE")], hypotheses=[hypothesis()])
    assert result.quality == "STALE"
    assert result.live_opening_flow_top3 == [] and result.entry_ready_top3 == []
    assert result.premarket_hypothesis_top3[0].theme_id == "alpha"
    assert any(t.state == "DATA_STALE" for t in result.transitions)
    assert any(n.notification_type == "DATA_FEED_STALE" for n in result.notifications)


@pytest.mark.parametrize("change", [{"eligible_for_live_ranking": False}, {"relative_turnover": None},
                                    {"quality": "PARTIAL"}, {"quality": "UNAVAILABLE"}])
def test_insufficient_coverage_or_history_never_inherits_observed_top3(change):
    engine, _ = established()
    bad = snapshot("alpha", 11).model_copy(update=change)
    result = evaluate(engine, [bad], hypotheses=[hypothesis()])
    assert result.live_opening_flow_top3 == []
    assert "LIVE_FLOW_UNVERIFIABLE" in result.reason_codes


def test_stale_snapshot_cannot_refresh_itself_with_later_api_request_clock():
    engine = OpeningFlowRotationEngine()
    result = engine.evaluate([snapshot()], as_of=START + timedelta(minutes=5, seconds=16), hypotheses=[hypothesis()])
    assert result.live_opening_flow_top3 == [] and result.quality == "STALE"


def test_fresh_snapshot_and_quote_cannot_mask_a_stale_turnover_window():
    result = evaluate(OpeningFlowRotationEngine(), [snapshot(minute=10, window_end=START + timedelta(minutes=5))], hypotheses=[hypothesis()])
    assert result.ranked_themes == [] and result.live_opening_flow_top3 == []
    assert result.quality == "STALE"
    assert "STALE_TURNOVER" in result.reason_codes


def test_t10_small_score_crossing_does_not_churn_incumbent_leader():
    engine, initial = established(rows={"alpha": 500, "beta": 490, "gamma": 200})
    assert initial.live_opening_flow_top3[0].theme_id == "alpha"
    result = evaluate(engine, [snapshot("alpha", 15, turnover=490), snapshot("beta", 15, turnover=500), snapshot("gamma", 15, turnover=200)])
    assert result.ranked_themes[0].theme_id == "beta"
    assert result.live_opening_flow_top3[0].theme_id == "alpha"
    assert result.notifications == []


def test_t10_major_new_verified_rotation_bypasses_dwell_and_two_window_wait():
    engine, _ = established()
    result = evaluate(engine, [snapshot("alpha", 11, turnover=40), snapshot("beta", 11, turnover=35),
                               snapshot("gamma", 11, turnover=20), snapshot("delta", 11, turnover=2000)], catalysts=[catalyst()])
    assert result.live_opening_flow_top3[0].theme_id == "delta"
    assert "MAJOR_ROTATION_EXCEPTION" in result.live_opening_flow_top3[0].reason_codes
    assert result.live_opening_flow_top3[0].metrics["confirmation_windows"] == 1


def test_t10_major_existing_member_rotation_can_reorder_before_dwell():
    engine, _ = established()
    result = evaluate(engine, [snapshot("alpha", 11, turnover=20), snapshot("beta", 11, turnover=350),
                               snapshot("gamma", 11, turnover=2000)])
    assert result.live_opening_flow_top3[0].theme_id == "gamma"
    assert "MAJOR_ROTATION_EXCEPTION" in result.live_opening_flow_top3[0].reason_codes


def test_material_score_gap_still_waits_for_minimum_dwell_without_major_exception():
    engine, _ = established()
    result = evaluate(engine, [snapshot("alpha", 11, turnover=400), snapshot("beta", 11, turnover=350),
                               snapshot("gamma", 11, turnover=1000)])
    assert result.live_opening_flow_top3[0].theme_id == "alpha"


def test_future_news_first_seen_or_publication_cannot_change_score():
    normal = evaluate(OpeningFlowRotationEngine(), [snapshot()])
    for changes in ({"first_seen_at": (START + timedelta(minutes=53)).isoformat()},
                    {"published_at": (START + timedelta(minutes=53)).isoformat()}):
        result = evaluate(OpeningFlowRotationEngine(), [snapshot()], catalysts=[catalyst("alpha", **changes)])
        assert result.ranked_themes[0].score == normal.ranked_themes[0].score
        assert result.ranked_themes[0].catalyst_event_ids == []


@pytest.mark.parametrize("changes", [{"status": "RETRACTED"}, {"status": "CORRECTED"},
    {"relationship": "THEMATIC_ASSOCIATION"}, {"status": "UNCONFIRMED"}, {"source_url": ""}])
def test_unverified_or_retracted_events_cannot_receive_verified_catalyst_points(changes):
    result = evaluate(OpeningFlowRotationEngine(), [snapshot()], catalysts=[catalyst("alpha", **changes)])
    assert result.ranked_themes[0].metrics["feature_scores"]["confirmed_catalyst"] == 0
    assert result.ranked_themes[0].catalyst_event_ids == []


def test_holiday_event_after_last_trading_close_is_not_lost_to_72hour_cutoff():
    event = catalyst("alpha", published_at="2026-10-08T16:00:00+09:00", first_seen_at="2026-10-08T16:00:10+09:00")
    ordinary = evaluate(OpeningFlowRotationEngine(), [snapshot()], catalysts=[event])
    extended = evaluate(OpeningFlowRotationEngine({"context": {"last_trading_close": "2026-10-08T15:30:00+09:00"}}), [snapshot()], catalysts=[event])
    assert ordinary.ranked_themes[0].catalyst_event_ids == []
    assert extended.ranked_themes[0].catalyst_event_ids == [event["event_id"]]


def test_entry_top3_requires_risk_and_completed_bar_even_when_theme_is_leader():
    engine = OpeningFlowRotationEngine()
    evaluate(engine, [snapshot()])
    result = evaluate(engine, [snapshot(minute=10)], entry_candidates=[entry()])
    assert result.entry_ready_top3[0]["symbol"] == "000001"


def test_public_entry_selector_accepts_verified_flat_ready_without_state_mutation():
    engine, _ = established()
    previous = engine.state_dict()
    selected = engine.select_entry_candidates([entry()], as_of=START + timedelta(minutes=10))
    assert selected == [entry()]
    assert engine.state_dict() == previous


def test_entry_selector_uses_one_slot_per_symbol_and_keeps_best_qualified_rr():
    engine, _ = established()
    candidates = [entry("alpha", reward_risk=2.1), entry("beta", reward_risk=3.5),
                  entry("gamma", symbol="000002", reward_risk=2.5),
                  entry("alpha", symbol="000003", reward_risk=2.4)]
    selected = engine.select_entry_candidates(candidates, as_of=START + timedelta(minutes=10))
    assert [item["symbol"] for item in selected] == ["000001", "000002", "000003"]
    assert selected[0]["theme_id"] == "beta" and selected[0]["reward_risk"] == 3.5


def test_risk_qualified_entry_from_fourth_confirmed_theme_is_separate_from_flow_top3():
    engine, result = established(rows={"alpha": 500, "beta": 350, "gamma": 200, "delta": 180})
    assert [item.theme_id for item in result.live_opening_flow_top3] == ["alpha", "beta", "gamma"]
    assert next(item for item in result.ranked_themes if item.theme_id == "delta").state == "LIVE_THEME_CONFIRMED"
    selected = engine.select_entry_candidates([entry("alpha", reward_risk=.9), entry("delta", symbol="000004", reward_risk=3.4)], as_of=START + timedelta(minutes=10))
    assert [item["theme_id"] for item in selected] == ["delta"]
    # A current missing theme must disappear from selector scope immediately,
    # even while its previously stored metric timestamp remains within 15 s.
    now = START + timedelta(minutes=10, seconds=5)
    current = [snapshot(theme, minute=10 + 5 / 60, turnover=value) for theme, value in
               {"alpha": 500, "beta": 350, "gamma": 200}.items()]
    evaluate(engine, current)
    assert engine.select_entry_candidates([entry("delta", symbol="000004", quote_at=now.isoformat())], as_of=now) == []


def test_rank_four_hypothesis_label_never_overrides_current_measured_entry_scope():
    engine, _ = established(rows={"alpha": 500, "beta": 350, "gamma": 200, "delta": 180})
    result = evaluate(engine, [snapshot(theme, 10, turnover=value) for theme, value in
        {"alpha": 500, "beta": 350, "gamma": 200, "delta": 180}.items()], hypotheses=[hypothesis("delta")])
    assert result.premarket_hypothesis_top3[0].state == "WATCH_ONLY"
    assert engine.select_entry_candidates([entry("delta")], as_of=START + timedelta(minutes=10))
    result = evaluate(engine, [snapshot(theme, 11, turnover=value, breadth=0 if theme == "delta" else 1) for theme, value in
        {"alpha": 500, "beta": 350, "gamma": 200, "delta": 180}.items()], hypotheses=[hypothesis("delta")])
    assert next(item for item in result.ranked_themes if item.theme_id == "delta").state == "FADING"
    assert engine.select_entry_candidates([entry("delta", quote_at=(START + timedelta(minutes=11)).isoformat())], as_of=START + timedelta(minutes=11)) == []


def test_public_entry_selector_does_not_revive_stale_theme_with_fresh_stock_quote():
    engine, _ = established()
    now = START + timedelta(minutes=10, seconds=16)
    candidates = [entry(quote_at=now.isoformat(), first_seen_at=now.isoformat())]
    assert engine.select_entry_candidates(candidates, as_of=now) == []
    assert engine.select_entry_candidates([entry()], as_of=START + timedelta(minutes=5)) == []


@pytest.mark.parametrize("changes", [{"completed_5m_bar": False}, {"reward_risk": 1.99},
    {"stop_distance_pct": 2.01}, {"rr_to_first_resistance": .99}, {"liquidity_ok": False},
    {"trading_halted": True}, {"vi_active": None}, {"extreme_spread": True},
    {"state": "BREAKOUT_CONFIRMED"}, {"quote_at": "2026-10-12T09:09:44+09:00"},
    {"first_seen_at": "2026-10-12T09:53:00+09:00"}, {"theme_id": "unobserved"},
    {"reward_risk": float("inf")}])
def test_unsafe_or_future_entries_are_excluded(changes):
    engine = OpeningFlowRotationEngine()
    evaluate(engine, [snapshot()])
    result = evaluate(engine, [snapshot(minute=10)], entry_candidates=[entry(**changes)])
    assert result.entry_ready_top3 == []


def test_notification_fingerprints_and_state_survive_restore_and_duplicate_read():
    engine, result = established()
    assert result.notifications
    resumed = OpeningFlowRotationEngine()
    resumed.restore_state(engine.state_dict())
    duplicate = evaluate(resumed, [snapshot("alpha", 10, turnover=500), snapshot("beta", 10, turnover=350), snapshot("gamma", 10, turnover=200)])
    assert duplicate.notifications == [] and duplicate.transitions == []
    assert [item.theme_id for item in duplicate.live_opening_flow_top3] == ["alpha", "beta", "gamma"]


def test_state_domain_resets_when_mode_or_trading_date_changes():
    engine, _ = established()
    replay = evaluate(engine, [snapshot(mode="replay")], mode="replay")
    assert replay.mode == "replay" and replay.quality == "REPLAY" and replay.live_opening_flow_top3 == []
    next_day = snapshot().model_copy(update={"as_of": START + timedelta(days=1, minutes=5)})
    result = engine.evaluate([next_day], as_of=next_day.as_of)
    assert result.live_opening_flow_top3 == []


def test_source_modes_never_mix_in_hypotheses_flow_or_news():
    result = evaluate(OpeningFlowRotationEngine(), [snapshot()], hypotheses=[hypothesis(mode="demo")], catalysts=[catalyst("alpha", mode="demo")])
    assert result.premarket_hypothesis_top3 == []
    assert result.ranked_themes[0].catalyst_event_ids == []
    missing = OpeningFlowRotationEngine().evaluate([snapshot(mode="demo")], as_of=START + timedelta(minutes=5), mode="live")
    assert missing.ranked_themes == []


def test_backwards_state_clock_is_rejected_instead_of_leaking_future_leaders():
    engine, _ = established()
    with pytest.raises(ValueError, match="backwards"):
        evaluate(engine, [snapshot()])


def test_regular_session_never_inherits_premarket_confirmation_or_leaders():
    engine, _ = established()
    next_session = snapshot(minute=11).model_copy(update={"session_id": "NXT_AFTERMARKET"})
    result = evaluate(engine, [next_session])
    assert result.live_opening_flow_top3 == []
    assert result.ranked_themes[0].metrics["confirmation_windows"] == 1
    assert engine.state_dict()["session_id"] == "NXT_AFTERMARKET"


def test_one_evaluation_cannot_sum_or_confirm_two_market_sessions():
    engine = OpeningFlowRotationEngine()
    with pytest.raises(ValueError, match="mix market sessions"):
        evaluate(engine, [snapshot(), snapshot("beta").model_copy(update={"session_id": "NXT_PREMARKET"})])


def test_theme_cooldown_suppresses_another_material_change_but_keeps_audit():
    engine, first = established()
    assert any(n.theme_id == "gamma" for n in first.notifications)
    changed = evaluate(engine, [snapshot("alpha", 11, turnover=20), snapshot("beta", 11, turnover=350), snapshot("gamma", 11, turnover=2000)])
    assert changed.live_opening_flow_top3[0].theme_id == "gamma"
    assert any(t.theme_id == "gamma" for t in changed.transitions)
    assert not any(n.theme_id == "gamma" for n in changed.notifications)


def test_theme_breadth_collapse_marks_fading_and_removes_entry_readiness():
    engine, _ = established()
    rows = [snapshot("alpha", 11, turnover=500, breadth=0), snapshot("beta", 11, turnover=350), snapshot("gamma", 11, turnover=200)]
    result = evaluate(engine, rows, hypotheses=[hypothesis()], entry_candidates=[entry(quote_at=(START + timedelta(minutes=11)).isoformat())])
    alpha = next(item for item in result.live_opening_flow_top3 if item.theme_id == "alpha")
    assert alpha.state == "FADING"
    assert result.premarket_hypothesis_top3[0].state == "FADING"
    assert result.entry_ready_top3 == []
    assert "THEME_BREADTH_COLLAPSED" in next(t for t in result.transitions if t.theme_id == "alpha").reason_codes
    assert any(n.notification_type == "THEME_BREADTH_COLLAPSED" for n in result.notifications)


def test_fading_theme_can_recover_with_audited_state_transition():
    engine, _ = established()
    evaluate(engine, [snapshot("alpha", 11, breadth=0)])
    recovered = evaluate(engine, [snapshot("alpha", 12)])
    assert recovered.live_opening_flow_top3[0].state == "LIVE_THEME_CONFIRMED"
    assert recovered.transitions[0].previous_state == "FADING"


@pytest.mark.parametrize("flag", ["replace_premarket_top3_after_open", "opening_flow_rotation"])
def test_disabled_feature_analyzes_metrics_without_promotions_or_rejection(flag):
    engine = OpeningFlowRotationEngine({"feature_flags": {flag: False}})
    evaluate(engine, [snapshot()], hypotheses=[hypothesis()])
    result = evaluate(engine, [snapshot(minute=10)], hypotheses=[hypothesis()], entry_candidates=[entry()])
    assert result.live_opening_flow_top3 == [] and result.entry_ready_top3 == []
    assert result.premarket_hypothesis_top3[0].state == "HYPOTHESIS"
    assert result.ranked_themes[0].state == "ANALYSIS_ONLY"
    assert "AUTOMATIC_ROTATION_DISABLED" in result.reason_codes
    assert result.notifications == []


def test_per_symbol_association_does_not_inherit_another_company_official_bonus():
    event = catalyst("alpha", theme_ids=["alpha", "beta"], ticker_relations=[
        {"symbol": "000001", "relationship": "DIRECT_VERIFIED"},
        {"symbol": "000002", "relationship": "THEMATIC_ASSOCIATION"}])
    a = snapshot("alpha").model_copy(update={"symbol_metrics": [{"symbol": "000001"}]})
    b = snapshot("beta").model_copy(update={"symbol_metrics": [{"symbol": "000002"}]})
    result = evaluate(OpeningFlowRotationEngine(), [a, b], catalysts=[event])
    items = {item.theme_id: item for item in result.ranked_themes}
    assert items["alpha"].metrics["feature_scores"]["confirmed_catalyst"] == 1
    assert items["beta"].metrics["feature_scores"]["confirmed_catalyst"] == 0
    assert items["beta"].catalyst_event_ids == []
    assert "UNVERIFIED_CATALYST" in items["beta"].reason_codes


def test_transitions_preserve_numeric_cause_and_source_for_changed_ranks():
    engine, _ = established()
    changed = evaluate(engine, [snapshot("alpha", 15, turnover=20), snapshot("beta", 15, turnover=1000), snapshot("gamma", 15, turnover=200)])
    transition = next(t for t in changed.transitions if t.theme_id == "beta")
    assert transition.previous_rank == 2 and transition.rank == 1
    assert transition.metrics["changes"]["turnover_velocity_krw_per_min"] == {"previous": 70, "current": 200}
    assert "70" in transition.explanation and "200" in transition.explanation
    assert transition.source_urls == [URL]
