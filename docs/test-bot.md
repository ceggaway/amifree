# Local test bot

Use a separate Telegram bot and Supabase project to test changes without affecting
the live bot. Initialize the test database with `app/db/all_migrations.sql` in the
test project's Supabase SQL Editor.

Save these settings in `.env.test` (this file is excluded from Git):

```dotenv
BOT_MODE=polling
TELEGRAM_BOT_TOKEN=your_separate_test_bot_token
SUPABASE_URL=https://your-test-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_test_project_backend_key
OPENAI_API_KEY=your_openai_api_key
```

After installing dependencies as described in the README, start the full test bot:

```bash
.venv/bin/python scripts/run_test_bot.py
```

The script rejects a bot token or database URL matching `.env`, and a bot with an
existing webhook. It serves the backend on `http://127.0.0.1:8001`. The Mini App
requires separate HTTPS configuration; this command does not expose it publicly.

Open the test bot in Telegram and send `/start`. After code changes, stop the local
process with Ctrl+C and run the command again. Existing chat messages do not update
automatically; repeat the action to see a new message. An account with a connected
calendar skips the welcome screen; use the original welcome's Calendar button and
then Back to revisit it without removing data.

Keep the computer awake while testing. Test plans persist in the test database.
Never commit `.env`, `.env.test`, or any API keys.
