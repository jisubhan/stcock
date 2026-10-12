"""Versioned actual-data warehouse for whole-market discovery.

No network, synthetic fallback, financial inference or credential storage. Catalog
scope is the provider's active common-stock catalog, not every exchange product.
"""
from datetime import datetime, timedelta
from contextlib import nullcontext
import json
import math
import os
import re

from app.radar.models import KST, aware
from app.radar.sessions import parsed_time, current_session
from app.radar.storage import connection, encoded, identity, save_provider_health
from app.settings import runtime_dir

ORIGIN = 'https://openapi.tossinvest.com/api/v1'
MARKETS = ('KOSPI', 'KOSDAQ', 'KR_ETC')
SCOPE = 'Toss-provided ACTIVE common STOCK catalog for KOSPI/KOSDAQ/KR_ETC; not all exchange products or independently verified exchange completeness'


def _clock(value=None):
    return aware(value or datetime.now(KST))


def _stamp(value):
    try:
        return parsed_time(value) if isinstance(value, str) else aware(value) if isinstance(value, datetime) else None
    except (ValueError, TypeError):
        return None


def _symbol(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Z0-9]{6}', value) is not None


def _number(value, positive=False):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) and (not positive or result > 0) else None


def _save_many(kind, pairs, first_seen_at, *, _connection=None):
    stamp = _clock(first_seen_at)
    prepared, conflicts = {}, set()
    for key, payload in pairs:
        key = str(key)
        content = {name: value for name, value in payload.items() if name != 'first_seen_at'} | {'source': 'toss'}
        if key in prepared and prepared[key] != content:
            conflicts.add(key)
        prepared[key] = content
    saved_values = []
    with nullcontext(_connection) if _connection is not None else connection() as conn:
        for key, content in prepared.items():
            if key in conflicts:
                continue
            current = conn.execute('SELECT payload_json FROM discovery_latest WHERE kind=? AND record_key=?', (kind, key)).fetchone()
            previous = json.loads(current['payload_json']) if current else None
            if previous and {name: value for name, value in previous.items() if name != 'first_seen_at'} == content:
                saved_values.append(previous)
                continue
            if kind == 'quote' and previous and previous.get('quote_at') and (
                    content.get('quote_at') is None or _stamp(content['quote_at']) < _stamp(previous['quote_at'])):
                # Reject before BOTH accepted history and latest cache. Otherwise
                # a later revision would expose the refused old quote via PIT.
                continue
            # Returning to an earlier value is a NEW revision, not the old
            # record's original availability time (A -> B -> A restoration).
            record_id = identity(kind, key, content, stamp)
            value = {**content, 'first_seen_at': stamp.isoformat()}
            conn.execute('INSERT OR IGNORE INTO discovery_records VALUES (?,?,?,?,?)',
                         (record_id, kind, key, stamp.isoformat(), encoded(value)))
            conn.execute('''INSERT INTO discovery_latest VALUES (?,?,?,?) ON CONFLICT(kind,record_key) DO UPDATE SET
                first_seen_at=excluded.first_seen_at,payload_json=excluded.payload_json
                WHERE excluded.first_seen_at>=discovery_latest.first_seen_at''',
                         (kind, key, stamp.isoformat(), encoded(value)))
            saved_values.append(value)
    return saved_values


def _save(kind, key, payload, first_seen_at, *, _connection=None):
    return _save_many(kind, [(key, payload)], first_seen_at, _connection=_connection)[0]


def _read(kind, as_of=None):
    clock = _clock(as_of)
    result = {}
    with connection() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discovery_latest'").fetchone():
            return result
        # Current queries read the bounded latest cache. A historical query only
        # needs indexed per-key fallback where the latest revision is later.
        for row in conn.execute('SELECT record_key,first_seen_at,payload_json FROM discovery_latest WHERE kind=?', (kind,)):
            selected = row
            if _stamp(row['first_seen_at']) > clock:
                selected = conn.execute('''SELECT payload_json FROM discovery_records WHERE kind=? AND record_key=? AND first_seen_at<=?
                    ORDER BY first_seen_at DESC,rowid DESC LIMIT 1''', (kind, row['record_key'], clock.isoformat())).fetchone()
            if selected:
                result[row['record_key']] = json.loads(selected['payload_json'])
    return result


