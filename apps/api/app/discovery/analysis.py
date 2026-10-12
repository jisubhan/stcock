"""Pure analysis of actual discovery observations, independent of legacy bundles.

Watch priority is an explainable ordering, never a return probability or an
investor-net-buy estimate. Missing observations stay missing. No transport,
authentication, storage, fixture data, or order calls are made here.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import math
from statistics import mean
from typing import Any, Mapping, Sequence

from app.settings import StrategyConfig
from app.strategy.engine import candles_with_ma, detect_box, moving_average, significant_candle
from app.strategy.intraday import (
    DailyStructure, EarlyEntryInput, EntryGates, MarketSession, PointInTimeQuote,
    PriceLevel, aware, build_five_minute_bars, evaluate_early_entry,
)


def _number(value: Any, *, positive=False, nonnegative=False) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(number) or (positive and number <= 0) or (nonnegative and number < 0):
        return None
    return number


def _time(value: Any) -> datetime | None:
    try:
        return aware(value) if value is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _actual(row: Mapping) -> bool:
    # The public function accepts the normalized official discovery contract.
    # Explicit contradictory provenance must never be relabelled as actual.
    return (row.get('source', 'toss') == 'toss'
            and row.get('mode', 'live') == 'live' and row.get('currency', 'KRW') == 'KRW'
            and row.get('venue', 'CONSOLIDATED') in ('CONSOLIDATED', 'integrated', 'INTEGRATED')
            and row.get('integrated', True) is True)


def _available_metadata(row: Mapping, clock: datetime, *, require_seen=False, price_feed=True) -> bool:
    if (not _actual(row) if price_feed else row.get('source', 'toss') != 'toss' or row.get('mode', 'live') != 'live'):
        return False
    if require_seen and _time(row.get('first_seen_at')) is None:
        return False
    for key in ('first_seen_at', 'as_of', 'observed_at', 'provider_at', 'revision_available_at',
                'provider_revision_at', 'quote_at', 'timestamp'):
        if row.get(key) is not None:
            stamp = _time(row[key])
            if stamp is None or stamp > clock:
                return False
    return True


def _quote(raw: Mapping | None, clock: datetime) -> dict | None:
    if not isinstance(raw, Mapping) or not _available_metadata(raw, clock):
        return None
    price = _number(raw.get('price', raw.get('lastPrice')), positive=True)
    seen = _time(raw.get('first_seen_at'))
    stamp = _time(raw.get('quote_at', raw.get('timestamp')))
    if price is None or seen is None or seen > clock or (stamp is not None and stamp > clock):
        return None
    if raw.get('quote_at', raw.get('timestamp')) is not None and stamp is None:
        return None
    age = (clock - stamp).total_seconds() if stamp else None
    status = ('LIVE' if raw.get('status') == 'LIVE' and age is not None and 0 <= age <= 15
              else 'STALE' if age is not None and age > 15 or raw.get('status') == 'STALE'
              else 'ANALYSIS_ONLY')
    return {'price': price, 'quote_at': stamp.isoformat() if stamp else None,
            'first_seen_at': seen.isoformat(), 'status': status, 'latency_seconds': age,
            'source': 'toss', 'venue': 'CONSOLIDATED', 'currency': 'KRW'}


def _ohlcv(row: Mapping) -> dict | None:
    values = {name: _number(row.get(name, row.get(name + 'Price')), positive=True)
              for name in ('open', 'high', 'low', 'close')}
    values['volume'] = _number(row.get('volume'), nonnegative=True)
    if any(value is None for value in values.values()):
        return None
    if (values['low'] > min(values['open'], values['close'])
            or values['high'] < max(values['open'], values['close']) or values['low'] > values['high']):
        return None
    traded = _number(row.get('tradedValue', row.get('tradingAmount')), nonnegative=True)
    if traded is not None:
        values['tradedValue'] = traded
    return values


def _daily(rows, clock, batch_seen, previous_day, sessions, warnings):
    visible: dict[str, dict] = {}
    conflicts = set()
    batch = _time(batch_seen)
    if batch is not None and batch > clock:
        return [], []
    try:
        cutoff = date.fromisoformat(previous_day) if previous_day else None
    except (ValueError, TypeError):
        cutoff = None
        warnings.append('INVALID_PREVIOUS_BUSINESS_DAY')
    ordered = sorted(rows, key=lambda item: (_time(item.get('first_seen_at', batch)) or clock)
                     if isinstance(item, Mapping) else clock)
    for raw in ordered:
        if not isinstance(raw, Mapping):
            continue
        stamp = raw.get('time', raw.get('timestamp'))
        provider_stamp = _time(stamp) if not (isinstance(stamp, str) and len(stamp) == 10) else None
        if provider_stamp is not None and provider_stamp > clock:
            continue
        try:
            # A provider timestamp has a timezone; normalized dates are allowed.
            day = date.fromisoformat(stamp) if isinstance(stamp, str) and len(stamp) == 10 else _time(stamp).date()
        except (ValueError, TypeError, AttributeError):
            continue
        seen = _time(raw.get('first_seen_at', batch))
        provider_at = _time(raw.get('observed_at', raw.get('provider_at')))
        if day > clock.date() or seen is None or seen > clock or (provider_at and provider_at > clock):
            continue
        if not _available_metadata(raw, clock):
            warnings.append('DAILY_SOURCE_OR_VENUE_MISMATCH')
            continue
        values = _ohlcv(raw)
        if values is None:
            warnings.append('INVALID_DAILY_OHLCV')
            continue
        is_today = day == clock.date()
        today_sessions = [s for s in sessions if s.start.date() == day]
        verified_today_close = bool(today_sessions and clock >= max(s.end for s in today_sessions)
                                    and seen >= max(s.end for s in today_sessions)
                                    and raw.get('status') == 'CLOSED')
        complete = (raw.get('status') != 'PARTIAL' and
                    ((not is_today and (day <= cutoff if cutoff is not None else raw.get('status') == 'CLOSED'))
                     or is_today and verified_today_close))
        row = {**values, 'time': day.isoformat(), 'first_seen_at': seen.isoformat(),
               'source': 'toss', 'venue': 'CONSOLIDATED', 'integrated': True,
               'interval': '1d', 'status': 'CLOSED' if complete else 'PARTIAL'}
        if (row['time'], row['first_seen_at']) in conflicts:
            continue
        old = visible.get(row['time'])
        if old is None or row['first_seen_at'] > old['first_seen_at']:
            visible[row['time']] = row
        elif row['first_seen_at'] == old['first_seen_at'] and row != old:
            warnings.append('CONFLICTING_DAILY_REVISION')
            conflicts.add((row['time'], row['first_seen_at']))
            visible.pop(row['time'], None)
    daily = sorted(visible.values(), key=lambda row: row['time'])
    return daily, [row for row in daily if row['status'] == 'CLOSED']


def _minutes(rows, clock, batch_seen, warnings):
    batch = _time(batch_seen)
    if batch is not None and batch > clock:
        return []
    visible = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        end = _time(raw.get('end', raw.get('time', raw.get('timestamp'))))
        seen = _time(raw.get('first_seen_at', batch))
        if end is None or seen is None or end > clock or seen > clock:
            continue
        if not _available_metadata(raw, clock) or raw.get('timestamp_semantics', 'END') != 'END':
            warnings.append('MINUTE_SOURCE_VENUE_OR_TIME_SEMANTICS_MISMATCH')
            continue
        values = _ohlcv(raw)
        if values is None or end.second or end.microsecond:
            warnings.append('INVALID_MINUTE_OHLCV')
            continue
        status = 'CLOSED' if raw.get('status', 'CLOSED') == 'CLOSED' and seen >= end else 'PARTIAL'
        visible.append({**values, 'time': end.isoformat(), 'end': end.isoformat(),
                        'first_seen_at': seen.isoformat(), 'source': 'toss', 'venue': 'CONSOLIDATED',
                        'integrated': True, 'timestamp_semantics': 'END', 'interval': '1m', 'status': status})
    revisions = {}
    conflicts = set()
    for row in sorted(visible, key=lambda item: (item['time'], item['first_seen_at'])):
        if (row['time'], row['first_seen_at']) in conflicts:
            continue
        old = revisions.get(row['time'])
        if old and old['first_seen_at'] == row['first_seen_at'] and old != row:
            warnings.append('CONFLICTING_MINUTE_REVISION')
            conflicts.add((row['time'], row['first_seen_at']))
            revisions.pop(row['time'], None)
        else:
            revisions[row['time']] = row
    return sorted(revisions.values(), key=lambda item: item['time'])


def _flow(rows, clock):
    """Only complete, fresh, actual intervals can supply turnover velocity."""
    visible = {}
    conflicts = set()
    normalized = [item.model_dump(mode='json') if hasattr(item, 'model_dump') else item for item in rows]
    ordered = sorted(normalized, key=lambda row: (_time(row.get('first_seen_at')) or clock,
                      _time(row.get('observed_at')) or clock) if isinstance(row, Mapping) else (clock, clock))
    for row in ordered:
        if not isinstance(row, Mapping) or not _available_metadata(row, clock):
            continue
        start, end = _time(row.get('interval_start')), _time(row.get('interval_end'))
        seen, observed, quote_at = (_time(row.get(key)) for key in ('first_seen_at', 'observed_at', 'quote_at'))
        turnover = _number(row.get('turnover_krw'), nonnegative=True)
        if (None in (start, end, seen, observed, quote_at) or turnover is None or end <= start
                or max(end, seen, observed, quote_at) > clock or clock > end + timedelta(minutes=5)
                or (clock - quote_at).total_seconds() > 15 or row.get('bar_status', 'COMPLETE') != 'COMPLETE'
                or row.get('venue') != 'CONSOLIDATED'):
            continue
        key = (row.get('symbol'), start, end, row.get('session_id'), row.get('provider'), row.get('source_url'))
        revision = (seen, observed)
        old = visible.get(key)
        if (key, revision) in conflicts:
            continue
        value = (end, seen, observed, turnover / ((end - start).total_seconds() / 60), row)
        if old is None or revision > (old[1], old[2]):
            visible[key] = value
        elif revision == (old[1], old[2]) and turnover != float(old[4]['turnover_krw']):
            conflicts.add((key, revision))
            visible.pop(key, None)
    if not visible:
        return None
    _, _, _, velocity, row = max(visible.values(), key=lambda item: (item[0], item[1], item[2]))
    return {'turnover_krw_per_min': velocity, 'turnover_krw': float(row['turnover_krw']),
            'interval_start': row['interval_start'], 'interval_end': row['interval_end'],
            'first_seen_at': row['first_seen_at'], 'source_url': row.get('source_url'),
            'measurement_definition': 'Observed interval trading amount; not investor net buying'}


def analyze_discovery_stock(*, symbol: str, name: str, as_of: datetime | str,
                            quote: Mapping | None, daily_candles: Sequence[Mapping] = (),
                            minute_candles: Sequence[Mapping] = (), sessions: Sequence = (),
                            theme_context: Mapping | None = None, flow_observations: Sequence = (),
                            investor_netbuy: Mapping | None = None, entry_gates: Mapping | None = None,
                            config: StrategyConfig | None = None, previous_business_day: str | None = None,
                            daily_first_seen_at: datetime | str | None = None,
                            minute_first_seen_at: datetime | str | None = None) -> dict:
    clock, settings = aware(as_of), config if config is not None else StrategyConfig()
    warnings, reasons = [], []
    calendar = []
    for raw in sessions:
        try:
            calendar.append(raw if isinstance(raw, MarketSession) else MarketSession(**raw))
        except (TypeError, ValueError):
            warnings.append('INVALID_OFFICIAL_SESSION')
    actual_quote = _quote(quote, clock)
    if actual_quote is None:
        reasons.append('QUOTE_UNAVAILABLE_AT_AS_OF')
    daily, closed = _daily(daily_candles, clock, daily_first_seen_at, previous_business_day, calendar, warnings)
    minute = _minutes(minute_candles, clock, minute_first_seen_at, warnings)
    try:
        bars = build_five_minute_bars(minute, calendar, clock, venue='CONSOLIDATED')
    except (ValueError, TypeError, KeyError):
        bars = []
        warnings.append('FIVE_MINUTE_OBSERVATIONS_INVALID')
    technical = {key: None for key in ('ma5', 'ma20', 'pivot', 'boxHigh', 'boxLow', 'box',
                 'significantCandle', 'significantCandleDate', 'distanceToMa5Pct', 'trendUp',
                 'previousClose', 'previousCloseDate', 'structuralStop', 'firstResistance', 'target', 'riskReward')}
    technical.update(shortPeriod=settings.moving_average.short, mediumPeriod=settings.moving_average.medium)
    daily_ready = len(closed) >= max(20, settings.moving_average.medium)
    chart_score = None
    supports = []
    structure_seen = max((_time(row['first_seen_at']) for row in closed), default=None)
    if daily_ready:
        ma5 = moving_average(closed, settings.moving_average.short)
        ma20 = moving_average(closed, settings.moving_average.medium)
        previous_ma5 = moving_average(closed[:-1], settings.moving_average.short)
        sig = None
        # The legacy helper's denominator requires non-zero prior volume.
        if all(sum(row['volume'] for row in closed[max(0, i - 20):i]) > 0 for i in range(20, len(closed))):
            sig = significant_candle(closed, settings)
        else:
            warnings.append('SIGNIFICANT_CANDLE_VOLUME_BASELINE_UNAVAILABLE')
        try:
            box = detect_box(closed, settings, sig)
        except (ValueError, ZeroDivisionError):
            box = None
            warnings.append('BOX_VOLUME_STRUCTURE_UNAVAILABLE')
        previous = next((row for row in reversed(closed) if row['time'] == previous_business_day), None) if previous_business_day else closed[-1]
        trend = ma5 >= previous_ma5 and ma5 > ma20
        price = actual_quote['price'] if actual_quote else None
        pivot = sig['close'] if sig else box['high'] if box and box['confirmed'] else None
        technical.update(ma5=ma5, ma20=ma20, trendUp=trend, pivot=pivot,
                         box=box, boxHigh=box['high'] if box else None, boxLow=box['low'] if box else None,
                         significantCandle=sig, significantCandleDate=sig['time'] if sig else None,
                         distanceToMa5Pct=abs(price / ma5 - 1) * 100 if price else None,
                         previousClose=previous['close'] if previous else None,
                         previousCloseDate=previous['time'] if previous else None)
        if sig and sig.get('tradedValueSource') == 'close_times_volume_estimate':
            warnings.append('SIGNIFICANT_CANDLE_TRADING_AMOUNT_IS_PRICE_VOLUME_ESTIMATE')
        supports = [PriceLevel('MA5_DAILY', ma5)]
        if previous:
            supports.append(PriceLevel('직전 완료 거래일 종가', previous['close']))
        if box and box['confirmed']:
            supports.extend((PriceLevel('확인된 박스 상단', box['high']), PriceLevel('확인된 박스 하단', box['low'])))
        if sig:
            supports.extend((PriceLevel('기준봉 시가', sig['open']), PriceLevel('기준봉 종가', sig['close'])))
        # First resistance belongs to the daily support structure, not today's
        # moving quote. Advancing the quote above it must not move the goalpost
        # to a higher resistance and restore a pre-breakout entry state.
        resistances = sorted(set(level for level in ([box['high']] if box and box['confirmed'] else [])
                                 + ([sig['close'], sig['high']] if sig else []) if level > ma5))
        stops = [level for level in ([box['low']] if box and box['confirmed'] else [])
                 + ([sig['open'], sig['low']] if sig else []) if price and level < price]
        stop = max(stops) * (1 - settings.risk.stop_buffer_pct / 100) if stops else None
        first = resistances[0] if resistances else None
        target = resistances[-1] if resistances and price and resistances[-1] > price else None
        technical.update(structuralStop=stop, firstResistance=first, target=target,
                         riskReward=(target - price) / (price - stop) if target and stop and price > stop else None)
        chart_score = 100 * (.4 * int(trend) + .2 * int(sig is not None)
                            + .2 * int(bool(box and box['confirmed']))
                            + .2 * int(bool(price and abs(price / ma5 - 1) * 100 <= settings.entry.ma5_distance_pct)))
        reasons.append('COMPLETED_DAILY_TECHNICAL_STRUCTURE')
    else:
        reasons.append('INSUFFICIENT_COMPLETED_DAILY_BARS')
    # Partial daily candles remain visible but never enter daily indicators.
    annotated = candles_with_ma(closed, settings.moving_average.short) if daily_ready else closed
    by_day = {row['time']: row for row in annotated}
    chart_daily = [by_day.get(row['time'], {**row, 'ma5': None}) for row in daily]
    theme = dict(theme_context) if theme_context is not None else None
    if theme and not _available_metadata(theme, clock):
        theme = None
        warnings.append('THEME_UNAVAILABLE_AT_AS_OF')
    same_symbol_flows = []
    for raw in flow_observations:
        row = raw.model_dump(mode='json') if hasattr(raw, 'model_dump') else raw
        if isinstance(row, Mapping) and row.get('symbol') == symbol:
            same_symbol_flows.append(row)
    flow = _flow(same_symbol_flows, clock)
    netbuy = None
    if isinstance(investor_netbuy, Mapping):
        seen, observed = _time(investor_netbuy.get('first_seen_at')), _time(investor_netbuy.get('observed_at'))
        if (seen and observed and max(seen, observed) <= clock and investor_netbuy.get('source', 'toss') == 'toss'
                and _available_metadata(investor_netbuy, clock, price_feed=False) and investor_netbuy.get('symbol') == symbol
                and investor_netbuy.get('unit') in ('SHARES', 'KRW')
                and investor_netbuy.get('venue') in ('KRX', 'NXT', 'CONSOLIDATED')):
            netbuy = dict(investor_netbuy)
    raw_gates = entry_gates if isinstance(entry_gates, Mapping) else {}
    if raw_gates and (not _available_metadata(raw_gates, clock, require_seen=True)
                      or (clock - _time(raw_gates['first_seen_at'])).total_seconds() > 15):
        raw_gates = {}
        warnings.append('ENTRY_GATES_SOURCE_TIME_OR_FRESHNESS_UNVERIFIED')
    gates = {key: raw_gates.get(key) for key in EntryGates.__dataclass_fields__}
    # A theme hypothesis or official sector membership is not flow confirmation.
    gates['theme_confirmed'] = bool(gates['theme_confirmed']) if (type(gates['theme_confirmed']) is bool
        and theme and theme.get('observed_flow_confirmed') is True) else None
    if flow is None:
        gates['turnover_krw_per_min'] = None
    elif gates['turnover_krw_per_min'] is not None:
        liquidity = _number(gates['turnover_krw_per_min'], nonnegative=True)
        gates['turnover_krw_per_min'] = min(flow['turnover_krw_per_min'], liquidity) if liquidity is not None else None
    levels = DailyStructure(technical['ma5'], technical['boxHigh'], technical['boxLow'], technical['pivot'],
                            technical['firstResistance'], technical['structuralStop'], technical['target'],
                            tuple(supports), tuple(PriceLevel(f"{row['time']} {side.upper()}", row[side])
                            for row in closed[-5:] for side in ('open', 'close')) if daily_ready else (), structure_seen)
    point_quote = (PointInTimeQuote(actual_quote['price'], _time(actual_quote['quote_at']),
                   _time(actual_quote['first_seen_at']), 'CONSOLIDATED', 'toss',
                   'LIVE' if actual_quote['status'] == 'LIVE' else 'STALE')
                   if actual_quote and actual_quote['quote_at'] else None)
    # Previous official sessions are visible in the chart at night/pre-open,
    # but cannot provide today's recovery/entry proof.
    current_bars = [bar for bar in bars if bar.start.date() == clock.date()]
    early = evaluate_early_entry(EarlyEntryInput(clock, point_quote, current_bars, levels, EntryGates(**gates), symbol))
    # Fixed maxima: missing factors do not inflate the remaining weights.
    factors = {}
    theme_score = _number((theme or {}).get('hypothesis_score', (theme or {}).get('score')))
    values = {'theme_context': (max(0, min(100, theme_score)) if theme_score is not None else None, 30),
              'quote_quality': (100 if actual_quote and actual_quote['status'] == 'LIVE' else 25 if actual_quote else None, 20),
              'chart_structure': (chart_score, 30),
              'observed_turnover': (min(100, flow['turnover_krw_per_min'] / 100_000_000 * 100) if flow else None, 20)}
    for key, (value, weight) in values.items():
        factors[key] = {'value': value, 'points': value * weight / 100 if value is not None else None, 'max_points': weight}
    available = sum(row['max_points'] for row in factors.values() if row['value'] is not None)
    score = round(sum(row['points'] or 0 for row in factors.values()), 2) if available else None
    quality = {'quote': actual_quote['status'] if actual_quote else 'UNAVAILABLE',
               'daily': 'READY' if daily_ready else 'PARTIAL' if daily else 'UNAVAILABLE',
               'minute': 'READY' if minute else 'UNAVAILABLE',
               'five_minute': 'READY' if bars and any(bar.status == 'CLOSED' for bar in bars) else 'PARTIAL' if bars else 'UNAVAILABLE',
               'flow': 'OBSERVED' if flow else 'UNAVAILABLE', 'investor_netbuy': 'OBSERVED' if netbuy else 'UNAVAILABLE',
               'entry': early['quality']}
    return {'symbol': symbol, 'name': name, 'as_of': clock.isoformat(), 'source': 'toss', 'mode': 'live',
            'quote': actual_quote, 'theme': theme, 'watch_score': {'score': score, 'coverage': available / 100,
             'available_max_points': available, 'max_points': 100, 'factors': factors,
             'definition': '관찰 우선순위 · 수익확률 또는 진입 확정 아님'},
            'recommendation_kind': 'THEME_WATCH', 'technical': technical,
            'chart': {'daily': chart_daily, 'minute': minute, 'five_minute': [bar.to_dict() for bar in bars],
                      'levels': {key: value for key, value in {'ma5': technical['ma5'], 'pivot': technical['pivot'],
                         'boxHigh': technical['boxHigh'], 'boxLow': technical['boxLow'], 'stop': technical['structuralStop'],
                         'target1': technical['firstResistance'], 'target2': technical['target']}.items() if value is not None}},
            'earlyEntry': early, 'flow': flow, 'investor_netbuy': netbuy, 'quality': quality,
            'reasons': list(dict.fromkeys(reasons)), 'warnings': list(dict.fromkeys(warnings)), 'orders_enabled': False}
