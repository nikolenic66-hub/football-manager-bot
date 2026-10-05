# Football Manager Telegram Bot — MVP

Telegram football manager for private leagues with friends. Python 3.12 + aiogram 3 + PostgreSQL + SQLAlchemy + Pillow.

## What is implemented

- player-card database seeded automatically on first start (1,248 cards: 888 BASE / 250 RARE / 80 EPIC / 30 LEGENDARY);
- clubs with 23-player starter squads, budget, stadium, reputation, ticket pricing and sponsor level;
- private leagues for 4/6/8/10/12 clubs; invite codes and double round-robin calendar;
- season progression with one atomic round at a time and final-season state;
- tactical match engine: xG, score, goals, assists, yellow/red cards, injuries, player ratings, MOTM, player talents and phase-based tactical matchup effects;
- saved pre-match lineups and tactics;
- saved starting XI + bench, planned substitutions and in-match roster state;
- formation, style and five tactical sliders; halftime state with a separate second-half tactical setup;
- player fitness/form changes after matches;
- contracts with salary, term, release clause and playing-time expectations;
- transfer listings, offers and negotiated player-to-player transfers;
- training plans with focus/intensity, development points and age/potential curves;
- player morale, match practice, appearances, starts and minutes;
- transfer market with buy/sell transactions and transfer history;
- full club economy: ticket revenue with price elasticity, sponsor income, matchday/stadium costs, periodic wages, season prize money and a persistent financial ledger;
- visual finance dashboard plus ticket-price and stadium-upgrade controls;
- Telegram visual screens for club dashboard, squad, tactics, market, fixtures, standings and match center;
- inline visual tactical controls: formation/style buttons redraw the tactical board in-place, including role/instruction movement arrows;
- PostgreSQL schema and Docker Compose deployment.

## Run locally

```bash
cp .env.example .env
# set BOT_TOKEN and DATABASE_URL
pip install -r requirements.txt
pytest -q
python -m app
```

On first `python -m app`, the database schema is created and the fictional player pool is seeded automatically.

## Run with Docker

```bash
docker compose up --build
```

PostgreSQL is included in `docker-compose.yml`.

## Main Telegram commands

- `/start` — club/menu
- `/squad` — squad, ratings, fitness and form
- `/tactics` — visual tactical board with inline formation/style controls
- `/clubview` — visual club dashboard
- `/finance` — financial dashboard and transaction ledger
- `/ticketprice 25` — set ticket price (€5..€120)
- `/upgradestadium` — upgrade stadium and capacity
- `/squadview` — visual squad screen
- `/marketview` — visual transfer market
- `/tableview` — visual league table
- `/fixturesview` — visual fixture calendar
- `/setformation 4-3-3`
- `/setstyle ATTACK`
- `/setsliders 60 70 50 65 40`
- `/createleague 8 Friends League`
- `/join CODE`
- `/startleague`
- `/lineup ID1 ... ID11` — save your starting XI
- `/sub 60 PLAYER_OFF PLAYER_ON` — plan a second-half substitution
- `/matchcenter` — visual live/latest match center
- `/dynamicmatch` — динамическая карта текущего состояния матча с фазой, прессингом и последним визуальным действием
- `/matchreplay [N]` — отдельный кадр Replay: передача, продвижение, прессинг, отбор, перехват или удар
- `/matchmap` — тактическая карта матча
- `/playround` — start the current round in real time
- `/halftime` — inspect the live halftime match
- `/resume` — show the remaining halftime timer; the second half starts automatically
- `/player PLAYER_ID` — player card/portrait
- `/visuals` — визуальный пакет
- `/formationimage 4-3-3` — визуальная схема
- `/matchimage` — последний матч как Match Center
- `/table` — standings
- `/fixtures` — calendar/results
- `/cards` — player-card database and rarity counts
- `/market` — available players
- `/buy PLAYER_ID`
- `/sell PLAYER_ID`
- `/list PLAYER_ID [ASKING_PRICE]` — list a player on the transfer market
- `/offer LISTING_ID AMOUNT` — make a transfer offer
- `/acceptoffer OFFER_ID` — accept an offer for your listed player
- `/offers` — incoming transfer offers
- `/contract PLAYER_ID [YEARS] [STAR|KEY|SQUAD|PROSPECT]` — renew a contract
- `/contracts` — contract/morale/practice overview
- `/expect PLAYER_ID STAR|KEY|SQUAD|PROSPECT` — set playing-time expectation
- `/training [FOCUS] [LIGHT|NORMAL|HIGH]` — set and run a training session

