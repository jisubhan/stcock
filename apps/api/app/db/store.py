import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from app.settings import runtime_dir


@contextmanager
def connection():
    conn = sqlite3.connect(runtime_dir() / 'radar.sqlite3', timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, name TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0), entry_price REAL NOT NULL,
            stop REAL NOT NULL, target REAL NOT NULL, opened_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN', exit_price REAL, closed_at TEXT, exit_reason TEXT
        );
        CREATE TABLE IF NOT EXISTS notification_read (
            id TEXT PRIMARY KEY, read_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
            config_json TEXT NOT NULL, result_json TEXT NOT NULL
        );
    ''')
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(paper_trades)')}
    if 'data_mode' not in columns:
        conn.execute("ALTER TABLE paper_trades ADD COLUMN data_mode TEXT NOT NULL DEFAULT 'demo'")
    if 'last_price' not in columns:
        conn.execute('ALTER TABLE paper_trades ADD COLUMN last_price REAL')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def now():
    return datetime.now(timezone.utc).isoformat()


def _position(row: sqlite3.Row, prices: dict[str, int]) -> dict:
    result = dict(row)
    available = result['symbol'] in prices
    price = result['exit_price'] if result['status'] == 'CLOSED' else prices.get(result['symbol'], result['last_price'] or result['entry_price'])
    result.update({'entryPrice': result.pop('entry_price'), 'currentPrice': price,
                   'openedAt': result.pop('opened_at'), 'closedAt': result.pop('closed_at'),
                   'exitPrice': result.pop('exit_price'), 'exitReason': result.pop('exit_reason')})
    result['dataMode'] = result.pop('data_mode')
    result['source'] = 'toss' if result['dataMode'] == 'live' else 'synthetic'
    result['staleQuote'] = result['status'] == 'OPEN' and not available
    result.pop('last_price')
    result['pnl'] = round((price - result['entryPrice']) * result['quantity'])
    result['returnPct'] = round((price / result['entryPrice'] - 1) * 100, 2)
    return result


def paper_snapshot(stocks: list[dict], mode='demo') -> dict:
    prices = {s['symbol']: s['price'] for s in stocks}
    with connection() as conn:
        rows = conn.execute('SELECT * FROM paper_trades WHERE data_mode=? ORDER BY id DESC', (mode,)).fetchall()
    all_trades = [_position(row, prices) for row in rows]
    positions = [t for t in all_trades if t['status'] == 'OPEN']
    history = [t for t in all_trades if t['status'] == 'CLOSED']
    return {'positions': positions, 'history': history, 'summary': {
        'openPositions': len(positions), 'closedPositions': len(history),
        'invested': round(sum(t['entryPrice'] * t['quantity'] for t in positions)),
        'unrealizedPnl': sum(t['pnl'] for t in positions),
        'realizedPnl': sum(t['pnl'] for t in history),
        'winRate': round(sum(t['pnl'] > 0 for t in history) / max(1, len(history)) * 100, 1)},
        'notice': '가상 매매만 기록합니다. 증권사 주문은 호출하지 않습니다.'}


def paper_buy(stock: dict, quantity: int, mode='demo') -> int:
    with connection() as conn:
        cursor = conn.execute('''INSERT INTO paper_trades
            (symbol,name,quantity,entry_price,stop,target,opened_at,data_mode,last_price) VALUES(?,?,?,?,?,?,?,?,?)''',
            (stock['symbol'], stock['name'], quantity, stock['price'], stock['risk']['stop'], stock['targets'][1], now(), mode, stock['price']))
        return cursor.lastrowid


def paper_sell(trade_id: int, stocks: list[dict], reason='MANUAL', mode='demo') -> None:
    prices = {s['symbol']: s['price'] for s in stocks}
    with connection() as conn:
        trade = conn.execute('SELECT * FROM paper_trades WHERE id=?', (trade_id,)).fetchone()
        if not trade:
            raise LookupError('가상 포지션을 찾을 수 없습니다.')
        if trade['status'] != 'OPEN':
            raise ValueError('이미 청산된 가상 포지션입니다.')
        if trade['data_mode'] != mode or trade['symbol'] not in prices:
            raise ValueError('해당 포지션의 동일한 데이터 모드 현재가를 확인한 뒤 청산하세요.')
        conn.execute("UPDATE paper_trades SET status='CLOSED',exit_price=?,closed_at=?,exit_reason=? WHERE id=?",
                     (prices[trade['symbol']], now(), reason, trade_id))


def monitor_paper(stocks: list[dict], mode='demo') -> None:
    # This also works with future verified quotes. Frozen demo snapshots naturally
    # keep positions open until manual sell or thresholds crossed.
    prices = {s['symbol']: s['price'] for s in stocks}
    with connection() as conn:
        for trade in conn.execute("SELECT * FROM paper_trades WHERE status='OPEN' AND data_mode=?", (mode,)).fetchall():
            if trade['symbol'] not in prices:
                continue
            price = prices[trade['symbol']]
            conn.execute('UPDATE paper_trades SET last_price=? WHERE id=?', (price, trade['id']))
            reason = 'STOP' if price <= trade['stop'] else 'TARGET' if price >= trade['target'] else None
            if reason:
                conn.execute("UPDATE paper_trades SET status='CLOSED',exit_price=?,closed_at=?,exit_reason=? WHERE id=?",
                             (price, now(), reason, trade['id']))


def read_notifications() -> set[str]:
    with connection() as conn:
        return {row['id'] for row in conn.execute('SELECT id FROM notification_read')}


def mark_notifications(ids: list[str]):
    with connection() as conn:
        conn.executemany('INSERT OR IGNORE INTO notification_read(id,read_at) VALUES(?,?)', [(item, now()) for item in ids])


def save_backtest(config: dict, result: dict) -> int:
    with connection() as conn:
        cursor = conn.execute('INSERT INTO backtest_runs(created_at,config_json,result_json) VALUES(?,?,?)',
                              (now(), json.dumps(config), json.dumps(result, ensure_ascii=False)))
        return cursor.lastrowid
