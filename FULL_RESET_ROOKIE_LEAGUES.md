# Full reset and automatic league placement

## Behavior
- The admin full reset is available even when a league or match is active. It still requires explicit confirmation because the operation is destructive. The live scheduler uses the same transaction-scoped advisory lock so its event/match updates do not race the reset.
- The reset removes current fixtures, results, standings, tournament records, league records and season history, then rebuilds club squads and club configurations.
- After the reset, the system chooses one primary non-test club for each real Telegram user (`telegram_id > 0`) and places those clubs first into fresh 16-team Rookie leagues.
- Each league is filled to 16 with existing non-test bot clubs; new bot clubs are created only when the existing pool is insufficient.
- Fresh leagues use the newly created preseason season and schedule the first round from the next day. Cup and Champions League are not started by this flow.
- The reset validates that every primary real-user club has an active Rookie league membership and does not have duplicate memberships.

## Validation performed
- `python -m py_compile app/services.py app/handlers/admin.py tests/test_liveops_admin_and_reset.py` passed.
- Targeted source-level tests for reset behavior passed.
- A full DB-backed reset test was not run in this workspace; runtime integration with the production PostgreSQL database and the Amvera build still needs to be confirmed.
