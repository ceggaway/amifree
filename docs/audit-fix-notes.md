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
| P1-01 | No rate limiting | `app/services/ratelimit.py`. Sign-in 120/min per caller (first `X-Forwarded-For` hop); extraction 15 per 5 min per user; calendar-link fetch 8 per 10 min per user. In-process, single worker. |
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

## Tester feedback list (13 October, `CS3216 final proj.pdf`)

All six fixed; tests in `tests/test_feedback_fixes.py`.

| # | Report | Fix |
| --- | --- | --- |
| 1 | Location edit showed the same before and after | `bare_venue` in `scheduling.py`: an edit reply that changes nothing and holds no date or time becomes the new venue. The before/after preview still lets the user refuse it. |
| 2 | `/free` only offered three days | `/free` now leaves a pending "free" question, so a typed `15 oct`, `oct 15`, `fri`, `this week`, `next week` or `fri 7-9pm` is answered. A long message, an invitation or a `busy ...` block is not swallowed. |
| 3 | "8PM" / "2000" not accepted as an end time | The bot remembers which time it asked for (`asked`), and a bare time answers it directly with `resolve_time`, skipping the LLM. Prompt reworded to "Please type in 24-hour time (eg. 07:00, 18:00)." |
| 4 | Full-day-free message and "Checked:" footer | `/free` lists busy items (blocks, plans, calendar entries) then free gaps; a fully free day says "Free all day". Saved plans are listed as "(Saved, not decided)" and never make you busy. Footer removed. |
| 5 | Block over a Going plan gave no warning; "(Singapore)" | `block_overlaps`: the block preview lists overlapping Going, Maybe and Saved plans. "(Singapore)" is now "SGT" in block and event previews. |
| 6 | Clash only visible later in `/events` | The "Blocked." confirmation lists the plans it clashes with. |
| 5b | (found in the screenshot) "What time is this block?" then "18:30-19:00" was read as an event | The half-given block is remembered (`block_ask`), and the next message completes it. |

Behaviour to know: the "Fill mine"/"Free" wording in polls is unchanged; `/free` for a partly busy day
shows both the busy list and the free gaps.

## Pictures and quick messages (13 October)

- `app/services/image_reader.py`: OpenAI vision turns a forwarded picture into plain text
  (`store=False`, image treated as untrusted content). The text then uses the existing extractor and
  date/time guards, so there is one set of rules for typed and pictured plans.
- `app/bot/handlers.py` `gather_text`: text, caption, poll question and options, photo, and image
  sent as a file. Caption and picture text are combined. A failed read falls back to the caption.
- Rate limit: 10 pictures per person per 5 minutes (`picture_limit`), in addition to the extraction limit.
- Quick messages: `DEFAULT_EVENT_DURATION_MINUTES` now defaults to 120 (was 0 = always ask); the
  card labels the length as assumed. `tonight`, `this evening`, `tmr morning` etc. resolve a bare hour
  to am/pm and clear a model "am or pm" flag.
- Config: `READ_PICTURES` (default true), `OPENAI_VISION_MODEL` (blank = `OPENAI_MODEL`). The
  deployment's own env must set `DEFAULT_EVENT_DURATION_MINUTES` if it overrides the default.
- Tests default the duration to 0 in `service_factory` so the older ask-for-end-time tests are unchanged.
- Not covered by tests against the real API: the actual vision model's output quality. Needs a live
  check with real posters and screenshots (suggest 10 to 20 samples) before relying on it.

## Self-audit pass (13 October)

A review of everything on this branch, plus the flows it touches. Found and fixed:

| Finding | Fix |
| --- | --- |
| A `/free` or half-typed block question left open swallowed a later message ("Dinner fri 7-9pm" got a date error; a plan was glued onto a block) | Questions expire after 15 minutes (`PROMPT_TTL`), only short single-line replies count as answers, a non-answer is handled as an ordinary message, and the examples are shown only if it was not an event either. A block reply must be a time range, or the missing end time. |
| `/free next week` could exceed Telegram's 4096-character limit | At most 8 busy items per day and a 3900-character cap with a clear note. |
| A retried webhook delivery never marked its update finished | `Delivery.claimed`; the update is finished when a retry finally delivers the replies. |
| Sign-in limit keyed on the proxy hop: one bucket for every user | Keyed on the first forwarded hop; limit raised to 120/min. |
| A bare "night"/"evening" anywhere in a post made a bare hour pm ("Games Night Fri 7") | Only explicit phrases count (tonight, this evening, fri night, tmr morning, in the afternoon), and only for hours that fit the part. |
| Poll "has a schedule" looked only inside the poll window | Also counts a synced calendar or any busy-block rule. |
| NUSMods import and group `/event` extraction were not rate limited | Both limited. |
| Readiness made 15 sequential database calls | Run together; a total failure is reported as "unreachable", not as missing migrations. |
| One slow LLM call blocked every user (the lag seen in testing) | Per-user and per-chat ordering with up to 32 updates in parallel, in both polling and webhook modes. |
| Bare end hour was asked about even when obvious ("9" after a 7pm start) | Read as the first am/pm after the start; still asked when neither fits. |
| "ok thanks" during an edit became the venue | Small talk is never taken as a venue. |

Checked and left alone: calendar link fetching (host allowlist re-checked on every redirect, size cap),
Mini App auth (signature, age, token expiry), reminders (one per user per day), notification claiming
(atomic in the database), so concurrent updates are safe there.

A 360,000-call randomised run over the date, time, block and `/free` parsers raised no unexpected
exception.

### Still not proven (needs live accounts or the owner)

- Real vision-model output on real posters and screenshots.
- Real Telegram, OpenAI and hosted Supabase behaviour, including migrations `005`-`008`.
- Concurrency under real load. It is tested with simulated slow requests, not production traffic.
- Multi-replica deployments are still unsupported: rate limits, locks and reply progress are in-process.
