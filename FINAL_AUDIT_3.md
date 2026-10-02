# FINAL AUDIT #3 — Real-Time Football Manager

## Verdict

The third audit found and fixed additional live-match, notification, and lifecycle issues. The clean release tree passes the complete local test suite.

## Bugs found and fixed

1. **First-half red cards/injuries could crash second-half simulation.**
   The engine previously required exactly 11 players. It now supports 7–11 players, and first-half exits are removed from the active XI before second-half generation.

2. **First-half exits were not reflected in player minutes.**
   The live state now records the minute/type of first-half red cards and injuries and corrects final minutes played.

3. **Live goal notification score could reset to an earlier score.**
   Notification scoring now uses the complete goal history rather than only the current event-cursor batch.

4. **Goal-score calculation used one SQL query per event.**
   Goal history is loaded once per worker tick and scored in memory.

5. **Future scheduled notifications polluted unread/history counts.**
   Only actually delivered notifications appear as unread/history items.

6. **Disabling a notification category could leave old pending notifications to arrive later.**
   Pending notifications are cancelled when a category is disabled.

7. **Telegram delivery could retry forever.**
   Notifications now have a bounded retry policy and a terminal `failed_at` state.

8. **A delayed worker could deliver the “10 minutes before kickoff” message after the match had already started.**
   Kickoff cancels stale pre-start reminders.

9. **Old instant-match lifecycle code remained alongside the real-time lifecycle.**
   Removed obsolete `play_round`, `resume_match`, `resume_round`, `force_resume_live_round`, and the unused scheduled-start helper.

10. **Recovery notifications had been lost during lifecycle cleanup.**
    Restored notifications for injury recovery and suspension expiry.

## Real-time schedule

- 1 game minute = 2 real seconds
- First half = 90 seconds
- Halftime = 60 seconds
- Second half = 90 seconds
- Full match ≈ 4 minutes
- Kickoff comes from the persistent league calendar
- Pre-match notification is sent 10 minutes before kickoff only
- No separate kickoff notification
- Important match events are delivered selectively

## Notification categories

MATCH, INJURY, TRANSFER, FINANCE, DEVELOPMENT, DISCIPLINE, MORALE, TOURNAMENT.

The notification center supports per-category preferences and read/unread state.

## Validation

- **66/66 pytest — PASS**
- `python -m compileall -q app tests` — PASS
- Visual audit — PASS
- 1,248 player-card PNGs — PASS
- 5 formation boards — PASS
- 12 club crests — PASS
- 10 event icons — PASS
- 120/120 real-player visual IDs — PASS
- 1,000 live-style engine matches — PASS
- 42,030 generated events in the live-style stress run
- Full season simulation — PASS
- Clean copied release tree — PASS
- No `__pycache__`, `.pyc`, `.pytest_cache` in release archive

## Remaining environment limitation

No live Telegram token or PostgreSQL server is available in this development environment. Therefore this audit does not claim a real Telegram integration run. SQL/migration code, application lifecycle, game engine, notification logic, visual assets, and local tests were checked independently.

## Recommended staging checks

1. Restart the bot during first half and verify clock catch-up.
2. Restart during the 60-second halftime.
3. Restart during second half.
4. Disable MATCH notifications before a scheduled kickoff and verify the reminder is cancelled.
5. Re-enable MATCH notifications after the missed kickoff and verify the old reminder does not arrive.
6. Force a first-half red card/injury and verify the second-half engine continues with the reduced XI.
7. Test two simultaneous leagues.
8. Test Telegram delivery failure and retry exhaustion.
9. Monitor PostgreSQL locks and worker latency under a full 12-team season.
