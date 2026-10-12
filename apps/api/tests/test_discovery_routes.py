"""Offline integration with constructed transport records, never market evidence."""
from datetime import datetime, timedelta

import pytest

from app.discovery import runtime, storage
from app.radar.models import KST
from app.radar.storage import read_state

CLOCK = datetime(2026, 10, 12, 9, 10, 5, tzinfo=KST)
SEEN = CLOCK - timedelta(seconds=3)


def catalog(symbol):
    return {'symbol': symbol, 'securityType': 'STOCK', 'isCommonShare': True, 'isinCode': 'test-' + symbol}


def calendar():
    day = CLOCK.replace(hour=0, minute=0, second=0)
    return {'today': {'date': day.date().isoformat(), 'integrated': {
        'preMarket': {'startTime': day.replace(hour=8).isoformat(), 'endTime': day.replace(hour=8, minute=50).isoformat()},
        'regularMarket': {'startTime': day.replace(hour=9).isoformat(), 'endTime': day.replace(hour=15, minute=30).isoformat()}}},
        'previousBusinessDay': {'date': '2026-10-09'}}


def seed():
    symbols = [f'{index:06}' for index in range(1, 131)] + ['0101N0']
    storage.save_catalog([catalog(symbol) for symbol in symbols], SEEN, market='KOSDAQ')
    storage.save_catalog([], SEEN, market='KOSPI')
    storage.save_catalog([], SEEN, market='KR_ETC')
    storage.save_stock_master([{'symbol': '0101N0', 'name': 'Constructed unregistered stock', 'market': 'KOSDAQ'}], SEEN)
    storage.save_sectors([{'sectorId': 'a', 'name': 'Constructed industry', 'depth': 1, 'parentSectorId': None}], SEEN)
    storage.save_memberships('a', [{'symbol': symbol, 'currency': 'KRW'} for symbol in symbols[:3] + ['0101N0']], SEEN)
    storage.save_sector_rankings('KR', [{'sectorId': 'a', 'changeRate': '.04', 'rank': 1}], SEEN)
    storage.save_sector_rankings('US', [{'sectorId': 'a', 'changeRate': '.02', 'rank': 1}], SEEN)
    storage.save_global_rankings({'rankedAt': (CLOCK - timedelta(seconds=5)).isoformat(), 'rankings': [
        {'symbol': '0101N0', 'rank': 1, 'tradingAmount': '100000000', 'currency': 'KRW',
         'price': {'changeRate': '.03'}}]}, SEEN)
    storage.save_calendar('KR', calendar(), SEEN)
    storage.save_market_quotes([{'symbol': symbol, 'lastPrice': '10050', 'currency': 'KRW',
        'timestamp': (CLOCK - timedelta(seconds=5)).isoformat()} for symbol in symbols[:3] + ['0101N0']], SEEN)
    return symbols


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setenv('TOSS_DATA_MODE', 'demo')  # No client/OAuth/network worker.
    monkeypatch.setattr(runtime, '_now', lambda value=None: value or CLOCK)
    monkeypatch.setattr(storage, '_live_allowed', lambda: True)
    return seed()


