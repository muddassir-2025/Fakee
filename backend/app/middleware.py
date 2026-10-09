"""Cross-cutting HTTP middleware for production hardening.

Pure ASGI (no ``BaseHTTPMiddleware``) so request/response streaming and
background tasks are unaffected.

* ``RequestContextMiddleware`` — assigns/propagates a request id, times the
  request and emits a structured access log line.
* ``SecurityHeadersMiddleware`` — adds defensive response headers.
* ``BodySizeLimitMiddleware`` — rejects oversized request bodies early.
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import settings
from .services import metrics

logger = logging.getLogger("app.access")
error_logger = logging.getLogger("app.errors")

REQUEST_ID_HEADER = "X-Request-ID"

# Path segments that are opaque ids (a 16-hex request id, a uuid, a number) are
# collapsed so metrics labels stay low-cardinality.
_ID_SEGMENT = re.compile(r"^(?:[0-9a-f]{8,}|\d+)$")


def _route_label(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return path
    raw = scope.get("path", "")
    return "/".join("{id}" if _ID_SEGMENT.match(seg) else seg for seg in raw.split("/"))

# Locked-down defaults that still let the interactive docs page load.
CSP = (
    "default-src 'self'; "
    "img-src 'self' data: https:; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "font-src 'self' https://cdn.jsdelivr.net; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)


class RequestContextMiddleware:
    """Request id + access logging."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id

        start = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Let the server-level handler render the 500, but still log it here
            # with the request id so the failure is traceable end to end.
            error_logger.exception(
                "Unhandled error [%s] %s %s",
                request_id,
                scope.get("method"),
                scope.get("path"),
            )
            raise
        finally:
            duration_s = time.perf_counter() - start
            duration_ms = duration_s * 1000
            client = scope.get("client")
            logger.info(
                "%s %s %s %s -> %s %.1fms",
                request_id,
                scope.get("method"),
                scope.get("path"),
                client[0] if client else "-",
                status_holder["status"],
                duration_ms,
            )
            labels = {
                "method": scope.get("method"),
                "route": _route_label(scope),
                "status": status_holder["status"],
            }
            metrics.inc("http_requests", labels)
            metrics.observe("http_request_duration_seconds", duration_s, labels)


class SecurityHeadersMiddleware:
    """Add defensive headers to every response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "no-referrer")
                headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                headers.setdefault(
                    "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
                )
                headers.setdefault("Content-Security-Policy", CSP)
                if settings.is_production:
                    headers.setdefault(
                        "Strict-Transport-Security",
                        "max-age=31536000; includeSubDomains",
                    )
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BodySizeLimitMiddleware:
    """Reject requests whose declared body exceeds ``max_request_bytes``."""

    def __init__(self, app: ASGIApp, max_bytes: int | None = None) -> None:
        self.app = app
        self.max_bytes = max_bytes if max_bytes is not None else settings.max_request_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        header = Headers(scope=scope).get("content-length")
        if header is not None:
            try:
                length = int(header)
            except ValueError:
                length = -1
            if length > self.max_bytes:
                response = JSONResponse(
                    {"detail": "Request body too large.", "max_bytes": self.max_bytes},
                    status_code=413,
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)
