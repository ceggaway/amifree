# Verification report

## Audit fixes: 13 October 2026

Local checks only, on `main` plus uncommitted changes, using Python 3.12.1 and Node.js 24.8.0. **Not run against live Telegram, OpenAI or a hosted Supabase project**, and migrations `005`–`008` have not been applied to any hosted database. CI (`.github/workflows/ci.yml`) is added but has not yet run on GitHub.

| Layer | Result | Evidence and limits |
| --- | --- | --- |
| Python tests | **438 passed** | Includes the PGlite database integration test (not skipped) |
| Application coverage | **93% statements** | `pytest --cov=app` |
| PostgreSQL schema and migrations | **24 checks passed** | `npm --prefix scripts/sql-tests test` |
| Lint, format | Pass | `ruff check`, `ruff format` |
| Backend smoke | Pass | `scripts/smoke_backend.py` |
| Mini App tests and build | **11 passed**; build passes | `vitest run`, `tsc --noEmit`, `vite build` |

Changed in this pass: readiness now checks the calendar tables, columns and function; `/api/me` reports real calendar state; `/whosfree` excludes members with no schedule from "no known clash" counts; sign-in, extraction and calendar-link fetches are rate limited; due calendars are selected in the database.

## Must-haves and Mini App: 9 October 2026

Local checks only, on branch `feat/must-haves`, using Python 3.12.1 and Node.js 24.8.0. **No feature in this section has been run against live Telegram, OpenAI or a hosted Supabase project**, and migrations `001`–`004` have not been applied to any hosted database.

| Layer | Result | Evidence and limits |
| --- | --- | --- |
| Python syntax, lint, format, dependencies | Pass | `compileall`, `ruff check`, `ruff format --check`, `pip check` |
| Python tests | **158 passed, no skips** | Baseline before this work was 103 |
| Application coverage | **93% statements** | Clash engine and Mini App auth at 100% |
| PostgreSQL schema and migrations | **21 checks passed** | `schema.sql` plus all migrations applied twice in PGlite, over existing rows |
| Mini App build | Pass | `tsc --noEmit` and `vite build`; served by a real Uvicorn process at `/app/` |
| Group behaviour | Pass locally | Real python-telegram-bot handlers with simulated Bot API responses |
| Live Telegram, OpenAI, Supabase | **Not run** | Needs migrations applied and a test group |

Behaviour covered by tests:

- A new user without NUSMods forwards a plan, saves it and sees it in `/plans`; Going turns it into a commitment.
- Going overlap clashes; back-to-back does not; a Saved overlap is reported as possible and offers a choice without deleting either plan; a busy day warns without clashing.
- `work every Wed 6-10pm` clashes on Wednesday, not Tuesday, respects its end date, and survives a NUSMods reimport in real PostgreSQL.
- Two members tapping the same card get different private alerts; no message is posted or edited into the group for an availability check; plain group messages produce no reply and no extraction.
- Headcount follows RSVPs and shows counts only. A card's buttons do nothing in another chat.
- An organiser edit from 7pm to 8pm alerts members who are Going, Maybe or Saved exactly once with the before and after times and a recomputed clash check; the decliner, the organiser and outsiders get nothing. Non-organisers and stale versions are refused.
- Two plans tomorrow produce one nightly message; rerunning the job sends nothing; a failed send is retried once it recovers; a blocked user is skipped and flagged; opt-out is respected.
- `/whosfree` refuses to suggest slots below three opt-ins or for a non-organiser, picks the slots where most participants are free, and exposes only counts.
- Nudges reach only silent known members, once per card, and skip people who answered in the meantime.
- Mini App sign-in rejects forged, tampered, expired, future-dated and wrong-bot `initData`; every API route returns 401 without a valid session; another user's plan or block ID returns 404; stale versions return 409; plans saved in the bot appear in the API and edits made through the API appear in the bot.
- Deleting a user removes their plans, blocks, poll answers and notification rows, and leaves shared cards for other members.

Known limits, not defects found by testing:

- The simulated PostgREST transport covers only the request shapes the app uses. Hosted Supabase behaviour (RPC null handling, row limits) needs a live check.
- Whether `/event` sent as a reply is delivered with the replied-to message under privacy mode, and whether Telegram accepts the callback `url` redirect used by Edit, are taken from Telegram's documentation and are untested live.
- A worker crash between sending a notification and recording it can repeat that one message after ten minutes.
- The React app has type checking but no component tests, and has not been opened inside Telegram.
- There is no rate limiting on `/api/auth/telegram`.

Live acceptance still to do: apply `app/db/migrations/001`–`004` in order to the dev Supabase project; set `MINI_APP_URL` and `JOBS_SECRET`; run in polling mode; walk the README's private, group and Mini App flows with two or three accounts.

