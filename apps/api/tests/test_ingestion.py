import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pytest
from app.fixtures import stock_samples
from app.market.ingestion import IngestionService, analysis_snapshot, normalized_candles
from app.market.live_bundle import LiveBundleError, load_live_bundle
from app.settings import StrategyConfig, runtime_dir, save_yaml

def run(coro):
    return asyncio.run(coro)

def proof():
    return {'verified': True, 'includes_nxt': True, 'price_basis': 'integrated', 'daily_candle_basis': 'integrated',
            'evidence': {key: 'offline-test-observation:' + key for key in ('quote_venue', 'daily_ohlc', 'nxt_premarket', 'nxt_aftermarket')}}

class ObservedClient:
    """Mock official observations, confined to isolated test runtime storage."""
    def __init__(self):
        self.samples = stock_samples()[:3]
        self.clock = datetime.now(timezone.utc)
        self.day = self.clock.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat()
        self.failure_cleared = False
    def reset_auth_failure(self):
        self.failure_cleared = True
    async def aclose(self):
        pass
    async def calendar(self, market):
        yesterday = (self.clock - timedelta(days=1)).astimezone(ZoneInfo('Asia/Seoul')).date().isoformat()
        if market == 'US':
            return {'today': {'date': self.day, 'regularMarket': {'endTime': (self.clock + timedelta(hours=2)).isoformat()}},
                    'previousBusinessDay': {'date': yesterday, 'regularMarket': {'endTime': (self.clock - timedelta(hours=10)).isoformat()}}}
        return {'today': {'date': self.day, 'integrated': {'regularMarket': {'startTime': (self.clock - timedelta(hours=1)).isoformat(), 'endTime': (self.clock + timedelta(hours=1)).isoformat()}}},
                'previousBusinessDay': {'date': yesterday}}
    async def prices(self, symbols):
        return [{'symbol': symbol, 'lastPrice': str(next((s['price'] for s in self.samples if s['symbol'] == symbol), 100)),
                 'currency': 'KRW' if symbol.isdigit() else 'USD', 'timestamp': self.clock.isoformat()} for symbol in symbols]
    async def stock_master(self, symbols):
        return [{'symbol': s['symbol'], 'name': s['name'], 'currency': 'KRW', 'status': 'ACTIVE'} for s in self.samples]
    async def rankings(self, market, count):
        return [{'symbol': s['symbol'], 'tradingVolume': str(s['volume']), 'tradingAmount': str(s['tradedValue']),
                 'price': {'lastPrice': str(s['price']), 'basePrice': str(s['price'] / 1.04)}} for s in self.samples]
    async def candles(self, symbol, interval, count):
        if symbol == 'MRVL':
            return [{'timestamp': (self.clock - timedelta(days=days)).replace(hour=0, minute=0, second=0).isoformat(),
                     'closePrice': str(close), 'currency': 'USD'} for days, close in [(1, 104), (2, 100), (3, 99)]]
        sample = next(s for s in self.samples if s['symbol'] == symbol)
        candles = sample['candles'] if interval == '1d' else sample['intraday']
        rows = []
        for i, c in enumerate(candles):
            when = (self.clock - timedelta(days=len(candles) - i - 1)).replace(hour=0, minute=0, second=0) if interval == '1d' else self.clock - timedelta(minutes=len(candles) - i - 1)
            rows.append({'timestamp': when.isoformat(), 'openPrice': str(c['open']), 'highPrice': str(c['high']),
                         'lowPrice': str(c['low']), 'closePrice': str(c['close']), 'volume': str(c['volume']), 'currency': 'KRW'})
        return rows[::-1]
    async def investor(self, symbol, count):
        return [{'date': self.day, 'foreigner': {'netBuyVolume': '100'}, 'institution': {'netBuyVolume': '-20'}}]
    async def program(self, symbol, count):
        return [{'date': self.day, 'arbitrage': {'netBuyVolume': '-10'}, 'nonArbitrage': {'netBuyVolume': '40'}}]
    async def index_prices(self, symbols):
        return []

@pytest.fixture
def observed_client():
    client = ObservedClient()
    save_yaml('themes.yml', {'themes': [{'id': 'optical_networking', 'name': '실제 관측 테스트',
                                       'memberSymbols': [s['symbol'] for s in client.samples], 'usTickers': ['MRVL']}]})
    return client

def test_complete_ingestion_activates_live_with_actual_joined_factors(observed_client):
    run(IngestionService(observed_client).ingest(proof()))
    assert load_live_bundle(StrategyConfig(), required_us_tickers=['MRVL']).ready
    context = analysis_snapshot()
    assert context.meta['mode'] == 'live' and context.meta['source'] == 'toss'
    assert len(context.samples) == 3 and context.market['indices'] == []
    assert context.themes[0]['usLeadPct'] == pytest.approx(4)
    assert context.samples[0]['changePct'] == pytest.approx(4)
    assert context.samples[0]['flow']['foreign'] == 100
    assert context.stocks[0]['flowUnit'] == 'shares'
    assert context.stocks[0]['flowVenue']['program'] == 'KRX only'
    assert not any('모의 데이터 기반' in risk for stock in context.stocks for risk in stock['risks'])

