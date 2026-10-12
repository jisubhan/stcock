"""Point-in-time 5m observations and conservative pre-resistance entry states.

The legacy daily engine is intentionally independent. A broker minute's time is
its END, not its start. A 09:10 five-minute window means [09:10, 09:15); its
09:15 minute record cannot contribute before both it and its first observation
are available. No transport, authentication, order, or historical claims live here.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import math
from statistics import mean
from typing import Any, Literal, Mapping, Sequence
from zoneinfo import ZoneInfo


KST = ZoneInfo('Asia/Seoul')
Venue = Literal['KRX', 'NXT', 'CONSOLIDATED']
Quality = Literal['LIVE', 'DEMO', 'DELAYED', 'STALE', 'PARTIAL', 'UNAVAILABLE']


def aware(value: datetime | str) -> datetime:
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('An explicit timestamp timezone is required')
    return result.astimezone(KST)


def finite(value: Any, *, positive=False, nonnegative=False) -> float:
    if isinstance(value, bool):
        raise ValueError('A finite numeric observation is required')
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0) or (nonnegative and number < 0):
        raise ValueError('A finite numeric observation is required')
    return number


def venue_name(value: str) -> Venue:
    if value in ('integrated', 'INTEGRATED', 'CONSOLIDATED'):
        return 'CONSOLIDATED'
    if value in ('KRX', 'NXT'):
        return value
    raise ValueError('An explicit KRX, NXT, or CONSOLIDATED feed is required')


@dataclass(frozen=True)
class MarketSession:
    session_id: str
    start: datetime
    end: datetime
    venue: Venue = 'CONSOLIDATED'

    def __post_init__(self):
        object.__setattr__(self, 'start', aware(self.start))
        object.__setattr__(self, 'end', aware(self.end))
        if not self.session_id or self.end <= self.start or self.venue not in ('KRX', 'NXT', 'CONSOLIDATED'):
            raise ValueError('A valid explicit market session is required')


@dataclass(frozen=True)
class MinuteCandle:
    end: datetime
    first_seen_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    venue: Venue = 'CONSOLIDATED'
    source: str = 'synthetic'
    turnover_krw: float | None = None
    status: Literal['CLOSED', 'PARTIAL'] = 'CLOSED'

    def __post_init__(self):
        object.__setattr__(self, 'end', aware(self.end))
        object.__setattr__(self, 'first_seen_at', aware(self.first_seen_at))
        if self.end.second or self.end.microsecond or self.venue not in ('KRX', 'NXT', 'CONSOLIDATED'):
            raise ValueError('An aligned minute END timestamp and explicit venue are required')
        for name in ('open', 'high', 'low', 'close'):
            object.__setattr__(self, name, finite(getattr(self, name), positive=True))
        object.__setattr__(self, 'volume', finite(self.volume, nonnegative=True))
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ValueError('Minute OHLC observations are inconsistent')
        if self.turnover_krw is not None:
            object.__setattr__(self, 'turnover_krw', finite(self.turnover_krw, nonnegative=True))
        if not self.source:
            raise ValueError('An explicit observation source is required')
        if self.status not in ('CLOSED', 'PARTIAL'):
            raise ValueError('Minute completion status is required')

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> MinuteCandle:
        end = row.get('end', row.get('end_at', row.get('timestamp', row.get('time'))))
        seen = row.get('first_seen_at', row.get('seen_at'))
        if end is None or seen is None:
            raise ValueError('Minute END and first_seen_at are required')
        if row.get('timestamp_semantics', 'END') != 'END':
            raise ValueError('Convert START timestamps explicitly before using the END builder')
        return cls(end=end, first_seen_at=seen, open=row.get('open', row.get('openPrice')),
                   high=row.get('high', row.get('highPrice')), low=row.get('low', row.get('lowPrice')),
                   close=row.get('close', row.get('closePrice')), volume=row['volume'],
                   venue=venue_name(row['venue']), source=row['source'], turnover_krw=row.get('turnover_krw'), status=row.get('status', 'CLOSED'))


@dataclass(frozen=True)
class FiveMinuteBar:
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    status: Literal['CLOSED', 'PARTIAL']
    session_id: str
    venue: Venue
    source: str
    minute_count: int
    first_seen_at: datetime
    missing_minute_ends: tuple[datetime, ...] = ()
    turnover_krw: float | None = None
    ma5_5m: float | None = None

    def __post_init__(self):
        for name in ('start', 'end', 'first_seen_at'):
            object.__setattr__(self, name, aware(getattr(self, name)))
        if self.end - self.start != timedelta(minutes=5) or self.start.minute % 5 or self.start.second or self.start.microsecond:
            raise ValueError('Five-minute bars require aligned KST windows')
        if self.status not in ('CLOSED', 'PARTIAL') or not 1 <= self.minute_count <= 5:
            raise ValueError('Five-minute bar completion metadata is invalid')
        if self.status == 'CLOSED' and (self.minute_count != 5 or self.missing_minute_ends or self.first_seen_at < self.end):
            raise ValueError('A CLOSED bar requires five complete observed minutes')
        for name in ('open', 'high', 'low', 'close'):
            object.__setattr__(self, name, finite(getattr(self, name), positive=True))
        object.__setattr__(self, 'volume', finite(self.volume, nonnegative=True))
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ValueError('Five-minute OHLC observations are inconsistent')
        if self.venue not in ('KRX', 'NXT', 'CONSOLIDATED') or not self.source or not self.session_id:
            raise ValueError('Five-minute source, venue, and session metadata are required')

    def to_dict(self) -> dict:
        return {'time': self.start.isoformat(), 'start': self.start.isoformat(), 'end': self.end.isoformat(),
                'open': self.open, 'high': self.high, 'low': self.low, 'close': self.close,
                'volume': self.volume, 'interval': '5m', 'status': self.status,
                'session_id': self.session_id, 'venue': self.venue, 'source': self.source,
                'minute_count': self.minute_count, 'first_seen_at': self.first_seen_at.isoformat(),
                'missing_minute_ends': [stamp.isoformat() for stamp in self.missing_minute_ends],
                'turnover_krw': self.turnover_krw, 'ma5_5m': self.ma5_5m}


def build_five_minute_bars(
    candles: Sequence[MinuteCandle | Mapping[str, Any]],
    sessions: Sequence[MarketSession | Mapping[str, Any]],
    as_of: datetime | str,
    *, venue: Venue = 'CONSOLIDATED',
) -> list[FiveMinuteBar]:
    """Build one selected feed, with no summing of already consolidated data.

    Input rows whose END or first_seen_at is later than the replay clock are
    ignored before their OHLC is used. The latest *available* duplicate revision
    wins. Missing minutes and session edge fragments remain PARTIAL forever.
    """
    clock = aware(as_of)
    calendar = [item if isinstance(item, MarketSession) else MarketSession(**item) for item in sessions]
    selected_venue = venue_name(venue)
    relevant = sorted((s for s in calendar if s.venue == selected_venue), key=lambda s: s.start)
    if any(left.end > right.start for left, right in zip(relevant, relevant[1:])):
        raise ValueError('Selected feed sessions must not overlap')
    revisions: dict[datetime, MinuteCandle] = {}
    for raw in candles:
        # Availability is examined before parsing price fields, so an unknown
        # future row cannot alter the current result (or cause a false failure).
        if not isinstance(raw, MinuteCandle):
            raw_end = raw.get('end', raw.get('end_at', raw.get('timestamp', raw.get('time'))))
            raw_seen = raw.get('first_seen_at', raw.get('seen_at'))
            if raw_end is not None and raw_seen is not None and (aware(raw_end) > clock or aware(raw_seen) > clock):
                continue
        candle = raw if isinstance(raw, MinuteCandle) else MinuteCandle.from_mapping(raw)
        if (candle.end > clock or candle.first_seen_at > clock or candle.venue != selected_venue
                or candle.status != 'CLOSED' or candle.first_seen_at < candle.end):
            continue
        prior = revisions.get(candle.end)
        if prior is None or candle.first_seen_at > prior.first_seen_at:
            revisions[candle.end] = candle
        elif candle.first_seen_at == prior.first_seen_at and candle != prior:
            raise ValueError('Conflicting minute observations require distinct revision timestamps')
    if len({c.source for c in revisions.values()}) > 1:
        raise ValueError('Do not mix synthetic and broker observations in one history')
    buckets: dict[tuple[str, datetime], tuple[MarketSession, list[MinuteCandle]]] = {}
    for candle in sorted(revisions.values(), key=lambda item: item.end):
        minute_start = candle.end - timedelta(minutes=1)
        session = next((s for s in relevant if s.start <= minute_start and candle.end <= s.end), None)
        if session is None:
            continue
        start = minute_start.replace(minute=minute_start.minute // 5 * 5, second=0, microsecond=0)
        key = (session.session_id, start)
        buckets.setdefault(key, (session, []))[1].append(candle)
    result: list[FiveMinuteBar] = []
    for (_, start), (session, rows) in sorted(buckets.items(), key=lambda item: item[0][1]):
        end = start + timedelta(minutes=5)
        expected = tuple(start + timedelta(minutes=i) for i in range(1, 6))
        available = {row.end for row in rows}
        missing = tuple(stamp for stamp in expected if stamp not in available)
        closed = not missing and end <= clock and session.start <= start and end <= session.end
        result.append(FiveMinuteBar(start, end, rows[0].open, max(r.high for r in rows), min(r.low for r in rows),
                    rows[-1].close, sum(r.volume for r in rows), 'CLOSED' if closed else 'PARTIAL',
                    session.session_id, selected_venue, rows[0].source, len(rows), max(r.first_seen_at for r in rows),
                    missing, sum(r.turnover_krw for r in rows) if all(r.turnover_krw is not None for r in rows) else None))
    for index, bar in enumerate(result):
        window = result[max(0, index - 4):index + 1]
        if len(window) == 5 and all(b.status == 'CLOSED' and b.session_id == bar.session_id for b in window) and all(
            left.end == right.start for left, right in zip(window, window[1:])
        ):
            result[index] = replace(bar, ma5_5m=mean(b.close for b in window))
    return result


@dataclass(frozen=True)
class PriceLevel:
    label: str
    price: float

    def __post_init__(self):
        object.__setattr__(self, 'price', finite(self.price, positive=True))
        if not self.label.strip():
            raise ValueError('A price level label is required')


@dataclass(frozen=True)
class DailyStructure:
    ma5_daily: float | None
    box_high: float | None
    box_low: float | None
    pivot: float | None
    first_resistance: float | None
    structural_stop: float | None
    target: float | None
    supports: tuple[PriceLevel, ...] = ()
    important_open_close: tuple[PriceLevel, ...] = ()
    observed_at: datetime | None = None

    def __post_init__(self):
        for name in ('ma5_daily', 'box_high', 'box_low', 'pivot', 'first_resistance', 'structural_stop', 'target'):
            if getattr(self, name) is not None:
                object.__setattr__(self, name, finite(getattr(self, name), positive=True))
        if self.observed_at is not None:
            object.__setattr__(self, 'observed_at', aware(self.observed_at))


@dataclass(frozen=True)
class PointInTimeQuote:
    price: float
    timestamp: datetime
    first_seen_at: datetime
    venue: Venue = 'CONSOLIDATED'
    source: str = 'synthetic'
    coverage_status: Quality = 'UNAVAILABLE'

    def __post_init__(self):
        object.__setattr__(self, 'price', finite(self.price, positive=True))
        object.__setattr__(self, 'timestamp', aware(self.timestamp))
        object.__setattr__(self, 'first_seen_at', aware(self.first_seen_at))
        if self.venue not in ('KRX', 'NXT', 'CONSOLIDATED') or not self.source:
            raise ValueError('An explicit quote source and venue are required')


@dataclass(frozen=True)
class EntryGates:
    # None is genuinely unknown. Absence never silently means a safe market.
    theme_confirmed: bool | None = None
    spread_pct: float | None = None
    turnover_krw_per_min: float | None = None
    suspended: bool | None = None
    vi_active: bool | None = None
    gap_pct: float | None = None
    near_price_limit: bool | None = None


@dataclass(frozen=True)
class EarlyEntryConfig:
    min_reward_risk: float = 2.0
    max_stop_distance_pct: float = 2.0
    no_chasing_if_to_first_resistance_rr_below: float = 1.0
    max_live_quote_age_seconds: float = 15.0
    max_completed_bar_age_seconds: float = 300.0
    min_support_dwell_seconds: float = 180.0
    support_tolerance_pct: float = 0.3
    max_spread_pct: float = 0.5
    min_turnover_krw_per_min: float = 10_000_000.0
    max_gap_pct: float = 10.0
    breakout_confirm_pct: float = 0.15

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, finite(getattr(self, name), positive=True))
        if (self.min_reward_risk < 2 or self.max_stop_distance_pct > 2
                or self.no_chasing_if_to_first_resistance_rr_below < 1
                or self.max_live_quote_age_seconds > 15):
            raise ValueError('v1.2 risk and quote freshness limits cannot be loosened')


@dataclass(frozen=True)
class EarlyEntryInput:
    as_of: datetime
    quote: PointInTimeQuote | None
    bars: Sequence[FiveMinuteBar]
    levels: DailyStructure
    gates: EntryGates = field(default_factory=EntryGates)
    symbol: str = ''
    scenario_label: str | None = None


def evaluate_early_entry(observation: EarlyEntryInput, config: EarlyEntryConfig | None = None) -> dict:
    if config is not None and not isinstance(config, EarlyEntryConfig):
        raise TypeError('An explicit EarlyEntryConfig is required')
    settings = config if config is not None else EarlyEntryConfig()
    clock, quote, levels, gates = aware(observation.as_of), observation.quote, observation.levels, observation.gates
    reasons: list[str] = []
    result = {'symbol': observation.symbol, 'as_of': clock.isoformat(), 'state': 'UNAVAILABLE', 'quality': 'UNAVAILABLE',
              'mode': 'demo' if quote and quote.source in ('synthetic', 'fixture') else 'live',
              'source': quote.source if quote else None, 'eligible': False, 'recovery_confirmed': False,
              'MA5_DAILY': levels.ma5_daily, 'MA5_5M': None, 'ma5_daily': levels.ma5_daily, 'ma5_5m': None,
              'support_dwell_seconds': 0.0, 'support': None, 'first_resistance': levels.first_resistance,
              'structural_stop': levels.structural_stop, 'target': levels.target, 'risk': None,
              'daily_levels': {'box_high': levels.box_high, 'box_low': levels.box_low, 'pivot': levels.pivot,
                               'important_open_close': [{'label': p.label, 'price': p.price} for p in levels.important_open_close]},
              'confirmation_bar': None, 'preview': False, 'reason_codes': reasons,
              'scenario_label': observation.scenario_label, 'orders_enabled': False}
    if quote is None or quote.first_seen_at > clock or quote.timestamp > clock:
        reasons.append('QUOTE_UNAVAILABLE_AT_AS_OF')
        return result
    result.update(price=quote.price, quote_as_of=quote.timestamp.isoformat(), quote_at=quote.timestamp.isoformat(),
                  first_seen_at=quote.first_seen_at.isoformat(), quote_age_seconds=(clock - quote.timestamp).total_seconds(),
                  completed_5m_bar=False, gates={name: getattr(gates, name) for name in gates.__dataclass_fields__})
    if levels.observed_at is None or aware(levels.observed_at) > clock:
        reasons.append('DAILY_LEVELS_UNAVAILABLE_AT_AS_OF')
        return result
    demo = quote.source in ('synthetic', 'fixture')
    if not demo and quote.source != 'toss':
        reasons.append('QUOTE_SOURCE_NOT_VERIFIED')
        return result
    age = (clock - quote.timestamp).total_seconds()
    if age > settings.max_live_quote_age_seconds or quote.coverage_status not in ('LIVE', 'DEMO'):
        result.update(state='WATCH_ONLY', quality='STALE' if age > 300 or quote.coverage_status == 'STALE' else 'DELAYED')
        reasons.append('QUOTE_TOO_OLD_OR_UNVERIFIED')
        return result
    if demo != (quote.coverage_status == 'DEMO'):
        reasons.append('SOURCE_QUALITY_MISMATCH')
        return result
    visible = [bar for bar in observation.bars if aware(bar.first_seen_at) <= clock and aware(bar.end) <= clock]
    visible.sort(key=lambda bar: bar.end)
    if any(bar.source != quote.source or bar.venue != quote.venue for bar in visible):
        reasons.append('PRICE_HISTORY_SOURCE_OR_VENUE_MISMATCH')
        return result
    # A current PARTIAL window may offer a visual preview, never recovery proof.
    partials = [bar for bar in observation.bars if bar.status == 'PARTIAL' and bar.first_seen_at <= clock and bar.start <= clock]
    result['preview'] = bool(partials)
    closed = [bar for bar in visible if bar.status == 'CLOSED']
    if not closed:
        result.update(state='EARLY_ENTRY_WATCH' if levels.first_resistance and quote.price < levels.first_resistance else 'WATCH_ONLY', quality='PARTIAL' if partials else 'UNAVAILABLE')
        reasons.append('NO_COMPLETED_5M_BAR')
        return result
    latest = closed[-1]
    result['confirmation_bar'] = {'start': latest.start.isoformat(), 'end': latest.end.isoformat(), 'status': 'CLOSED'}
    result['completed_5m_bar'] = True
    if (clock - latest.end).total_seconds() > settings.max_completed_bar_age_seconds:
        result.update(state='WATCH_ONLY', quality='STALE')
        reasons.append('COMPLETED_5M_BAR_TOO_OLD')
        return result
    result['quality'] = 'DEMO' if demo else 'LIVE'
    contiguous = [latest]
    for bar in reversed(closed[:-1]):
        if bar.session_id != latest.session_id or bar.end != contiguous[0].start:
            break
        contiguous.insert(0, bar)
    first_resistance = levels.first_resistance
    if first_resistance is None or not math.isfinite(first_resistance) or first_resistance <= 0:
        result.update(state='WATCH_ONLY', quality='UNAVAILABLE')
        reasons.append('FIRST_RESISTANCE_UNAVAILABLE')
        return result
    # A breakout label is a factual completed-bar event, not permission to buy.
    if quote.price >= first_resistance:
        result['state'] = 'BREAKOUT_CONFIRMED' if latest.close >= first_resistance * (1 + settings.breakout_confirm_pct / 100) else 'WATCH_ONLY'
        reasons.append('AFTER_FIRST_RESISTANCE_NO_EARLY_ENTRY')
        return result
    traded_resistance = [bar for bar in visible if bar.session_id == latest.session_id and bar.high >= first_resistance]
    traded_resistance.extend(bar for bar in partials if bar.session_id == latest.session_id and bar.source == quote.source
                             and bar.venue == quote.venue and bar.high >= first_resistance)
    if traded_resistance:
        result.update(state='WATCH_ONLY', resistance_first_observed_at=min(bar.first_seen_at for bar in traded_resistance).isoformat())
        reasons.append('RESISTANCE_ALREADY_TRADED_RETEST_ONLY')
        return result
    result['state'] = 'EARLY_ENTRY_WATCH'
    unknown = [name for name in ('theme_confirmed', 'spread_pct', 'turnover_krw_per_min', 'suspended', 'vi_active', 'gap_pct', 'near_price_limit') if getattr(gates, name) is None]
    reasons.extend('UNKNOWN_' + name.upper() for name in unknown)
    invalid = []
    for name in ('theme_confirmed', 'suspended', 'vi_active', 'near_price_limit'):
        value = getattr(gates, name)
        if value is not None and type(value) is not bool:
            reasons.append('INVALID_' + name.upper())
            invalid.append(name)
            result['gates'][name] = None
    if gates.theme_confirmed is False: reasons.append('THEME_NOT_CONFIRMED')
    if gates.suspended is True: reasons.append('TRADING_SUSPENDED')
    if gates.vi_active is True: reasons.append('VI_ACTIVE')
    if gates.near_price_limit is True: reasons.append('NEAR_PRICE_LIMIT')
    for name, value in (('spread_pct', gates.spread_pct), ('turnover_krw_per_min', gates.turnover_krw_per_min), ('gap_pct', gates.gap_pct)):
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or (name != 'gap_pct' and value < 0)):
            reasons.append('INVALID_' + name.upper())
            invalid.append(name)
            result['gates'][name] = None
    if gates.spread_pct is not None and 'spread_pct' not in invalid and gates.spread_pct > settings.max_spread_pct: reasons.append('SPREAD_TOO_WIDE')
    if gates.turnover_krw_per_min is not None and 'turnover_krw_per_min' not in invalid and gates.turnover_krw_per_min < settings.min_turnover_krw_per_min: reasons.append('INSUFFICIENT_LIQUIDITY')
    if gates.gap_pct is not None and 'gap_pct' not in invalid and abs(gates.gap_pct) > settings.max_gap_pct: reasons.append('EXTREME_GAP')
    stop, target = levels.structural_stop, levels.target
    if stop is None or target is None or not all(math.isfinite(x) and x > 0 for x in (stop, target)) or not stop < quote.price < target:
        reasons.append('STRUCTURAL_STOP_OR_TARGET_UNAVAILABLE')
    else:
        risk = quote.price - stop
        stop_pct = risk / quote.price * 100
        reward_risk = (target - quote.price) / risk
        first_rr = (first_resistance - quote.price) / risk
        result['risk'] = {'stop_distance_pct': stop_pct, 'reward_risk': reward_risk, 'first_resistance_reward_risk': first_rr}
        result.update(stop_distance_pct=stop_pct, reward_risk=reward_risk, rr_to_first_resistance=first_rr)
        if stop_pct > settings.max_stop_distance_pct: reasons.append('STOP_DISTANCE_EXCEEDS_LIMIT')
        if reward_risk < settings.min_reward_risk: reasons.append('REWARD_RISK_BELOW_MINIMUM')
        if first_rr < settings.no_chasing_if_to_first_resistance_rr_below: reasons.append('FIRST_RESISTANCE_TOO_CLOSE_NO_CHASING')
    if len(contiguous) < 6:
        reasons.append('INSUFFICIENT_CONTIGUOUS_5M_HISTORY')
    else:
        ma_now, ma_before = mean(b.close for b in contiguous[-5:]), mean(b.close for b in contiguous[-6:-1])
        result.update(MA5_5M=ma_now, ma5_5m=ma_now)
        recovered_ma = contiguous[-2].close <= ma_before and latest.close > ma_now and latest.close > latest.open
        supports = list(levels.supports) + list(levels.important_open_close)
        if levels.ma5_daily is not None and levels.ma5_daily > 0:
            supports.append(PriceLevel('MA5_DAILY', levels.ma5_daily))
        candidates = [p for p in supports if p.price <= quote.price and (quote.price / p.price - 1) * 100 <= 1.0]
        held_candidates = []
        tolerance = settings.support_tolerance_pct / 100
        for support in candidates:
            for index in range(len(contiguous) - 2, -1, -1):
                touched = contiguous[index]
                # OHLC tells us the touch was observed at close, not when within
                # the candle it occurred. Dwell starts at that END conservatively.
                if not support.price * (1 - tolerance) <= touched.low <= support.price * (1 + tolerance):
                    continue
                after = contiguous[index:]
                held = all(b.low >= support.price * (1 - tolerance) and b.close >= support.price * (1 - tolerance) for b in after)
                dwell = (latest.end - touched.end).total_seconds()
                if held and dwell >= settings.min_support_dwell_seconds:
                    held_candidates.append((dwell, support))
                    break
        if held_candidates:
            dwell, support = max(held_candidates, key=lambda item: (item[0], item[1].price))
            result.update(support={'label': support.label, 'price': support.price}, support_dwell_seconds=dwell)
        if not recovered_ma: reasons.append('COMPLETED_5M_MA_RECOVERY_NOT_CONFIRMED')
        if not held_candidates: reasons.append('OBSERVED_SUPPORT_DWELL_NOT_CONFIRMED')
        # Do not use an old recovery below support to approve a new chasing quote.
        if quote.price < ma_now: reasons.append('CURRENT_QUOTE_BELOW_MA5_5M')
        result['recovery_confirmed'] = recovered_ma and bool(held_candidates)
    if unknown or invalid:
        result['quality'] = 'UNAVAILABLE'
    if not reasons and result['recovery_confirmed']:
        result.update(state='EARLY_ENTRY_READY', eligible=True)
        reasons.append('COMPLETED_RECOVERY_BEFORE_FIRST_RESISTANCE')
    return result


def _legacy_stock_entry(raw_stock: Mapping[str, Any], legacy_analyzed: Mapping[str, Any],
                        as_of: datetime | str, sessions: Sequence[MarketSession | Mapping[str, Any]],
                        mode: str, config: EarlyEntryConfig | None = None) -> dict:
    """Safely expose the new contract beside, rather than replace, legacy state.

    Live missing spread/VI/halts stay unknown. Snapshot capture supplies the
    *current* first_seen_at when an older source did not store it; this is never
    a license to pretend that snapshot was seen earlier during replay.
    """
    clock = aware(as_of)
    source = 'synthetic' if mode == 'demo' else str(raw_stock.get('source', 'unknown'))
    feed = 'CONSOLIDATED' if mode == 'demo' else venue_name(str(raw_stock.get('venue', 'unknown')))
    observed = aware(raw_stock.get('captured_at', clock))
    rows = []
    for row in raw_stock.get('intraday', []):
        end = aware(row['time']) + (timedelta(minutes=1) if mode == 'demo' else timedelta())
        rows.append({**row, 'end': end, 'first_seen_at': row.get('first_seen_at', observed), 'venue': feed,
                     'source': source, 'timestamp_semantics': 'END'})
    bars = build_five_minute_bars(rows, sessions, clock, venue=feed)
    technical = legacy_analyzed.get('technical', {})
    supports = tuple(PriceLevel(str(row['label']), row['price']) for row in legacy_analyzed.get('levels', []) if row.get('price', 0) > 0)
    daily = [row for row in raw_stock.get('candles', []) if row['time'][:10] < clock.date().isoformat()]
    important = tuple(PriceLevel(f"{row['time']} {side.upper()}", row[side]) for row in daily[-5:] for side in ('open', 'close'))
    resistance_levels = [technical.get('boxHigh'), technical.get('pivot'), (technical.get('significantCandle') or {}).get('high')]
    anchor = technical.get('ma5')
    resistance_levels = [value for value in resistance_levels if isinstance(value, (float, int)) and value > 0 and (anchor is None or value > anchor)]
    first = min(resistance_levels) if resistance_levels and technical.get('box', {}).get('confirmed') else None
    targets = legacy_analyzed.get('targets', [])
    levels = DailyStructure(technical.get('ma5'), technical.get('boxHigh'), technical.get('boxLow'), technical.get('pivot'),
                           first, legacy_analyzed.get('risk', {}).get('stop') if '미확인' not in legacy_analyzed.get('risk', {}).get('stopBasis', '미확인') else None,
                           targets[-1] if targets else None, supports, important, observed)
    raw_gates = raw_stock.get('entry_gates', {})
    gates = EntryGates(**{name: raw_gates.get(name) for name in EntryGates.__dataclass_fields__})
    quote_stamp = clock if mode == 'demo' else raw_stock.get('timestamp')
    quote = PointInTimeQuote(raw_stock['price'], aware(quote_stamp), observed, feed, source, 'DEMO' if mode == 'demo' else 'LIVE') if quote_stamp else None
    decision = evaluate_early_entry(EarlyEntryInput(clock, quote, bars, levels, gates, raw_stock['symbol']), config)
    return {'intraday5m': [bar.to_dict() for bar in bars], 'earlyEntry': decision}


def evaluate_legacy_stock_entry(raw_stock: Mapping[str, Any], legacy_analyzed: Mapping[str, Any],
                                as_of: datetime | str, sessions: Sequence[MarketSession | Mapping[str, Any]],
                                mode: str, config: EarlyEntryConfig | None = None) -> dict:
    """API-safe additive adapter; unavailable setup must not break stock detail."""
    try:
        if mode not in ('demo', 'live'):
            raise ValueError('An explicit data mode is required')
        if mode == 'demo' and (raw_stock.get('source') == 'toss' or any(row.get('source') == 'toss' for row in raw_stock.get('intraday', []))):
            raise ValueError('Broker observations cannot be relabeled as synthetic fixtures')
        return _legacy_stock_entry(raw_stock, legacy_analyzed, as_of, sessions, mode, config)
    except (ValueError, TypeError, KeyError, AttributeError):
        try:
            safe_clock = aware(as_of).isoformat()
        except (ValueError, TypeError, AttributeError):
            safe_clock = None
        return {'intraday5m': [], 'earlyEntry': {'symbol': raw_stock.get('symbol'), 'as_of': safe_clock,
                'state': 'UNAVAILABLE', 'quality': 'UNAVAILABLE', 'mode': mode, 'eligible': False,
                'recovery_confirmed': False, 'completed_5m_bar': False, 'MA5_DAILY': None, 'MA5_5M': None,
                'ma5_daily': None, 'ma5_5m': None, 'reason_codes': ['MALFORMED_OR_UNAVAILABLE_ENTRY_OBSERVATIONS'],
                'orders_enabled': False}}


def fixture_scenario(name: Literal['sk_innovation', 'isupetasys'] = 'sk_innovation', *, as_of: datetime | str | None = None) -> EarlyEntryInput:
    """Invented Oct-8 regression scenario, NEVER actual prices or history.

    The isupetasys recovery ends at 09:30 and is first seen at 09:30:02.
    At 09:25 it is impossible to use that future proof. Symbol names describe
    requested regression shapes only; price magnitudes below are invented.
    """
    if name not in ('sk_innovation', 'isupetasys'):
        raise ValueError('Unknown synthetic scenario')
    clock = aware(as_of or '2026-10-08T09:30:02+09:00')
    start = aware('2026-10-08T09:00:00+09:00')
    session = MarketSession('DEMO_KRX_REGULAR', start, aware('2026-10-08T15:30:00+09:00'))
    prices = [10100., 10120., 10110., 10100., 10090., 10140.]
    scale = 1.0 if name == 'sk_innovation' else 2.0
    rows = []
    for index, close in enumerate(prices):
        opening = prices[index - 1] if index else 10100.
        for minute in range(1, 6):
            end = start + timedelta(minutes=index * 5 + minute)
            progress = minute / 5
            previous = opening + (close - opening) * (minute - 1) / 5
            price = opening + (close - opening) * progress
            rows.append(MinuteCandle(end, end + timedelta(seconds=2), previous * scale,
                (max(previous, price) + 8) * scale, (min(previous, price) - 8) * scale, price * scale,
                1000.0 if index != 5 else 3000.0, source='synthetic'))
    bars = build_five_minute_bars(rows, [session], clock)
    visible = [row for row in rows if row.end <= clock and row.first_seen_at <= clock]
    quote_price = visible[-1].close if visible else prices[0] * scale
    levels = DailyStructure(10100. * scale, 10300. * scale, 10000. * scale, 10300. * scale, 10300. * scale,
                            10000. * scale, 10500. * scale, (PriceLevel('observed prior CLOSE', 10100. * scale),),
                            (PriceLevel('2026-10-07 OPEN', 10100. * scale),), start - timedelta(minutes=1))
    gates = EntryGates(True, .1, 100_000_000., False, False, 1.0, False)
    return EarlyEntryInput(clock, PointInTimeQuote(quote_price, clock, clock, source='synthetic', coverage_status='DEMO'),
                           bars, levels, gates, '096770' if name == 'sk_innovation' else '007660',
                           'Invented 2026-10-08 regression shape; not historical market data')
