"""Deterministic indicators and explainable scores. No broker calls or LLMs."""
from statistics import mean
from datetime import datetime
from math import ceil
from typing import Sequence
from app.settings import StrategyConfig
from app.fixtures import US_RETURNS


def clamp(number: float, low=0., high=100.) -> float:
    return max(low, min(high, number))

def moving_average(candles: Sequence[dict], window: int = 5) -> float:
    if len(candles) < window:
        raise ValueError(f'at least {window} candles are required')
    if any(not candle.get('integrated', False) for candle in candles[-window:]):
        raise ValueError('do not mix unverified or KRX-only closes with integrated candles')
    return mean(candle['close'] for candle in candles[-window:])


def significant_candle(candles: list[dict], config: StrategyConfig) -> dict | None:
    settings = config.significant_candle
    result = None
    for i in range(max(20, len(candles) - settings.lookback_days), len(candles)):
        candle = candles[i]
        previous = candles[i - 1]
        gain = (candle['close'] / previous['close'] - 1) * 100
        volume_ratio = candle['volume'] / mean(c['volume'] for c in candles[i - 20:i])
        close_position = (candle['close'] - candle['low']) / max(1, candle['high'] - candle['low'])
        traded_value = candle.get('tradedValue', candle['close'] * candle['volume'])
        if (gain >= settings.min_gain_pct and volume_ratio >= settings.min_volume_ratio_20d
                and close_position >= settings.close_position_min
                and traded_value >= config.screen.min_traded_value_krw):
            result = {**candle, 'index': i, 'gainPct': round(gain, 2),
                      'tradedValueSource': 'observed' if candle.get('tradedValue') is not None else 'close_times_volume_estimate',
                      'volumeRatio': round(volume_ratio, 2), 'closePosition': round(close_position, 3)}
    return result


def _clusters(values: list[tuple[float, float]], tolerance: float, min_touches: int) -> list[dict]:
    groups: list[list[tuple[float, float]]] = []
    for price, weight in sorted(values):
        for group in groups:
            center = mean(p for p, _ in group)
            if abs(price / center - 1) <= tolerance:
                group.append((price, weight))
                break
        else:
            groups.append([(price, weight)])
    return [{'price': round(sum(p * w for p, w in g) / sum(w for _, w in g)),
             'touches': len(g), 'volumeWeight': sum(w for _, w in g)}
            for g in groups if len(g) >= min_touches]


def detect_box(candles: list[dict], config: StrategyConfig, significant: dict | None = None) -> dict:
    # Find the consolidation before the latest significant breakout, rather than
    # taking arbitrary maximum/minimum across a trending window.
    if significant and len(candles) - significant['index'] - 1 >= 5:
        section = candles[max(significant['index'] + 1, len(candles) - config.box.lookback_days):]
    else:
        end = significant['index'] if significant else len(candles)
        section = candles[max(0, end - config.box.lookback_days):end]
    highs, lows = [], []
    for i in range(1, len(section) - 1):
        candle, before, after = section[i], section[i - 1], section[i + 1]
        if candle['high'] >= before['high'] and candle['high'] >= after['high']:
            highs.append((candle['high'], candle['volume']))
        if candle['low'] <= before['low'] and candle['low'] <= after['low']:
            lows.append((candle['low'], candle['volume']))
    tolerance = config.box.touch_tolerance_pct / 100
    resistance = _clusters(highs, tolerance, config.box.min_touches)
    support = _clusters(lows, tolerance, config.box.min_touches)
    if resistance and support:
        upper = max(resistance, key=lambda c: (c['touches'], c['volumeWeight']))
        valid_support = [s for s in support if s['price'] < upper['price']]
        if valid_support:
            lower = max(valid_support, key=lambda c: (c['touches'], c['volumeWeight']))
            return {'high': upper['price'], 'low': lower['price'],
                    'highTouches': upper['touches'], 'lowTouches': lower['touches'],
                    'method': 'volume_weighted_swing_clusters', 'confirmed': True,
                    'start': section[0]['time'], 'end': section[-1]['time']}
    # Fall back only for visualization; an unconfirmed range earns no box points.
    return {'high': max(c['high'] for c in section), 'low': min(c['low'] for c in section),
            'highTouches': len(highs), 'lowTouches': len(lows),
            'method': 'unconfirmed_range', 'confirmed': False,
            'start': section[0]['time'], 'end': section[-1]['time']}


