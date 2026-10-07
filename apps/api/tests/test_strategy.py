from copy import deepcopy
import pytest
from app.fixtures import stock_samples
from app.settings import StrategyConfig, read_themes
from app.strategy.engine import (analyze_stock, calculate_themes, detect_box,
                                  detect_reclaim, moving_average, significant_candle, state_for)


def test_integrated_ma_and_mixed_venue_rejected():
    candles = [{'close': number, 'integrated': True} for number in [100, 102, 104, 106, 108]]
    assert moving_average(candles) == 104
    candles[-1]['integrated'] = False
    with pytest.raises(ValueError, match='KRX-only'):
        moving_average(candles)


def test_significant_requires_gain_volume_close_and_liquidity():
    config = StrategyConfig()
    candles = stock_samples()[0]['candles']
    detected = significant_candle(candles, config)
    assert detected and detected['gainPct'] >= 4 and detected['volumeRatio'] >= 1.8
    changed = deepcopy(candles)
    changed[detected['index']]['volume'] = 1
    assert significant_candle(changed, config) is None
    config.significant_candle.min_gain_pct = 20
    assert significant_candle(candles, config) is None


def test_box_clusters_need_multiple_swing_touches():
    config = StrategyConfig()
    candles = stock_samples()[0]['candles']
    sig = significant_candle(candles, config)
    box = detect_box(candles, config, sig)
    assert box['confirmed']
    assert box['highTouches'] >= 2 and box['lowTouches'] >= 2
    assert box['low'] < box['high']
    assert box['method'] == 'volume_weighted_swing_clusters'


def test_us_lead_cannot_qualify_lone_domestic_stock():
    config = StrategyConfig()
    stocks = stock_samples()[:3]
    themes = [read_themes()[0]]
    assert calculate_themes(stocks, themes, config)[0]['qualified']
    stocks[1]['changePct'] = -1
    assert not calculate_themes(stocks, themes, config)[0]['qualified']
    themes[0]['memberSymbols'] = [stocks[0]['symbol']]
    assert not calculate_themes(stocks, themes, config)[0]['qualified']


def test_score_explainable_and_tight_risk_blocks_recommendation():
    config = StrategyConfig()
    stocks = stock_samples()
    themes = calculate_themes(stocks, read_themes(), config)
    result = analyze_stock(stocks[0], themes, config)
    assert result['eligible'] and result['score'] >= 80
    assert abs(sum(v['points'] for v in result['scoreBreakdown'].values()) - result['scorePenalty'] - result['score']) <= .51
    assert result['risk']['stop'] < result['entry']['low'] < result['entry']['high'] < result['targets'][1]
    assert result['risk']['stopBasis'] == '확인된 박스 하단'
    assert result['risk']['stop'] == round(result['technical']['boxLow'] * (1 - config.risk.stop_buffer_pct / 100))
    config.risk.max_stop_loss_pct = .1
    result = analyze_stock(stocks[0], themes, config)
    assert not result['eligible']
    assert any('최대 손실' in risk for risk in result['risks'])


def test_volume_decay_without_price_hold_gets_no_holding_reason():
    config = StrategyConfig()
    stocks = stock_samples()
    themes = calculate_themes(stocks, read_themes(), config)
    stock = deepcopy(stocks[0])
    sig = significant_candle(stock['candles'], config)
    stock['candles'][-1]['close'] = sig['close'] * .96
    result = analyze_stock(stock, themes, config)
    assert not any('거래량 감소' in reason for reason in result['reasons'])


def test_simtek_170200_pivot_requires_reclaim_and_volume():
    config = StrategyConfig()
    bars = [
        {'low': 168800, 'close': 169100, 'volume': 100},
        {'low': 168950, 'close': 170100, 'volume': 100},
        {'low': 170200, 'close': 170700, 'volume': 400},
    ]
    assert detect_reclaim(bars, 169000, 170200, config)
    bars[-1]['volume'] = 90
    assert not detect_reclaim(bars, 169000, 170200, config)
    bars[-1]['volume'], bars[-1]['close'] = 400, 170150
    assert not detect_reclaim(bars, 169000, 170200, config)


def test_simtek_box_invalidation_after_support_break():
    assert state_for(165000, {'low': 168500, 'high': 169500}, 166000, 175000,
                     170200, False, False, True, 90) == 'INVALIDATED'


@pytest.mark.parametrize('name,entry,exit_price,expected', [
    ('올릭스', 116700, 122600, 5.0557),
    ('SK이노베이션', 148700, 152000, 2.2192),
    ('HD현대에너지솔루션', 148800, 152000, 2.1505),
])
def test_document_regression_arithmetic(name, entry, exit_price, expected):
    # These are user-provided trade examples, not invented historical candles.
    assert (exit_price / entry - 1) * 100 == pytest.approx(expected, abs=.0001)
