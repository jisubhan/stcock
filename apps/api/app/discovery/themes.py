"""Conservative, point-in-time sector hypotheses from observed provider facts.

Official sector membership is classification evidence, not proof of a news
beneficiary. Daily sector performance is ANALYSIS_ONLY when the provider offers
no timestamp. No prices, US returns, investor flow, or trading gates are invented.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import mean
from typing import Any, Mapping

from app.discovery.analysis import _number, _quote, _time, analyze_discovery_stock
from app.radar.sessions import calendar_sessions
from app.strategy.intraday import aware


DEFINITION = '관찰 우선순위 · 수익확률 또는 수급 유입 확정 아님'
SECTOR_URL = 'https://openapi.tossinvest.com/api/v1/sectors/rankings'


def _empty(reason):
    return {'hypotheses_top3': [], 'observed_leaders': [], 'themes': [], 'unmapped_leaders': [],
            'observed_turnover_leaders': [],
            'mapping_pending_themes': [],
            'reason_codes': [reason], 'score_definition': DEFINITION}


def _visible(row, clock, *, require_seen=True):
    if not isinstance(row, Mapping) or row.get('source', 'toss') != 'toss' or row.get('mode', 'live') != 'live':
        return False
    seen = _time(row.get('first_seen_at'))
    if not (seen is not None or not require_seen) or (seen is not None and seen > clock):
        return False
    for key in ('first_seen_at', 'provider_at', 'observed_at', 'as_of', 'quote_at', 'ranked_at', 'rankedAt', 'revision_available_at'):
        if row.get(key) is not None:
            stamp = _time(row[key])
            if stamp is None or stamp > clock:
                return False
    return True


def _rankings(container, clock, *, weekly=False):
    if isinstance(container, Mapping) and weekly:
        container = {**container, 'first_seen_at': container.get('weekly_first_seen_at', container.get('first_seen_at')),
                     'last_success_at': container.get('weekly_last_success_at', container.get('last_success_at')),
                     'provider_at': container.get('weekly_provider_at', container.get('provider_at')),
                     'quality': container.get('weekly_quality', container.get('quality'))}
    if not _visible(container, clock) or container.get('quality') in ('STALE', 'UNAVAILABLE', 'DEMO'):
        return {}
    provider = _time(container.get('provider_at'))
    if container.get('provider_at') is not None and (provider is None or provider > clock):
        return {}
    # Without provider time a response is analysis evidence only. Its receipt
    # does not make an old cached response into today's observed sector leader.
    refreshed = _time(container.get('last_success_at')) if container.get('last_success_at') is not None else _time(container['first_seen_at'])
    if refreshed is None or refreshed > clock or clock - refreshed > timedelta(hours=24):
        return {}
    result = {}
    for row in container.get('weekly_items' if weekly else 'items', []):
        if not isinstance(row, Mapping) or not row.get('sectorId'):
            continue
        if not _visible(row, clock, require_seen=False):
            continue
        item = dict(row)
        item['change_pct'] = value * 100 if (value := _number(row.get('changeRate'))) is not None else None
        item['first_seen_at'] = container['first_seen_at']
        item['provider_at'] = provider.isoformat() if provider else None
        item['quality'] = 'ANALYSIS_ONLY' if provider is None else 'OBSERVED'
        result[str(row['sectorId'])] = item
    return result


def _global_rankings(container, clock):
    if not _visible(container, clock):
        return []
    stamp = _time(container.get('rankedAt'))
    if container.get('rankedAt') is not None and (stamp is None or stamp > clock):
        return []
    if clock - _time(container['first_seen_at']) > timedelta(hours=24):
        return []
    rows = []
    for raw in container.get('rankings', []):
        if not isinstance(raw, Mapping) or not isinstance(raw.get('symbol'), str):
            continue
        if not _visible(raw, clock, require_seen=False):
            continue
        price = raw.get('price') if isinstance(raw.get('price'), Mapping) else {}
        value = _number(price.get('changeRate'))
        age = (clock - stamp).total_seconds() if stamp else None
        rows.append({**raw, 'change_pct': value * 100 if value is not None else None,
                     'ranked_at': stamp.isoformat() if stamp else None,
                     'first_seen_at': container['first_seen_at'],
                     'quality': 'ANALYSIS_ONLY' if stamp is None else 'OBSERVED' if age <= 15 else 'STALE',
                     'latency_seconds': age})
    return rows


def _factor(value, weight, *, quality='UNAVAILABLE', scale=1, positive=False):
    numeric = _number(value)
    strength = min(1., max(0., numeric / scale)) if numeric is not None and positive else numeric
    return {'value': numeric, 'points': strength * weight if strength is not None else None,
            'max_points': weight, 'quality': quality if numeric is not None else 'UNAVAILABLE'}


def _evidence(kind, title, row, *, source_url=SECTOR_URL, relationship=None):
    return {'kind': kind, 'title': title, 'source_url': row.get('source_url') or source_url,
            'first_seen_at': row.get('first_seen_at'), 'provider_at': row.get('provider_at'),
            'relationship': relationship, 'quality': row.get('quality', 'ANALYSIS_ONLY')}


def _catalysts(snapshot, sector_id, clock):
    values = []
    for raw in snapshot.get('catalysts', []):
        if (not isinstance(raw, Mapping) or raw.get('mode', 'live') != 'live'
                or raw.get('source') in ('synthetic', 'fixture', 'demo', 'replay')
                or raw.get('coverage_status') == 'DEMO'):
            continue
        seen, published = _time(raw.get('first_seen_at')), _time(raw.get('published_at'))
        revision = _time(raw.get('revision_available_at'))
        if (not seen or not published or max(seen, published, revision or seen) > clock
                or raw.get('status') != 'VERIFIED' or raw.get('coverage_status') in ('STALE', 'UNAVAILABLE')
                or raw.get('relationship') not in ('DIRECT_VERIFIED', 'NEWS_REPORTED')
                or not str(raw.get('source_url', '')).startswith(('https://', 'http://'))):
            continue
        if sector_id not in raw.get('theme_ids', []) and sector_id.removeprefix('sector:') not in raw.get('sector_ids', []):
            continue
        confidence, materiality = _number(raw.get('confidence')), _number(raw.get('materiality_score'))
        if confidence is None or materiality is None:
            continue
        values.append((min(1., max(0., confidence)) * min(1., max(0., materiality)), raw))
    return values


def _diverse_top3(ranked, sectors):
    """Do not pad Top3 with the same known parent/child or member cohort."""
    selected = []

    def ancestors(sector_id):
        result = set()
        parent = sectors.get(sector_id, {}).get('parentSectorId')
        while parent is not None and str(parent) not in result:
            result.add(str(parent))
            parent = sectors.get(str(parent), {}).get('parentSectorId')
        return result

    for theme in ranked:
        cohort = set(theme['official_member_symbols'])
        duplicated = False
        for old in selected:
            old_cohort = set(old['official_member_symbols'])
            denominator = min(len(cohort), len(old_cohort))
            heavy_overlap = bool(denominator and len(cohort & old_cohort) / denominator >= .8)
            related = (theme['sector_id'] in ancestors(old['sector_id'])
                       or old['sector_id'] in ancestors(theme['sector_id']))
            if related or heavy_overlap:
                duplicated = True
                if 'TOP3_OVERLAPPING_CLASSIFICATION_DEDUPED' not in theme['reason_codes']:
                    theme['reason_codes'].append('TOP3_OVERLAPPING_CLASSIFICATION_DEDUPED')
                break
        if not duplicated:
            selected.append(theme)
        if len(selected) == 3:
            break
    return selected


def _us_context(calendar, clock):
    result = {'label': 'SESSION_UNVERIFIED', 'performance_session': 'UNVERIFIED_PROVIDER_TIME',
              'reason': '업종 응답에 공급자 시각이 없어 미국 현재 세션 또는 확정 종가 성과로 단정하지 않습니다.'}
    if not _visible(calendar, clock, require_seen=False):
        return result
    phases = {'dayMarket': 'US_DAY_MARKET', 'preMarket': 'US_PRE_MARKET',
              'regularMarket': 'US_REGULAR_MARKET', 'afterMarket': 'US_AFTER_MARKET'}
    completed = []
    for day in (calendar.get('previousBusinessDay') or {}, calendar.get('today') or {}):
        for key, label in phases.items():
            interval = day.get(key)
            if not isinstance(interval, Mapping):
                continue
            start, end = _time(interval.get('startTime')), _time(interval.get('endTime'))
            if not start or not end or end <= start:
                continue
            if start <= clock < end:
                return {**result, 'label': label, 'calendar_status': 'VERIFIED_SCHEDULE',
                        'session_start': start.isoformat(), 'session_end': end.isoformat()}
            if end <= clock:
                completed.append(end)
    return {**result, 'label': 'US_LAST_COMPLETED_SESSION', 'calendar_status': 'VERIFIED_SCHEDULE',
            'last_session_end': max(completed).isoformat()} if completed else result


def build_theme_hypotheses(snapshot: Mapping[str, Any], as_of: datetime | str) -> dict:
    """Rank at most three evidence-backed hypotheses; never pad missing facts.

    ``snapshot`` is an already observed official discovery snapshot. This pure
    function does not collect data. Missing sectors do not hide unmapped leaders.
    Sector overlap allocation is over unique observed stock turnover, not a sum
    of overlapping provider sector totals. US comparison needs the same official
    sector id or an explicit supplied cross-market mapping.
    """
    clock = aware(as_of)
    if not isinstance(snapshot, Mapping) or snapshot.get('source') != 'toss':
        return _empty('ACTUAL_DISCOVERY_SOURCE_REQUIRED')
    if snapshot.get('first_seen_at') and not _visible(snapshot, clock):
        return _empty('DISCOVERY_UNAVAILABLE_AT_AS_OF')
    reasons = ['OFFICIAL_SECTOR_CLASSIFICATION_IS_NOT_DIRECT_NEWS_BENEFICIARY',
               'WATCH_PRIORITY_IS_NOT_RETURN_PROBABILITY', 'TRADING_AMOUNT_IS_NOT_INVESTOR_NET_BUYING']
    calendar = (snapshot.get('calendar') or {}).get('KR') or {}
    try:
        sessions = calendar_sessions(calendar, include_previous=True)
    except (ValueError, TypeError, KeyError):
        sessions = []
    previous_day = calendar.get('previousBusinessDay', {}).get('date')
    rank_inputs = snapshot.get('sector_rankings') or {}
    kr, us = _rankings(rank_inputs.get('KR'), clock), _rankings(rank_inputs.get('US'), clock)
    us_week = _rankings(rank_inputs.get('US'), clock, weekly=True)
    global_rows = _global_rankings(snapshot.get('global_rankings'), clock)
    global_by_symbol = {row['symbol']: row for row in global_rows}
    technical_inputs = snapshot.get('technical') or {}
    stocks, analyses = {}, {}
    for raw in snapshot.get('stocks', []):
        if (not isinstance(raw, Mapping) or not isinstance(raw.get('symbol'), str)
                or raw.get('country', 'KR') != 'KR' or not _visible(raw, clock, require_seen=False)):
            continue
        symbol = raw['symbol']
        quote = _quote(raw.get('quote'), clock)
        data = technical_inputs.get(symbol) or {}
        if data.get('source', 'toss') != 'toss' or (data.get('first_seen_at') and not _visible(data, clock)):
            data = {}
        analysis = analyze_discovery_stock(symbol=symbol, name=raw.get('name') or symbol, as_of=clock,
            quote=quote, daily_candles=data.get('daily', []), minute_candles=data.get('minute', []),
            sessions=sessions, previous_business_day=previous_day,
            daily_first_seen_at=data.get('daily_first_seen_at', data.get('first_seen_at')),
            minute_first_seen_at=data.get('minute_first_seen_at', data.get('first_seen_at')))
        stocks[symbol], analyses[symbol] = dict(raw), analysis
    sectors = {}
    for row in snapshot.get('sectors', []):
        if _visible(row, clock) and row.get('sectorId') and row.get('name') and row.get('source_url'):
            sectors[str(row['sectorId'])] = dict(row)
    memberships, by_symbol = {}, {}
    for row in snapshot.get('memberships', []):
        if (not _visible(row, clock) or row.get('relationship') != 'OFFICIAL_SECTOR_MEMBER'
                or str(row.get('sector_id')) not in sectors or not row.get('source_url') or not row.get('symbol')):
            continue
        sector_id, symbol = str(row['sector_id']), row['symbol']
        memberships.setdefault(sector_id, {})[symbol] = dict(row)
        by_symbol.setdefault(symbol, set()).add(sector_id)
    actual_amounts = {row['symbol']: amount for row in global_rows if row.get('currency') == 'KRW'
                      and (amount := _number(row.get('tradingAmount'), nonnegative=True)) is not None}
    denominator = sum(actual_amounts.values())
    themes = []
    for index, (sector_id, sector) in enumerate(sectors.items()):
        official_members = memberships.get(sector_id, {})
        members = []
        changes, chart_scores, velocities = [], [], []
        for symbol, relation in official_members.items():
            if symbol not in analyses:
                continue
            analysis, stock = analyses[symbol], stocks[symbol]
            actual_quote = analysis['quote']
            rank = global_by_symbol.get(symbol, {})
            change = _number(rank.get('change_pct'))
            # A ranking change is a dated sector-analysis fact, not fresh flow.
            if change is None and actual_quote:
                reference = next((row for row in reversed(analysis['chart']['daily'])
                                  if row['status'] == 'CLOSED' and row['time'] == previous_day), None)
                if reference:
                    change = (actual_quote['price'] / reference['close'] - 1) * 100
            if change is not None and actual_quote:
                changes.append(change)
            chart = analysis['watch_score']['factors']['chart_structure']['value']
            if chart is not None:
                chart_scores.append(chart / 100)
            # Only explicitly supplied actual interval observations may supply a
            # velocity. A day's tradingAmount and a daily gain never substitute.
            supplied_flow = (snapshot.get('flow_observations') or {}).get(symbol, []) if isinstance(snapshot.get('flow_observations'), Mapping) else []
            if supplied_flow:
                from app.discovery.analysis import _flow
                flow = _flow([row for row in supplied_flow if (row.get('symbol') if isinstance(row, Mapping)
                             else getattr(row, 'symbol', None)) == symbol], clock)
                if flow:
                    velocities.append(flow['turnover_krw_per_min'])
            members.append({'symbol': symbol, 'name': stock.get('name') or symbol,
                            'state': 'WATCH', 'recommendation_kind': 'THEME_WATCH', 'source': 'toss',
                            'price': actual_quote['price'] if actual_quote else None, 'quote': actual_quote,
                            'change_pct': change, 'association_kind': 'OFFICIAL_SECTOR_MEMBER',
                            'source_url': relation['source_url'], 'first_seen_at': relation['first_seen_at'],
                            'watch_score': analysis['watch_score'], 'technical_quality': analysis['quality']['daily'],
                            'global_rank': rank.get('rank'), 'trading_amount_krw': actual_amounts.get(symbol)})
        domestic = kr.get(sector_id)
        us_ids = sector.get('us_sector_ids') or [sector_id]
        us_matches, weekly_matches = [us[key] for key in us_ids if key in us], [us_week[key] for key in us_ids if key in us_week]
        us_values = [row['change_pct'] for row in us_matches if row['change_pct'] is not None]
        weekly_values = [row['change_pct'] for row in weekly_matches if row['change_pct'] is not None]
        catalysts = _catalysts(snapshot, 'sector:' + sector_id, clock)
        factors = {
            'us_1d': _factor(mean(us_values) if us_values else None, 15, quality='ANALYSIS_ONLY', scale=3, positive=True),
            'us_1w': _factor(mean(weekly_values) if weekly_values else None, 10, quality='ANALYSIS_ONLY', scale=8, positive=True),
            'domestic_sector_performance': _factor(domestic['change_pct'] if domestic else None, 20, quality='ANALYSIS_ONLY', scale=3, positive=True),
            'domestic_breadth': _factor(sum(change > 0 for change in changes) / len(changes) if changes else None, 20, quality='ANALYSIS_ONLY'),
            'domestic_velocity': _factor(sum(velocities) if velocities else None, 15, quality='OBSERVED', scale=1_000_000_000, positive=True),
            'chart_structure': _factor(mean(chart_scores) if chart_scores else None, 10, quality='READY'),
            'catalyst': _factor(max((value for value, _ in catalysts), default=None), 10, quality='VERIFIED'),
        }
        available = sum(item['max_points'] for item in factors.values() if item['value'] is not None)
        score = round(sum(item['points'] or 0 for item in factors.values()), 2) if available else None
        evidence = [_evidence('OFFICIAL_SECTOR_CLASSIFICATION', '공급자 공식 업종 분류 · 뉴스 직접 수혜 확인 아님', sector,
                              source_url=sector['source_url'], relationship='OFFICIAL_SECTOR_MEMBER')]
        if domestic:
            evidence.append(_evidence('SECTOR_PERFORMANCE', '한국 업종 1일 성과 · 공급자 시각 미제공 시 분석 전용', domestic))
        evidence += [_evidence('SECTOR_PERFORMANCE', '같은 공식 업종의 미국 1일 성과 · 인과관계 또는 국내 수혜 확정 아님', row) for row in us_matches]
        evidence += [_evidence('SECTOR_PERFORMANCE', '같은 공식 업종의 미국 1주 성과', row) for row in weekly_matches]
        evidence += [_evidence('CATALYST', row['title'], row, relationship=row['relationship']) for _, row in catalysts]
        raw_amount = sum(actual_amounts.get(symbol, 0) for symbol in official_members)
        allocated = sum(actual_amounts.get(symbol, 0) / len(by_symbol[symbol]) for symbol in official_members if symbol in by_symbol)
        if raw_amount:
            evidence.append(_evidence('OBSERVED_TURNOVER', '조회된 전시장 랭킹 종목의 일 누적 거래대금 · 순매수 또는 속도 아님',
                         snapshot['global_rankings'], source_url='https://openapi.tossinvest.com/api/v1/rankings'))
        fresh = sum(bool(member['quote'] and member['quote']['status'] == 'LIVE') for member in members)
        theme_reasons = ['OFFICIAL_CLASSIFICATION_ONLY', 'NO_AUTOMATIC_NEWS_BENEFICIARY_INFERENCE']
        if not us_values and not weekly_values:
            theme_reasons.append('US_SECTOR_MAPPING_OR_PERFORMANCE_UNAVAILABLE')
        if not velocities:
            theme_reasons.append('INTERVAL_TURNOVER_UNAVAILABLE')
        if not catalysts:
            theme_reasons.append('CATALYST_NOT_CONFIGURED_OR_NO_VERIFIED_EVIDENCE')
        if len(members) < len(official_members):
            theme_reasons.append('PARTIAL_MEMBER_COVERAGE')
        theme = {'id': 'sector:' + sector_id, 'sector_id': sector_id, 'name': sector['name'],
                 'depth': sector.get('depth'), 'parent_sector_id': sector.get('parentSectorId'),
                 'state': 'PREMARKET_HYPOTHESIS' if not any(s.start.date() == clock.date() for s in sessions)
                     or clock < min(s.start for s in sessions if s.start.date() == clock.date()) else 'WATCH',
                 'quality': 'ANALYSIS_ONLY' if score is not None else 'UNAVAILABLE', 'score': score,
                 'score_definition': DEFINITION, 'factor_coverage': available / 100, 'available_max_points': available,
                 'factors': factors, 'evidence': evidence, 'members': members, 'recommended_stocks': [],
                 'official_member_symbols': list(official_members),
                 'flow_confirmed': False, 'observation_kind': 'DAILY_SECTOR_PERFORMANCE',
                 'coverage': {'members_expected': len(official_members), 'members_observed': len(members),
                              'domestic_catalog_members': sum(stocks[symbol].get('catalog_member', True) is True
                                  for symbol in official_members if symbol in stocks),
                              'quote_fresh_count': fresh, 'change_observations': len(changes),
                              'ratio': len(members) / len(official_members) if official_members else 0,
                              'membership_scope': 'Available official membership mappings; unqueried sector constituents are not counted as observed',
                              'turnover_scope': 'Observed global trading-amount ranking subset; not full market turnover'},
                 'turnover': {'raw_member_trading_amount_krw': raw_amount if any(symbol in actual_amounts for symbol in official_members) else None,
                              'allocated_trading_amount_krw': allocated if any(symbol in actual_amounts for symbol in official_members) else None,
                              'observed_unique_denominator_krw': denominator or None,
                              'allocated_share': allocated / denominator if denominator else None,
                              'allocation_definition': 'Each observed stock amount divided equally across its available official sectors; no parent/child double sum'},
                 'first_seen_at': sector['first_seen_at'], 'reason_codes': theme_reasons,
                 'provider_rank': domestic.get('rank') if domestic else None, '_index': index}
        # Add theme priority to the already calculated fixed-weight stock score.
        for member in members:
            watch = member['watch_score']
            factors_copy = {key: dict(value) for key, value in watch['factors'].items()}
            factors_copy['theme_context'] = {'value': score, 'points': score * .30 if score is not None else None, 'max_points': 30}
            covered = sum(value['max_points'] for value in factors_copy.values() if value['value'] is not None)
            member['watch_score'] = {**watch, 'score': round(sum(value['points'] or 0 for value in factors_copy.values()), 2) if covered else None,
                                    'coverage': covered / 100, 'available_max_points': covered, 'factors': factors_copy}
        candidates = [member for member in members if member['watch_score']['score'] is not None]
        # Provider order and actual observed amount break equal scores; symbol
        # lexical order is never invented as a market signal.
        candidates.sort(key=lambda item: (item['quote'] is not None, item['watch_score']['score'],
                                         bool(item['quote'] and item['quote']['status'] == 'LIVE'),
                                         item['trading_amount_krw'] or 0), reverse=True)
        theme['recommended_stocks'] = candidates[:3]
        themes.append(theme)
    pending = [theme for theme in themes if not theme['coverage']['domestic_catalog_members']]
    for theme in pending:
        theme['state'] = 'THEME_MAPPING_PENDING'
        theme['reason_codes'].append('DOMESTIC_OFFICIAL_MEMBER_MAPPING_REQUIRED')
    scored = [theme for theme in themes if theme['score'] is not None and theme['coverage']['domestic_catalog_members']]
    scored.sort(key=lambda item: (-item['score'], item['provider_rank'] if isinstance(item['provider_rank'], (int, float)) else float('inf'), item['_index']))
    hypotheses = _diverse_top3(scored, sectors)
    # This view is explicitly daily observation evidence, never live-flow
    # confirmation. Existing rotation output remains the authority for that.
    observed = [theme for theme in themes if theme['factors']['domestic_sector_performance']['value'] is not None]
    observed.sort(key=lambda item: (-item['factors']['domestic_sector_performance']['value'],
                                  item['provider_rank'] if isinstance(item['provider_rank'], (int, float)) else float('inf'), item['_index']))
    turnover_ranked = [theme for theme in themes if theme['turnover']['allocated_trading_amount_krw'] is not None]
    turnover_ranked.sort(key=lambda item: (-item['turnover']['allocated_trading_amount_krw'], item['_index']))
    turnover_leaders = []
    for theme in _diverse_top3(turnover_ranked, sectors):
        matching = [row for row in global_rows if row['symbol'] in memberships.get(theme['sector_id'], {})
                    and row['symbol'] in actual_amounts]
        statuses = {row['quality'] for row in matching}
        quality = 'OBSERVED' if statuses == {'OBSERVED'} else 'STALE' if 'STALE' in statuses else 'ANALYSIS_ONLY'
        turnover_leaders.append({**theme, 'quality': quality, 'state': 'WATCH',
                                 'observation_kind': 'OBSERVED_DAILY_TRADING_AMOUNT', 'flow_confirmed': False,
                                 'provider_at': matching[0]['ranked_at'] if matching else None,
                                 'latency_seconds': max((row['latency_seconds'] for row in matching if row['latency_seconds'] is not None), default=None),
                                 'reason_codes': list(dict.fromkeys(theme['reason_codes'] + ['CUMULATIVE_TRADING_AMOUNT_NOT_INTERVAL_FLOW',
                                      'GLOBAL_TOP_RANKING_SUBSET_NOT_FULL_MARKET_TURNOVER'])),
                                 'measurement_definition': 'Known official Top ranking daily cumulative trading amount, allocated once across overlapping classifications; not net buying or velocity'})
    unmapped = []
    for row in global_rows:
        if row['symbol'] in by_symbol:
            continue
        stock, quote = stocks.get(row['symbol'], {}), (analyses.get(row['symbol']) or {}).get('quote')
        rank_price = row.get('price') if isinstance(row.get('price'), Mapping) else {}
        unmapped.append({'symbol': row['symbol'], 'name': stock.get('name') or row['symbol'], 'rank': row.get('rank'),
                         'price': quote['price'] if quote else _number(rank_price.get('lastPrice'), positive=True),
                         'quote': quote, 'change_pct': row.get('change_pct'), 'trading_amount_krw': actual_amounts.get(row['symbol']),
                         'quality': quote['status'] if quote else 'ANALYSIS_ONLY', 'association_kind': 'UNCLASSIFIED',
                         'classification_status': 'PENDING', 'first_seen_at': row['first_seen_at'],
                         'source_url': 'https://openapi.tossinvest.com/api/v1/rankings',
                         'reason_codes': ['OFFICIAL_SECTOR_MEMBERSHIP_NOT_OBSERVED']})
    if not hypotheses:
        reasons.append('NO_SCORABLE_THEME_FACTS')
    if not global_rows:
        reasons.append('GLOBAL_RANKING_UNAVAILABLE')
    if not sectors:
        reasons.append('OFFICIAL_SECTOR_CATALOG_UNAVAILABLE')
    for theme in themes:
        theme.pop('_index', None)
    for theme in turnover_leaders:
        theme.pop('_index', None)
    return {'hypotheses_top3': hypotheses, 'observed_leaders': observed[:3], 'themes': themes,
            'observed_turnover_leaders': turnover_leaders,
            'mapping_pending_themes': pending,
            'unmapped_leaders': unmapped, 'reason_codes': reasons, 'score_definition': DEFINITION,
            'coverage': {'catalog_sectors': len(sectors), 'unique_classified_symbols': len(by_symbol),
                         'quoted_stocks': sum(analysis['quote'] is not None for analysis in analyses.values()),
                         'global_ranked_symbols': len(global_by_symbol),
                         'unmapped_ranked_symbols': len(unmapped), 'scope': 'Configured observed official universe and global Top ranking subset'},
            'us_session_context': _us_context((snapshot.get('calendar') or {}).get('US') or {}, clock)}
