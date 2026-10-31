"""First-run setup: the welcome screen and step-by-step guides for finding schedule links.

Every screen edits the same message, so setup never floods the chat (ux-flows.md, /start A).
"""

import base64
import html
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import quote

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

NUSMODS_URL = "https://nusmods.com/timetable"
# academic-calendar.json's semester keys -> NUSMods' timetable pages.
NUSMODS_PAGES = {"1": "sem-1", "2": "sem-2", "3": "st-i", "4": "st-ii"}


def nusmods_url(today: date | None = None) -> str:
    """The timetable page of the semester running now (or starting within two weeks), so
    nobody has to pick it; the plain timetable page when the calendar doesn't know."""
    from app.schemas.event import SG
    from app.services.nusmods import CALENDAR

    today = today or datetime.now(SG).date()
    best = None
    for year in CALENDAR.values():
        for key, sem in year.items():
            start = date(*sem["start"])
            if start - timedelta(days=14) <= today and (best is None or start > best[0]):
                best = (start, key)
    if best is None or best[1] not in NUSMODS_PAGES:
        return NUSMODS_URL
    return f"{NUSMODS_URL}/{NUSMODS_PAGES[best[1]]}"


EVERYDAY_HELP = (
    "📋 <b>My plans</b> · /plans — see upcoming events and deadlines you’ve saved with me.\n\n"
    "📌 <b>Deadlines</b> · /deadline — save or forward a due date and get reminders before it’s due. "
    "Try “Report due Fri 11:59pm”.\n\n"
    "💬 <b>Feedback</b> · /feedback — If you run into any issues or have suggestions, send us feedback using /feedback. You can send it anonymously or with your @handle"
)

# Short on purpose: one thing to do (try it), and the privacy promise next to the buttons
# that ask for a calendar. Everything else is in /help or shows up when it's relevant.
WELCOME = (
    "👋 <b>Hi! I’m AmIFree.</b> I check if you’re free for plans people send you, and save "
    "them for you.\n\n"
    "📩 <b>Try it:</b> forward me any message with a plan, send a 📸 photo or 🎙 voice note, "
    "or tap <b>Try an example</b> to see what I do.\n\n"
    "🔒 I only see when you’re busy, never your event names or details. I never read your "
    "group chats.\n\n"
    "💡 Everything else I can do: /help"
)

# The example: a friend's message, and the card it would get against a made-up timetable.
# People copy it and send it, so it spells out pm: a bare "12 to 1:30" is asked about.
DEMO_MESSAGE = "Lunch at Fine Food on Wed, 12pm to 1:30pm? 🍜"
DEMO_OUTRO = (
    "<i>This example uses a made-up timetable, and nothing was saved.</i>\n\n"
    "👉 Connect your calendar so I can catch your real clashes, or forward me a message of "
    "your own."
)
# The second example, a deadline. Sent as is, it saves the same deadline (test_onboarding).
DEADLINE_DEMO_MESSAGE = "CS3244 report due Fri 11:59pm"
DEADLINE_DEMO_OUTRO = (
    "⏰ I’ll remind you at 9am the day before, and on the morning it’s due.\n\n"
    "📅 With your calendar connected (Google or Apple), it shows up there too, as "
    "“Due: CS3244 report”.\n\n"
    "<i>This is an example, and nothing was saved.</i>"
)

PICK_CALENDAR = (
    "📅 Where do you usually add your events?\n\n"
    "I'll put your plans there by themselves, warn you about clashes, and help your groups "
    "find a time you're free.\n\n"
    "🎓 At NTU? Export your timetable from ntumods.com as a calendar file (.ics) and import "
    "it into the calendar you connect here, so I can check your classes too.\n\n"
    "I only see when you're busy, never what your events are called, where they are "
    "or who's going."
)

# Device-neutral on purpose: not everyone deciding is on an iPhone.
NOT_SURE = (
    "🤔 Not sure which one you use?\n\n"
    "📱 Android phone: it's almost always Google Calendar.\n\n"
    "🍎 iPhone: open the Calendar app and tap Calendars at the bottom.\n"
    "• Calendars under iCloud → Apple\n"
    "• Calendars under Gmail → Google\n\n"
    "💻 Computer: if you check your calendar at calendar.google.com, it's Google.\n\n"
    "📧 Calendars under Exchange or Outlook (often a school or work email) → Outlook."
)

# Asked after the first plan is kept, at 3 plans and at 6 (CALENDAR_ASK_AT in scheduling),
# counting the multi-time picker's setup buttons too.
CALENDAR_PROMPT = (
    "📅 Want plans like this on your calendar automatically?\n"
    "I'll also warn you about clashes and help your groups find a free time."
)
# The same ask when nothing is connected yet, so it says what that plan wasn't checked against.
CALENDAR_PROMPT_UNCHECKED = (
    "📅 I could only check this against plans you've saved with me. Connect your calendar "
    "and I'll catch clashes with your classes and other events too, and add your plans "
    "there automatically."
)
# Added to the third and final ask, so stopping is said, not silent.
LAST_CALENDAR_ASK = (
    "This is the last time I'll ask. You can connect a calendar any time in /calendar, "
    "and until then I'll still check new plans for clashes with the ones you've added here."
)
PROMPT_LATER = (
    "No problem. Tap 🍎 or 📅 on a plan to add it yourself, or set this up any time with /calendar."
)

# Every calendar guide ends the same way, so nobody wonders what to do after the last step.
PASTE_NEXT = (
    "\n\n👇 <b>Paste the link here</b> when you have it.\n"
    "Stuck? Setup is optional — forward me a plan any time."
)

