"""HTTP client. Only the configured endpoint receives requests and credentials."""

import asyncio
import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from .contract import ContractError, DecideRequest, validate_decision

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = 60.0
    ca_bundle: str | None = None

    def __post_init__(self) -> None:
        endpoint_url(self.base_url)
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("JEV_TIMEOUT_SECONDS must be positive and finite")

    @classmethod
    def from_env(cls) -> "Settings":
        url = os.environ.get("JEV_BASE_URL", "").strip()
        if not url:
            raise ValueError("JEV_BASE_URL is required (your self-hosted backend URL)")
        return cls(
            base_url=url,
            api_key=os.environ.get("JEV_API_KEY") or None,
            timeout_seconds=float(os.environ.get("JEV_TIMEOUT_SECONDS", "60")),
            ca_bundle=os.environ.get("JEV_CA_BUNDLE") or None,
        )


def endpoint_url(base: str) -> str:
    parts = urlsplit(base.strip())
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            "JEV_BASE_URL requires http(s), a host, and no credentials/query/fragment"
        )
    path = parts.path.rstrip("/")
    if not path.endswith("/v1/decide"):
        path += "/decide" if path.endswith("/v1") else "/v1/decide"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


class BackendError(Exception):
    def __init__(
        self, code: str, message: str, *, status: int | None = None, detail: Any = None
    ) -> None:
        super().__init__(message)
        self.code, self.status, self.detail = code, status, detail

    def as_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.status is not None:
            error["http_status"] = self.status
        if self.detail is not None:
            error["upstream"] = self.detail
        return {"error": error}


class DecideClient:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.url = endpoint_url(settings.base_url)
        self.timeout_seconds = settings.timeout_seconds
        self.http = httpx.AsyncClient(
            headers=(
                {"Authorization": f"Bearer {settings.api_key}"}
                if settings.api_key
                else {}
            ),
            timeout=settings.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            verify=settings.ca_bundle or True,
            transport=transport,
        )

    async def __aenter__(self) -> "DecideClient":
        await self.http.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.http.__aexit__(*exc)

    async def _request(
        self, method: str, *, body: dict[str, Any] | None = None
    ) -> dict:
        start = time.monotonic()
        status = None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await self.http.request(
                    method, self.url + ("/info" if method == "GET" else ""), json=body
                )
                status = response.status_code
                try:
                    data = response.json()
                except ValueError:
                    data = None
                if not 200 <= status < 300:
                    raise BackendError(
                        "upstream_http_error",
                        "JEV backend returned an HTTP error",
                        status=status,
                        detail=data,
                    )
                if not isinstance(data, dict):
                    raise BackendError(
                        "invalid_response", "JEV backend returned non-object JSON"
                    )
                if "error" in data:
                    raise BackendError(
                        "upstream_error",
                        "JEV backend returned an error in a success response",
                        status=status,
                        detail=data,
                    )
                return data
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise BackendError(
                "timeout", "JEV request exceeded JEV_TIMEOUT_SECONDS"
            ) from exc
        except httpx.RequestError as exc:
            raise BackendError(
                "connection_error", "Could not reach the configured JEV backend"
            ) from exc
        finally:
            # Never log evidence, questions, probabilities, URLs, tokens or upstream error bodies.
            log.info(
                "jev_request method=%s http_status=%s elapsed_seconds=%.3f",
                method,
                status,
                time.monotonic() - start,
            )

    async def decide(self, request: DecideRequest) -> dict[str, Any]:
        data = await self._request("POST", body=request.wire_body())
        try:
            validate_decision(data, request)
        except ContractError as exc:
            raise BackendError("invalid_response", str(exc)) from exc
        return data

    async def info(self) -> dict[str, Any]:
        data = await self._request("GET")
        if data.get("protocol") != "jev27-bare-v1":
            raise BackendError(
                "invalid_response", "backend protocol is not jev27-bare-v1"
            )
        return data
