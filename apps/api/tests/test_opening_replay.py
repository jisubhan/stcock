"""Point-in-time integration tests use invented data and no provider calls."""

from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from app.replay import opening_session
from app.replay.fixtures import SCENARIOS, SNAPSHOT_TIMES, clock, load_scenario
from app.replay.opening_session import GENERAL_SYNTHETIC_DAYS, replay_session


def leaders(result):
    return result["live_opening_flow_top3"]["items"]


def flows(result):
    return {item["theme_id"]: item for item in result["theme_flow_snapshots"]}


def point(result, label):
    return next(item for item in result["timeline"] if item["snapshot_time"] == label)


def test_t04_domestic_observed_flow_supersedes_weak_us_premarket_hypothesis():
    result = replay_session(snapshot_time="09:10", scenario="domestic_rotation")
    assert result["premarket_hypothesis_top3"]["items"][0]["theme_id"] == "optical_us"
    assert leaders(result)[0]["theme_id"] == "domestic_policy"
    observed = flows(result)
    assert observed["domestic_policy"]["turnover_velocity_krw_per_min"] > observed["optical_us"]["turnover_velocity_krw_per_min"]
    assert observed["domestic_policy"]["breadth_up_ratio"] == 1
    assert observed["domestic_policy"]["strength_type"] == "broad-based strength"
    assert observed["optical_us"]["price_momentum_5m"] < 0
    assert any(item["theme_id"] == "optical_us" and "PREMARKET_HYPOTHESIS_REJECTED" in item["reason_codes"]
               for item in result["rotation_history"])
    assert "policy-demo" in leaders(result)[0]["catalyst_event_ids"]
    assert result["entry_ready_top3"]["status"] == "UNAVAILABLE"
    assert result["entry_ready_top3"]["items"] == []


def test_us_leadership_requires_domestic_opening_observations_and_can_remain_leader():
    premarket = replay_session(snapshot_time="08:55", scenario="us_leadership")
    assert leaders(premarket) == []
    assert premarket["theme_flow_snapshots"] == []
    assert premarket["premarket_hypothesis_top3"]["items"][0]["theme_id"] == "optical_us"

    result = replay_session(snapshot_time="09:30", scenario="us_leadership")
    assert leaders(result)[0]["theme_id"] == "optical_us"
    observed = flows(result)["optical_us"]
    assert observed["breadth_up_ratio"] == 1
    assert observed["relative_turnover"] > 2
    assert observed["strength_type"] == "broad-based strength"
    assert result["entry_ready_top3"]["items"] == []


def test_reverse_rotation_changes_leader_using_later_observed_windows():
    result = replay_session(snapshot_time="09:30", scenario="reverse_rotation")
    assert point(result, "09:10")["live_opening_flow_top3"][0]["theme_id"] == "domestic_policy"
    assert point(result, "09:20")["live_opening_flow_top3"][0]["theme_id"] == "optical_us"
    assert leaders(result)[0]["theme_id"] == "optical_us"
    observed = flows(result)
    assert observed["domestic_policy"]["price_momentum_5m"] < 0
    assert observed["optical_us"]["price_momentum_5m"] > 0


@pytest.mark.parametrize("scenario", ["sector_divergence", "single_name_only"])
def test_t03_single_name_news_or_divergent_sector_cannot_claim_broad_participation(scenario):
    result = replay_session(snapshot_time="09:30", scenario=scenario)
    observed = flows(result)
    tender = observed["tender_single"]
    assert tender["strength_type"] == "single-name strength"
    assert tender["breadth_up_ratio"] == pytest.approx(1 / 3)
    assert tender["concentration_top1_ratio"] > .9
    assert tender["confirmed_investor_netbuy"] is None
    assert "SINGLE_NAME_STRENGTH" in tender["reason_codes"]
    if scenario == "sector_divergence":
        domestic = observed["domestic_policy"]
        assert domestic["strength_type"] == "single-name strength"
        assert domestic["breadth_up_ratio"] == pytest.approx(1 / 3)
    for item in leaders(result):
        if item["theme_id"] in ("domestic_policy", "tender_single"):
            assert item["metrics"]["strength_type"] != "broad-based strength"
    assert result["entry_ready_top3"]["items"] == []


def test_t12_pump_then_fade_loses_domestic_leader_and_reports_adverse_momentum():
    result = replay_session(snapshot_time="09:30", scenario="pump_then_fade")
    assert point(result, "09:10")["live_opening_flow_top3"][0]["theme_id"] == "domestic_policy"
    assert leaders(result)[0]["theme_id"] == "optical_us"
    assert "domestic_policy" not in [item["theme_id"] for item in leaders(result)]
    assert flows(result)["domestic_policy"]["price_momentum_5m"] < 0
    assert flows(result)["domestic_policy"]["breadth_up_ratio"] == 0
    assert result["metrics"]["entry_performance"]["status"] == "UNAVAILABLE"