# Guides are sent as HTML so the words to look for on screen are bold.
GUIDES = {
    "export": (
        "📆 Classes in your own calendar\n\n"
        "NUSMods can export your timetable to your calendar app itself: open your timetable "
        "on nusmods.com and look for its Export option next to Share/Sync.\n\n"
        "AmIFree's calendar sync is for your plans, not your classes, so I don't copy classes "
        "there. To have me check your classes, paste your NUSMods Share/Sync link instead."
    ),
    "nusmods": (
        "📚 <b>Connect your NUSMods timetable</b>\n"
        "I’ll check for clashes with your classes and exams.\n\n"
        "<b>1. Open your timetable</b>\n"
        "Tap <b>Open NUSMods</b> below. It opens this semester. Check that all your "
        "lectures, tutorials and labs are there.\n\n"
        "<b>2. Copy the link</b>\n"
        "Tap <b>Share/Sync</b>, then <b>Copy link</b>.\n\n"
        "<b>3. Send it here</b>\n"
        "Paste the link into this chat and send it.\n\n"
        "📱 Timetable looks empty? NUSMods keeps it in the browser you made it in. Open "
        "nusmods.com there instead (often Safari or Chrome).\n\n"
        "Timetable changed? Send me the updated link."
    ),
    "ios": (
        "🍎 <b>iPhone · iCloud Calendar</b> · about 1 minute\n"
        "Do this on your iPhone.\n\n"
        "Calendar listed under <b>Gmail</b>? Tap <b>📅 Mine's Google</b> below "
        "to switch to the Google setup guide.\n\n"
        "1. Open the <b>Calendar</b> app.\n"
        "2. Tap the calendar icon at the bottom right.\n"
        "3. Under <b>iCloud</b>, tap the <b>ⓘ</b> next to your calendar.\n"
        "4. Scroll down, turn on <b>Public Calendar</b>.\n"
        "5. Tap <b>Share Link…</b>, then <b>Copy</b>.\n\n"
        "✅ You'll know it's right: it starts with <code>webcal://</code>.\n\n"
        "⚠️ This only works for iCloud calendars. A calendar under Gmail, or with no Public "
        "Calendar switch, is from Google or Outlook — use that guide instead.\n\n"
        "🔒 Anyone with the link can see that calendar, including event names, so only paste "
        "it here. I keep just your busy times.\n\n"
        "🗂 Several iCloud calendars (Classes, CCA, Events)? Add each one's link." + PASTE_NEXT
    ),
    "mac": (
        "💻 <b>Mac · iCloud Calendar</b> · about 1 minute\n"
        "Do this on your Mac. (iPad? Use the iPhone steps.)\n\n"
        "Calendar listed under <b>Gmail</b>? Tap <b>📅 Mine's Google</b> below "
        "to switch to the Google setup guide.\n\n"
        "1. Open the <b>Calendar</b> app.\n"
        "2. In the left list, under <b>iCloud</b>, find your calendar.\n"
        "    <i>No list? Click the calendar icon up top.</i>\n"
        "3. Hover over its name, click the <b>person icon</b>.\n"
        "4. Tick <b>Public Calendar</b> — a link appears.\n"
        "5. Click the <b>share button</b> by the link, then <b>Copy</b>, then <b>Done</b>.\n\n"
        "✅ You'll know it's right: it starts with <code>webcal://</code>.\n\n"
        "⚠️ This only works for iCloud calendars. A calendar under Gmail, or with no Public "
        "Calendar box, is from Google or Outlook — use that guide instead.\n\n"
        "🔒 Anyone with the link can see that calendar, including event names, so only paste "
        "it here. I keep just your busy times.\n\n"
        "🗂 Several iCloud calendars? Add each one's link." + PASTE_NEXT
    ),
    "google": (
        "📅 <b>Google Calendar · read only</b> · about 1 minute\n"
        "Do this on a computer, or in your phone's browser in desktop mode.\n\n"
        "1. Go to <b>calendar.google.com</b> and sign in.\n"
        "2. Click <b>⚙️</b> → <b>Settings</b>.\n"
        "3. Under <b>Settings for my calendars</b>, select your calendar.\n"
        "4. Open <b>Integrate calendar</b> and copy <b>Secret address in iCal format</b>.\n\n"
        "✅ You'll know it's right: the link ends in <code>.ics</code>.\n"
        "⚠️ Not the Public address. No secret address on a school or work account? "
        "Your admin may have disabled it. Try a personal calendar.\n\n"
        "I'll only read your busy times, without adding or changing events.\n"
        "Several calendars? Send each calendar's link separately.\n\n"
        "🔒 Keep the link private — anyone with it can see your calendar. If it leaks, "
        "click <b>Reset</b> in Google and send me the new link." + PASTE_NEXT
    ),
}

OUTLOOK_NO_FEED = (
    "Adding plans to Outlook by themselves isn't available yet, so tap 📅 on a plan to add "
    "it. This part lets me check when you're busy."
)
OUTLOOK_PART1_DONE = (
    "✅ <b>Part 1 done</b>: Outlook has subscribed, so your plans will appear in your AmIFree "
    "calendar there."
)

