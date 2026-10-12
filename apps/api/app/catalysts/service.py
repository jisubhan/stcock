from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import re

from .adapters import CatalystSourceAdapter, FixtureAdapter, adapters_from_environment
from .models import (CatalystEvent, Coverage, EventStatus, ProviderCursor, RawCatalyst,
                     Relationship, SourceKind, TickerRelation, aware)
from .store import CatalystStore, digest


RANK = {Relationship.UNVERIFIED: 0, Relationship.THEMATIC_ASSOCIATION: 1,
        Relationship.NEWS_REPORTED: 2, Relationship.DIRECT_VERIFIED: 3}


def story_identity(raw: RawCatalyst) -> str:
    # No fuzzy headline/time matching: similarly titled distinct events survive.
    identity = ("common:" + raw.canonical_story_id
                if raw.canonical_story_id else raw.provider + ':' + raw.provider_item_id)
    return raw.mode + ':' + identity


def content_hash(raw: RawCatalyst) -> str:
    return digest(json.dumps(raw.model_dump(mode="json"), sort_keys=True, ensure_ascii=False))


def normalize_event(raw: RawCatalyst, *, now: datetime, themes: list[dict] | None = None,
                    aliases: dict[str, list[str]] | None = None, previous: CatalystEvent | None = None,
                    coverage: Coverage = Coverage.DELAYED) -> CatalystEvent:
    """Pure linker used by replay; now is the observed clock, never feed input."""
    now = aware(now)
    published_revision = raw.provider_revision_at or raw.published_at
    if max(raw.published_at, published_revision) > now:
        raise ValueError("A source event cannot be used before publication")
    themes, aliases = themes or [], aliases or {}
    mentioned = set(raw.explicit_tickers)
    aliased = {symbol for symbol, names in aliases.items() if any(
        re.search(r"(?<![A-Za-z0-9가-힣])" + re.escape(name) + r"(?![A-Za-z0-9가-힣])", raw.title)
        for name in names if name)}
    official = raw.source_kind in {SourceKind.OFFICIAL_DISCLOSURE, SourceKind.OFFICIAL_PRESS}
    relations = []
    for symbol in sorted(mentioned | aliased):
        indices = [i for i, evidence in enumerate(raw.evidence) if symbol in evidence.company_symbols
                   and evidence.source_kind in {SourceKind.OFFICIAL_DISCLOSURE, SourceKind.OFFICIAL_PRESS}]
        if official and raw.official_reference_verified and symbol in mentioned and indices:
            relation, confidence = Relationship.DIRECT_VERIFIED, raw.confidence
        elif raw.source_kind == SourceKind.LICENSED_NEWS and symbol in mentioned:
            relation, confidence = Relationship.NEWS_REPORTED, min(raw.confidence, .8)
        else:
            relation, confidence = Relationship.THEMATIC_ASSOCIATION, min(raw.confidence, .35)
        relations.append(TickerRelation(symbol=symbol, relationship=relation, confidence=confidence, evidence_indices=indices))
    theme_ids = set(raw.reported_theme_ids)
    for theme in themes:
        if set(theme.get('memberSymbols', [])) & (mentioned | aliased):
            theme_ids.add(theme['id'])
        elif any(name and name in raw.title for name in [theme.get('name', ''), *theme.get('aliases', [])]):
            theme_ids.add(theme['id'])
    relationship = max((relation.relationship for relation in relations), key=RANK.get, default=
                       Relationship.THEMATIC_ASSOCIATION if theme_ids else Relationship.UNVERIFIED)
    confidence = min(raw.confidence, {Relationship.UNVERIFIED: .15, Relationship.THEMATIC_ASSOCIATION: .35,
                                     Relationship.NEWS_REPORTED: .8, Relationship.DIRECT_VERIFIED: 1}[relationship])
    codes = []
    terms = raw.tender_terms
    direct_symbols = {relation.symbol for relation in relations if relation.relationship == Relationship.DIRECT_VERIFIED}
    tender_company = (terms.company_symbol if terms else None) or (next(iter(direct_symbols)) if len(direct_symbols) == 1 else None)
    if terms and not (relationship == Relationship.DIRECT_VERIFIED and official and
                      tender_company in direct_symbols and
                      any(e.source_url == terms.source_url and tender_company in e.company_symbols
                          and e.source_kind in {SourceKind.OFFICIAL_DISCLOSURE, SourceKind.OFFICIAL_PRESS} for e in raw.evidence)):
        terms = None
        codes.append('TENDER_TERMS_UNVERIFIED')
    elif terms:
        terms = terms.model_copy(update={'company_symbol': tender_company})
    if relationship in {Relationship.THEMATIC_ASSOCIATION, Relationship.UNVERIFIED}:
        codes.append('WATCH_ONLY_RELATIONSHIP')
    if raw.event_type.value == 'POLICY' and raw.certainty in {'PLAN', 'DISCUSSION', 'ANNOUNCEMENT', 'UNKNOWN'}:
        codes.append('POLICY_NOT_A_CONFIRMED_CONTRACT')
    status = raw.status
    if raw.source_kind == SourceKind.UNVERIFIED and status == EventStatus.VERIFIED:
        status = EventStatus.UNCONFIRMED
    identity = story_identity(raw)
    providers, evidence = [raw.provider], list(raw.evidence)
    provider, item_id, source_url, title, source_kind = raw.provider, raw.provider_item_id, raw.source_url, raw.title, raw.source_kind
    tickers, materiality = sorted(mentioned | aliased), raw.materiality_score
    event_id = 'catalyst-' + digest(identity)[:24]
    first_seen, published = now, raw.published_at
    if previous:
        event_id, first_seen = previous.event_id, previous.first_seen_at
        published = previous.published_at
        providers = sorted(set(previous.providers + [raw.provider]))
        # Cross-provider repetition adds provenance without allowing a weaker
        # newspaper copy to erase verified official facts or tender evidence.
        protected_official = previous.provider != raw.provider and RANK[previous.relationship] > RANK[relationship]
        if protected_official:
            relationship, confidence, relations = previous.relationship, previous.confidence, previous.ticker_relations
            terms, status = previous.tender_terms, previous.status
            provider, item_id, source_url, title, source_kind = previous.provider, previous.provider_item_id, previous.source_url, previous.title, previous.source_kind
            tickers, materiality = previous.tickers, previous.materiality_score
            theme_ids.update(previous.theme_ids)
        if protected_official or (previous.provider != raw.provider and status not in {EventStatus.CORRECTED, EventStatus.RETRACTED}):
            previous_evidence = {json.dumps(e.model_dump(mode='json'), sort_keys=True): e for e in previous.evidence}
            previous_evidence.update({json.dumps(e.model_dump(mode='json'), sort_keys=True): e for e in raw.evidence})
            evidence = list(previous_evidence.values())
        # A same-source revision/retraction supersedes its old claims. Those
        # claims remain in prior revisions rather than current evidence.
    for relation in relations:
        relation.evidence_indices = [i for i, proof in enumerate(evidence)
                                     if relation.symbol in proof.company_symbols and proof.source_kind in {SourceKind.OFFICIAL_DISCLOSURE, SourceKind.OFFICIAL_PRESS}]
    version = 'revision-' + digest(raw.provider + ':' + raw.provider_item_id + ':' + content_hash(raw))[:32]
    return CatalystEvent(event_id=event_id, provider=provider, providers=providers, provider_item_id=item_id,
                         source_url=source_url, title=title, published_at=published, first_seen_at=first_seen,
                         ingested_at=now, updated_at=now, revision_published_at=published_revision,
                         revision_available_at=max(now, published_revision), revision_id=version,
                         event_type=raw.event_type, tickers=tickers, theme_ids=sorted(theme_ids),
                         relationship=relationship, ticker_relations=relations, evidence=evidence,
                         novelty_score=0 if previous else 1, materiality_score=materiality,
                         confidence=confidence, status=status, duplicate_group_id=event_id,
                         coverage_status=Coverage.DEMO if raw.mode == 'demo' else coverage,
                         source_kind=source_kind, mode=raw.mode, certainty=raw.certainty,
                         tender_terms=terms, explanation_codes=codes)


