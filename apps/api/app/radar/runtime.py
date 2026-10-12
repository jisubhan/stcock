"""Current v1.2 services. No demo observations enter the current-live path."""
from __future__ import annotations

import asyncio
from datetime import datetime
import json
from statistics import mean
from threading import RLock

from app.settings import read_themes, runtime_dir
from app.radar.config import read_v12_config
from app.radar.models import KST, ThemeRelation
from app.radar.flow import compute_theme_flows
from app.radar.rotation import OpeningFlowRotationEngine
from app.radar.presentation import overview_envelope
from app.radar.sessions import calendar_sessions, current_session, previous_close
from app.radar.storage import (connection, encoded, identity, read_observations, read_provider_health,
    read_relations, read_state, rotation_history, save_relations, save_rotation, save_state, save_catalyst_notifications)

_service = None
_service_key = None
_lock = RLock()


def catalyst_service():
    global _service, _service_key
    key = str(runtime_dir())
    if _service is None or _service_key != key:
        from app.catalysts.service import build_service
        from app.fixtures import STOCK_SEEDS
        _service = build_service(themes=read_themes(), aliases={row[0]: [row[1]] for row in STOCK_SEEDS})
        _service_key = key
    return _service


def current_catalysts(as_of=None, since=None, verified=None, last_trading_close=None, *, current=True):
    values = catalyst_service().list_events(as_of=as_of, since=since, verified=verified,
        last_trading_close=last_trading_close, mode='live')
    items = [item.model_dump(mode='json') if hasattr(item, 'model_dump') else item for item in values]
    if current:
        health = {item['provider']: item for item in catalyst_service().health()}
        for item in items:
            status = health.get(item['provider'], {}).get('status')
            if status in ('UNAVAILABLE', 'STALE'):
                item['coverage_status'] = status
    return items


def configured_relations(as_of, events, *, historical=False):
    existing = read_relations(as_of)
    # Use the graph available at this instant, including removal revisions.
    # An official industry membership does not assert a news beneficiary.
    from app.discovery.storage import discovery_snapshot
    discovery = discovery_snapshot(as_of)
    sector_names = {str(row['sectorId']): row.get('name') for row in discovery.get('sectors', [])}
    official = discovery.get('memberships', [])
    official_keys = {('sector:' + str(row['sector_id']), row['symbol'], row['source_url']) for row in official}
    if historical:
        # A historical query never imports today's configuration or writes an
        # invented past first_seen_at. Only contemporaneously recorded maps exist.
        config = read_state('theme_config', as_of) or []
        allowed = {(theme['id'], symbol) for theme in config for symbol in theme['memberSymbols']}
        event_keys = {(theme_id, symbol, event['source_url']) for event in events if event.get('status') != 'RETRACTED'
                      for theme_id in event.get('theme_ids', []) for symbol in event.get('tickers', [])}
        return [item for item in existing if (item.provider == 'configured_theme_map' and (item.theme_id, item.symbol) in allowed)
                or (item.provider == 'toss_official_sector' and (item.theme_id, item.symbol, item.source_url) in official_keys)
                or (item.provider not in ('configured_theme_map', 'toss_official_sector')
                    and (item.theme_id, item.symbol, item.source_url) in event_keys)]
    themes = read_themes()
    if read_state('theme_config') != themes:
        save_state('theme_config', themes, as_of)
    desired = []
    for theme in themes:
        for symbol in theme['memberSymbols']:
            desired.append(dict(theme_id=theme['id'], theme_name=theme['name'], symbol=symbol, weight=1.,
                relationship='THEMATIC_ASSOCIATION', source_url='https://github.com/jisubhan/stcock/blob/main/config/themes.yml',
                provider='configured_theme_map', effective_from=as_of, first_seen_at=as_of))
    for row in official:
        sector_id = str(row['sector_id'])
        if sector_id not in sector_names:
            continue
        available = datetime.fromisoformat(row['first_seen_at'])
        desired.append(dict(theme_id='sector:' + sector_id, theme_name=sector_names[sector_id], symbol=row['symbol'],
            weight=1., relationship='OFFICIAL_SECTOR_MEMBER', source_url=row['source_url'],
            provider='toss_official_sector', effective_from=available, first_seen_at=available))
    for event in events:
        if event.get('status') == 'RETRACTED':
            continue
        confidence = min(1., max(.05, float(event.get('confidence', 0))))
        relation = event.get('relationship', 'UNVERIFIED')
        available = max(datetime.fromisoformat(event['first_seen_at']),
                        datetime.fromisoformat(event.get('revision_available_at', event['first_seen_at'])))
        for theme_id in event.get('theme_ids', []):
            for symbol in event.get('tickers', []):
                scoped = next((row for row in event.get('ticker_relations', []) if row.get('symbol') == symbol), None)
                symbol_relation = scoped.get('relationship', 'UNVERIFIED') if scoped else relation
                symbol_confidence = min(confidence, float(scoped.get('confidence', 0))) if scoped else confidence
                symbol_factor = {'DIRECT_VERIFIED': 1., 'NEWS_REPORTED': .7, 'THEMATIC_ASSOCIATION': .25, 'UNVERIFIED': .1}.get(symbol_relation, .1)
                desired.append(dict(theme_id=theme_id, theme_name=next((t['name'] for t in themes if t['id'] == theme_id), theme_id),
                    symbol=symbol, weight=max(.05, symbol_factor * symbol_confidence), relationship=symbol_relation,
                    source_url=event['source_url'], provider=event['provider'],
                    effective_from=available, first_seen_at=available))
    selected, added = [], []
    indexed = {}
    for item in existing:
        key = (item.theme_id, item.symbol, item.source_url)
        old = indexed.get(key)
        if old is None or (item.effective_from, item.first_seen_at) > (old.effective_from, old.first_seen_at):
            indexed[key] = item
    for row in desired:
        old = indexed.get((row['theme_id'], row['symbol'], row['source_url']))
        same = old and old.theme_name == row['theme_name'] and old.weight == row['weight'] and old.relationship == row['relationship']
        item = old if same else ThemeRelation(**row)
        selected.append(item)
        if not same:
            added.append(item)
    if added:
        save_relations(added)
    return selected