APPLE_PART1 = (
    "🍎 <b>Apple Calendar · Part 1 of 2</b>\n"
    "<b>Add your plans to your calendar automatically</b>\n\n"
    "Every event you save with me will show up in your calendar by itself, and update if it "
    "changes. No more adding them by hand.\n\n"
    "<b>1.</b> Tap <b>Add to Calendar</b> below, then tap <b>Subscribe</b>. A calendar called "
    "<b>AmIFree</b> appears.\n\n"
    "<b>2.</b> Make changes show up faster:\n"
    "• iPhone: Settings → Apps → Calendar → Calendar Accounts → Fetch New Data → "
    "<b>Every 15 Minutes</b> (the fastest an iPhone allows)\n"
    "• Mac: in Calendar, right-click AmIFree → Get Info → Auto-refresh → "
    "<b>Every 5 minutes</b>\n\n"
    "I'll message you here once your calendar has subscribed, then we'll do part 2."
)
APPLE_ALREADY_GOOGLE = (
    "\n\nℹ️ Your plans already go to your Google Calendar. If that's the calendar on your "
    "iPhone, you don't need this part: they'd show up twice."
)
APPLE_NO_FEED = (
    "🍎 <b>Apple Calendar</b>\n\n"
    "Adding plans to your calendar by themselves isn't available yet, so tap 🍎 on a plan to "
    "add it. This part lets me check when you're busy."
)
APPLE_PART1_DONE = "✅ <b>Part 1 done</b>: your plans will appear in your AmIFree calendar."
APPLE_PART2 = "🍎 <b>Apple Calendar · Part 2 of 2</b>\n<b>Let me check when you're busy</b>\n\n"
APPLE_SKIPPED = (
    "OK, skipped. Your plans still go to your AmIFree calendar, but I won't know when you're "
    "busy with other things. Finish it any time in /calendar."
)
# Plain text, so it fits both HTML and plain messages.
FEED_REFRESH_TIP = (
    "⏱ Want changes to show up faster? Set how often your calendar checks for them:\n"
    "• Mac: in Calendar, right-click AmIFree → Get Info → Auto-refresh → Every 5 minutes\n"
    "• iPhone: Settings → Apps → Calendar → Calendar Accounts → Fetch New Data → Every 15 "
    "Minutes (the fastest an iPhone allows)"
)
APPLE_ALL_SET = (
    "✅ <b>All set</b>\n"
    "• Your plans appear in your AmIFree calendar\n"
    "• I'll warn you about clashes and include you when your groups look for a free time"
)


# Apple part 2, one step per message, each with the picture of that screen
# (app/assets/ios). "Mine's Google" shows on the step whose text mentions Gmail.
APPLE_STEPS = {
    "ios": (
        (
            "ios_step1.png",
            "Open the <b>Calendar</b> app and tap the <b>calendar icon</b> at the bottom "
            "(circled in red).",
        ),
        (
            "ios_step2.png",
            "Under <b>iCloud</b>, tap the <b>ⓘ</b> next to the calendar you use.\n\n"
            "Only see <b>Gmail</b>, no iCloud? Your calendar is Google: tap "
            "<b>📅 Mine's Google</b>.",
        ),
        (
            "ios_step3.png",
            "Scroll down and turn on <b>Public Calendar</b>.\n\n"
            "🔒 Anyone with this calendar's link can see its events, so only paste the link "
            "here. I keep just your busy times.",
        ),
        (
            "ios_step4.png",
            "Tap <b>Share Link…</b>, then <b>Copy</b>.\n\n"
            "👇 <b>Paste the link here.</b> It starts with <code>webcal://</code>.\n"
            "Several iCloud calendars (Classes, CCA)? Send each one's link.",
        ),
    ),
    "mac": (
        (
            "mac_step1.png",
            "In the <b>Calendar</b> app, find your calendar under <b>iCloud</b> on the left. "
            "Hover over it, click the <b>person icon</b>, then tick <b>Public Calendar</b>.\n\n"
            "No list? Click the calendar icon at the top. Only see <b>Gmail</b>? Tap "
            "<b>📅 Mine's Google</b>.",
        ),
        (
            "mac_step2.png",
            "Click the <b>share button</b> next to the link, then <b>Copy</b>, then "
            "<b>Done</b>.\n\n"
            "👇 <b>Paste the link here.</b> It starts with <code>webcal://</code>.\n"
            "🔒 Anyone with the link can see that calendar, so only paste it here. I keep just "
            "your busy times.",
        ),
    ),
}
GOOGLE_HINT = {("ios", 2), ("mac", 1)}


def apple_step(
    device: str = "ios", n: int = 1, lead: str = ""
) -> tuple[str, bytes | None, InlineKeyboardMarkup]:
    """One step of Apple part 2: its text, its picture (None if it can't be read), buttons."""
    steps = APPLE_STEPS[device]
    picture, body = steps[n - 1]
    where = "On a Mac · " if device == "mac" else ""
    text = (
        (lead + "\n\n" if lead else "")
        + APPLE_PART2
        + f"<b>{where}Step {n} of {len(steps)}</b>\n"
        + body
    )
    try:
        photo = (Path(__file__).resolve().parent.parent / "assets" / "ios" / picture).read_bytes()
    except OSError:
        photo = None
    nav = []
    if n > 1:
        nav.append(InlineKeyboardButton("‹ Back", callback_data=f"a2:{device}:{n - 1}"))
    if n < len(steps):
        nav.append(InlineKeyboardButton("Next ›", callback_data=f"a2:{device}:{n + 1}"))
    rows = [nav] if nav else []
    if n == 1:
        rows.append(
            [_button("💻 On a Mac instead?", "apple2mac")]
            if device == "ios"
            else [_button("📱 On an iPhone instead?", "apple2")]
        )
    if (device, n) in GOOGLE_HINT:
        rows.append([_button("📅 Mine's Google", "google")])
    rows.append([_button("⏭ Skip this part", "appleskip")])
    return text, photo, InlineKeyboardMarkup(rows)


PLAN_ACTIONS = (
    "When the event card appears, tap <b>Add to plans</b> to save it, or <b>Skip</b> "
    "if you don’t want to save it."
)

SKIPPED = (
    "<b>Send me your first plan</b>\n\n"
    "Forward an event message or photo, send a voice note, or type the details.\n\n"
    "I’ll check for clashes with plans you’ve saved with me. Your timetable and personal "
    "calendar events won’t be included until you connect them through /calendar.\n\n" + PLAN_ACTIONS
)

SEND_PLAN = (
    "✅ <b>You’re ready to send a plan</b>\n\n"
    "Forward an event message or photo, send a voice note, or type the details. I’ll check for clashes with "
    "your connected calendar and plans you’ve saved with me.\n\n" + PLAN_ACTIONS + "\n\n"
    "You can add NUSMods later with /calendar if your classes are missing."
)

