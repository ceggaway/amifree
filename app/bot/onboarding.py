"""First-run setup: the welcome screen and step-by-step guides for finding schedule links.

Every screen edits the same message, so setup never floods the chat (ux-flows.md, /start A).
"""

import html
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

NUSMODS_URL = "https://nusmods.com/timetable"

EVERYDAY_HELP = (
    "📋 <b>My plans</b> · /plans — see upcoming events and deadlines you’ve saved with me.\n\n"
    "📌 <b>Deadlines</b> · /deadline — save or forward a due date and get reminders before it’s due. "
    "Try “Report due Fri 11:59pm”.\n\n"
    "💬 <b>Feedback</b> · /feedback — If you run into any issues or have suggestions, send us feedback using /feedback. You can send it anonymously or with your @handle"
)

WELCOME = (
    "👋 <b>Hi! I’m AmIFree, your Telegram planning assistant.</b> Send me an event to check "
    "if you’re free, then save it with me to sync it to your connected personal calendar.\n\n"
    "<b>1. Connect your schedule</b>\n"
    "Connect your <b>personal calendar</b> so I can check for clashes with existing events. "
    "If your classes aren’t included, I’ll help you connect your NUSMods timetable separately.\n\n"
    "<b>2. Send me a plan</b>\n"
    "Forward an event message or photo, or type the details. I’ll show "
    "<b>whether you’re free or have any clashes</b>.\n\n"
    "<b>3. Save with a tap</b>\n"
    "Tap <b>Add to plans</b> to save the event. With calendar syncing enabled, I’ll "
    "<b>automatically add it to your personal calendar</b>.\n\n"
    "See /help for all features and instructions.\n\n"
    "🔒 <b>Your privacy matters.</b> I only check your calendar’s busy times, not event "
    "details. I never read your group chats.\n\n"
    "Choose <b>Connect personal calendar</b> to include your existing schedule, or "
    "<b>Skip calendar setup</b> to try the bot first."
)

PICK_CALENDAR = (
    "📅 Which calendar do you use?\n\n"
    "🍎 iPhone works right from your phone.\n"
    "📅 Google works in the Google Calendar app or a computer browser.\n"
    "📧 Outlook is coming soon.\n\n"
    "I only see when you're busy, never what your events are called, where they are "
    "or who's going."
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
        "Tap <b>Open NUSMods</b> below and select the correct semester.\n\n"
        "<b>2. Check your classes</b>\n"
        "Make sure you’ve selected all your lectures, tutorials and labs.\n\n"
        "<b>3. Copy the link</b>\n"
        "Tap <b>Share/Sync</b>, then <b>Copy link</b>.\n\n"
        "<b>4. Send it here</b>\n"
        "Paste the link into this chat and send it.\n\n"
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
    "outlook": (
        "📧 <b>Outlook — coming soon</b>\n"
        "We're still setting up Outlook calendars, so this one isn't ready yet.\n\n"
        "For now you can:\n"
        "🍎 Use your iPhone / iCloud calendar\n"
        "📅 Use your Google calendar\n"
        "📚 Or add your NUSMods timetable\n\n"
        "You can still forward me a plan any time — setup is optional."
    ),
}

PLAN_ACTIONS = (
    "When the event card appears, tap <b>Add to plans</b> to save it, or <b>Skip</b> "
    "if you don’t want to save it."
)

SKIPPED = (
    "<b>Send me your first plan</b>\n\n"
    "Forward an event message or photo, or type the details.\n\n"
    "I’ll check for clashes with plans you’ve saved with me. Your timetable and personal "
    "calendar events won’t be included until you connect them through /calendar.\n\n" + PLAN_ACTIONS
)

SEND_PLAN = (
    "✅ <b>You’re ready to send a plan</b>\n\n"
    "Forward an event message or photo, or type the details. I’ll check for clashes with "
    "your connected calendar and plans you’ve saved with me.\n\n" + PLAN_ACTIONS + "\n\n"
    "You can add NUSMods later with /calendar if your classes are missing."
)

CLASSES_QUESTION = (
    "<b>Are your classes included in this calendar?</b>\n\n"
    "Tap <b>Add NUSMods</b> if your classes are missing. Tap <b>Continue</b> if they’re "
    "already included or you don’t have classes to add."
)

SCHEDULE_READY = (
    "✅ <b>You’re ready to send a plan</b>\n\n"
    "Forward an event message or photo, or type the details. I’ll check your connected "
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
        "Forward an event message or photo, or type the details.\n\n" + PLAN_ACTIONS
    )


def _menu(label: str, name: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=f"menu:{name}")


def _button(label: str, screen: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=f"setup:{screen}")


def welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                _button("📅 Connect personal calendar", "calendar"),
                _button("Skip calendar setup", "skip"),
            ],
        ]
    )


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
            [_button("🍎 iPhone / iCloud", "ios"), _button("📅 Google", "google")],
            [_button("📧 Outlook", "outlook"), _button("« Back", "home")],
        ]
    )


