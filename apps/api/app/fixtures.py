"""Deterministic synthetic samples, never historical or exchange verified data."""
from datetime import date, timedelta
import math

AS_OF = '2026-10-07T10:24:00+09:00'
VENUE_LABEL = '모의 통합 캔들 · KRX/NXT 미검증'

STOCK_SEEDS = [
    ('010170', '대한광통신', 18050, 'optical_networking', 4.25, 126_800_000_000, 0),
    ('138080', '오이솔루션', 25850, 'optical_networking', 3.65, 73_200_000_000, 1),
    ('056360', '코위버', 6250, 'optical_networking', 2.18, 28_400_000_000, 2),
    ('034020', '두산에너빌리티', 81200, 'ai_power_nuclear', 3.32, 245_900_000_000, 3),
    ('052690', '한전기술', 98500, 'ai_power_nuclear', 2.82, 66_300_000_000, 4),
    ('051600', '한전KPS', 48600, 'ai_power_nuclear', 1.75, 54_600_000_000, 5),
    ('000660', 'SK하이닉스', 248500, 'ai_semiconductor', 2.54, 512_400_000_000, 6),
    ('240810', '원익IPS', 43650, 'ai_semiconductor', 2.12, 85_700_000_000, 7),
    ('222800', '심텍', 170450, 'ai_semiconductor', 1.88, 98_600_000_000, 8),
    ('373220', 'LG에너지솔루션', 381000, 'battery', 1.12, 112_400_000_000, 9),
    ('006400', '삼성SDI', 298500, 'battery', .62, 69_200_000_000, 10),
    ('247540', '에코프로비엠', 168500, 'battery', -.85, 47_100_000_000, 11),
    ('322000', 'HD현대에너지솔루션', 148800, 'energy_solar', 2.05, 52_600_000_000, 12),
    ('009830', '한화솔루션', 28650, 'energy_solar', 1.55, 32_900_000_000, 13),
    ('096770', 'SK이노베이션', 148700, 'energy_solar', -.42, 78_500_000_000, 14),
]

# Seed associations are examples users can edit, not authoritative classifications.
US_RETURNS = {
    'MRVL': 4.32, 'CIEN': 3.75, 'COHR': 3.16, 'LITE': 3.64,
    'CEG': 2.86, 'VST': 2.14, 'GEV': 2.66, 'CCJ': 1.98,
    'NVDA': 2.24, 'AMD': 1.86, 'AVGO': 1.75, 'MU': 2.02,
    'ALB': .8, 'TSLA': 1.2, 'QS': -.3, 'FSLR': 1.66, 'ENPH': 1.28, 'XOM': .72,
}

def _trading_days(count: int):
    result, cursor = [], date(2026, 10, 7)
    # Synthetic weekday calendar deliberately does not claim real exchange holidays.
    while len(result) < count:
        if cursor.weekday() < 5:
            result.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return result[::-1]

def daily_candles(price: int, variant: int) -> list[dict]:
    dates = _trading_days(90)
    candles = []
    base = price / (1.000 + (variant % 3) * .002)
    for i, day in enumerate(dates):
        if i < 75:
            ratio = .72 + i * .0033 + math.sin(i * 1.2 + variant) * .008
        elif i < 81:
            ratio = .950 + (i % 3) * .003 + math.sin(i + variant) * .001
        elif i == 81:
            ratio = .984 if variant % 3 == 2 else 1.000
        else:
            # Eight days of consolidation with repeated swing highs and lows.
            ratio = [1.001, 1.021, .998, 1.024, .999, 1.007, 1.000,
                     1.000 + (variant % 3) * .002][i - 82]
        close = round(base * ratio)
        opening = candles[-1]['close'] if candles else round(close * .997)
        if i == 81:
            opening = round(base * .958)
        wick = (.003 + (variant % 3) * .0004) * base
        high = round(max(opening, close) + wick)
        low = round(min(opening, close) - wick)
        if i >= 82:
            opening = round(close * (.999 if i % 2 else 1.001))
            high = round(max(opening, close) + wick)
            low = round(min(opening, close) - wick * 1.2)
        volume = round((1_200_000 + variant * 47_000) * (1 + .1 * math.sin(i)))
        if i == 81:
            volume *= 3
        if i > 81:
            # Continued heavy turnover removes the volume-digestion bonus.
            volume = round(volume * (2.2 if variant % 3 == 1 else .62))
        candles.append({'time': day, 'open': opening, 'high': high, 'low': low,
                        'close': close, 'volume': volume, 'integrated': True,
                        'source': 'synthetic', 'venue': 'SIMULATED'})
    return candles

def intraday_candles(price: int, variant: int) -> list[dict]:
    anchors = [(0, .997), (15, .988), (35, 1.004), (50, .997),
               (62, .993), (75, .998), (84, 1.0)]
    ratios = []
    for minute in range(85):
        if variant in (10, 11, 14):
            ratio = 1.012 - minute / 84 * .012
        else:
            left, right = next((a, b) for a, b in zip(anchors, anchors[1:]) if a[0] <= minute <= b[0])
            fraction = (minute - left[0]) / (right[0] - left[0])
            ratio = left[1] + (right[1] - left[1]) * fraction
        ratios.append(ratio)
    result = []
    for i, ratio in enumerate(ratios):
        close = round(price * ratio)
        opening = round(price * (ratios[i - 1] if i else .999))
        volume = 12_000 + variant * 400 + i * 180
        if i == 84 and variant not in (10, 11, 14):
            volume *= 3
        result.append({'time': f'2026-10-07T{9 + i // 60:02d}:{i % 60:02d}:00+09:00',
                       'open': opening, 'high': round(max(opening, close) * 1.001),
                       'low': round(min(opening, close) * .999), 'close': close, 'volume': volume,
                       'interval': '1m', 'integrated': True, 'source': 'synthetic', 'venue': 'SIMULATED'})
    return result

def stock_samples() -> list[dict]:
    return [{'symbol': symbol, 'name': name, 'price': price, 'seedThemeId': theme,
             'changePct': change, 'tradedValue': traded, 'volume': round(traded / price),
             'volumeRatio': round(2.65 - variant * .075, 2),
             'volumeAcceleration': 2.6 if variant not in (10, 11, 14) else .75,
             'flow': {'foreign': round(traded * (.028 - variant * .002)),
                      'institution': round(traded * (.012 - variant * .001)),
                      'program': round(traded * (.019 - variant * .0014))},
             'candles': daily_candles(price, variant), 'intraday': intraday_candles(price, variant)}
            for symbol, name, price, theme, change, traded, variant in STOCK_SEEDS]

def metadata() -> dict:
    return {'mode': 'demo', 'source': 'synthetic', 'asOf': AS_OF,
            'venueLabel': VENUE_LABEL, 'liveTrading': False}