CLASSES_QUESTION = (
    "<b>Are your classes included in this calendar?</b>\n\n"
    "Tap <b>Add NUSMods</b> if your classes are missing. Tap <b>Continue</b> if they’re "
    "already included or you don’t have classes to add.\n\n"
    "🎓 At NTU? Export your timetable from ntumods.com as a calendar file (.ics), import it "
    "into this calendar, then tap <b>Continue</b>."
)

SCHEDULE_READY = (
    "✅ <b>You’re ready to send a plan</b>\n\n"
    "Forward an event message or photo, send a voice note, or type the details. I’ll check your connected "
    "calendar, timetable and saved plans for clashes.\n\n" + PLAN_ACTIONS
)

NOT_A_SCHEDULE_LINK = (
    "That's not a NUSMods or personal calendar link. Pick yours below and I'll show you "
    "where to find it."
)


TRY_IT = (
    "Try it: forward me any plan, like\n"
    '"Dinner Fri 7-9pm at Vivo"\n'
    "and I'll tell you straight away if you're free.\n\n"
    "Or send /free tomorrow to see your free time."
)


def _your(label: str) -> str:
    """ "CCA" -> "your CCA calendar"; "iPhone calendar" -> "your iPhone calendar"."""
    return "your " + (label if label.lower().endswith("calendar") else f"{label} calendar")


def calendar_added(
    label: str, busy_count: int, *, timetable_connected: bool = False, sync_enabled: bool = False
) -> str:
    text = (
        "✅ <b>Personal calendar connected</b>\n"
        "I can now check for clashes with events in this calendar."
    )
    if not busy_count:
        text += (
            f"\n\nI don’t see any events in {html.escape(_your(label))} for the next few months. "
            "If you use another calendar for your plans, add it through /calendar."
        )
    if sync_enabled:
        text += "\n\nEvents you save with me will automatically sync to this calendar."
    if timetable_connected:
        return text + "\n\n" + SCHEDULE_READY
    return text + "\n\n" + CLASSES_QUESTION


def another_calendar_added(label: str, total: int) -> str:
    return (
        f"✅ Added {_your(label)}. I'm now checking {total} calendars.\n\n"
        "Send another link to add more, or /calendar to see them all."
    )


def nusmods_added(academic_year: str, semester: int, *, personal_connected: bool = True) -> str:
    sources = (
        "your personal calendar events" if personal_connected else "plans you’ve saved with me"
    )
    return (
        "✅ <b>Timetable connected</b>\n"
        f"I can now check for clashes with your classes and exams, alongside {sources}.\n\n"
        "<b>Send me your first plan</b>\n"
        "Forward an event message or photo, send a voice note, or type the details.\n\n"
        + PLAN_ACTIONS
    )


def _menu(label: str, name: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=f"menu:{name}")


def _button(label: str, screen: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=f"setup:{screen}")


def welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [_button("▶️ Try an example", "demo"), _button("📅 Connect calendar", "calendar")],
        ]
    )


def demo_screen(today: date) -> tuple[str, InlineKeyboardMarkup]:
    """The example card for the coming Wednesday, clashing with a made-up lecture.

    Built from the real card parts so it looks like what people will get, but it's never
    stored: no event, no plan, no buttons that act on one."""
    from app.bot.messages import personal_availability, personal_details
    from app.models.timetable import BusyBlock
    from app.schemas.event import SG, EventDraft
    from app.services.availability import Availability

    day = today + timedelta(days=(2 - today.weekday()) % 7 or 7)

    def at(hour: int, minute: int = 0) -> datetime:
        return datetime.combine(day, time(hour, minute), SG)

    plan = EventDraft(title="Lunch", start_at=at(12), end_at=at(13, 30), location="Fine Food")
    lecture = BusyBlock(title="CS1231S Lecture", start_at=at(10), end_at=at(12, 30))
    card = personal_availability(Availability(conflicts=(lecture,), candidate=plan))
    card += "\n\n" + personal_details(plan)
    text = (
        "▶️ <b>Example</b>\n\nSay a friend sends you this:\n"
        f"<blockquote>{html.escape(DEMO_MESSAGE)}</blockquote>\n\n"
        f"I’d reply:\n\n{card}\n\n{DEMO_OUTRO}"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [_button("📌 Next: a deadline", "demo2")],
            [_button("📅 Connect calendar", "calendar")],
            [_button("📩 Try my own message", "skip"), _button("« Back", "home")],
        ]
    )
    return text, keyboard


def deadline_demo_screen(now: datetime) -> tuple[str, InlineKeyboardMarkup]:
    """The second example: a deadline due the coming Friday, as its card would show it."""
    from app.schemas.event import SG
    from app.services.deadlines import when

    today = now.astimezone(SG).date()
    day = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
    due = datetime.combine(day, time(23, 59), SG)
    text = (
        "📌 <b>Example 2: a deadline</b>\n\nSay your prof posts:\n"
        f"<blockquote>{html.escape(DEADLINE_DEMO_MESSAGE)}</blockquote>\n\n"
        f"I’d save it:\n\n📌 <b>CS3244 report</b>\nDue {when(due, now)}\n\n" + DEADLINE_DEMO_OUTRO
    )
    keyboard = InlineKeyboardMarkup(
        [
            [_button("📅 Connect calendar", "calendar")],
            [_button("📩 Try my own message", "skip"), _button("« Back", "demo")],
        ]
    )
    return text, keyboard


def classes_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[_button("📚 Add NUSMods", "nusmods")], [_button("Continue", "sendplan")]]
    )


