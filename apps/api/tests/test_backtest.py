from app.fixtures import stock_samples
from app.settings import StrategyConfig
from app.strategy.backtest import evaluate_trade, run_backtest


def test_same_bar_stop_and_target_uses_stop():
    outcome = evaluate_trade(100, 98, 104, [{'open': 100, 'low': 97, 'high': 105, 'close': 104}])
    assert outcome['result'] == 'STOP'
    assert outcome['resultPct'] == -2
    assert outcome['ambiguousBar']


def test_gap_stop_uses_open_and_costs_are_applied():
    outcome = evaluate_trade(100, 98, 104, [{'open': 95, 'low': 94, 'high': 99, 'close': 96}], .015, .05)
    assert outcome['result'] == 'GAP_STOP'
    assert outcome['resultPct'] < -5


def test_walkforward_records_nonoverlapping_next_open_fills():
    sample = stock_samples()[0]
    config = StrategyConfig()
    result = run_backtest([sample], config, {})
    assert result['summary']['signalCount'] > 0
    dates = [c['time'] for c in sample['candles']]
    previous_end = -1
    for trade in result['trades']:
        index = dates.index(trade['entryDate'])
        assert dates.index(trade['signalDate']) == index - 1
        assert index > previous_end
        expected = sample['candles'][index]['open'] * (1 + config.backtest.slippage_pct / 100)
        assert abs(trade['entryPrice'] - expected) < .01
        previous_end = index + trade['holdingBars'] - 1
    assert result['assumptions']['data'] == 'synthetic'