def test_persisted_ip_refusal_never_automatically_retries(monkeypatch):
    monkeypatch.setenv('TOSS_CLIENT_ID', 'test-id')
    monkeypatch.setenv('TOSS_CLIENT_SECRET', 'test-secret')
    (runtime_dir() / 'toss-probe.json').write_text(json.dumps({'authentication': {'status': 'failed', 'http_status': 403, 'condition': 'ip_not_allowed', 'error_code': 'access_denied'}, 'calls': 1}))
    service = IngestionService()
    async def check():
        await service.start()
        await service.refresh()
        assert service._task is None and service.client is None
    run(check())
    status = service.status()
    assert status['authStatus'] == 'failed' and status['httpStatus'] == 403
    assert status['errorCode'] == 'access_denied' and status['condition'] == 'ip_not_allowed'
    assert 'IP' in status['dataQuality']['reason'] and status['callerIp'] is None

def test_explicit_demo_prevents_auth_even_with_injected_credentials(monkeypatch):
    monkeypatch.setenv('TOSS_DATA_MODE', 'demo')
    monkeypatch.setenv('TOSS_CLIENT_ID', 'test-id')
    monkeypatch.setenv('TOSS_CLIENT_SECRET', 'test-secret')
    service = IngestionService()
    async def check():
        await service.start()
        await service.refresh(explicit=True)
        assert service.client is None and service._task is None
    run(check())

def test_manual_recovery_clears_auth_latch_and_restarts_finished_task(monkeypatch, observed_client):
    import app.brokers.toss.documented as module
    service = IngestionService(observed_client)
    service._blocked = True
    async def successful_probe(client):
        assert client.failure_cleared
        return SimpleNamespace(to_dict=lambda: {'authentication': {'status': 'passed'}, 'venue': proof()})
    monkeypatch.setattr(module, 'run_probe', successful_probe)
    monkeypatch.setattr(module, 'save_probe', lambda report: None)
    async def check():
        done = asyncio.create_task(asyncio.sleep(0))
        await done
        service._task = done
        result = await service.refresh(explicit=True)
        assert observed_client.failure_cleared and result['dataQuality']['status'] == 'ready'
        assert service._task is not done and not service._task.done()
        await service.close()
    run(check())

def test_stale_daily_history_blocks_live_snapshot(observed_client):
    original = observed_client.candles
    async def stale(symbol, interval, count):
        rows = await original(symbol, interval, count)
        if symbol.isdigit() and interval == '1d':
            for row in rows:
                row['timestamp'] = (datetime.fromisoformat(row['timestamp']) - timedelta(days=30)).isoformat()
        return rows
    observed_client.candles = stale
    with pytest.raises(LiveBundleError, match='일봉'):
        run(IngestionService(observed_client).ingest(proof()))
    assert not (runtime_dir() / 'live_snapshot.json').exists()

def test_currency_and_mixed_offset_chronology_are_checked():
    base = {'openPrice': '100', 'highPrice': '102', 'lowPrice': '99', 'closePrice': '101', 'volume': '10', 'currency': 'KRW'}
    rows = [{**base, 'timestamp': '2026-10-07T01:01:00Z'}, {**base, 'timestamp': '2026-10-07T10:00:00+09:00'}]
    assert normalized_candles(rows, '1m')[0]['time'] == '2026-10-07T10:00:00+09:00'
    rows[0]['currency'] = 'USD'
    with pytest.raises(ValueError, match='currency'):
        normalized_candles(rows, '1m')

def test_stale_bundle_fallback_and_paper_modes_are_separate(observed_client):
    from app.db.store import paper_buy, paper_snapshot
    run(IngestionService(observed_client).ingest(proof()))
    live = analysis_snapshot()
    paper_buy(live.stocks[0], 1, mode='live')
    paper_buy(live.stocks[0], 2, mode='demo')
    assert paper_snapshot(live.stocks, mode='live')['summary']['openPositions'] == 1
    path = runtime_dir() / 'live_snapshot.json'
    data = json.loads(path.read_text())
    data['asOf'] = (observed_client.clock - timedelta(seconds=200)).isoformat()
    path.write_text(json.dumps(data))
    fallback = analysis_snapshot()
    assert fallback.meta['mode'] == 'demo' and fallback.meta['stale']
    assert all(stock['source'] == 'synthetic' for stock in fallback.stocks)
    assert paper_snapshot(fallback.stocks, mode='demo')['summary']['openPositions'] == 1