def link_help_keyboard() -> InlineKeyboardMarkup:
    """After a link that didn't work: one tap to the right guide, no retyping /start."""
    return InlineKeyboardMarkup(
        [
            [_button("🍎 iPhone / iCloud", "ios"), _button("📅 Google", "google")],
            [_button("📧 Outlook", "outlook"), _button("📚 NUSMods", "nusmods")],
        ]
    )


def try_it_keyboard() -> InlineKeyboardMarkup:
    """After the first schedule is connected: one tap to see it working."""
    return InlineKeyboardMarkup([[_menu("🕒 Am I free?", "free")]])


def pick_calendar_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [_button("📅 Google Calendar", "google"), _button("🍎 Apple / iCloud", "apple")],
            [_button("🤔 I'm not sure", "notsure")],
            [_button("📚 NUSMods timetable", "nusmodspick")],
            [_button("📧 Outlook", "outlook"), _button("« Back", "home")],
        ]
    )


def not_sure_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [_button("📅 Google Calendar", "google"), _button("🍎 Apple / iCloud", "apple")],
            [_button("📧 Outlook", "outlook"), _button("« Back", "calendar")],
        ]
    )


def calendar_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                _button("Set up (1 min)", "calendar"),
                InlineKeyboardButton("Not now", callback_data="calp:later"),
            ]
        ]
    )


def calendar_setup_keyboard() -> InlineKeyboardMarkup:
    """Under plans that go nowhere else yet: the same Apple and Google setup as /calendar."""
    return InlineKeyboardMarkup(
        [[_button("🍎 Apple Calendar", "apple"), _button("📅 Google Calendar", "google")]]
    )


OUTLOOK_PARTS = {
    1: "📧 <b>Outlook · Part 1 of 2</b>\n<b>Add your plans to Outlook automatically</b>\n\n",
    2: "📧 <b>Outlook · Part 2 of 2</b>\n<b>Let me check when you're busy</b>\n\n",
}
# One short step per screen, most with a marked-up picture. {url} is the feed link.
OUTLOOK_STEPS = {
    1: (
        "Every plan you save with me will show up in an <b>AmIFree</b> calendar in Outlook, "
        "and update if it changes.\n\n"
        "💻 Best on a <b>computer</b> (Telegram Desktop or <b>web.telegram.org</b>): the "
        "Outlook phone app can't add it.\n\n"
        "👇 <b>Tap your Outlook below.</b> It opens with your AmIFree calendar filled in. "
        "Sign in if asked, then click <b>Import</b>.\n\n"
        "⏳ Outlook checks for new plans every few hours, so they can take a while to show "
        "up there. Your reminders here are on time either way.",
        "Button didn't work? Add it by hand:\n\n"
        "Open <b>outlook.office.com</b> (school or work email) or <b>outlook.live.com</b> "
        "(personal email). Click the <b>calendar icon</b>, then <b>Add calendar</b> → "
        "<b>Subscribe from web</b>.\n\n"
        "Paste your AmIFree link (click it to copy):\n<code>{url}</code>\n\n"
        "Name it <b>AmIFree</b>, then click <b>Import</b>.\n\n"
        "🔒 Keep the link private: anyone with it can see your plans.",
    ),
    2: (
        "👇 <b>Tap your Outlook below.</b> It opens <b>Shared calendars</b> in Outlook's "
        "settings. Sign in if asked.\n\n"
        "Under <b>Publish a calendar</b>, pick your calendar. Next to it, choose <b>Can view "
        "when I'm busy</b>. That shares only when you're busy, never titles or places, and "
        "it's all I need.\n\n"
        "Button didn't open it? In Outlook on the web: <b>Settings ⚙️</b> → <b>Calendar</b> → "
        "<b>Shared calendars</b>.\n\n"
        "⚠️ No <b>Publish a calendar</b> section? Your school or company has turned it off, so "
        "I can't read that calendar yet.",
        "Click <b>Publish</b>. Two links appear: <b>HTML</b> and <b>ICS</b>.\n\n"
        "Click the <b>ICS</b> link, then <b>Copy link</b>.",
        "👇 <b>Paste the link into this chat.</b>\n\n"
        "✅ You'll know it's right: it ends in <code>.ics</code>.\n\n"
        "🗂 Several calendars (Class, CCA)? Repeat steps 1 to 3 for each one and send each "
        "link.\n\n"
        "Stuck? Setup is optional — forward me a plan any time.",
    ),
}


# (part, step) -> its picture in assets/outlook. Pasting the link into this chat has none.
OUTLOOK_PICTURES = {
    (1, 1): "subscribe_import.png",
    (1, 2): "add_by_hand.png",
    (2, 1): "publish_busy.png",
    (2, 2): "publish_ics.png",
}


def outlook_step(
    part: int, n: int = 1, url: str = "", lead: str = "", part1: bool = True
) -> tuple[str, bytes | None, InlineKeyboardMarkup]:
    """One step of Outlook setup: text, picture (None if none or unreadable), buttons.
    part1=False when there's no feed link, so no part 1."""
    steps = OUTLOOK_STEPS[part]
    photo = None
    if (part, n) in OUTLOOK_PICTURES:
        try:
            photo = (
                Path(__file__).resolve().parent.parent
                / "assets"
                / "outlook"
                / OUTLOOK_PICTURES[part, n]
            ).read_bytes()
        except OSError:
            photo = None
    body = steps[n - 1].replace("{url}", html.escape(url))
    title = (
        OUTLOOK_PARTS[part]
        if part1
        else "📧 <b>Outlook</b>\n<b>Let me check when you're busy</b>\n\n"
    )
    text = (lead + "\n\n" if lead else "") + title + f"<b>Step {n} of {len(steps)}</b>\n" + body
    nav = []
    if n > 1:
        nav.append(InlineKeyboardButton("‹ Back", callback_data=f"o:{part}:{n - 1}"))
    elif part == 2 and part1:
        nav.append(InlineKeyboardButton("‹ Part 1", callback_data="o:1:1"))
    if part == 1 and n == 1:
        rows = outlook_subscribe_rows(url)
        rows.append(
            [
                InlineKeyboardButton("Button didn't work?", callback_data="o:1:2"),
                InlineKeyboardButton("Done: part 2 ›", callback_data="o:2:1"),
            ]
        )
    else:
        if n < len(steps):
            nav.append(InlineKeyboardButton("Next ›", callback_data=f"o:{part}:{n + 1}"))
        elif part == 1:
            nav.append(InlineKeyboardButton("Done: part 2 ›", callback_data="o:2:1"))
        # Part 2 starts with one tap to Shared calendars, like part 1's subscribe buttons.
        rows = [*outlook_shared_rows(), nav] if (part, n) == (2, 1) else [nav]
    return text, photo, InlineKeyboardMarkup([*rows, [_button("« Other calendars", "calendar")]])


