"""Manufactured observations for pure discovery contracts, not market history."""
from copy import deepcopy

import pytest

from app.discovery.themes import build_theme_hypotheses


CLOCK = '2026-10-12T09:10:05+09:00'
SEEN = '2026-10-12T09:10:01+09:00'
FUTURE = '2026-10-12T09:11:00+09:00'
URL = 'https://openapi.tossinvest.com/api/v1/sectors'


def fixture():
    sectors = [{'sectorId': str(i), 'name': '제조 업종 ' + str(i), 'depth': 1,
                'parentSectorId': None, 'first_seen_at': SEEN, 'source_url': URL} for i in range(1, 5)]
    stocks, memberships, rankings = [], [], []
    for index in range(1, 5):
        for child in range(3):
            symbol = f'{index * 10 + child:06}'
            stocks.append({'symbol': symbol, 'name': '제조 관측 ' + symbol,
                           'quote': {'price': 10000 + index, 'quote_at': '2026-10-12T09:10:00+09:00',
                                     'first_seen_at': SEEN, 'status': 'LIVE'}})
            memberships.append({'sector_id': str(index), 'symbol': symbol,
                                'first_seen_at': SEEN, 'source_url': URL,
                                'relationship': 'OFFICIAL_SECTOR_MEMBER'})
            rankings.append({'rank': len(rankings) + 1, 'symbol': symbol, 'currency': 'KRW',
                             'price': {'lastPrice': '10000', 'basePrice': '9900', 'changeRate': '.01'},
                             'tradingAmount': '100000000', 'tradingVolume': '10000'})
    return {'source': 'toss', 'first_seen_at': SEEN, 'sectors': sectors, 'stocks': stocks,
            'memberships': memberships, 'technical': {}, 'calendar': {},
            'sector_rankings': {'KR': {'items': [{'sectorId': str(i), 'name': '제조 업종 ' + str(i),
                                  'rank': i, 'changeRate': str(.04 / i), 'tradingAmount': '300000000'} for i in range(1, 5)],
                                  'first_seen_at': SEEN, 'provider_at': None, 'quality': 'ANALYSIS_ONLY'},
                               'US': {'items': [], 'weekly_items': [], 'first_seen_at': SEEN,
                                      'provider_at': None, 'quality': 'ANALYSIS_ONLY'}},
            'global_rankings': {'rankedAt': '2026-10-12T09:10:00+09:00', 'rankings': rankings, 'first_seen_at': SEEN}}


def test_three_evidence_backed_themes_and_watch_stocks():
    result = build_theme_hypotheses(fixture(), CLOCK)
    assert len(result['hypotheses_top3']) == 3
    assert result['hypotheses_top3'][0]['id'] == 'sector:1'
    for theme in result['hypotheses_top3']:
        assert theme['quality'] == 'ANALYSIS_ONLY'
        assert theme['flow_confirmed'] is False
        assert len(theme['recommended_stocks']) == 3
        assert all(row['association_kind'] == 'OFFICIAL_SECTOR_MEMBER' for row in theme['members'])
        assert all(row['quote']['status'] == 'LIVE' for row in theme['recommended_stocks'])


def test_missing_us_flow_and_catalyst_stay_nullable():
    theme = build_theme_hypotheses(fixture(), CLOCK)['themes'][0]
    for key in ('us_1d', 'us_1w', 'domestic_velocity', 'catalyst', 'chart_structure'):
        assert theme['factors'][key]['value'] is None
        assert theme['factors'][key]['points'] is None
    assert theme['factor_coverage'] < 1
    assert theme['score'] <= theme['available_max_points']


def test_revalidated_sector_feed_preserves_original_availability_without_live_upgrade():
    value = fixture()
    value['sector_rankings']['US'].update(items=[{'sectorId': '1', 'changeRate': '.03'}],
        first_seen_at='2026-10-09T09:10:00+09:00', last_success_at=SEEN)
    theme = build_theme_hypotheses(value, CLOCK)['themes'][0]
    assert theme['factors']['us_1d']['value'] == 3
    assert theme['factors']['us_1d']['quality'] == 'ANALYSIS_ONLY'
    evidence = next(row for row in theme['evidence'] if '미국 1일' in row['title'])
    assert evidence['first_seen_at'] == '2026-10-09T09:10:00+09:00'
    assert evidence['provider_at'] is None


