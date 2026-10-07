from app.db.store import monitor_paper, paper_buy, paper_snapshot
from app.main import snapshot


def test_paper_stop_and_target_are_monitored_and_persisted():
    _, _, stocks, _ = snapshot()
    first = stocks[0]
    paper_buy(first, 3)
    quotes = [dict(stock) for stock in stocks]
    quotes[0]['price'] = first['risk']['stop'] - 5
    monitor_paper(quotes)
    stopped = paper_snapshot(quotes)
    assert stopped['summary']['openPositions'] == 0
    assert stopped['history'][0]['exitReason'] == 'STOP'
    assert stopped['history'][0]['pnl'] < 0
    paper_buy(first, 4)
    quotes[0]['price'] = first['targets'][1] + 5
    monitor_paper(quotes)
    completed = paper_snapshot(quotes)
    assert completed['summary']['closedPositions'] == 2
    assert completed['history'][0]['exitReason'] == 'TARGET'
    assert completed['history'][0]['pnl'] > 0
