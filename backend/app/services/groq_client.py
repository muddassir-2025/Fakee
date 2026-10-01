"""Thin async client for Groq's OpenAI-compatible chat completions API.

Returns ``None`` when Groq is not configured or the call fails, so callers can
degrade gracefully to heuristic extraction.
"""

from __future__ import annotations

import json
import logging

import httpx

from ..config import settings
from .text_utils import safe_json_loads

logger = logging.getLogger(__name__)


class GroqClient:
    def __init__(self) -> None:
        self.enabled = settings.groq_enabled
        self._client: httpx.AsyncClient | None = None

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
        try:
            client = await self._get_client()
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

            response = await client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
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