## Original MVP report: 4 October 2026

Verified on **4 October 2026 (Asia/Singapore)** using Python 3.12.1 and Node.js 22.19.0. This report covers the completed implementation and local checks. Live Telegram, OpenAI and hosted Supabase acceptance remains pending credentials.

## Results

| Layer | Result | Evidence and limits |
| --- | --- | --- |
| Python syntax | Pass | `python -m compileall -q app scripts tests` |
| Static checks | Pass | `ruff check .` and `ruff format --check .` |
| Dependencies | Pass | `python -m pip check`; tested direct versions pinned and transitive versions recorded in `constraints.txt` |
| Python tests | **95 passed, no skips** | Includes domain, SDK transport, Telegram, backend lifecycle and SQL integration tests |
| Application coverage | **91% statements** | Availability engine is 100%; coverage does not prove all possible input or provider behavior |
| PostgreSQL schema | **18 checks passed** | Actual SQL executed in PGlite's PostgreSQL engine; includes schema reapplication, RLS permissions, transactions, seeds and aggregate analytics |
| Telegram frontend | Pass locally | Real PTB handlers and serialized message/button requests; Telegram Bot API responses simulated |
| Backend lifecycle | Pass locally | Polling and webhook startup/shutdown exercised with simulated upstream APIs; real Uvicorn HTTP smoke test also passed |
| Database adapter | Pass locally | Real async Supabase SDK/repository connected to real PostgreSQL through a test-only simulated PostgREST transport |
| NUSMods importer | Pass with live public API | AY2026/27 CS3216 lecture selection imported 13 dated blocks and skipped recess; no credentials required |
| Representative messages | **14 fixture cases passed** | Real validation/date resolution with fixture extraction fields; live LLM extraction quality not yet measured |
| Live Telegram/OpenAI/Supabase | Pending | Credentials intentionally deferred by the user |
| Hosted deployment | Pending | Render instructions supplied; no service deployed |

The real HTTP smoke test verified `/healthz` returns 200 and `/readyz` returns 503 in credential-free disabled mode, followed by graceful shutdown. The 503 is expected: the server does not claim a connection without a running bot and database.

## Behavior verified

- New user → setup → preview → Going → second overlapping invitation returns Clash.
- Timetable/exam and Going conflicts; Maybe/Declined/Cancelled/Preview do not block; back-to-back intervals do not clash.
- Source message timestamp for forwarded invitations; correction message timestamp for edits; Singapore normalization and academic-year rollover.
- Missing, ambiguous and invalid dates/times trigger clarification. Draft state survives a service restart. Expired pending confirmations cannot change an event.
- User isolation in service, repository and SQL functions. Group chats cannot expose private event details.
- Duplicate messages preserve event identity; stale RSVP callbacks cannot overwrite newer state.
- Edits and cancellations require confirmation, preserve event identity and retain Going status where appropriate. New conflicts appearing after an update preview require another confirmation.
- Friendly user errors for timeout/invalid parsing/database failures; application logs omit exception strings that could contain credentials or private text. Configuration validation also hides input values.
- Webhook secret validation, malformed/non-ASCII headers, invalid updates and oversized bodies.
- Timetable import rollback, atomic RSVP/action logging, retention cleanup, user deletion cascades, and reproducible development fixtures.

## Reproduce

From `amifree/`, activate `.venv` and install the pinned development dependencies and optional SQL engine as documented in README. Then run:

```bash
python -m compileall -q app scripts tests
ruff check .
ruff format --check .
python -m pip check
pytest -q --cov=app --cov-report=term-missing
npm --prefix scripts/sql-tests test
python scripts/smoke_backend.py
```

The database integration test skips explicitly if Node/PGlite is unavailable. The recorded complete run installed it and had no skips. The test gateway implements the application's PostgREST request subset; provider routing, deployed RLS settings, network access and credentials still require hosted Supabase verification.

## Live acceptance remaining

1. Initialize the user's fresh Supabase project using `app/db/schema.sql`.
2. Configure the four required credentials in `.env` and run `python scripts/check_connections.py`.
3. Run the bot in polling mode. Confirm `/readyz` returns 200 with a working database.
4. Complete the README's real Telegram flow with the user's NUSMods timetable, including a second account for isolation and correction/update paths.
5. Evaluate actual LLM extraction on representative real invitations, measuring corrections and processing latency.
6. If deployment is desired, configure the single-worker Render webhook service and verify webhook delivery and secret validation in that environment.

The implementation is ready for these steps. It has not been marked as fully connected or live-validated.