def test_future_sector_revalidation_receipt_is_not_available():
    value = fixture()
    value['sector_rankings']['US'].update(items=[{'sectorId': '1', 'changeRate': '.03'}], last_success_at=FUTURE)
    assert build_theme_hypotheses(value, CLOCK)['themes'][0]['factors']['us_1d']['value'] is None


def test_change_rate_fraction_is_percent_once():
    theme = build_theme_hypotheses(fixture(), CLOCK)['themes'][0]
    assert theme['factors']['domestic_sector_performance']['value'] == 4
    assert theme['members'][0]['change_pct'] == 1


def test_daily_trading_amount_is_never_velocity_or_net_buying():
    value = fixture()
    for row in value['global_rankings']['rankings']:
        row['tradingAmount'] = '999999999999999'
    theme = build_theme_hypotheses(value, CLOCK)['themes'][0]
    assert theme['factors']['domestic_velocity']['value'] is None
    assert theme['flow_confirmed'] is False
    assert theme['turnover']['raw_member_trading_amount_krw'] > 0


def test_future_snapshot_is_unavailable_without_reading_poison():
    value = fixture()
    value.update(first_seen_at=FUTURE, stocks=None, sectors=None)
    result = build_theme_hypotheses(value, CLOCK)
    assert result['hypotheses_top3'] == []
    assert 'DISCOVERY_UNAVAILABLE_AT_AS_OF' in result['reason_codes']


@pytest.mark.parametrize('field', ['first_seen_at', 'provider_at'])
def test_future_sector_feed_does_not_change_current_hypotheses(field):
    value = fixture()
    value['sector_rankings']['US'][field] = FUTURE
    value['sector_rankings']['US']['items'] = [{'sectorId': '1', 'changeRate': 'poison'}]
    assert build_theme_hypotheses(value, CLOCK)['hypotheses_top3'] == build_theme_hypotheses(fixture(), CLOCK)['hypotheses_top3']


def test_future_member_and_quote_are_not_available():
    value = fixture()
    value['memberships'][0]['first_seen_at'] = FUTURE
    value['stocks'][1]['quote']['first_seen_at'] = FUTURE
    theme = build_theme_hypotheses(value, CLOCK)['themes'][0]
    assert len(theme['members']) == 2
    assert sum(row['quote'] is not None for row in theme['recommended_stocks']) == 1
    assert next(row for row in theme['members'] if row['symbol'] == '000011')['price'] is None


def test_future_global_timestamp_cannot_supply_breadth_or_unmapped_leaders():
    value = fixture()
    value['global_rankings']['rankedAt'] = FUTURE
    value['global_rankings']['rankings'].append({'symbol': '999999', 'price': 'poison'})
    result = build_theme_hypotheses(value, CLOCK)
    assert result['unmapped_leaders'] == []
    assert all(theme['factors']['domestic_breadth']['value'] is None for theme in result['themes'])


def test_unknown_or_inferred_membership_does_not_make_official_member():
    value = fixture()
    value['memberships'][0]['relationship'] = 'THEMATIC_ASSOCIATION'
    result = build_theme_hypotheses(value, CLOCK)
    assert all(row['symbol'] != '000010' for row in result['themes'][0]['members'])
    assert any(row['symbol'] == '000010' for row in result['unmapped_leaders'])


def test_no_facts_does_not_pad_three_hypotheses_by_sector_name():
    value = fixture()
    value['stocks'], value['global_rankings']['rankings'] = [], []
    value['sector_rankings']['KR']['items'] = []
    result = build_theme_hypotheses(value, CLOCK)
    assert len(result['themes']) == 4
    assert result['hypotheses_top3'] == []
    assert all(row['score'] is None for row in result['themes'])