# Outlook on the web's own "Subscribe from web" screen, with the link and name filled in.
# (The older owa/?path=...&rru=addsubscription link fails with an error for NUS accounts.)
OUTLOOK_SUBSCRIBE = "https://outlook.{host}.com/calendar/0/addfromweb?url={url}&name=AmIFree"


# Outlook on the web's Settings → Calendar → Shared calendars, where Publish a calendar is.
OUTLOOK_SHARED = "https://outlook.{host}.com/calendar/options/calendar/SharedCalendars"


def outlook_shared_rows() -> list[list[InlineKeyboardButton]]:
    return [
        [
            InlineKeyboardButton(
                "🏫 School / work Outlook ↗", url=OUTLOOK_SHARED.format(host="office")
            )
        ],
        [InlineKeyboardButton("📧 Personal Outlook ↗", url=OUTLOOK_SHARED.format(host="live"))],
    ]


def outlook_subscribe_rows(feed_url: str) -> list[list[InlineKeyboardButton]]:
    """One-tap subscribe: school or work email is office.com, a personal one is live.com."""
    webcal = quote("webcal://" + feed_url.split("://", 1)[-1], safe="")
    return [
        [
            InlineKeyboardButton(
                "🏫 School / work Outlook",
                url=OUTLOOK_SUBSCRIBE.format(host="office", url=webcal),
            )
        ],
        [
            InlineKeyboardButton(
                "📧 Personal Outlook", url=OUTLOOK_SUBSCRIBE.format(host="live", url=webcal)
            )
        ],
    ]


def apple_part1_keyboard(add_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add to Calendar ↗", url=add_url)],
            [InlineKeyboardButton("Didn't work? Show the manual steps", callback_data="cal:feed")],
            [_button("⏭ Skip to part 2", "apple2")],
            [_button("« Other calendars", "calendar")],
        ]
    )


def first_calendar_keyboard(finish_apple: bool = False, feed: bool = False) -> InlineKeyboardMarkup:
    """Offer calendar setup and NUSMods before connecting. Half-way through Apple setup, the
    Apple button picks up where they stopped instead of sitting beside a second one."""
    from app.bot.keyboards import calendar_home_rows

    finish = _button("🍎 Finish Apple setup", "apple2")
    rows = [
        [
            finish if finish_apple and b.callback_data == "setup:apple" else b
            for b in row
            # /calendar has its own "Add or update NUSMods" row below.
            if b.callback_data not in ("setup:home", "setup:nusmodspick")
        ]
        for row in pick_calendar_keyboard().inline_keyboard
    ]
    return InlineKeyboardMarkup([*rows, *calendar_home_rows(feed)[1:]])


PHOTOS_CAPTION = "Swipe through the pictures: the red marks show where to tap."

# Guide name -> (folder under app/assets, pictures in order).
SCREEN_PHOTOS = {
    "ios": ("ios", ("ios_step1.png", "ios_step2.png", "ios_step3.png", "ios_step4.png")),
    "mac": ("ios", ("mac_step1.png", "mac_step2.png")),
}


def screen_photos(name: str) -> tuple[bytes, ...]:
    """The step pictures for one setup guide. Empty if there are none or they can't be read."""
    if name not in SCREEN_PHOTOS:
        return ()
    folder, files = SCREEN_PHOTOS[name]
    base = Path(__file__).resolve().parent.parent / "assets" / folder
    try:
        return tuple((base / f).read_bytes() for f in files)
    except OSError:
        return ()


# Google step 1 (sharing), one small part per message, each with the picture of that
# screen: (picture, short title, what to do). {email} is AmIFree's Google account, in <code>
# so a tap copies it.
# Sharing from the Google Calendar phone app, one picture per step (assets/google_phone).
# On a computer the email comes first instead (google_share_screen).
GOOGLE_STEPS = (
    (
        "gphone_step1.png",
        "Open the menu",
        "Open the <b>Google Calendar</b> app on your phone and tap <b>☰</b> (top left).",
    ),
    ("gphone_step2.png", "Open Settings", "Scroll down and tap <b>Settings</b>."),
    (
        "gphone_step3.png",
        "Choose your calendar",
        "Tap your calendar (usually <b>My calendar</b>), then tap <b>Shared with</b>.",
    ),
    (
        "gphone_step4.png",
        "Add AmIFree",
        "Tap <b>Add people or groups</b> and paste this email (tap it to copy):\n\n"
        "<code>{email}</code>",
    ),
    (
        "gphone_step5.png",
        "Let AmIFree add your plans",
        "Tap the permission under AmIFree and choose\n"
        "<b>Make changes (see private events as free/busy)</b>\n\n"
        "Then tap <b>Save</b> (top right).",
    ),
)
# Sharing is the long part, so it alone is counted ("1 of 5"); the email and code that
# follow say "Almost done" and "Last step" instead of a second count.
GOOGLE_STEP1 = "📅 <b>Google Calendar · Share your calendar with me</b>\n\n"