def calculate_themes(stocks: list[dict], themes: list[dict], config: StrategyConfig, us_returns: dict[str, float] | None = None) -> list[dict]:
    observed_us = US_RETURNS if us_returns is None else us_returns
    lookup = {s['symbol']: s for s in stocks}
    result = []
    for theme in themes:
        members = [lookup[symbol] for symbol in theme['memberSymbols'] if symbol in lookup]
        breadth = 100 * sum(s['changePct'] > 0 for s in members) / max(1, len(theme['memberSymbols']))
        avg_return = mean([s['changePct'] for s in members] or [0])
        traded = sum(s['tradedValue'] for s in members)
        us_available = [observed_us[t] for t in theme['usTickers'] if t in observed_us]
        us_return = mean(us_available or [0])
        # Transparent bounded factor normalization. US leadership alone cannot
        # qualify a theme lacking at least three domestic rising constituents.
        normalized = {'breadth': breadth, 'return': clamp(avg_return / 3 * 100),
                      'tradedValue': clamp(traded / 200_000_000_000 * 100),
                      'usLead': clamp(us_return / 4 * 100)}
        strength = normalized['breadth'] * .30 + normalized['return'] * .20 + normalized['tradedValue'] * .30 + normalized['usLead'] * .20
        qualified = (sum(s['changePct'] > 0 for s in members) >= config.theme.min_constituents
                     and breadth >= config.theme.min_breadth_pct and strength >= config.theme.min_strength)
        leaders = sorted(members, key=lambda s: s['tradedValue'], reverse=True)[:3]
        result.append({**theme, 'score': round(strength), 'breadthPct': round(breadth, 1),
                       'tradedValue': traded, 'changePct': round(avg_return, 2),
                       'leaders': [{'symbol': s['symbol'], 'name': s['name']} for s in leaders],
                       'usLeadPct': round(us_return, 2), 'qualified': qualified,
                       'risingCount': sum(s['changePct'] > 0 for s in members),
                       'memberCount': len(theme['memberSymbols']), 'observedMemberCount': len(members),
                       'coveragePct': round(len(members) / max(1, len(theme['memberSymbols'])) * 100, 1), 'factors': normalized,
                       'top3ConcentrationPct': round(100 * sum(s['tradedValue'] for s in leaders) / max(1, traded), 1)})
    return sorted(result, key=lambda t: t['score'], reverse=True)


def detect_reclaim(intraday: list[dict], ma5: float, pivot: float, config: StrategyConfig) -> bool:
    if len(intraday) < 3:
        return False
    touched = any(c['low'] <= ma5 * (1 + config.entry.support_tolerance_pct / 100) for c in intraday[:-1])
    below_pivot = any(c['close'] <= pivot for c in intraday[:-1])
    recent = intraday[-1]
    crossed = recent['close'] >= pivot * (1 + config.entry.pivot_reclaim_confirm_pct / 100)
    prior_volume = mean(c['volume'] for c in intraday[:-1][-5:])
    accelerated = recent['volume'] / max(1, prior_volume) >= config.entry.volume_acceleration
    return touched and below_pivot and crossed and accelerated


def state_for(price: float, entry: dict, stop: float, target: float, pivot: float,
              confirmed: bool, reclaim: bool, eligible: bool, score: float) -> str:
    if price < stop:
        return 'INVALIDATED'
    if price >= target:
        return 'TAKE_PROFIT_ZONE'
    if not eligible:
        return 'WATCH' if score >= 60 else 'SCANNING'
    if reclaim and confirmed:
        return 'BREAKOUT_CONFIRM'
    if entry['low'] <= price <= entry['high'] and confirmed:
        return 'ENTRY_READY'
    if abs(price / entry['high'] - 1) <= .025:
        return 'APPROACHING_ENTRY'
    return 'WATCH'


