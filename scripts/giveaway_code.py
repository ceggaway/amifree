"""Check a /giveaway screenshot: prints the code the bot gives each handle.

    python scripts/giveaway_code.py @ben_lim @amy_tan

Run it with the bot's .env, so it uses the same secret as the live bot. If the code here
doesn't match the screenshot, the screenshot was edited or came from another account.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.services.giveaway import check_code


def main():
    handles = sys.argv[1:]
    if not handles:
        print(__doc__)
        return
    settings = Settings()
    secret = (
        settings.calendar_link_key.get_secret_value()
        or settings.telegram_bot_token.get_secret_value()
    )
    if not secret:
        print("No secret found. Run this from the project folder with the bot's .env.")
        return
    for handle in handles:
        print(f"@{handle.lstrip('@')}  {check_code(handle, secret)}")


if __name__ == "__main__":
    main()
