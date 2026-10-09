# AmIFree UX Flows

This document describes how AmIFree should behave from the user's point of view, based on our two surveys (70 responses) and the professors' feedback. For each feature it gives the flow, the messages and buttons, and **what changes from the current bot**, so the backend can implement it feature by feature.

Each change has a priority:

- **P1:** needed before the pilot.
- **P2:** after P1, before STePS if time allows.
- **Later:** future enhancement.

## 1. UX principles

| Principle | What it means in practice |
| --- | --- |
| Less effort than the problem | 0–2 taps per action. No forms. |
| Chat first | Users mainly interact with the bot. The Mini App is only used for filling in availability for group events. |
| One card, edited in place | After a button tap, edit the existing message instead of sending a new one. Group cards update live without notifying anyone. |
| No spam | Send a new message only when the user needs to act or know something. |
| Honest answers | Always "No clashes found", never "You're free". The bot can't see everything. |
| Privacy | The bot never reads group chats. Others only ever see free or busy, never what someone is doing or why. |
| Confirm before saving | Forwarded plans are never saved without a tap. |

## 2. Commands

**Private chat**

| Command | Menu description (shown when users type `/`) |
| --- | --- |
| `/plans` | What's coming up |
| `/busy` | Block time, e.g. /busy sat 2-6pm |
| `/free` | Check if you're free |
| `/event` | Plan a group event |
| `/settings` | Schedule, reminders, privacy |
| `/help` | How to use AmIFree |

**Group chat**

| Command | Menu description |
| --- | --- |
| `/event` | Plan an event and find a time |

Forwarding or typing a plan needs no command. `/start` stays for first-time setup (see "Setup and other screens").

**Changes from current:** replace `/block` and `/blocks` with `/busy` (P1). Move `/reminders` and `/deletemydata` into `/settings` (P2). Remove `/events` and `/cancel` from the menu (P1). Keep `/block`, `/blocks`, `/reminders`, `/deletemydata` and `/cancel` working as hidden shortcuts so nothing breaks. Replace `/whosfree` with `/event` (see Feature 1).

**How every feature is reached**

| Feature | Entry point |
| --- | --- |
| Forward to Save | Forward or type a plan |
| Quick Block | `/busy` |
| Am I free? | `/free`; [Am I free?] on group event cards |
| Reminders | Automatic; adjust in `/settings` → Reminders |
| Change Alerts | Automatic |
| Group events (finding a time, RSVP) | `/event`; buttons on the group card |
| Organiser control panel | Sent after posting; also in `/plans` (⚙️) |
| Edit, remove, skip a week | `/plans` → [Check event] |
| Schedule (NUSMods, personal calendar) | `/start` once; then `/settings` → Schedule |
| Privacy and deleting data | `/settings` → Privacy |
| Availability grid (Mini App) | [Add my availability] on the group card |

## 3. Message rules

- **The first line of a plan card is the verdict:** ✅ No clashes found, or ⚠️ Clashes with X.
- **There is no "busy day" message anywhere.** Students are always busy, so it isn't useful.
- **Missing details show "?",** with no explanation. For example: `🗓 Thu 15 Oct · 8pm – ?` or `📍 ?`
- **At most 3 buttons per row,** with short labels.
- **Symbols:** ✅ no clash · ⚠️ clash · 🚫 blocked time · 🔔 changed · ❌ cancelled · ⏰ reminder · 🌙 night-before summary
- **Times are written like "7–9pm" or "7:30pm",** not 24-hour time.
- **Every "type what to change" prompt has a [Cancel] button,** so nobody gets stuck in edit mode.
- **Say "personal calendar",** not "calendar", for Google, Apple or Outlook calendars, so it isn't confused with NUSMods.

## 4. Implementation order

1. Setup and other screens: `/start`, `/help`, `/plans`, `/settings`, plus personal calendar links
2. Feature 2: Forward to Save
3. Feature 3: Quick Block (`/busy`)
4. Feature 4: Am I free?
5. Feature 5: Reminders
6. Feature 6: Change Alerts
7. Feature 7: Group events (RSVP and attendance)
8. Feature 1: Finding a time for group events (needs the Mini App availability grid)

The features are numbered in the order they were designed. Features 2–5 are mostly self-contained, so they come first.

---

## Feature 1: Finding a time for a group event (organiser)

