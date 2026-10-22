# Real gameplay logic audit — 2026-10-21

## Changes applied

1. **Market catalogue semantics:** player templates remain available as catalogue entries after another club has acquired a copy. A catalogue template is not presented as a seller-owned listing unless it has an active transfer listing.
2. **Correct player ownership on purchase:** buying a template creates a physical player copy for the buyer instead of attaching the catalogue row directly to `club_players`. The transfer history records the acquired player instance. The same template can be acquired by different clubs, while duplicate-template checks still prevent duplicate copies inside one club.
3. **Roster cap:** direct market purchases now enforce the same 30-player squad limit as accepted transfer offers.
4. **Transfer offer settlement:** accepting an offer now uses the amount actually offered, rather than silently multiplying the agreed amount again. The query locks only `o`, `l`, and `p`, avoiding PostgreSQL's `FOR UPDATE` restriction on the nullable side of the `LEFT JOIN club_players`.
5. **Seller integrity:** acceptance verifies the seller still owns the player and that the seller will retain at least 18 players. The insert also normalizes a legacy missing template ID.
6. **Training race:** training now locks the club row before checking the cooldown. Concurrent requests for the same club can no longer both pass a stale cooldown check and award development twice.
7. **Contracts:** newly created player copies receive a three-year contract expiry, including copies made for packs, academy promotions, starting squads, and market purchases.
8. **Database readiness:** `/api/health` remains a liveness endpoint; new `/api/ready` checks PostgreSQL with `SELECT 1` and returns HTTP 503 if the database is unavailable.
9. **Regression contracts:** added `tests/test_real_gameplay_contracts.py` for the gameplay and readiness invariants above.

## Verification performed

- Both input archives were read and tested for ZIP integrity before editing.
- `python -m compileall -q app tests` passed after changes.
- Targeted suite passed: 28 tests across Telegram initData validation, database URL normalization, migration/security checks, production-readiness configuration, SQL safety, and the new gameplay contracts.
- `app/game/engine.py` was not changed.
- `MASTER_UPGRADE_PLAN.md` was preserved.

## Remaining verification limits

- The full pytest suite could not collect in this environment because the `aiogram` package is not installed (`ModuleNotFoundError`).
- No live PostgreSQL/Amvera instance was available, so transaction behaviour must still be smoke-tested against the deployed database.
- The Mini App build was not run: dependencies are not installed and no `package-lock.json` is present in the project directory.
- Telegram login must still be tested from an actual Telegram Mini App session using the deployment's real `BOT_TOKEN`.
