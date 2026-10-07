from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import Mapping

import httpx

from ..base import BrokerUnavailable
from .schemas import ApiManifest, field_at, validate_response


class TokenManager:
    """OAuth renewal with field names supplied by a verified manifest."""

    def __init__(self, manifest: ApiManifest, env: Mapping[str, str], client: httpx.AsyncClient) -> None:
        self.manifest = manifest
        self.env = env
        self.client = client
        self._access_token = env.get("TOSS_ACCESS_TOKEN", "")
        self._refresh_token = ""
        self._expires_at = 0.0 if not self._access_token else float("inf")
        self._lock = asyncio.Lock()

    def _payload(self, template: dict[str, str]) -> dict[str, str]:
        def substitute(match: re.Match[str]) -> str:
            key = match.group(1)
            if key == "REFRESH_TOKEN":
                value = self._refresh_token
            elif key in ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET"):
                value = self.env.get(key, "")
            else:
                raise BrokerUnavailable("OAuth 매핑에 허용되지 않은 환경변수가 있습니다.")
            if not value:
                raise BrokerUnavailable("서버의 Toss OAuth 인증 설정이 누락되었습니다.")
            return value
        return {name: re.sub(r"\$\{([A-Z_]+)\}", substitute, value) for name, value in template.items()}

    async def header(self, force_refresh: bool = False) -> dict[str, str]:
        async with self._lock:
            if force_refresh or time.monotonic() >= self._expires_at - 30:
                await self._renew()
            oauth = self.manifest.oauth
            return {oauth.auth_header: f"{oauth.auth_prefix}{self._access_token}"}

    async def _renew(self) -> None:
        oauth = self.manifest.oauth
        template = oauth.refresh_payload if self._refresh_token and oauth.refresh_payload else oauth.payload
        payload = self._payload(template)
        request = {"json" if oauth.encoding == "json" else "data": payload}
        try:
            response = await self.client.post(str(self.manifest.base_url).rstrip("/") + oauth.path, **request)
            if response.status_code >= 400:
                raise BrokerUnavailable(f"Toss OAuth 인증 실패 (HTTP {response.status_code}).")
            body = response.json()
            validate_response(body, oauth.response_schema)
            token = field_at(body, oauth.access_token_path)
            expires_in = float(field_at(body, oauth.expires_in_path))
            if not isinstance(token, str) or not token or not math.isfinite(expires_in) or expires_in <= 30 or expires_in > 31_536_000:
                raise ValueError("Invalid token payload")
            refresh = field_at(body, oauth.refresh_token_path) if oauth.refresh_token_path else ""
            if refresh and not isinstance(refresh, str):
                raise ValueError("Invalid refresh token")
            self._access_token = token
            self._refresh_token = refresh or self._refresh_token
            self._expires_at = time.monotonic() + expires_in
        except BrokerUnavailable:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            # Never include the exception body: upstream errors may echo secrets.
            raise BrokerUnavailable("Toss OAuth 응답 또는 네트워크 검증에 실패했습니다.") from None