@pytest.mark.parametrize("label", ["09:20", "09:30"])
def test_t09_stale_quote_outage_clears_current_observed_leaders(label):
    result = replay_session(snapshot_time=label, scenario="data_outage")
    assert point(result, "09:10")["live_opening_flow_top3"]
    assert leaders(result) == []
    assert result["live_opening_flow_top3"]["status"] == "STALE"
    assert result["data_freshness"]["quotes"]["status"] == "STALE"
    assert result["entry_ready_top3"]["items"] == []
    assert all(item["eligible_for_live_ranking"] is False for item in result["theme_flow_snapshots"])
    assert any("STALE_QUOTE" in item["reason_codes"] for item in result["theme_flow_snapshots"])
    assert any(item["state"] == "DATA_STALE" for item in result["rotation_history"])


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_all_scenarios_keep_replay_and_metrics_explicitly_synthetic(scenario):
    result = replay_session(snapshot_time="09:30", scenario=scenario)
    assert result["mode"] == "demo"
    assert result["data_source"] == "synthetic_replay"
    assert result["actual_market_data"] is False
    assert result["live_trading"] is False
    assert "Invented" in result["notice"]
    assert result["metrics"]["actual_market_data"] is False
    assert result["metrics"]["data_source"] == "SYNTHETIC_FIXTURE"
    assert all(item["mode"] == "demo" and item["actual_market_data"] is False for item in result["timeline"])
    assert all(item["provider"] == ["SYNTHETIC_FIXTURE"] or not item["provider"]
               for item in result["theme_flow_snapshots"])
    assert all(item["confirmed_investor_netbuy"] is None for item in result["theme_flow_snapshots"])
    assert result["data_freshness"]["investor_netbuy"]["status"] == "UNAVAILABLE"
    for metric in ("EntryCandidatePrecision", "MFE", "MAE"):
        assert result["metrics"][metric]["status"] == "UNAVAILABLE"
        assert result["metrics"][metric]["value"] is None
    performance = result["metrics"]["entry_performance"]
    assert performance["status"] == "UNAVAILABLE"
    assert performance["trades"] == []
    assert performance["net_return_pct"] is None
    assert performance["tax_pct"] is None


@pytest.mark.parametrize("label", SNAPSHOT_TIMES)
def test_t08_timeline_and_news_contain_only_the_available_prefix(label):
    result = replay_session(snapshot_time=label)
    target = clock("2026-10-12", label)
    assert result["timeline"][-1]["snapshot_time"] == label
    assert [item["snapshot_time"] for item in result["timeline"]] == [time for time in SNAPSHOT_TIMES if time <= label]
    for snapshot in result["timeline"]:
        seen = datetime.fromisoformat(snapshot["as_of"])
        assert seen <= target
        assert "late-news-demo" not in snapshot["catalyst_event_ids"]
        for flow in snapshot["theme_flow_snapshots"]:
            assert datetime.fromisoformat(flow["as_of"]) == seen
            assert datetime.fromisoformat(flow["data_freshness"]["window_end"]) <= seen
            for observation in flow["symbol_metrics"]:
                assert datetime.fromisoformat(observation["window_end"]) <= seen
                assert datetime.fromisoformat(observation["quote_at"]) <= seen
        for transition in snapshot["transitions"]:
            assert datetime.fromisoformat(transition["as_of"]) <= seen
    assert "late-news-demo" not in [event["event_id"] for event in result["catalysts"]]
    for event in result["catalysts"]:
        assert datetime.fromisoformat(event["published_at"]) <= target
        assert datetime.fromisoformat(event["first_seen_at"]) <= target
    for transition in result["rotation_history"]:
        assert datetime.fromisoformat(transition["as_of"]) <= target


def test_changing_future_prices_flow_and_news_cannot_change_0910_replay(monkeypatch):
    original = replay_session(snapshot_time="09:10")
    fixture = load_scenario()
    target = clock("2026-10-12", "09:10")
    future_changed = deepcopy(fixture)
    future_changed["observations"] = [
        row.model_copy(update={"turnover_krw": 10**16, "price_close": 10**12})
        if row.interval_end > target else row for row in future_changed["observations"]
    ]
    for event in future_changed["catalysts"]:
        if datetime.fromisoformat(event["first_seen_at"]) > target:
            event.update(title="FUTURE INPUT MUST NOT AFFECT PRESENT", theme_ids=["optical_us"],
                         materiality_score=1, confidence=1)
    monkeypatch.setattr(opening_session, "load_scenario", lambda *args: future_changed)
    changed = replay_session(snapshot_time="09:10")
    assert changed["timeline"] == original["timeline"]
    assert changed["metrics"] == original["metrics"]
    assert changed["live_opening_flow_top3"] == original["live_opening_flow_top3"]
    assert changed["catalysts"] == original["catalysts"]


def test_t08_early_publication_with_0953_first_receipt_is_still_invisible(monkeypatch):
    original = replay_session(snapshot_time="09:10")
    fixture = load_scenario()
    late = next(item for item in fixture["catalysts"] if item["event_id"] == "late-news-demo")
    late["published_at"] = clock("2026-10-12", "08:00").isoformat()
    assert late["first_seen_at"] == clock("2026-10-12", "09:53").isoformat()
    monkeypatch.setattr(opening_session, "load_scenario", lambda *args: fixture)
    result = replay_session(snapshot_time="09:10")
    assert "late-news-demo" not in [item["event_id"] for item in result["catalysts"]]
    assert result["timeline"] == original["timeline"]
    assert result["live_opening_flow_top3"] == original["live_opening_flow_top3"]


