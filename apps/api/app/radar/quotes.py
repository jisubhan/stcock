"""Actual quote cache independent of turnover, history and recommendation gates.

Reads never authenticate or issue HTTP. Unknown/stale prices are never replaced
with demo values. Provider quote time and first receipt time stay separate.
"""
from datetime import datetime
import json
import math
import os
import re

from app.radar.config import read_v12_config
from app.radar.models import KST, aware
from app.radar.sessions import current_session, parsed_time
from app.radar.storage import connection, encoded, identity, read_state, save_provider_health
from app.settings import runtime_dir

PROVIDER = 'Toss REST · KRX+NXT 통합'
SOURCE_URL = 'https://openapi.tossinvest.com/api/v1/prices'
DEFINITION = '공급자 현재가, 거래대금/순매수 아님'


def _stamp(value):
    try:
        return parsed_time(value) if isinstance(value, str) else aware(value) if isinstance(value, datetime) else None
    except (ValueError, TypeError):
        return None


def _auth_allowed():
    if os.environ.get('TOSS_DATA_MODE', 'auto').lower() == 'demo':
        return False
    try:
        report = json.loads((runtime_dir() / 'toss-probe.json').read_text())
        auth = report.get('authentication', {})
        return (auth.get('status') in ('passed', 'success', 'ready') and report.get('venue', {}).get('verified') is True
                and auth.get('condition') not in ('ip_not_allowed', 'invalid_client', 'access_denied', 'authentication_rejected'))
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _health(health, received):
    with connection() as conn:
        conn.execute('INSERT OR IGNORE INTO quote_monitor_health VALUES (?,?,?)',
                     (identity(received, health), received.isoformat(), encoded(health)))
    save_provider_health('TOSS_QUOTES', health, received)


def mark_quotes_unavailable(received_at, reason, status='UNAVAILABLE', **extras):
    """Fixed, caller-owned diagnostic text only; never pass raw provider errors."""
    received = aware(received_at)
    if status not in ('UNAVAILABLE', 'STALE', 'PARTIAL'):
        raise ValueError('Invalid quote failure status')
    previous = {}
    with connection() as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quote_monitor_health'").fetchone():
            row = conn.execute('SELECT payload_json FROM quote_monitor_health WHERE observed_at<=? ORDER BY observed_at DESC,rowid DESC LIMIT 1', (received.isoformat(),)).fetchone()
            if row:
                previous = json.loads(row['payload_json'])
    health = {'status': status, 'provider': PROVIDER, 'reason': reason, 'available': False,
              'last_success_at': None, 'latency_seconds': None, 'measurement_definition': DEFINITION,
              'requested_symbols': previous.get('requested_symbols', []), 'fresh_symbols': [],
              **{key: value for key, value in extras.items() if key in ('poll_interval_seconds', 'http_status', 'retry_after_seconds')}}
    _health(health, received)
    return health


def save_quotes(rows, received_at, requested_symbols, poll_interval_seconds=5):
    """Append validated observed prices and separate quote-specific feed health."""
    received = aware(received_at)
    requested = list(dict.fromkeys(symbol for symbol in requested_symbols if isinstance(symbol, str) and re.fullmatch(r'[A-Z0-9]{6}', symbol)))[:200]
    maximum_age = float(read_v12_config()['data']['max_live_quote_age_seconds'])
    valid, fresh = [], set()
    for row in rows:
        if not isinstance(row, dict) or row.get('symbol') not in requested or row.get('currency') != 'KRW' or isinstance(row.get('lastPrice'), bool):
            continue
        try:
            price = float(row['lastPrice'])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(price) or price <= 0:
            continue
        stamp = _stamp(row.get('timestamp'))
        if row.get('timestamp') is not None and stamp is None:
            continue
        if stamp is not None and stamp > received:
            continue
        value = {'symbol': row['symbol'], 'price': price, 'currency': 'KRW',
                 'quote_at': stamp.isoformat() if stamp else None, 'first_seen_at': received.isoformat(),
                 'source': 'toss', 'provider': PROVIDER, 'source_url': SOURCE_URL,
                 'change_pct': None, 'nxt_supported': None}
        valid.append(value)
        if stamp is not None and 0 <= (received - stamp).total_seconds() <= maximum_age:
            fresh.add(row['symbol'])
    with connection() as conn:
        for value in valid:
            # Repeated reception of the identical provider quote does not move
            # first_seen_at forward or add another invented quote event.
            key = identity(value['symbol'], value['quote_at'], value['price'], value['currency'])
            conn.execute('INSERT OR IGNORE INTO quote_observations VALUES (?,?,?,?,?)',
                         (key, value['symbol'], value['quote_at'], received.isoformat(), encoded(value)))
            saved = conn.execute('SELECT quote_at,first_seen_at,payload_json FROM quote_observations WHERE observation_id=?', (key,)).fetchone()
            conn.execute('''INSERT INTO quote_latest VALUES (?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET
                quote_at=excluded.quote_at,first_seen_at=excluded.first_seen_at,payload_json=excluded.payload_json
                WHERE (excluded.quote_at IS NOT NULL AND (quote_latest.quote_at IS NULL OR excluded.quote_at>quote_latest.quote_at
                  OR (excluded.quote_at=quote_latest.quote_at AND excluded.first_seen_at>=quote_latest.first_seen_at)))
                OR (excluded.quote_at IS NULL AND quote_latest.quote_at IS NULL AND excluded.first_seen_at>=quote_latest.first_seen_at)''',
                (value['symbol'], saved['quote_at'], saved['first_seen_at'], saved['payload_json']))
    calendar = read_state('calendar:' + received.date().isoformat(), received) or {}
    session = current_session(calendar, received) if calendar else None
    status = 'LIVE' if fresh and len(fresh) == len(requested) and session else 'PARTIAL' if fresh and session else 'STALE' if valid else 'UNAVAILABLE'
    health = {'status': status, 'provider': PROVIDER, 'available': bool(valid),
              'last_success_at': max((value['quote_at'] for value in valid if value['quote_at']), default=None),
              'latency_seconds': max(((received - _stamp(value['quote_at'])).total_seconds() for value in valid if value['quote_at']), default=None),
              'coverage': {'symbols_seen': len({value['symbol'] for value in valid}), 'symbols_expected': len(requested),
                           'fresh_symbols': len(fresh), 'scope': 'Requested registered/news/ranking symbols; not the whole market'},
              'requested_symbols': requested, 'fresh_symbols': sorted(fresh),
              'poll_interval_seconds': poll_interval_seconds, 'measurement_definition': DEFINITION,
              'reason': '검증된 공급자 현재가입니다. 추천·거래대금·투자자 순매수와 별도로 표시합니다.' if fresh and session else
                        '장 종료 또는 공급자 시각 미제공·지연으로 마지막 실제 가격을 STALE로 표시합니다.' if valid else
                        '요청 범위에 유효한 공급자 현재가가 없습니다.'}
    _health(health, received)
    return health