def first_calendar_keyboard() -> InlineKeyboardMarkup:
    """Offer calendar setup, NUSMods, and plans in a calendar app before connecting."""
    from app.bot.keyboards import calendar_home_rows

    return InlineKeyboardMarkup(
        [*pick_calendar_keyboard().inline_keyboard[:-1], *calendar_home_rows()[1:]]
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
        "🔗 <b>Share your Google Calendar</b> · about 1 minute\n"
        "Do this on a computer (or your phone's browser in desktop mode).\n\n"
        "1. Go to <b>calendar.google.com</b>.\n"
        "2. Click the <b>⚙️</b> gear, then <b>Settings</b>.\n"
        "3. Under <b>Settings for my calendars</b>, click your calendar.\n"
        "4. Open <b>Share with specific people or groups</b>, then <b>Add people and "
        "groups</b>.\n"
        f"5. Paste this email: <code>{html.escape(email)}</code>\n"
        "6. Set the permission to <b>Make changes (see private events as free/busy)</b>.\n"
        "7. Click <b>Send</b>.\n\n"
        "Then send me your own Google email here (like you@gmail.com). I'll drop a code in "
        "your calendar — send it back to show it's yours.\n\n"
        "✨ Connects live, and I can add your Going plans to this calendar too.\n\n"
        "🔒 I only read your busy times, and only change events I add. To stop: remove AmIFree "
        "from <b>Shared with</b>, or tap Remove in /calendar."
    )


def google_share_steps_phone(email: str) -> str:
    return (
        "🔗 <b>Share your Google Calendar</b> · about 1 minute\n"
        "Do this in the Google Calendar app on your phone.\n\n"
        "1. Open <b>Google Calendar</b>, tap <b>☰</b> (top left).\n"
        "2. Tap <b>Settings</b>.\n"
        "3. Tap your calendar — <b>My calendar</b>, or the one to check.\n"
        "4. Tap <b>Shared with</b>, then <b>Add people or groups</b>.\n"
        f"5. Paste this email: <code>{html.escape(email)}</code>\n"
        "6. Set the permission to <b>Make changes (see private events as free/busy)</b>.\n"
        "7. Tap <b>Save</b>.\n\n"
        "Then send me your own Google email here (like you@gmail.com). I'll drop a code in "
        "your calendar — send it back to show it's yours.\n\n"
        "✨ Connects live, and I can add your Going plans to this calendar too.\n\n"
        "🔒 I only read your busy times, and only change events I add. To stop: remove AmIFree "
        "from <b>Shared with</b>, or tap Remove in /calendar."
    )


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


GOOGLE_PHONE_PHOTO_FILES = (
    "gphone_step1.png",
    "gphone_step2.png",
    "gphone_step3.png",
    "gphone_step4.png",
    "gphone_step5.png",
)


def google_phone_photos() -> tuple[bytes, ...]:
    """The phone share screenshots, in order. Empty if they can't be read: the steps still work."""
    folder = Path(__file__).resolve().parent.parent / "assets" / "google_phone"
    try:
        return tuple((folder / name).read_bytes() for name in GOOGLE_PHONE_PHOTO_FILES)
    except OSError:
        return ()


def google_steps_keyboard(phone: bool = False) -> InlineKeyboardMarkup:
    switch = (
        InlineKeyboardButton("💻 On a computer instead?", callback_data="gcal:computer")
        if phone
        else InlineKeyboardButton("📱 On my phone instead?", callback_data="gcal:start")
    )
    return InlineKeyboardMarkup([[switch], [_button("« Other calendars", "calendar")]])


def guide_keyboard(screen: str) -> InlineKeyboardMarkup:
    if screen == "nusmods":
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Open NUSMods ↗", url=NUSMODS_URL)],
                [_button("‹ Back", "classes")],
            ]
        )
    if screen == "export":
        return InlineKeyboardMarkup([[_button("« NUSMods link", "nusmods")]])
    if screen == "outlook":
        return InlineKeyboardMarkup(
            [
                [_button("📅 Use Google", "google"), _button("🍎 Use iPhone", "ios")],
                [_button("« Other calendars", "calendar")],
            ]
        )
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
        "ios",
        "mac",
        "google",
        "googlelink",
        "outlook",
        "classes",
        "skip",
        "sendplan",
    }
)


def parse_mode(name: str) -> str | None:
    """Telegram parse mode for one setup screen."""
    return "HTML" if name in HTML_SCREENS else None


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
    if name == "classes":
        return CLASSES_QUESTION, classes_keyboard()
    if name == "skip":
        return SKIPPED, None
    if name == "sendplan":
        return SEND_PLAN, None
    if name in GUIDES:
        return GUIDES[name], guide_keyboard(name)
    return None
