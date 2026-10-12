"""Persist exact cumulative-turnover differences, never close times volume.

This hooks into the existing single authenticated Toss client. The ranking's
provider timestamp is required; receipt time is not a substitute for it.
"""
from datetime import datetime, timedelta
import json
import math

from app.radar.models import FlowObservation, HistoricalFlowObservation, KST
from app.radar.sessions import current_session, parsed_time
from app.radar.storage import connection, encoded, identity, save_observations, save_provider_health
from app.radar.config import read_v12_config


def collect_rankings(result, quotes, calendar, first_seen_at):
    first_seen_at = first_seen_at.astimezone(KST)
    source = 'https://openapi.tossinvest.com/api/v1/rankings'
    ranked_at = result.get('rankedAt')
    if not ranked_at:
        save_provider_health('TOSS_TURNOVER', {'status': 'UNAVAILABLE', 'provider': 'Toss REST',
            'reason': 'Official ranking timestamp is unavailable; receipt time is not substituted.'}, first_seen_at)
        return []
    end = parsed_time(ranked_at)
    if end > first_seen_at:
        save_provider_health('TOSS_TURNOVER', {'status': 'UNAVAILABLE', 'provider': 'Toss REST',
            'reason': 'Provider ranking time is in the future.'}, first_seen_at)
        return []
    limit = read_v12_config()['data']['max_live_quote_age_seconds']
    if (first_seen_at - end).total_seconds() > limit:
        save_provider_health('TOSS_TURNOVER', {'status': 'STALE', 'provider': 'Toss REST',
            'reason': 'Ranking time exceeds the freshness limit; a fresh quote does not refresh turnover.'}, first_seen_at)
        return []
    session = current_session(calendar, end)
    if session is None:
        return []
    observed = []
    with connection() as conn:
        for row in result.get('rankings', []):
            symbol = row['symbol']
            quote = quotes.get(symbol)
            if not quote or not quote.get('timestamp'):
                continue
            amount = float(row['tradingAmount'])
            price = float(row['price']['lastPrice'])
            if not math.isfinite(amount) or amount < 0 or not math.isfinite(price) or price <= 0:
                continue
            quote_at = parsed_time(quote['timestamp'])
            if quote_at > first_seen_at:
                continue
            previous = conn.execute('''SELECT payload_json FROM turnover_samples WHERE symbol=?
                AND session_id=? AND session_date=? AND mode='live' AND as_of<?
                ORDER BY as_of DESC,first_seen_at DESC,rowid DESC LIMIT 1''',
                (symbol, session.session_id, end.date().isoformat(), end.isoformat())).fetchone()
            payload = {'symbol': symbol, 'turnover_krw': amount, 'price': price, 'as_of': end.isoformat(),
                       'first_seen_at': first_seen_at.isoformat(), 'quote_at': quote_at.isoformat(),
                       'provider': 'Toss REST MARKET_TRADING_AMOUNT', 'source_url': source,
                       'session_start': session.start.isoformat(), 'session_end': session.end.isoformat()}
            conn.execute('INSERT OR IGNORE INTO turnover_samples VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (identity(symbol, end, 'CONSOLIDATED', 'live', amount, price, first_seen_at), symbol, session.session_id, end.date().isoformat(),
                 end.isoformat(), first_seen_at.isoformat(), 'live', encoded(payload)))
            if previous:
                prev = json.loads(previous['payload_json'])
                start = parsed_time(prev['as_of'])
                if session.start <= start < end and amount >= prev['turnover_krw']:
                    observed.append(FlowObservation(symbol=symbol, observed_at=end, first_seen_at=first_seen_at,
                        interval_start=start, interval_end=end, session_id=session.session_id,
                        venue='CONSOLIDATED', turnover_krw=amount - prev['turnover_krw'],
                        price_open=prev['price'], price_close=price, quote_at=quote_at,
                        provider='Toss REST MARKET_TRADING_AMOUNT', source_url=source, mode='live'))
    save_observations(observed)
    save_provider_health('TOSS_TURNOVER', {'status': 'LIVE' if observed else 'PARTIAL', 'provider': 'Toss REST',
        'last_success_at': end.isoformat(), 'latency_seconds': (first_seen_at - end).total_seconds(),
        'coverage': {'symbols_seen': len(observed), 'symbols_expected': len(result.get('rankings', [])),
                     'scope': 'queried_ranked_symbols'},
        'reason': 'Observed differences of integrated cumulative turnover; not investor net buying.',
        'calendar': calendar, 'session_start': session.start.isoformat(), 'session_id': session.session_id}, first_seen_at)
    return observed


def historical_windows(observations, as_of, session_id, minute_from_session, window_minutes=5):
    """Build baselines only from fully covered, actually stored past intervals."""
    groups = {}
    for row in observations:
        if row.mode != 'live' or row.interval_end.date() >= as_of.date() or row.session_id != session_id:
            continue
        groups.setdefault((row.symbol, row.interval_end.date(), row.venue, row.provider), []).append(row)
    history = []
    for (symbol, day, venue, provider), rows in groups.items():
        from app.radar.flow import _interval_rows
        rows, error = _interval_rows(rows)
        if error:
            continue
        rows.sort(key=lambda item: item.interval_end)
        # The session start for this observed trading day is provider-derived.
        with connection() as conn:
            saved = conn.execute('''SELECT payload_json FROM turnover_samples WHERE symbol=?
                AND session_date=? AND session_id=? AND mode='live' LIMIT 1''',
                (symbol, day.isoformat(), session_id)).fetchone()
        if not saved:
            continue
        start = parsed_time(json.loads(saved['payload_json'])['session_start'])
        end = start + timedelta(minutes=minute_from_session)
        left = end - timedelta(minutes=window_minutes)
        selected = [row for row in rows if left <= row.interval_start and row.interval_end <= end]
        cursor = left
        for row in selected:
            if row.interval_start != cursor or row.bar_status != 'COMPLETE':
                break
            cursor = row.interval_end
        if not selected or cursor != end:
            continue
        history.append(HistoricalFlowObservation(symbol=symbol, session_id=session_id, session_date=day,
            minute_from_session=minute_from_session, window_minutes=window_minutes,
            turnover_krw=sum(row.turnover_krw for row in selected),
            observed_at=end, first_seen_at=max(row.first_seen_at for row in selected),
            venue=venue, provider=provider, source_url=selected[-1].source_url, mode='live'))
    return history