def test_actual_universe_search_paging_unregistered_classification(client, frozen):
    overview = client.get('/api/discovery/overview').json()
    assert overview['source'] == 'toss'
    assert overview['universe']['expected'] == 131
    assert overview['universe']['missing'] == 127
    assert overview['orders_enabled'] is False
    assert overview['theme_hypotheses_top3'][0]['id'] == 'sector:a'
    assert overview['theme_hypotheses_top3'][0]['flow_confirmed'] is False
    assert 'members' not in overview['themes'][0]
    assert overview['observed_theme_leaders'][0]['observation_kind'] == 'OBSERVED_DAILY_TRADING_AMOUNT'
    assert overview['sector_performance_observations'][0]['observation_kind'] != 'OBSERVED_DAILY_TRADING_AMOUNT'
    first = client.get('/api/discovery/stocks?limit=25').json()
    second = client.get('/api/discovery/stocks?page=2&limit=25').json()
    assert first['total'] == 131 and len(first['items']) == len(second['items']) == 25
    assert not ({row['symbol'] for row in first['items']} & {row['symbol'] for row in second['items']})
    found = client.get('/api/discovery/stocks?q=unregistered&theme_id=sector:a').json()['items']
    assert [row['symbol'] for row in found] == ['0101N0']
    assert found[0]['theme_refs'][0]['relationship'] == 'OFFICIAL_SECTOR_MEMBER'
    assert found[0]['catalog_member'] is True
    assert found[0]['observed_turnover_krw'] == 100000000
    missing = client.get('/api/discovery/stocks?unclassified=true').json()
    assert missing['total'] == 127
    assert missing['items'][0]['price'] is None


def test_selected_symbol_queues_real_candles_and_never_uses_demo_chart(client, frozen):
    pending = client.get('/api/discovery/stocks/0101N0?theme_id=sector:a').json()
    assert pending['status'] == 'PENDING' and pending['chart']['daily'] == []
    assert any(job['symbol'] == '0101N0' for job in storage.pending_analysis())
    days, cursor = [], datetime(2026, 10, 9, tzinfo=KST)
    while len(days) < 30:
        if cursor.weekday() < 5:
            days.append(cursor.date().isoformat())
        cursor -= timedelta(days=1)
    daily = [{'time': day, 'open': 10000, 'high': 10200, 'low': 9900, 'close': 10050,
              'volume': 1234, 'source': 'toss', 'venue': 'CONSOLIDATED', 'integrated': True} for day in reversed(days)]
    storage.save_analysis_inputs('0101N0', daily, [], SEEN, SEEN)
    actual = client.get('/api/discovery/stocks/0101N0?theme_id=sector:a').json()
    assert actual['status'] == 'READY'
    assert len(actual['chart']['daily']) == 30 and actual['chart']['minute'] == []
    assert actual['chart']['daily'][0]['volume'] == 1234
    assert actual['earlyEntry']['eligible'] is False
    assert actual['investor_netbuy'] is None
    assert actual['theme']['association_kind'] == 'OFFICIAL_SECTOR_MEMBER'
    assert actual['orders_enabled'] is False
    assert client.get('/api/discovery/stocks/ZZZZZZ').status_code == 404
    assert client.get('/api/discovery/stocks/invalid').status_code == 422
    assert client.get('/api/discovery/stocks?page=0').status_code == 422


@pytest.mark.parametrize('malformed', [
    {'currency': 'USD'}, {'tradingAmount': '-100'}, {'source': 'synthetic'},
    {'mode': 'demo'}, {'first_seen_at': (CLOCK + timedelta(seconds=1)).isoformat()},
])
def test_raw_invalid_ranking_is_not_displayed_as_observed_krw(client, frozen, malformed):
    storage.save_global_rankings({'rankedAt': CLOCK.isoformat(), 'rankings': [{
        'symbol': '0101N0', 'tradingAmount': '100', 'currency': 'KRW', **malformed}]}, CLOCK)
    row = client.get('/api/discovery/stocks?q=0101N0').json()['items'][0]
    assert row['observed_turnover_krw'] is None
    assert row['turnover_quality'] == 'UNAVAILABLE'


def test_ranking_bootstrap_does_not_inflate_catalog_denominator(client, frozen):
    storage.save_global_rankings({'rankedAt': CLOCK.isoformat(), 'rankings': [{
        'symbol': '999999', 'tradingAmount': '100', 'currency': 'KRW'}]}, CLOCK)
    overview = client.get('/api/discovery/overview').json()
    assert overview['universe']['expected'] == 131
    assert overview['universe']['bootstrap_observed'] == 1
    row = client.get('/api/discovery/stocks?q=999999').json()['items'][0]
    assert row['catalog_member'] is False
    assert row['price'] is None
    assert client.get('/api/discovery/stocks/999999').json()['status'] == 'PENDING'