def on_verified_calendar(calendar, as_of):
    stamp = as_of.astimezone(KST)
    if calendar.get('today', {}).get('date') != stamp.date().isoformat():
        raise ValueError('The official calendar does not match the observed day.')
    save_state('calendar:' + stamp.date().isoformat(), calendar, stamp)


def on_verified_bundle(data, calendar):
    """Preserve official sessions and freeze pre-open hypotheses when known."""
    stamp = datetime.fromisoformat(data['asOf']).astimezone(KST)
    on_verified_calendar(calendar, stamp)
    regular = next((item for item in calendar_sessions(calendar) if item.session_id == 'KRX_REGULAR'), None)
    if regular is None or stamp >= regular.start:
        return
    hypotheses = []
    for theme in read_themes():
        values = [data['usReturns'][ticker] for ticker in theme['usTickers'] if ticker in data['usReturns']]
        if len(values) != len(theme['usTickers']) or not values:
            continue
        hypotheses.append({'theme_id': theme['id'], 'theme_name': theme['name'],
            'score': max(0., min(100., 50 + mean(values) * 10)), 'as_of': stamp.isoformat(),
            'first_seen_at': stamp.isoformat(), 'mode': 'live',
            'metrics': {'us_completed_session_return_pct': mean(values)},
            'source_urls': ['https://openapi.tossinvest.com/api/v1/candles'],
            'explanation': '검증된 미국 완료 정규장 종가 수익률의 사전 가설입니다. 개장 후 거래대금 순위와 다릅니다.'})
    hypotheses.sort(key=lambda row: -row['score'])
    key = 'hypotheses:' + stamp.date().isoformat()
    if read_state(key) is None:
        save_state(key, hypotheses[:3], stamp)


