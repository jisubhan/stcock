"""Small, explicit domestic-event contracts. A linked theme is not a beneficiary."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import math
from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceKind(StrEnum):
    OFFICIAL_DISCLOSURE = "OFFICIAL_DISCLOSURE"
    OFFICIAL_PRESS = "OFFICIAL_PRESS"
    LICENSED_NEWS = "LICENSED_NEWS"
    UNVERIFIED = "UNVERIFIED"


class Relationship(StrEnum):
    DIRECT_VERIFIED = "DIRECT_VERIFIED"
    NEWS_REPORTED = "NEWS_REPORTED"
    THEMATIC_ASSOCIATION = "THEMATIC_ASSOCIATION"
    UNVERIFIED = "UNVERIFIED"


class Coverage(StrEnum):
    LIVE = "LIVE"
    DELAYED = "DELAYED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    DEMO = "DEMO"


class EventType(StrEnum):
    TENDER_OFFER = "TENDER_OFFER"
    EARNINGS = "EARNINGS"
    LARGE_CONTRACT = "LARGE_CONTRACT"
    MNA = "MNA"
    POLICY = "POLICY"
    CAPEX = "CAPEX"
    SECTOR_DEMAND = "SECTOR_DEMAND"
    OTHER = "OTHER"


class EventStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNCONFIRMED = "UNCONFIRMED"
    CORRECTED = "CORRECTED"
    RETRACTED = "RETRACTED"


Score = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False, strict=True)]
Symbol = Annotated[str, Field(pattern=r"^[A-Z0-9]{6}$")]


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Explicit timezone is required")
    return value.astimezone(timezone.utc)


def safe_url(value: str) -> str:
    url = urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError("Evidence requires a public HTTPS URL without credentials")
    if any(key.lower() in {"token", "access_token", "api_key", "apikey", "crtfc_key", "secret", "password"}
           for key, _ in parse_qsl(url.query)):
        raise ValueError("Credentials cannot be embedded in evidence URLs")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Evidence(StrictModel):
    source_url: Annotated[str, Field(min_length=8, max_length=2048)]
    claim: Annotated[str, Field(min_length=1, max_length=1000)]
    strength: Score = 0
    source_kind: SourceKind = SourceKind.UNVERIFIED
    company_symbols: list[Symbol] = Field(default_factory=list)
    _url = field_validator("source_url")(safe_url)


class TenderTerms(StrictModel):
    source_url: str
    company_symbol: Symbol | None = None
    offer_price: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    reference_price: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    conditions: Annotated[str, Field(max_length=500)] = ""
    stake_percent: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None
    _url = field_validator("source_url")(safe_url)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def dated(cls, value):
        return aware(value) if value is not None else value

    @model_validator(mode="after")
    def ordered(self):
        if self.starts_at and self.ends_at and self.starts_at > self.ends_at:
            raise ValueError("Tender end precedes start")
        return self


class RawCatalyst(StrictModel):
    provider: Annotated[str, Field(min_length=1, max_length=80)]
    provider_item_id: Annotated[str, Field(min_length=1, max_length=200)]
    source_url: str
    title: Annotated[str, Field(min_length=1, max_length=500)]
    published_at: datetime
    provider_revision_at: datetime | None = None
    event_type: EventType = EventType.OTHER
    status: EventStatus = EventStatus.UNCONFIRMED
    source_kind: SourceKind = SourceKind.UNVERIFIED
    # An exact source-supplied common fact identity, never a headline similarity.
    canonical_story_id: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    revises_provider_item_id: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    explicit_tickers: list[Symbol] = Field(default_factory=list)
    reported_theme_ids: list[str] = Field(default_factory=list)
    official_reference_verified: Annotated[bool, Field(strict=True)] = False
    evidence: list[Evidence] = Field(min_length=1, max_length=20)
    confidence: Score = 0
    materiality_score: Score = 0
    tender_terms: TenderTerms | None = None
    certainty: Literal["ANNOUNCEMENT", "PLAN", "DISCUSSION", "APPROVED", "CONTRACT_CONFIRMED", "UNKNOWN"] = "UNKNOWN"
    mode: Literal["live", "demo"] = "live"
    _url = field_validator("source_url")(safe_url)
    _published = field_validator("published_at")(aware)

    @field_validator("provider_revision_at")
    @classmethod
    def revised(cls, value):
        return aware(value) if value is not None else value


class TickerRelation(StrictModel):
    symbol: Symbol
    relationship: Relationship
    confidence: Score
    evidence_indices: list[int] = Field(default_factory=list)


class CatalystEvent(StrictModel):
    event_id: str
    provider: str
    providers: list[str]
    provider_item_id: str
    source_url: str
    title: Annotated[str, Field(min_length=1, max_length=500)]
    published_at: datetime
    first_seen_at: datetime
    ingested_at: datetime
    updated_at: datetime
    revision_published_at: datetime
    revision_available_at: datetime
    revision_id: str
    event_type: EventType
    tickers: list[Symbol]
    theme_ids: list[str]
    relationship: Relationship
    ticker_relations: list[TickerRelation]
    evidence: list[Evidence]
    novelty_score: Score
    materiality_score: Score
    confidence: Score
    status: EventStatus
    duplicate_group_id: str
    coverage_status: Coverage
    source_kind: SourceKind
    mode: Literal["live", "demo"]
    certainty: str = "UNKNOWN"
    tender_terms: TenderTerms | None = None
    explanation_codes: list[str] = Field(default_factory=list)
    _url = field_validator("source_url")(safe_url)
    _times = field_validator("published_at", "first_seen_at", "ingested_at", "updated_at", "revision_published_at", "revision_available_at")(aware)

    @model_validator(mode="after")
    def availability(self):
        if self.ingested_at < self.first_seen_at or self.updated_at < self.ingested_at:
            raise ValueError("Invalid observation chronology")
        if self.revision_available_at < max(self.first_seen_at, self.ingested_at, self.revision_published_at, self.published_at):
            raise ValueError("A revision cannot be available before it was observed/published")
        if self.mode == "demo" and self.coverage_status != Coverage.DEMO:
            raise ValueError("Fixture events must carry DEMO coverage")
        if self.tender_terms and self.relationship != Relationship.DIRECT_VERIFIED:
            raise ValueError("Structured tender terms require verified official direct evidence")
        return self


class ProviderCursor(StrictModel):
    since: datetime | None = None
    opaque: Annotated[str, Field(max_length=1000)] | None = None
    @field_validator("since")
    @classmethod
    def since_aware(cls, value):
        return aware(value) if value is not None else value


class ProviderHealth(StrictModel):
    provider: str
    status: Coverage = Coverage.UNAVAILABLE
    coverage: list[str]
    last_success_at: datetime | None = None
    latency_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    reason_codes: list[str] = Field(default_factory=list)
    required_configuration: list[str] = Field(default_factory=list)
    mode: Literal["live", "demo"] = "live"
    @field_validator("last_success_at")
    @classmethod
    def success_aware(cls, value):
        return aware(value) if value is not None else value


class PollResult(StrictModel):
    events: list[RawCatalyst]
    next_cursor: ProviderCursor
