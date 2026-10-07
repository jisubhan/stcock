"""A verified manifest supplies actual broker paths and payload semantics.

The attached product brief is deliberately not an API schema source. No
endpoint names, OAuth fields, or exchange semantics are guessed here.
"""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResponseSchema(ManifestModel):
    type: Literal["object", "array", "string", "number", "integer", "boolean", "null"]
    required: list[str] = Field(default_factory=list)
    properties: dict[str, ResponseSchema] = Field(default_factory=dict)
    items: ResponseSchema | None = None
    enum: list[Any] | None = None
    additional_properties: bool = True

    @model_validator(mode="after")
    def schema_is_complete(self) -> ResponseSchema:
        if self.type == "array" and self.items is None:
            raise ValueError("Array schemas require an item schema")
        if self.type == "object" and not set(self.required).issubset(self.properties):
            raise ValueError("Required fields must have property schemas")
        return self


class Endpoint(ManifestModel):
    method: Literal["GET"] = "GET"
    path: str
    query: dict[str, str] = Field(default_factory=dict)
    data_path: str
    response_schema: ResponseSchema
    field_map: dict[str, str] = Field(default_factory=dict)
    rate_group: str = Field(min_length=1)
    rate_reset_format: Literal["epoch_seconds", "seconds"]

    @field_validator("path")
    @classmethod
    def relative_path_only(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//") or any(x in value for x in ("..", "?", "#")):
            raise ValueError("Endpoint paths must be relative to the verified HTTPS origin")
        return value


class OAuth(ManifestModel):
    path: str
    encoding: Literal["form", "json"]
    payload: dict[str, str]
    refresh_payload: dict[str, str] = Field(default_factory=dict)
    access_token_path: str = Field(min_length=1)
    expires_in_path: str = Field(min_length=1)
    refresh_token_path: str | None = None
    auth_header: str = Field(min_length=1)
    auth_prefix: str
    response_schema: ResponseSchema

    _relative_path_only = field_validator("path")(Endpoint.relative_path_only.__func__)


class VenueVerification(ManifestModel):
    verified: bool = False
    price_basis: Literal["integrated", "per_venue", "krx_only", "unknown"] = "unknown"
    daily_candle_basis: Literal["integrated", "per_venue", "krx_only", "unknown"] = "unknown"
    includes_nxt: bool = False
    # Each item is an evidence reference (doc section, redacted fixture path,
    # or validation report). Secret-bearing raw responses must not be saved.
    evidence: dict[str, str] = Field(default_factory=dict)


class ApiManifest(ManifestModel):
    verified: bool = False
    document_url: HttpUrl
    verified_at: datetime
    base_url: HttpUrl
    venue: VenueVerification
    oauth: OAuth
    endpoints: dict[str, Endpoint]

    @field_validator("base_url", "document_url")
    @classmethod
    def secure_url(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https" or value.username or value.password or value.query or value.fragment:
            raise ValueError("Verified API and document URLs must use HTTPS without credentials")
        return value

    @field_validator("verified_at")
    @classmethod
    def dated_evidence(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Verification dates must contain a timezone")
        return value


def validate_response(value: Any, schema: ResponseSchema, location: str = "response") -> None:
    """Validate the explicitly supported schema vocabulary without coercion."""
    expected = {
        "object": dict, "array": list, "string": str, "number": (int, float),
        "integer": int, "boolean": bool, "null": type(None),
    }[schema.type]
    numeric_bool = schema.type in ("number", "integer") and isinstance(value, bool)
    if not isinstance(value, expected) or numeric_bool:
        raise ValueError(f"Unexpected payload type at {location}")
    if schema.type in ("number", "integer") and not math.isfinite(value):
        raise ValueError(f"Nonfinite payload number at {location}")
    if schema.enum is not None and value not in schema.enum:
        raise ValueError(f"Unexpected enum value at {location}")
    if schema.type == "object":
        if any(name not in value for name in schema.required):
            raise ValueError(f"Required payload field missing at {location}")
        if not schema.additional_properties and not set(value).issubset(schema.properties):
            raise ValueError(f"Unrecognized payload field at {location}")
        for name, child in schema.properties.items():
            if name in value:
                validate_response(value[name], child, f"{location}.{name}")
    elif schema.type == "array":
        assert schema.items is not None
        for index, item in enumerate(value):
            validate_response(item, schema.items, f"{location}[{index}]")


def field_at(value: Any, path: str) -> Any:
    if not path:
        return value
    for part in path.split("."):
        if isinstance(value, list) and part.isdigit():
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value[part]
        else:
            raise ValueError("Verified field mapping does not match response")
    return value
