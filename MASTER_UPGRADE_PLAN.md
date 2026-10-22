# MASTER_UPGRADE_PLAN.md

**Project:** `football-manager-bot` / Manager2  
**Source of truth for this plan:** only `part-1.zip` + `part-2.zip` supplied in this chat.  
**Important:** GitHub `main` of Manager2 is **not** used for this plan.  
**Planning horizon:** 2–3 months.  
**Tactics constraint:** the tactical specification is treated as the product requirement; **formations = 10, not 5**. The current code already declares these ten formations in `app/game/tactics.py`:

1. `4-3-3`
2. `4-4-2`
3. `4-2-3-1`
4. `3-5-2`
5. `5-3-2`
6. `4-1-2-1-2`
7. `4-3-1-2`
8. `4-5-1`
9. `3-4-3`
10. `4-1-4-1`

> This document is an **architecture/product plan only**. No code change is part of this document.

---

## 1. Введение

### 1.1. Что уже есть в Manager2

Аудит двух архивов показывает, что Manager2 уже имеет полноценное игровое ядро, поэтому правильная стратегия — **расширять существующую систему, а не переписывать её**.

Current baseline includes:

- Python 3.12 + aiogram 3 + PostgreSQL + SQLAlchemy + Pillow;
- private leagues for 4/6/8/10/12 clubs;
- invite codes and double round-robin calendars;
- season progression and league history;
- tactical match engine with xG, score, goals, assists, cards, injuries, ratings and MOTM;
- tactical phases, formation matchups, styles, five tactical sliders and player talents;
- persisted starting XI, bench and planned substitutions;
- halftime state and second-half tactical changes;
- live match worker with persisted live clock state;
- replay/match-map event metadata with PASS/CARRY/PRESSURE/TACKLE/INTERCEPTION/SHOT;
- opponent scouting and counterplans;
- contracts, wages, playing-time expectations and morale;
- training, development points and `player_development_log`;
- transfer listings, offers, negotiated transfers and transfer history;
- club economy, ticket-price elasticity, sponsors, wages, matchday/stadium costs and prize money;
- persistent financial ledger;
- stadium level/capacity and upgrade flow;
- Telegram visual screens for squad, tactics, market, fixtures, standings, finance and match center;
- 1,248-card player pool with real/generated provenance, rarity and card metadata;
- Docker/Amvera deployment baseline and a substantial regression-test suite.

### 1.2. Главная цель V2

Сделать Manager2 не просто Telegram-ботом с матчами, а **живым multiplayer football manager**, где решения менеджера имеют причинно-следственные последствия:

`тактика → матч → форма/усталость → развитие → трансферы → экономика → стадион → репутация → следующий сезон`.

### 1.3. Правило интеграции внешнего кода

Приоритет — **сначала адаптировать механику и контракт интерфейса, затем переносить код только там, где лицензия и архитектура позволяют**.

- Apache-2.0: прямой перенос отдельных совместимых компонентов потенциально допустим с сохранением notice/license и фиксацией изменений.
- GPLv3: прямое включение кода в Manager2 требует отдельного лицензионного решения; в текущем плане — **только идеи/алгоритмы**.
- No license / placeholder / personal-educational terms: **код не копировать**, использовать только идеи и архитектурные наблюдения.
- Данные Football Manager, портреты и proprietary player datasets не импортировать без отдельного права на использование.

---

## 2. Матч-движок

### 2.1. Текущее состояние

Ключевые файлы:

- `app/game/engine.py`
- `app/game/tactics.py`
- `app/game/ratings.py`
- `app/game/talents.py`
- `app/game/board.py`
- `app/game/channels.py`
- `app/game/match_state.py`
- `app/live_worker.py`
- `app/services.py`

Существующий engine уже учитывает:

- player quality;
- formation profile/matchup;
- tactical styles;
- five sliders;
- phase effects;
- player talents;
- counterplans;
- fatigue;
- score/minute urgency;
- home advantage;
- position/channel effects;
- live events and persisted live state.

### 2.2. Сравнение с Global Soccer Manager

**Manager2 лучше:**

