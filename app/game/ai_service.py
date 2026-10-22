"""Shared APInex AI request layer used by game features."""
from __future__ import annotations

import logging
import os
from typing import Optional

try:
    import httpx
except ImportError:  # Keep the game usable if the optional HTTP client is absent.
    httpx = None

logger = logging.getLogger(__name__)

API_URL = "https://api.apinex.bond/v1/chat/completions"
MODEL = "free/deepseek-v4-pro-0813"
_TIMEOUT_SECONDS = 10.0


async def ask_ai(
    prompt: str,
    system_prompt: str,
    max_tokens: int = 200,
    temperature: float = 0.7,
) -> Optional[str]:
    """Send one chat-completion request; return text or ``None`` on any failure."""
    api_key = os.getenv("APINEX_API_KEY", "").strip()
    if not api_key:
        logger.warning("ai_service: fallback, reason=missing_api_key")
        return None
    if httpx is None:
        logger.warning("ai_service: fallback, reason=httpx_unavailable")
        return None

    try:
        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": str(system_prompt)},
                {"role": "user", "content": str(prompt)},
            ],
            "max_tokens": max(1, int(max_tokens)),
            "temperature": float(temperature),
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        logger.info(
            "ai_service: request model=%s prompt_chars=%d max_tokens=%d",
            MODEL,
            len(str(prompt)),
            payload["max_tokens"],
        )
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(API_URL, headers=headers, json=payload)
        if response.status_code == 402:
            logger.warning("ai_service: fallback, reason=http_402_payment_required")
            return None
        if response.status_code == 429:
            logger.warning("ai_service: fallback, reason=http_429_rate_limited")
            return None
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            logger.warning("ai_service: fallback, reason=invalid_content_type")
            return None
        content = content.strip()
        if not content:
            logger.warning("ai_service: fallback, reason=empty_response")
            return None
        return content
    except Exception as exc:
        logger.warning(
            "ai_service: fallback, reason=request_error error=%s",
            type(exc).__name__,
        )
        return None