**Goal:** an organiser finds a time that works for everyone without anyone filling in availability from scratch.

### Flow

1. **Organiser creates the event.** They type `/event` in the group, and the bot sends a link to its private chat. They describe the event in one message, for example *"CS3216 meeting, 2h, next week, evenings, reply by Sunday"*. The bot shows a summary with defaults filled in (time window 9am–10pm, deadline 2 days from now):

   ```
   📋 CS3216 meeting · 2 hours
   📅 Mon 12 – Fri 16 Oct · evenings (6–10pm)
   ⏰ Reply by Sun 11 Oct, 11:59pm

   [Post to group]  [Edit]
   ```

   If the description contains a **specific time** instead of a period, the bot skips straight to the event card (Feature 7). Organisers can also start with `/event` in private chat and pick the group with Telegram's chat picker.

2. **Members give availability.** The group card shows the event name, dates, deadline, a live response count and **[Add my availability]**. The button opens the Mini App grid:
   - Busy times are already blocked (NUSMods classes, personal calendar, saved plans, Going events and busy blocks). Only the member sees why a slot is busy.
   - All remaining free slots are pre-selected.
   - A note: *"We've blocked the times you're busy. Untick any other times that don't work. The organiser only sees how many people are free, not who."*
   - Members untick anything else, then tap **Submit**. Only available or not available, no "maybe".
   - Members can edit their answer until the deadline.
   - The grid is designed for phones: 3–4 days visible at a time, swipe sideways for more, 30-minute rows, tap or drag to toggle.

3. **The card updates live.** For example "6 responded" and "⏰ Closes in 3 hours". These are edits to the same message, so no notifications are sent.