- PostgreSQL persistence вместо только simulation-oriented persistence;
- multiplayer league state;
- xG и richer match statistics;
- Telegram live delivery;
- match replay/match map;
- player-specific talents;
- explicit counterplans;
- existing tactical board;
- club economy/transfer/contract integration.

**Global Soccer Manager лучше как архитектурный reference:**

- отдельные match phases как явный state machine;
- possession-chain abstraction;
- clear MatchEvent model;
- live tactical changes как first-class match action;
- deterministic simulation/testing around phase transitions;
- auto-substitution policy.

Global Soccer Manager документирует фазы `build_up → midfield → final_third → shot`, stateful match simulation и возможность менять тактику по ходу матча; проект лицензирован Apache-2.0. См. `MATCH_ENGINE_NOTES.md` и engine docs.

**Источник:** Global Soccer Manager — Apache-2.0.  
**Direct-code status:** потенциально можно адаптировать небольшие совместимые компоненты, но не заменять `engine.py` целиком.

### 2.3. План: не переписывать engine

**Фича:** Possession Chains / Phase Transitions  
**Источник:** Global Soccer Manager.  
**Механика:** разбить существующую генерацию match events на последовательности `build-up → midfield → final third → shot/turnover`, сохранив текущие xG, channel, talent и counterplan layers.  
**Manager2:** `app/game/engine.py`, новый `app/game/possession_chain.py` при необходимости.  
**Добавить:** phase result object, next-phase decision, turnover reason, event metadata.  
**Direct code:** Apache-2.0 позволяет рассматривать адаптацию, но предпочтительно переписать адаптер под Manager2 API; сохранить license/notice, если код переносится.  
**Risk:** средний; engine-sensitive.  
**Priority:** 2.

**Фича:** Live Tactical Change  
**Источник:** Global Soccer Manager + fm26.  
**Механика:** изменение formation/style/sliders должно менять следующие possession chains, а не только сохраняться в БД.  
**Manager2:** `app/services.py`, `app/game/engine.py`, `app/live_worker.py`.  
**Добавить:** `effective_tactics_at_minute`, tactical snapshot per phase, safe re-evaluation boundary.  
**Direct code:** Global Soccer Manager — потенциально да по Apache-2.0; fm26 — нет, его README содержит placeholder `[Your License Here]`, поэтому код не копировать.  
**Risk:** высокий, если менять live state без snapshot.  
**Priority:** 2.

**Фича:** Auto-substitutions  
**Источник:** Global Soccer Manager + cmanager.  
**Механика:** AI может заменить травмированного/сильно уставшего игрока на подходящего игрока той же position-group; учитывать красную карточку и лимит замен.  
**Manager2:** `app/game/engine.py`, `app/services.py`, новый `app/game/substitutions.py`.  
**Добавить:** substitution policy, fatigue threshold, injury/red-card trigger, replacement score.  
**Direct code:** Global Soccer Manager — Apache-2.0, потенциально адаптируем; cmanager — лицензия в репозитории не зафиксирована на странице аудита, поэтому прямое копирование не планировать.  
**Risk:** средний.  
**Priority:** 2.

**Фича:** Injury Risk 2.0  
**Источник:** fm26.  
**Механика:** fatigue + stamina + strength/endurance + match intensity + late-match factor + tackle context + pitch/context + severity.  
**Manager2:** `app/game/engine.py`, `app/game/match_state.py`, `migrations/` новая injury-history migration.  
**Добавить:** injury-risk calculator и persisted injury history.  
**Direct code:** нет; fm26 license placeholder, используем только поведение как reference.  
**Risk:** средний.  
**Priority:** 2.

---

## 3. Тактика и AI

### 3.1. Текущая база

`app/game/tactics.py` уже содержит **10 формаций**, пять стилей и пять слайдеров:

- tempo;
- pressing;
- width;
- defensive line;
- aggression.

Также есть formation profiles, matchup rules, strengths/weaknesses и formation slider deltas.

`app/game/talents.py` уже связывает player talents с tactical context.

### 3.2. AI-тренер — P1

