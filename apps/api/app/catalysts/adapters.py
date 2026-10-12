"""No built-in guessed news/DART URLs. Explicit contracts enable GET feeds."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import Field, field_validator, model_validator

from .models import Coverage, PollResult, ProviderCursor, ProviderHealth, RawCatalyst, SourceKind, StrictModel, safe_url


class CatalystSourceAdapter(Protocol):
    provider: str
    async def poll_since(self, cursor: ProviderCursor) -> PollResult: ...
    def health(self) -> ProviderHealth: ...
    async def aclose(self) -> None: ...


class FeedFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class UnavailableAdapter:
    def __init__(self, provider: str, coverage: list[str], requirements: list[str], code="SOURCE_NOT_CONFIGURED"):
        self.provider = provider
        self._health = ProviderHealth(provider=provider, coverage=coverage, reason_codes=[code], required_configuration=requirements)
    async def poll_since(self, cursor):
        return PollResult(events=[], next_cursor=cursor)
    def health(self):
        return self._health.model_copy(deep=True)
    async def aclose(self):
        return None


class FixtureAdapter:
    """Only an explicit fixture/replay uses this adapter; never an env default."""
    def __init__(self, events: list[RawCatalyst], provider="DEMO_FIXTURE"):
        self.provider = provider
        self.events = [event.model_copy(update={"provider": provider, "mode": "demo"}) for event in events]
        self._health = ProviderHealth(provider=provider, coverage=["synthetic domestic event scenarios"], status=Coverage.DEMO, mode="demo")
    async def poll_since(self, cursor):
        events = [event for event in self.events if cursor.since is None or event.published_at >= cursor.since]
        return PollResult(events=events, next_cursor=cursor)
    def health(self):
        return self._health.model_copy(deep=True)
    async def aclose(self):
        return None


class FeedContract(StrictModel):
    provider: str
    endpoint: str
    official_document_url: str
    allowed_domains: list[str] = Field(min_length=1)
    source_kind: SourceKind
    coverage: list[str] = Field(min_length=1)
    contract_verified: bool = Field(False, strict=True)
    response_schema_verified: bool = Field(False, strict=True)
    incremental_revision_contract_verified: bool = Field(False, strict=True)
    license_allowed: bool = Field(False, strict=True)
    response_schema: dict[str, Any]
    records_path: str
    field_map: dict[str, str]
    static_query: dict[str, str] = Field(default_factory=dict)
    since_query: str | None = None
    cursor_query: str | None = None
    next_cursor_path: str | None = None
    overlap_seconds: int = Field(120, ge=0, le=86400)
    expected_delay_seconds: int | None = Field(None, ge=0, le=86400)
    stale_after_seconds: int = Field(300, ge=1, le=86400)
    initial_since: ProviderCursor | None = None
    auth_env: str | None = None
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    _urls = field_validator("endpoint", "official_document_url")(safe_url)

    @model_validator(mode="after")
    def scope(self):
        parsed = urlsplit(self.endpoint)
        if parsed.query or parsed.hostname not in self.allowed_domains:
            raise ValueError("A feed endpoint must match its explicit credential destination")
        if self.auth_env and not (self.auth_env.startswith("CATALYST_") or self.auth_env in {"DART_API_KEY", "DART_KEY", "KIND_API_KEY", "NEWS_API_KEY"}):
            raise ValueError("Feed credentials must use the dedicated catalyst namespace")
        if self.auth_header.lower() in {"cookie", "host", "proxy-authorization", "referer"}:
            raise ValueError("Unsupported authentication header")
        if any(key.lower() in {"token", "access_token", "api_key", "apikey", "crtfc_key", "secret", "password"} for key in self.static_query):
            raise ValueError("Secret query values cannot be stored in a contract")
        required = {"provider_item_id", "source_url", "title", "published_at", "evidence"}
        if not required.issubset(self.field_map):
            raise ValueError("Mandatory provenance/availability mappings are missing")
        return self


def field_at(data: Any, path: str):
    for part in path.split(".") if path else []:
        data = data[int(part)] if isinstance(data, list) and part.isdigit() else data[part]
    return data


def validate_schema(value: Any, schema: dict[str, Any]):
    """Fail closed on unsupported schema vocabulary instead of ignoring it."""
    supported = {"type", "required", "properties", "items", "enum", "additionalProperties", "description", "title"}
    if set(schema) - supported:
        raise FeedFailure("UNSUPPORTED_RESPONSE_SCHEMA")
    types = {"object": dict, "array": list, "string": str, "number": (int, float), "integer": int, "boolean": bool, "null": type(None)}
    kind = schema.get("type")
    allowed = kind if isinstance(kind, list) else [kind]
    if any(item not in types for item in allowed):
        raise FeedFailure("UNSUPPORTED_RESPONSE_SCHEMA")
    if not any(isinstance(value, types[item]) and not (item in {"number", "integer"} and isinstance(value, bool)) for item in allowed):
        raise FeedFailure("RESPONSE_SCHEMA_MISMATCH")
    if isinstance(value, float) and not math.isfinite(value):
        raise FeedFailure("RESPONSE_SCHEMA_MISMATCH")
    if "enum" in schema and value not in schema["enum"]:
        raise FeedFailure("RESPONSE_SCHEMA_MISMATCH")
    if isinstance(value, dict):
        if any(key not in value for key in schema.get("required", [])):
            raise FeedFailure("RESPONSE_SCHEMA_MISMATCH")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False and not set(value).issubset(props):
            raise FeedFailure("RESPONSE_SCHEMA_MISMATCH")
        for key, child in props.items():
            if key in value:
                validate_schema(value[key], child)
    if isinstance(value, list):
        if "items" not in schema:
            raise FeedFailure("UNSUPPORTED_RESPONSE_SCHEMA")
        for child in value:
            validate_schema(child, schema["items"])


class LicensedJSONFeedAdapter:
    def __init__(self, contract: FeedContract, *, client: httpx.AsyncClient | None = None, env: Mapping[str, str] | None = None):
        self.provider, self.contract = contract.provider, contract
        self.env = os.environ if env is None else env
        self.client = client or httpx.AsyncClient(timeout=15, verify=True, follow_redirects=False)
        self._owns_client = client is None
        self._blocked = False
        self._retry_after = 0.
        self._health = ProviderHealth(provider=self.provider, coverage=contract.coverage, required_configuration=[])
    def _guard(self):
        contract = self.contract
        if not contract.license_allowed:
            raise FeedFailure("LICENSE_NOT_APPROVED")
        if not contract.contract_verified or not contract.response_schema_verified:
            raise FeedFailure("CONTRACT_NOT_VERIFIED")
        if not contract.incremental_revision_contract_verified or not (contract.since_query or contract.cursor_query):
            raise FeedFailure("INCREMENTAL_REVISION_CONTRACT_MISSING")
        if contract.auth_env and not self.env.get(contract.auth_env):
            raise FeedFailure("CREDENTIAL_BINDING_MISSING")
    async def poll_since(self, cursor):
        if self._blocked or time.monotonic() < self._retry_after:
            return PollResult(events=[], next_cursor=cursor)
        try:
            self._guard()
            contract = self.contract
            if cursor.since is None and cursor.opaque is None:
                cursor = contract.initial_since or cursor
                if cursor.since is None and cursor.opaque is None:
                    raise FeedFailure("LAST_TRADING_CLOSE_OR_INITIAL_CURSOR_REQUIRED")
            headers = {"Accept": "application/json"}
            if contract.auth_env:
                headers[contract.auth_header] = contract.auth_prefix + self.env[contract.auth_env]
            query = dict(contract.static_query)
            if cursor.since and contract.since_query:
                query[contract.since_query] = (cursor.since - timedelta(seconds=contract.overlap_seconds)).isoformat()
            if cursor.opaque and contract.cursor_query:
                query[contract.cursor_query] = cursor.opaque
            response = await self.client.get(contract.endpoint, params=query, headers=headers)
            if response.status_code in (401, 403):
                self._blocked = True
                raise FeedFailure("SOURCE_AUTHENTICATION_BLOCKED")
            if response.status_code == 429:
                try:
                    delay = min(3600., max(1., float(response.headers.get("Retry-After", "90"))))
                    if not math.isfinite(delay):
                        delay = 90.
                except ValueError:
                    delay = 90.
                self._retry_after = time.monotonic() + delay
                raise FeedFailure("SOURCE_RATE_LIMITED")
            if response.status_code != 200:
                self._retry_after = time.monotonic() + 90
                raise FeedFailure("SOURCE_REQUEST_FAILED")
            if len(response.content) > 2 * 1024 * 1024:
                raise FeedFailure("SOURCE_RESPONSE_TOO_LARGE")
            payload = response.json()
            validate_schema(payload, contract.response_schema)
            records = field_at(payload, contract.records_path)
            if not isinstance(records, list):
                raise FeedFailure("RESPONSE_SCHEMA_MISMATCH")
            # Strict RawCatalyst excludes whole article bodies and upstream
            # first_seen_at. Availability is stamped by our service, not feed.
            events = [RawCatalyst.model_validate({**{key: field_at(record, path) for key, path in contract.field_map.items()},
                       "provider": self.provider, "source_kind": contract.source_kind, "mode": "live"}) for record in records]
            now = datetime.now(timezone.utc)
            next_opaque = field_at(payload, contract.next_cursor_path) if contract.next_cursor_path else None
            next_cursor = ProviderCursor(since=now, opaque=next_opaque)
            self._health = ProviderHealth(provider=self.provider, status=Coverage.DELAYED, coverage=contract.coverage,
                                          last_success_at=now, latency_seconds=contract.expected_delay_seconds,
                                          reason_codes=["DOCUMENTED_POLLING_LATENCY" if contract.expected_delay_seconds is not None else "LATENCY_NOT_VERIFIED"])
            return PollResult(events=events, next_cursor=next_cursor)
        except FeedFailure as error:
            self.fail(error.code)
        except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError, RecursionError):
            self.fail("SOURCE_SCHEMA_OR_NETWORK_FAILURE")
        return PollResult(events=[], next_cursor=cursor)
    def fail(self, code):
        self._health.status = Coverage.STALE if self._health.last_success_at else Coverage.UNAVAILABLE
        self._health.reason_codes = [code]
    def health(self):
        result = self._health.model_copy(deep=True)
        if result.last_success_at and (datetime.now(timezone.utc) - result.last_success_at).total_seconds() > self.contract.stale_after_seconds:
            result.status = Coverage.STALE
            result.reason_codes = ["FEED_STALE"]
        return result
    async def aclose(self):
        if self._owns_client:
            await self.client.aclose()


def adapters_from_environment() -> list[CatalystSourceAdapter]:
    """Only a reviewed non-secret contract file can enable a feed."""
    unavailable = [
        UnavailableAdapter("DART", ["company disclosures"], ["accessible official API/auth contract", "verified response and publication precision", "approved credential binding if required"]),
        UnavailableAdapter("KIND", ["exchange disclosures"], ["permitted official API/feed", "terms and response contract"]),
        UnavailableAdapter("LICENSED_NEWS", ["domestic economic/policy news"], ["named contracted news provider", "license permitting metadata/summary reuse", "verified endpoint/schema and required credential binding"]),
        UnavailableAdapter("OFFICIAL_PRESS", ["government/regional announcements"], ["permitted official HTTPS feed", "verified timestamp and revision schema"]),
    ]
    path = os.environ.get("CATALYST_PROVIDER_CONFIG")
    if not path:
        return unavailable
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 256 * 1024:
            raise ValueError("Contract too large")
        definitions = json.loads(raw)
        if not isinstance(definitions, list):
            raise ValueError("A list of contracts is required")
        contracts = [FeedContract.model_validate(item) for item in definitions]
        if len({contract.provider for contract in contracts}) != len(contracts):
            raise ValueError("Provider names must be unique")
        configured = [LicensedJSONFeedAdapter(contract) for contract in contracts]
        return configured + [adapter for adapter in unavailable if adapter.provider not in {c.provider for c in contracts}]
    except (OSError, ValueError, TypeError, RecursionError):
        return unavailable + [UnavailableAdapter("CONFIGURATION", ["configuration diagnostics"], ["valid non-secret verified contract file"], "CONTRACT_CONFIGURATION_INVALID")]
