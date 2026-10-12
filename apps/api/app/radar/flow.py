"""Observed rolling turnover, coverage and comparable-session baselines.

No network, storage, clock reads, synthetic history, candle interpolation or
turnover-to-net-buy inference occurs here. Callers supply the point-in-time clock.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean
from typing import Iterable, Mapping

from .models import (FlowObservation, HistoricalFlowObservation, InvestorNetBuyObservation,
                     ThemeFlowSnapshot, ThemeRelation, aware)


def section(config: dict | None) -> dict:
    config = config or {}
    return config.get("flowtrade_v1_2", config)


def _typed(items, cls):
    return [item if isinstance(item, cls) else cls.model_validate(item) for item in items]


def _visible(item, clock: datetime, mode: str) -> bool:
    return item.mode == mode and item.observed_at <= clock and item.first_seen_at <= clock


def _interval_rows(rows: list[FlowObservation]) -> tuple[list[FlowObservation], str | None]:
    """Consolidated data wins; separate venues are only summed when disjoint.

    Identical venue/interval publications are revisions, not additional trades.
    Ambiguous partially overlapping intervals are refused rather than prorated.
    """
    latest = {}
    for row in rows:
        key = (row.interval_start, row.interval_end, row.venue)
        if key not in latest or (row.observed_at, row.first_seen_at, row.provider) > (
                latest[key].observed_at, latest[key].first_seen_at, latest[key].provider):
            latest[key] = row
    consolidated = [r for r in latest.values() if r.venue == "CONSOLIDATED"]
    selected = []
    for row in latest.values():
        if row.venue != "CONSOLIDATED":
            overlapping = [c for c in consolidated if c.interval_start < row.interval_end and row.interval_start < c.interval_end]
            if overlapping:
                if any(c.interval_start <= row.interval_start and c.interval_end >= row.interval_end for c in overlapping):
                    continue
                return [], "OVERLAPPING_INTERVALS"
        selected.append(row)
    # KRX + NXT intervals may overlap, but two different intervals from the same
    # venue cannot be summed. Consolidated and component intervals never coexist.
    for venue in ("CONSOLIDATED", "KRX", "NXT"):
        own = sorted((r for r in selected if r.venue == venue), key=lambda r: r.interval_start)
        if any(left.interval_end > right.interval_start for left, right in zip(own, own[1:])):
            return [], "OVERLAPPING_INTERVALS"
    return sorted(selected, key=lambda r: (r.interval_start, r.interval_end, r.venue)), None


def _covers(rows: list[FlowObservation], start: datetime, end: datetime) -> bool:
    cursor = start
    for row in sorted(rows, key=lambda r: (r.interval_start, r.interval_end)):
        if row.interval_start > cursor:
            return False
        cursor = max(cursor, row.interval_end)
    return cursor >= end


def _historical_by_day(rows: list[HistoricalFlowObservation], current_venues: set[str]) -> dict:
    latest = {}
    for row in rows:
        key = (row.session_date, row.venue)
        if key not in latest or (row.observed_at, row.first_seen_at, row.provider) > (
                latest[key].observed_at, latest[key].first_seen_at, latest[key].provider):
            latest[key] = row
    by_day = defaultdict(list)
    for row in latest.values():
        by_day[row.session_date].append(row)
    result = {}
    for day, entries in by_day.items():
        venues = {row.venue for row in entries}
        if current_venues in ({"CONSOLIDATED"}, {"KRX", "NXT"}):
            if "CONSOLIDATED" in venues:
                result[day] = sum(row.turnover_krw for row in entries if row.venue == "CONSOLIDATED")
            elif {"KRX", "NXT"} <= venues:
                result[day] = sum(row.turnover_krw for row in entries)
        elif current_venues in ({"KRX"}, {"NXT"}) and current_venues <= venues:
            result[day] = sum(row.turnover_krw for row in entries if row.venue in current_venues)
    return result


def compute_theme_flows(
    observations: Iterable[FlowObservation | Mapping],
    relations: Iterable[ThemeRelation | Mapping],
    history: Iterable[HistoricalFlowObservation | Mapping],
    *, as_of: datetime, session_start: datetime, session_id: str,
    window_minutes: float = 5, config: dict | None = None, mode: str = "live",
    investor_netbuy: Iterable[InvestorNetBuyObservation | Mapping] = (),
) -> list[ThemeFlowSnapshot]:
    """Calculate explicitly scoped rolling-window observations.

    Historical ``minute_from_session`` is the END of the comparison window, not
    its start. A 09:05--09:10 regular-session window matches minute=10/window=5.
    At least ten distinct earlier comparable sessions are required by default.
    """
    clock, session_start = aware(as_of), aware(session_start)
    if mode not in ("live", "demo", "replay") or window_minutes <= 0:
        raise ValueError("Invalid radar mode or rolling window")
    if session_start > clock:
        raise ValueError("Session has not begun at this point-in-time")
    cfg = section(config)
    data = cfg.get("data", {})
    max_age = float(data.get("max_live_quote_age_seconds", 15))
    minimum_coverage = float(data.get("min_theme_coverage_ratio", .65))
    required_history = int(data.get("historical_same_time_sessions", 10))
    all_rows = _typed(observations, FlowObservation)
    all_relations = _typed(relations, ThemeRelation)
    all_history = _typed(history, HistoricalFlowObservation)
    netbuys = _typed(investor_netbuy, InvestorNetBuyObservation)
    active = {}
    for relation in all_relations:
        if relation.effective_from <= clock and relation.first_seen_at <= clock:
            key = (relation.theme_id, relation.symbol)
            if key not in active or (relation.effective_from, relation.first_seen_at) > (
                    active[key].effective_from, active[key].first_seen_at):
                active[key] = relation
    by_theme = defaultdict(list)
    per_symbol_weight = defaultdict(float)
    for relation in active.values():
        by_theme[relation.theme_id].append(relation)
        per_symbol_weight[relation.symbol] += relation.weight
    universe = set(per_symbol_weight)
    visible = [row for row in all_rows if _visible(row, clock, mode) and row.session_id == session_id
               and row.symbol in universe and row.interval_start >= session_start and row.interval_end <= clock
               and row.bar_status == "COMPLETE" and row.quote_at <= clock]
    window_end = max((row.interval_end for row in visible), default=clock)
    window_start = max(session_start, window_end - timedelta(minutes=window_minutes))
    duration = (window_end - window_start).total_seconds() / 60
    elapsed = (window_end - session_start).total_seconds() / 60
    turnover_age = (clock - window_end).total_seconds()
    stale_turnover = turnover_age > max_age
    by_symbol = defaultdict(list)
    errors = defaultdict(list)
    for row in visible:
        if row.interval_start >= window_start:
            by_symbol[row.symbol].append(row)
        elif row.interval_end > window_start:
            errors[row.symbol].append("PARTIAL_WINDOW")
    metrics = {}
    history_values = {}
    for symbol in universe:
        rows, error = _interval_rows(by_symbol.get(symbol, []))
        if error:
            errors[symbol].append(error)
            continue
        if not rows or duration <= 0:
            errors[symbol].append("MISSING_TURNOVER_OBSERVATION")
            continue
        if not _covers(rows, window_start, window_end):
            errors[symbol].append("INCOMPLETE_WINDOW")
            continue
        latest_quote = max(row.quote_at for row in rows)
        age = (clock - latest_quote).total_seconds()
        if age > max_age:
            errors[symbol].append("STALE_QUOTE")
            continue
        turnover = sum(row.turnover_krw for row in rows)
        first = min(rows, key=lambda row: (row.interval_start, row.observed_at))
        last = max(rows, key=lambda row: (row.interval_end, row.observed_at))
        momentum = (last.price_close / first.price_open - 1) * 100 if first.price_open and last.price_close else None
        comparable = [row for row in all_history if row.symbol == symbol and row.session_id == session_id
                      and _visible(row, clock, mode) and row.session_date < session_start.date()
                      and abs(row.minute_from_session - elapsed) < 1e-6
                      and abs(row.window_minutes - duration) < 1e-6]
        historical = _historical_by_day(comparable, {row.venue for row in rows})
        history_values[symbol] = historical
        baseline = mean(historical.values()) if len(historical) >= required_history else None
        rvol = turnover / baseline if baseline is not None and baseline > 0 else None
        metrics[symbol] = {"symbol": symbol, "turnover_krw": turnover, "turnover_velocity_krw_per_min": turnover / duration,
                           "relative_turnover": rvol, "history_sessions": len(historical), "price_momentum_pct": momentum,
                           "quote_age_seconds": age, "quote_at": latest_quote.isoformat(),
                           "venues": sorted({row.venue for row in rows}), "providers": sorted({row.provider for row in rows}),
                           "source_urls": sorted({row.source_url for row in rows} | {row.source_url for row in comparable}),
                           "window_start": window_start.isoformat(), "window_end": window_end.isoformat()}
    universe_turnover = sum(row["turnover_krw"] for row in metrics.values())
    universe_ratio = len(metrics) / len(universe) if universe else 0
    snapshots = []
    for theme_id, theme_relations in sorted(by_theme.items()):
        expected = {r.symbol for r in theme_relations}
        seen = expected & metrics.keys()
        weights = {r.symbol: r.weight / max(1., per_symbol_weight[r.symbol]) for r in theme_relations}
        attributed = {symbol: metrics[symbol]["turnover_krw"] * weights[symbol] for symbol in seen}
        raw = sum(metrics[s]["turnover_krw"] for s in seen)
        turnover = sum(attributed.values())
        ratio = len(seen) / len(expected) if expected else 0
        common_days = set.intersection(*(set(history_values[symbol]) for symbol in seen)) if seen else set()
        baseline = mean(sum(history_values[symbol][day] * weights[symbol] for symbol in seen) for day in common_days) if len(common_days) >= required_history else None
        relative = turnover / baseline if baseline is not None and baseline > 0 else None
        priced = [metrics[s]["price_momentum_pct"] for s in seen if metrics[s]["price_momentum_pct"] is not None]
        breadth = sum(value > 0 for value in priced) / len(seen) if seen and len(priced) == len(seen) else None
        symbol_rvol = [metrics[s]["relative_turnover"] for s in seen]
        high_rvol_breadth = sum(value >= 2 for value in symbol_rvol) / len(seen) if seen and all(value is not None for value in symbol_rvol) else None
        concentration = max(attributed.values()) / turnover if turnover > 0 else None
        reasons = sorted({code for symbol in expected for code in errors[symbol]})
        if stale_turnover:
            reasons.append("STALE_TURNOVER")
        if ratio < minimum_coverage:
            reasons.append("DATA_COVERAGE_LOW")
        if universe_ratio < minimum_coverage:
            reasons.append("UNIVERSE_COVERAGE_LOW")
        if relative is None:
            reasons.append("INSUFFICIENT_HISTORY")
        if breadth is None and seen:
            reasons.append("MISSING_PRICE_DATA")
        single = len(seen) == 1 or (concentration is not None and concentration > .65)
        broad = len(seen) >= 3 and breadth is not None and breadth >= .65 and not single
        strength = "single-name strength" if single else "broad-based strength" if broad else "mixed" if seen else "unavailable"
        if single:
            reasons.append("SINGLE_NAME_STRENGTH")
        else:
            reasons.append("BROAD_BASED_STRENGTH" if broad else "MIXED_BREADTH")
        eligible = bool(seen and ratio >= minimum_coverage and universe_ratio >= minimum_coverage
                        and relative is not None and breadth is not None and not stale_turnover)
        quality = "STALE" if stale_turnover else "LIVE" if eligible else "STALE" if not seen and "STALE_QUOTE" in reasons else "PARTIAL" if seen else "UNAVAILABLE"
        if mode != "live" and seen:
            quality = "DEMO" if mode == "demo" else "REPLAY"
        providers = sorted({p for symbol in seen for p in metrics[symbol]["providers"]})
        sources = sorted({url for symbol in seen for url in metrics[symbol]["source_urls"]} | {r.source_url for r in theme_relations})
        # Net buying remains a separate observed dataset with original unit,
        # venue, provider and lag. Never multiply shares by the current price.
        visible_netbuys = [n for n in netbuys if n.symbol in seen and _visible(n, clock, mode)]
        latest_netbuys = {}
        for row in visible_netbuys:
            key = (row.symbol, row.venue, row.unit)
            if key not in latest_netbuys or row.observed_at > latest_netbuys[key].observed_at:
                latest_netbuys[key] = row
        groups = defaultdict(list)
        for row in latest_netbuys.values():
            if row.venue != "CONSOLIDATED" and (row.symbol, "CONSOLIDATED", row.unit) in latest_netbuys:
                continue
            groups[(row.venue, row.unit)].append(row)
        series = []
        for (venue, unit), entries in groups.items():
            categories = set(key for row in entries for key in row.values)
            values = {key: sum(row.values[key] * weights[row.symbol] for row in entries) if all(row.values.get(key) is not None for row in entries) else None for key in categories}
            series.append({"venue": venue, "unit": unit, "values": values, "as_of": min(row.observed_at for row in entries).isoformat(),
                           "quality": "STALE" if any(row.quality == "STALE" for row in entries) else "DELAYED" if any(row.quality == "DELAYED" for row in entries) else "LIVE",
                           "providers": sorted({row.provider for row in entries}), "source_urls": sorted({row.source_url for row in entries}),
                           "coverage": {"symbols_seen": len({row.symbol for row in entries}), "symbols_expected": len(expected),
                                        "ratio": len({row.symbol for row in entries}) / len(expected) if expected else 0},
                           "definition": "Net buying observed for this explicitly covered subset; not a full-theme total"})
        snapshots.append(ThemeFlowSnapshot(theme_id=theme_id, theme_name=next((r.theme_name for r in theme_relations if r.theme_name), theme_id),
            as_of=clock, session_id=session_id, window_minutes=duration, mode=mode, provider=providers, source_urls=sources,
            coverage={"symbols_seen": len(seen), "symbols_expected": len(expected), "ratio": ratio,
                      "venues": sorted({v for symbol in seen for v in metrics[symbol]["venues"]}),
                      "covered_universe_symbols": len(metrics), "expected_universe_symbols": len(universe),
                      "universe_ratio": universe_ratio, "universe_turnover_krw": universe_turnover},
            turnover_krw=turnover, raw_turnover_krw=raw, turnover_velocity_krw_per_min=turnover / duration if duration else 0,
            raw_turnover_velocity_krw_per_min=raw / duration if duration else 0, relative_turnover=relative, history_sessions=len(common_days),
            breadth_up_ratio=breadth, breadth_high_rvol_ratio=high_rvol_breadth, concentration_top1_ratio=concentration,
            price_momentum_5m=mean(priced) if priced else None, covered_universe_share=turnover / universe_turnover if universe_turnover > 0 else None,
            strength_type=strength, symbol_metrics=[{**metrics[s], "theme_attribution_weight": weights[s]} for s in sorted(seen)],
            confirmed_investor_netbuy={"series": series, "definition": "Observed investor net buying; separate units and venue scopes"} if series else None,
            confidence=ratio * (1 if relative is not None else .5), quality=quality,
            data_freshness={"as_of": clock.isoformat(), "window_start": window_start.isoformat(), "window_end": window_end.isoformat(),
                            "session_start": session_start.isoformat(), "session_elapsed_minutes": elapsed,
                            "turnover_age_seconds": turnover_age,
                            "max_quote_age_seconds": max((metrics[s]["quote_age_seconds"] for s in seen), default=None),
                            "freshness_limit_seconds": max_age}, reason_codes=sorted(set(reasons)), eligible_for_live_ranking=eligible))
    return snapshots
