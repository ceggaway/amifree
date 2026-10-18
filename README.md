# AmIFree

Forward a Telegram plan, check the card, and tap Save. AmIFree tells you whether it clashes with what it knows about you, reminds you the night before, and tells you when a saved plan changes. In groups, organisers post event cards with RSVPs, a private "Am I free?" check and a headcount, and run `/whosfree` polls that fill themselves from members' schedules.

One FastAPI backend serves the Telegram bot, a JSON API and a React Telegram Mini App. Supabase stores the data. Clash detection is one deterministic Python implementation used by all three; no LLM decides availability.

| For students | For organisers |
| --- | --- |
| Forward to save, `/plans` | `/event` cards with Going / Maybe / Can't and a live headcount |
| Instant clash check: No clashes found, Clashes with X, or Busy day | Edit once: everyone who kept the plan gets one change alert |
| Private "Am I free?" on every group card, and `/free fri 7-9pm` or `/free tomorrow` | `/whosfree` self-filling poll |
| Night-before summary, change alerts | Auto-chase (two private nudges) and a Nudge button for non-responders |
| Quick blocks: `busy sat 2-6pm`, `work every Wed 6-10pm` | "Still coming?" check the day before, with a confirmed / unsure / dropped summary |
| Add to Google or Apple Calendar after Save or Going | Recurring events: `Training every Tue/Thu until week 13`, with single-session changes |
| "Still coming?" answered with one tap | Availability heatmap in the Mini App, attendance list, export and turnout stats |
| | Optional event detection, switched on by a group admin |

NUSMods is optional. After Save or Going, the bot offers 📅 Google Calendar (a pre-filled link) and 🍎 Apple Calendar (an .ics file, also works in Outlook). No calendar sign-in is needed; AmIFree does not read outside calendars, and cards say so. Change alerts offer the same buttons: the .ics replaces the old entry, a Google link adds a new one. OCR of posters is not supported.

**Status:** everything above is implemented and covered by the local test suite. None of the new features has been exercised against live Telegram, OpenAI or a hosted Supabase project. See `docs/verification.md`.

## Privacy by design

