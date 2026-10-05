# football-manager-bot — fixed release report

## Validation

- Test suite: **77 passed**.
- Python bytecode compilation: `python -m compileall -q app scripts` passed.
- Docker Compose syntax was not executed because Docker is not installed in the audit environment.
- No new runtime dependency was added.

## Main fixes

### CRITICAL / HIGH

1. **League start race** — `start_league()` now locks the waiting league with `FOR UPDATE`; migration `012` adds a fixture uniqueness constraint.
2. **League join race** — `join_league()` locks the league before checking capacity and inserting the member.
3. **Duplicate live workers** — `live_worker()` acquires a PostgreSQL session-level advisory lock and releases it on shutdown.
4. **Live state after restart** — phase progression continues from persisted `phase_started_at` / `halftime_ends_at`, with `SKIP LOCKED` batch processing.
5. **Second-half incidents** — RED/INJURY are generated deterministically at a minute and immediately remove the player from the active squad for subsequent minutes.
6. **Second-half fatigue** — fatigue is calculated as a team aggregate instead of using `home_now[0]` / `away_now[0]`.
7. **Small/empty squads** — the match engine no longer crashes when a side drops below seven players or reaches zero; effective strength falls with player availability.
8. **Substitution validation** — outgoing player must be in the starting XI; incoming player must be a bench player; duplicates/injuries are rejected.
9. **Transfer lock ordering** — buyer and seller clubs are locked in deterministic `ORDER BY id FOR UPDATE` order to reduce deadlock risk.
10. **Private league isolation** — scouting and counter-tactics now require the two clubs to share a league.
11. **One club per user** — creation is checked under a user row lock; migration `012` adds a unique index for clean databases.
12. **Financial match settlement** — match financial entries are idempotent by `(match_id, club_id, category)`; recurring match expenses can create explicit club debt rather than aborting match finalization.
13. **Live player statistics** — migration `013` adds `match_player_stats` and `matches.motm_player_id`; live finalization persists ratings, goals, assists and MOTM input atomically with match completion.
14. **Migration lifecycle** — `schema_migrations` prevents re-running historical SQL. Legacy installations are marked through migration 011 without replaying destructive historical statements.
15. **Database pool** — `pool_pre_ping=True` and `pool_recycle=1800` are configured.
16. **Rate limiting** — aiogram message middleware applies a lightweight per-user/per-command cooldown.
17. **Config validation** — bot token and PostgreSQL URL are validated through `pydantic-settings`.
18. **Docker DB exposure** — PostgreSQL port is no longer published by default and the password is supplied via environment variables.
19. **Visual card caching** — player cards use a deterministic fingerprint sidecar to avoid regenerating unchanged images.
20. **Portrait script path** — generated cards now target `assets/visual/cards` instead of the incorrect `visual/cards` path.

## Tests added

- empty-team match simulation;
- deterministic RED/INJURY second-half event generation/order;
- first-half empty-team safety;
- advisory-lock contract;
- league join/start row-lock contracts;
- deterministic transfer lock-order contract;
- transfer cancellation/acceptance state guards;
- financial negative-balance/debt behavior;
- versioned migration contract;
- Docker secret/exposure contract;
- persisted live-phase restart contract.

## Deliberately not changed

- No new microservices, Redis, Kafka or Kubernetes.
- No existing assets were intentionally modified.
- The large `services.py` / single-router architecture was not split into many modules because doing so would be a broad structural rewrite rather than a correctness fix.
- No ORM lazy-loading code exists in the current project: DB access is predominantly SQLAlchemy Core/raw SQL with bound parameters, so `selectinload/joinedload` is not applicable to the current data model.

## Known limitation

The environment used for this build did not provide a running PostgreSQL instance. Therefore the added concurrency checks are code-contract/unit tests, not a live two-transaction PostgreSQL race test. The production code does use PostgreSQL-specific `FOR UPDATE`, `SKIP LOCKED` and advisory locks; those should be exercised once against a real PostgreSQL service in CI.
