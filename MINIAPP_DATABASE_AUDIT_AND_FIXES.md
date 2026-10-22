# Mini App / database integration audit and fixes

## Existing API integration

The current Mini App uses real FastAPI endpoints for club data, squad, matches, tactics, transfers/listings/offers/history, scouting/reports, league standings/fixtures, training, academy, packs, and stadium upgrades. The endpoints use `authenticated_telegram_user`; club-scoped data is resolved from the authenticated Telegram identity and database. The checked UI files do not use hardcoded demo arrays for those listed API-driven screens. Empty database results can still appear empty rather than being replaced with synthetic content.

## Current fixes

1. Added `GET /api/transfers/offers`, incoming/outgoing offer accept/reject routes, transfer history, and persisted scouting reports.
2. Mutating endpoints are not wrapped in automatic retry, avoiding accidental duplicate purchases, training, pack opens, promotions, stadium upgrades, or club creation after uncertain commit outcomes.
3. HTTP and WebSocket authentication now share strict Telegram initData HMAC validation and freshness checks. Signature validation has not been disabled. Logs include only safe reason codes.
4. Provider-style `postgres://` and `postgresql://` values are normalized to `postgresql+asyncpg://` before creating the async SQLAlchemy engine.
5. The database engine retains `pool_pre_ping=True`, `pool_recycle=300`, and `connect_args={"statement_cache_size": 0}`.
6. The live worker checks its dedicated advisory-lock connection and does not attempt explicit unlock on a known-closed connection; PostgreSQL releases session locks when the session is lost.

## Validation limits

- `python -m compileall -q app tests`: passed.
- Focused tests: 9 passed.
- Full pytest collection is blocked in this environment because `aiogram` is not installed.
- Frontend build could not be confirmed because dependency installation timed out; `npm run build` then reported missing React/type modules.
- Live database and Amvera smoke tests cannot be performed from this environment. After deployment, check `/api/health`, then open the Mini App from Telegram and verify `/api/me`, club loading, and database-backed operations.

The patch does not modify `app/game/engine.py` and preserves `MASTER_UPGRADE_PLAN.md`.
