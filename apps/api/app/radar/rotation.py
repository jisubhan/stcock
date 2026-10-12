"""Stateful, point-in-time opening rotation without conflating entry readiness."""
from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import json
import math
from typing import Iterable, Mapping

from .flow import section
from .models import (RadarNotification, RankItem, RotationResult, RotationTransition,
                     ThemeFlowSnapshot, aware)


def _dt(value) -> datetime | None:
    if value is None:
        return None
    try:
        return aware(value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except (ValueError, TypeError):
        return None


def _fingerprint(*parts) -> str:
    return sha256(json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


class OpeningFlowRotationEngine:
    """One logical session/mode state, persisted by the caller if desired.

    A repeated API read never supplies another confirmation. Confirmation windows
    must end at least one complete rolling-window duration apart. Material changes
    have an explicit, measured exception to dwell/hysteresis, never to data quality.
    """
    def __init__(self, config: dict | None = None):
        self.config = section(config)
        self._state = self._empty()

    @staticmethod
    def _empty():
        return {"version": 1, "mode": None, "session_date": None, "session_id": None, "last_as_of": None,
                "leaders": [], "leader_since": {}, "states": {}, "confirmations": {},
                "current_confirmed": [], "last_metrics": {}, "last_scores": {}, "cooldowns": {}, "fingerprints": []}

    def state_dict(self) -> dict:
        """JSON-safe copy; no provider secrets or external storage access."""
        return json.loads(json.dumps(self._state))

    def restore_state(self, state: Mapping) -> None:
        state = json.loads(json.dumps(state))
        # Earlier v1.2 snapshots lacked this current-evaluation selector scope.
        # An empty default safely requires another evaluation before selection.
        state.setdefault("current_confirmed", [])
        if state.get("version") != 1 or state.get("mode") not in (None, "live", "demo", "replay"):
            raise ValueError("Unsupported rotation state")
        required = self._empty()
        if any(key not in state or type(state[key]) is not type(value) for key, value in required.items()
               if value is not None):
            raise ValueError("Malformed rotation state")
        if state.get("last_as_of") is not None and _dt(state["last_as_of"]) is None:
            raise ValueError("Malformed rotation clock")
        self._state = {key: state.get(key, value) for key, value in required.items()}

    def _catalysts(self, inputs, snapshot, clock):
        entries, unverified = [], False
        news_age = self.config.get("data", {}).get("max_news_age_for_new_catalyst_hours", 72)
        last_close = _dt(self.config.get("context", {}).get("last_trading_close"))
        earliest = clock - timedelta(hours=news_age)
        if last_close is not None and last_close <= clock:
            earliest = min(earliest, last_close)
        for value in inputs:
            event = value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)
            if snapshot.theme_id not in event.get("theme_ids", []):
                continue
            published, first_seen = _dt(event.get("published_at")), _dt(event.get("first_seen_at"))
            if not published or not first_seen or published > clock or first_seen > clock or published < earliest:
                continue
            if event.get("mode", snapshot.mode) != snapshot.mode:
                continue
            if event.get("status") in ("CORRECTED", "RETRACTED"):
                continue
            if event.get("status") != "VERIFIED" or event.get("relationship") not in ("DIRECT_VERIFIED", "NEWS_REPORTED"):
                unverified = True
                continue
            if "ticker_relations" in event:
                observed_symbols = {item.get("symbol") for item in snapshot.symbol_metrics}
                matched = [item for item in event["ticker_relations"] if item.get("symbol") in observed_symbols
                           and item.get("relationship") in ("DIRECT_VERIFIED", "NEWS_REPORTED")]
                if not matched:
                    unverified = True
                    continue
                event["relationship"] = "DIRECT_VERIFIED" if any(item["relationship"] == "DIRECT_VERIFIED" for item in matched) else "NEWS_REPORTED"
            if not str(event.get("source_url", "")).startswith(("https://", "http://", "demo://", "replay://")):
                unverified = True
                continue
            entries.append(event)
        return entries, unverified

    def _score(self, snapshots, catalysts, clock):
        weights = self.config.get("scoring", {}).get("live_theme", {
            "turnover_velocity": .25, "relative_turnover": .20, "breadth": .20,
            "price_momentum": .10, "confirmed_catalyst": .15, "data_quality": .10})
        penalties = self.config.get("scoring", {}).get("penalty", {})
        maximum = max((s.turnover_velocity_krw_per_min for s in snapshots), default=0)
        ranked = []
        for snapshot in snapshots:
            events, unverified = self._catalysts(catalysts, snapshot, clock)
            strength = max((float(event.get("confidence", 0)) * float(event.get("materiality_score", 0))
                            * (1 if event.get("relationship") == "DIRECT_VERIFIED" else .75) for event in events), default=0)
            age = snapshot.data_freshness.get("max_quote_age_seconds")
            freshness = 1 if age is not None and age <= self.config.get("data", {}).get("max_live_quote_age_seconds", 15) else 0
            features = {
                "turnover_velocity": snapshot.turnover_velocity_krw_per_min / maximum if maximum else 0,
                "relative_turnover": min(1., (snapshot.relative_turnover or 0) / 3),
                "breadth": .6 * (snapshot.breadth_up_ratio or 0) + .4 * (snapshot.breadth_high_rvol_ratio or 0),
                "price_momentum": min(1., max(0., snapshot.price_momentum_5m or 0) / 3),
                "confirmed_catalyst": min(1., max(0., strength)),
                "data_quality": snapshot.coverage.ratio * snapshot.coverage.universe_ratio * freshness}
            score = sum(features.get(key, 0) * float(weight) * 100 for key, weight in weights.items())
            reasons = list(snapshot.reason_codes)
            if snapshot.strength_type == "single-name strength" and penalties.get("single_name_only", True):
                score -= 12
            if unverified and penalties.get("unverified_catalyst", True):
                score -= 5
                reasons.append("UNVERIFIED_CATALYST")
            if not events:
                reasons.append("UNEXPLAINED_MOMENTUM")
            else:
                reasons.append("VERIFIED_DOMESTIC_CATALYST")
            metrics = {"turnover_krw": snapshot.turnover_krw, "raw_turnover_krw": snapshot.raw_turnover_krw,
                       "turnover_velocity_krw_per_min": snapshot.turnover_velocity_krw_per_min,
                       "relative_turnover": snapshot.relative_turnover, "history_sessions": snapshot.history_sessions,
                       "breadth_up_ratio": snapshot.breadth_up_ratio, "breadth_high_rvol_ratio": snapshot.breadth_high_rvol_ratio,
                       "concentration_top1_ratio": snapshot.concentration_top1_ratio, "price_momentum_5m": snapshot.price_momentum_5m,
                       "coverage_ratio": snapshot.coverage.ratio, "universe_coverage_ratio": snapshot.coverage.universe_ratio,
                       "covered_universe_share": snapshot.covered_universe_share, "strength_type": snapshot.strength_type,
                       "confirmed_investor_netbuy": snapshot.confirmed_investor_netbuy, "feature_scores": features,
                       "window_end": snapshot.data_freshness.get("window_end"), "window_minutes": snapshot.window_minutes,
                       "session_elapsed_minutes": snapshot.data_freshness.get("session_elapsed_minutes", 0),
                       "eligible_for_live_ranking": snapshot.eligible_for_live_ranking, "quality": snapshot.quality,
                       "max_quote_age_seconds": age}
            sources = sorted(set(snapshot.source_urls) | {e["source_url"] for e in events})
            ranked.append(RankItem(theme_id=snapshot.theme_id, theme_name=snapshot.theme_name, rank=1,
                                  score=round(max(0., min(100., score)), 4), state="OPENING_ROTATION_CANDIDATE",
                                  mode=snapshot.mode, as_of=clock, reason_codes=sorted(set(reasons)),
                                  explanation=self._explanation(snapshot, score, events), metrics=metrics,
                                  source_urls=sources, catalyst_event_ids=sorted({str(e["event_id"]) for e in events})))
        ranked.sort(key=lambda item: (-item.score, -item.metrics["turnover_velocity_krw_per_min"], item.theme_id))
        for rank, item in enumerate(ranked, 1):
            item.rank = rank
        return ranked

    @staticmethod
    def _explanation(snapshot, score, events):
        relative = f"{snapshot.relative_turnover:.2f}배" if snapshot.relative_turnover is not None else "확인 불가"
        breadth = f"{snapshot.breadth_up_ratio * 100:.1f}%" if snapshot.breadth_up_ratio is not None else "확인 불가"
        return (f"거래대금 속도 {snapshot.turnover_velocity_krw_per_min:,.0f}원/분, 동일 세션 상대 거래대금 {relative}, "
                f"상승 참여 {breadth}, 관측 {snapshot.coverage.symbols_seen}/{snapshot.coverage.symbols_expected}종목 "
                f"({snapshot.coverage.ratio * 100:.1f}%), 점수 {max(0, min(100, score)):.1f}. "
                + (f"검증된 재료 {len(events)}건." if events else "확인된 뉴스 원인은 없습니다. 거래대금은 순매수가 아닙니다."))

    def _confirm(self, item, clock):
        end = _dt(item.metrics.get("window_end"))
        previous = self._state["confirmations"].get(item.theme_id, {})
        last_end = _dt(previous.get("window_end"))
        duration = item.metrics.get("window_minutes", 5) * 60
        if end is None:
            return 0
        if not last_end:
            count = 1
        elif (end - last_end).total_seconds() >= max(1, duration):
            # A long observation outage is not sustained confirmation.
            count = previous.get("count", 0) + 1 if (end - last_end).total_seconds() <= max(120, duration * 2) else 1
        else:
            return previous.get("count", 0)
        self._state["confirmations"][item.theme_id] = {"count": count, "window_end": end.isoformat()}
        return count

    def _entries(self, inputs, leaders, clock, mode):
        if not leaders:
            return []
        config = self.config.get("entry", {})
        result = []
        for value in inputs:
            item = value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)
            if item.get("theme_id") not in leaders or item.get("mode", mode) != mode:
                continue
            available = _dt(item.get("first_seen_at")) or _dt(item.get("as_of"))
            quote = _dt(item.get("quote_at"))
            if not available or available > clock or not quote or quote > clock:
                continue
            if (clock - quote).total_seconds() > self.config.get("data", {}).get("max_live_quote_age_seconds", 15):
                continue
            if item.get("state") not in ("EARLY_ENTRY_READY", "ENTRY_READY") or not item.get("completed_5m_bar", False):
                continue
            try:
                rr = float(item["reward_risk"])
                stop = float(item["stop_distance_pct"])
                first_rr = float(item["rr_to_first_resistance"])
            except (ValueError, TypeError, KeyError):
                continue
            if not all(math.isfinite(value) for value in (rr, stop, first_rr)):
                continue
            if not (rr >= config.get("min_reward_risk", 2.) and 0 < stop <= config.get("max_stop_distance_pct", 2.)
                    and first_rr >= config.get("no_chasing_if_to_first_resistance_rr_below", 1.)):
                continue
            if any(item.get(key) is not False for key in ("trading_halted", "vi_active", "extreme_spread")) or item.get("liquidity_ok") is not True:
                continue
            result.append(item)
        result.sort(key=lambda item: (-float(item["reward_risk"]), str(item.get("symbol", ""))))
        unique, symbols = [], set()
        for item in result:
            symbol = item.get("symbol")
            if not isinstance(symbol, str) or not symbol or symbol in symbols:
                continue
            unique.append(item)
            symbols.add(symbol)
            if len(unique) == 3:
                break
        return unique

    def select_entry_candidates(self, inputs, as_of: datetime, mode="live") -> list[dict]:
        """Pure selection using this session's fresh, confirmed theme state.

        Runtime adapters flatten observed risk/market gates before calling this.
        Selecting entries never advances a confirmation, emits an alert or changes
        rank state. A fresh stock quote cannot revive an old theme observation.
        """
        clock = aware(as_of)
        last_clock = _dt(self._state.get("last_as_of"))
        if (mode not in ("live", "demo", "replay") or self._state.get("mode") != mode
                or self._state.get("session_date") != clock.date().isoformat()
                or not last_clock or last_clock > clock):
            return []
        maximum_age = self.config.get("data", {}).get("max_live_quote_age_seconds", 15)
        leaders = set()
        for theme_id in self._state["current_confirmed"]:
            metrics = self._state["last_metrics"].get(theme_id, {})
            end = _dt(metrics.get("window_end"))
            quote_age = metrics.get("max_quote_age_seconds")
            if (end and quote_age is not None and 0 <= (clock - end).total_seconds() <= maximum_age
                    and quote_age + (clock - last_clock).total_seconds() <= maximum_age):
                leaders.add(theme_id)
        return self._entries(inputs, leaders, clock, mode)

    def evaluate(self, snapshots: Iterable[ThemeFlowSnapshot | Mapping], *, as_of: datetime,
                 hypotheses=(), entry_candidates=(), catalysts=(), mode="live") -> RotationResult:
        clock = aware(as_of)
        if mode not in ("live", "demo", "replay"):
            raise ValueError("Unknown rotation data mode")
        snapshots = [s if isinstance(s, ThemeFlowSnapshot) else ThemeFlowSnapshot.model_validate(s) for s in snapshots]
        snapshots = [s for s in snapshots if s.mode == mode and s.as_of <= clock]
        sessions = {snapshot.session_id for snapshot in snapshots}
        if len(sessions) > 1:
            raise ValueError("One rotation evaluation cannot mix market sessions")
        session_id = next(iter(sessions), self._state.get("session_id"))
        domain = (mode, clock.date().isoformat(), session_id)
        if domain != (self._state["mode"], self._state["session_date"], self._state.get("session_id")):
            self._state = self._empty()
            self._state.update(mode=mode, session_date=domain[1], session_id=session_id)
        previous_clock = _dt(self._state["last_as_of"])
        if previous_clock and clock < previous_clock:
            raise ValueError("Rotation state cannot run backwards; replay in a separate engine")
        maximum_age = self.config.get("data", {}).get("max_live_quote_age_seconds", 15)
        valid = [s for s in snapshots if s.eligible_for_live_ranking and s.relative_turnover is not None
                 and s.quality in ("LIVE", "DEMO", "REPLAY") and (clock - s.as_of).total_seconds() <= maximum_age
                 and _dt(s.data_freshness.get("window_end")) is not None
                 and 0 <= (clock - _dt(s.data_freshness["window_end"])).total_seconds() <= maximum_age
                 and s.data_freshness.get("max_quote_age_seconds") is not None
                 and s.data_freshness["max_quote_age_seconds"] + (clock - s.as_of).total_seconds() <= maximum_age]
        # Missing/stale inputs never inherit yesterday's observed winners or the
        # hypothesis list. Prior ranks only survive as immutable audit records.
        ranked = self._score(valid, list(catalysts), clock)
        flags = self.config.get("feature_flags", {})
        rotation_enabled = flags.get("opening_flow_rotation", True) and flags.get("replace_premarket_top3_after_open", True)
        by_id = {item.theme_id: item for item in ranked}
        current_ids = set(by_id)
        for theme_id in list(self._state["confirmations"]):
            if theme_id not in current_ids:
                self._state["confirmations"].pop(theme_id)
        scoring = self.config.get("scoring", {})
        minimum = int(scoring.get("min_confirmation_windows", 2))
        hysteresis = float(scoring.get("promote_hysteresis_points", 8))
        dwell = float(scoring.get("min_leader_dwell_seconds", 180))
        confirmed = []
        for item in ranked:
            count = self._confirm(item, clock) if rotation_enabled else 0
            item.metrics["confirmation_windows"] = count
            if not rotation_enabled:
                item.state = "ANALYSIS_ONLY"
                item.reason_codes.append("AUTOMATIC_ROTATION_DISABLED")
            elif count >= minimum:
                item.state = "LIVE_THEME_CONFIRMED"
                confirmed.append(item.theme_id)
            elif item.metrics.get("session_elapsed_minutes", 0) < 5:
                item.state = "OPENING_DISCOVERY"
        previous_leaders = list(self._state["leaders"])
        retained = [theme_id for theme_id in previous_leaders if theme_id in current_ids] if rotation_enabled else []
        if not rotation_enabled:
            self._state["confirmations"].clear()
        winners = retained[:3]
        for challenger in confirmed:
            if challenger in winners:
                continue
            if len(winners) < 3:
                winners.append(challenger)
                continue
            weakest = min(winners, key=lambda theme_id: by_id[theme_id].score)
            gap = by_id[challenger].score - by_id[weakest].score
            since = _dt(self._state["leader_since"].get(weakest)) or clock
            if gap >= hysteresis and (clock - since).total_seconds() >= dwell:
                winners[winners.index(weakest)] = challenger
        # Explicit major rotation exception: confirmed new information and a
        # large score gap, or >=2x leader velocity with broader participation.
        # It bypasses dwell/confirmation only, never provenance/coverage/history.
        for item in ranked:
            if item.theme_id in winners or len(winners) < 3:
                continue
            weakest = min(winners, key=lambda theme_id: by_id[theme_id].score)
            leader = by_id[weakest]
            gap = item.score - leader.score
            major = gap >= max(20, hysteresis * 2) and (
                bool(item.catalyst_event_ids) or (
                    item.metrics["turnover_velocity_krw_per_min"] >= leader.metrics["turnover_velocity_krw_per_min"] * 2
                    and item.metrics["breadth_up_ratio"] >= .8 and item.metrics["strength_type"] == "broad-based strength"))
            if major:
                winners[winners.index(weakest)] = item.theme_id
                item.state = "LIVE_THEME_CONFIRMED"
                item.reason_codes.append("MAJOR_ROTATION_EXCEPTION")
        # Preserve a small-margin incumbent order, but let an >=8-point,
        # sufficiently dwelled challenger (or explicit major rotation) lead.
        stable = [theme_id for theme_id in previous_leaders if theme_id in winners]
        stable += [theme_id for theme_id in winners if theme_id not in stable]
        for challenger in sorted(stable, key=lambda theme_id: -by_id[theme_id].score):
            position = stable.index(challenger)
            while position:
                incumbent = stable[position - 1]
                since = _dt(self._state["leader_since"].get(incumbent)) or clock
                gap = by_id[challenger].score - by_id[incumbent].score
                challenger_item, incumbent_item = by_id[challenger], by_id[incumbent]
                major = gap >= max(20, hysteresis * 2) and (bool(challenger_item.catalyst_event_ids) or (
                    challenger_item.metrics["turnover_velocity_krw_per_min"] >= incumbent_item.metrics["turnover_velocity_krw_per_min"] * 2
                    and challenger_item.metrics["breadth_up_ratio"] >= .8 and challenger_item.metrics["strength_type"] == "broad-based strength"))
                if major and "MAJOR_ROTATION_EXCEPTION" not in challenger_item.reason_codes:
                    challenger_item.reason_codes.append("MAJOR_ROTATION_EXCEPTION")
                if gap < hysteresis or ((clock - since).total_seconds() < dwell and "MAJOR_ROTATION_EXCEPTION" not in by_id[challenger].reason_codes):
                    break
                stable[position - 1], stable[position] = stable[position], stable[position - 1]
                position -= 1
        winners = stable[:3]
        for item in ranked:
            if (item.theme_id in self._state["current_confirmed"] or self._state["states"].get(item.theme_id) in ("LIVE_THEME_CONFIRMED", "FADING")) and (item.metrics["breadth_up_ratio"] < .34 or item.metrics["relative_turnover"] < 1):
                item.state = "FADING"
                item.reason_codes.append("THEME_BREADTH_COLLAPSED" if item.metrics["breadth_up_ratio"] < .34 else "LEADER_FLOW_WEAKENED")
        observed = [by_id[theme_id].model_copy(deep=True) for theme_id in winners]
        for rank, item in enumerate(observed, 1):
            item.rank = rank
            item.state = "FADING" if item.state == "FADING" else "LIVE_THEME_CONFIRMED"
        hypothesis_items = []
        for value in hypotheses:
            value = value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)
            if not value.get("theme_id") or value.get("mode", mode) != mode:
                continue
            available = _dt(value.get("first_seen_at")) or _dt(value.get("as_of"))
            if available and available > clock:
                continue
            theme_id = value["theme_id"]
            state = "HYPOTHESIS"
            item = by_id.get(theme_id)
            if rotation_enabled and theme_id in winners:
                state = "FADING" if by_id[theme_id].state == "FADING" else "LIVE_THEME_CONFIRMED"
            elif rotation_enabled and item and item.metrics["confirmation_windows"] >= minimum:
                state = "REJECTED_FOR_TODAY" if item.metrics["relative_turnover"] < 1 or item.metrics["breadth_up_ratio"] < .34 else "WATCH_ONLY"
            elif rotation_enabled and ranked:
                state = "WATCH_ONLY"
            hypothesis_items.append(RankItem(theme_id=theme_id, theme_name=value.get("theme_name", value.get("name", theme_id)),
                rank=len(hypothesis_items) + 1, score=max(0., min(100., float(value.get("score", 0)))), state=state,
                mode=mode, as_of=clock, explanation=value.get("explanation", "개장 전 가설입니다. 실측 수급 순위와 별도로 표시합니다."),
                reason_codes=["PREMARKET_HYPOTHESIS"], metrics=value.get("metrics", {}), source_urls=value.get("source_urls", [])))
            if len(hypothesis_items) == 3:
                break
        desired_states = {item.theme_id: item.state for item in ranked}
        desired_states.update({item.theme_id: item.state for item in hypothesis_items})
        desired_states.update({item.theme_id: item.state for item in observed})
        for theme_id in previous_leaders:
            if theme_id not in winners:
                desired_states[theme_id] = "AUTOMATIC_ROTATION_DISABLED" if not rotation_enabled else "DATA_STALE" if theme_id not in current_ids else by_id[theme_id].state
        transitions, notifications = [], []
        for theme_id, state in desired_states.items():
            old_state = self._state["states"].get(theme_id, "HYPOTHESIS" if any(h.theme_id == theme_id for h in hypothesis_items) else "UNSEEN")
            old_rank = previous_leaders.index(theme_id) + 1 if theme_id in previous_leaders else None
            new_rank = winners.index(theme_id) + 1 if theme_id in winners else None
            if old_state == state and old_rank == new_rank:
                continue
            item = by_id.get(theme_id)
            metrics = item.metrics if item else {}
            old_metrics = self._state["last_metrics"].get(theme_id, {})
            changes = {key: {"previous": old_metrics.get(key), "current": metrics.get(key)} for key in
                       ("turnover_velocity_krw_per_min", "relative_turnover", "breadth_up_ratio", "coverage_ratio")}
            explanation = item.explanation if item else "시세 신선도·커버리지 또는 상대 거래대금 자료가 부족해 실측 순위를 중지합니다."
            if old_rank is not None or new_rank is not None:
                explanation = f"순위 {old_rank or '미선정'} → {new_rank or '제외'}. " + explanation
            if old_metrics and metrics:
                explanation += (f" 직전 속도 {old_metrics.get('turnover_velocity_krw_per_min', 0):,.0f} → "
                                f"현재 {metrics.get('turnover_velocity_krw_per_min', 0):,.0f}원/분.")
            reasons = list(item.reason_codes if item else ["DATA_FEED_STALE"])
            if new_rank is not None and old_rank is None:
                reasons.append("OPENING_ROTATION_CONFIRMED")
                if not any(h.theme_id == theme_id for h in hypothesis_items):
                    reasons.append("NEW_THEME_DISCOVERED")
            elif state == "REJECTED_FOR_TODAY":
                reasons.append("PREMARKET_HYPOTHESIS_REJECTED")
            elif old_rank is not None and old_rank != new_rank:
                reasons.append("THEME_LEADER_CHANGED")
            fingerprint = _fingerprint(mode, theme_id, old_state, state, old_rank, new_rank, metrics.get("window_end"))
            transition = RotationTransition(transition_id=fingerprint, theme_id=theme_id, as_of=clock,
                previous_state=old_state, state=state, previous_rank=old_rank, rank=new_rank,
                previous_score=self._state["last_scores"].get(theme_id), score=item.score if item else None,
                explanation=explanation, reason_codes=sorted(set(reasons)), metrics={**metrics, "changes": changes},
                source_urls=item.source_urls if item else [], mode=mode)
            transitions.append(transition)
            important = rotation_enabled and (old_rank != new_rank or state in ("REJECTED_FOR_TODAY", "DATA_STALE", "FADING"))
            notification_type = "DATA_FEED_STALE" if state == "DATA_STALE" else "THEME_BREADTH_COLLAPSED" if "THEME_BREADTH_COLLAPSED" in reasons else "PREMARKET_HYPOTHESIS_REJECTED" if state == "REJECTED_FOR_TODAY" else "THEME_LEADER_CHANGED" if old_rank else "OPENING_ROTATION_CONFIRMED"
            cooldown_key = f"{theme_id}:{notification_type}" if notification_type in ("DATA_FEED_STALE", "THEME_BREADTH_COLLAPSED") else theme_id
            last_notification = _dt(self._state["cooldowns"].get(cooldown_key))
            cooldown = self.config.get("notification", {}).get("theme_cooldown_seconds", 600)
            if important and fingerprint not in self._state["fingerprints"] and (last_notification is None or (clock - last_notification).total_seconds() >= cooldown):
                notifications.append(RadarNotification(fingerprint=fingerprint, theme_id=theme_id, notification_type=notification_type,
                    as_of=clock, explanation=explanation, source_urls=transition.source_urls, mode=mode))
                self._state["cooldowns"][cooldown_key] = clock.isoformat()
                self._state["fingerprints"].append(fingerprint)
        for theme_id in winners:
            if theme_id not in previous_leaders:
                self._state["leader_since"][theme_id] = clock.isoformat()
        self._state["leaders"] = winners
        self._state["current_confirmed"] = [item.theme_id for item in ranked if item.state == "LIVE_THEME_CONFIRMED"] if rotation_enabled else []
        self._state["states"].update(desired_states)
        self._state["last_metrics"].update({item.theme_id: item.metrics for item in ranked})
        self._state["last_scores"].update({item.theme_id: item.score for item in ranked})
        self._state["last_as_of"] = clock.isoformat()
        self._state["fingerprints"] = self._state["fingerprints"][-256:]
        reasons = []
        if not rotation_enabled:
            reasons = ["AUTOMATIC_ROTATION_DISABLED"]
        elif not valid:
            reasons = sorted({reason for snapshot in snapshots for reason in snapshot.reason_codes} | {"LIVE_FLOW_UNVERIFIABLE"})
            if any(_dt(s.data_freshness.get("window_end")) is not None
                   and (clock - _dt(s.data_freshness["window_end"])).total_seconds() > maximum_age for s in snapshots):
                reasons = sorted(set(reasons) | {"STALE_TURNOVER"})
        elif not observed:
            reasons = ["AWAITING_CONFIRMATION_WINDOWS"]
        quality = "LIVE" if valid else "STALE" if any(s.quality == "STALE" or (clock - s.as_of).total_seconds() > maximum_age
            or (_dt(s.data_freshness.get("window_end")) is not None and (clock - _dt(s.data_freshness["window_end"])).total_seconds() > maximum_age)
            for s in snapshots) else "UNAVAILABLE"
        if mode != "live":
            quality = "DEMO" if mode == "demo" else "REPLAY"
        return RotationResult(as_of=clock, mode=mode, quality=quality, premarket_hypothesis_top3=hypothesis_items,
            live_opening_flow_top3=observed, entry_ready_top3=self.select_entry_candidates(entry_candidates, clock, mode),
            ranked_themes=ranked, transitions=transitions, notifications=notifications, reason_codes=reasons,
            data_freshness={"as_of": clock.isoformat(), "max_quote_age_seconds": max((s.data_freshness.get("max_quote_age_seconds", 0) for s in valid), default=None),
                            "freshness_limit_seconds": maximum_age})