**Фича:** AI Coach / AI Tactical Adjustment  
**Источник:** fm26.  
**Механика:** AI оценивает scoreline, minute, possession, shots/on-target, dangerous phases, stamina/fatigue, momentum/context и меняет style/sliders/formation/substitution plan.  
**Manager2:** новый `app/game/ai_coach.py`; интеграция через `app/services.py` и `app/live_worker.py`; чтение tactical state из `app/game/tactics.py`.  
**Добавить:** `AICoachDecision`, reason codes, decision cooldown, confidence, allowed changes.  
**Direct code:** нет; fm26 не предоставляет пригодную open-source license declaration.  
**Risk:** высокий, потому что AI не должен выдавать бесплатный hidden bonus.  
**Priority:** **1**.

Принцип:

```text
match state
   ↓
AI observation
   ↓
problem detection
   ↓
candidate tactical changes
   ↓
cost/risk evaluation
   ↓
decision
   ↓
next possession phase
```

AI не должен менять всё каждый игровой тик. Нужны cooldown и минимальная разница между решениями.

### 3.3. AI decision reasons

Минимальные reason codes:

- `TRAILING`
- `LEADING`
- `LOW_XG`
- `UNDER_PRESSURE`
- `MIDFIELD_LOSS`
- `FATIGUE`
- `RED_CARD`
- `INJURY`
- `WIDE_EXPOSURE`
- `HIGH_LINE_EXPOSED`

### 3.4. Тестовый контракт AI

Каждое решение должно быть explainable:

`decision → reason → expected effect → actual next-phase effect`.

Это важно для Telegram/Mini App: пользователь должен понимать, почему соперник перестроился.

---

## 4. Трансферы

### 4.1. Текущее состояние

Manager2 уже имеет:

- market listing;
- asking price;
- offers;
- accept/reject flow;
- player-to-player transfer;
- contracts;
- salary;
- release clause;
- transfer history;
- player potential/value;
- club budget;
- financial ledger.

Основные точки интеграции: `app/services.py`, `migrations/005_scouting.sql`, economy migrations и bot transfer handlers.

### 4.2. Transfer Interest — P1

**Фича:** Transfer Interest  
**Источник:** FMSuperScout + идея из scouting tools.  
**Механика:** оценка того, насколько игрок хочет перейти в клуб, а не только может ли клуб оплатить transfer fee.  
**Manager2:** новый `app/game/transfer_interest.py`; `app/services.py`; новая migration при необходимости.  
**Добавить:** deterministic score 0–100 и reason breakdown.  
**Direct code:** FMSuperScout не следует считать лицензированным для прямого встраивания без отдельной проверки LICENSE/terms; использовать алгоритмическую идею и реализовать свою модель.  
**Risk:** средний.  
**Priority:** **1**.

Предлагаемая модель:

```text
interest =
  club_reputation_fit
+ wage_fit
+ playing_time_fit
+ age/career_fit
+ potential/development_fit
+ competition_level_fit
+ contract_context
- risk_penalties
```

Никакой один фактор не должен гарантировать переход.

### 4.3. AI Clubs: analyse → search → buy

**Фича:** AI Transfer Strategy  
**Источник:** cmanager.  
**Механика:** AI-клуб анализирует squad gaps → определяет priority positions → формирует shortlist → оценивает budget/wage/interest → делает offer → обновляет squad. cmanager уже реализует AI transfer strategy, contract expiry/free-agent pool и multi-season transfer loop.  
**Manager2:** новый `app/game/ai_transfer.py`; `app/services.py`; возможная `app/tasks/ai_transfer_market.py`.  
**Добавить:** squad need matrix, target score, budget guard, bid policy, sale policy.  
**Direct code:** не планируется — license status не подтверждён.  
**Risk:** высокий из-за multiplayer economy.  
**Priority:** 2.

### 4.4. Market Inflation

**Фича:** League-driven market inflation  
**Источник:** cmanager.  
**Механика:** league revenue/TV pool/competition money влияет на transfer price scale и зарплаты. cmanager прямо связывает TV pool с transfer-market inflation.  
**Manager2:** новый `app/game/market_economy.py`; `app/game/economy.py`; `app/services.py`.  
**Добавить:** league price index, season reset/decay, bounds.  
**Direct code:** нет; реализовать самостоятельно.  
**Risk:** высокий, потому что уже есть реальные market values.  
**Priority:** 3.