def _read_one(kind, key, as_of=None):
    """Indexed current/PIT lookup without reading every chart payload."""
    clock = _clock(as_of)
    with connection() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discovery_latest'").fetchone():
            return None
        row = conn.execute('SELECT first_seen_at,payload_json FROM discovery_latest WHERE kind=? AND record_key=?', (kind, key)).fetchone()
        if row and _stamp(row['first_seen_at']) > clock:
            row = conn.execute('''SELECT payload_json FROM discovery_records WHERE kind=? AND record_key=? AND first_seen_at<=?
                ORDER BY first_seen_at DESC,rowid DESC LIMIT 1''', (kind, key, clock.isoformat())).fetchone()
        return json.loads(row['payload_json']) if row else None


def _live_allowed():
    try:
        report = json.loads((runtime_dir() / 'toss-probe.json').read_text())
        return (os.environ.get('TOSS_DATA_MODE', 'auto').lower() != 'demo'
                and report.get('authentication', {}).get('status') in ('passed', 'success', 'ready')
                and report.get('venue', {}).get('verified') is True)
    except (ValueError, OSError, TypeError):
        return False


def save_catalog(rows, first_seen_at, *, market, status='READY'):
    if market not in MARKETS:
        raise ValueError('Unknown domestic catalog market')
    symbols, records = [], []
    for row in rows:
        if not _symbol(row.get('symbol')) or row.get('securityType') != 'STOCK' or row.get('isCommonShare') is not True:
            continue
        symbols.append(row['symbol'])
        records.append((row['symbol'], {key: row.get(key) for key in ('symbol', 'name', 'securityType', 'isCommonShare', 'isinCode')} | {
            'market': market, 'requested_status': 'ACTIVE', 'source_url': ORIGIN + '/stocks/all'}))
    with connection() as conn:
        saved = _save_many('stock', records, first_seen_at, _connection=conn)
        symbols = [row['symbol'] for row in saved]
        _save('catalog', market, {'market': market, 'symbols': sorted(set(symbols)), 'status': status,
                                  'filters': {'status': 'ACTIVE', 'securityType': 'STOCK', 'commonShare': True},
                                  'source_url': ORIGIN + '/stocks/all', 'provider_at': None}, first_seen_at, _connection=conn)
        _save('catalog_receipt', market, {'last_success_at': _clock(first_seen_at).isoformat()}, first_seen_at, _connection=conn)
    return len(set(symbols))


def save_sectors(rows, first_seen_at):
    saved = _save_many('sector', [(row['sectorId'], {key: row.get(key) for key in ('sectorId', 'name', 'depth', 'parentSectorId')} | {
        'source_url': ORIGIN + '/sectors', 'provider_at': None}) for row in rows if isinstance(row.get('sectorId'), str)], first_seen_at)
    _save('sector_scope', 'global', {'sector_ids': sorted({row['sectorId'] for row in saved}),
                                   'source_url': ORIGIN + '/sectors'}, first_seen_at)


def save_memberships(sector_id, rows, first_seen_at, country='KR'):
    symbols, records = [], []
    for row in rows:
        if not _symbol(row.get('symbol')):
            continue
        symbols.append(row['symbol'])
        records.append((f'{country}:{sector_id}:{row["symbol"]}', {
            'sector_id': sector_id, 'symbol': row['symbol'], 'country': country,
            'relationship': 'OFFICIAL_SECTOR_MEMBER', 'source_url': ORIGIN + f'/sectors/{sector_id}/stocks',
            'provider_at': None, 'quality': 'ANALYSIS_ONLY', 'volume': row.get('volume'),
            'tradingAmount': row.get('tradingAmount'), 'currency': row.get('currency')}))
    saved = _save_many('membership', records, first_seen_at)
    symbols = [row['symbol'] for row in saved]
    # Membership revisions are scoped to the actual latest list; removed members
    # stay in immutable history but disappear from the current graph.
    _save('membership_scope', f'{country}:{sector_id}', {'country': country, 'sector_id': sector_id, 'symbols': sorted(set(symbols))}, first_seen_at)


