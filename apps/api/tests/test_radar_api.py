"""Versioned route, migration and availability integration regressions."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

import pytest
from app.radar.models import FlowObservation, KST
from app.radar import runtime, storage
from app.strategy.intraday import evaluate_early_entry, fixture_scenario


@pytest.fixture(autouse=True)
def no_external_providers(monkeypatch):
    monkeypatch.delenv('CATALYST_PROVIDER_CONFIG', raising=False)
    monkeypatch.delenv('RADAR_CONFIG_PATH', raising=False)
    monkeypatch.setenv('TOSS_DATA_MODE', 'demo')


@pytest.mark.parametrize('route', ['/api/market/overview', '/api/themes/opening-flow', '/api/catalysts',
    '/api/feeds/health', '/api/recommendations?view=premarket', '/api/recommendations?view=live-flow',
    '/api/recommendations?view=entry-ready', '/api/themes/rotation-history'])
def test_read_only_routes_preserve_provenance_and_never_invent_current_sources(client, route):
    response = client.get(route)
    assert response.status_code == 200
    data = response.json()
    for key in ('as_of', 'data_freshness', 'coverage', 'provider', 'measurement_definition'):
        assert key in data
    if 'items' in data:
        assert data['items'] == []
    if route == '/api/market/overview':
        assert data['mode'] == 'unavailable'
        assert data['live_trading'] is False
        for key in ('premarket_hypothesis_top3', 'live_opening_flow_top3', 'entry_ready_top3'):
            assert data[key]['items'] == []
        assert all(data['data_freshness'][name]['status'] == 'UNAVAILABLE'
                   for name in ('quotes', 'news', 'disclosures', 'investor_netbuy', 'history'))


def test_replay_routes_change_observed_leader_without_turning_demo_into_live(client):
    before = client.get('/api/analytics/opening-replay?snapshot_time=09:02').json()
    after = client.get('/api/analytics/opening-replay?snapshot_time=09:10').json()
    reverse = client.get('/api/analytics/opening-replay?snapshot_time=09:30&scenario=reverse_rotation').json()
    outage = client.get('/api/analytics/opening-replay?snapshot_time=09:20&scenario=data_outage').json()
    assert before['live_opening_flow_top3']['items'] == []
    assert after['live_opening_flow_top3']['items'][0]['theme_id'] == 'domestic_policy'
    assert reverse['live_opening_flow_top3']['items'][0]['theme_id'] == 'optical_us'
    assert outage['live_opening_flow_top3']['items'] == []
    for data in (before, after, reverse, outage):
        assert data['mode'] == 'demo' and data['actual_market_data'] is False
        assert data['data_freshness']['investor_netbuy']['status'] == 'UNAVAILABLE'
        assert all(item['confirmed_investor_netbuy'] is None for item in data['theme_flow_snapshots'])


@pytest.mark.parametrize('query', ['session=invalid', 'snapshot_time=15:30', 'scenario=unknown',
    'as_of=2026-10-12T09:10:00', 'as_of=2026-10-13T09:10:00%2B09:00'])
def test_replay_rejects_ambiguous_dates_clocks_and_scenarios(client, query):
    assert client.get('/api/analytics/opening-replay?' + query).status_code == 422


def test_current_query_cannot_import_todays_maps_or_health_backwards(client):
    earlier = '2026-10-08T09:10:00+09:00'
    response = client.get('/api/market/overview', params={'as_of': earlier})
    assert response.status_code == 200
    assert response.json()['premarket_hypothesis_top3']['items'] == []
    with storage.connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM ticker_theme_relations').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM radar_state_revisions').fetchone()[0] == 0
    assert all(response.json()['data_freshness'][key]['last_success_at'] is None for key in ('news', 'disclosures', 'quotes'))
    future = datetime.now(KST) + timedelta(days=1)
    assert client.get('/api/market/overview', params={'as_of': future.isoformat()}).status_code == 422
    assert client.get('/api/market/overview?as_of=2026-10-08T09:10:00').status_code == 422


def test_state_versions_and_observation_corrections_retain_point_in_time_values():
    start = datetime(2026, 10, 12, 9, tzinfo=KST)
    end = start + timedelta(minutes=1)
    first = FlowObservation(symbol='000001', observed_at=end, first_seen_at=end,
        interval_start=start, interval_end=end, session_id='KRX_REGULAR', venue='CONSOLIDATED',
        turnover_krw=100., quote_at=end, provider='TEST', source_url='https://example.invalid', mode='live')
    corrected = first.model_copy(update={'first_seen_at': end + timedelta(seconds=10), 'turnover_krw': 150.})
    storage.save_observations([first, corrected, corrected])
    old = storage.read_observations(end + timedelta(seconds=5), 'KRX_REGULAR')
    new = storage.read_observations(end + timedelta(seconds=11), 'KRX_REGULAR')
    assert [item.turnover_krw for item in old] == [100.]
    assert sorted(item.turnover_krw for item in new) == [100., 150.]
    storage.save_state('calendar:test', {'known': 'old'}, end)
    storage.save_state('calendar:test', {'known': 'corrected'}, end + timedelta(minutes=1))
    assert storage.read_state('calendar:test', end + timedelta(seconds=5)) == {'known': 'old'}
    assert storage.read_state('calendar:test', start) is None
    assert storage.read_state('calendar:test') == {'known': 'corrected'}


def test_news_relationship_strength_is_available_only_after_revision():
    first = datetime(2026, 10, 12, 9, 10, tzinfo=KST)
    revised = first + timedelta(minutes=10)
    event = {'status': 'VERIFIED', 'event_id': 'news-test', 'provider': 'OFFICIAL_PRESS',
        'source_url': 'https://example.invalid/revision', 'confidence': 1., 'relationship': 'DIRECT_VERIFIED',
        'tickers': ['000001', '000002'], 'theme_ids': ['new_policy'], 'first_seen_at': first.isoformat(),
        'revision_available_at': revised.isoformat(), 'ticker_relations': [
            {'symbol': '000001', 'relationship': 'DIRECT_VERIFIED', 'confidence': 1.},
            {'symbol': '000002', 'relationship': 'THEMATIC_ASSOCIATION', 'confidence': .35}]}
    rows = runtime.configured_relations(revised, [event])
    linked = [item for item in rows if item.theme_id == 'new_policy']
    assert all(item.first_seen_at == revised and item.effective_from == revised for item in linked)
    assert {item.symbol: item.relationship for item in linked}['000002'] == 'THEMATIC_ASSOCIATION'
    assert not any(item.theme_id == 'new_policy' for item in storage.read_relations(first + timedelta(minutes=1)))
    assert not any(item.theme_id == 'new_policy' for item in runtime.configured_relations(revised, [{**event, 'status': 'RETRACTED'}], historical=True))


def test_known_ready_gate_bridge_allows_candidate_but_unknown_gate_stays_unknown():
    decision = evaluate_early_entry(fixture_scenario(as_of='2026-10-08T09:30:02+09:00'))
    assert decision['state'] == 'EARLY_ENTRY_READY'
    result = runtime.rotation_entry_candidate(decision, 'policy', 'demo')
    assert result['trading_halted'] is False and result['vi_active'] is False
    assert result['extreme_spread'] is False and result['liquidity_ok'] is True
    unknown = runtime.rotation_entry_candidate({**decision, 'gates': {}}, 'policy', 'demo')
    assert all(unknown[key] is None for key in ('trading_halted', 'vi_active', 'extreme_spread', 'liquidity_ok'))


def test_provider_failure_stops_current_ranking_even_with_recent_cached_data(monkeypatch):
    now = datetime(2026, 10, 12, 9, 10, tzinfo=KST)
    monkeypatch.setenv('TOSS_DATA_MODE', 'auto')
    calendar = {'today': {'date': now.date().isoformat(), 'integrated': {'regularMarket': {
        'startTime': now.replace(minute=0).isoformat(), 'endTime': now.replace(hour=15, minute=30).isoformat()}}}}
    storage.save_state('calendar:' + now.date().isoformat(), calendar, now - timedelta(minutes=1))
    storage.save_provider_health('TOSS_TURNOVER', {'status': 'UNAVAILABLE',
        'last_success_at': (now - timedelta(seconds=5)).isoformat(), 'reason': 'Provider schema failed.'}, now)
    monkeypatch.setattr(runtime, 'compute_theme_flows', lambda *args, **kwargs: pytest.fail('A failed provider must stop current ranking.'))
    result = runtime.current_overview(now, persist=True)
    assert result['live_opening_flow_top3']['items'] == []
    assert result['entry_ready_top3']['items'] == []
    assert result['data_freshness']['quotes']['status'] == 'UNAVAILABLE'


def test_quote_only_read_never_mints_token_or_fills_missing_prices(client, monkeypatch):
    from app.brokers.toss.documented import OfficialTossClient
    async def forbidden_auth(*args, **kwargs):
        pytest.fail('Reading cached quotes must never trigger OAuth.')
    monkeypatch.setattr(OfficialTossClient, 'authenticate', forbidden_auth)
    for _ in range(2):
        response = client.get('/api/market/quotes')
        assert response.status_code == 200
        data = response.json()
        assert data['source'] == 'toss'
        assert data['status'] == 'UNAVAILABLE'
        assert data['items'] == []
        assert 'measurement_definition' in data
    assert client.get('/api/connection').json()['mode'] == 'demo'


def test_quote_only_route_can_show_price_before_a_full_analysis_bundle(client, monkeypatch):
    from app.radar.quotes import save_quotes
    from app.settings import runtime_dir
    now = datetime.now(KST)
    monkeypatch.setenv('TOSS_DATA_MODE', 'auto')
    # Constructed provider proof/calendar; this is an offline integration case.
    (runtime_dir() / 'toss-probe.json').write_text(json.dumps({
        'authentication': {'status': 'passed'}, 'venue': {'verified': True}}))
    calendar = {'today': {'date': now.date().isoformat(), 'integrated': {'preMarket': {
        'startTime': (now - timedelta(minutes=1)).isoformat(),
        'endTime': (now + timedelta(minutes=1)).isoformat()}}}}
    storage.save_state('calendar:' + now.date().isoformat(), calendar, now - timedelta(seconds=1))
    save_quotes([{'symbol': '005930', 'currency': 'KRW', 'lastPrice': '123450',
                  'timestamp': now.isoformat()}], now, ['005930'])
    data = client.get('/api/market/quotes').json()
    assert data['status'] == 'LIVE'
    assert data['session_id'] == 'NXT_PRE_MARKET'
    assert data['items'][0]['price'] == 123450
    assert data['items'][0]['quote_at'] == now.isoformat()
    assert data['items'][0]['change_pct'] is None
    # No fabricated investor flow, analysis snapshot, entry or daily chart.
    assert client.get('/api/connection').json()['mode'] == 'demo'
    assert client.get('/api/recommendations?view=entry-ready').json()['items'] == []


def test_additive_migration_and_down_preserve_existing_paper_data():
    with storage.connection() as conn:
        conn.execute("INSERT INTO notification_read VALUES ('legacy-user-read', '2026-10-12')")
        conn.execute("INSERT INTO backtest_runs(created_at,config_json,result_json) VALUES ('2026-10-12','{}','{}')")
        path = Path(conn.execute('PRAGMA database_list').fetchone()[2])
    backup = path.with_name('before-down.sqlite3')
    with sqlite3.connect(path) as conn, sqlite3.connect(backup) as archive:
        conn.backup(archive)
        conn.executescript((storage.SQL_PATH.parent / '002_quote_monitor_down.sql').read_text())
        conn.executescript((storage.SQL_PATH.parent / '001_radar_v12_down.sql').read_text())
        assert conn.execute('SELECT id FROM notification_read').fetchone()[0] == 'legacy-user-read'
        assert conn.execute('SELECT COUNT(*) FROM backtest_runs').fetchone()[0] == 1
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='minute_symbol_metrics'").fetchone()
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='quote_observations'").fetchone()
    with sqlite3.connect(backup) as archive:
        assert archive.execute("SELECT 1 FROM sqlite_master WHERE name='minute_symbol_metrics'").fetchone()
        assert archive.execute("SELECT 1 FROM sqlite_master WHERE name='quote_observations'").fetchone()
    with storage.connection() as conn:
        assert conn.execute("SELECT version FROM schema_migrations WHERE version='radar_v12_001'").fetchone()
        assert conn.execute("SELECT version FROM schema_migrations WHERE version='quote_monitor_002'").fetchone()
        assert conn.execute('SELECT COUNT(*) FROM backtest_runs').fetchone()[0] == 1