def test_bootstrap_membership_does_not_prove_domestic_catalog_theme(client, monkeypatch):
    monkeypatch.setenv('TOSS_DATA_MODE', 'demo')
    monkeypatch.setattr(runtime, '_now', lambda value=None: value or CLOCK)
    storage.save_sectors([{'sectorId': 'a', 'name': 'Constructed industry', 'depth': 1}], SEEN)
    storage.save_memberships('a', [{'symbol': '0101N0', 'currency': 'KRW'}], SEEN)
    storage.save_sector_rankings('US', [{'sectorId': 'a', 'changeRate': '.05'}], SEEN)
    storage.save_global_rankings({'rankedAt': CLOCK.isoformat(), 'rankings': [{'symbol': '0101N0',
        'tradingAmount': '100', 'currency': 'KRW'}]}, CLOCK)
    result = client.get('/api/discovery/overview').json()
    assert result['universe']['expected'] == 0
    assert result['theme_hypotheses_top3'] == []
    assert result['mapping_pending_themes'][0]['state'] == 'THEME_MAPPING_PENDING'


def test_official_membership_removal_and_past_graph_are_respected(frozen):
    from app.radar.runtime import configured_relations
    earlier = configured_relations(CLOCK, [])
    assert any(row.symbol == '0101N0' and row.relationship == 'OFFICIAL_SECTOR_MEMBER' for row in earlier)
    later = CLOCK + timedelta(minutes=1)
    storage.save_memberships('a', [], later)
    assert not any(row.provider == 'toss_official_sector' for row in configured_relations(later, []))
    historic = configured_relations(CLOCK, [], historical=True)
    assert any(row.symbol == '0101N0' and row.provider == 'toss_official_sector' for row in historic)
    assert not any(row.provider == 'toss_official_sector' for row in configured_relations(later, [], historical=True))


def test_background_hypothesis_updates_before_open_and_freezes_after_open(monkeypatch):
    early = CLOCK.replace(hour=8, minute=10)
    storage.save_catalog([catalog('0101N0')], early, market='KOSDAQ')
    storage.save_sectors([{'sectorId': 'a', 'name': 'Constructed industry', 'depth': 1}], early)
    storage.save_memberships('a', [{'symbol': '0101N0', 'currency': 'KRW'}], early)
    storage.save_calendar('KR', calendar(), early)
    storage.save_sector_rankings('US', [{'sectorId': 'a', 'changeRate': '.01'}], early)
    runtime.refresh_discovery_predictions(early)
    first = read_state('hypotheses:2026-10-12')
    assert first and first[0]['metrics']['prediction_kind'] == 'RULE_BASED_WATCH_PRIORITY'
    latest = early + timedelta(minutes=20)
    storage.save_sector_rankings('US', [{'sectorId': 'a', 'changeRate': '.08'}], latest)
    runtime.refresh_discovery_predictions(latest)
    revised = read_state('hypotheses:2026-10-12')
    assert revised[0]['score'] > first[0]['score']
    storage.save_sector_rankings('US', [{'sectorId': 'a', 'changeRate': '-.01'}], CLOCK)
    runtime.refresh_discovery_predictions(CLOCK)
    assert read_state('hypotheses:2026-10-12') == revised


def test_historical_news_uses_contemporaneous_health(monkeypatch, frozen):
    from app.radar import runtime as radar_runtime
    flags = []
    monkeypatch.setattr(radar_runtime, 'current_catalysts', lambda now, *, current: flags.append(current) or [])
    runtime.discovery_overview(CLOCK)
    runtime.discovery_stocks(as_of=CLOCK)
    runtime.discovery_overview()
    assert flags == [False, False, True]