def google_step(email: str, n: int = 1) -> tuple[str, bytes | None, InlineKeyboardMarkup]:
    """One part of sharing on a phone: its text, its picture (None if it can't be read), buttons."""
    picture, title, body = GOOGLE_STEPS[n - 1]
    text = (
        GOOGLE_STEP1
        + f"<b>{n} of {len(GOOGLE_STEPS)} · {title}</b>\n"
        + body.format(email=html.escape(email))
    )
    if n == len(GOOGLE_STEPS):
        text += (
            "\n\nThen tap <b>Done</b> below.\n\n"
            "🔒 I only read your busy times, and only change events I add."
        )
    try:
        photo = (
            Path(__file__).resolve().parent.parent / "assets" / "google_phone" / picture
        ).read_bytes()
    except OSError:
        photo = None
    nav = []
    if n > 1:
        nav.append(InlineKeyboardButton("‹ Back", callback_data=f"gs:phone:{n - 1}"))
    if n < len(GOOGLE_STEPS):
        nav.append(InlineKeyboardButton("Next ›", callback_data=f"gs:phone:{n + 1}"))
    rows = [nav] if nav else []
    if n == len(GOOGLE_STEPS):
        rows.insert(0, [InlineKeyboardButton("✅ Done, I've shared it", callback_data="gcal:done")])
    if n == 1:
        # Telegram buttons only take web links, so this is Google's address, which phones
        # hand to the app when it's installed.
        rows.append(
            [InlineKeyboardButton("📅 Open Google Calendar app ↗", url=GOOGLE_CALENDAR_URL)]
        )
        rows.append(
            [InlineKeyboardButton("💻 On a computer instead?", callback_data="gcal:computer")]
        )
        rows.append([_button("« Other calendars", "calendar")])
    return text, photo, InlineKeyboardMarkup(rows)


GOOGLE_CALENDAR_URL = "https://calendar.google.com"
GOOGLE_EMAIL_ASK = (
    "📅 <b>Google Calendar · Almost done</b>\n<b>What's your Google email?</b>\n\n"
    "The one that owns the calendar you just shared. Just type it here, like "
    "<code>you@gmail.com</code>.\n\n/cancel to stop."
)
GOOGLE_EMAIL_AGAIN = (
    "That doesn't look like an email. Type the Google email your calendar is on, like "
    "you@gmail.com. /cancel to stop."
)
# On a computer the email comes first, so the next step can open that calendar's own
# settings page: no gear, no Settings, no picking the calendar.
GOOGLE_EMAIL_FIRST = (
    "📅 <b>Google Calendar</b>\n<b>What's your Google email?</b>\n\n"
    "The account your calendar is on. Just type it here, like <code>you@gmail.com</code>, "
    "and I'll open that calendar's sharing settings for you.\n\n/cancel to stop."
)
GOOGLE_SHARING_BLOCKED = (
    "\n\nSome school and work accounts block sharing with outside accounts. If that's yours, "
    "use a link instead: I'll check when you're busy, but I can't add your plans."
)


def google_email_first_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📱 On my phone instead?", callback_data="gcal:start")],
            [_button("« Other calendars", "calendar")],
        ]
    )


def google_share_screen(
    bot_email: str, email: str
) -> tuple[str, bytes | None, InlineKeyboardMarkup]:
    """Computer step 2 of 3: one screen, with a button straight to that calendar's settings."""
    text = (
        "📅 <b>Google Calendar · Share your calendar with me</b>\n\n"
        "<b>1.</b> Tap <b>Open sharing settings</b> below. It opens the calendar for "
        f"{html.escape(email)}.\n"
        "<b>2.</b> Scroll to <b>Shared with</b> and click <b>Add people and groups</b>.\n"
        "<b>3.</b> Paste this email (tap it to copy):\n"
        f"<code>{html.escape(bot_email)}</code>\n"
        "<b>4.</b> Permission: <b>Make changes (see private events as free/busy)</b>, then "
        "click <b>Send</b>.\n\n"
        "Then tap <b>Done</b> below.\n\n"
        "🔒 I only read your busy times, and only change events I add."
    )
    try:
        photo = (
            Path(__file__).resolve().parent.parent / "assets" / "google" / "step5_dialog.png"
        ).read_bytes()
    except OSError:
        photo = None
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📅 Open sharing settings ↗", url=google_share_link(email))],
            [InlineKeyboardButton("✅ Done, I've shared it", callback_data="gcal:shared")],
            [InlineKeyboardButton("✏️ Different email", callback_data="gcal:computer")],
            [_button("« Other calendars", "calendar")],
        ]
    )
    return text, photo, keyboard


def google_code_ask(email: str) -> str:
    return (
        "📅 <b>Google Calendar · Last step</b>\n<b>Show me it's yours</b>\n\n"
        f'I\'ve put an event called "AmIFree code" in the calendar for {html.escape(email)} '
        "today. Open Google Calendar and send me the 6-digit number in it. I'll delete the "
        "event after.\n\n/cancel to stop."
    )


GOOGLE_SETTINGS = "https://calendar.google.com/calendar/r/settings"


def google_calendar_settings(email: str) -> str:
    """The settings page of one Google calendar, where Shared with is. A main calendar's id is
    its owner's email, and Google puts the id in the address base64-encoded."""
    calendar = base64.b64encode(email.encode()).decode().rstrip("=")
    return f"{GOOGLE_SETTINGS}/calendar/{calendar}"


def google_share_link(email: str) -> str:
    """That calendar's settings, opened in the right account when the browser has several."""
    return f"{google_calendar_settings(email)}?authuser={quote(email, safe='@')}"