### 4.5. Transfer Interest объяснимый UI

Вместо одной цифры показывать:

```text
Interest 78/100
+ Reputation  +18
+ Wage         +16
+ Playing time +15
+ Development  +14
- Club level    -5
- Contract      -3
```

Это станет одной из ключевых функций Mini App market.

---

## 5. Экономика

### 5.1. Текущее состояние

`app/game/economy.py` уже содержит:

- ticket price bounds;
- stadium capacity;
- attendance model;
- ticket revenue;
- stadium match cost;
- sponsor income;
- wage bill;
- season prize money.

`app/services.py` сохраняет операции в financial ledger.

### 5.2. TV / Sponsor Economy

**Фича:** TV pool + sponsor scaling  
**Источник:** cmanager.  
**Механика:** распределять TV money и sponsor income по competition/club tier, а не только по фиксированной формуле.  
**Manager2:** `app/game/economy.py`, `app/services.py`, новые finance migrations.  
**Добавить:** league TV pool, sponsor tiers, season settlement breakdown.  
**Direct code:** нет; использовать механику как reference.  
**Risk:** средний.  
**Priority:** 2 для базовой TV/sponsor модели; 3 для полной inflation loop.

### 5.3. Financial Health

**Фича:** Financial Health Score  
**Источник:** synthesis из cmanager + текущего Manager2 ledger.  
**Механика:** cash runway, wage ratio, debt, recurring revenue, stadium load, transfer spend.  
**Manager2:** новый `app/game/financial_health.py`; finance service/UI.  
**Добавить:** 0–100 score + warnings.  
**Direct code:** собственная реализация.  
**Risk:** низкий.  
**Priority:** 2.

---

## 6. Репутация клуба

### 6.1. Текущее состояние

В `clubs` уже есть `reputation`, а экономика использует reputation для attendance/sponsor income. Это хорошая база, но параметр пока слишком общий.

### 6.2. Club Reputation Model

**Фича:** Multi-factor Club Reputation  
**Источник:** Open Football.  
**Механика:** разделить reputation на спортивную, внутреннюю, финансовую и историческую составляющие, которые влияют на трансферы, спонсоров, attendance и интерес игроков. Open Football строит долгосрочную симуляцию, где transfers, finances и reputation связаны.  
**Manager2:** `migrations/` новая reputation migration; новый `app/game/reputation.py`; интеграция в `app/game/economy.py` и transfer interest.  
**Добавить:** `domestic_rep`, `international_rep`, `financial_rep`, `historical_rep`, `current_form_rep` или агрегатор без обязательного хранения всех компонентов.  
**Direct code:** Open Football — Apache-2.0; прямой перенос отдельных generic компонентов возможен с соблюдением license, но предпочтительно реализовать собственную модель.  
**Risk:** средний.  
**Priority:** 2.

### 6.3. Репутация как feedback loop

```text
результаты
  ↓
club reputation
  ↓
attendance / sponsors / player interest
  ↓
budget
  ↓
transfers / squad quality
  ↓
результаты
```

Нужны cap/floor и медленное изменение, чтобы один матч не сделал клуб «легендарным».

---

## 7. Игроки и Развитие

### 7.1. Текущее состояние

Manager2 уже имеет:

- 1–100 core attributes;
- potential;
- market value;
- salary;
- age;
- fitness/form/morale;
- talents;
- tactical archetype;
- secondary positions;
- preferred foot;
- development points;
- training batches;
- `player_development_log`.

Поэтому **полностью переделывать player model сейчас не нужно**.

### 7.2. Player Development History — P1

**Фича:** Player Development History  
**Источник:** FMSuperScout.  
**Механика:** периодические snapshots и сравнение текущего состояния с выбранной reference date; growth chart показывает CA/attribute movement во времени. FMSuperScout использует snapshots именно для отслеживания роста.  
**Manager2:** существующий `player_development_log` + новый `app/game/player_history.py`; новая migration для snapshot table, если лог недостаточен.  
**Добавить:** `player_attribute_snapshot`, `ca_snapshot`, `potential_snapshot`, source/event.  
**Direct code:** нет; FMSuperScout code не переносить без подтверждённой лицензии.  
**Risk:** низкий/средний.  
**Priority:** **1**.

