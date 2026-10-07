def test_health_and_data_are_explicitly_demo(client):
    health = client.get('/api/health').json()
    assert health['status'] == 'ok'
    assert health['mode'] == 'demo' and not health['liveTrading']
    assert not health['broker']['ready']
    market = client.get('/api/market').json()
    assert market['source'] == 'synthetic' and market['session'] == 'SIMULATED'
    assert '미검증' in market['venueLabel']
    connection = client.get('/api/connection').json()
    assert connection['credentials'] == connection['broker']['credentials']
    assert connection['ready'] is False and connection['venue_verified'] is False
    assert connection['mode'] == 'demo' and connection['brokerMode'] == 'blocked'


def test_candidates_detail_filters_and_errors(client):
    data = client.get('/api/stocks').json()
    assert len(data['items']) == 15 and len(data['recommended']) == 3
    assert all(s['eligible'] and s['risk']['stop'] > 0 for s in data['recommended'])
    detail = client.get('/api/stocks/010170').json()
    assert detail['name'] == '대한광통신' and len(detail['candles']) == 90
    assert len(detail['intraday']) == 85 and detail['intraday'][-1]['time'] == detail['asOf']
    assert detail['intraday'][0]['interval'] == '1m'
    assert detail['sparkline'] == [c['close'] for c in detail['candles'][-20:]]
    assert detail['candles'][-1]['ma5'] == detail['technical']['ma5']
    result = client.get('/api/stocks', params={'theme': 'battery'}).json()
    assert len(result['items']) == 3 and all(s['themeId'] == 'battery' for s in result['items'])
    assert client.get('/api/stocks/999999').status_code == 404


def test_strategy_update_recalculates_and_preserves_invalid_config(client):
    before = client.get('/api/strategy').json()
    assert client.put('/api/strategy', json={'risk': {'min_risk_reward': 4}}).status_code == 200
    assert not client.get('/api/stocks', params={'recommended': True}).json()['items']
    assert client.put('/api/strategy', json={'weights': {'theme_strength': 99}}).status_code == 422
    assert client.put('/api/strategy', json={'risk': {'bogus': 42}}).status_code == 422
    after = client.get('/api/strategy').json()
    assert after['weights'] == before['weights']
    assert after['risk']['min_risk_reward'] == 4


def test_theme_edit_affects_strength_and_candidate_eligibility(client):
    response = client.put('/api/themes/optical_networking', json={'memberSymbols': ['010170']})
    assert response.status_code == 200
    assert not response.json()['item']['qualified']
    assert not client.get('/api/stocks/010170').json()['eligible']
    assert client.put('/api/themes/optical_networking', json={'memberSymbols': ['999999']}).status_code == 422
    assert client.put('/api/themes/optical_networking', json={'usTickers': ['invalid']}).status_code == 422


def test_theme_create_delete_and_unassigned_fallback(client):
    response = client.post('/api/themes', json={'id': 'custom', 'name': '사용자 테마', 'memberSymbols': ['010170'], 'usTickers': ['NVDA']})
    assert response.status_code == 201
    assert client.delete('/api/themes/custom').status_code == 200
    assert client.delete('/api/themes/optical_networking').status_code == 200
    assert client.get('/api/stocks/010170').json()['theme'] == '미분류'


def test_paper_buy_sell_persistence_and_invalid_quantity(client):
    assert client.get('/api/paper').json()['summary']['openPositions'] == 0
    assert client.post('/api/paper/buy', json={'symbol': '010170', 'quantity': 0}).status_code == 422
    response = client.post('/api/paper/buy', json={'symbol': '010170', 'quantity': 10})
    assert response.status_code == 201
    trade_id = response.json()['id']
    assert client.get('/api/paper').json()['summary']['invested'] == 180500
    sold = client.post(f'/api/paper/{trade_id}/sell')
    assert sold.json()['summary']['closedPositions'] == 1
    assert sold.json()['history'][0]['exitReason'] == 'MANUAL'
    assert client.post(f'/api/paper/{trade_id}/sell').status_code == 409
    assert client.post('/api/paper/999/sell').status_code == 404


def test_notifications_read_survive_requests(client):
    data = client.get('/api/notifications').json()
    assert data['unread'] > 0
    ids = [item['id'] for item in data['items']]
    assert client.post('/api/notifications/read', json={'ids': ids}).json()['unread'] == 0
    assert client.get('/api/notifications').json()['unread'] == 0


def test_backtest_run_records_simulated_results(client):
    result = client.post('/api/backtest/run').json()
    assert result['runId'] > 0 and result['mode'] == 'demo'
    assert result['summary']['signalCount'] == len(result['trades'])
    assert result['assumptions']['intrabar'] == 'stop first when both stop and target touched'
    assert client.post('/api/orders', json={}).status_code == 404


def test_maximum_configured_ma_window_and_confirmation_minutes(client):
    assert client.put('/api/strategy', json={'moving_average': {'medium': 60}, 'entry': {'confirmation_minutes': 30}}).status_code == 200
    detail = client.get('/api/stocks/010170')
    assert detail.status_code == 200
    data = detail.json()
    assert data['technical']['mediumPeriod'] == 60
    assert data['scores']['realtime'] == 20
    assert client.put('/api/strategy', json={'moving_average': {'short': 20}}).status_code == 200
    assert client.get('/api/stocks/010170').json()['technical']['shortPeriod'] == 20