def test_old_interval_received_later_behaves_as_absent_until_receipt(monkeypatch):
    fixture = load_scenario()
    target = clock("2026-10-12", "09:10")
    late = next(row for row in fixture["observations"] if row.symbol == "KR01" and row.interval_end == target)
    absent = deepcopy(fixture)
    absent["observations"] = [row for row in absent["observations"]
                              if not (row.symbol == late.symbol and row.interval_end == late.interval_end)]
    monkeypatch.setattr(opening_session, "load_scenario", lambda *args: absent)
    missing = replay_session(snapshot_time="09:10")

    fixture["observations"] = [row.model_copy(update={"first_seen_at": target + timedelta(seconds=1)})
                               if row is late else row for row in fixture["observations"]]
    monkeypatch.setattr(opening_session, "load_scenario", lambda *args: fixture)
    unavailable = replay_session(snapshot_time="09:10")
    assert unavailable["timeline"] == missing["timeline"]
    assert unavailable["metrics"] == missing["metrics"]
    assert unavailable["live_opening_flow_top3"] == missing["live_opening_flow_top3"]


def test_turnover_recall_uses_same_time_window_rather_than_end_of_day_gainers():
    result = replay_session(snapshot_time="09:30", scenario="reverse_rotation")
    recall = result["metrics"]["Top3TurnoverRecall@09:10"]
    assert recall["status"] == "DEMO"
    assert recall["as_of"] == clock("2026-10-12", "09:10").isoformat()
    assert "domestic_policy" in recall["reference_theme_ids"]
    assert "optical_us" not in recall["reference_theme_ids"]
    assert leaders(result)[0]["theme_id"] == "optical_us"
    assert "never final-day gainers" in recall["definition"]
    assert 0 <= recall["value"] <= 1
    expected = set(recall["reference_theme_ids"]) & set(recall["selected_theme_ids"])
    assert set(recall["recalled_theme_ids"]) == expected
    assert recall["value"] == pytest.approx(len(expected) / len(recall["reference_theme_ids"]))


def test_detection_latency_starts_at_first_received_time_not_original_publication():
    result = replay_session(snapshot_time="09:30")
    metric = result["metrics"]["DetectionLatency"]
    assert metric["status"] == "DEMO"
    policy = next(event for event in result["catalysts"] if event["event_id"] == "policy-demo")
    measured = next(event for event in metric["events"] if event["event_id"] == "policy-demo")
    received = datetime.fromisoformat(policy["first_seen_at"])
    published = datetime.fromisoformat(policy["published_at"])
    selected = datetime.fromisoformat(measured["selected_at"])
    assert received > published + timedelta(days=2)
    assert measured["seconds"] == (selected - received).total_seconds()
    assert measured["seconds"] < (selected - published).total_seconds()


@pytest.mark.parametrize("day", GENERAL_SYNTHETIC_DAYS)
def test_t12_at_least_five_general_days_are_test_shapes_with_no_future_entry_proof(day):
    assert len(set(GENERAL_SYNTHETIC_DAYS)) >= 5
    result = replay_session(session=day, snapshot_time="09:30")
    target = clock(day, "09:30")
    assert result["mode"] == "demo"
    assert result["actual_market_data"] is False
    assert result["replay"]["actual_market_data"] is False
    assert result["replay"]["session"] == day
    assert "실제 거래일/시세 아님" in result["session_label"]
    assert result["entry_ready_top3"]["items"] == []
    for trace in result["entry_regressions"]:
        assert trace["actual_market_data"] is False
        assert trace["participates_in_opening_ranks"] is False
        assert "never a real historical backtest" in trace["notice"]
        for decision in trace["decisions"]:
            assert datetime.fromisoformat(decision["as_of"]) <= target
            assert decision["quality"] == "DEMO"
            assert decision["scenario_label"].startswith("Invented")


def test_explicit_as_of_overrides_snapshot_label_without_future_metrics():
    target = clock("2026-10-12", "09:05")
    result = replay_session(snapshot_time="09:30", as_of=target)
    assert result["as_of"] == target.isoformat()
    assert result["timeline"][-1]["snapshot_time"] == "09:05"
    assert result["metrics"]["Top3TurnoverRecall@09:10"]["status"] == "UNAVAILABLE"
    assert result["metrics"]["Top3TurnoverRecall@09:10"]["value"] is None


@pytest.mark.parametrize("kwargs", [
    {"snapshot_time": "09:53"}, {"scenario": "not_a_scenario"},
    {"as_of": "2026-10-13T09:10:00+09:00"},
])
def test_invalid_replay_request_is_rejected_instead_of_selecting_another_session(kwargs):
    with pytest.raises(ValueError):
        replay_session(**kwargs)
