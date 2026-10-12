"""Typed, provenance-carrying inputs and outputs for the v1.2 radar.

These models do not fetch data. A turnover observation never implies an investor
net purchase. DEMO and replay observations remain explicitly labelled.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

KST = ZoneInfo("Asia/Seoul")
Mode = Literal["live", "demo", "replay"]
Venue = Literal["KRX", "NXT", "CONSOLIDATED"]
Quality = Literal["LIVE", "DELAYED", "STALE", "PARTIAL", "UNAVAILABLE", "DEMO", "REPLAY"]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Finite = Annotated[float, Field(allow_inf_nan=False)]
Ratio = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Radar timestamps must be timezone aware")
    return value.astimezone(KST)


class RadarModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class FlowObservation(RadarModel):
    symbol: str = Field(min_length=1)
    observed_at: datetime
    first_seen_at: datetime
    interval_start: datetime
    interval_end: datetime
    session_id: str = Field(min_length=1)
    venue: Venue
    turnover_krw: Nonnegative
    price_open: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None
    price_close: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None
    quote_at: datetime
    provider: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    mode: Mode = "live"
    bar_status: Literal["COMPLETE", "PARTIAL"] = "COMPLETE"
    observation_id: str | None = None

    _timestamps = field_validator("observed_at", "first_seen_at", "interval_start", "interval_end", "quote_at")(aware)

    @model_validator(mode="after")
    def interval_is_valid(self):
        if self.interval_end <= self.interval_start:
            raise ValueError("Flow intervals use positive [start, end) ranges")
        if self.bar_status == "COMPLETE" and self.observed_at < self.interval_end:
            raise ValueError("A completed interval cannot be observed before its end")
        return self


class HistoricalFlowObservation(RadarModel):
    symbol: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    session_date: date
    minute_from_session: Nonnegative
    window_minutes: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    turnover_krw: Nonnegative
    observed_at: datetime
    first_seen_at: datetime
    venue: Venue
    provider: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    mode: Mode = "live"

    _timestamps = field_validator("observed_at", "first_seen_at")(aware)


class ThemeRelation(RadarModel):
    theme_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    weight: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)] = 1
    relationship: Literal["DIRECT_VERIFIED", "NEWS_REPORTED", "THEMATIC_ASSOCIATION", "UNVERIFIED"] = "THEMATIC_ASSOCIATION"
    effective_from: datetime
    first_seen_at: datetime
    source_url: str = Field(min_length=1)
    theme_name: str | None = None
    provider: str = "configured_relation"

    _timestamps = field_validator("effective_from", "first_seen_at")(aware)


class InvestorNetBuyObservation(RadarModel):
    symbol: str = Field(min_length=1)
    observed_at: datetime
    first_seen_at: datetime
    venue: Venue
    unit: Literal["SHARES", "KRW"]
    values: dict[str, Finite | None]
    provider: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    quality: Literal["LIVE", "DELAYED", "STALE"] = "DELAYED"
    mode: Mode = "live"

    _timestamps = field_validator("observed_at", "first_seen_at")(aware)


class Coverage(RadarModel):
    symbols_seen: int = Field(ge=0)
    symbols_expected: int = Field(ge=0)
    ratio: Ratio
    venues: list[Venue] = Field(default_factory=list)
    covered_universe_symbols: int = Field(ge=0)
    expected_universe_symbols: int = Field(ge=0)
    universe_ratio: Ratio
    universe_turnover_krw: Nonnegative
    denominator_definition: str = "Observed symbols in this labelled window and configured covered universe; quote and turnover freshness gate current eligibility; not the entire Korean market"


class ThemeFlowSnapshot(RadarModel):
    theme_id: str
    theme_name: str
    as_of: datetime
    session_id: str
    window_minutes: Nonnegative
    mode: Mode
    provider: list[str]
    source_urls: list[str]
    coverage: Coverage
    turnover_krw: Nonnegative
    raw_turnover_krw: Nonnegative
    turnover_velocity_krw_per_min: Nonnegative
    raw_turnover_velocity_krw_per_min: Nonnegative
    relative_turnover: Nonnegative | None = None
    history_sessions: int = Field(0, ge=0)
    breadth_up_ratio: Ratio | None = None
    breadth_high_rvol_ratio: Ratio | None = None
    concentration_top1_ratio: Ratio | None = None
    price_momentum_5m: Finite | None = None
    covered_universe_share: Ratio | None = None
    strength_type: Literal["single-name strength", "broad-based strength", "mixed", "unavailable"]
    symbol_metrics: list[dict[str, Any]] = Field(default_factory=list)
    catalyst_event_ids: list[str] = Field(default_factory=list)
    confirmed_investor_netbuy: dict[str, Any] | None = None
    confidence: Ratio
    quality: Quality
    data_freshness: dict[str, Any]
    measurement_definition: str = "Observed interval turnover (KRW), not investor net buying; normalized multi-theme attribution"
    reason_codes: list[str] = Field(default_factory=list)
    eligible_for_live_ranking: bool = False

    _timestamp = field_validator("as_of")(aware)


class RankItem(RadarModel):
    theme_id: str
    theme_name: str
    rank: int = Field(ge=1)
    score: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    state: str
    mode: Mode
    as_of: datetime
    reason_codes: list[str] = Field(default_factory=list)
    explanation: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    source_urls: list[str] = Field(default_factory=list)
    catalyst_event_ids: list[str] = Field(default_factory=list)

    _timestamp = field_validator("as_of")(aware)


class RotationTransition(RadarModel):
    transition_id: str
    theme_id: str
    as_of: datetime
    previous_state: str
    state: str
    previous_rank: int | None = None
    rank: int | None = None
    previous_score: Finite | None = None
    score: Finite | None = None
    explanation: str
    reason_codes: list[str]
    metrics: dict[str, Any]
    source_urls: list[str]
    mode: Mode

    _timestamp = field_validator("as_of")(aware)


class RadarNotification(RadarModel):
    fingerprint: str
    theme_id: str
    notification_type: str
    as_of: datetime
    explanation: str
    source_urls: list[str]
    mode: Mode

    _timestamp = field_validator("as_of")(aware)


class RotationResult(RadarModel):
    as_of: datetime
    mode: Mode
    quality: Quality
    premarket_hypothesis_top3: list[RankItem]
    live_opening_flow_top3: list[RankItem]
    entry_ready_top3: list[dict[str, Any]]
    ranked_themes: list[RankItem]
    transitions: list[RotationTransition]
    notifications: list[RadarNotification]
    reason_codes: list[str]
    data_freshness: dict[str, Any]
    measurement_definition: str = "Premarket hypotheses, observed flow strength and risk-qualified entries are separate lists"

    _timestamp = field_validator("as_of")(aware)
