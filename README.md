# AmIFree

A Telegram assistant that tells NUS students whether they're free for a plan and helps organisers get a group to agree on a time. Built for CS3216 (Group 8) and live on Telegram as [@amifree_devv_bot](https://t.me/amifree_devv_bot).

Forward an invitation (text, poster or voice note), check the card, and tap Save. AmIFree checks it against your NUSMods timetable, connected calendars and saved plans, reminds you the night before, and tells you when a saved plan changes. In groups, organisers post event cards with RSVPs and a live headcount, or run `/whosfree` polls that fill themselves from members' schedules.

| For students | For organisers |
| --- | --- |
| Forward a plan → card with a clash check → Save | `/event` cards with Going / Can't and a live headcount |
| Private "Am I free?" on group cards; `/free fri 7-9pm` | `/whosfree` polls with an availability heatmap in the Mini App |
| Night-before summary, morning list, change alerts | Automatic nudges for non-responders and a "Still coming?" check |
| Deadlines (`/deadline`) and quick blocks (`busy sat 2-6pm`) | Recurring events (`Training every Tue/Thu until week 13`) |
| Sync to Google, Apple or any iCal calendar | Attendance check-in, turnout stats and CSV export |

Full behaviour of every command and feature: [`docs/features.md`](docs/features.md).

## How it works

One FastAPI backend serves the Telegram bot, a JSON API and a React Telegram Mini App; Supabase (PostgreSQL) stores the data.

- **AI reads, rules decide.** OpenAI structured output extracts the title, time and place from a message, picture or voice note. Dates are then resolved with fixed rules, every time must appear in the message itself, and anything unclear is asked back. Clash detection is one deterministic Python function (`app/services/availability.py`) shared by the bot and the Mini App; no LLM decides availability.
- **Shared events are stored once.** A group event lives in `group_events`; each member's RSVP is their own `events` row linked to it, so one edit updates everyone's plan and sends one change alert.
- **Reliable delivery.** Reminders and alerts go through a `notification_logs` outbox with dedupe keys and retries, and are marked sent only after Telegram accepts them. Edits carry a version number, so stale writes are rejected.

| Path | What's there |
| --- | --- |
| `app/bot/` | Telegram handlers, onboarding and calendar guides |
| `app/services/` | Extraction, date resolution, availability, group events, polls, calendars, jobs |
| `app/api/` | Mini App API and Telegram `initData` sign-in |
| `app/db/` | `schema.sql`, numbered migrations and the `all_migrations.sql` bundle |
| `web/` | React + TypeScript Mini App |
| `tests/`, `scripts/sql-tests/` | Python tests and SQL tests on real PostgreSQL (PGlite) |
| `evals/` | Extraction accuracy evals |
| `docs/` | [Feature reference](docs/features.md), [group features](docs/group-features.md), [verification log](docs/verification.md), [design specs](docs/design/) |

## Privacy

- The bot only sees messages sent or forwarded to it, commands and button taps. Telegram privacy mode stays on, so it never reads group chats unless a group admin switches on `/detect on`, and even then nothing is saved without a tap.
- Schedule-based answers in groups are private pop-ups. Group cards show counts, never reasons; members opt in before their schedule counts in a `/whosfree` poll.
- Connected calendars are read for busy times only, never titles or places. Calendar links are encrypted at rest.
- `/deletemydata` removes a user's timetable, plans, blocks, poll answers, names, notifications and analytics. Cards they organised stay for other members, with no organiser attached.

## Run it locally

Needs Python 3.12, and Node.js 22 for the Mini App. No Docker or local database is needed.

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -c constraints.txt -r requirements.txt
cp .env.example .env   # then fill in the four keys below
```

1. Create a Supabase project and run `app/db/all_migrations.sql` in its SQL Editor (schema plus every migration; safe to rerun).
2. In `.env`, set `TELEGRAM_BOT_TOKEN` (from BotFather), `OPENAI_API_KEY`, `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`. Keep the service key on the server. Every other setting is optional and explained in `.env.example`.
3. Check the connections and start the bot in polling mode:

```bash
python scripts/check_connections.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then send `/start` to your bot and forward it a plan such as `CCA dinner Friday 7–9pm at Vivo`. `/healthz` and `/readyz` report liveness and database readiness.

Mini App:

```bash
cd web && npm ci && npm run build   # served by the backend at /app/; npm run dev uses mock data
```

Telegram opens Mini Apps only over HTTPS, so set `MINI_APP_URL` to a deployed or tunnelled `/app/` address. Group app buttons also need `BOT_USERNAME` and `MINI_APP_SHORT_NAME` (from `/newapp` in BotFather).

## Tests

```bash
python -m pip install -c constraints.txt -r requirements-dev.txt
ruff check . && ruff format --check .
pytest -q
npm --prefix scripts/sql-tests ci && npm --prefix scripts/sql-tests test
npm --prefix web test
```

CI (`.github/workflows/ci.yml`) runs lint, backend tests, SQL tests, frontend tests and production builds on every push. Tests use simulated Telegram, OpenAI and Supabase responses; [`docs/verification.md`](docs/verification.md) records what they do and don't cover.

## Deploy (Render)

Create a Python web service with:

| Setting | Value |
| --- | --- |
| Build | `pip install -c constraints.txt -r requirements.txt && npm --prefix web ci && npm --prefix web run build` |
| Start | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health check | `/readyz` |
| Environment | `PYTHON_VERSION=3.12.1`, the four keys, `BOT_MODE=webhook`, `APP_BASE_URL` (the service's HTTPS URL), `TELEGRAM_WEBHOOK_SECRET` (random, 16–256 chars) |

- **Update the database before deploying a new migration.** The app refuses to start if the schema is behind. Rerun `all_migrations.sql`; after adding a migration, regenerate it with `python scripts/migrations_bundle.py > app/db/all_migrations.sql` (a test checks this).
- **Run one worker and one replica.** Updates are processed in order per person and per chat. Never run polling and webhook instances of the same bot at once.
- **Schedule reminders externally**, since free hosts sleep: set `JOBS_SECRET` and call `POST $APP_BASE_URL/internal/jobs/run` with header `X-Jobs-Secret` every 10 minutes (e.g. Render Cron).
- Optional: `GOOGLE_SERVICE_ACCOUNT` enables Google Calendar sharing; see [`docs/features.md`](docs/features.md#share-a-google-calendar-adds-plans-for-you).

## Credits

Built by CS3216 Group 8, NUS, 2026. Timetables come from the [NUSMods API](https://api.nusmods.com/); the academic calendar in `app/data/` is from NUSMods (licence in `app/data/NUSMODS-LICENSE.txt`).
