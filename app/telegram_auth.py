"""Strict Telegram Mini App initData validation without logging user secrets."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qsl
from typing import Any

_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class TelegramInitDataError(ValueError):
    """Raised when Telegram Mini App initData cannot be trusted."""


def validate_telegram_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 86400,
    now: float | None = None,
) -> dict[str, Any]:
    """Validate Telegram's HMAC signature, freshness, and user payload.

    The raw initData must be passed unchanged by the Mini App. This function
    never logs or returns the raw query string or its signature.
    """
    if not isinstance(init_data, str) or not init_data.strip():
        raise TelegramInitDataError("missing")
    if not isinstance(bot_token, str) or not bot_token.strip():
        raise TelegramInitDataError("bot_token_missing")

    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True, max_num_fields=100)
    except (ValueError, UnicodeError) as exc:
        raise TelegramInitDataError("malformed") from exc
    if not pairs:
        raise TelegramInitDataError("malformed")

    fields: dict[str, str] = {}
    for key, value in pairs:
        if key in fields:
            raise TelegramInitDataError("duplicate_field")
        fields[key] = value

    received_hash = fields.pop("hash", None)
    if not received_hash or not _HASH_RE.fullmatch(received_hash):
        raise TelegramInitDataError("hash_missing_or_malformed")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.strip().encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash.lower()):
        raise TelegramInitDataError("signature_mismatch")

    try:
        auth_date = int(fields["auth_date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TelegramInitDataError("auth_date_invalid") from exc

    current_time = time.time() if now is None else now
    if auth_date > current_time + 300 or current_time - auth_date > max_age_seconds:
        raise TelegramInitDataError("expired")

    try:
        user = json.loads(fields["user"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise TelegramInitDataError("user_invalid") from exc
    if not isinstance(user, dict) or not user.get("id"):
        raise TelegramInitDataError("user_missing")
    try:
        telegram_id = int(user["id"])
    except (TypeError, ValueError) as exc:
        raise TelegramInitDataError("user_invalid") from exc
    if telegram_id <= 0:
        raise TelegramInitDataError("user_invalid")

    return {
        "telegram_id": telegram_id,
        "first_name": user.get("first_name"),
        "username": user.get("username"),
        "auth_date": auth_date,
    }