- The bot acts only on messages sent or forwarded to it in private, on commands, and on button taps. Telegram privacy mode stays on, so ordinary group messages are never delivered to it, unless a group admin makes the bot an admin and switches on `/detect on` (see below). `/event` reads only the one message it replies to.
- Anything derived from a person's schedule in a group is returned as a callback alert that only the person who tapped can see. Group cards show counts, never names or reasons. The one exception is **Who's going?**, which lists the names of people who tapped Going (never Maybe or Can't).
- Names (`first_name`, `@username`) are kept per group, only for people who tapped something there, so Who's going? and the organiser's attendance export can show them. They are deleted with `/deletemydata` and with the person's record.
- `/whosfree` reads a schedule only after that member taps Count me in, and suggests slots only once three people have joined.
- Nudges and reminders go only to people who have used AmIFree; `/reminders off` stops summaries and nudges.
- `/deletemydata` (alias `/delete`) removes the user's timetable, plans, busy blocks, poll answers, "still coming?" answers, check-ins, names, notification records and analytics. Group cards they organised stay for the other members, with no organiser attached.

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
2. Run the complete `app/db/schema.sql` in its SQL Editor, then each file in `app/db/migrations/` in numeric order (`001` to `008`). Together they create the tables, indexes and transaction functions, enable row-level security, and grant access only to the server's service role. Every file preserves existing rows and is safe to reapply. For a project that already holds data, take a backup first. Startup fails with a clear message if a migration is missing.
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
2. Forward a message such as `CCA dinner Friday 7–9pm at Vivo`. No timetable is needed.
3. Review the card and tap Save, then send `/plans`. Send `busy sat 2-6pm` and confirm the block.
4. Optionally send your NUSMods Share/Sync link (select all class types first). Tap Going on the plan; the bot confirms the commitment.
5. Forward a second invitation overlapping that time. It should report Clash and offer Going anyway.
6. Tap Maybe or Can't make it on a commitment to release its block. Use Edit to correct details; changes require confirmation.
7. Forward `CCA dinner moved to 8pm` or `CCA dinner cancelled`. Review the proposed change before confirming. Ambiguous matches require selection.
8. Test a second Telegram account to verify isolation.

Private commands: `/start`, `/help`, `/plans`, `/events`, `/block`, `/blocks`, `/reminders`, `/free`, `/calendar`, `/cancel`, `/deletemydata`. Deletion requires a confirmation.

### Groups

1. In BotFather leave **Group Privacy** turned on (the default). Add the bot to a test group.
2. Reply `/event` to a message containing a plan, or send `/event Dinner Fri 7-9pm at Vivo`. The bot posts a card.
3. With two accounts, tap **Am I free?** on the same card. Each sees a private popup; nothing appears in the group.
4. Tap Going / Maybe / Can't and watch the headcount. The plan appears in each member's `/plans`.
5. As the organiser tap **⚙️ Manage**. Telegram opens the private chat with Edit, Cancel event, Nudge members, Who's confirmed and Mark who came. Choose Edit, send the correction and confirm. Members who kept the plan each receive one message stating what changed.
6. Send `/whosfree project meeting 2h next week reply by Sun`. Have three accounts tap **Count me in**. Slots are suggested when the reply-by time passes; the organiser can also tap **Suggest slots** earlier.
7. Tap **Who's going?** for the Going names, and **I'm here** from 30 minutes before the start to check in.
8. A card is redrawn as "ended · N went" once the event is over (or "N said Going" when nobody checked in).

#### Group features

| Feature | How it works |
| --- | --- |
| **"Still coming?"** | 4–30 hours before an event starts, each member marked Going gets one private message: Still coming / Not sure / Can't anymore. Can't anymore also sets their RSVP to Can't. The organiser sees, under **⚙️ Manage → Who's confirmed**, "18 confirmed · 4 unsure · 3 dropped · 2 no reply". Members with `/reminders off` are not asked. |
| **Recurring events** | `/event Training every Tue/Thu 7-9pm at UTSRC until week 13` (also `until 30 Nov`, `for 8 weeks`). Every session is its own card, posted four days before it. Week numbers use the NUS calendar (recess between weeks 6 and 7). Editing a card changes that session only; the organiser is then offered **Apply to all future sessions** or **Cancel all future sessions**. **Going to all** RSVPs to every remaining session. A series is capped at 26 weeks / 60 sessions. |
| **Auto-chase** | Up to two private nudges per card: a day after posting (or halfway to the deadline, if sooner) and three hours before the deadline. A person is nudged at most once per stage. A poll's deadline is its reply-by time; a card's is its start. |
| **Heatmap** | The poll card's **📊 Who's free when** opens the Mini App grid of how many participants are free in each half hour. Counts only; shown to poll participants, and only once three schedules are known. Needs `BOT_USERNAME` and `MINI_APP_SHORT_NAME` (create the short name with `/newapp` in BotFather). |
| **Attendance, turnout and export** | `/attendance` (organiser) sends a private list of everyone who answered or checked in; tap a name to mark who came, or mark everyone who said Going. `/stats` sends turnout and a per-event breakdown, and `/export [days]` sends a CSV (name, RSVP, still coming?, attended), both privately. Turnout counts people who tapped **I'm here** or were ticked by the organiser. All free by default. |
| **Clubs Pro** | Switched off by default (`EXPORT_REQUIRES_PRO=false`). When on, `/stats` breakdown and `/export` need Pro, and `/pro` in a group sends the buyer a Telegram Stars invoice (`PRO_PRICE_STARS` for `PRO_DAYS`). Payments are recorded once per charge and extend the group's Pro time. `/refund` (private chat) returns the buyer's latest payment within `REFUND_WINDOW_DAYS` and removes the days it bought; a payment that can't be matched to a group is refunded automatically. You can also set a group Pro by hand with a `group_plans` row (`tier='PRO'`) or `PRO_CHAT_IDS`. |
| **Usual times** | `/usual tue evenings, sat all day` (also `weekdays after 6pm`, `mon-fri 6-9pm`, `/usual clear`) saves standing free times. Poll grids start from them, and in polls you are counted as free only inside them until you change that poll's grid. People with no schedule count as known once they set them. |
| **Set my own times** | The poll card's **✍️ Set my times** opens a grid pre-filled from your schedule or usual times, with busy times blocked (never explained). Tap, or drag across cells (turn on Paint mode on touch screens) to tick or untick many; then submit; the heatmap and suggested slots use your answer. People with no schedule tick the times they are free, and count as known once they do. |
| **Event detection** | Off by default. A group admin runs `/detect on` after making the bot a group admin (Telegram only delivers ordinary messages to admin bots). The bot then checks messages that contain a day and a time, reads them once, and offers **Make event card / Ignore**. Nothing is saved without a tap, message text is not stored, and unanswered suggestions delete themselves after 10 minutes. `/detect off` turns it off. |

Members must have opened a private chat with the bot at least once before it can DM them reminders, alerts or nudges. This is a Telegram rule.

### Mini App

```bash
cd web
npm ci
npm run build      # writes web/dist, which the backend serves at /app/
npm run dev        # browser-only UI work with mock data, proxied to a local backend
```

Set `MINI_APP_URL` to the HTTPS address of `/app/` on your deployed backend (for example `https://your-service.onrender.com/app/`). On startup the bot sets its menu button to **Open AmIFree**. Telegram only opens HTTPS Mini Apps, so local testing inside Telegram needs a tunnel or a staging deploy. The Mini App sends Telegram's signed `initData` to `POST /api/auth/telegram`; the server verifies it and returns a one-hour session token. A normal browser has no signature and cannot sign in. Development builds fall back to mock data, which is compiled out of production builds.

### Weekly events and posters

A post that repeats on one weekday ("Saturdays, 9:30am-11:30am", "every Tue 7-8pm") asks once: **Just the next one** (a normal card for the coming date), **Every Saturday** (a weekly busy block, previewed before saving) or **Neither**. If its end time is missing, the bot asks for that rather than for a date. Any forwarded message is read: text, a caption, a forwarded poll (question and options), and **pictures** (a photo, or an image sent as a file). A picture is read by the OpenAI vision model into plain text, which then goes through the same extractor and the same deterministic date/time checks as typed text, so a poster behaves exactly like its words would. The picture is not stored. `READ_PICTURES=false` turns this off, and `OPENAI_VISION_MODEL` picks a different model. A caption and the picture's text are combined; a picture with no event text, or one that can't be read, falls back to the caption or a short explanation. Pictures are limited to 10 per person per 5 minutes. Voice notes, videos without a caption and stickers are not read.

Quick messages work with no end time: `dinner 7pm today`, `dinner 7 tonight` (a day part such as tonight or this evening makes a bare 7 mean 7pm) or `breakfast 9 tmr morning` use `DEFAULT_EVENT_DURATION_MINUTES` (120 by default) and the card says the length is assumed. Set it to 0 to ask for the end time instead.

### Am I free? (`/free`)

`/free fri 7-9pm` checks that time against everything AmIFree knows; `/free fri 7pm` checks two hours. `/free tomorrow`, `/free 17th` or `/free this weekend` list free gaps of at least 30 minutes between 8am and 11pm; plain `/free` offers Today / Tomorrow / This weekend buttons. It is parsed with the same date/time rules as forwarded plans, with no LLM call, and asks "AM or PM?" when that is unclear.

Partial overlaps are still clashes, but every clash message says how much of the plan is lost: "it ends at 7:30pm, so you'd miss the first 30 min", "you'd need to leave at 8pm (the last 1 hr)", "from 7:30–8pm", or "(the whole time)". This applies to cards, `/free`, change alerts and the private group popup.

### Calendar sync (`/calendar`)

`/calendar` gives each user a private feed link, `APP_BASE_URL/cal/<secret>.ics`, with iPhone steps (Settings → Apps → Calendar → Calendar Accounts → Add Account → Other → Add Subscribed Calendar). Their Going and Saved plans then appear in a calendar called AmIFree, and edits and cancellations update on the calendar app's next refresh. Once the feed has been fetched by a calendar app (link previews don't count), cards say "Syncing to your calendar automatically" instead of showing the 🍎/📅 buttons. **New link** and **Turn off** invalidate the old link; `/deletemydata` removes it.

