"""T01/T02/T08/T14 synthetic scenarios; no real 10/12 story is asserted."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import httpx
import pytest
from pydantic import ValidationError

from app.catalysts import (CATALYST_SCHEMA_SQL, CatalystService, CatalystStore, FeedContract,
                          FixtureAdapter, LicensedJSONFeedAdapter, RawCatalyst,
                          adapters_from_environment, normalize_event)
from app.catalysts.models import Coverage, EventStatus, ProviderCursor


def at(value):
    return datetime.fromisoformat(value)


def raw(provider='DEMO_NEWS_A', item='n1', **changes):
    data = dict(provider=provider, provider_item_id=item, source_url='https://example.test/news/' + item,
                title='DEMO 정책 논의 기사', published_at='2026-10-09T10:00:00+09:00',
                event_type='POLICY', status='UNCONFIRMED', source_kind='LICENSED_NEWS',
                explicit_tickers=['005930'], confidence=.8, certainty='DISCUSSION',
                evidence=[dict(source_url='https://example.test/news/' + item, claim='Synthetic scenario: policy discussion only', strength=.6)])
    data.update(changes)
    return RawCatalyst.model_validate(data)


@pytest.fixture
def store(tmp_path):
    path = tmp_path / 'catalysts.sqlite3'
    @contextmanager
    def connection():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.executescript(CATALYST_SCHEMA_SQL)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return CatalystStore(connection)


def service(store, events, provider='DEMO_NEWS_A'):
    return CatalystService([FixtureAdapter(events, provider)], store=store,
                           themes=[{'id': 'policy', 'name': '국내 정책', 'memberSymbols': ['005930']}])


def test_T01_holiday_rollover_from_actual_last_trading_close(store):
    friday = raw()
    weekend = raw(item='n2', published_at='2026-10-11T18:00:00+09:00')
    worker = service(store, [friday, weekend])
    last_close = at('2026-10-08T20:00:00+09:00')
    now = at('2026-10-12T08:00:00+09:00')
    asyncio.run(worker.poll(now=now, last_trading_close=last_close))
    events = worker.list_events(last_trading_close=last_close, as_of=now, mode='demo')
    assert len(events) == 2 and all(event['coverage_status'] == 'DEMO' for event in events)
    assert store.cursor('DEMO_NEWS_A').since == last_close
    repeat = asyncio.run(worker.poll(now=now + timedelta(minutes=1)))
    assert repeat['duplicates'] == 2 and len(worker.revisions(events[0]['event_id'], as_of=now + timedelta(minutes=1))) == 1


def test_T02_common_story_dedupe_preserves_distinct_facts_and_revisions(store):
    original = raw(canonical_story_id='verified-common-fact-123')
    duplicate = raw(provider='DEMO_NEWS_B', item='b1', canonical_story_id='verified-common-fact-123')
    worker = CatalystService([FixtureAdapter([original], 'DEMO_NEWS_A'), FixtureAdapter([duplicate], 'DEMO_NEWS_B')], store=store)
    t0 = at('2026-10-12T09:01:00+09:00')
    asyncio.run(worker.poll(now=t0))
    events = worker.list_events(as_of=t0, mode='demo')
    assert len(events) == 1 and events[0]['providers'] == ['DEMO_NEWS_A', 'DEMO_NEWS_B']
    event_id = events[0]['event_id']
    distinct = raw(item='n3', canonical_story_id='another-policy-fact')
    worker.adapters = [FixtureAdapter([distinct], 'DEMO_NEWS_A')]
    asyncio.run(worker.poll(now=t0 + timedelta(minutes=1)))
    assert len(worker.list_events(as_of=t0 + timedelta(minutes=1), mode='demo')) == 2
    correction = raw(provider='DEMO_NEWS_B', item='b1', canonical_story_id='verified-common-fact-123',
                     status='CORRECTED', title='DEMO 계획이 아닌 논의로 정정', provider_revision_at='2026-10-12T09:15:00+09:00',
                     evidence=[{'source_url': 'https://example.test/news/b1', 'claim': 'Corrected synthetic claim: discussion only', 'strength': .6}])
    worker.adapters = [FixtureAdapter([correction], 'DEMO_NEWS_B')]
    asyncio.run(worker.poll(now=t0 + timedelta(minutes=19)))
    before = next(event for event in worker.list_events(as_of=t0 + timedelta(minutes=9), mode='demo') if event['event_id'] == event_id)
    after = next(event for event in worker.list_events(as_of=t0 + timedelta(minutes=19), mode='demo') if event['event_id'] == event_id)
    assert before['status'] == 'UNCONFIRMED' and after['status'] == 'CORRECTED'
    assert before['first_seen_at'] == after['first_seen_at']
    assert [proof['claim'] for proof in after['evidence']] == ['Corrected synthetic claim: discussion only']
    assert any(proof['claim'].startswith('Synthetic scenario:') for proof in before['evidence'])
    withdrawn = correction.model_copy(update={'status': EventStatus.RETRACTED, 'provider_revision_at': at('2026-10-12T09:25:00+09:00')})
    worker.adapters = [FixtureAdapter([withdrawn], 'DEMO_NEWS_B')]
    asyncio.run(worker.poll(now=t0 + timedelta(minutes=29)))
    current = next(event for event in worker.list_events(as_of=t0 + timedelta(minutes=29), mode='demo') if event['event_id'] == event_id)
    assert current['status'] == 'RETRACTED' and len(worker.revisions(event_id, as_of=t0 + timedelta(minutes=29))) == 4


def test_T08_first_seen_and_revision_availability_prevent_lookahead(store):
    article = raw(published_at='2026-10-12T09:00:00+09:00')
    worker = service(store, [article])
    seen = at('2026-10-12T09:53:00+09:00')
    asyncio.run(worker.poll(now=seen))
    assert worker.list_events(as_of=at('2026-10-12T09:10:00+09:00'), mode='demo') == []
    assert len(worker.list_events(as_of=seen, mode='demo')) == 1
    with pytest.raises(ValidationError):
        raw(first_seen_at='2026-10-12T08:00:00+09:00')
    with pytest.raises(ValidationError):
        raw(published_at='2026-10-12T09:00:00')
    with pytest.raises(ValidationError):
        raw(official_reference_verified='true')
    with pytest.raises(ValueError):
        normalize_event(raw(published_at='2026-10-12T10:00:00+09:00'), now=seen)


def contract(**changes):
    value = dict(provider='NEWS_TEST', endpoint='https://feed.example.test/events',
                 official_document_url='https://feed.example.test/docs', allowed_domains=['feed.example.test'],
                 source_kind='LICENSED_NEWS', coverage=['licensed domestic news metadata'],
                 contract_verified=True, response_schema_verified=True, license_allowed=True,
                 incremental_revision_contract_verified=True, since_query='since',
                 initial_since={'since': '2026-10-08T20:00:00+09:00'},
                 response_schema={'type': 'object', 'required': ['events'], 'properties': {'events': {'type': 'array', 'items': {'type': 'object'}}}},
                 records_path='events', field_map={name: name for name in ('provider_item_id', 'source_url', 'title', 'published_at', 'evidence')},
                 auth_env='NEWS_API_KEY')
    value.update(changes)
    return FeedContract.model_validate(value)


@pytest.mark.parametrize('status', [401, 403, 429])
def test_T14_auth_or_rate_block_has_no_fabricated_events_or_repeated_calls(status):
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(status, headers={'Retry-After': '60'}, json={'token': 'never-log-this', 'article': 'not-news'})
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            adapter = LicensedJSONFeedAdapter(contract(), client=http, env={'NEWS_API_KEY': 'test-secret'})
            for _ in range(2):
                assert (await adapter.poll_since(ProviderCursor())).events == []
            health = adapter.health().model_dump_json()
            assert 'test-secret' not in health and 'never-log-this' not in health
            assert adapter.health().status == Coverage.UNAVAILABLE
    asyncio.run(check())
    assert len(calls) == 1


@pytest.mark.parametrize('changes,env,reason', [({'license_allowed': False}, {'NEWS_API_KEY': 'test'}, 'LICENSE_NOT_APPROVED'),
                                              ({'contract_verified': False}, {'NEWS_API_KEY': 'test'}, 'CONTRACT_NOT_VERIFIED'),
                                              ({}, {}, 'CREDENTIAL_BINDING_MISSING')])
def test_T14_unapproved_license_contract_or_missing_binding_sends_no_request(changes, env, reason):
    calls = []
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request))) as http:
            adapter = LicensedJSONFeedAdapter(contract(**changes), client=http, env=env)
            assert (await adapter.poll_since(ProviderCursor())).events == []
            assert reason in adapter.health().reason_codes
    asyncio.run(check())
    assert calls == []


def test_alias_or_policy_title_never_proves_direct_beneficiary():
    event = normalize_event(raw(explicit_tickers=[], title='DEMO 삼성전자 정책 방문', source_kind='OFFICIAL_PRESS',
                                official_reference_verified=True), now=at('2026-10-12T08:00:00+09:00'), aliases={'005930': ['삼성전자']})
    assert event.relationship == 'THEMATIC_ASSOCIATION'
    assert 'WATCH_ONLY_RELATIONSHIP' in event.explanation_codes
    assert 'POLICY_NOT_A_CONFIRMED_CONTRACT' in event.explanation_codes


def test_unconfigured_provider_coverage_is_unavailable_not_no_events(monkeypatch):
    monkeypatch.delenv('CATALYST_PROVIDER_CONFIG', raising=False)
    adapters = adapters_from_environment()
    assert {adapter.provider for adapter in adapters} == {'DART', 'KIND', 'LICENSED_NEWS', 'OFFICIAL_PRESS'}
    assert all(adapter.health().status == Coverage.UNAVAILABLE and adapter.health().required_configuration for adapter in adapters)
    assert adapters[0].health().coverage != adapters[2].health().coverage


def test_verified_custom_feed_mock_ingests_and_advances_cursor_only_with_valid_batch(store):
    now = datetime.now(timezone.utc)
    article = raw(provider='NEWS_TEST', published_at=now - timedelta(hours=1), mode='live').model_dump(mode='json')
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={'events': [article]})
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            adapter = LicensedJSONFeedAdapter(contract(), client=http, env={'NEWS_API_KEY': 'test-secret'})
            worker = CatalystService([adapter], store=store)
            result = await worker.poll(now=now)
            assert result['new_events'] == 1
            events = worker.list_events(as_of=now)
            assert len(events) == 1 and at(events[0]['first_seen_at']) == now
            assert events[0]['coverage_status'] == 'DELAYED' and 'test-secret' not in json.dumps(events)
            assert store.cursor('NEWS_TEST').since is not None
            assert 'LATENCY_NOT_VERIFIED' in worker.health()[0]['reason_codes']
            assert 'since=' in str(calls[0].url)
    asyncio.run(check())


def test_failed_source_batch_does_not_advance_cursor_or_publish_partial_events(store):
    future = raw(published_at='2026-10-12T10:00:00+09:00')
    worker = service(store, [raw(), future])
    result = asyncio.run(worker.poll(now=at('2026-10-12T09:00:00+09:00')))
    assert result['new_events'] == 0 and result['errors'] == 1
    assert worker.list_events(as_of=at('2026-10-12T09:00:00+09:00'), mode='demo') == []
    assert store.cursor('DEMO_NEWS_A').since is None
    assert worker.health()[0]['status'] == 'UNAVAILABLE'


def test_tender_terms_only_come_from_explicit_official_company_evidence():
    official_url = 'https://example.test/official/disclosure-1'
    terms = {'source_url': official_url, 'offer_price': 100, 'conditions': 'Synthetic tender conditions; no guaranteed return'}
    news = raw(event_type='TENDER_OFFER', tender_terms=terms)
    observed = at('2026-10-12T08:00:00+09:00')
    assert normalize_event(news, now=observed).tender_terms is None
    official = raw(event_type='TENDER_OFFER', tender_terms=terms, source_kind='OFFICIAL_DISCLOSURE',
                   official_reference_verified=True, evidence=[{'source_url': official_url, 'claim': 'Synthetic explicit issuer reference',
                   'strength': .9, 'source_kind': 'OFFICIAL_DISCLOSURE', 'company_symbols': ['005930']}], confidence=.9)
    event = normalize_event(official, now=observed)
    assert event.relationship == 'DIRECT_VERIFIED' and event.tender_terms.offer_price == 100
    assert event.tender_terms.company_symbol == '005930'
    wrong_target = official.model_copy(update={'tender_terms': official.tender_terms.model_copy(update={'company_symbol': '000660'})})
    assert normalize_event(wrong_target, now=observed).tender_terms is None
