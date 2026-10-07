#!/usr/bin/env python3
"""Conservative Toss API diagnostics: no guessed endpoints or order requests."""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


# Add entries ONLY after confirming the current official documentation, exact
# read-only GET path and response schema. Never load endpoints from user input.
VERIFIED_READ_ONLY_ENDPOINTS: dict[str, dict[str, str]] = {}
DOC_CANDIDATES = (
    "https://developers.tossinvest.com/docs",
    "https://developers.tossinvest.com/llms.txt",
    "https://openapi.tossinvest.com/openapi-docs/overview.md",
    "https://openapi.tossinvest.com/openapi-docs/latest/openapi.json",
    "https://openapi.tossinvest.com/openapi-docs/latest/asyncapi.json",
    "https://openapi.tossinvest.com/openapi-docs/faq.md",
)
AUTH_VARIABLES = ("TOSS_ACCESS_TOKEN", "TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET")
VISIBLE_HEADERS = frozenset((
    "content-type", "date", "x-ratelimit-limit", "x-ratelimit-remaining",
    "x-ratelimit-reset", "retry-after",
))
SENSITIVE_KEY = re.compile(r"token|secret|password|authorization|api.?key|credential", re.I)
MAX_BYTES = 2 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


def shape(value: Any, depth: int = 0) -> dict[str, Any]:
    kind = json_type(value)
    result: dict[str, Any] = {"type": kind}
    if depth >= 8:
        return result
    if isinstance(value, dict):
        result["properties"] = {
            key: {"redacted": True} if SENSITIVE_KEY.search(key) else shape(item, depth + 1)
            for key, item in list(value.items())[:60]
        }
        if len(value) > 60:
            result["truncated"] = True
    elif isinstance(value, list):
        unique: list[dict[str, Any]] = []
        for item in value[:10]:
            item_shape = shape(item, depth + 1)
            if item_shape not in unique:
                unique.append(item_shape)
        result["observedItemShapes"] = unique
    return result


def validate(value: Any, schema: Any, path: str = "$", depth: int = 0) -> list[str]:
    """Validate basic documented structure; fail on unsupported constraints."""
    if depth > 64:
        return [f"{path}: schema nesting limit exceeded"]
    if not isinstance(schema, dict):
        return [f"{path}: expected a schema object"]
    supported = {"type", "properties", "required", "items", "additionalProperties",
                 "enum", "description", "title", "$schema", "$id"}
    if set(schema) - supported:
        return [f"{path}: schema has unsupported constraints; use a full JSON Schema validator"]
    errors: list[str] = []
    allowed = schema.get("type")
    if allowed:
        allowed = [allowed] if isinstance(allowed, str) else allowed
        actual = json_type(value)
        if actual not in allowed and not (actual == "integer" and "number" in allowed):
            return [f"{path}: type mismatch"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: enum mismatch")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: missing required field {name}")
        for name, item in value.items():
            if name in properties:
                errors += validate(item, properties[name], f"{path}.{name}", depth + 1)
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected field")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors += validate(item, schema["additionalProperties"], f"{path}.*", depth + 1)
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors += validate(item, schema["items"], f"{path}[{index}]", depth + 1)
    return errors[:100]


def load_json(path: str) -> Any:
    raw = Path(path).read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError("JSON file exceeds the 2 MiB limit")
    return json.loads(raw)


def request(url: str, token_env: str | None = None) -> dict[str, Any]:
    # Both docs and API URLs originate in static code, never command-line URLs.
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("Only verified HTTPS URLs without embedded credentials are allowed")
    if token_env and parsed.hostname != "openapi.tossinvest.com":
        raise ValueError("Authenticated probes are restricted to the verified API host")
    headers = {"User-Agent": "FlowTradeRadar-verification/1.0", "Accept": "application/json,text/html"}
    if token_env:
        token = os.environ.get(token_env)
        if not token:
            return {"url": url, "status": "missing_credential", "variable": token_env}
        headers["Authorization"] = "Bearer " + token
    opener = urllib.request.build_opener(
        NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context())
    )
    try:
        with opener.open(urllib.request.Request(url, headers=headers, method="GET"), timeout=12) as response:
            result: dict[str, Any] = {
                "url": url, "http_status": response.status,
                "headers": {k: v for k, v in response.headers.items() if k.lower() in VISIBLE_HEADERS},
            }
            if "application/json" in response.headers.get("Content-Type", ""):
                body = response.read(MAX_BYTES + 1)
                if len(body) <= MAX_BYTES:
                    try:
                        result["observed_schema"] = shape(json.loads(body))
                    except (ValueError, RecursionError):
                        result["schema_status"] = "invalid_or_excessively_nested_json"
                else:
                    result["schema_status"] = "response_too_large"
            return result
    except urllib.error.HTTPError as error:
        # Do not print response bodies, Location, Set-Cookie or authorization.
        return {"url": url, "http_status": error.code,
                "headers": {k: v for k, v in error.headers.items() if k.lower() in VISIBLE_HEADERS}}
    except urllib.error.URLError as error:
        # Never stringify arbitrary remote exception messages or credentials.
        reason = str(error.reason)
        label = "proxy_connect_403" if "Tunnel connection failed: 403" in reason else "network_or_tls_error"
        return {"url": url, "status": label, "origin_http_status": None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-docs", action="store_true", help="GET linked official public documentation without authentication")
    parser.add_argument("--probe", metavar="NAME", help="probe a documented registry entry; currently none are verified")
    parser.add_argument("--sample", metavar="JSON_FILE", help="describe a local JSON response without displaying values")
    parser.add_argument("--schema", metavar="JSON_FILE", help="validate a sample against a separately verified basic JSON Schema")
    args = parser.parse_args()
    if args.schema and not args.sample:
        parser.error("--schema requires --sample")
    if args.probe and args.probe not in VERIFIED_READ_ONLY_ENDPOINTS:
        emit({"status": "unverified_endpoint_refused", "verified_endpoints": sorted(VERIFIED_READ_ONLY_ENDPOINTS)})
        return 2
    emit({"status": "public_diagnostics_only", "tls_verification": True,
          "verified_read_only_endpoints": sorted(VERIFIED_READ_ONLY_ENDPOINTS),
          "credential_presence": {name: bool(os.environ.get(name)) for name in AUTH_VARIABLES},
          "actual_response_verification": "not_checked_by_this_script",
          "connection_diagnostics": "apps/api/scripts/verify_toss.py",
          "documentation_record": "docs/toss-api-verification.md",
          "orders_supported": False})
    if args.check_docs:
        for url in DOC_CANDIDATES:
            emit(request(url))
    if args.sample:
        try:
            sample = load_json(args.sample)
            emit({"sample_schema": shape(sample), "values_omitted": True})
            if args.schema:
                errors = validate(sample, load_json(args.schema))
                emit({"schema_validation": "failed" if errors else "passed", "errors": errors})
                if errors:
                    return 1
        except (OSError, ValueError, TypeError, RecursionError):
            emit({"status": "invalid_or_unreadable_local_json"})
            return 1
    if args.probe:
        endpoint = VERIFIED_READ_ONLY_ENDPOINTS[args.probe]
        # Enforce the guard even if a future registry entry is accidentally wrong.
        dangerous_path = re.search(r"/(?:orders?|oauth2?|token|cancel|execute)(?:/|$)", urllib.parse.urlsplit(endpoint["url"]).path, re.I)
        if not endpoint.get("official_document_url") or dangerous_path:
            emit({"status": "unsafe_registry_entry_refused"})
            return 2
        emit(request(endpoint["url"], endpoint.get("token_env")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