def _feed(status, provider, reason, **extras):
    return {'status': status, 'provider': provider, 'reason': reason,
            'last_success_at': None, 'latency_seconds': None, 'coverage': None, **extras}


def rotation_entry_candidate(decision, theme_id, mode='live'):
    """Bridge known risk observations; absence is never converted to safe False."""
    from app.strategy.intraday import EarlyEntryConfig
    config = EarlyEntryConfig()
    gates = decision.get('gates', {})
    spread, velocity = gates.get('spread_pct'), gates.get('turnover_krw_per_min')
    return {**decision, 'theme_id': theme_id, 'mode': mode,
            'trading_halted': gates.get('suspended'), 'vi_active': gates.get('vi_active'),
            'extreme_spread': spread > config.max_spread_pct if isinstance(spread, (int, float)) and not isinstance(spread, bool) else None,
            'liquidity_ok': velocity >= config.min_turnover_krw_per_min if isinstance(velocity, (int, float)) and not isinstance(velocity, bool) else None}


def feed_health(as_of=None, *, historical=False):
    now = (as_of or datetime.now(KST)).astimezone(KST)
    values = catalyst_service().health(now=now)
    if isinstance(values, dict):
        values = values.get('items', values.get('providers', list(values.values())))
    providers = [item.model_dump(mode='json') if hasattr(item, 'model_dump') else item for item in values]
    if historical:
        # Current adapter health is not a time series. Never project a later
        # successful poll backwards; event revisions retain their own provenance.
        providers = [{**item, 'status': 'UNAVAILABLE', 'last_success_at': None, 'latency_seconds': None,
                      'reason': 'Historical provider health observations are not available.'} for item in providers]
    sources = {item['provider']: item for item in providers}
    def grouped(kinds, label):
        selected = [item for item in providers if any(kind in str(item.get('provider', '')).upper() for kind in kinds)]
        available = [item for item in selected if item.get('status') != 'UNAVAILABLE']
        if not available:
            return _feed('UNAVAILABLE', label, '연결된 검증 소스가 없습니다. 미탐지를 재료 부재로 해석하지 않습니다.', sources=selected)
        rank = {'LIVE': 0, 'DELAYED': 1, 'PARTIAL': 2, 'STALE': 3, 'UNAVAILABLE': 4}
        worst = max(available, key=lambda item: rank.get(item.get('status'), 4))
        return {**worst, 'sources': selected}
    turnover = {} if historical else read_provider_health().get('TOSS_TURNOVER', {})
    quote = _feed('UNAVAILABLE', 'Toss REST', '검증된 현재가·거래대금 관측이 없습니다.')
    stamp = turnover.get('last_success_at')
    if stamp:
        age = (now - datetime.fromisoformat(stamp)).total_seconds()
        limit = read_v12_config()['data']['max_live_quote_age_seconds']
        status = 'LIVE' if 0 <= age <= limit else 'DELAYED' if 0 <= age <= 60 else 'STALE'
        provider_status = turnover.get('status', 'UNAVAILABLE')
        if provider_status != 'LIVE' and status == 'LIVE':
            status = provider_status
        quote = _feed(status, 'Toss REST · 통합 시세', '표시된 관측 범위의 공식 공급자 시각 기준입니다.',
                      last_success_at=stamp, latency_seconds=max(0, age), coverage=turnover.get('coverage'))
    if not historical:
        import os
        from app.market.ingestion import safe_json
        blocked = safe_json('toss-probe.json').get('authentication', {}).get('condition') in (
            'ip_not_allowed', 'invalid_client', 'access_denied', 'authentication_rejected')
        if blocked or os.environ.get('TOSS_DATA_MODE', 'auto').lower() == 'demo':
            quote = _feed('UNAVAILABLE', 'Toss REST', '실제 수집이 비활성화됐거나 인증 거부가 기록돼 있습니다.')
    from app.radar.quotes import quotes_snapshot
    prices = quotes_snapshot(now)
    visible_prices = prices.get('items', [])
    prices_health = _feed(prices['status'], prices['provider'], prices['reason'],
        last_success_at=max((item['quote_at'] for item in visible_prices if item.get('quote_at')), default=None),
        latency_seconds=min((item['latency_seconds'] for item in visible_prices
                             if isinstance(item.get('latency_seconds'), (int, float))), default=None),
        coverage={'symbols_seen': sum(item.get('status') == 'LIVE' for item in visible_prices),
                  'symbols_observed': len(visible_prices), 'scope': 'Queried symbols; not the entire market'})
    if historical:
        prices_health['status'] = 'STALE' if visible_prices else 'UNAVAILABLE'
        prices_health['reason'] = '과거 시점의 실제 가격 조회이며 현재 시세로 사용할 수 없습니다.'
    return {'prices': prices_health, 'quotes': quote, 'news': grouped(('NEWS', 'PRESS'), '한국 뉴스·정책 발표'),
            'disclosures': grouped(('DART', 'KIND'), 'DART/KIND 공시'),
            'investor_netbuy': _feed('UNAVAILABLE', 'Toss 투자자·프로그램 수급',
                '검증된 일별 순매수와 거래대금을 분리합니다. v1.2 장중 시각·지연이 확인되지 않아 사용하지 않습니다.'),
            'history': _feed('UNAVAILABLE', '저장된 동일 세션 거래대금', '동일 시간대 관측 세션 10개가 필요합니다.'),
            'providers': sources}


