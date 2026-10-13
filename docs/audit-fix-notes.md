# Audit fix notes (local, not pushed)

Source: *AmIFree Production Readiness Audit & Fix Backlog*, audited at commit `ff88742`.
State: **uncommitted changes in the working tree; nothing pushed.** Each claim below was
checked against the code before being worked on.

## Done

| ID | Finding | Change |
| --- | --- | --- |
| P0-02 | `ping()` ignored the calendar schema | `app/db/supabase.py`: checks `personal_calendars`, `calendar_busy_blocks`, new `users` columns and probes `amifree_replace_calendar` with a nil id (no data touched). Missing objects are logged by name; `/readyz` and startup fail. |
| P0-03 | `/api/me` hardcoded `outside_calendars_connected: False` | `app/api/routes.py` returns per-calendar `label`/`status`/`synced_at` and a real flag. `web/src/pages/Home.tsx` copy follows it (connected / link not working / not connected). Sealed URLs and error text are never returned. |
| P0-04 | `/whosfree` counted members with no data as free | `app/services/polls.py`: members with no timetable, synced calendar, blocks or plans are left out of slot counts and reported as "had no schedule to check". Suggestions need 3 members **with** a schedule. Card says "no known clash", not "free". |
| P0-05 | No CI | `.github/workflows/ci.yml`: lint, format, smoke, tests + coverage, SQL tests, web build. `docs/verification.md` refreshed. |
| P1-01 | No rate limiting | `app/services/ratelimit.py`. Sign-in 30/min per caller; extraction 15 per 5 min per user; calendar-link fetch 8 per 10 min per user. In-process, single worker. |
| P1-05 | `calendars_due` read 5000 rows into Python | Filters (`checked_at` null or stale), orders and limits in the database. |

Test changes: `tests/sql_transport.py` learned multi-column select, `or` filters and
`nullsfirst`; poll tests now give members a timetable; new tests in `test_api.py`,
`test_supabase.py`, `test_polls.py`, `test_ratelimit.py`.

| P0-01 | `/free` counted Saved plans as busy | Decision: show as tentative. `free_days` in `app/services/scheduling.py`: only blocks and Going plans make you busy; a Saved plan in otherwise-free time is listed under "🟡 Tentative". Footer explains it. |

| P1-02 | Webhook dedupe lived in memory | Migration `008` adds `processed_updates` and `amifree_claim_update/finish/release`. `app/main.py` webhook: a retry of a finished or in-progress update is acknowledged and not repeated (even after a restart); a failure before anything commits releases the claim so the retry runs; a crashed attempt is retaken after 120s. Webhook mode only; polling is unchanged. |
| P0-02 follow-up | No schema-version ledger | Migration `008` adds `schema_migrations` (records version 8; later migrations record themselves). `ping()` requires version >= `SCHEMA_VERSION` (8). |
| P1-03 | Notification failures invisible | `repo.notification_stats`, `Notifier.report_backlog` (warning log `notification_backlog ...` on any failed or 15+ minute stuck message, counts only) and `GET /internal/jobs/stats` (same `X-Jobs-Secret`). |
| P1-09 | Deletion not proven across all tables | `schema.test.mjs`: seeds a row in every per-user table, deletes the user, and asserts zero rows in **every** public table with a `user_id` column (found from the catalogue, so a new table without a cascade fails). Mini App cross-user checks already existed in `test_api.py`. |
| Poll | "Fill mine" answered Free from an empty schedule | `polls.py`: refuses with a clear alert and records nothing. |
| P1-08 | No Mini App tests | `web`: vitest + Testing Library. Home source summary (5 states), PlanCard (version sent, clash confirm, stale), Singapore time helpers. `npm --prefix web test`, run in CI. Mini App feature parity (`/free`, calendar connect screens) is still not built. |

## Needs a decision

- None open.

## Not code changes (owner / live work)

- **P0-06** live multi-account UAT. **P0-07** persistent worker, external scheduler calling
  `/internal/jobs/run`, set `JOBS_SECRET`.
- Apply migrations `005`–`008` in the Supabase SQL Editor, set `CALENDAR_LINK_KEY`. Until the
  migrations are applied, `/readyz` and startup now fail by design (008 included).

## Deferred

- Outbox for side effects beyond update de-dup (P1-02 full), P1-04 live provider tests,
  P1-06 export wording, P1-07 dashboards/backups, Mini App `/free` and calendar-connect
  screens, P1-10 real-message extraction benchmark, all P2.

## Other local changes from this session

- Telegram request timeouts raised (`telegram_builder()` in `app/main.py`); fixes `TimedOut`.
- `date_resolution.py` / `test_date_formats.py`: your earlier uncommitted edits, untouched.