### 7.3. CA/PA

**Фича:** Current Ability / Potential Ability layer  
**Источник:** fm26.  
**Механика:** отделить текущий уровень от потолка; OVR рассчитывать от attributes + position/role, а PA использовать как ceiling.  
**Manager2:** player schema migration, `app/game/ratings.py`, `app/services.py`.  
**Добавить:** CA derived value; PA remains explicit.  
**Direct code:** нет; fm26 license placeholder.  
**Risk:** высокий, потому что текущая система использует `potential` и OVR.  
**Priority:** 2.

### 7.4. Meta Score

**Фича:** Manager2 Meta Score  
**Источник:** FMSuperScout.  
**Механика:** отдельный показатель эффективности/перспективности, который не заменяет OVR. В будущем учитывать position, role fit, growth room и current form.  
**Manager2:** новый `app/game/meta_score.py`.  
**Добавить:** `meta_current`, `meta_potential`, explanation factors.  
**Direct code:** нет.  
**Risk:** низкий.  
**Priority:** 2.

---

## 8. Скаутинг

### 8.1. Текущее состояние

Manager2 уже имеет `/scout`, opponent scouting, transfer market filters и player-management reports.

### 8.2. Умный скаутинг — P1

**Фича:** Advanced Scouting Filters  
**Источник:** FMSuperScout + FM Scout.  
**Механика:** комбинируемые filters: position, age, potential, value, wage, nationality, contract, transfer status, foot, attributes and role. FMSuperScout поддерживает grouped attribute filters with min/max thresholds and AND-combination; FM Scout также имеет 40+ attribute filters, player profiles, best-XI and squad-gap analysis.  
**Manager2:** `app/services.py`, transfer-market handlers; новый `app/game/scouting.py`; новые indexes/migration.  
**Добавить:** filter specification object, query builder, indexed attributes, saved searches.  
**Direct code:** нет.  
**Risk:** средний.  
**Priority:** **1**.

### 8.3. Player Comparison

**Фича:** Compare 2–3 players  
**Источник:** FMSuperScout.  
**Механика:** side-by-side attributes, role fit, form, fitness, contract, market value, potential and development trend.  
**Manager2:** новый `app/game/player_comparison.py`; Mini App/API later; Telegram fallback can show compact comparison.  
**Добавить:** normalized comparison DTO.  
**Direct code:** нет.  
**Risk:** низкий.  
**Priority:** 2.

### 8.4. Role Fit

**Фича:** Position/Role Rating  
**Источник:** FMSuperScout/FМ Scout.  
**Механика:** player receives different fit scores by tactical role; role score depends on attributes and Manager2 tactical instructions, not a copied FM formula.  
**Manager2:** новый `app/game/role_rating.py`, интеграция с `app/game/ratings.py` and `app/game/roles.py` if/where available.  
**Добавить:** `role_fit(player, role, tactic_context)`.  
**Direct code:** нет.  
**Risk:** средний.  
**Priority:** 2.

### 8.5. Источник FM Scout: только UX/алгоритмы

FM Scout имеет player search, pitch-position diagram, best XI, squad-gap analysis, league comparison and shortlist, но его repository прямо говорит `personal and educational use only`. Поэтому **код не переносить**. Использовать только UI/feature ideas.  
**Priority:** 3 для Best XI и squad analysis.

---

## 9. Стадион и Инфраструктура

### 9.1. Текущее состояние

Manager2 уже имеет stadium level 1–20, capacity calculation, ticket price, attendance elasticity, matchday cost и upgrade command.

### 9.2. Infrastructure Layers

**Фича:** Infrastructure modules  
**Источник:** synthesis из текущей экономики + manager projects.  
**Механика:** разбить один `stadium_level` на meaningful facilities: seats, VIP/hospitality, pitch, training centre, medical centre, academy, scouting facilities.  
**Manager2:** новая `app/game/infrastructure.py`; migration for facility levels.  
**Добавить:** facility upgrade cost, effects and maintenance.  
**Direct code:** собственная реализация.  
**Risk:** средний.  
**Priority:** 2/3.

