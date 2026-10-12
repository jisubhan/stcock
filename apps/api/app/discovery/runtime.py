"""Market discovery read models, using actual stored observations only."""
from datetime import datetime
import math

from app.discovery.analysis import analyze_discovery_stock
from app.discovery.storage import discovery_snapshot, enqueue_analysis, read_analysis_inputs
from app.discovery.themes import build_theme_hypotheses, _global_rankings
from app.radar.models import KST
from app.radar.sessions import calendar_sessions, current_session
from app.radar.storage import read_observations, read_state, save_state
from app.settings import read_strategy


def _now(as_of=None):
    return (as_of or datetime.now(KST)).astimezone(KST)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _context(now, *, historical=False):
    snapshot = discovery_snapshot(now)
    calendar = snapshot.get('calendar', {}).get('KR') or {}
    session = current_session(calendar, now) if calendar else None
    snapshot['flow_observations'] = {}
    if session:
        for item in read_observations(now, session.session_id):
            snapshot['flow_observations'].setdefault(item.symbol, []).append(item)
    from app.radar.runtime import current_catalysts
    snapshot['catalysts'] = current_catalysts(now, current=not historical)
    return snapshot


def _public_theme(theme):
    # The catalog and paginated stock endpoint own the full membership list.
    return {key: value for key, value in theme.items() if key not in ('members', 'official_member_symbols')}


def _remember_scores(snapshot, built, now):
    themes = built.get('themes', [])
    compact = [{key: theme.get(key) for key in ('id', 'name', 'score', 'quality', 'factor_coverage',
                'evidence', 'flow_confirmed', 'first_seen_at')} for theme in themes]
    key = 'discovery:theme_scores:' + now.date().isoformat()
    if read_state(key) != compact:
        save_state(key, compact, now)
    calendar = snapshot.get('calendar', {}).get('KR') or {}
    regular = next((session for session in calendar_sessions(calendar) if session.session_id == 'KRX_REGULAR'), None)
    if regular and now < regular.start:
        selected = built.get('hypotheses_top3', [])
        hypotheses = [{'theme_id': theme['id'], 'theme_name': theme['name'], 'score': theme['score'],
            'as_of': now.isoformat(), 'first_seen_at': now.isoformat(), 'mode': 'live',
            'metrics': {'factor_coverage': theme.get('factor_coverage'), 'prediction_kind': 'RULE_BASED_WATCH_PRIORITY'},
            'source_urls': list(dict.fromkeys(item['source_url'] for item in theme.get('evidence', []) if item.get('source_url'))),
            'explanation': '공식 산업분류·미국/국내 관측 기반 사전 관찰 가설입니다. 순매수 또는 수익 확률이 아닙니다.'}
            for theme in selected]
        if hypotheses:
            # Keep the most recent pre-open hypothesis, then preserve it after open.
            save_state('hypotheses:' + now.date().isoformat(), hypotheses, now)


def refresh_discovery_predictions(as_of=None):
    """A bounded worker can update hypotheses without a browser being open."""
    now = _now(as_of)
    snapshot = _context(now)
    built = build_theme_hypotheses(snapshot, now)
    _remember_scores(snapshot, built, now)
    return built