def quotes_snapshot(as_of=None):
    """Read actual point-in-time observations only; no OAuth, HTTP or demo fallback."""
    clock = aware(as_of or datetime.now(KST))
    values, health = {}, {}
    with connection() as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quote_observations'").fetchone()
        if exists:
            saved = conn.execute('SELECT payload_json FROM quote_monitor_health WHERE observed_at<=? ORDER BY observed_at DESC,rowid DESC LIMIT 1', (clock.isoformat(),)).fetchone()
            if saved:
                health = json.loads(saved['payload_json'])
            if as_of is None:
                # Current public GET reads at most the bounded monitored universe,
                # not an ever-growing five-second observation history.
                requested = health.get('requested_symbols', [])[:200]
                if requested:
                    slots = ','.join('?' for _ in requested)
                    rows = conn.execute(f'SELECT payload_json FROM quote_latest WHERE first_seen_at<=? AND symbol IN ({slots}) LIMIT 200',
                                        (clock.isoformat(), *requested)).fetchall()
                else:
                    rows = conn.execute('SELECT payload_json FROM quote_latest WHERE first_seen_at<=? ORDER BY first_seen_at DESC LIMIT 200', (clock.isoformat(),)).fetchall()
            else:
                symbols = health.get('requested_symbols') or [row['symbol'] for row in conn.execute('SELECT symbol FROM quote_latest LIMIT 200')]
                rows = []
                for symbol in symbols[:200]:
                    row = conn.execute('''SELECT payload_json FROM quote_observations WHERE symbol=? AND first_seen_at<=?
                        ORDER BY quote_at DESC,first_seen_at DESC,rowid DESC LIMIT 1''', (symbol, clock.isoformat())).fetchone()
                    if row:
                        rows.append(row)
            for row in rows:
                value = json.loads(row['payload_json'])
                values[value['symbol']] = value
    calendar = read_state('calendar:' + clock.date().isoformat(), clock) or {}
    session = current_session(calendar, clock) if calendar else None
    allowed = _auth_allowed() and health.get('available', False) and health.get('status') in ('LIVE', 'PARTIAL')
    maximum_age = float(read_v12_config()['data']['max_live_quote_age_seconds'])
    requested = health.get('requested_symbols', [])
    items = []
    for symbol in requested or sorted(values):
        if symbol not in values:
            continue
        value = values[symbol]
        stamp = _stamp(value['quote_at'])
        age = (clock - stamp).total_seconds() if stamp else None
        live = bool(allowed and session and age is not None and 0 <= age <= maximum_age and symbol in health.get('fresh_symbols', []))
        items.append({**value, 'status': 'LIVE' if live else 'STALE', 'latency_seconds': age,
                      'reason': None if live else '장 종료·시세 지연·관측 누락 또는 연결 장애로 실시간으로 사용할 수 없습니다.'})
    count = sum(item['status'] == 'LIVE' for item in items)
    expected = len(requested) or len(items)
    status = 'LIVE' if count and count == expected else 'PARTIAL' if count else 'STALE' if items else 'UNAVAILABLE'
    reason = health.get('reason', '검증된 실제 현재가 관측이 없습니다.')
    if not _auth_allowed():
        reason = '인증·거래소 검증 또는 실제 수집 모드가 확인되지 않아 LIVE 표시를 중지합니다.'
    elif not session and items:
        reason = '현재 활성 장 세션이 없습니다. 마지막 실제 가격을 STALE로 표시합니다.'
    return {'as_of': clock.isoformat(), 'source': 'toss', 'provider': PROVIDER, 'status': status,
            'session_id': session.session_id if session else 'CLOSED', 'session_label': session.session_id if session else '장 종료 / 공식 장 일정 미확인',
            'is_open': bool(session), 'poll_interval_seconds': health.get('poll_interval_seconds', 5),
            'items': items, 'reason': reason, 'measurement_definition': DEFINITION,
            'coverage': {'symbols_seen': len(items), 'symbols_expected': expected, 'fresh_symbols': count,
                         'scope': 'Requested monitored symbols; not the whole market'}}