The feed needs a public HTTPS `APP_BASE_URL`, also in polling mode. For a local test, run a tunnel (`cloudflared tunnel --url http://localhost:8000`) and set `APP_BASE_URL` to its address; quick-tunnel addresses change on every run, so re-subscribe after restarting it. The secret is redacted from access logs. Without `APP_BASE_URL`, `/calendar` explains that sync isn't available yet.

### Personal calendar link (busy times)

Sending the bot a private iCal link (Google "Secret address in iCal format", an iCloud public calendar link, or an Outlook published ICS link) connects that calendar. Each link adds a calendar, up to 5 (for example separate iCloud calendars for classes, CCA and events); sending a connected link again just refreshes it. Calendars are labelled with their own name (`X-WR-CALNAME`, e.g. "CCA"), so clashes read "Clashes with CCA calendar". Its busy times then count in clash checks, Am I free? and `/whosfree`, and cards list "personal calendar" under Checked. `/calendar` lists each connected calendar with its status and a **Remove** button, and has step-by-step guides for finding links.

- **Busy times only.** `calendar_busy_blocks` has no title column; cards call them "Personal calendar". All-day, free (transparent) and cancelled events, and AmIFree's own feed, are ignored. Recurring events are expanded for the next 4 months.
- **The link is encrypted at rest** with `CALENDAR_LINK_KEY` (blank derives a key from the bot token). Changing the key makes stored links unreadable; those users are asked to send their link again.
- **Only Google, iCloud and Outlook hosts over HTTPS are fetched**, every redirect is re-checked, and responses are capped at 5 MB, so a pasted link can't make the server reach anything else.
- **Refreshed hourly** by the jobs tick, each calendar on its own. A revoked link clears only that calendar's busy times and `/calendar` marks it ⚠️; a temporary failure keeps the last good copy and retries an hour later. Migration `007` moves a link connected under `006` into the new `personal_calendars` table.

