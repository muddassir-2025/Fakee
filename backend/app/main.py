"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api.routes import router
from .config import settings
from .db import engine, init_db
from .middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from .services.exa import exa_client
from .services.groq_client import groq_client

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "Starting %s (%s, docs=%s)",
        settings.app_name,
        settings.environment,
        "on" if settings.docs_enabled_effective else "off",
    )
    if not settings.groq_enabled:
        logger.warning("GROQ_API_KEY not set — using heuristic extraction.")
    if not settings.exa_live:
        # Expected: the extension does its own searching. Server-side
        # /investigate would use deterministic mock results.
        logger.info("Server-side Exa search disabled — mock results for /investigate.")
    await init_db()
    logger.info("Database ready: %s", "sqlite" if settings.is_sqlite else "postgresql")
    try:
        yield
    finally:
        await groq_client.aclose()
        await exa_client.aclose()
        await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    description=(
        "Evidence-based fake job/internship detector: structured extraction, "
        "web investigation, domain verification, scam-pattern detection and an "
        "explainable risk assessment."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.docs_enabled_effective else None,
    redoc_url="/redoc" if settings.docs_enabled_effective else None,
    openapi_url="/openapi.json" if settings.docs_enabled_effective else None,
)

# Middleware runs outermost-first in reverse registration order, so the request
# context (timing + request id) wraps everything, then the body limit, then the
# security headers, then CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=settings.allow_cors_credentials,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(RequestContextMiddleware)

if settings.trusted_host_list != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)

app.include_router(router, prefix=settings.api_prefix)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler: never leak a stack trace to the client."""
    request_id = _request_id(request)
    logger.exception("Unhandled exception [%s] on %s", request_id, request.url.path)
    detail = str(exc) if settings.verbose_errors else "Internal server error."
    return JSONResponse(
        status_code=500,
        content={"detail": detail, "request_id": request_id},
    )


@app.get("/")
async def root() -> dict:
    return {
        "service": settings.app_name,
        "docs": "/docs" if settings.docs_enabled_effective else None,
        "api": settings.api_prefix,
    }
