# Player identity anonymization audit

## Findings

- `real_players_extra.json` contained 480 unique real-player identities. The records' football attributes were retained; only `first_name` and `last_name` were replaced with deterministic fictional names.
- The admin command in `app/handlers/admin.py` imported that JSON and hard-coded `real_player=TRUE`. It now generates fictional names independently of the JSON payload and inserts `real_player=FALSE` with `data_source='synthetic_extra'`.
- `scripts/import_players.py` accepted arbitrary CSV names and marked inserted rows as real. It now generates fictional identities and writes `real_player=FALSE`.
- `app/player_catalog.py` currently contains 120 fictional premium templates. No current `INSERT INTO players` statements were found in `migrations/`.
- A full-name scan against the 480 names from the original JSON found no exact matches in any other project file. The requested literal search for Messi/Ronaldo/Bellingham/Neymar/Mbappe/Haaland/Salah also returned no matches.
- The likely historical total of approximately 600 came from the 480-record extra-player JSON plus the older 120-player premium catalog. The current archive already has the premium catalog anonymized.

## Changes

- `app/seed.py`: `TOTAL_PLAYERS=3000`; builds 120 fictional premium templates plus 2,880 synthetic templates.
- `app/player_names.py`: shared deterministic fictional-name helper.
- `scripts/expand_players.py`: additive expansion to IDs 1..3000; inserts only missing IDs and never updates existing rows.
- `scripts/anonymize_content.py`: continues to select and anonymize every row in `players`, not just the first 120.
- `migrations/062_anonymize_legacy_real_players.sql`: anonymizes legacy rows marked real or carrying legacy real/CSV source markers. It changes identity/portrait/source fields only; player attributes and ratings are not changed.
- Import paths for JSON and CSV now use fictional identities and do not set `real_player=TRUE`.

## Verification

- 480 JSON records, 480 unique fictional names; names match the deterministic fictional-name generator.
- Full seed generation: 3,000 IDs, 3,000 unique names, zero `real_player=TRUE` rows, zero premium/synthetic name collisions.
- Generated rarity counts: BASE 2,640; RARE 250; EPIC 80; LEGENDARY 30.
- `py_compile` passed for the requested files and related changed modules.
- Focused tests: 15 passed.
- The live PostgreSQL database was not connected in this build environment. Apply migration 062 and run `python scripts/anonymize_content.py` against the configured database to change existing database rows; this audit does not claim those live rows were already updated.