def current_overview(as_of=None, window_minutes=5, *, persist=True):
    with _lock:
        now = (as_of or datetime.now(KST)).astimezone(KST)
        historical = as_of is not None and not persist
        config = read_v12_config()
        calendar = read_state('calendar:' + now.date().isoformat(), now if historical else None) or {}
        session = current_session(calendar, now) if calendar else None
        close = previous_close(calendar) if calendar else None
        if close:
            config['context'] = {'last_trading_close': close.isoformat()}
        events = current_catalysts(now, last_trading_close=close, current=not historical)
        relations = configured_relations(now, events, historical=historical)
        snapshots, entries = [], []
        state_key = f'rotation:live:{now.date()}:{session.session_id if session else "CLOSED"}'
        engine = OpeningFlowRotationEngine(config)
        state = None if window_minutes != 5 else read_state(state_key, now if historical else None)
        if state and (state.get('last_as_of') is None or datetime.fromisoformat(state['last_as_of']) <= now):
            engine.restore_state(state)
        elif state:
            persist = False  # A past query cannot overwrite present engine state.
        from app.market.ingestion import safe_json
        import os
        auth_blocked = safe_json('toss-probe.json').get('authentication', {}).get('condition') in (
            'ip_not_allowed', 'invalid_client', 'access_denied', 'authentication_rejected')
        provider_status = read_provider_health().get('TOSS_TURNOVER', {}).get('status', 'UNAVAILABLE')
        collection_allowed = historical or (os.environ.get('TOSS_DATA_MODE', 'auto').lower() != 'demo' and not auth_blocked
                                           and provider_status in ('LIVE', 'PARTIAL'))
        if session and config['feature_flags']['opening_flow_rotation'] and collection_allowed:
            observations = read_observations(now, session.session_id)
            end = max((row.interval_end for row in observations), default=now)
            elapsed = (end - session.start).total_seconds() / 60
            from app.radar.collector import historical_windows
            past = read_observations(now, session.session_id, previous=True)
            history = historical_windows(past, now, session.session_id, elapsed, min(window_minutes, elapsed)) if elapsed > 0 else []
            snapshots = compute_theme_flows(observations, relations, history, as_of=now, session_start=session.start,
                session_id=session.session_id, window_minutes=window_minutes, config=config, mode='live')
        hypotheses = read_state('hypotheses:' + now.date().isoformat(), now if historical else None) or []
        result = engine.evaluate(snapshots, as_of=now, hypotheses=hypotheses, entry_candidates=entries,
                                 catalysts=events, mode='live')
        if session and collection_allowed and not historical:
            from app.market.ingestion import analysis_snapshot
            from app.strategy.intraday import evaluate_legacy_stock_entry
            context = analysis_snapshot()
            if context.meta['mode'] == 'live':
                lookup = {item['symbol']: item for item in context.stocks}
                confirmed = {item.theme_id for item in result.ranked_themes if item.state == 'LIVE_THEME_CONFIRMED'}
                for snapshot in snapshots:
                    if snapshot.theme_id not in confirmed:
                        continue
                    metric_lookup = {item['symbol']: item for item in snapshot.symbol_metrics}
                    for raw in context.samples:
                        if raw['symbol'] not in lookup or raw['symbol'] not in metric_lookup:
                            continue
                        # Theme confirmation and liquidity are measured here.
                        # Spread, VI, halts and price-limit facts remain unknown
                        # unless actually supplied by a verified risk source.
                        candidate_raw = {**raw, 'entry_gates': {**(raw.get('entry_gates') or {}),
                            'theme_confirmed': True,
                            'turnover_krw_per_min': metric_lookup[raw['symbol']]['turnover_velocity_krw_per_min']}}
                        decision = evaluate_legacy_stock_entry(candidate_raw, lookup[raw['symbol']], now,
                            calendar_sessions(calendar), 'live')['earlyEntry']
                        entries.append(rotation_entry_candidate(decision, snapshot.theme_id))
                result.entry_ready_top3 = engine.select_entry_candidates(entries, now)
        if persist:
            save_rotation(result, snapshots, engine.state_dict(), state_key)
        freshness = feed_health(now, historical=historical)
        if snapshots:
            count = min((item.history_sessions for item in snapshots), default=0)
            if count >= config['data']['historical_same_time_sessions']:
                freshness['history'] = _feed('LIVE', '동일 세션 저장 관측', '미래 자료를 제외한 동일 시간대 비교입니다.',
                                            coverage={'sessions': count}, last_success_at=now.isoformat())
        payload = overview_envelope(result, snapshots, freshness=freshness, catalysts=events,
            history=rotation_history(now.date().isoformat(), as_of=now),
            session_label=session.session_id if session else '장 일정 미확인 / 장 종료')
        payload['entry_watch_candidates'] = entries
        if not snapshots:
            expected = len({item.symbol for item in relations})
            payload['coverage'].update(symbols_expected=expected, expected_universe_symbols=expected,
                                       covered_universe_symbols=0, universe_ratio=0.)
        payload['market_lists'] = {'turnover_definition': '누적 거래대금 순위와 상승률 순위는 별개입니다.'}
        if historical:
            payload['historical'] = True
            payload['recorded_quality'] = payload['quality']
            payload['quality'] = 'STALE'
            payload['notice'] = '과거에 알려진 자료의 조회입니다. 현재 실시간 추천으로 사용할 수 없습니다.'
            for key in ('premarket_hypothesis_top3', 'live_opening_flow_top3', 'entry_ready_top3'):
                if payload[key]['items']:
                    payload[key]['status'] = 'STALE'
                payload[key]['explanation_codes'].append('HISTORICAL_SNAPSHOT_NOT_CURRENT_RECOMMENDATION')
        return payload


class CatalystWorker:
    def __init__(self):
        self.task = None

    async def start(self):
        if read_v12_config()['feature_flags']['domestic_catalyst_radar']:
            self.task = asyncio.create_task(self._run())

    async def _run(self):
        while True:
            now = datetime.now(KST)
            calendar = read_state('calendar:' + now.date().isoformat()) or {}
            try:
                await catalyst_service().poll(last_trading_close=previous_close(calendar) if calendar else None)
                save_catalyst_notifications(current_catalysts(datetime.now(KST)), now)
            except asyncio.CancelledError:
                raise
            except Exception:
                # The provider boundary reports fixed health codes. Do not log
                # arbitrary responses, secrets or full copyrighted news bodies.
                pass
            await asyncio.sleep(read_v12_config()['refresh']['news_poll_seconds'])

    async def close(self):
        global _service, _service_key
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        service = catalyst_service()
        if hasattr(service, 'aclose'):
            await service.aclose()
        _service, _service_key = None, None
