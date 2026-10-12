"""Replay the production flow and rotation cores over invented point-in-time data.

Only each observation's available prefix is passed to those cores. This module
performs no provider calls, broker authentication, database writes, or orders.
Its metrics describe synthetic observation coverage and decisions, not actual
October news, performance, investor buying, or real market-wide accuracy.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from statistics import mean
from typing import Any

from app.radar.config import read_v12_config
from app.radar.flow import compute_theme_flows
from app.radar.models import aware
from app.radar.presentation import overview_envelope
from app.radar.rotation import OpeningFlowRotationEngine
from app.strategy.intraday import evaluate_early_entry, fixture_scenario
from .fixtures import SCENARIOS, SNAPSHOT_TIMES, clock, load_scenario


GENERAL_SYNTHETIC_DAYS = ('2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08', '2026-10-13')


def _time(value: datetime | str) -> datetime:
    return aware(datetime.fromisoformat(value) if isinstance(value, str) else value)


def _available_catalysts(events, at):
    return [deepcopy(item) for item in events if _time(item['published_at']) <= at and _time(item['first_seen_at']) <= at]


def _feed_health(snapshots, at, events):
    seen = sum(snapshot.coverage.symbols_seen for snapshot in snapshots)
    stale = any(snapshot.quality == 'STALE' for snapshot in snapshots)
    last_quotes = [row['quote_at'] for snapshot in snapshots for row in snapshot.symbol_metrics if row.get('quote_at')]
    return {
        'quotes': {'status': 'STALE' if stale and not seen else 'DEMO' if seen else 'UNAVAILABLE',
                   'provider': 'SYNTHETIC_FIXTURE', 'last_success_at': max(last_quotes) if last_quotes else None,
                   'latency_seconds': max((s.data_freshness.get('max_quote_age_seconds') or 0 for s in snapshots), default=None),
                   'coverage': 'Invented covered symbol universe; not the Korean market'},
        'news': {'status': 'DEMO', 'provider': 'SYNTHETIC_FIXTURE',
                 'last_success_at': max((item['first_seen_at'] for item in events), default=None),
                 'coverage': 'Invented policy-event shapes, no licensed news feed'},
        'disclosures': {'status': 'DEMO', 'provider': 'SYNTHETIC_FIXTURE',
                        'coverage': 'Invented tender-event shape, not an actual disclosure'},
        'investor_netbuy': {'status': 'UNAVAILABLE', 'provider': 'NONE', 'last_success_at': None,
                           'reason': 'Investor net buying is absent; turnover never replaces it'},
        'history': {'status': 'DEMO', 'provider': 'SYNTHETIC_FIXTURE',
                    'coverage': 'Ten earlier invented same-session, same-time windows; not real trading history'},
    }


def _unavailable(definition, reason):
    return {'status': 'UNAVAILABLE', 'value': None, 'definition': definition, 'reason': reason,
            'data_source': 'SYNTHETIC_FIXTURE', 'actual_market_data': False}


def _metrics(timeline, events, at):
    selections = {}
    for point in timeline:
        for item in point['live_opening_flow_top3']:
            for event_id in item.get('catalyst_event_ids', []):
                selections.setdefault(event_id, point['as_of'])
    latencies = []
    for event in events:
        if event['event_id'] in selections:
            chosen = _time(selections[event['event_id']])
            seen = _time(event['first_seen_at'])
            latencies.append({'event_id': event['event_id'], 'first_seen_at': seen.isoformat(),
                              'selected_at': chosen.isoformat(), 'seconds': (chosen - seen).total_seconds()})
    detection = {'status': 'DEMO', 'value': mean(item['seconds'] for item in latencies), 'unit': 'seconds',
                 'events': latencies, 'definition': 'System first_seen_at to first observed-leader selection; not original publication time'} if latencies else _unavailable(
                 'System first_seen_at to first observed-leader selection', 'NO_AVAILABLE_EVENT_SELECTED')
    point_10 = next((point for point in timeline if point['snapshot_time'] == '09:10'), None)
    if point_10:
        valid = [item for item in point_10['theme_flow_snapshots'] if item['eligible_for_live_ranking']
                 and item['data_freshness'].get('max_quote_age_seconds') is not None
                 and item['data_freshness']['max_quote_age_seconds'] <= 15]
        reference = sorted(valid, key=lambda item: (-item['raw_turnover_krw'], item['theme_id']))[:3]
        selected = {item['theme_id'] for item in point_10['live_opening_flow_top3']}
        recalled = selected & {item['theme_id'] for item in reference}
        recall = {'status': 'DEMO', 'value': len(recalled) / len(reference), 'as_of': point_10['as_of'],
                  'reference_theme_ids': [item['theme_id'] for item in reference], 'selected_theme_ids': sorted(selected),
                  'recalled_theme_ids': sorted(recalled),
                  'definition': 'Recall against the observed 09:05-09:10 raw-turnover Top 3 in the covered universe; never final-day gainers'} if reference else _unavailable(
                  'Observed same-time turnover Top 3 recall', 'NO_FRESH_09_10_REFERENCE')
    else:
        recall = _unavailable('Observed 09:05-09:10 turnover Top 3 recall', '09_10_HAS_NOT_OCCURRED_AT_REPLAY_CLOCK')
    selected_items = [item for point in timeline for item in point['live_opening_flow_top3']]
    broad = [item for item in selected_items if item['metrics'].get('strength_type') == 'broad-based strength'
             and (item['metrics'].get('breadth_up_ratio') or 0) >= .65]
    breadth = {'status': 'DEMO', 'value': len(broad) / len(selected_items), 'selected_observations': len(selected_items),
               'broad_observations': len(broad),
               'definition': 'Fraction of selected theme observations with measured >=65% rising participation; not investor-flow ground truth'} if selected_items else _unavailable(
               'Selected-leader observed breadth consistency', 'NO_OBSERVED_LEADERS')
    stability = []
    for left, right in zip(timeline, timeline[1:]):
        previous = {item['theme_id'] for item in left['live_opening_flow_top3']}
        current = {item['theme_id'] for item in right['live_opening_flow_top3']}
        if previous and current:
            stability.append({'from': left['as_of'], 'to': right['as_of'], 'value': len(previous & current) / len(previous | current)})
    stable_metric = {'status': 'DEMO', 'value': mean(pair['value'] for pair in stability), 'pairs': stability,
                     'definition': 'Mean set Jaccard stability of consecutive nonempty observed Top 3 snapshots'} if stability else _unavailable(
                     'Consecutive nonempty Top 3 set stability', 'INSUFFICIENT_SELECTED_SNAPSHOTS')
    held_minutes = 0.0
    for left, right in zip(timeline, timeline[1:]):
        hypothesis_ids = {item['theme_id'] for item in left['premarket_hypothesis_top3']}
        flows = {item['theme_id']: item for item in left['theme_flow_snapshots'] if item['eligible_for_live_ranking']}
        for leader in left['live_opening_flow_top3']:
            if leader['theme_id'] not in hypothesis_ids or leader['theme_id'] not in flows:
                continue
            incumbent = flows[leader['theme_id']]['turnover_velocity_krw_per_min']
            contradicted = any(item['theme_id'] != leader['theme_id'] and item['strength_type'] == 'broad-based strength'
                               and item['turnover_velocity_krw_per_min'] >= incumbent * 2 for item in flows.values())
            if contradicted:
                held_minutes += (_time(right['as_of']) - _time(left['as_of'])).total_seconds() / 60
    return {'as_of': at.isoformat(), 'data_source': 'SYNTHETIC_FIXTURE', 'actual_market_data': False,
            'DetectionLatency': detection, 'Top3TurnoverRecall@09:10': recall,
            'ThemeBreadthPrecision': breadth, 'Top3Stability': stable_metric,
            'IncorrectPremarketHoldMinutes': {'status': 'DEMO', 'value': held_minutes,
                'definition': 'Sampled minutes retaining a premarket leader while an available broad challenger has >=2x its velocity; multiple hypotheses count separately'},
            'EntryCandidatePrecision': _unavailable('Risk-qualified entry correctness', 'NO_SESSION_RISK_QUALIFIED_ENTRY_GROUND_TRUTH'),
            'MFE': _unavailable('Maximum favorable excursion after a risk-qualified entry', 'NO_VALID_ENTRY_AND_COMPLETED_FORWARD_WINDOW'),
            'MAE': _unavailable('Maximum adverse excursion after a risk-qualified entry', 'NO_VALID_ENTRY_AND_COMPLETED_FORWARD_WINDOW'),
            'entry_performance': {'status': 'UNAVAILABLE', 'trades': [], 'net_return_pct': None,
                'fees_pct_per_side': .015, 'slippage_pct_per_side': .05, 'tax_pct': None,
                'reason': 'These flow inputs lack independently observed spread/VI/structural entry levels and a supplied tax model. No outcome is invented.'}}


def _entry_regressions(at):
    traces = []
    for name in ('sk_innovation', 'isupetasys'):
        decisions = []
        for label in ('09:25:02', '09:29:02', '09:30:00', '09:30:02'):
            moment = _time(f'2026-10-08T{label}+09:00')
            if moment <= at:
                decision = evaluate_early_entry(fixture_scenario(name, as_of=moment))
                decisions.append(decision)
        traces.append({'scenario': name, 'status': 'DEMO' if decisions else 'UNAVAILABLE', 'decisions': decisions,
                       'data_source': 'SYNTHETIC_FIXTURE', 'actual_market_data': False,
                       'participates_in_opening_ranks': False,
                       'notice': 'Independent invented October 8 regression shape, never a real historical backtest or a KR01 flow stock'})
    return traces


def _entry_watch(snapshots, leaders, rows, at):
    """Show observation-only watches without synthesizing OHLC/volume or risk."""
    indexed = {snapshot.theme_id: snapshot for snapshot in snapshots}
    watches = []
    for leader in leaders:
        snapshot = indexed.get(leader.theme_id)
        if snapshot is None or not snapshot.symbol_metrics:
            continue
        measured = max(snapshot.symbol_metrics, key=lambda row: row['turnover_velocity_krw_per_min'])
        symbol_rows = [row for row in rows if row.symbol == measured['symbol'] and row.first_seen_at <= at
                       and row.observed_at <= at and row.interval_end <= at and row.quote_at <= at]
        if not symbol_rows:
            continue
        latest = max(symbol_rows, key=lambda row: row.interval_end)
        watches.append({'symbol': latest.symbol, 'theme_id': leader.theme_id, 'state': 'WATCH_ONLY',
                        'quality': 'UNAVAILABLE', 'mode': 'demo', 'source': 'SYNTHETIC_FIXTURE',
                        'as_of': at.isoformat(), 'first_seen_at': latest.first_seen_at.isoformat(),
                        'quote_at': latest.quote_at.isoformat(), 'price': latest.price_close, 'eligible': False,
                        'reason_codes': ['STRUCTURAL_ENTRY_LEVELS_UNAVAILABLE', 'SPREAD_VI_HALT_OBSERVATIONS_UNAVAILABLE'],
                        'explanation': '관측 강도만 확인했습니다. 거래대금으로 봉 거래량·스프레드·위험 구조를 만들어 진입을 승인하지 않습니다.'})
    return watches


def replay_session(session='2026-10-12', snapshot_time='09:10', scenario='domestic_rotation', as_of=None) -> dict[str, Any]:
    """Return the approved overview shape plus a prefix-only timeline and metrics."""
    if snapshot_time not in SNAPSHOT_TIMES:
        raise ValueError('Unsupported opening snapshot time')
    fixture = load_scenario(session, scenario)
    target = _time(as_of) if as_of is not None else clock(session, snapshot_time)
    if target.date().isoformat() != session:
        raise ValueError('Replay clock must belong to the requested synthetic session')
    config = read_v12_config()
    config.setdefault('context', {})['last_trading_close'] = fixture['last_trading_close'].isoformat()
    engine = OpeningFlowRotationEngine(config)
    times = [clock(session, label) for label in SNAPSHOT_TIMES if clock(session, label) <= target]
    if not times or times[-1] != target:
        times.append(target)
    timeline, transitions = [], []
    final_result, final_snapshots, visible_events = None, [], []
    for moment in times:
        visible_rows = [row for row in fixture['observations'] if row.interval_end <= moment and row.observed_at <= moment
                        and row.first_seen_at <= moment]
        visible_relations = [row for row in fixture['relations'] if row.first_seen_at <= moment and row.effective_from <= moment]
        visible_history = [row for row in fixture['history'] if row.observed_at <= moment and row.first_seen_at <= moment
                           and row.session_date < moment.date()]
        visible_events = _available_catalysts(fixture['catalysts'], moment)
        snapshots = compute_theme_flows(visible_rows, visible_relations, visible_history, as_of=moment,
                       session_start=fixture['start'], session_id='KRX_REGULAR', window_minutes=5,
                       config=config, mode='replay') if moment >= fixture['start'] else []
        # Strong observed flow alone is not an entry. These inputs intentionally
        # omit verified spread, VI/halts and daily structural setup observations.
        result = engine.evaluate(snapshots, as_of=moment, hypotheses=fixture['hypotheses'],
                                 entry_candidates=(), catalysts=visible_events, mode='replay')
        data = result.model_dump(mode='json')
        transitions.extend(data['transitions'])
        timeline.append({'snapshot_time': moment.strftime('%H:%M'), 'as_of': moment.isoformat(),
                         'mode': 'demo', 'data_source': 'SYNTHETIC_FIXTURE', 'actual_market_data': False,
                         'quality': result.quality, 'premarket_hypothesis_top3': data['premarket_hypothesis_top3'],
                         'live_opening_flow_top3': data['live_opening_flow_top3'], 'entry_ready_top3': [],
                         'catalyst_event_ids': [item['event_id'] for item in visible_events],
                         'theme_flow_snapshots': [item.model_dump(mode='json') for item in snapshots],
                         'transitions': data['transitions'], 'notifications': data['notifications']})
        final_result, final_snapshots = result, snapshots
    freshness = _feed_health(final_snapshots, target, visible_events)
    payload = overview_envelope(final_result, final_snapshots, freshness=freshness, catalysts=visible_events,
                history=transitions, session_label='합성 KRX 정규장 시나리오 · 실제 거래일/시세 아님',
                replay={'available': True, 'session': session, 'snapshot_time': target.strftime('%H:%M'),
                        'snapshot_times': list(SNAPSHOT_TIMES), 'scenario': scenario, 'scenarios': list(SCENARIOS),
                        'general_synthetic_days': list(GENERAL_SYNTHETIC_DAYS), 'actual_market_data': False})
    payload.update(timeline=timeline, metrics=_metrics(timeline, visible_events, target), actual_market_data=False,
                   entry_regressions=_entry_regressions(target),
                   entry_watch=_entry_watch(final_snapshots, final_result.live_opening_flow_top3, fixture['observations'], target))
    payload['entry_ready_top3'].update(status='UNAVAILABLE', explanation_codes=['SESSION_ENTRY_RISK_OBSERVATIONS_UNAVAILABLE'],
                                     reason_codes=['SESSION_ENTRY_RISK_OBSERVATIONS_UNAVAILABLE'])
    if freshness['quotes']['status'] in ('STALE', 'UNAVAILABLE'):
        payload['live_opening_flow_top3']['status'] = freshness['quotes']['status']
    return payload
