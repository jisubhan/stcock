"""Walk-forward evaluation with stop-first handling of ambiguous daily bars."""
from collections import defaultdict
from statistics import mean
from app.settings import StrategyConfig
from .engine import moving_average, significant_candle


def evaluate_trade(entry: float, stop: float, target: float, future: list[dict], fee_pct=0., slippage_pct=0.) -> dict:
    if entry <= 0 or stop >= entry or target <= entry or not future:
        raise ValueError('invalid trade levels or empty evaluation window')
    filled_entry = entry * (1 + slippage_pct / 100)
    exit_price = future[-1]['close']
    result = 'TIME_EXIT'
    observed = []
    ambiguous = False
    for bar in future:
        observed.append(bar)
        stop_hit, target_hit = bar['low'] <= stop, bar['high'] >= target
        if bar['open'] <= stop:
            exit_price, result = bar['open'], 'GAP_STOP'
            break
        if bar['open'] >= target:
            exit_price, result = target, 'TARGET'
            break
        if stop_hit:
            # We cannot infer intrabar ordering from OHLC. A bar touching both
            # levels is charged as a stop; never cherry-pick its winning path.
            exit_price, result, ambiguous = stop, 'STOP', target_hit
            break
        if target_hit:
            exit_price, result = target, 'TARGET'
            break
    filled_exit = exit_price * (1 - slippage_pct / 100)
    mfe = max(0., (max(b['high'] for b in observed) / filled_entry - 1) * 100)
    mae = min(0., (min(b['low'] for b in observed) / filled_entry - 1) * 100)
    pct = (filled_exit / filled_entry - 1) * 100 - 2 * fee_pct
    return {'entryPrice': round(filled_entry, 2), 'exitPrice': round(filled_exit, 2),
            'resultPct': round(pct, 3), 'result': result, 'mfe': round(mfe, 3), 'mae': round(mae, 3),
            'targetHit': result == 'TARGET', 'stopHit': result in ('STOP', 'GAP_STOP'),
            'ambiguousBar': ambiguous, 'holdingBars': len(observed),
            'thresholds': {str(threshold): (mfe >= threshold if threshold > 0 else mae <= threshold)
                           for threshold in (1, 2, 3, 5, -1, -2)}}


def summarize(trades: list[dict]) -> dict:
    gains = sum(t['resultPct'] for t in trades if t['resultPct'] > 0)
    losses = abs(sum(t['resultPct'] for t in trades if t['resultPct'] < 0))
    losing_streak = maximum_streak = 0
    for trade in trades:
        losing_streak = losing_streak + 1 if trade['resultPct'] <= 0 else 0
        maximum_streak = max(maximum_streak, losing_streak)
    return {'signalCount': len(trades),
            'winRate': round(100 * sum(t['resultPct'] > 0 for t in trades) / max(1, len(trades)), 1),
            'averageReturn': round(mean([t['resultPct'] for t in trades] or [0]), 2),
            'averageMfe': round(mean([t['mfe'] for t in trades] or [0]), 2),
            'averageMae': round(mean([t['mae'] for t in trades] or [0]), 2),
            'profitFactor': round(gains / losses, 2) if losses else None,
            'maxLosingStreak': maximum_streak,
            'ambiguousBars': sum(t['ambiguousBar'] for t in trades)}


def run_backtest(stocks: list[dict], config: StrategyConfig, theme_names: dict[str, str]) -> dict:
    trades = []
    for stock in stocks:
        candles = stock['candles']
        next_available = 25
        for i in range(25, len(candles) - 1):
            if i < next_available:
                continue
            # All signal inputs end at this index; only fills/exits see later bars.
            history = candles[:i + 1]
            ma = moving_average(history, config.moving_average.short)
            previous_ma = moving_average(history[:-1], config.moving_average.short)
            sig = significant_candle(history, config)
            near = abs(history[-1]['close'] / ma - 1) * 100 <= config.entry.ma5_distance_pct
            if not (ma > previous_ma and history[-1]['close'] >= ma and near):
                continue
            pattern = 'PIVOT_RECLAIM' if sig and history[-1]['close'] > sig['close'] else 'MA5_BOUNCE'
            entry = candles[i + 1]['open']
            stop = min(c['low'] for c in history[-3:]) * (1 - config.risk.stop_buffer_pct / 100)
            risk_pct = (entry - stop) / entry * 100
            if stop >= entry or risk_pct > config.risk.max_stop_loss_pct:
                continue
            target = entry + (entry - stop) * config.risk.target_r_multiple
            future = candles[i + 1:i + 1 + config.backtest.forward_bars]
            evaluation = evaluate_trade(entry, stop, target, future, config.backtest.fee_pct, config.backtest.slippage_pct)
            trades.append({'id': f"{stock['symbol']}-{history[-1]['time']}", 'symbol': stock['symbol'],
                           'name': stock['name'], 'theme': theme_names.get(stock['seedThemeId'], '미분류'),
                           'pattern': pattern, 'signalDate': history[-1]['time'],
                           'entryDate': future[0]['time'], 'stop': round(stop), 'target': round(target), **evaluation})
            # One position per stock; do not duplicate overlapping outcomes.
            next_available = i + 1 + evaluation['holdingBars']
    trades.sort(key=lambda t: (t['entryDate'], t['symbol']))
    groups = defaultdict(list)
    theme_groups = defaultdict(list)
    for trade in trades:
        groups[trade['pattern']].append(trade)
        theme_groups[trade['theme']].append(trade)
    patterns = [{'pattern': pattern, **summarize(groups[pattern])}
                for pattern in ('MA5_BOUNCE', 'PIVOT_RECLAIM', 'BOX_RETEST', 'BREAKOUT')]
    return {'summary': summarize(trades), 'patterns': patterns,
            'themes': [{'theme': theme, **summarize(group)} for theme, group in theme_groups.items()],
            'trades': trades, 'assumptions': {
                'data': 'synthetic', 'execution': 'next candle open',
                'intrabar': 'stop first when both stop and target touched',
                'feesPctPerSide': config.backtest.fee_pct,
                'slippagePctPerSide': config.backtest.slippage_pct,
                'forwardBars': config.backtest.forward_bars,
                'coverage': 'daily candle MA5 and pivot patterns only; zero-count patterns lack minute replay',
                'excursions': 'OHLC extrema through exit candle; intrabar ordering is unknown'}}