## Verification performed in this environment

- `pytest -q` — all current tests pass.
- `python -m compileall -q app` — successful.
- `scripts/simulate_season.py` — successful 8-team, 14-round simulation.
- deterministic stress test — 1,000 matches / 25,032 visualized match events passed.
- visual replay smoke test — event-level pitch arrows and markers rendered successfully.

A real Telegram + PostgreSQL integration run still requires a local/server environment with the Python dependencies, a PostgreSQL instance and a Telegram BotFather token.

The project can run from the bundled curated seed, and can be extended with properly licensed real-player data through the importer.

## Club economy

Each league round settles the club finances after the match: the home club receives ticket revenue based on attendance and ticket price, both clubs receive sponsor income, player salaries are charged for the period, and the home club pays matchday/stadium costs. All operations are written to `club_financial_transactions`, while `club_season_finances` keeps season totals.

Ticket price is intentionally elastic: raising the price increases revenue per spectator but reduces demand. Stadium level increases capacity and upgrade cost. Final league positions receive season prize money. Transfer purchases and sales are also recorded in the financial ledger.

The `/finance` screen shows the current balance, ticket price, sponsor level, stadium capacity, category totals and recent transactions.

## Player card system

The player pool is now designed for **1,248 cards** with four rarities:

- ⚪ BASE — 888 cards
- 🟢 RARE — 250 cards
- 🟣 EPIC — 80 cards
- 🟡 LEGENDARY — 30 cards

The curated premium seed contains real footballers (including a separate 30-card legend pool). The remaining pool is generated and marked `data_source=generated`, so real-world data can be imported without mixing it with generated players.

For a larger real-player database, use `scripts/import_players.py` with a properly licensed/public CSV dataset. The project does not silently download third-party player data at startup.

The database also stores card version, rarity, real/generated flag, preferred foot, secondary positions, portrait URL and data source, so portraits and multiple card editions can be added without changing the game model later.

## Real players and portraits

The seed contains a curated real-player premium/legend pool and a larger generated base pool. For a larger licensed real-player import, use `scripts/import_players.py` with a CSV whose data rights permit reuse. `scripts/fetch_portraits.py` can then resolve portraits from Wikimedia Commons and save the source/license/author metadata next to the player record. Wikimedia notes that files have individual license conditions and that portrait/personality rights can be separate from copyright, so the script deliberately stores provenance instead of assuming every image is automatically safe to reuse.

## Player talents

Cards have tactical talents such as `PLAYMAKER`, `PRESS_RESISTANT`, `INVERTED_FULLBACK`, `COUNTER_RUNNER`, `POACHER`, `ANCHOR`, `TARGET_MAN` and `FALSE_NINE`. Talents affect specific tactical situations rather than simply adding a flat overall rating.

## Real-time match loop

Matches run in accelerated real time: **1 game minute = 2 real seconds**. The first half lasts 90 seconds, halftime lasts 60 seconds, and the second half lasts another 90 seconds, so a full match is about 4 minutes.

The league calendar is the single source of truth for kickoff times. Every round receives a persisted `scheduled_at`; the live worker starts the round automatically when that time arrives. The scheduled kickoff is retained after the match starts so the fixtures screen does not lose the original time. If the worker wakes up late, the persisted kickoff timestamp is used as the phase start rather than granting a fresh first half.

The live clock is persisted in PostgreSQL (`live_phase`, `phase_started_at`, `halftime_ends_at`, `second_half_started_at`). A background worker advances matches every second, starts the second half automatically, finalizes the match and survives bot restarts.

During halftime all managers in the league have the same 60-second window to change formation, style and sliders. `/resume` does not bypass the shared timer. Telegram notifications are intentionally selective: one pre-match reminder is scheduled 10 minutes before kickoff, while live notifications are reserved for goals, dangerous shots, yellow/red cards, injuries and substitutions. Assists are folded into goal notifications instead of creating duplicate messages. Routine passes and movement remain available through the match map.

## Notification center

`/notifications` provides per-category controls and a notification history. Categories are Match, Injuries, Transfers, Finance, Development, Discipline, Morale and Tournament. Notifications are deduplicated by a persistent key. Delivery failures are retried with backoff, and Telegram network calls are performed after the notification rows are claimed so the live worker does not hold database row locks while waiting on Telegram.

