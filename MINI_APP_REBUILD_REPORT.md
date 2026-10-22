# Mini App functional recovery — 2026-10-21

## Changes in this build

- Restored a dedicated starting-XI screen with a pitch board, manual selection of 11 unique players, availability checks, persistent save, automatic selection, and AI recommendation through the existing `suggest_best_xi` service.
- Added authenticated API endpoints for loading, auto-selecting, recommending, and saving the lineup. Saving a recommended lineup also saves its formation.
- Added manager profile editing with a dedicated `users.manager_name` column so Telegram profile sync does not overwrite the custom in-game name.
- Added authenticated club-emblem editing for an existing club and a preview before saving.
- Removed the sword symbol from Mini App emblem selection and normalized legacy sword emblems to a ball in the API response. Player rows use a person placeholder unless a valid HTTP(S) portrait URL exists.
- Combined scouting into the transfer-market screen as a tab. The AI adviser now opens a real scouting report rather than only showing a toast message.
- Limited the academy intake according to academy level, capped youth OVR for low-level academies, and sorted 90+ potential prospects after more ordinary youth options.
- Reworked the calendar into a date-grouped agenda with competition labels and scores. The fixtures endpoint selects the active/current league, includes the club's current-season cup/Champions League matches, and excludes test fixtures.
- Fixed next-match selection so completed test friendlies cannot override the actual next scheduled match.
- League fixtures now use a consistent 20:00 Moscow-time kickoff instead of inheriting the arbitrary time at which a season was created.

## Validation performed

- `python -m compileall -q app scripts tests` — passed.
- Focused tests: `tests/test_league.py`, `tests/test_season_lifecycle.py`, `tests/test_player_name_diversity.py`, `tests/test_telegram_init_data_auth.py`, `tests/test_migrations_and_security.py` — 15 passed.
- TypeScript parser check reported no syntax errors in the changed TSX/TS files. A full Mini App build could not be run in this environment because npm dependencies are not installed and the package registry request timed out. The server/database integration could not be exercised against a live database because no production database credentials or `asyncpg` driver are available in this environment.

## Deployment notes

Extract both release ZIP parts into the same project directory, allowing the files from part 2 to merge with part 1. The application startup migration runner applies `migrations/066_manager_profile.sql` automatically. No manual Bash operation is required for this feature set.
