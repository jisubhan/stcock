"""Invented scenarios. Dates label tests, not real-world market observations."""
from datetime import date, datetime, time, timedelta
from app.radar.models import FlowObservation, HistoricalFlowObservation, ThemeRelation, KST

SCENARIOS = ('domestic_rotation', 'us_leadership', 'reverse_rotation', 'sector_divergence',
             'single_name_only', 'pump_then_fade', 'data_outage')
SNAPSHOT_TIMES = ('08:55', '09:02', '09:05', '09:10', '09:20', '09:30')
THEMES = {
    'optical_us': ('미국 선행 광통신 · 합성', ['US01', 'US02', 'US03']),
    'ai_us': ('미국 선행 AI · 합성', ['AI01', 'AI02', 'AI03']),
    'energy_us': ('미국 선행 에너지 · 합성', ['EN01', 'EN02', 'EN03']),
    'domestic_policy': ('국내 정책 재료 · 합성', ['KR01', 'KR02', 'KR03']),
    'tender_single': ('공개매수 단일 종목 · 합성', ['TD01', 'TD02', 'TD03']),
}


def clock(day, label):
    return datetime.combine(date.fromisoformat(day), time.fromisoformat(label), KST)


def load_scenario(day='2026-10-12', scenario='domestic_rotation'):
    if scenario not in SCENARIOS:
        raise ValueError('Unknown synthetic opening scenario.')
    start = clock(day, '09:00')
    known = clock(day, '08:00')
    source = 'https://example.invalid/flowtrade/synthetic/'
    relations = [ThemeRelation(theme_id=theme_id, theme_name=name, symbol=symbol,
                    effective_from=known, first_seen_at=known, source_url=source + theme_id,
                    relationship='THEMATIC_ASSOCIATION', provider='SYNTHETIC_FIXTURE')
                 for theme_id, (name, symbols) in THEMES.items() for symbol in symbols]
    hypotheses = [{'theme_id': theme_id, 'theme_name': THEMES[theme_id][0], 'score': score,
                   'first_seen_at': known.isoformat(), 'as_of': known.isoformat(), 'mode': 'replay',
                   'source_urls': [source + theme_id], 'explanation': '합성 미국 종가 자료로 만든 사전 가설입니다.'}
                  for theme_id, score in [('optical_us', 92), ('ai_us', 86), ('energy_us', 80)]]
    observations, history = [], []
    for theme_id, (_, symbols) in THEMES.items():
        for index, symbol in enumerate(symbols):
            last_price = 10000 + index * 100
            for minute in range(1, 31):
                end = start + timedelta(minutes=minute)
                flow, momentum = _activity(scenario, theme_id, index, minute)
                next_price = last_price * (1 + momentum / 100)
                observations.append(FlowObservation(symbol=symbol, observed_at=end, first_seen_at=end,
                    interval_start=end - timedelta(minutes=1), interval_end=end, session_id='KRX_REGULAR',
                    venue='CONSOLIDATED', turnover_krw=flow, price_open=last_price, price_close=next_price,
                    quote_at=end, provider='SYNTHETIC_FIXTURE', source_url=source + symbol, mode='replay'))
                if scenario == 'data_outage' and minute >= 20:
                    # Explicitly invented stale quotes. Fresh turnover does not
                    # turn a two-minute-old quote into a LIVE entry observation.
                    observations[-1].quote_at = end - timedelta(minutes=2)
                last_price = next_price
            # Ten genuinely earlier *synthetic* sessions. Their availability is
            # earlier than replay time; no current-day outcome enters baselines.
            for days_ago in range(1, 11):
                earlier = start - timedelta(days=days_ago + 3)
                for minute in (2, 5, 10, 20, 30):
                    width = min(5, minute)
                    end = earlier + timedelta(minutes=minute)
                    history.append(HistoricalFlowObservation(symbol=symbol, session_id='KRX_REGULAR',
                        session_date=earlier.date(), minute_from_session=minute, window_minutes=width,
                        turnover_krw=100_000_000 * width, observed_at=end, first_seen_at=end,
                        venue='CONSOLIDATED', provider='SYNTHETIC_FIXTURE', source_url=source + 'baseline', mode='replay'))
    holiday = clock(day, '08:15') - timedelta(days=3)
    events = [_event('policy-demo', 'domestic_policy', ['KR01', 'KR02', 'KR03'], holiday, known,
                     'POLICY', '정부 정책 발표 형식의 합성 테스트 재료', source),
              _event('tender-demo', 'tender_single', ['TD01'], known, known, 'TENDER_OFFER',
                     '공개매수 형식의 합성 테스트 공시', source),
              _event('late-news-demo', 'domestic_policy', ['KR01'], clock(day, '09:53'), clock(day, '09:53'),
                     'POLICY', '09:53 이후에만 사용 가능한 합성 후속 보도', source)]
    return {'session': day, 'scenario': scenario, 'start': start, 'relations': relations,
            'hypotheses': hypotheses, 'observations': observations, 'history': history, 'catalysts': events,
            'last_trading_close': clock(day, '15:30') - timedelta(days=4),
            'data_source': 'SYNTHETIC_FIXTURE', 'actual_market_data': False}


def _event(event_id, theme_id, tickers, published, seen, kind, title, source):
    return {'event_id': event_id, 'provider': 'SYNTHETIC_FIXTURE', 'source_url': source + event_id,
            'title': title, 'published_at': published.isoformat(), 'first_seen_at': seen.isoformat(),
            'ingested_at': seen.isoformat(), 'updated_at': seen.isoformat(), 'event_type': kind,
            'tickers': tickers, 'theme_ids': [theme_id], 'relationship': 'DIRECT_VERIFIED',
            'evidence': [{'source_url': source + event_id, 'claim': '합성 테스트 입력이며 실제 사실이 아닙니다.', 'strength': 1.0}],
            'novelty_score': 1.0, 'materiality_score': 0.9, 'confidence': 1.0,
            'status': 'VERIFIED', 'coverage_status': 'DEMO', 'duplicate_group_id': event_id, 'mode': 'replay'}


def _activity(scenario, theme, index, minute):
    base = 100_000_000 * (1 - index * .04)
    if theme == 'domestic_policy':
        strong = scenario in ('domestic_rotation', 'data_outage') or scenario == 'pump_then_fade' and minute <= 15
        strong = strong or scenario == 'reverse_rotation' and minute <= 10
        strong = strong or scenario == 'sector_divergence' and index == 0
        if strong:
            return base * (6 if minute <= 10 else 7), .12 if index != 2 else .09
        if scenario == 'pump_then_fade' and minute > 15:
            return base * .18, -.25
        return base * .18, -.025
    if theme == 'tender_single':
        return (base * 8, .3) if index == 0 else (base * .1, -.02)
    if theme in ('optical_us', 'ai_us', 'energy_us'):
        multiplier = {'optical_us': 3.5, 'ai_us': 2.7, 'energy_us': 2.0}[theme]
        strong = scenario == 'us_leadership' or scenario == 'reverse_rotation' and minute > 10
        strong = strong or scenario == 'pump_then_fade' and minute > 15
        if strong:
            return base * multiplier, .08
        return base * (.28 if theme == 'optical_us' else .42), -.015
    return base, 0