def discovery_overview(as_of=None):
    now = _now(as_of)
    snapshot = _context(now, historical=as_of is not None)
    built = build_theme_hypotheses(snapshot, now)
    if as_of is None:
        _remember_scores(snapshot, built, now)
    universe = snapshot['universe']
    status = ('PARTIAL' if universe.get('quote_fresh') else 'STALE' if universe.get('observed')
              else 'PARTIAL' if universe.get('expected') else 'UNAVAILABLE')
    from app.radar.runtime import feed_health
    freshness = feed_health(now, historical=as_of is not None)
    return {'as_of': now.isoformat(), 'source': 'toss', 'status': status, 'universe': universe,
        'theme_hypotheses_top3': [_public_theme(theme) for theme in built.get('hypotheses_top3', [])],
        'observed_theme_leaders': [_public_theme(theme) for theme in built.get('observed_turnover_leaders', [])],
        'sector_performance_observations': [_public_theme(theme) for theme in built.get('observed_leaders', [])],
        'themes': [_public_theme(theme) for theme in built.get('themes', [])],
        'unmapped_leaders': built.get('unmapped_leaders', []), 'reason_codes': built.get('reason_codes', []),
        'mapping_pending_themes': [_public_theme(theme) for theme in built.get('mapping_pending_themes', [])],
        'us_session_context': built.get('us_session_context'), 'scanner_health': snapshot.get('health', {}),
        'news_status': freshness['news']['status'], 'disclosures_status': freshness['disclosures']['status'],
        'measurement_definition': snapshot['measurement_definition'], 'score_definition': built.get('score_definition'),
        'orders_enabled': False, 'notice': '예상 테마와 종목은 근거 기반 관찰 우선순위입니다. 실제 순매수·진입 확정과 구분합니다.'}


def _theme_context(snapshot, stock, theme_id, now):
    sectors = {str(row['sectorId']): row for row in snapshot.get('sectors', [])}
    choices = stock.get('sector_ids', [])
    selected = theme_id.removeprefix('sector:') if theme_id else next(iter(choices), None)
    if selected not in choices or selected not in sectors:
        return None
    sector = sectors[selected]
    saved = next((row for row in read_state('discovery:theme_scores:' + now.date().isoformat(), now) or []
                  if row['id'] == 'sector:' + selected), {})
    relation = next((row for row in snapshot.get('memberships', [])
                     if str(row['sector_id']) == selected and row['symbol'] == stock['symbol']), {})
    return {'id': 'sector:' + selected, 'name': sector['name'], 'hypothesis_score': saved.get('score'), 'source': 'toss',
        'association_kind': 'OFFICIAL_SECTOR_MEMBER', 'observed_flow_confirmed': False,
        'first_seen_at': relation.get('first_seen_at'), 'evidence': saved.get('evidence') or [{
            'kind': 'OFFICIAL_SECTOR_CLASSIFICATION', 'source_url': relation.get('source_url'),
            'first_seen_at': relation.get('first_seen_at'), 'relationship': 'OFFICIAL_SECTOR_MEMBER'}]}


def _analyze(snapshot, stock, now, theme_id=None):
    inputs = snapshot.get('technical', {}).get(stock['symbol']) or {}
    calendar = snapshot.get('calendar', {}).get('KR') or {}
    return analyze_discovery_stock(symbol=stock['symbol'], name=stock.get('name') or stock['symbol'], as_of=now,
        quote=stock.get('quote'), daily_candles=inputs.get('daily', []), minute_candles=inputs.get('minute', []),
        daily_first_seen_at=inputs.get('daily_first_seen_at'), minute_first_seen_at=inputs.get('minute_first_seen_at'),
        sessions=calendar_sessions(calendar, include_previous=True), previous_business_day=calendar.get('previousBusinessDay', {}).get('date'),
        theme_context=_theme_context(snapshot, stock, theme_id, now),
        flow_observations=snapshot.get('flow_observations', {}).get(stock['symbol'], []),
        config=read_strategy(), investor_netbuy=None, entry_gates=None)