### 9.3. Stadium feedback loop

- VIP → ticket revenue;
- pitch → match/injury context;
- training centre → development efficiency;
- medical centre → recovery/injury mitigation;
- academy → youth intake;
- scouting → scouting quality;
- hospitality → sponsor/VIP revenue.

Это должно быть **отложено до стабилизации economy**, чтобы не перегрузить P1/P2.

---

## 10. Мультиплеер

### 10.1. Текущее состояние

Manager2 уже ориентирован на private leagues 4/6/8/10/12 clubs, invite codes, round-robin, persisted league state and shared halftime windows.

### 10.2. Пул ожидания / Waiting Pool

**Фича:** League Waiting Pool  
**Источник:** собственная multiplayer архитектура; не копировать single-player managers.  
**Механика:** пользователи создают/join waiting pool, league starts when target size reached or creator starts it, with explicit state machine.  
**Manager2:** `app/services.py`, league migrations, new `app/game/league_lifecycle.py`.  
**Добавить:** `WAITING → READY → ACTIVE → FINISHED → ARCHIVED`, min/max players, invite expiry, reconnect-safe join.  
**Direct code:** собственная реализация.  
**Risk:** высокий; concurrency-sensitive.  
**Priority:** 2.

### 10.3. Multiplayer safety rules

- one authoritative DB state;
- transaction boundaries around league start;
- unique constraints for membership;
- idempotent round settlement;
- one live worker lock;
- no Telegram handler-only game state;
- deterministic event IDs where possible.

---

## 11. Mini App

### 11.1. Цель

Перенести управление из большого набора Telegram messages в полноценный Telegram Mini App, **не меняя game engine**.

### 11.2. Frontend stack

**Стек:**

- Vue 3;
- TypeScript;
- Vite;
- Pinia;
- Tailwind CSS.

### 11.3. Backend

FastAPI должен жить **рядом с текущим ботом** и использовать общие game services/repositories, а не дублировать бизнес-логику.

```text
Telegram Bot ─┐
              ├── shared game services ── PostgreSQL
Mini App ─────┘             │
                             └── workers/live state
```

Предпочтительная API-группа:

- `/api/auth/telegram`
- `/api/me`
- `/api/club`
- `/api/club/dashboard`
- `/api/squad`
- `/api/players/{id}`
- `/api/players/compare`
- `/api/scouting/search`
- `/api/lineup`
- `/api/tactics`
- `/api/fixtures`
- `/api/matches/{id}`
- `/api/matches/{id}/events`
- `/api/market`
- `/api/finance`
- `/api/stadium`
- `/api/league`

Telegram Mini App authentication must validate Telegram `initData` server-side; do not trust a raw `telegram_id` supplied by the browser.

### 11.4. Deployment

**Docker:** Amvera builds the Vue frontend and runs the Python backend. Build should produce static frontend assets and serve them from the backend/container or a dedicated static layer without duplicating game logic.

### 11.5. Reference design

Primary reference for information architecture and Telegram football-manager Web App patterns:

