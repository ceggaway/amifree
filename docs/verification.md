# MVP verification report

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