def discovery_stocks(*, page=1, limit=25, query='', theme_id=None, unclassified=False, as_of=None):
    now = _now(as_of)
    snapshot = _context(now, historical=as_of is not None)
    forecast = build_theme_hypotheses(snapshot, now)
    if as_of is None:
        _remember_scores(snapshot, forecast, now)
    rows, recommendations = [], {}
    for theme in forecast.get('themes', []):
        for member in theme.get('members', []):
            if theme_id is None or theme['id'] == theme_id:
                old = recommendations.get(member['symbol'])
                score = (member.get('watch_score') or {}).get('score')
                if old is None or (score or -1) > ((old.get('watch_score') or {}).get('score') or -1):
                    recommendations[member['symbol']] = member
    sector_lookup = {str(row['sectorId']): row for row in snapshot.get('sectors', [])}
    ranking = {row['symbol']: row for row in _global_rankings(snapshot.get('global_rankings'), now)}
    requested_sector = theme_id.removeprefix('sector:') if theme_id else None
    by_symbol = {}
    for relation in snapshot.get('memberships', []):
        by_symbol.setdefault(relation['symbol'], []).append(relation)
    search = query.casefold().strip()
    for stock in snapshot['stocks']:
        if requested_sector and requested_sector not in stock.get('sector_ids', []):
            continue
        if unclassified and stock.get('sector_ids'):
            continue
        if search and search not in (stock['symbol'] + ' ' + (stock.get('name') or '')).casefold():
            continue
        quote, rec = stock['quote'], recommendations.get(stock['symbol'], {})
        ranked = ranking.get(stock['symbol'], {})
        amount = _number(ranked.get('tradingAmount')) if ranked.get('currency') == 'KRW' else None
        if amount is not None and amount < 0:
            amount = None
        relationships = by_symbol.get(stock['symbol'], [])
        rows.append({'symbol': stock['symbol'], 'name': stock.get('name') or stock['symbol'], 'market': stock.get('market'),
            'price': quote.get('price'), 'quote_at': quote.get('quote_at'), 'first_seen_at': quote.get('first_seen_at'),
            'quote_status': quote['status'], 'catalog_member': stock.get('catalog_member', False),
            'nxt_supported': stock.get('nxt_supported'), 'sector_ids': stock.get('sector_ids', []),
            'theme_refs': [{'id': 'sector:' + str(row['sector_id']), 'name': sector_lookup.get(str(row['sector_id']), {}).get('name', row['sector_id']),
                'relationship': 'OFFICIAL_SECTOR_MEMBER', 'source_url': row.get('source_url'), 'first_seen_at': row.get('first_seen_at')} for row in relationships],
            'watch_score': (rec.get('watch_score') or {}).get('score'), 'observed_turnover_krw': amount,
            'turnover_quality': ranked.get('quality', 'UNAVAILABLE') if amount is not None else 'UNAVAILABLE',
            'analysis_status': 'READY' if stock['symbol'] in snapshot.get('technical', {}) else 'PENDING',
            'reason_codes': ['OFFICIAL_SECTOR_MEMBER'] if relationships else ['OFFICIAL_CLASSIFICATION_PENDING'],
            'recommendation_kind': 'THEME_WATCH', 'source': 'toss'})
    rows.sort(key=lambda row: (row['watch_score'] is not None, row['watch_score'] or 0,
                              row['quote_status'] == 'LIVE', row['observed_turnover_krw'] or 0), reverse=True)
    return {'as_of': now.isoformat(), 'source': 'toss', 'status': snapshot['universe']['status'],
        'items': rows[(page - 1) * limit:page * limit], 'total': len(rows), 'page': page, 'limit': limit,
        'scope': snapshot['universe']['scope'], 'measurement_definition': '관찰 우선순위와 당일 누적 체결 거래대금; 투자자 순매수·매수 확정 아님'}


def discovery_stock_detail(symbol, theme_id=None):
    now = _now()
    snapshot = _context(now)
    stock = next((row for row in snapshot['stocks'] if row['symbol'] == symbol), None)
    if stock is None:
        return None
    queued = enqueue_analysis(symbol, priority=1000)
    analysis = _analyze(snapshot, stock, now, theme_id)
    inputs = read_analysis_inputs(symbol, now)
    ready = bool(analysis['chart']['daily'] or analysis['chart']['minute'])
    return {**analysis, 'status': 'READY' if ready else 'PENDING' if queued['status'] in ('QUEUED', 'READY') else 'UNAVAILABLE',
        'analysis_queue': queued, 'analysis_received_at': inputs.get('first_seen_at') if inputs else None,
        'reason': '실제 캔들로 차트를 분석했습니다. 진입 확정은 별도 위험 조건입니다.' if ready else
                  '실제 일봉·분봉을 백그라운드에서 조회합니다. 수신 전에는 차트를 채우지 않습니다.'}
