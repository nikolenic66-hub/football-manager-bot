# Latest fixes: Telegram Mini App auth + PostgreSQL connection setup

## Changed files

- `app/server.py`
- `app/telegram_auth.py` (new)
- `app/config.py`
- `tests/test_telegram_init_data_auth.py` (new)
- `tests/test_database_url_normalization.py` (new)

## Telegram initData

- HTTP endpoints and the WebSocket now use the same validation routine.
- Validation checks Telegram's HMAC signature using the configured bot token, rejects duplicate query parameters, validates the auth date and parses the signed user object.
- Signature validation remains enabled. Unsigned or modified initData is rejected.
- API logs record only a reason code; raw initData, hashes, user payloads, and bot tokens are never logged.
- Error messages now distinguish missing data, bad signature/token mismatch, expired data, and malformed data.

## PostgreSQL URL

`create_async_engine` requires an async SQLAlchemy driver. The settings validator now converts provider-style `postgres://...` and `postgresql://...` URLs to `postgresql+asyncpg://...`. Explicit `postgresql+asyncpg://...` URLs remain supported; unsupported drivers are rejected early.

The database engine retains `pool_pre_ping=True`, `pool_recycle=300`, and `connect_args={"statement_cache_size": 0}`. The live-worker advisory-lock release remains guarded against closed/invalidated connections.

## Checks

- `python -m compileall -q app tests`: passed.
- Focused tests: `9 passed` (Telegram initData validation, DB URL configuration contract, live-worker lock recovery).
- Full `pytest -q`: collection blocked because `aiogram` is not installed in this execution environment (`ModuleNotFoundError`).
- `npm run build`: not verified successfully because `npm install` timed out and React/type packages were unavailable; TypeScript then reported missing React modules. The Dockerfile still performs `npm install` and `npm run build` during the deployment image build.
- No live Amvera or PostgreSQL connection was available here, so deployment runtime must still be confirmed after publishing these archives.