4. **Reminders (private, only to people who haven't responded):**
   - Up to 2 automatic private reminders: one at the halfway point and one about 3 hours before the deadline.
   - The organiser's **Nudge** button sends the same private reminder, at most once every 12 hours.
   - People who have responded never get reminders.

5. **The bot recommends the best time.** At the deadline, the organiser gets one private message:

   ```
   🏆 Best times for CS3216 meeting (6 responded)
   1. Tue 13 Oct · 7–9pm · 6 of 6 free
   2. Thu 15 Oct · 6–8pm · 5 of 6 free
   3. Mon 12 Oct · 8–10pm · 5 of 6 free

   [Choose 1] [Choose 2] [Choose 3]
   [Show more times]  [Extend deadline]
   ```

   It shows counts only, never names. **Show more times** adds the next 3 slots to the same message. The organiser can also close early once everyone has responded.

6. **The event is confirmed.** The same group card becomes the event card (Feature 7). Everyone who responded gets one private message:
   - Free at that time: *"✅ CS3216 meeting is confirmed for Tue 13 Oct, 7–9pm. You're marked as going."* **[Can't make it]**
   - Not free at that time: *"CS3216 meeting is confirmed for Tue 13 Oct, 7–9pm. This clashes with your schedule."* **[I can make it]**

### Rules

- **Availability updates itself:** if a member saves a plan or block that overlaps a slot they marked free before the deadline, their availability updates automatically. The confirmation of that plan gets one extra line: *"↻ Updated your availability for CS3216 meeting."*
- **Changing the dates or duration** before a time is chosen re-fills everyone's availability from their schedule and sends one message: *"🔔 CS3216 meeting: dates changed to … Your availability has been updated."* **[Review]**

### Changes from current

| Current | New | Priority |
| --- | --- | --- |
| `/whosfree` poll with "Count me in" and 9 vote buttons (✅ 🤔 ❌ per slot) | `/event` with a period → Mini App grid pre-filled from schedules | P2 (needs the grid in `web/`) |
| Slots suggested once 3 people join | Organiser sets the period; the bot ranks slots from submitted availability | P2 |
| Organiser buttons (Suggest, Pick, Nudge) on the group card | Results and choices sent privately to the organiser | P2 |
| Maybe answer | Available or not available only | P2 |

> **Open question for the team:** until the Mini App grid is ready, do we keep the current `/whosfree` poll as a temporary version?

---

## Feature 2: Forward to Save

**Goal:** a student forwards a plan and saves it in one tap, with a clash check.

### Flow

1. The student **forwards, pastes or types** a plan. No command is needed.
2. The bot replies with **one card**:

   ```
   ✅ No clashes found

   📌 Badminton session
   🗓 Thu 15 Oct · 8pm – ?
   📍 ?

   [Save]  [Edit]
   ```

3. **Save:** the same card changes to `✅ Saved` with **[Undo]**. No new message.
4. **Edit:** the bot says *"Type what to change, e.g. 'till 10pm, venue UTSRC'."* with a **[Cancel]** button. The student types the change, and the card updates with a new clash check. If a typo is unclear, the bot shows the closest match: *"LT27?"* **[Yes] [Edit]**
5. **Ignoring the card means nothing is saved.** There is no Discard button.

### Missing and vague details

| Situation | Behaviour |
| --- | --- |
| No end time | Shows `8pm – ?`. Clash checks assume 2 hours, but this isn't shown. |
| No venue | Shows `📍 ?` |
| Relative date ("next Thurs") | Shows the actual date, e.g. Thu 15 Oct |
| No date at all | Can still be saved. Listed in `/plans` under "No date yet". Not included in reminders or clash checks until a date is added. |
| Date already passed | Assumes the next occurrence |
| Plans with "?" | Shown with "?" in `/plans`; tap to complete later |

### Clashes

The verdict line shows the clash, but **Save is always one tap**. After saving, one optional button may appear:

| Clashes with | After saving |
| --- | --- |
| A class or blocked time | Nothing extra |
| Another saved plan | *"Saved. You have 2 plans at 8pm."* **[Remove Hall meeting]** |
| A group event they're going to | *"Saved. You're also going to CS3216 meeting."* **[Not going to CS3216 meeting]** (updates their RSVP) |

Nothing is removed unless the student taps.

### Several events in one message

```
📋 3 events found

✅ Training · Tue 13 Oct · 7:30–9:30pm
✅ Training · Thu 15 Oct · 7:30–9:30pm
⚠️ Friendly match · Sat 17 Oct · 2pm – ?

[Save all]  [Choose]
```

### Undecided plans ("Tue or Thu?")

- The card shows **[Save both as pending] [Tue] [Thu]**.
- Pending plans show "?" in `/plans` and aren't counted as busy until the date is decided.
- The question is asked once, inside the night-before reminder before the earliest option (Feature 5).
- A forwarded follow-up that confirms the date resolves it automatically.

### Other cases

| Case | Behaviour |
| --- | --- |
| No plan found | *"No plan found in that message. Try typing it, e.g. 'dinner fri 7pm'."* |
| Same plan forwarded again | *"Already saved."* **[Update]** if details changed |
| It's a group event the bot already knows | *"This is CS3216 meeting from your project group."* **[I'm going]** |
| No NUSMods or personal calendar yet | Saved as normal, then once only: *"Add your NUSMods or personal calendar link to check your schedule too."* **[Add] [Not now]** |

### Changes from current

| Current (`messages.py`, `keyboards.py`, `scheduling.py`) | New | Priority |
| --- | --- | --- |
| Buttons: Going / Maybe / Can't make it / Edit / Save | **Save / Edit** only; ignoring the card = not saved | P1 |
| Statuses PREVIEW / SAVED / GOING / MAYBE / DECLINED on personal plans | A saved personal plan means going. No Maybe or Can't for personal plans | P1 |
| "Busy day" verdict | Removed | P1 |
| Asks for a missing end time | Shows "?" and assumes 2 hours for clash checks | P1 |
| "⚠️ End time assumes a 60-minute duration…" | Removed; "?" replaces it | P1 |
| Card footer "Checked: your Going plans… Status: …" | Removed | P1 |
| Possible-overlap buttons ("Attend this one", "Attend X") | One optional button after saving | P1 |
| Save sends "Saved. Find it any time with /plans." plus a new card | Edit the same card to "✅ Saved" + Undo | P1 |
| Edit flow | Type the change; show the closest match for unclear input | P1 |
| Several events in one message | Detect them and show a list with Save all / Choose | P2 |
| Undecided plans ("Fri or Sat") ask which day | Save both as pending, ask in the night-before reminder | P2 |

---

## Feature 3: Quick Block (`/busy`)

**Goal:** block busy time the bot can't see (family, work, informal plans) in about 3 seconds.

### Flow

- **`/busy` + text saves instantly** (0 taps), except for weekly blocks with no end date.
- **`/busy` on its own** shows one-tap presets:

  ```
  🚫 When are you busy?

  [Tonight]  [Tomorrow]  [This weekend]

  Or type e.g. /busy sat 2-6pm
  ```

  Tonight = today 6–11pm · Tomorrow = all day · This weekend = Sat and Sun, all day.

### How text is read

| Student types | Saved as | Taps |
| --- | --- | --- |
| `/busy sat 2-6pm` | Sat 17 Oct · 2–6pm | 0 |
| `/busy family dinner sun 7pm` | Family dinner · Sun 18 Oct · 7pm – ? | 0 |
| `/busy tmr night` | Sat 17 Oct · 6–11pm | 0 |
| `/busy sun` | Sun 18 Oct · all day | 0 |
| `/busy away 20-22 oct` | Away · Tue 20 – Thu 22 Oct · all day | 0 |
| `/busy 2-6pm` | Today · 2–6pm | 0 |
| `/busy work every wed 6-10pm till 14 nov` | Work · every Wed · 6–10pm · until 14 Nov | 0 |
| `/busy work every wed 6-10pm` | Asks for an end date (below) | 1 |
| `/busy` → [Tonight] | Today · 6–11pm | 1 |

Defaults: no day means today; "night" or "evening" means 6–11pm; "2-6" without am or pm means 2–6pm; no end time shows "?" and counts as 2 hours.

### Weekly block with no end date

```
🚫 Work · every Wed · 6–10pm
Until when?

[End of semester (14 Nov)]  [Pick a date]
[No end date]
```

### Cards

```
🚫 Blocked
Sat 17 Oct · 2–6pm

[Undo]  [Edit]
```

```
🚫 Blocked every Wed · Work
6–10pm · until 14 Nov

[Undo]  [Edit]
```

If the block overlaps something:

```
🚫 Blocked
Sat 17 Oct · 2–6pm
⚠️ Overlaps with Badminton (3–5pm)

[Undo]  [Edit]  [Remove Badminton]
```

For a group event, the third button is **[Not going to CS3216 meeting]**. Inside an open availability request, one line is added: *"↻ Updated your availability for CS3216 meeting."*

If the bot can't understand the text:

```
🤔 Couldn't tell when. Try /busy sat 2-6pm, or:

[Tonight]  [Tomorrow]  [This weekend]
```

### Editing and removing

| Action | How |
| --- | --- |
| Undo | [Undo] on the card |
| Edit | [Edit] → type the change (the next message is treated as the correction; use Telegram's force reply). The prompt has [Cancel]. |
| Skip one week | `/plans` → tap the block → [Skip this week] |
| Remove | `/plans` → tap the block → [Remove] |

Labels ("Family", "Work") are private. Others only ever see "busy".

### Changes from current (`quick_blocks.py`, `scheduling.py`)

| Current | New | Priority |
| --- | --- | --- |
| `/block` command; plain "busy sat 2-6pm" also works | `/busy` command only | P1 |
| "Blocked. Plans that overlap it will show a clash. /blocks lists them." | The Blocked card with [Undo] [Edit] | P1 |
| `/blocks` lists blocks with Remove | Blocks shown in `/plans` (🚫) with Edit / Skip this week / Remove | P1 |
| No presets | `/busy` alone shows Tonight / Tomorrow / This weekend | P1 |
| Weekly blocks with no end date | Ask for an end date | P1 |
| Overlap with a plan | Optional [Remove X] / [Not going to X] button | P2 |
| Skip one week | New | P2 |

---

## Feature 4: Am I free?

**Goal:** answer "am I free?" in one tap or one line, privately.

### In groups (button on event cards)

Tapping **[Am I free?]** shows a popup that only the tapper sees (callback alert, maximum 200 characters):

| Situation | Popup |
| --- | --- |
| No clash | ✅ No clashes found for Tue 13 Oct, 7–9pm. |
| Clash | ⚠️ Clashes with CS2040 Tutorial (6–8pm). |
| Several clashes | ⚠️ Clashes with CS2040 Tutorial (6–8pm) and Work (blocked). |
| Partly overlaps | ⚠️ Your CS2040 Lab ends at 7:30pm, so you'd miss the first 30 min. |
| No schedule added | ✅ No clashes found. Add your NUSMods or personal calendar link to check your schedule too. |
| Event cancelled | ❌ This event has been cancelled. |

Tapping **Going** shows the same check in its popup, e.g. *"✅ You're going. ⚠️ Clashes with CS2040 Tutorial."*

**Not set up yet:** the popup says *"Set up in 10 seconds to check."* and opens the bot's private chat (`t.me/<bot>?start=<event>`). The answer is shown first, with setup as optional (see "Setup and other screens" → `/start`, case B).

### In private chat (`/free`)

| Student types | Reply |
| --- | --- |
| `/free fri 7-9pm` | ✅ No clashes found · Fri 16 Oct · 7–9pm, or a list of clashes |
| `/free fri 7pm` | Same, checking 7–9pm |
| `/free tomorrow` | 🟢 Free on Sat 17 Oct: • 8am – 2pm • 6pm – 11pm |
| `/free` | [Today] [Tomorrow] [This weekend]. One tap shows free time |

Free-time lists run from 8am to 11pm. Checks use NUSMods classes, the personal calendar, saved plans, Going events and busy blocks.

### Changes from current

| Current | New | Priority |
| --- | --- | --- |
| Popup ends with "Only you can see this." and may say "Busy day" | Popups as above; no busy day; no extra line | P1 |
| "Possible overlap with X" | "Your X ends at 7:30pm, so you'd miss the first 30 min." | P2 |
| No `/free` command | New `/free` command | P1 |
| Free-time list for a day | New | P2 |
| Not-set-up flow | Deep link → one step → answer with Going / Not going | P1 |

---

## Feature 5: Reminders

**Goal:** students never forget a plan, without spam.

### Night-before summary

- Sent at **9pm by default**, **only if there's something tomorrow**.
- Includes saved plans, group events marked Going and **blocked times**. Excludes classes.

```
🌙 Tomorrow · Thu 15 Oct

• 4pm  Project meeting · Central Library
• 6pm  🚫 Work (till 10pm)
• 7:30pm  Training · MPSH 2  🔔 venue changed
• 7pm  Dinner with Jun · 📍 ?
```

- No buttons on normal summaries.
- 🔔 marks details changed since the plan was saved. "?" marks missing details.
- Items with no time are listed at the end ("• Time ?  Dinner with Jun").
- A pending plan question is added at the bottom:

  ```
  ❓ Badminton: is it tomorrow or Thu?
  [Tomorrow]  [Thu]  [Not happening]
  ```

### First summary only

The first summary adds a setup question, so there's no separate setup message:

```
You'll get this at 9pm whenever you have plans the next day.
⏰ Also remind you before each one?

[No thanks]  [15 min]  [30 min]  [1 hour]
[Change 9pm]
```

After a tap, the buttons are replaced with e.g. "✓ I'll also remind you 30 min before". If ignored, before-event reminders stay off and the bot doesn't ask again.

### Before-event reminder (opt-in)

```
⏰ In 30 min · Training
7:30pm · MPSH 2
```

Recent changes are flagged: `7:30pm · MPSH 2  🔔 venue changed from UTSRC`

### Rules

| Case | Behaviour |
| --- | --- |
| No plans tomorrow | No summary |
| Plan added after the summary was sent | No extra summary |
| Item with no start time | No before-event reminder |
| Before-event reminder would be before 7am | Skipped |
| Event cancelled or the user is no longer going | Pending reminders cancelled |
| Two items start at the same time | One before-event reminder |

### Settings (`/settings` → Reminders)

```
🔔 Reminders

Night before: 9pm
[8pm]  [9pm]  [10pm]  [Off]

Before each event: 30 min
[Off]  [15 min]  [30 min]  [1 hour]
```

### Changes from current (`reminders.py`, `jobs.py`)

| Current | New | Priority |
| --- | --- | --- |
| Lists Going plans only | Also busy blocks (🚫) | P1 |
| Times as "19:00–21:00 … @ venue" | "7:30pm  Training · MPSH 2" | P1 |
| "Still undecided: …" line | Removed (no undecided status for personal plans) | P1 |
| Footer "/plans for details. /reminders off stops these summaries." | Removed; the first summary explains once | P1 |
| 🔔 changed and "?" missing markers | New | P2 |
| Fixed time; on or off only | 8pm / 9pm / 10pm / Off | P2 |
| No before-event reminders | Opt-in 15 / 30 / 60 min | P2 |

---

## Feature 6: Change Alerts

**Goal:** students learn about changes in one short message. Nothing in their schedule changes unless they decide.

### Who gets alerts

| Source | Recipients |
| --- | --- |
| Organiser edits a group event | Everyone marked Going |
| Organiser cancels a group event | Everyone marked Going |
| Student forwards a follow-up about a saved plan | That student, as a card to confirm |

### Alerts

```
🔔 CS3216 meeting
⏰ 7–9pm → 8–10pm
Tue 13 Oct · COM1-0208
```

```
🔔 CS3216 meeting
📍 COM1-0208 → LT27
Tue 13 Oct · 7–9pm
```

Several fields are shown in one message. No buttons, unless the change creates a clash:

```
🔔 CS3216 meeting
⏰ 7–9pm → 8–10pm
⚠️ Now clashes with Badminton (8pm)

[Not going]  [Remove Badminton]
```

Within 3 hours of the start:

```
🔔 Last-minute change · CS3216 meeting today
📍 COM1-0208 → LT27
```

Cancelled (removed from plans; pending reminders cancelled):

```
❌ CS3216 meeting on Tue 13 Oct is cancelled.
```

To people who said **Not going** and now have no clash:

```
🔔 CS3216 meeting moved to Thu 15 Oct · 6–8pm
✅ No clashes found

[I'm going]
```

### Forwarded follow-up about a personal plan

```
🔔 Update Badminton?
⏰ 8pm → 9pm
Thu 15 Oct · UTSRC

[Update]  [Save as new]
```

If the message says it's cancelled: `❌ Cancel Badminton (Thu 15 Oct)?` **[Cancel it] [Keep]**

### Rules

| Rule | Why |
| --- | --- |
| Edits within 5 minutes are combined into one alert (original → final) | Organisers often fix several details in a row |
| Only date, time, venue and cancellation trigger alerts | Name and typo fixes just update the card |
| Sent silently between 11pm and 7am, unless the event is within 12 hours | No late-night pings |
| No alert if nothing really changed | No pointless messages |
| The group card shows 🔔 Updated; reminders are rescheduled | Everything stays consistent |

### Changes from current (`change_alerts.py`, `event_updates.py`)

| Current | New | Priority |
| --- | --- | --- |
| One line: "Dinner changed: Fri 9 Oct 7pm–9pm → 8pm–10pm; venue unchanged" | Card format above; only changed fields | P1 |
| "time unchanged", "venue unchanged", "notes updated" | Removed | P1 |
| Renames and notes changes are included in alerts | Only date, time, venue and cancellation trigger alerts | P1 |
| Follow-up buttons "Confirm update" / "Keep unchanged" | [Update] [Save as new] | P1 |
| Combine edits within 5 minutes | New | P2 |
| Silent overnight | New (`disable_notification`) | P2 |
| Alert to Not-going people who are now free | New | P2 |

---

## Feature 7: Group events (RSVP and attendance)

**Goal:** a clean group card that answers "when, where, who's going", with organiser tools kept private.

### Creating

The organiser types `/event` and describes the event in the bot's private chat. A **specific time** (e.g. *"Badminton Sat 17 Oct 2–4pm UTSRC"*) posts an event card directly. A **period** starts Feature 1. Both show a preview first: **[Post to group] [Edit]**.

### The event card (in the group)

```
📣 Badminton
🗓 Sat 17 Oct · 2–4pm
📍 UTSRC Sports Hall
✅ 12/15 going

[Going]  [Not going]
[Am I free?]  [Who's going?]
```

- "12/15" means 12 Going out of 15 replies (Going + Not going).
- Counts update live by editing the message (combine edits; Telegram limits edits per group).
- No organiser buttons on the card.

### RSVP

| Action | Popup (only the tapper sees it) |
| --- | --- |
| Tap Going | ✅ You're going. Added to your plans. (plus a ⚠️ clash warning if there is one) |
| Tap Not going | Got it. (removed from their plans) |
| Tap the same answer again | You're already marked as going. |
| Change their mind | Tap the other button, any time until the event starts |

No setup is needed to RSVP. If the user hasn't started the bot, the popup adds: *"Start @AmIFreeBot for reminders."*

**Who's going?** shows a popup: *"Going (12): Wei Ling, Jun, Priya, Daniel, Siti, Ming, Arjun + 5 more"*. Only Going names are shown; Not going stays private.

### Organiser's control panel (private chat)

After posting, the organiser gets one private message that **updates itself**:

```
⚙️ Badminton · Sat 17 Oct · 2–4pm
✅ 12/15 going

[See names]  [Edit]  [Cancel event]
```

- **See names** expands the same message to list Going and Not going.
- **Edit** and **Cancel event** follow Feature 6.
- No private messages are sent when people change their RSVP.
- Events they organise appear in `/plans` with ⚙️.

### After the event

```
📣 Badminton · ended
Sat 17 Oct · 2–4pm · 12 went
```

The buttons are removed.

### Changes from current (`group_events.py`, `keyboards.py`, `messages.py`)

| Current | New | Priority |
| --- | --- | --- |
| Buttons: Going / Maybe / Can't, Am I free? / Save, Edit / Cancel event / Nudge | Going / Not going, Am I free? / Who's going? | P1 |
| "Going 12 · Maybe 3 · Can't 2" plus a line about Am I free? | "✅ 12/15 going" | P1 |
| `/event` replies to a message in the group | `/event` → describe it in private chat → preview → Post | P2 |
| Organiser actions on the group card | Private control panel that updates itself | P1 |
| No "Who's going?" | New popup with Going names | P1 |
| Card stays the same after the event | "ended" state, buttons removed | P2 |

### Later

"Still coming?" check the day before · capacity limit with a waitlist · co-organisers · recurring events · attendance export.

---

## Setup and other screens

### `/start`

**A. New user** (one message, one optional step):

```
👋 Hi Wei Ling! I'm AmIFree, your plan assistant.

📩 Forward me any plan, and I'll check if you're free and save it
🔔 I'll remind you the night before
👥 Add me to a group to find a time that works for everyone

📚 To start, paste your NUSMods or personal calendar link.
🔒 I never read your group chats.

[Open NUSMods]  [Personal calendar link?]  [Skip]
```

- **Open NUSMods** is a link button to the NUSMods website.
- **Personal calendar link?** expands the same message:

  ```
  📅 Google Calendar: Settings → your calendar → "Secret address in iCal format" → Copy
  🍎 Apple Calendar: Share calendar → Public calendar → Copy link
  ```

- After a link is pasted, the message updates to: `✅ Schedule added. Forward me any plan to start.`
- After Skip: `✅ All set. Forward me any plan to start. Add your schedule anytime in /settings.`
- Wrong link: `That's not a NUSMods or personal calendar link. Try again or tap Skip.`
- Last semester's NUSMods link: `That's last semester's timetable. Please send your current one.`

**B. Arriving from a group card** (deep link `t.me/<bot>?start=<event>` after tapping Am I free? or Going). The answer comes first; setup is optional:

```
✅ No clashes found
CS3216 meeting · Tue 13 Oct · 7–9pm

📚 Add your NUSMods or personal calendar link to check your schedule too.

[Going]  [Not going]
[Open NUSMods]
```

If they paste a link afterwards, the answer is re-checked and updated in the same message. If they arrived by tapping Going, the first line reads `✅ You're going`. Going / Not going update the group card.

**C. Already set up:**

```
You're all set ✅ Forward me a plan, or use /plans · /busy · /free
```

**New semester** (once, the first time they use the bot):

```
📚 New semester! Paste your new NUSMods link.

[Open NUSMods]  [Later]
```

### `/plans`

```
🗓 Upcoming

Thu 15 Oct
1  8pm  Badminton · UTSRC
2  6–10pm  🚫 Work

Sat 17 Oct
3  2–4pm  CS3216 meeting ⚙️
4  7pm  Dinner with Jun · 📍 ?
5  8pm  Badminton ? (Tue or Thu)

No date yet
6  Supper with hall friends

[Check event]  [Show more]
```

- Shows the next 7 days. Includes saved plans, group events marked Going, blocked times (🚫), events they organise (⚙️), pending plans and plans with missing details. Classes are not included.
- **Show more** adds the next 7 days to the bottom of the same message. Numbering continues. The button disappears when there is nothing further ahead. Stop expanding before Telegram's 4,096-character limit (about 4 weeks).
- **Check event** changes the buttons to `[1] [2] [3] [4] [5] [6]` and `[Back]`. Tapping a number opens that item in the same message:

| Item | Buttons |
| --- | --- |
| Saved plan | [Edit] [Remove] [Back] |
| One-off block | [Edit] [Remove] [Back] |
| Weekly block | [Edit] [Skip this week] [Remove] [Back] |
| Group event (going) | [Not going] [Back] |
| Event they organise | Control panel: [See names] [Edit] [Cancel event] [Back] |
| Pending plan | [Tue] [Thu] [Not happening] |

- **Remove** doesn't ask for confirmation. It shows `🗑 Removed · Badminton` with **[Undo]**. **Cancel event** asks for confirmation because it affects others.
- Empty: `🗓 Nothing coming up. Forward me a plan to add one.`

### `/settings`

All sections open in the same message, with [Back] to return.

```
⚙️ Settings

📚 Schedule: NUSMods ✓ · Personal calendar not added
🔔 Reminders: 9pm · 30 min before

[Schedule]  [Reminders]  [Privacy]
```

**Schedule**

```
📚 Your schedule

NUSMods: Sem 1 · 5 modules ✓
Personal calendar: not added

Paste a NUSMods or personal calendar link to add or replace one.

[Open NUSMods]  [Personal calendar link?]
[Remove NUSMods]  [Back]
```

Pasting a link updates the screen straight away (e.g. `Personal calendar: Google ✓`). The Remove buttons only appear for sources that have been added.

**Reminders:** as in Feature 5, plus [Back].

**Privacy**

```
🔒 Privacy

• I never read your group chats
• Others only see if you're free or busy, never your plans
• I keep event details, not your messages

[Delete my data]  [Back]
```

**Delete my data:**

```
⚠️ Delete everything?
Your plans, blocks, schedule and settings will be removed. This can't be undone.

[Delete everything]  [Cancel]
```

Then: `🗑 Your data is deleted. Send /start to set up again.` Events they organised stay in their groups for the other members, with no organiser attached.

### `/help`

```
💡 How to use AmIFree

📩 Forward or type a plan to check and save it
🗓 /plans · what's coming up
🚫 /busy sat 2-6pm · block time
🔍 /free fri 7pm · check if you're free
⚙️ /settings · schedule, reminders, privacy

👥 In groups
📣 /event · plan an event and find a time
Add me to the group first.
```

### Personal calendar links (new backend feature)

Students can paste a personal calendar link in iCal (.ics) format from Google, Apple or Outlook. The bot reads busy times from it and treats them like NUSMods classes: counted in clash checks, Am I free?, `/free` and the availability grid, but never shown to others. This avoids Google sign-in, Google's app verification and its 100-user cap for unverified apps. 78% of survey respondents use a calendar app daily or a few times a week.

### Changes from current

| Current | New | Priority |
| --- | --- | --- |
| Welcome: "Forward me a plan… Optional: send your NUSMods Share/Sync link…" | New welcome (A) with [Open NUSMods] [Personal calendar link?] [Skip] | P1 |
| "Setup complete. Imported 42 class/exam busy periods… Coverage: …" | `✅ Schedule added. Forward me any plan to start.` | P1 |
| Returning users: "You're ready. Timetable: …" (several lines) | One line (C) | P1 |
| Group-card deep link | Answer first, setup optional (B) | P1 |
| Google Calendar and other calendars not connected | Personal calendar links (iCal) | P1 |
| `/plans`, `/blocks` and `/events` are separate lists with Previous / Next | One `/plans` list with [Check event] and [Show more] | P1 |
| Long help text | Short `/help` above | P1 |
| `/reminders` on/off and `/deletemydata` | Inside `/settings` (old commands kept as hidden shortcuts) | P2 |
| No new-semester prompt; no "No date yet" or pending sections in `/plans`; no Remove NUSMods | New | P2 |

---

## Open questions for the team

1. **Personal plans:** a saved personal plan simply means going (no Going / Maybe / Can't). Group events keep Going / Not going. This changes how plan statuses are stored. OK?
2. **`/whosfree`:** until the Mini App availability grid is ready, do we keep the current `/whosfree` poll as a temporary version?
3. **Personal calendar links (iCal):** marked P1. Is this doable in the time we have?

## Telegram constraints to keep in mind

- A bot **can't message someone privately until they've started it.** Popups and group cards are the fallback.
- Bots **can't list a group's members.** They can only check whether a known user is in the group.
- **Popups (callback alerts) are limited to 200 characters.**
- **Mini App buttons (`web_app`) only work in private chats.** In groups, use a normal link to the Mini App.
- **Editing a message doesn't notify anyone,** which is why cards update in place.