def test_factual_zero_is_distinct_from_missing():
    value = fixture()
    value['sector_rankings']['KR']['items'][0]['changeRate'] = '0'
    factor = build_theme_hypotheses(value, CLOCK)['themes'][0]['factors']['domestic_sector_performance']
    assert factor['value'] == 0 and factor['points'] == 0
    assert factor['quality'] == 'ANALYSIS_ONLY'


def test_us_requires_same_official_sector_or_explicit_mapping():
    value = fixture()
    value['sector_rankings']['US']['items'] = [{'sectorId': 'US-1', 'name': '제조 업종 1', 'changeRate': '.03'}]
    theme = build_theme_hypotheses(value, CLOCK)['themes'][0]
    assert theme['factors']['us_1d']['value'] is None
    value['sectors'][0]['us_sector_ids'] = ['US-1']
    assert build_theme_hypotheses(value, CLOCK)['themes'][0]['factors']['us_1d']['value'] == 3


def test_weekly_us_facts_have_separate_factor_and_no_live_label():
    value = fixture()
    value['sector_rankings']['US']['weekly_items'] = [{'sectorId': '1', 'changeRate': '.08'}]
    theme = build_theme_hypotheses(value, CLOCK)['themes'][0]
    assert theme['factors']['us_1w']['value'] == 8
    assert theme['factors']['us_1w']['quality'] == 'ANALYSIS_ONLY'
    assert theme['factors']['us_1d']['value'] is None


def test_parent_child_overlap_allocates_stock_amount_once():
    value = fixture()
    value['sectors'][1]['parentSectorId'] = '1'
    for row in value['memberships'][:3]:
        value['memberships'].append({**row, 'sector_id': '2'})
    result = build_theme_hypotheses(value, CLOCK)
    total = sum(theme['turnover']['allocated_trading_amount_krw'] or 0 for theme in result['themes'])
    assert total == pytest.approx(1_200_000_000)
    assert sum(theme['turnover']['allocated_share'] or 0 for theme in result['themes']) == pytest.approx(1)
    assert result['themes'][0]['turnover']['raw_member_trading_amount_krw'] == 300_000_000
    assert result['themes'][0]['turnover']['allocated_trading_amount_krw'] == 150_000_000


def test_unmapped_strong_stock_stays_visible_with_observed_rank():
    value = fixture()
    value['global_rankings']['rankings'].append({'symbol': '999999', 'rank': 1, 'currency': 'KRW',
        'price': {'lastPrice': '15000', 'changeRate': '.2'}, 'tradingAmount': '2000000000'})
    row = build_theme_hypotheses(value, CLOCK)['unmapped_leaders'][0]
    assert row['symbol'] == '999999' and row['rank'] == 1
    assert row['price'] == 15000
    assert row['classification_status'] == 'PENDING'
    assert row['quality'] == 'ANALYSIS_ONLY'


def test_partial_quotes_do_not_silently_claim_full_sector_market_coverage():
    value = fixture()
    value['stocks'] = value['stocks'][:1]
    theme = build_theme_hypotheses(value, CLOCK)['themes'][0]
    assert theme['coverage']['members_expected'] == 3
    assert theme['coverage']['members_observed'] == 1
    assert theme['coverage']['ratio'] == pytest.approx(1 / 3)
    assert 'PARTIAL_MEMBER_COVERAGE' in theme['reason_codes']


def test_news_title_alone_does_not_promote_direct_beneficiary():
    value = fixture()
    value['catalysts'] = [{'title': '제조 업종 1 수혜 기대', 'published_at': SEEN, 'first_seen_at': SEEN,
                          'source_url': 'https://publisher.invalid/story', 'theme_ids': ['sector:1'],
                          'status': 'VERIFIED', 'relationship': 'THEMATIC_ASSOCIATION',
                          'confidence': 1, 'materiality_score': 1}]
    theme = build_theme_hypotheses(value, CLOCK)['themes'][0]
    assert theme['factors']['catalyst']['value'] is None
    assert all(row['kind'] != 'CATALYST' for row in theme['evidence'])


