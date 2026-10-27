"""Send a one-time announcement DM to existing users. Dry run unless --send is given.

    python scripts/announce.py                     # who would get it, sends nothing
    python scripts/announce.py --to 123456789      # send only to this Telegram id (try it first)
    python scripts/announce.py --send              # send to everyone who hasn't blocked the bot

Goes to everyone who hasn't blocked the bot (or, with --active-days, only recent users).
People who never added their NUSMods timetable get an extra line inviting them to. Each send is recorded as a user action, so running it
again never messages anyone twice. Never prints Telegram ids or message content of users.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from supabase import acreate_client
from supabase.lib.client_options import AsyncClientOptions
from telegram import Bot
from telegram.error import BadRequest, Forbidden, RetryAfter

from app.config import Settings
from app.db.supabase import SupabaseRepository
from app.main import configure_logging

# Recorded per user once sent; a new announcement needs a new name.
NAME = "multi-time-2026-10"
ACTION = f"announcement:{NAME}"

TEXT = """✨ New in AmIFree

You can now send me a message with more than one time 👇

🗓 "Training Tue 7-9pm and Thu 6-8pm"
→ I'll list both. Tap Add both, or just the one you want

🤔 "Dinner Fri 7pm or Sat 6pm"
→ I'll show which time you're free for, you pick

🔁 "Badminton Tuesdays and Thursdays 7-9pm"
→ add just the next one, or every Tue & Thu

Everything you add goes to /plans. Connect Google or Apple Calendar in /calendar to see your plans there too 📅

Try it: forward any plan with a few times 👀"""

# For people who started the bot but never added their timetable.
SETUP_TIP = """

📚 Haven't added your classes yet? Send /start and paste your NUSMods link, so I can check your plans against your timetable too."""

# Telegram allows about 30 messages a second per bot; stay well under it.
PAUSE_SECONDS = 0.1


def eligible(row: dict, since: datetime | None) -> bool:
    """Whether this users row should get the announcement."""
    if row.get("dm_blocked"):
        return False
    if since is None:
        return True
    active = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
    return active >= since


def message_for(row: dict) -> str:
    return TEXT if row.get("onboarding_status") == "READY" else TEXT + SETUP_TIP


async def deliver(bot: Bot, telegram_id: int, text: str = TEXT) -> str:
    """Send once, waiting out a rate limit. Returns "sent", "blocked" or "failed"."""
    for _ in range(3):
        try:
            await bot.send_message(telegram_id, text)
            return "sent"
        except RetryAfter as exc:
            wait = exc.retry_after
            seconds = wait.total_seconds() if isinstance(wait, timedelta) else wait
            await asyncio.sleep(seconds + 1)
        except (Forbidden, BadRequest):
            return "blocked"
        except Exception:
            return "failed"
    return "failed"


async def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--send", action="store_true", help="actually send to everyone eligible")
    parser.add_argument("--to", type=int, help="send only to this Telegram user id")
    parser.add_argument(
        "--with-tip", action="store_true", help="with --to: include the NUSMods setup line"
    )
    parser.add_argument(
        "--active-days",
        type=int,
        default=0,
        help="only users active in the last N days (default 0: everyone)",
    )
    args = parser.parse_args(argv)

    settings = Settings()
    configure_logging("WARNING")
    async with httpx.AsyncClient(timeout=20) as http:
        supa = await acreate_client(
            settings.supabase_url,
            settings.supabase_service_role_key.get_secret_value(),
            options=AsyncClientOptions(httpx_client=http),
        )
        repo = SupabaseRepository(supa)
        async with Bot(settings.telegram_bot_token.get_secret_value()) as bot:
            if args.to:
                result = await deliver(bot, args.to, TEXT + (SETUP_TIP if args.with_tip else ""))
                print(f"Test message to the given id: {result}")
                return 0 if result == "sent" else 1

            since = (
                datetime.now(UTC) - timedelta(days=args.active_days) if args.active_days else None
            )
            users = await repo._all(lambda: supa.table("users").select("*").order("id"))
            done = {
                row["user_id"]
                for row in await repo._all(
                    lambda: (
                        supa.table("user_actions")
                        .select("user_id")
                        .eq("action_type", ACTION)
                        .order("id")
                    )
                )
            }
            targets = [u for u in users if eligible(u, since) and u["id"] not in done]
            tips = sum(message_for(u) != TEXT for u in targets)
            print(
                f"{len(users)} users, {len(done)} already got it, {len(targets)} to send"
                f" ({tips} with the NUSMods tip)"
                + (f" (active in the last {args.active_days} days)" if since else "")
            )
            if not args.send:
                print("Dry run: nothing sent. Add --send to send.")
                return 0

            counts = {"sent": 0, "blocked": 0, "failed": 0}
            for user in targets:
                result = await deliver(bot, user["telegram_user_id"], message_for(user))
                counts[result] += 1
                if result == "sent":
                    await repo.action(user["id"], ACTION)
                elif result == "blocked":
                    await repo.set_user_flags(user["id"], dm_blocked=True)
                await asyncio.sleep(PAUSE_SECONDS)
            print(
                f"Sent {counts['sent']}, blocked {counts['blocked']}, failed {counts['failed']}"
                + (" (run again to retry failed ones)" if counts["failed"] else "")
            )
            return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