### Reminders and alerts

Night-before summaries, change alerts and nudges are rows in `notification_logs`, sent by an idempotent job. The server runs that job every minute while it is awake. Hosts that sleep or restart will miss runs, so also schedule it externally: set `JOBS_SECRET` and call it every ten minutes, for example from Render Cron:

```bash
curl -fsS -X POST -H "X-Jobs-Secret: $JOBS_SECRET" "$APP_BASE_URL/internal/jobs/run"
```

Summaries are sent from `NIGHTLY_HOUR` (default 20, Singapore time) until midnight. A message is marked sent only after Telegram accepts it; failures retry with backoff up to five times.

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

**Before every deploy that adds a migration, update the database first.** The app checks the
schema at startup and refuses to start when it is behind, which Render reports as a failed
deploy. Whoever runs the Supabase project copies [`app/db/all_migrations.sql`](app/db/all_migrations.sql)
into the Supabase SQL Editor and clicks Run. It holds the schema and every migration in order,
and all of it is safe to reapply, so it brings any database up to date. After adding a
migration, regenerate it with `python scripts/migrations_bundle.py > app/db/all_migrations.sql`
(a test fails if you forget). `python scripts/check_connections.py` confirms the database
from a machine with that project's credentials (read-only).

Publish the source to your own Git repository, then create a Python Web Service in Render. If the repository contains this folder alongside other projects, set Root Directory to `amifree`; if this folder is the repository root, leave Root Directory blank.

| Setting | Value |
| --- | --- |
| Python | 3.12 (set `PYTHON_VERSION=3.12.1`) |
| Build command | `pip install -c constraints.txt -r requirements.txt && npm --prefix web ci && npm --prefix web run build` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health check | `/readyz` |
| Environment | Four required credentials plus webhook settings below |

Set `BOT_MODE=webhook`, `APP_BASE_URL` to the service's HTTPS origin, and `TELEGRAM_WEBHOOK_SECRET` to a random value of 16–256 letters, digits, `_` or `-`. Generate a suitable value with:

```bash
python -c 'import secrets; print(secrets.token_hex(32))'
```