def test_future_news_revision_does_not_supply_catalyst_points():
    value = fixture()
    value['catalysts'] = [{'title': '제조 공시', 'published_at': SEEN, 'first_seen_at': SEEN,
                          'revision_available_at': FUTURE, 'source_url': 'https://publisher.invalid/story',
                          'theme_ids': ['sector:1'], 'status': 'VERIFIED', 'relationship': 'DIRECT_VERIFIED',
                          'confidence': 1, 'materiality_score': 1}]
    assert build_theme_hypotheses(value, CLOCK)['themes'][0]['factors']['catalyst']['value'] is None


def test_stale_sector_receipt_is_not_current_hypothesis_fact():
    value = fixture()
    value['sector_rankings']['KR']['first_seen_at'] = '2026-10-09T09:10:00+09:00'
    assert all(theme['factors']['domestic_sector_performance']['value'] is None
               for theme in build_theme_hypotheses(value, CLOCK)['themes'])


def test_synthetic_source_is_rejected_and_input_not_mutated():
    value = fixture()
    original = deepcopy(value)
    build_theme_hypotheses(value, CLOCK)
    assert value == original
    value['source'] = 'synthetic'
    assert build_theme_hypotheses(value, CLOCK)['hypotheses_top3'] == []


def test_future_week_receipt_never_inherits_daily_available_timestamp():
    value = fixture()
    value['sector_rankings']['US'].update(weekly_first_seen_at=FUTURE,
        items=[{'sectorId': '1', 'changeRate': '.03'}], weekly_items=[{'sectorId': '1', 'changeRate': '.8'}])
    theme = build_theme_hypotheses(value, CLOCK)['themes'][0]
    assert theme['factors']['us_1d']['value'] == 3
    assert theme['factors']['us_1w']['value'] is None


def test_available_week_receipt_does_not_inherit_future_daily_receipt():
    value = fixture()
    value['sector_rankings']['US'].update(first_seen_at=FUTURE, weekly_first_seen_at=SEEN,
        items=[{'sectorId': '1', 'changeRate': '.03'}], weekly_items=[{'sectorId': '1', 'changeRate': '.08'}])
    theme = build_theme_hypotheses(value, CLOCK)['themes'][0]
    assert theme['factors']['us_1d']['value'] is None
    assert theme['factors']['us_1w']['value'] == 8


def test_observed_turnover_view_ranks_amount_not_daily_sector_gain():
    value = fixture()
    for row in value['global_rankings']['rankings']:
        if row['symbol'].startswith('00004'):
            row['tradingAmount'] = '900000000'
    result = build_theme_hypotheses(value, CLOCK)
    assert result['observed_leaders'][0]['id'] == 'sector:1'
    leader = result['observed_turnover_leaders'][0]
    assert leader['id'] == 'sector:4'
    assert leader['quality'] == 'OBSERVED'
    assert leader['observation_kind'] == 'OBSERVED_DAILY_TRADING_AMOUNT'
    assert leader['flow_confirmed'] is False
    assert leader['latency_seconds'] == 5


@pytest.mark.parametrize('ranked_at,quality', [(None, 'ANALYSIS_ONLY'), ('2026-10-12T09:09:00+09:00', 'STALE')])
def test_turnover_ranking_freshness_is_not_replaced_by_receipt_time(ranked_at, quality):
    value = fixture()
    value['global_rankings']['rankedAt'] = ranked_at
    leaders = build_theme_hypotheses(value, CLOCK)['observed_turnover_leaders']
    assert leaders and all(row['quality'] == quality for row in leaders)
    assert all(row['flow_confirmed'] is False for row in leaders)


def test_parent_child_hypotheses_do_not_fill_multiple_top3_slots():
    value = fixture()
    value['sectors'][1]['parentSectorId'] = '1'
    result = build_theme_hypotheses(value, CLOCK)
    ids = {row['id'] for row in result['hypotheses_top3']}
    assert not {'sector:1', 'sector:2'} <= ids
    assert 'TOP3_OVERLAPPING_CLASSIFICATION_DEDUPED' in result['themes'][1]['reason_codes']