def analyze_stock(stock: dict, themes: list[dict], config: StrategyConfig) -> dict:
    daily, intra, price = stock['candles'], stock['intraday'], stock['price']
    associated = [theme for theme in themes if stock['symbol'] in theme['memberSymbols']]
    theme = max(associated, key=lambda t: t['score']) if associated else {'id': 'unassigned', 'name': '미분류', 'score': 0, 'qualified': False, 'usLeadPct': 0}
    ma5 = moving_average(daily, config.moving_average.short)
    ma20 = moving_average(daily, config.moving_average.medium)
    previous_ma5 = moving_average(daily[:-1], config.moving_average.short)
    trend_up = ma5 >= previous_ma5 and ma5 > ma20
    sig = significant_candle(daily, config)
    box = detect_box(daily, config, sig)
    previous_close = daily[-2]['close']
    pivot = sig['close'] if sig else box['high']
    support_candidates = [(f'{config.moving_average.short}일 이동평균', ma5), ('전일 종가', previous_close),
                          ('박스 상단', box['high']), ('최근 저점', min(c['low'] for c in daily[-4:])),
                          ('거래량 중심', sum(c['close'] * c['volume'] for c in daily[-8:]) / sum(c['volume'] for c in daily[-8:]))]
    if sig:
        support_candidates.extend([('기준봉 시가', sig['open']), ('기준봉 종가', sig['close'])])
    near_supports = [(name, value) for name, value in support_candidates if abs(value / price - 1) * 100 <= config.entry.support_tolerance_pct]
    # The observation zone is the support cluster, not a manually entered price.
    anchor = mean(value for _, value in near_supports) if near_supports else ma5
    entry = {'low': round(anchor * (1 - config.entry.zone_width_pct / 100)),
             'high': round(anchor * (1 + config.entry.zone_width_pct / 100))}
    entry_mid = (entry['low'] + entry['high']) / 2
    # Invalidation belongs to a confirmed box or significant candle. An
    # arbitrary recent low cannot silently replace that structural logic.
    structural_candidates = [box['low']] if box['confirmed'] else []
    if sig:
        structural_candidates += [sig['open'], sig['low']]
    below = [level for level in structural_candidates if level < entry_mid]
    structural_stop_confirmed = bool(below)
    stop = round((max(below) if below else entry['low']) * (1 - config.risk.stop_buffer_pct / 100))
    risk_per_share = max(1, entry_mid - stop)
    resistance_levels = [box['high']] if box['confirmed'] else []
    if sig:
        resistance_levels.append(sig['high'])
    resistances = sorted(set(level for level in resistance_levels if level > max(entry['high'], price)))
    target1 = round(resistances[0] if resistances else entry_mid + risk_per_share * 2)
    target2 = round(max(target1 + risk_per_share, entry_mid + risk_per_share * config.risk.target_r_multiple))
    rr = (target2 - entry_mid) / risk_per_share
    stop_loss = (stop / entry_mid - 1) * 100
    holding = bool(sig and daily[-1]['volume'] / sig['volume'] < config.significant_candle.holding_volume_ratio
                   and daily[-1]['close'] >= sig['close'] * config.significant_candle.holding_price_ratio)
    rising_intraday = len(intra) >= 3 and intra[-1]['close'] > intra[-3]['close'] and intra[-1]['low'] >= intra[-3]['low']
    # Require repeated held closes after a support touch and a meaningful volume rise.
    interval_minutes = 1.
    if len(intra) >= 2:
        interval_minutes = max(1., (datetime.fromisoformat(intra[-1]['time']) - datetime.fromisoformat(intra[-2]['time'])).total_seconds() / 60)
    confirmation_bars = max(1, ceil(config.entry.confirmation_minutes / interval_minutes))
    held_closes = intra[-confirmation_bars:]
    volume_acceleration = intra[-1]['volume'] / max(1, mean(c['volume'] for c in intra[-6:-1])) if len(intra) >= 6 else 0
    confirmed = (rising_intraday and len(held_closes) >= confirmation_bars
                 and all(c['close'] >= entry['low'] for c in held_closes)
                 and volume_acceleration >= config.entry.volume_acceleration)
    reclaim = detect_reclaim(intra, ma5, pivot, config)
    distance = abs(price / ma5 - 1) * 100
    retest = box['confirmed'] and abs(price / box['high'] - 1) * 100 <= config.entry.support_tolerance_pct and confirmed
    pattern = 'PIVOT_RECLAIM' if reclaim else 'BOX_RETEST' if retest else 'MA5_BOUNCE' if distance <= config.entry.ma5_distance_pct else 'BREAKOUT'
    positive_flow = sum(value > 0 for value in stock['flow'].values())
    flow_score = clamp(positive_flow / 3 * 55 + clamp(stock['volumeRatio'] / 3 * 100) * .25 + clamp(volume_acceleration / 2 * 100) * .20)
    chart_score = clamp((25 if trend_up else 0) + (25 if sig else 0) + (25 if holding else 0) + (15 if box['confirmed'] and price > box['high'] else 0) + (10 if daily[-1]['close'] >= max(c['close'] for c in daily[-20:]) * .985 else 0))
    count = len(near_supports)
    confluence_score = 100 if count >= 3 else 100 * 10 / 15 if count == 2 else 100 * 4 / 15 if count == 1 else 0
    rr_score = 100 if rr >= 3 else 80 if rr >= 2 else 50 if rr >= 1.5 else 0
    normalized = {'theme_strength': theme['score'], 'money_flow': flow_score,
                  'chart_structure': chart_score, 'support_confluence': confluence_score,
                  'risk_reward': rr_score, 'realtime_confirmation': 100 if confirmed else 20}
    breakdown = {key: {'points': round(value * getattr(config.weights, key) / 100, 2),
                       'max': getattr(config.weights, key)} for key, value in normalized.items()}
    raw_score = sum(factor['points'] for factor in breakdown.values())
    risk_penalty = min(20., max(0., abs(stop_loss) - config.risk.max_stop_loss_pct) * 5)
    score = round(clamp(raw_score - risk_penalty))
    eligible = (score >= config.screen.min_recommend_score and theme['qualified'] and structural_stop_confirmed
                and rr >= config.risk.min_risk_reward and abs(stop_loss) <= config.risk.max_stop_loss_pct
                and stock['tradedValue'] >= config.screen.min_traded_value_krw)
    state = state_for(price, entry, stop, target1, pivot, confirmed, reclaim, eligible, score)
    reasons = []
    if theme['qualified']:
        reasons.append(f"{theme['name']} 동반 상승 · 테마 강도 {theme['score']}점")
    if positive_flow:
        labels = {'foreign': '외국인', 'institution': '기관', 'program': '프로그램'}
        reasons.append(' · '.join(labels[k] for k, v in stock['flow'].items() if v > 0) + ' 순매수 유입')
    if trend_up:
        reasons.append(f'상승 {config.moving_average.short}일선과 {config.moving_average.medium}일선 기준 추세 유지')
    if holding:
        reasons.append('기준봉 이후 거래량 감소 · 종가 유지')
    if box['confirmed'] and price > box['high']:
        reasons.append(f"반복 터치 박스 상단 {box['high']:,}원 돌파")
    if count >= 2:
        reasons.append(f"±{config.entry.support_tolerance_pct:g}% 내 지지 {count}개 중첩")
    if reclaim:
        reasons.append(f"{config.moving_average.short}일선 터치 후 {pivot:,}원 피벗 재탈환 · 거래량 확인")
    if confirmed:
        reasons.append('장중 저점 상승과 거래량 가속 확인')
    actual = stock.get('source') == 'toss'
    risks = (['실제 관측 데이터의 결정론적 분석이며 투자 성과를 보장하지 않습니다.',
              '프로그램 수급은 KRX만 집계하며 투자자 수급은 주식 수 기준입니다.'] if actual else
             ['모의 데이터 기반이며 실제 투자 성과를 보장하지 않습니다.', 'KRX/NXT 통합 가격 의미는 공식 응답 검증 전입니다.'])
    if not theme['qualified']:
        risks.append('동반 상승 3개 종목 또는 테마 강도 조건 미충족')
    if abs(stop_loss) > config.risk.max_stop_loss_pct:
        risks.append(f"기술적 무효화까지 {abs(stop_loss):.2f}% · 최대 손실 조건 초과")
    if not confirmed:
        risks.append('장중 지지와 거래량 확인이 필요합니다.')
    if not structural_stop_confirmed:
        risks.append('확인된 박스 또는 기준봉 무효화 구조가 없어 추천에서 제외합니다.')
    grade = 'S' if score >= 90 else 'A' if score >= 80 else 'B' if score >= 70 else 'WATCH' if score >= 60 else 'EXCLUDED'
    return {key: stock[key] for key in ('symbol', 'name', 'price', 'changePct', 'tradedValue', 'volume')} | {
        'theme': theme['name'], 'themeId': theme['id'], 'score': score, 'grade': grade,
        'mode': 'live' if actual else 'demo', 'source': 'toss' if actual else 'synthetic',
        'flowUnit': stock.get('flowUnit', 'KRW'),
        'flowVenue': stock.get('flowVenue', {'foreign': 'synthetic', 'institution': 'synthetic', 'program': 'synthetic'}),
        'flowAsOf': stock.get('flowAsOf', {'investor': daily[-1]['time'], 'program': daily[-1]['time']}),
        'sparkline': [c['close'] for c in daily[-20:]],
        'state': state, 'pattern': pattern, 'eligible': eligible, 'entry': entry,
        'technical': {'ma5': round(ma5), 'ma20': round(ma20), 'pivot': pivot,
                      'shortPeriod': config.moving_average.short, 'mediumPeriod': config.moving_average.medium,
                      'boxHigh': box['high'], 'boxLow': box['low'], 'box': box,
                      'significantCandleDate': sig['time'] if sig else None,
                      'significantCandle': sig, 'distanceToMa5Pct': round(distance, 2),
                      'confluenceCount': count, 'trendUp': trend_up},
        'risk': {'stop': stop, 'stopLossPct': round(stop_loss, 2),
                 'maxStopLossPct': config.risk.max_stop_loss_pct,
                 'stopBasis': '확인된 박스 하단' if box['confirmed'] and below and max(below) == box['low'] else '기준봉 구조' if below else '미확인 구조 · 추천 제외',
                 'invalidation': f'{stop:,}원 하회 후 지지 회복 실패'},
        'targets': [target1, target2], 'riskReward': round(rr, 2),
        'reasons': reasons, 'risks': risks, 'scores': {
            'theme': round(theme['score']), 'flow': round(flow_score), 'chart': round(chart_score),
            'entry': round(confluence_score), 'riskReward': rr_score, 'realtime': 100 if confirmed else 20},
        'scoreBreakdown': breakdown, 'scorePenalty': round(risk_penalty, 2),
        'flow': stock['flow'], 'volumeRatio': stock['volumeRatio'],
        'volumeAcceleration': round(volume_acceleration, 2),
        'levels': [{'label': name, 'price': round(value)} for name, value in support_candidates]}


def candles_with_ma(candles: list[dict], window: int = 5) -> list[dict]:
    return [{**candle, 'ma5': round(moving_average(candles[:i + 1], window)) if i + 1 >= window else None}
            for i, candle in enumerate(candles)]