def save_sector_rankings(country, items, first_seen_at, duration='1d'):
    with connection() as conn:
        _save('sector_ranking', f'{country}:{duration}', {'country': country, 'duration': duration, 'items': items,
            'provider_at': None, 'quality': 'ANALYSIS_ONLY', 'source_url': ORIGIN + '/sectors/rankings'}, first_seen_at, _connection=conn)
        _save('sector_ranking_receipt', f'{country}:{duration}', {'last_success_at': _clock(first_seen_at).isoformat()}, first_seen_at, _connection=conn)


def save_global_rankings(metadata, first_seen_at, country='KR'):
    clock = _clock(first_seen_at)
    provider = _stamp(metadata.get('rankedAt'))
    if metadata.get('rankedAt') is not None and (provider is None or provider > clock):
        raise ValueError('Invalid or future ranking provider time')
    _save('global_ranking', country, {'rankedAt': provider.isoformat() if provider else None,
        'rankings': metadata.get('rankings', []), 'source_url': ORIGIN + '/rankings', 'country': country}, clock)


def save_calendar(country, calendar, first_seen_at):
    _save('calendar', country, {'calendar': calendar, 'source_url': ORIGIN + f'/market-calendar/{country}'}, first_seen_at)


def save_stock_master(rows, first_seen_at):
    _save_many('master', [(row['symbol'], {key: row.get(key) for key in ('symbol', 'name', 'status', 'market', 'currency', 'koreanMarketDetail')} | {
        'source_url': ORIGIN + '/stocks'}) for row in rows if _symbol(row.get('symbol'))], first_seen_at)


def save_market_quotes(rows, first_seen_at, expected_symbols=None):
    clock = _clock(first_seen_at)
    expected = set(expected_symbols) if expected_symbols is not None else None
    records = []
    for row in rows:
        if not _symbol(row.get('symbol')) or row.get('currency') != 'KRW' or (expected is not None and row['symbol'] not in expected):
            continue
        price = _number(row.get('lastPrice'), positive=True)
        provider = _stamp(row.get('timestamp'))
        if price is None or (row.get('timestamp') is not None and provider is None) or (provider is not None and provider > clock):
            continue
        records.append((row['symbol'], {'symbol': row['symbol'], 'price': price,
              'quote_at': provider.isoformat() if provider else None, 'currency': 'KRW', 'source_url': ORIGIN + '/prices'}))
    count = len(_save_many('quote', records, clock))
    return {'stored': count, 'rejected': len(rows) - count}


def save_scan_health(payload, first_seen_at):
    _save('health', 'scanner', payload, first_seen_at)
    save_provider_health('TOSS_MARKET_SCAN', payload, _clock(first_seen_at))


def save_analysis_inputs(symbol, daily, minute, daily_first_seen_at, minute_first_seen_at, source_urls=None, reason=None):
    daily_clock, minute_clock = _clock(daily_first_seen_at), _clock(minute_first_seen_at)
    return _save('analysis', symbol, {'symbol': symbol, 'daily': daily, 'minute': minute,
        'daily_first_seen_at': daily_clock.isoformat(), 'minute_first_seen_at': minute_clock.isoformat(),
        'mode': 'live', 'status': 'READY' if daily and minute else 'PARTIAL',
        'reason': reason,
        'source_urls': source_urls or {'daily': ORIGIN + '/candles', 'minute': ORIGIN + '/candles'}}, max(daily_clock, minute_clock))


def read_analysis_inputs(symbol, as_of=None):
    return _read_one('analysis', symbol, as_of)


def enqueue_analysis(symbol, priority=100, as_of=None):
    clock = _clock(as_of)
    known = _read_one('stock', symbol, as_of) is not None or any(row.get('symbol') == symbol for row in (_read_one('global_ranking', 'KR', as_of) or {}).get('rankings', []))
    if not _symbol(symbol) or not known:
        return {'symbol': symbol, 'status': 'UNAVAILABLE', 'reason': 'Symbol has not been observed in a verified catalog or ranking.'}
    if priority >= 100:
        _save('selection', symbol, {'symbol': symbol, 'selected_at': clock.isoformat()}, clock)
    existing = read_analysis_inputs(symbol, as_of)
    if existing and (clock - _stamp(existing['first_seen_at'])).total_seconds() <= 60 and existing['status'] == 'READY':
        return {'symbol': symbol, 'status': 'READY', 'requested_at': clock.isoformat()}
    with connection() as conn:
        conn.execute('''INSERT INTO discovery_analysis_queue VALUES (?,?,?,'QUEUED',NULL,NULL) ON CONFLICT(symbol) DO UPDATE SET
            priority=MAX(priority,excluded.priority),status='QUEUED',requested_at=CASE WHEN status='QUEUED' THEN requested_at ELSE excluded.requested_at END''',
                     (symbol, clock.isoformat(), int(priority)))
    return {'symbol': symbol, 'status': 'QUEUED', 'requested_at': clock.isoformat()}