def test_heavily_overlapping_classifications_return_less_than_three():
    value = fixture()
    value['sectors'] = value['sectors'][:2]
    value['memberships'] = value['memberships'][:3]
    for row in list(value['memberships']):
        value['memberships'].append({**row, 'sector_id': '2'})
    result = build_theme_hypotheses(value, CLOCK)
    assert len(result['hypotheses_top3']) == 1
    assert len(result['observed_turnover_leaders']) == 1


def test_us_only_sector_performance_cannot_pad_domestic_top3():
    value = fixture()
    value['sectors'] = value['sectors'][:2]
    value['memberships'] = value['memberships'][:3]
    value['sector_rankings']['US']['items'] = [{'sectorId': '2', 'changeRate': '.5'}]
    result = build_theme_hypotheses(value, CLOCK)
    assert [row['id'] for row in result['hypotheses_top3']] == ['sector:1']
    assert result['mapping_pending_themes'][0]['id'] == 'sector:2'
    assert result['mapping_pending_themes'][0]['state'] == 'THEME_MAPPING_PENDING'


def test_official_domestic_member_without_quote_remains_watch_with_pending_price():
    value = fixture()
    for row in value['stocks'][:3]:
        row['quote'] = None
    theme = next(row for row in build_theme_hypotheses(value, CLOCK)['themes'] if row['id'] == 'sector:1')
    assert theme['score'] is not None
    assert theme['recommended_stocks']
    assert all(row['state'] == 'WATCH' and row['price'] is None for row in theme['recommended_stocks'])


def test_us_calendar_phase_does_not_claim_sector_performance_is_current():
    value = fixture()
    value['calendar']['US'] = {'today': {'date': '2026-10-11', 'afterMarket': {
        'startTime': '2026-10-12T08:00:00+09:00', 'endTime': '2026-10-12T10:00:00+09:00'}}}
    context = build_theme_hypotheses(value, CLOCK)['us_session_context']
    assert context['label'] == 'US_AFTER_MARKET'
    assert context['calendar_status'] == 'VERIFIED_SCHEDULE'
    assert context['performance_session'] == 'UNVERIFIED_PROVIDER_TIME'


@pytest.mark.parametrize('contradiction', [{'source': 'synthetic'}, {'mode': 'demo'}, {'provider_at': FUTURE}, {'as_of': FUTURE}])
def test_sector_row_contradictory_provenance_or_future_is_not_relabelled(contradiction):
    value = fixture()
    value['sector_rankings']['US']['items'] = [{'sectorId': '1', 'changeRate': '.5', **contradiction}]
    assert build_theme_hypotheses(value, CLOCK)['themes'][0]['factors']['us_1d']['value'] is None


@pytest.mark.parametrize('contradiction', [{'source': 'synthetic'}, {'mode': 'demo'}, {'provider_at': FUTURE}])
def test_global_row_contradiction_is_rejected_without_hiding_actual_rows(contradiction):
    value = fixture()
    value['global_rankings']['rankings'].append({'symbol': '999999', 'rank': 1, 'currency': 'KRW',
        'price': {'lastPrice': '90000', 'changeRate': '.5'}, 'tradingAmount': '1000000000', **contradiction})
    result = build_theme_hypotheses(value, CLOCK)
    assert result['unmapped_leaders'] == []
    assert result['observed_turnover_leaders']


@pytest.mark.parametrize('contradiction', [{'source': 'synthetic'}, {'coverage_status': 'DEMO'}, {'mode': 'replay'}])
def test_synthetic_news_cannot_be_relabelled_as_actual_verified_catalyst(contradiction):
    value = fixture()
    value['catalysts'] = [{'title': '제조 공시', 'published_at': SEEN, 'first_seen_at': SEEN,
                          'source_url': 'https://publisher.invalid/story', 'theme_ids': ['sector:1'],
                          'status': 'VERIFIED', 'relationship': 'DIRECT_VERIFIED', 'mode': 'live',
                          'confidence': 1, 'materiality_score': 1, **contradiction}]
    assert build_theme_hypotheses(value, CLOCK)['themes'][0]['factors']['catalyst']['value'] is None
