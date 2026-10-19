"""First-run setup: the welcome screen and step-by-step guides for finding schedule links.

Every screen edits the same message, so setup never floods the chat (ux-flows.md, /start A).
"""

import html
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

NUSMODS_URL = "https://nusmods.com/timetable"

WELCOME = (
    "👋 <b>Hi! I’m AmIFree.</b> Forward me any plan and I’ll tell you if you’re free.\n\n"
    "<b>Get set up in about a minute:</b>\n"
    "1️⃣ <b>Connect your calendar</b> so I can see when you’re busy.\n"
    "2️⃣ <b>Add your NUSMods</b> if your classes aren’t in your calendar.\n"
    "3️⃣ <b>Forward me a plan</b>, like “Dinner Fri 7–9pm at Vivo”.\n\n"
    "Want to skip 1 and 2? Just start forwarding plans. You can set up any time with /calendar.\n"
    "👥 Organising for a group? Add me to the chat and reply /event to a plan.\n\n"
    "🔒 I only see <i>when</i> you’re busy, never what your events are. I never read your "
    "group chats."
)

PICK_CALENDAR = (
    "📅 Which calendar do you use?\n\n"
    "🍎 iPhone works right from your phone.\n"
    "📅 Google and 📧 Outlook need a computer (or your phone's browser in desktop mode).\n\n"
    "I only see when you're busy, never what your events are called, where they are "
    "or who's going."
)

# Every calendar guide ends the same way, so nobody wonders what to do after the last step.
PASTE_NEXT = (
    "\n\n👇 Paste the link here when you have it.\n"
    "Stuck? Setup is optional: you can forward me a plan any time."
)

GUIDES = {
    "export": (
        "📆 Classes in your own calendar\n\n"
        "NUSMods can export your timetable to your calendar app itself: open your timetable "
        "on nusmods.com and look for its Export option next to Share/Sync.\n\n"
        "AmIFree's calendar sync is for your plans, not your classes, so I don't copy classes "
        "there. To have me check your classes, paste your NUSMods Share/Sync link instead."
    ),
    "nusmods": (
        "📚 Your NUSMods link · about 1 minute\n\n"
        "1. Open NUSMods (button below) and go to your timetable.\n"
        "2. Check every class type is chosen: lecture, tutorial, lab…\n"
        "3. Tap Share/Sync, then Copy link.\n\n"
        "I'll add your classes and exams as busy times. Send a new link whenever your "
        "timetable changes." + PASTE_NEXT
    ),
    "ios": (
        "🍎 iPhone / iCloud calendar · about 1 minute\n\n"
        "1. Open the Calendar app on your iPhone.\n"
        "2. Tap Calendars at the bottom.\n"
        "3. Tap ⓘ next to the calendar you use most.\n"
        "4. Turn on Public Calendar.\n"
        "5. Tap Share Link…, then Copy.\n\n"
        "It starts with webcal://, that's right.\n\n"
        "Use several calendars, like Classes, CCA and Events? Do this for each one and paste "
        "every link. I'll check them all.\n\n"
        "Anyone who has a link can see that calendar, so only paste it here. Turn Public "
        "Calendar off any time to stop sharing it." + PASTE_NEXT
    ),
    "google": (
        "📅 Google Calendar · about 1 minute\n\n"
        "Do this on a computer, or your phone's browser in desktop mode: the Google "
        "Calendar app can't show it.\n\n"
        "1. Go to calendar.google.com and sign in.\n"
        "2. Click ⚙️ at the top right, then Settings.\n"
        "3. On the left, under Settings for my calendars, click your calendar.\n"
        "4. Scroll down to Integrate calendar.\n"
        "5. Copy Secret address in iCal format. It ends in .ics.\n\n"
        "Several calendars? Repeat for each one and paste every link.\n\n"
        "Keep it secret: anyone with it can see your calendar. If it ever leaks, click "
        "Reset next to it in Google and send me the new one." + PASTE_NEXT
    ),
    "outlook": (
        "📧 Outlook calendar · about 1 minute\n\n"
        "Do this on a computer.\n\n"
        "1. Go to outlook.com (or outlook.office.com for a school or work account), then "
        "⚙️ Settings.\n"
        "2. Choose Calendar, then Shared calendars.\n"
        '3. Under Publish a calendar, pick your calendar and "Can view when I\'m busy", '
        "then Publish.\n"
        "4. Copy the ICS link.\n\n"
        "Can't see Publish? Your school account may not allow it. Try another calendar, or "
        "add your NUSMods instead." + PASTE_NEXT
    ),
}