def pending_analysis(limit=2):
    with connection() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM discovery_analysis_queue WHERE status='QUEUED' ORDER BY priority DESC,requested_at LIMIT ?", (limit,))]


def finish_analysis(symbol, status='READY', reason=None):
    with connection() as conn:
        conn.execute('UPDATE discovery_analysis_queue SET status=?,completed_at=?,reason=? WHERE symbol=?', (status, datetime.now(KST).isoformat(), reason, symbol))


def current_hot_symbols(limit=200, as_of=None):
    clock = _clock(as_of)
    ranking = _read('global_ranking', as_of).get('KR', {})
    stamp = _stamp(ranking.get('rankedAt'))
    symbols = [row['symbol'] for row in ranking.get('rankings', []) if _symbol(row.get('symbol'))] if stamp and 0 <= (clock - stamp).total_seconds() <= 15 else []
    cutoff = clock - timedelta(minutes=15)
    selections = sorted((row for row in _read('selection', as_of).values()
                         if cutoff <= _stamp(row['selected_at']) <= clock), key=lambda row: row['selected_at'], reverse=True)
    selected = [row['symbol'] for row in selections]
    with connection() as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discovery_analysis_queue'").fetchone():
            pending = [row['symbol'] for row in conn.execute("SELECT symbol FROM discovery_analysis_queue WHERE status='QUEUED' AND requested_at>=? AND requested_at<=? ORDER BY priority DESC,requested_at DESC LIMIT ?", (cutoff.isoformat(), clock.isoformat(), limit))]
            selected += pending
    symbols = selected + symbols
    return list(dict.fromkeys(symbols))[:limit]


