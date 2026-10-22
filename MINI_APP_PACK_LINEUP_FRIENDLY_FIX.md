# Mini App fixes: pack opening, lineup formation, friendly/test matches

Date: 2026-10-21

## Changes

1. **Player packs**
   - The pack API returns the actual card rarity, OVR, potential, age, nationality, card version and portrait URL from the selected player template.
   - Mini App shows a full player card after opening and a staged opening overlay animation.
   - Card frame/glow is driven by the server's `rarity`: BASE, RARE, EPIC, LEGENDARY. No rarity is inferred from pack number or a frontend random choice.

2. **Starting XI / formation**
   - `/api/lineup` returns the actual slot coordinates from `app/game/board.py`.
   - The pitch renders player markers at the selected formation's coordinates instead of grouping players into four hard-coded rows.
   - The save endpoint validates the selected 11, rejects unavailable players, and stores them in the same formation-slot order consumed by the match engine.
   - AI recommendations return their own formation coordinates, so a recommended 4-2-3-1 does not get drawn using stale 4-3-3 slots.

3. **Friendly and test match**
   - Added authenticated `POST /api/matches/friendly` and `POST /api/matches/test` endpoints.
   - Both run the existing match simulation engine with the user's real club against a reusable isolated test opponent.
   - Match rows are tagged as test/friendly and are not linked to a league or season, so they do not change standings or award normal league rewards.
   - Match events, lineups, tactics, final score and xG are persisted. A short debounce returns the just-completed match if the user repeats a request immediately, avoiding duplicate rows.
   - Added Mini App buttons for friendly and test matches and a result panel.

4. **Regression tests**
   - Added static regression checks for pack rarity/card fields, animation classes, formation slot coordinates, and both match endpoints.
   - Updated an existing source-contract test to match the current query that prioritizes the user's non-test club.

## Validation

- 11 targeted tests passed (`test_minapp_pack_lineup_friendly.py`, `test_quick_test_own_club.py`, `test_database_url_normalization.py`, `test_player_name_diversity.py`).
- All Python source files compile successfully.
- TypeScript/JSX syntax check passed (no TS parser/syntax errors).
- A complete frontend typecheck/build could not be verified in this environment because React/Vite dependencies are absent and `npm install` timed out. The production Dockerfile still builds the Mini App with `npm install && npm run build`; a successful Docker build and live Amvera/database run are not claimed.
- No production database was accessed, no existing clubs/players were deleted, and no migration is required for these changes.

## Deployment note

Deploy the two ZIP parts together, extracted into the same project directory. Do not replace only one part. Verify the Docker build and test the pack, lineup save/reload, friendly match and test match in the deployed Mini App before treating the release as production-verified.