SKIPPED = (
    "✅ All set. Forward me any plan to start.\n"
    "Add your schedule any time with /calendar, so I can check your classes and calendar too."
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


def calendar_added(label: str, busy_count: int) -> str:
    """Shown right after the first calendar link: what just happened, and what to do next."""
    name = _your(label)
    if not busy_count:
        return (
            f"✅ Connected to {name}, but I don't see any events in it for the next few "
            "months.\n\nIf you keep your plans in a different calendar, send /start and I'll "
            "show you how to add that one instead. You can still forward me plans any time."
        )
    return (
        f"✅ Schedule added! I can now see when you're busy in {name}.\n\n"
        + TRY_IT
        + "\n\n🔒 I only see when you're busy, never what your events are."
    )


def another_calendar_added(label: str, total: int) -> str:
    return (
        f"✅ Added {_your(label)}. I'm now checking {total} calendars.\n\n"
        "Send another link to add more, or /calendar to see them all."
    )


def nusmods_added(academic_year: str, semester: int) -> str:
    return (
        f"✅ Schedule added! Your classes and exams for {academic_year} semester {semester} "
        "are in.\n\n" + TRY_IT
    )


def _menu(label: str, name: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=f"menu:{name}")


def _button(label: str, screen: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=f"setup:{screen}")


def welcome_keyboard() -> InlineKeyboardMarkup:
    """Setup is optional, so the four everyday buttons are on the first screen too."""
    return InlineKeyboardMarkup(
        [
            [
                _button("📅 Connect my calendar", "calendar"),
                _button("📚 Add my NUSMods", "nusmods"),
            ],
            [_menu("📋 My plans", "plans"), _menu("🕒 Am I free?", "free")],
            [_menu("📌 Deadlines", "deadlines"), _menu("⚙️ Settings", "settings")],
        ]
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
            [_button("🍎 iPhone / iCloud", "ios"), _button("📅 Google", "google")],
            [_button("📧 Outlook", "outlook"), _button("« Back", "home")],
        ]
    )


def first_calendar_keyboard() -> InlineKeyboardMarkup:
    """/calendar with nothing connected yet: which calendar, or plans into a phone's calendar."""
    from app.bot.keyboards import calendar_home_rows

    return InlineKeyboardMarkup(
        [*pick_calendar_keyboard().inline_keyboard[:-1], calendar_home_rows()[1]]
    )


GOOGLE_CHOOSE = (
    "📅 Google Calendar\n\n"
    "🔗 Share it with AmIFree (recommended): I add the plans you tap Going on to your "
    "calendar, and I read when you're busy.\n\n"
    "📋 Paste a link: I only read when you're busy. I can't add anything."
)


def google_choose_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔗 Share it (adds plans)", callback_data="gcal:start")],
            [_button("📋 Paste a link (read only)", "googlelink")],
            [_button("« Other calendars", "calendar")],
        ]
    )


def google_share_steps(email: str) -> str:
    return (
        "🔗 <b>Share your Google Calendar with AmIFree</b>\n\n"
        "This lets me add your Going plans to your calendar. It takes about 1 minute, and you "
        "can stop any time.\n"
        "You need a computer, or your phone's browser in desktop mode.\n\n"
        "<b>Part 1 · Open your calendar's settings</b>\n"
        "1. Go to calendar.google.com.\n"
        "2. Click the ⚙️ gear at the top right, then <b>Settings</b>.\n"
        "3. On the left, under <b>Settings for my calendars</b>, click the calendar you want to "
        "connect.\n\n"
        "<b>Part 2 · Add AmIFree</b>\n"
        "4. Scroll down to <b>Share with specific people or groups</b> and click "
        "<b>Add people and groups</b>.\n"
        f"5. Paste this email (tap it to copy): <code>{html.escape(email)}</code>\n"
        "6. Open the <b>Permission</b> menu and choose <b>Make changes (see private events as "
        "free/busy)</b>. If you don't see that, choose <b>Make changes to events</b>.\n"
        "7. Click <b>Send</b>.\n\n"
        "<b>Part 3 · Tell me your email</b>\n"
        "8. Send me your own Google email here, like you@gmail.com. I'll put a code in that "
        "calendar. Send the code back to show the calendar is yours.\n\n"
        "I only change events I added myself. To stop, remove AmIFree from Shared with, or tap "
        "Remove in /calendar."
    )


GOOGLE_PHOTOS_CAPTION = "Swipe through the pictures: the red box shows what to click in each step."

GOOGLE_PHOTO_FILES = (
    "step1_gear.png",
    "step2_settings.png",
    "step3_calendar.png",
    "step4_add.png",
    "step5_dialog.png",
)


def google_share_photos() -> tuple[bytes, ...]:
    """The annotated screenshots, in order. Empty if they can't be read: the steps still work."""
    folder = Path(__file__).resolve().parent.parent / "assets" / "google"
    try:
        return tuple((folder / name).read_bytes() for name in GOOGLE_PHOTO_FILES)
    except OSError:
        return ()


def google_steps_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_button("« Other calendars", "calendar")]])


def guide_keyboard(screen: str) -> InlineKeyboardMarkup:
    if screen == "nusmods":
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Open NUSMods ↗", url=NUSMODS_URL)],
                [_button("Classes in my own calendar?", "export"), _button("« Back", "home")],
            ]
        )
    if screen == "export":
        return InlineKeyboardMarkup([[_button("« NUSMods link", "nusmods")]])
    return InlineKeyboardMarkup([[_button("« Other calendars", "calendar")]])


def screen(name: str, google: bool = False) -> tuple[str, InlineKeyboardMarkup | None] | None:
    """The text and buttons for one setup screen, or None for an unknown name."""
    if name == "home":
        return WELCOME, welcome_keyboard()
    if name == "calendar":
        return PICK_CALENDAR, pick_calendar_keyboard()
    if name == "google" and google:
        return GOOGLE_CHOOSE, google_choose_keyboard()
    if name == "googlelink":
        return GUIDES["google"], guide_keyboard("google")
    if name == "skip":
        return SKIPPED, None
    if name in GUIDES:
        return GUIDES[name], guide_keyboard(name)
    return None
