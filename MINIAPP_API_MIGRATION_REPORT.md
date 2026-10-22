# Mini App API migration report

## Scope
Removed hardcoded player, transfer-market, scouting recommendation, training, academy, pack, league-fixture, and stadium data from `mini_app/src/ClubScreen.tsx`. These screens now load and mutate data through authenticated API calls. Actions refresh affected club state where appropriate.

## Existing API routes before this change
- `GET /api/health`
- `GET /api/me`
- `POST /api/club/create`
- `GET /api/club/{club_id}`
- `GET /api/squad/{club_id}`
- `GET /api/matches/live`
- `GET /api/matches/next`
- `GET /api/tactics/{club_id}`
- `POST /api/tactics/{club_id}`
- `POST /api/transfers/bid`
- `GET /api/league`
- `WS /ws`

## New authenticated API routes
- `GET /api/transfers/listings` — market player rows from the database. `POST /api/transfers/bid` submits an offer for a listed player, or purchases an unlisted/free-market player through the existing service.
- `GET /api/scout/recommendations` — eligible youth/player templates ordered by potential gap.
- `GET /api/league/fixtures` — fixtures belonging to the authenticated club's league and involving that club.
- `GET /api/training/today` — current training plan and real squad summary.
- `POST /api/training/complete` — invokes the existing `train_squad` game service and returns cooldown/completion status.
- `GET /api/academy/youth` — eligible youth templates not already in the user's squad.
- `POST /api/academy/promote` — adds an eligible template copy to the authenticated club.
- `GET /api/packs/list` — reads configured packs from the `packs` table.
- `POST /api/packs/open` — invokes the existing pack-opening service, charges the club and records pack history.
- `GET /api/stadium/info` — real stadium, level, capacity, ticket price, fans and upgrade cost.
- `POST /api/stadium/upgrade` — invokes the existing stadium-upgrade/financial transaction service.

All new endpoints use `authenticated_telegram_user` and resolve the club via `user_and_club`. The UI uses loading/error/empty states instead of fake player or activity rows. The transfer UI explicitly tells the user that offers/history need their own API support rather than fabricating records.

## Changed files
- `app/server.py`
- `mini_app/src/api.ts`
- `mini_app/src/ClubScreen.tsx`

## Validation
- Python `compileall`: passed.
- TypeScript/TSX syntax transpilation: passed.
- `npm run build`: could not be completed because the environment did not have installed React/Vite dependencies; package installation timed out. This is an environment/dependency limitation, not a claimed successful build.
- `pytest`: collection blocked because `aiogram` is not installed in the environment.

No changes were made to `app/game/engine.py`. `MASTER_UPGRADE_PLAN.md` remains in release part 1. `TACTICS_SPEC.md` is not included.