class CatalystService:
    def __init__(self, adapters: list[CatalystSourceAdapter] | None = None, *, store: CatalystStore | None = None,
                 themes: list[dict] | None = None, aliases: dict[str, list[str]] | None = None):
        self.adapters = adapters_from_environment() if adapters is None else adapters
        self.store = store or CatalystStore()
        self.themes, self.aliases = themes or [], aliases or {}
        self._lock = asyncio.Lock()
        self._health_overrides = {}

    async def poll(self, *, now: datetime | None = None, last_trading_close: datetime | None = None) -> dict:
        simulated_clock = now is not None
        now = aware(now or datetime.now(timezone.utc))
        totals = {'new_events': 0, 'duplicates': 0, 'revisions': 0, 'errors': 0}
        if self._lock.locked():
            return {**totals, 'providers': self.health(), 'poll_in_progress': True}
        async with self._lock:
            for adapter in self.adapters:
                batch_counts = {'new_events': 0, 'duplicates': 0, 'revisions': 0}
                cursor = self.store.cursor(adapter.provider)
                if cursor.since is None and last_trading_close:
                    cursor = ProviderCursor(since=aware(last_trading_close), opaque=cursor.opaque)
                try:
                    result = await adapter.poll_since(cursor)
                    observed_at = now if simulated_clock else datetime.now(timezone.utc)
                    health = adapter.health()
                    rows, staged, batch_ids = [], {}, set()
                    for raw in result.events:
                        if raw.provider != adapter.provider or (raw.mode == 'live' and health.status not in {Coverage.LIVE, Coverage.DELAYED}):
                            raise ValueError('An unavailable feed cannot publish live events')
                        content = content_hash(raw)
                        fingerprint = (raw.provider, raw.provider_item_id, content)
                        if fingerprint in batch_ids or self.store.seen_content(*fingerprint):
                            batch_counts['duplicates'] += 1
                            continue
                        batch_ids.add(fingerprint)
                        story = story_identity(raw)
                        previous = staged.get(story) or self.store.current(story, raw.provider, raw.provider_item_id, raw.revises_provider_item_id)
                        event = normalize_event(raw, now=observed_at, themes=self.themes, aliases=self.aliases,
                                                previous=previous, coverage=health.status)
                        if previous:
                            batch_counts['duplicates' if previous.provider != raw.provider else 'revisions'] += 1
                        else:
                            batch_counts['new_events'] += 1
                        staged[story] = event
                        rows.append((event, story, raw.provider, content, raw.provider_item_id))
                    if health.status in {Coverage.LIVE, Coverage.DELAYED, Coverage.DEMO}:
                        self.store.save_batch(rows, adapter.provider, result.next_cursor, observed_at)
                        for key, count in batch_counts.items():
                            totals[key] += count
                    self._health_overrides[adapter.provider] = health
                    self.store.record_health(health, observed_at)
                except Exception:
                    totals['errors'] += 1
                    health = adapter.health()
                    health.status = Coverage.STALE if health.last_success_at else Coverage.UNAVAILABLE
                    health.reason_codes = ['SOURCE_BATCH_INVALID']
                    self._health_overrides[adapter.provider] = health
                    self.store.record_health(health, now)
        return {**totals, 'providers': self.health(), 'poll_in_progress': False}

    def list_events(self, *, since=None, as_of=None, verified=None, last_trading_close=None, mode='live') -> list[dict]:
        since = since if since is not None else last_trading_close
        events = self.store.events(since=since, as_of=as_of, verified=verified, mode=mode)
        health = {item['provider']: item for item in self.health()}
        output = []
        for event in events:
            data = event.model_dump(mode='json')
            # Historical views retain contemporaneous coverage; current views
            # expose a provider outage while preserving known event provenance.
            if as_of is None and mode == 'live' and event.provider in health:
                if health[event.provider]['status'] in {Coverage.STALE.value, Coverage.UNAVAILABLE.value}:
                    data['coverage_status'] = health[event.provider]['status']
            output.append(data)
        return output

    def revisions(self, event_id, *, as_of=None):
        return [event.model_dump(mode='json') for event in self.store.revisions(event_id, as_of)]

    def health(self, now=None):
        output = []
        for adapter in self.adapters:
            current = adapter.health()
            previous = self._health_overrides.get(adapter.provider)
            if previous and previous.status in {Coverage.STALE, Coverage.UNAVAILABLE}:
                current = previous
            output.append(current.model_dump(mode='json'))
        return output

    async def aclose(self):
        for adapter in self.adapters:
            await adapter.aclose()


def build_service(*, themes=None, aliases=None, store=None) -> CatalystService:
    return CatalystService(themes=themes, aliases=aliases, store=store)
