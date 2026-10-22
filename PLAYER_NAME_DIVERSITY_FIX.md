# Player name diversity fix

## What changed

- Expanded the deterministic fictional first-name catalog to 420 entries and the surname catalog to 1,400 entries.
- Changed the deterministic name sequence so first names and surnames both vary across consecutive players. The complete catalog produces 588,000 distinct first/last-name pairs before a fallback suffix is needed.
- The 480 legacy extra-player JSON records now have 480 distinct full names and 480 distinct surnames; their gameplay stats, ages, positions, nationalities, and potential values are preserved.
- Expanded the 120-player premium catalog to use 120 distinct surnames and distributed synthetic seed players across 40 nationality codes instead of assigning every generated player NLD.
- Added regression tests for catalog size, unique surnames in the extra-player set, and the surname-ordering bug.
- Reworked `scripts/reanonymize_players.py` to report duplicate-name counts in dry-run mode and, when explicitly run without `--dry-run`, update only `first_name` and `last_name` in one transaction.

## Apply to an existing database

First deploy both updated project archive parts and ensure they are merged into the same project directory. Then run the preview command from the project root:

```bash
python scripts/reanonymize_players.py --dry-run
```

Review the counts. To actually replace existing player names, run:

```bash
python scripts/reanonymize_players.py
```

This script changes only `players.first_name` and `players.last_name`; it does not modify IDs, stats, club ownership, rarity, contracts, nationality, market value, or other gameplay data. Back up PostgreSQL before applying it. Do not set `FORCE_RESEED=1` just to fix names: that variable triggers a destructive full reseed in `app/seed.py`.

## Validation

The name-focused tests pass in the local archive inspection environment. Python compilation passes. Full application tests are not claimed here because the environment does not have all project runtime dependencies installed, and no live Amvera database was available for verification.