Important persistent notifications include injury recovery, the end of a suspension, transfer offers/completions, player development, critical club balance and season completion/prize placement.

## Visual system

The repository now includes a deterministic visual layer: 1,248 fallback player-card PNGs, five formation boards, twelve generic crest templates, ten event icons and a UI concept reference. Player cards use a silhouette when no verified portrait file is available; `scripts/fetch_portraits.py` can populate portraits from Wikimedia Commons on a machine with internet access and stores source/license/author metadata. The game can therefore render every player even before a portrait is licensed/imported.

Telegram uses photo messages for the club crest, player cards, formation boards, tactical board, squad, market, standings, fixtures and Match Center. The tactical screen can update its existing photo message when a manager changes formation or style, reducing chat clutter. Real portrait files are intentionally populated only when their source/license metadata is available; otherwise every player has a verified fallback card.

Run `PYTHONPATH=. python scripts/build_visual_assets.py` after changing the visual templates.

## Match depth

The current match loop is explicitly two-phase: 0–45, halftime decisions, then 46–90. Starting XI selection is persisted; up to seven bench players are available; planned substitutions can change the player pool during the second half. Yellow cards, rare red cards and injuries are emitted by the engine, with injury/suspension state persisted between matches.

The visual Match Center is generated from the same match state used by the engine, so the image is not a separate mock screen. Match events now also carry deterministic JSON metadata with pitch coordinates and action type (`PASS`, `CARRY`, `PRESSURE`, `TACKLE`, `INTERCEPTION`, `SHOT`), allowing `/matchreplay` to reconstruct concrete actions instead of showing only text commentary.

## Скаутинг и контртактика

Перед матчем можно изучить ближайшего соперника:

```text
/scout
```

или конкретный клуб:

```text
/scout OPPONENT_CLUB_ID
```

Отчёт строится по последним завершённым матчам и показывает типовую схему, стиль, результативность, ключевых игроков и наблюдаемые тенденции.

Контртактика сохраняется отдельно для пары клубов:

```text
/setcounter OPPONENT_ID PRESS_PLAYMAKER TARGET_PLAYER_ID
/setcounter OPPONENT_ID ATTACK_FLANKS
/setcounter OPPONENT_ID HIGH_LINE_TRAP
/setcounter OPPONENT_ID TARGET_SLOW_CB TARGET_PLAYER_ID
/setcounter OPPONENT_ID LOW_BLOCK
```

Поддерживаемые фокусы: `PRESS_PLAYMAKER`, `ATTACK_FLANKS`, `HIGH_LINE_TRAP`, `TARGET_SLOW_CB`, `LOW_BLOCK`, `BALANCED`.

Контртактика влияет на расчёт xG только в подходящих игровых ситуациях; она не является постоянным скрытым бонусом к общему рейтингу команды.

## Tactical phases

The engine now separates tactical effects into build-up, possession, pressing, defensive block, transition attack/defence and width. Player talents feed those phases, while the opponent's pressing and defensive line create matchup effects. This keeps tactical decisions interpretable: for example, a high press can disrupt a weak build-up, while a high defensive line can expose a counter-runner or a counter-oriented setup.

The visual tactical board uses the exact same formation slot source as the engine. The five supported shapes are audited for exact positional counts:

- `4-3-3` = 4 defenders / 3 midfielders / 3 forwards
- `4-4-2` = 4 defenders / 4 midfielders / 2 forwards
- `4-2-3-1` = 4 defenders / 2 holding midfielders / 3 attacking midfielders / 1 striker
- `3-5-2` = 3 defenders / 5 midfielders / 2 strikers
- `5-3-2` = 5 defenders / 3 midfielders / 2 strikers

`scripts/audit_visuals.py` verifies all 1,248 card files, the formation asset count, the exact formation semantics, the real-player card ID coverage and the card manifest.

## Production-safety notes (fixed release)

- `POSTGRES_PASSWORD` is required by Docker Compose; the database port is internal by default.
- `DATABASE_URL` must use the same PostgreSQL credentials.
- Migrations are versioned in `schema_migrations`; legacy installations are not replayed destructively.
- Only one live worker is active at a time via PostgreSQL advisory lock.
