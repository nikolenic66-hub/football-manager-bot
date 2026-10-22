import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from app.telegram_auth import TelegramInitDataError, validate_telegram_init_data

BOT_TOKEN = "123456789:TEST_token_for_unit_tests_only"


def make_init_data(*, auth_date=1_800_000_000, user=None, token=BOT_TOKEN):
    fields = {
        "auth_date": str(auth_date),
        "query_id": "AAEAAAE",
        "user": json.dumps(user or {"id": 42, "first_name": "Test", "username": "tester"}, separators=(",", ":")),
    }
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_valid_init_data_returns_telegram_identity():
    result = validate_telegram_init_data(make_init_data(), BOT_TOKEN, now=1_800_000_010)
    assert result == {
        "telegram_id": 42,
        "first_name": "Test",
        "username": "tester",
        "auth_date": 1_800_000_000,
    }


def test_rejects_wrong_bot_token_without_disabling_signature_check():
    with pytest.raises(TelegramInitDataError, match="signature_mismatch"):
        validate_telegram_init_data(make_init_data(), "987654321:OTHER_TOKEN", now=1_800_000_010)


def test_rejects_expired_init_data():
    with pytest.raises(TelegramInitDataError, match="expired"):
        validate_telegram_init_data(make_init_data(auth_date=1_700_000_000), BOT_TOKEN, now=1_800_000_000)


def test_rejects_duplicate_fields():
    valid = make_init_data()
    with pytest.raises(TelegramInitDataError, match="duplicate_field"):
        validate_telegram_init_data(valid + "&auth_date=1800000000", BOT_TOKEN, now=1_800_000_010)


def test_rejects_modified_user_payload():
    valid = make_init_data()
    with pytest.raises(TelegramInitDataError, match="signature_mismatch"):
        validate_telegram_init_data(valid.replace("Test", "Hacked"), BOT_TOKEN, now=1_800_000_010)
