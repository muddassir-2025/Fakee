"""Thin async client for Groq's OpenAI-compatible chat completions API.

Returns ``None`` when Groq is not configured or the call fails, so callers can
degrade gracefully to heuristic extraction.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone

import httpx

from ..config import settings
from .text_utils import safe_json_loads

logger = logging.getLogger(__name__)

# Statuses worth retrying: rate limits and transient upstream failures.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class GroqClient:
    def __init__(self) -> None:
        self.enabled = settings.groq_enabled
        self._client: httpx.AsyncClient | None = None
        # Last rate-limit snapshot seen on a Groq response, exposed to clients
        # via /api/groq/quota so they can show how much budget remains.
        self._quota: dict = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.groq_base_url,
                timeout=settings.groq_timeout_seconds,
                headers={
                    "Authorization": f"Bearer {settings.groq_api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _record_quota(self, response: httpx.Response) -> None:
        """Remember the rate-limit headers Groq returns on every response."""

        def num(name: str) -> int | None:
            raw = response.headers.get(name)
            if raw is None:
                return None
            try:
                return int(float(raw))
            except (TypeError, ValueError):
                return None

        limit_requests = num("x-ratelimit-limit-requests")
        limit_tokens = num("x-ratelimit-limit-tokens")
        if limit_requests is None and limit_tokens is None:
            return
        self._quota = {
            "limit_requests": limit_requests,
            "remaining_requests": num("x-ratelimit-remaining-requests"),
            "limit_tokens": limit_tokens,
            "remaining_tokens": num("x-ratelimit-remaining-tokens"),
            "reset_requests": response.headers.get("x-ratelimit-reset-requests"),
            "reset_tokens": response.headers.get("x-ratelimit-reset-tokens"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def quota(self) -> dict:
        """Return the last-known Groq budget (empty until the first call)."""
        return {"enabled": self.enabled, **self._quota}

    @staticmethod
    def _retry_delay(attempt: int, response: httpx.Response | None) -> float:
        """Exponential backoff with jitter, honouring a Retry-After header."""
        if response is not None:
            header = response.headers.get("retry-after")
            if header:
                try:
                    return max(0.0, min(30.0, float(header)))
                except ValueError:
                    pass
        base = settings.groq_backoff_base_seconds
        return min(20.0, base * (2 ** attempt)) + random.uniform(0, 0.5)

    async def chat(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = True,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str | None:
        """Return the assistant message content, or None on any failure."""
        if not self.enabled:
            return None
        payload: dict = {
            "model": settings.groq_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        attempts = max(1, settings.groq_max_retries + 1)
        try:
            client = await self._get_client()
            for attempt in range(attempts):
                try:
                    response = await client.post("/chat/completions", json=payload)
                except httpx.TransportError as exc:
                    # Connection reset/timeout — retryable like a 5xx.
                    if attempt == attempts - 1:
                        logger.warning("Groq transport error after retries: %s", exc)
                        return None
                    await asyncio.sleep(self._retry_delay(attempt, None))
                    continue

                # Capture quota headers on every response, including 429s.
                self._record_quota(response)

                if response.status_code in RETRYABLE_STATUS and attempt < attempts - 1:
                    delay = self._retry_delay(attempt, response)
                    logger.info(
                        "Groq returned %s; retrying in %.1fs (attempt %d/%d)",
                        response.status_code,
                        delay,
                        attempt + 1,
                        attempts,
                    )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
            return None
        except Exception as exc:  # noqa: BLE001 - degrade on any failure
            logger.warning("Groq chat failed: %s", exc)
            return None

    async def chat_json(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> dict | list | None:
        content = await self.chat(
            system, user, json_mode=True, temperature=temperature, max_tokens=max_tokens
        )
        if not content:
            return None
        parsed = safe_json_loads(content)
        if parsed is None:
            logger.warning("Groq returned non-JSON content: %s", content[:200])
        return parsed


groq_client = GroqClient()
