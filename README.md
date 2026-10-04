# AmIFree MVP

Forward a Telegram invitation, review its extracted details and Free/Clash verdict, then RSVP. Going events become AmIFree commitments and block future checks. The frontend is the private Telegram chat; FastAPI runs the backend and Supabase stores the data.

Built against the v2 product specification. Includes NUSMods classes/exams, structured event extraction, clarification, Edit, conservative confirmed updates/cancellations, RSVP, basic analytics and user deletion. Google Calendar, group scheduling, Supabase Auth, Mini Apps and OCR are outside the MVP.

## Local setup

Use Python 3.12. Docker and a local PostgreSQL server are not required.

```bash
cd amifree
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -c constraints.txt -r requirements.txt
```

Create `.env` from `.env.example` if it does not already exist. Preserve an existing configuration. On Windows activate with `.venv\Scripts\Activate.ps1`.

### Supabase

1. Create a Supabase project.
2. Run the complete `app/db/schema.sql` in its SQL Editor. It creates all five tables, indexes and transaction functions, enables row-level security, and grants access only to the server's service role. Reapplying it is safe for the current schema; future schema changes need versioned migrations.
3. Copy the project URL and backend `service_role` key into `.env`. Keep the key on the server; use neither the anon key nor a browser session. The SDK also accepts Supabase's newer backend secret key in the same configuration variable. See [Supabase API keys](https://supabase.com/docs/guides/getting-started/api-keys).

```dotenv
TELEGRAM_BOT_TOKEN=your_bot_token
OPENAI_API_KEY=your_openai_api_key
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_backend_key
BOT_MODE=polling
```

Create the Telegram bot through BotFather and obtain an OpenAI API key with model access and API billing configured. `OPENAI_MODEL` defaults to `gpt-4o-mini`; it must support structured outputs with the Responses API.

All reads/writes happen server-side through the async Supabase Python client. The server verifies Telegram identity and checks ownership on every event operation. The service key bypasses RLS, so these ownership checks are essential. No separate account system is used.

### Check connections and run

Run from the `amifree/` directory so `.env` loads correctly:

```bash
python scripts/check_connections.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The checker reads Supabase tables, Telegram bot identity, OpenAI model access and the NUSMods public API. It does not generate an LLM response or change your data. Startup additionally checks the schema's cleanup function, installs the bot's command menu, and starts polling. Startup stops clearly if credentials or database setup are missing.

Open `http://127.0.0.1:8000/healthz` for liveness and `/readyz` for bot readiness plus a live database check. A missing connection makes readiness return HTTP 503. This backend has no browser user interface; use the bot in Telegram.

For a backend-only smoke test before credentials are available:

```bash
BOT_MODE=disabled python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In disabled mode `/healthz` returns 200 and `/readyz` returns 503. This does not claim that the bot or database is connected.

## First real Telegram test

1. Open your bot's private chat and send `/start`.
2. Copy your student timetable's Share/Sync link from NUSMods and send it. Select all class types first. The bot reports academic year, semester and coverage.
3. Forward a message such as `CCA dinner Friday 7–9pm at Vivo`.
4. Review the date/time and verdict. Tap Going; the bot confirms the commitment.
5. Forward a second invitation overlapping that time. It should report Clash and offer Going anyway.
6. Tap Maybe or Can't make it on a commitment to release its block. Use Edit to correct details; changes require confirmation.
7. Forward `CCA dinner moved to 8pm` or `CCA dinner cancelled`. Review the proposed change before confirming. Ambiguous matches require selection.
8. Test a second Telegram account to verify isolation. Group chats receive only a prompt to open a private chat.

Commands: `/start`, `/help`, `/events`, `/cancel`, `/delete`. `/delete` requires a confirmation and cascades through that user's timetable, events, extraction records and analytics.

## Verification

```bash
python -m pip install -c constraints.txt -r requirements-dev.txt
python -m compileall -q app scripts tests
ruff check .
ruff format --check .
python -m pip check
python scripts/smoke_backend.py
pytest -q --cov=app --cov-report=term-missing
```

For all database tests, install the optional test-only engine using Node.js 22 and npm:

```bash
npm --prefix scripts/sql-tests ci --ignore-scripts --no-audit --no-fund
npm --prefix scripts/sql-tests test
pytest -q
```

[PGlite](https://pglite.dev/docs/) runs real PostgreSQL in WebAssembly. SQL tests execute the actual schema and functions, including RLS permissions, transactions, retries, deletion, seeds and analytics. A Python integration test connects the real Supabase repository/SDK to this engine through a small simulated PostgREST transport. That transport is test-only and covers the app's request subset; it does not prove a hosted Supabase connection. The integration test is explicitly skipped if Node/PGlite is absent; install the engine for a complete pass.

Telegram tests exercise real python-telegram-bot handlers and serialized Bot API requests with simulated network responses. OpenAI tests exercise the real SDK's strict schema parsing with simulated responses. The 14 representative message fixtures validate date/time handling; they are not evidence of live LLM extraction accuracy. See `docs/verification.md` and `docs/test-messages.md` for results and limits.

Direct Python dependencies are pinned; `constraints.txt` records tested transitive versions. Upgrade deliberately and rerun the full suite. Node's SQL engine is also pinned and has a lockfile.

## Deploy on Render

Publish the source to your own Git repository, then create a Python Web Service in Render. If the repository contains this folder alongside other projects, set Root Directory to `amifree`; if this folder is the repository root, leave Root Directory blank.

| Setting | Value |
| --- | --- |
| Python | 3.12 (set `PYTHON_VERSION=3.12.1`) |
| Build command | `pip install -c constraints.txt -r requirements.txt` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health check | `/readyz` |
| Environment | Four required credentials plus webhook settings below |

Set `BOT_MODE=webhook`, `APP_BASE_URL` to the service's HTTPS origin, and `TELEGRAM_WEBHOOK_SECRET` to a random value of 16–256 letters, digits, `_` or `-`. Generate a suitable value with:

```bash
python -c 'import secrets; print(secrets.token_hex(32))'
```

The server registers `/telegram/webhook` with Telegram on startup. Incoming requests must carry the matching `X-Telegram-Bot-Api-Secret-Token`. Never run polling and webhook instances for the same bot simultaneously. Stop the local polling process before deploying. Follow the [Render FastAPI deployment guide](https://render.com/docs/deploy-fastapi) for service creation and [port configuration](https://render.com/docs/web-services#port-binding).

Use one replica and one worker for this MVP. Webhook updates are serialized and polling processes one update at a time; database versions reject stale writes. Multiple replicas would require shared locking and a durable update queue. During deployment, stop the old instance before starting a second worker for the same bot. No deployment has been performed by this project setup.

## MVP assumptions

- All event times use Asia/Singapore. Numeric dates are day/month. Dates without a year use the next occurrence. A bare weekday means its next occurrence including today; `next Friday` means Friday in the next calendar week. Cards display the resolved date before RSVP.
- Forwarded invitations use the original Telegram message timestamp when available, otherwise the received message timestamp. Clarification retains that reference. Edits use the first correction message's timestamp and retain it for further clarification.
- Date/time evidence must appear in the source text or the existing edit baseline. Missing/ambiguous critical fields prompt clarification. Non-Singapore timezones, recurring events and multiple-event messages are flagged by the extractor; the live pilot must validate its handling of these cases.
- Missing end times prompt clarification by default. Setting `DEFAULT_EVENT_DURATION_MINUTES` to a positive value permits a visibly labelled assumption. Overnight events need an explicit end date. Existing updates preserve duration when no replacement end time is supplied.
- Overlap uses half-open intervals: an event ending at 19:00 does not clash with one starting at 19:00. Only Going events block availability. Conflicts are private.
- NUSMods links normally omit academic year. It is inferred from the message's Singapore timestamp, switching in August. Set `NUSMODS_ACADEMIC_YEAR=2026-2027` to override it for a specific pilot. The imported year is shown in the setup response.
- Student share-link formats v1/v2/v3 are supported. Missing/stale selections are rejected; the importer does not choose a class. Hidden courses are still imported. TA-specific links are rejected with an explanation.
- The importer expands listed teaching weeks into dated blocks and accounts for recess after teaching week 6. Special terms and explicit week ranges are supported. Exams require a published duration. Holiday exceptions, ad hoc cancellations and unscheduled classes are only as accurate as NUSMods' published lesson data. Resend a fresh link after timetable changes; reimport replaces old blocks atomically.
- Coverage spans the imported semester. Outside coverage the bot reports unknown timetable coverage rather than claiming Free; it still detects stored Going clashes.
- Event discovery includes all upcoming events plus events created in the last 30 days. `/events` has pages of 10 cards, with upcoming events first; candidate choices show at most 10 matches. A vague follow-up requires selection. A new clash appearing after an update preview requires another confirmation.
- Pending clarification/update/delete state survives restarts in Supabase. It expires after `PENDING_TTL_HOURS` (default 24). One pending action per user is supported; `/cancel` clears it.
- Optional raw extraction auditing is off (`RETAIN_EXTRACTIONS=false`). Pending state temporarily includes source text. With auditing enabled, records expire after `EXTRACTION_RETENTION_DAYS` (default 7). Cleanup runs at startup and hourly while the backend is running, and stale pending data is cleared when its user next interacts. If the backend is offline, cleanup resumes on restart.
- Users, structured events and aggregate action records remain until `/delete`. Provider-side Telegram/OpenAI/Supabase retention is separate. HTTP client logs are suppressed to avoid URLs containing bot tokens; application logs report error types and update IDs without raw messages or credentials.
- Analytics are best-effort and can miss activity during database failures. `scripts/analytics.sql` calculates onboarding, forwards per active user, correction rate, clashes, RSVP rate, server processing latency and week-two retention. Latency excludes Telegram delivery; ambiguous drafts do not have a final verdict until clarified. Legacy action rows without message IDs cannot be fully deduplicated.

## Calendar maintenance and development data

The bundled `app/data/academic-calendar.json` covers AY2024/25–2026/27 and comes from the [official NUSMods calendar](https://github.com/nusmodifications/nusmods/blob/master/packages/nusmods-academic-calendar/academic-calendar.json). Before importing another year, update it from that source, preserve `app/data/NUSMODS-LICENSE.txt`, and rerun importer tests. The bot rejects unsupported years rather than guessing semester dates.

`app/db/seed.sql` is optional for a separate development project. It replaces only synthetic users `999000001` and `999000002` and creates one fixed-date class and Going event for 9 October 2026. It is reproducible and not required for a real Telegram account. Do not seed a public pilot project. `scripts/analytics.sql` is read-only and returns aggregate counts.

## Module interfaces

- `NUSModsImporter.import_link(link, reference)` returns normalized `BusyBlock` records, academic year, semester and coverage.
- `EventExtractor.extract(text, reference, previous)` returns strict `Extraction` fields. Deterministic validation converts them to an aware `EventDraft` or a clarification prompt.
- `check_availability(candidate, timetable, events, ...)` returns Free, Clash or Unknown; it never calls an LLM.
- `SchedulingService` manages onboarding, pending state, event previews, versioned RSVP/update actions and Telegram replies. `SupabaseRepository` scopes all event operations to their owner and uses transaction functions for multi-row mutations.
- FastAPI provides `/healthz`, `/readyz` and the authenticated webhook endpoint. There is no public endpoint for reading a user's schedule.

### Webhook failure recovery

Failed processing or reply delivery returns HTTP 503 so Telegram can retry. The running worker retains reply progress and completed update IDs for its latest 1,000 handled updates. Retries resume unsent replies without repeating completed business operations; duplicate completed updates are acknowledged without additional replies. This cache is in memory and is lost on restart or eviction. Continue to run one worker. Existing database event/message uniqueness and event version checks remain the fallback replay guards; this is not durable exactly-once delivery. A reply accepted by Telegram before a network timeout may be delivered twice on retry.

Times with a colon and no AM/PM suffix are treated as 24-hour time, including mixed ranges such as `07:00–9pm`. Abbreviated ranges such as `7–9pm` still share the suffix.