def discovery_snapshot(as_of=None):
    clock = _clock(as_of)
    catalog, stocks = _read('catalog', as_of), _read('stock', as_of)
    quotes, master = _read('quote', as_of), _read('master', as_of)
    sector_scope = _read_one('sector_scope', 'global', as_of)
    active_sector_ids = set(sector_scope.get('sector_ids', [])) if sector_scope else None
    sectors = [row for row in _read('sector', as_of).values() if active_sector_ids is None or row['sectorId'] in active_sector_ids]
    scopes = _read('membership_scope', as_of)
    memberships = [{**row, 'first_seen_at': max(row['first_seen_at'], scopes[f'KR:{row["sector_id"]}']['first_seen_at'])}
        for row in _read('membership', as_of).values() if row['country'] == 'KR'
        and (active_sector_ids is None or row['sector_id'] in active_sector_ids)
        and row['symbol'] in scopes.get(f'KR:{row["sector_id"]}', {}).get('symbols', [])]
    graph = {}
    for row in memberships:
        graph.setdefault(row['symbol'], []).append(row['sector_id'])
    catalog_symbols = {symbol for row in catalog.values() for symbol in row.get('symbols', [])}
    global_rankings = _read_one('global_ranking', 'KR', as_of) or {}
    # Timestamped provider rankings are useful before the full catalog arrives,
    # but never enlarge the catalog denominator or prove catalog completeness.
    ranked_symbols = {row.get('symbol') for row in global_rankings.get('rankings', []) if _symbol(row.get('symbol'))}
    symbols = sorted(catalog_symbols | ranked_symbols)
    health = _read('health', as_of).get('scanner', {})
    calendars = {country: row['calendar'] for country, row in _read('calendar', as_of).items()}
    session = current_session(calendars.get('KR', {}), clock) if calendars.get('KR') else None
    allowed = _live_allowed() and health.get('status') not in ('UNAVAILABLE', 'BLOCKED')
    output = []
    for symbol in symbols:
        stock = stocks.get(symbol, {'symbol': symbol})
        actual = quotes.get(symbol)
        stamp = _stamp(actual.get('quote_at')) if actual else None
        age = (clock - stamp).total_seconds() if stamp else None
        quality = 'LIVE' if allowed and session and age is not None and 0 <= age <= 15 else 'STALE' if actual else 'UNAVAILABLE'
        info = master.get(symbol, {})
        quote = {**actual, 'status': quality, 'latency_seconds': age} if actual else {'price': None, 'quote_at': None, 'first_seen_at': None, 'status': 'UNAVAILABLE', 'latency_seconds': None}
        output.append({**stock, 'name': info.get('name') or stock.get('name') or symbol,
            'market': info.get('market') or stock.get('market'), 'catalog_member': symbol in catalog_symbols,
            'quote': quote, 'sector_ids': sorted(graph.get(symbol, [])),
            'classified': bool(graph.get(symbol)), 'nxt_supported': (info.get('koreanMarketDetail') or {}).get('nxtSupported'),
            'master': info, 'provenance': {'catalog': stock.get('source_url'), 'quote': actual.get('source_url') if actual else None,
                'name': info.get('source_url') if info.get('name') else stock.get('source_url') if stock.get('name') else None},
            'theme_relationship': 'OFFICIAL_SECTOR_MEMBER' if graph.get(symbol) else 'UNMAPPED_STRENGTH'})
    catalog_output = [row for row in output if row['catalog_member']]
    classified = sum(row['classified'] for row in catalog_output)
    sector_records = _read('sector_ranking', as_of)
    sector_receipts = _read('sector_ranking_receipt', as_of)
    sector_rankings = {}
    for country in ('KR', 'US'):
        day, week = sector_records.get(f'{country}:1d', {}), sector_records.get(f'{country}:1w', {})
        sector_rankings[country] = {**day, 'items': day.get('items', []), 'weekly_items': week.get('items', []),
                                   'last_success_at': sector_receipts.get(f'{country}:1d', {}).get('last_success_at'),
                                   'weekly_last_success_at': sector_receipts.get(f'{country}:1w', {}).get('last_success_at'),
                                   'weekly_first_seen_at': week.get('first_seen_at'), 'weekly_provider_at': None,
                                   'weekly_source_url': week.get('source_url'),
                                   'provider_at': None, 'quality': 'ANALYSIS_ONLY'}
    receipts = _read('catalog_receipt', as_of)
    catalog_status = {market: 'STALE' if not receipts.get(market, {}).get('last_success_at') or
                      _stamp(receipts[market]['last_success_at']).date() != clock.date()
                      else row.get('status', 'PARTIAL') for market, row in catalog.items()}
    active_catalog = all(catalog_status.get(market) == 'READY' for market in MARKETS)
    stale_catalog = bool(catalog_status) and all(status == 'STALE' for status in catalog_status.values())
    return {'as_of': clock.isoformat(), 'source': 'toss', 'provider': 'Toss official catalog / REST prices',
        'universe': {'status': 'READY' if active_catalog and catalog_symbols else 'STALE' if stale_catalog else 'PARTIAL' if symbols else 'UNAVAILABLE', 'scope': SCOPE,
            'expected': len(catalog_symbols), 'observed': sum(row['quote']['price'] is not None for row in catalog_output),
            'classified': classified, 'unclassified': len(catalog_symbols) - classified,
            'quote_fresh': sum(row['quote']['status'] == 'LIVE' for row in catalog_output),
            'missing': sum(row['quote']['price'] is None for row in catalog_output),
            'stale': sum(row['quote']['status'] == 'STALE' for row in catalog_output),
            'bootstrap_observed': len(ranked_symbols - catalog_symbols),
            'markets': list(catalog), 'started': health.get('started_at'), 'finished': health.get('finished_at'),
            'catalog_first_seen_at': {market: row['first_seen_at'] for market, row in catalog.items()},
            'catalog_last_success_at': {market: row['last_success_at'] for market, row in receipts.items()},
            'catalog_status': catalog_status},
        'stocks': output, 'sectors': sectors, 'memberships': memberships, 'sector_rankings': sector_rankings,
        'global_rankings': global_rankings, 'calendar': calendars,
        'technical': _read('analysis', as_of), 'health': health,
        'measurement_definition': 'Catalog-wide quoted prices, separate timestamped Top100 turnover, and un-timestamped sector statistics; no net-buy inference'}
