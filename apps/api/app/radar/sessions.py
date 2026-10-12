"""Session boundaries come from the verified provider calendar, not the clock."""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.strategy.intraday import MarketSession

KST = ZoneInfo('Asia/Seoul')
LABELS = {'preMarket': 'NXT_PRE_MARKET', 'regularMarket': 'KRX_REGULAR', 'afterMarket': 'NXT_AFTER_MARKET'}


def parsed_time(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('An aware provider calendar timestamp is required.')
    return stamp.astimezone(KST)


def calendar_sessions(calendar, *, include_previous=False):
    days = [calendar.get('today', {})]
    if include_previous:
        days.insert(0, calendar.get('previousBusinessDay', {}))
    return [MarketSession(LABELS[key], parsed_time(times['startTime']), parsed_time(times['endTime']), 'CONSOLIDATED')
            for day in days for key, times in (day.get('integrated') or {}).items() if key in LABELS and times]


def current_session(calendar, as_of):
    return next((session for session in calendar_sessions(calendar) if session.start <= as_of < session.end), None)


def previous_close(calendar):
    integrated = calendar.get('previousBusinessDay', {}).get('integrated') or {}
    ends = [parsed_time(times['endTime']) for times in integrated.values() if isinstance(times, dict) and times.get('endTime')]
    return max(ends) if ends else None