The server registers `/telegram/webhook` with Telegram on startup. Incoming requests must carry the matching `X-Telegram-Bot-Api-Secret-Token`. Never run polling and webhook instances for the same bot simultaneously. Stop the local polling process before deploying. Follow the [Render FastAPI deployment guide](https://render.com/docs/deploy-fastapi) for service creation and [port configuration](https://render.com/docs/web-services#port-binding).

Use one replica and one worker for this MVP. Within that worker, different people are handled at the same time (up to 32 updates), while each person's updates, and each chat's, are processed strictly in order (`app/bot/concurrency.py`); database versions reject stale writes. One slow extraction or picture read therefore delays only the person who sent it. Multiple replicas would require shared locking and a durable update queue. During deployment, stop the old instance before starting a second worker for the same bot. No deployment has been performed by this project setup.

## MVP assumptions

- All event times use Asia/Singapore. Numeric dates are day/month. Dates without a year use the next occurrence. A bare weekday means its next occurrence including today; `next Friday` means Friday in the next calendar week. Cards display the resolved date before RSVP.
- Forwarded invitations use the original Telegram message timestamp when available, otherwise the received message timestamp. Clarification retains that reference. Edits use the first correction message's timestamp and retain it for further clarification.
- Date/time evidence must appear in the source text or the existing edit baseline. Missing/ambiguous critical fields prompt clarification. Non-Singapore timezones and multiple-event messages are flagged by the extractor; weekly patterns in groups become a series (see Group features), and in private chat ask once between the next date and a weekly block; the live pilot must validate its handling of these cases.
- Missing end times prompt clarification by default. Setting `DEFAULT_EVENT_DURATION_MINUTES` to a positive value permits a visibly labelled assumption. Overnight events need an explicit end date. Existing updates preserve duration when no replacement end time is supplied.
- Overlap uses half-open intervals: an event ending at 19:00 does not clash with one starting at 19:00. Definite clashes are Going plans, NUSMods classes and exams, and busy blocks. A Saved plan is undecided: it is reported as a possible overlap with a choice of which to attend, and never blocks. "Busy day" is a warning, not a clash, shown when `BUSY_DAY_THRESHOLD` (default 3) other Going plans fall on the same day. Conflicts are private.
- "No clashes found" covers only what AmIFree knows: every card lists what was checked and says when no NUSMods timetable covers the date.
- Quick blocks are parsed without the LLM. Weekly blocks repeat for 90 days unless `until <date>` is given, cannot cross midnight, and are stored separately from NUSMods blocks so a reimport never removes them.
- A member's answer on a group card is their own plan linked to the shared card. Only the organiser can edit or cancel a shared plan. Change alerts are sent only for edits made through AmIFree: Telegram does not tell a bot when someone edits a message elsewhere.
- Bots cannot list group members. Headcounts, nudges and `/whosfree` only involve people who have interacted with AmIFree in that group. Each person is nudged at most once per chase stage (see Group features).
- NUSMods links normally omit academic year. It is inferred from the message's Singapore timestamp, switching in August. Set `NUSMODS_ACADEMIC_YEAR=2026-2027` to override it for a specific pilot. The imported year is shown in the setup response.
- Student share-link formats v1/v2/v3 are supported. Missing/stale selections are rejected; the importer does not choose a class. Hidden courses are still imported. TA-specific links are rejected with an explanation.
- The importer expands listed teaching weeks into dated blocks and accounts for recess after teaching week 6. Special terms and explicit week ranges are supported. Exams require a published duration. Holiday exceptions, ad hoc cancellations and unscheduled classes are only as accurate as NUSMods' published lesson data. Resend a fresh link after timetable changes; reimport replaces old blocks atomically.
- Coverage spans the imported semester. Outside coverage, or with no timetable imported, cards say that no NUSMods timetable covers the date; Going plans and busy blocks are still checked.
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
- `GroupEventService` and `PollService` handle group cards and `/whosfree`; they return a private alert and, separately, the public card to render. `Notifier` (in `app/services/jobs.py`) queues and delivers reminders, change alerts and nudges.
- FastAPI provides `/healthz`, `/readyz`, the authenticated webhook, `/internal/jobs/run` (shared secret) and `/api/*` (Telegram-signed session). Every `/api` route resolves the user on the server; a request without a valid session gets 401 and another user's IDs get 404. CORS is disabled unless `MINI_APP_DEV_ORIGIN` names one development origin.

### Webhook failure recovery

Failed processing or reply delivery returns HTTP 503 so Telegram can retry. The running worker retains reply progress and completed update IDs for its latest 1,000 handled updates. Retries resume unsent replies without repeating completed business operations; duplicate completed updates are acknowledged without additional replies. This cache is in memory and is lost on restart or eviction, so migration `008` also records each update in `processed_updates`: a retry of a finished update is acknowledged and not repeated even after a restart, a failure before anything was committed releases the claim so the retry runs, and an attempt that crashed is retaken after two minutes. Continue to run one worker. This is not durable exactly-once delivery: replies are not in an outbox. A reply accepted by Telegram before a network timeout may be delivered twice on retry.

Times with a colon and no AM/PM suffix are treated as 24-hour time, including mixed ranges such as `07:00–9pm`. Abbreviated ranges such as `7–9pm` still share the suffix.