- [vfxper/fm26 frontend](https://github.com/vfxper/fm26/tree/main/frontend)
- [vfxper/fm26 repository](https://github.com/vfxper/fm26)

Use the reference as **UX inspiration only**. Do not copy proprietary assets, player data or code without license clearance.

### 11.6. Mini App screens

1. Dashboard
2. Squad
3. Player profile
4. Compare
5. Tactics
6. Live match
7. Match replay
8. Fixtures
9. League table
10. Transfers
11. Scouting
12. Finance
13. Stadium
14. Training
15. Notifications

Mini App implementation is intentionally **long-term** and follows stabilization of P1/P2 game mechanics.

---

## 12. Backlog

The following are explicitly **P3 after Mini App / after core systems are stable**.

### P3-01 — Weather
**Source:** Global Soccer Manager.  
**Mechanic:** weather/pitch condition as match context affecting pace, fatigue, passing risk and injury probability.  
**Manager2:** new `MatchContext`; `app/game/engine.py`, `match_state.py`.  
**Direct code:** potentially Apache-2.0 compatible, but prefer own implementation.  
**Risk:** medium.  
**Priority:** 3.

### P3-02 — Market Inflation
**Source:** cmanager.  
**Mechanic:** league revenue/TV pool changes transfer-price scale.  
**Manager2:** `app/game/market_economy.py`.  
**Direct code:** no.  
**Risk:** high economy impact.  
**Priority:** 3.

### P3-03 — Autonomous AI World
**Source:** Open Football.  
**Mechanic:** AI clubs buy/sell, develop players and alter reputation/finances without waiting for human action.  
**Manager2:** worker/task layer + `ai_transfer.py` + season lifecycle.  
**Direct code:** Open Football is Apache-2.0, but porting its Rust engine is unnecessary; implement the world loop in Python/Manager2 services.  
**Risk:** high.  
**Priority:** 3.

### P3-04 — Staff
**Source:** Openfoot Manager.  
**Mechanic:** coaches, scouts, medical staff, training effects.  
**Manager2:** new staff domain + migrations.  
**Direct code:** no; Openfoot Manager is GPLv3.  
**Risk:** medium.  
**Priority:** 3.

### P3-05 — Inbox / News
**Source:** Openfoot Manager.  
**Mechanic:** dynamic club/world news feed.  
**Manager2:** notifications/event aggregation.  
**Direct code:** no; GPLv3.  
**Risk:** low.  
**Priority:** 3.

### P3-06 — Depth Chart
**Source:** Openfoot Manager.  
**Mechanic:** visual hierarchy of first/second/third-choice players per role/position.  
**Manager2:** new squad-analysis service + Mini App UI.  
**Direct code:** no; GPLv3.  
**Risk:** low.  
**Priority:** 3.

### P3-07 — Best XI
**Source:** FM Scout.  
**Mechanic:** best XI recommendation based on selected formation and role/duty fit.  
**Manager2:** `app/game/best_xi.py`.  
**Direct code:** no; FM Scout terms are personal/educational.  
**Risk:** medium.  
**Priority:** 3.

### P3-08 — Squad Analysis
**Source:** FMSuperScout.  
**Mechanic:** squad strengths, weak positions, depth, average attributes, role coverage.  
**Manager2:** `app/game/squad_analysis.py`.  
**Direct code:** no; implement own model.  
**Risk:** low.  
**Priority:** 3.

---

## 13. Strict Priority Map

### P1 — ближайшие 2 недели

1. **AI Coach / AI Tactical Adjustment — fm26**
2. **Transfer Interest + Smart Scouting — FMSuperScout**
3. **Player Development History — FMSuperScout**

P1 rule: do not change the card renderer or redesign the full Mini App before these are stable.

### P2 — следующий месяц

1. **Auto-substitutions — Global Soccer Manager**
2. **AI transfers — cmanager**
3. **Club Reputation — Open Football**
4. **Live tactical changes — Global Soccer Manager**

P2 rule: preserve existing match/economy APIs and add services around them.

### P3 — backlog after Mini App

1. Weather — Global Soccer Manager
2. Market inflation — cmanager
3. Autonomous AI world — Open Football
4. Staff — Openfoot Manager
5. Inbox/news — Openfoot Manager
6. Depth chart — Openfoot Manager
7. Best XI — FM Scout
8. Squad analysis — FMSuperScout

---

## 14. Suggested New Modules

The following names are architectural proposals, not implementation instructions for this sprint:

```text
app/game/ai_coach.py
app/game/possession_chain.py
app/game/substitutions.py
app/game/injury_risk.py
app/game/transfer_interest.py
app/game/ai_transfer.py
app/game/market_economy.py
app/game/reputation.py
app/game/player_history.py
app/game/meta_score.py
app/game/scouting.py
app/game/player_comparison.py
app/game/role_rating.py
app/game/financial_health.py
app/game/infrastructure.py
app/game/league_lifecycle.py
app/game/best_xi.py
app/game/squad_analysis.py
```

Avoid turning `app/services.py` into a larger monolith. Existing service functions should remain orchestration boundaries; new business rules belong in domain/game modules with unit tests.

---

## 15. Testing Strategy

Every P1/P2 feature must have:

1. pure unit tests;
2. database integration tests where persistence is involved;
3. deterministic simulation tests;
4. regression tests for existing Manager2 behavior;
5. concurrency/idempotency tests for multiplayer-sensitive changes;
6. explainable event/decision metadata where AI is involved.

### Match invariants

- no match crashes with <11 players;
- no duplicate substitutions;
- red-card player cannot return;
- injured player cannot continue;
- halftime remains shared across managers;
- live worker remains single-authority;
- xG stays bounded and calibrated;
- event ordering is deterministic under the same seed/context.

### Economy invariants

- every money movement has a ledger entry;
- no transfer creates negative budget unless explicitly allowed by policy;
- sponsor/TV settlement is idempotent;
- season prize settlement is idempotent;
- inflation has hard bounds;
- multiplayer transactions are atomic.

### Transfer invariants

- a player cannot be sold twice;
- one listing has one owner at a time;
- offer acceptance is atomic;
- AI cannot buy outside budget/wage constraints;
- Transfer Interest never bypasses contract rules.

---

## 16. License / Code Reuse Matrix

| Source | Relevant contribution | License status | Direct code? |
|---|---|---|---|
| Global Soccer Manager | match phases, live tactics, auto-subs, weather | Apache-2.0 | **Potentially yes**, with notices; prefer adaptation |
| fm26 | AI coach, injury model, CA/PA, player search | README has `[Your License Here]` | **No** |
| cmanager | AI transfers, TV/sponsor, inflation | no clear license confirmed in audit | **No** |
| Open Football | reputation, autonomous world, long-term sim | Apache-2.0 | **Potentially**, but own Python implementation preferred |
| FMSuperScout | filters, comparison, development, meta | license not treated as cleared for reuse in this plan | **No** |
| Openfoot Manager | staff, inbox, depth planning | GPLv3 | **No** for Manager2 unless licensing strategy changes |
| FM Scout | best XI, squad gaps, filters | personal/educational use | **No** |

This table is a planning gate, not legal advice. Before shipping copied third-party code, perform a file-level license/NOTICE review.

---

## 17. What We Deliberately Do NOT Change Yet

- Do not replace the existing match engine.
- Do not replace PostgreSQL.
- Do not replace the existing league/multiplayer model.
- Do not rebuild the existing transfer workflow from zero.
- Do not redesign the player-card renderer before the player/scouting model stabilizes.
- Do not replace the Telegram bot; Mini App becomes a second client.
- Do not modify `.github/workflows/main.yml` as part of this plan.
- Do not import proprietary Football Manager player data or portraits without rights.

---

## 18. Следующие шаги

### Этот спринт — AI-тренер

Build the P1 AI Coach contract first:

- observation snapshot;
- decision reasons;
- tactical candidate generation;
- decision cooldown;
- safe integration into live match phases;
- tests against leading/trailing/fatigue/pressure scenarios.

### Следующий спринт — Transfer Interest

Add:

- player interest score;
- reputation/wage/age/potential/playing-time factors;
- reason breakdown;
- integration into offer/acceptance flow;
- scouting filters that surface high-interest targets.

### Следующий месяц — P2

Implement auto-substitution, AI transfer strategy, club reputation and live tactical changes while preserving the current engine and multiplayer contracts.

### В долгую — Mini App

Create the Vue 3 + TypeScript + Vite + Pinia + Tailwind Mini App with FastAPI beside the bot, sharing the same game services and PostgreSQL state. The Mini App is the presentation layer; it must not become a second game engine.

---

## 19. External Reference Projects

- Global Soccer Manager — match engine / live tactics reference.
- vfxper/fm26 — Telegram Web App / AI tactical / player-system reference.
- phant0um/cmanager — AI transfer / economy reference.
- ZOXEXIVO/open-football — reputation / autonomous world reference.
- mavarobli/FMSuperScout — scouting / development / comparison reference.
- openfootmanager/openfootmanager — staff / inbox / depth-planning reference.
- dgarfias/fm_scouter — best-XI / squad-analysis UX reference.

The external repositories are **references only**. Manager2 remains the system of record and implementation target.