def unlink_guide(
    *,
    bot_email: str | None = None,
    shared_email: str | None = None,
    google_link: bool = False,
    icloud: bool = False,
    outlook: bool = False,
    feed: bool = False,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """What the user still has to undo in their own calendar apps once AmIFree lets go, one
    section per kind they connected. AmIFree can't do these itself: only the owner can."""
    sections, rows = [], []
    if shared_email and bot_email:
        sections.append(
            "📅 <b>Google Calendar you shared with me</b>\n"
            "I still have access until you remove me, and only you can do that. Tap "
            "<b>Open sharing settings</b> below, find <b>Shared with</b>, and tap ✕ next to\n"
            f"<code>{html.escape(bot_email)}</code>\n"
            "(In the Google Calendar app: ☰ → Settings → your calendar → Shared with.)\n"
            "Events I added stay in your calendar. Delete them there if you don't want them."
        )
        rows.append(
            [
                InlineKeyboardButton(
                    "📅 Open sharing settings ↗", url=google_calendar_settings(shared_email)
                )
            ]
        )
    if google_link:
        sections.append(
            "📅 <b>Google secret link</b>\n"
            "I've deleted it. So nobody else can use it, reset it: <b>Open Google Calendar "
            "settings</b> below → your calendar → <b>Integrate calendar</b> → <b>Secret address "
            "in iCal format</b> → <b>Reset</b>."
        )
        rows.append(
            [InlineKeyboardButton("📅 Open Google Calendar settings ↗", url=GOOGLE_SETTINGS)]
        )
    if icloud:
        sections.append(
            "🍎 <b>iCloud calendar you made public</b>\n"
            "To make it private again: Calendar app → <b>Calendars</b> → ⓘ next to it → turn "
            "off <b>Public Calendar</b>. (Mac: click the person icon next to it and untick "
            "Public Calendar.)"
        )
    if outlook:
        sections.append(
            "📧 <b>Outlook calendar you published</b>\n"
            "To stop sharing it: Outlook on the web → <b>Settings ⚙️</b> → <b>Calendar</b> → "
            "<b>Shared calendars</b> → under Publish a calendar, <b>Unpublish</b>."
        )
    if feed:
        sections.append(
            "🍎 <b>AmIFree calendar</b>\n"
            "It stops updating. To remove it:\n"
            "• iPhone: Settings → Apps → Calendar → Calendar Accounts → <b>AmIFree</b> → "
            "Delete Account\n"
            "• Mac: in Calendar, right-click <b>AmIFree</b> → Unsubscribe\n"
            "• Outlook: right-click <b>AmIFree</b> in the calendar list → Remove"
        )
    if not sections:
        return "", None
    text = "<b>To finish disconnecting, do this in your calendar apps:</b>\n\n" + "\n\n".join(
        sections
    )
    return text, InlineKeyboardMarkup(rows) if rows else None


def google_connected(busy_count: int, added: int, *, timetable_connected: bool) -> str:
    text = (
        "✅ <b>Google Calendar connected</b>\n"
        "• Plans you save or say Going to are added to your Google Calendar\n"
        "• I only see when you're busy, never titles or details\n"
        "• Remove it any time in /calendar"
    )
    if added:
        text += f"\n\nI added your {added} current plan{'s' if added != 1 else ''} just now."
    if not busy_count:
        text += (
            "\n\nI don't see any events in it for the next few months. If you use another "
            "calendar too, add it through /calendar."
        )
    return text + "\n\n" + (SCHEDULE_READY if timetable_connected else CLASSES_QUESTION)


def guide_keyboard(screen: str) -> InlineKeyboardMarkup:
    if screen == "nusmods":
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Open NUSMods ↗", url=nusmods_url())],
                [_button("‹ Back", "classes")],
            ]
        )
    if screen == "nusmodspick":
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Open NUSMods ↗", url=nusmods_url())],
                [_button("« Other calendars", "calendar")],
            ]
        )
    if screen == "export":
        return InlineKeyboardMarkup([[_button("« NUSMods link", "nusmods")]])
    if screen == "ios":
        return InlineKeyboardMarkup(
            [
                [_button("💻 On a Mac instead?", "mac")],
                [_button("📅 Mine's Google", "google")],
                [_button("« Other calendars", "calendar")],
            ]
        )
    if screen == "mac":
        return InlineKeyboardMarkup(
            [
                [_button("📱 On an iPhone instead?", "ios")],
                [_button("📅 Mine's Google", "google")],
                [_button("« Other calendars", "calendar")],
            ]
        )
    return InlineKeyboardMarkup([[_button("« Other calendars", "calendar")]])


HTML_SCREENS = frozenset(
    {
        "home",
        "nusmods",
        "nusmodspick",
        "ios",
        "mac",
        "google",
        "googlelink",
        "classes",
        "skip",
        "sendplan",
        "demo",
    }
)


def parse_mode(name: str) -> str | None:
    """Telegram parse mode for one setup screen."""
    return "HTML" if name in HTML_SCREENS else None


def screen(name: str) -> tuple[str, InlineKeyboardMarkup | None] | None:
    """The text and buttons for one setup screen, or None for an unknown name.

    "google" with sharing on, and the "apple" screens, are built by the service."""
    if name == "home":
        return WELCOME, welcome_keyboard()
    if name == "calendar":
        return PICK_CALENDAR, pick_calendar_keyboard()
    if name == "notsure":
        return NOT_SURE, not_sure_keyboard()
    if name == "googlelink":
        return GUIDES["google"], guide_keyboard("google")
    if name == "classes":
        return CLASSES_QUESTION, classes_keyboard()
    if name == "nusmodspick":
        # The same guide, reached from the calendar picker, so Back returns there.
        return GUIDES["nusmods"], guide_keyboard("nusmodspick")
    if name == "skip":
        return SKIPPED, None
    if name == "sendplan":
        return SEND_PLAN, None
    if name in GUIDES:
        return GUIDES[name], guide_keyboard(name)
    return None
