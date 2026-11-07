"""The public web pages: a home page and a privacy policy. They exist so the site says plainly
who runs it and what it does. A site that is only redirects to Google looks like phishing to
Google Safe Browsing."""

import html
from urllib.parse import quote

# Shared by every page, including the Google share page. Literal braces: not an f-string.
SITE_CSS = """
body{font:17px/1.55 -apple-system,system-ui,sans-serif;margin:0;background:#fff;color:#111}
main{max-width:560px;margin:auto;padding:28px 16px 40px}
.brand{font-weight:700;font-size:20px;margin:0 0 8px}
h1{font-size:26px;line-height:1.25;margin:8px 0 16px}
h2{font-size:19px;margin:28px 0 8px}
a{color:#0a66d6}
a.button{display:block;text-align:center;background:#0a7cff;color:#fff;padding:14px;
border-radius:12px;text-decoration:none;font-weight:600;margin:20px 0}
.small{font-size:14px;color:#555}
ul{padding-left:22px}
@media (prefers-color-scheme: dark){
body{background:#111;color:#eee}a{color:#5aa9ff}.small{color:#aaa}}
"""


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{SITE_CSS}</style></head><body><main>
<p class="brand">📅 AmIFree</p>
{body}
</main></body></html>"""


def _bot_link(bot_username: str) -> str:
    handle = bot_username.lstrip("@")
    return f"https://t.me/{quote(handle)}" if handle else ""


def home_page(bot_username: str = "") -> str:
    link = _bot_link(bot_username)
    open_bot = (
        f'<a class="button" href="{html.escape(link)}">Open AmIFree in Telegram</a>' if link else ""
    )
    return _page(
        "AmIFree · Am I free?",
        f"""<h1>Am I free? A Telegram bot for your plans</h1>
<p>AmIFree is a Telegram bot for students. Forward it a message about a plan and it tells you
whether you're free, saves it, and reminds you before it starts. It also keeps track of
deadlines, and in group chats it helps everyone find a time that works.</p>
{open_bot}
<h2>What it does</h2>
<ul>
<li>Checks a plan against your timetable and calendars</li>
<li>Reminds you about plans and deadlines</li>
<li>Finds a time that works for a whole group, without showing anyone's schedule</li>
</ul>
<h2>Your calendars</h2>
<p>You can connect Google Calendar, iPhone or Outlook so AmIFree checks those too. For Google
Calendar, you share your calendar with the bot in your own Google settings. AmIFree never asks
for your password.</p>
<p class="small"><a href="/privacy">Privacy policy</a></p>""",
    )


def privacy_page(bot_username: str = "") -> str:
    link = _bot_link(bot_username)
    bot = f'<a href="{html.escape(link)}">the bot</a>' if link else "the bot"
    return _page(
        "AmIFree · Privacy",
        f"""<h1>Privacy</h1>
<h2>What AmIFree keeps</h2>
<ul>
<li>Your Telegram ID and name, so it knows who it is talking to</li>
<li>The timetable, plans, deadlines and busy times you give it</li>
<li>Your answers in group cards (Going, Can't, availability), and your name in groups where
you tapped something, so the organiser can see who's going</li>
<li>Your reminder settings and basic usage counts</li>
</ul>
<h2>What it reads</h2>
<ul>
<li>Only messages you send or forward to it privately, commands, and button taps. It doesn't
read ordinary group chat messages unless a group admin turns that on.</li>
<li>Calendars you choose to connect, to check when you're busy. For Google Calendar, it can
also add the events you ask it to.</li>
<li>To understand a plan, the message, screenshot or voice note you send about it goes to an
AI service (OpenAI) for processing.</li>
</ul>
<h2>What it never does</h2>
<ul>
<li>Ask for your Google, Apple or Microsoft password</li>
<li>Show your schedule to a group. Groups only see counts.</li>
<li>Sell your data</li>
</ul>
<h2>Deleting your data</h2>
<p>Send /deletemydata to {bot} to delete everything AmIFree keeps about you. To stop sharing a
Google Calendar, remove the bot under <b>Shared with</b> in your Google Calendar settings.</p>
<h2>Questions</h2>
<p>Use /feedback in {bot} to reach the team.</p>
<p class="small"><a href="/">About AmIFree</a></p>""",
    )
