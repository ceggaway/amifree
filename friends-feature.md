# Friends feature: build spec

**For:** whoever builds this into the AmIFree bot (and their coding assistant).
**Status:** UX agreed by the team. Build Part 1, then Part 2.
**Why:** In our survey (61 NUS students), "none of my friends were going" was the single most common reason for missing an event (33%). In user tests, two of six students asked for a way to send an event to a friend and see if they are going. This feature turns "I'd go if someone came with me" into a plan in a couple of taps.

The feature has two parts:

- **Part 1: Ask a friend.** The user sends an invite card for one of their saved events into any Telegram chat. The friend answers with one tap, without needing AmIFree. The user is told the answer automatically.
- **Part 2: See which friends are going.** When two AmIFree friends forward the same event to the bot, each sees whether the other is going, and gets told when the other commits.

---

## 0. Ground rules for the whole feature

1. **Minimal taps.** The user should need 2 taps to invite, the friend 1 tap to reply. No typing.
2. **The friend never has to install or start anything to reply.**
3. **Good news is pushed, not pulled.** The user is told by direct message when a friend replies. Telegram does not notify anyone when a message is edited, so updating the card alone is not enough.
4. **Only answers are shared, never schedules.** A friend's timetable, calendar or other plans are never shown to anyone. Clash checks stay private to the person they belong to (same rule as the existing "Am I free?" popup).
5. **Same words everywhere.** Use **Going**, **Maybe**, **Can't make it**, as on the existing personal event card. Invites use only **Going** and **Can't make it**, because someone is waiting for a clear answer.
6. **Keep the current bot's style.** Short messages, one emoji at the start of a line at most, dates as `Fri 24 Oct, 7–9pm` (use the existing `time_range()` and `clock()` helpers in `app/bot/messages.py`). The copy below is the target wording. Small edits to fit the existing helpers are fine, but do not add extra lines or buttons.
7. **Do not break existing flows.** The personal event card, `/event` group cards, polls and reminders keep working as they do now. This feature adds to them.

---

## 1. Telegram mechanics (read before designing)

These are fixed by the Telegram Bot API and shape the design:

| Fact | Consequence |
|---|---|
| A bot cannot message a user who has never started it. | Friends who don't use AmIFree can only be reached through a message the user sends. That is why invites use **inline mode**. |
| An inline message is sent **from the user's own account** ("Rachel ... via @AmIFreeBot"), and its buttons work for everyone in that chat, including people who never started the bot. | The friend can reply with one tap without starting the bot. |
| `chosen_inline_result` and the inline query **do not reveal which chat or person** the invite was sent to. `InlineQuery.chat_type` only says private chat vs group. | The bot does not know who was invited until they tap a button. Copy must not name the friend before they reply (e.g. "Invite sent", not "Invite sent to Wei Ling"). |
| A callback from a button on an inline message has `callback_query.message = None` and an `inline_message_id` instead. Editing uses `edit_message_text(inline_message_id=...)`. | The current `callback()` path in `app/bot/handlers.py` assumes a message and chat. Route invite callbacks (their own prefix, e.g. `fi:`) through a separate handler. |
| The callback gives the tapper's Telegram user id and name. | The bot can check whether the tapper already uses AmIFree and personalise the popup. |
| `answerCallbackQuery` text is limited to 200 characters and is only seen by the tapper. | Use it for personal info (e.g. "Saved to your plans"). |
| Deep links `t.me/<bot>?start=<payload>` open the bot with a Start button. The payload is at most 64 characters of `A-Za-z0-9_-`. | Use `start=inv_<invite_id>` to sign a friend up with the event already saved. |
| `InlineKeyboardButton(switch_inline_query_chosen_chat=SwitchInlineQueryChosenChat(query=..., allow_user_chats=True, allow_group_chats=True))` opens Telegram's chat picker with the inline query pre-filled. | "Ask a friend" is one tap to open the picker, one tap to choose the chat. |

**BotFather setup (one-off):**
- `/setinline` on, placeholder text: `Pick a plan to share`
- `/setinlinefeedback` → 100% (needed to receive `chosen_inline_result` and the `inline_message_id`). Inline results must carry a `reply_markup`, otherwise no `inline_message_id` is returned.
- `/setdescription` (shown to new users before they tap Start):
  `AmIFree saves events from your Telegram chats, checks if you're free, and helps you go with friends.`

---

## 2. Concepts and data

Names below are suggestions. Fit them to the existing schema style in `app/db/schema.sql` and `app/db/migrations/` (add a new numbered migration, update `all_migrations.sql` the way the repo already does, keep RLS and grants consistent with other tables).

### 2.1 Invite
One invite = one saved event shared by one user into one chat. Holds:
- inviter user id, event id, and a snapshot of title / start / end / location (so the card can be redrawn)
- `chat_type`: `private` or `group` (from `InlineQuery.chat_type`)
- the `inline_message_id`(s) of the card(s) sent (the original and any nudge cards)
- `nudge_sent_at`, `created_at`, `closed` (event started or cancelled)

### 2.2 Invite response
One row per person who tapped: invite id, tapper's Telegram user id, AmIFree user id if they have one, display name (first name), status `GOING` or `DECLINED`, updated time. Unique on (invite, Telegram user).

### 2.3 AmIFree friends
Two AmIFree users become **AmIFree friends** automatically when one taps **Going** on the other's invite (in any chat), or when a non-user taps Going and later signs up through that invite's "Get a reminder" link. Store as an unordered pair, created once. No contact lists, no friend requests.

### 2.4 Sharing setting
Per user: `share_going_with_friends` = `null` (not asked yet), `true` or `false`. Asked once (see 4.1).

### 2.5 Hiding one event
Per saved event: `hidden_from_friends` boolean, default false. Set from the event's Edit menu.

### 2.6 Same-event key
Part 2 needs to know when two users saved the same real-world event. Each user has their own `events` row (`source_message_id` is the user's own message, so it cannot be used across users). Add an `origin_key` to `events`, set at forward time:

1. **Exact:** build it from `message.forward_origin`:
   - `MessageOriginChannel`: channel chat id + `message_id`
   - `MessageOriginUser`: original sender user id + original `date`
   - `MessageOriginChat`: sender chat id + original `date`
   - `MessageOriginHiddenUser`: sender name + original `date`
2. **Fallback (no forward origin, or different posts):** treat two events as the same only if the normalised title (lowercase, punctuation and extra spaces removed) **and** the start time are identical, and the dates match.
3. **If unsure, no match.** A missed match is acceptable, a wrong match is not.

---

## 3. Part 1: Ask a friend

### 3.1 Entry points

**A. From the event card.** Add one button to the existing personal event card in `app/bot/keyboards.py` (`event_keyboard`): `👋 Ask a friend`. It uses `switch_inline_query_chosen_chat` with query `inv <event_id>`. Show it for events the user has saved (`SAVED`, `GOING`, `MAYBE`), not for `PREVIEW`, `DECLINED` or `CANCELLED`. If showing it on every card feels crowded, show it once the user has tapped Going or Maybe.

Example after the user taps Going on the existing card (keep the existing verdict line, details and checked line; this adds one line and one button):

```
✅ No clashes found

Hackathon Talk
Fri, 24 Oct 2026 · 19:00 → 24 Oct 21:00 SGT
Location: UTown Auditorium

Don't want to go alone? Ask a friend 👇
[👋 Ask a friend]
[Maybe] [Can't make it]  [Edit]
```

**B. From any chat.** Typing `@AmIFreeBot` in a chat shows the user's upcoming saved events (Going first, then Maybe, then Saved, max 10, soonest first). Tapping one sends the invite. An empty result shows a single item: `No upcoming plans. Forward an event to @AmIFreeBot first.`

### 3.2 The invite card

The wording depends on `InlineQuery.chat_type`. Buttons: `✅ Going` `❌ Can't make it`.

**Private chat:**
```
Going to Hackathon Talk, come with me? 👀
📅 Fri 24 Oct, 7–9pm
📍 UTown Auditorium
Your reply goes to Rachel.

[✅ Going]   [❌ Can't make it]
```

**Group chat:**
```
Anyone keen for Hall Food Fest with me? 👀
📅 Sat 25 Oct, 6–9pm
📍 Dining Hall
Tap to join 👇

✅ Going (1): Rachel
[✅ Going]   [❌ Can't make it]
[🔔 Get a reminder]
```
(Leave out the 📍 line if there is no location.)

**Confirmation to the inviter (bot DM, after `chosen_inline_result`):**
```
📨 Invite sent
I'll let you know when they reply.
[👋 Ask another friend]
```
`Ask another friend` opens the chat picker again for the same event.

### 3.3 Scenario 1: the friend taps Going (private chat)

What happens depends on whether the tapper already uses AmIFree.

**Tapper already uses AmIFree:** save the event to their plans as `GOING` (create their own `events` row with the same `origin_key`; if they already have it, set it to `GOING`). Create the AmIFree friendship.
- Popup (only they see it): `🎉 You're going with Rachel! Saved to your plans.`
- Card is edited to the event details only, with **no buttons** and **no "who's going" line**:
  ```
  Hackathon Talk
  📅 Fri 24 Oct, 7–9pm
  📍 UTown Auditorium
  ```

**Tapper does not use AmIFree:** record the response (no account is created).
- Popup: `🎉 You're going with Rachel!`
- Card is edited to:
  ```
  Hackathon Talk
  📅 Fri 24 Oct, 7–9pm
  📍 UTown Auditorium

  🔔 Want a reminder? Sign up for AmIFree, it's free.
  [🔔 Get a reminder]
  ```
  `Get a reminder` is a URL button to `t.me/<bot>?start=inv_<invite_id>`. See 3.7.

**Inviter gets (bot DM), in both cases:**
```
🎉 Wei Ling is in!
You're going to Hackathon Talk together.
📅 Fri 24 Oct, 7pm
```

### 3.4 Scenario 2: the friend taps Can't make it (private chat)

- Card is edited to this text only, with no buttons:
  ```
  Thanks for letting us know. We'll tell Rachel you can't make it.
  ```
- Inviter gets:
  ```
  😕 Wei Ling can't make it
  Hackathon Talk · Fri 24 Oct, 7pm
  Want to ask someone else?

  [👋 Ask another friend]
  [Go solo]   [I'll skip it]
  ```
  - `Ask another friend`: opens the chat picker for the same event.
  - `Go solo`: inviter's event set to `GOING` (if not already).
  - `I'll skip it`: inviter's event set to `DECLINED`.
  - After any tap, edit this message to remove the buttons and show the result in one line, e.g. `✅ You're going` / `❌ Skipped`.

### 3.5 Scenario 3: nobody replies

The bot cannot see who the invite went to (section 1), so the follow-up always goes to the **inviter**.

- **When:** once per invite, if it has **no responses at all**, at the halfway point between sending and the event start, but **no later than 24 hours before** the event. If the event is less than 3 hours away when the invite is sent, skip the follow-up.
- **Inviter gets:**
  ```
  ⏳ No reply yet to your Hackathon Talk invite
  It's on Fri, 7pm. Send a quick nudge, or ask someone else.

  [🔔 Send a nudge]   [👋 Ask another friend]
  ```
- `Send a nudge` opens the chat picker with a nudge query (e.g. `nudge <invite_id>`). The nudge card belongs to the **same invite**, so replies count the same way:
  ```
  Still keen for Hackathon Talk? 👀
  📅 Fri 24 Oct, 7–9pm

  [✅ Going]   [❌ Can't make it]
  ```
- **When the event starts:** edit every card of the invite that still has buttons to:
  ```
  Hackathon Talk
  📅 Fri 24 Oct, 7–9pm
  This event has started.
  ```
  and remove the buttons. Mark the invite closed.

Hook the follow-up and closing into the existing scheduled jobs (`app/services/jobs.py`, the periodic runner in `app/main.py`).

### 3.6 Scenario 4: invite sent to a group chat

- The card keeps its buttons so more people can join. Each Going tap adds the person's first name to the `✅ Going (n):` line (inviter first, then in tap order).
- Names of people who tapped Can't make it are **never shown on the group card**.
- Popups:
  - existing AmIFree user taps Going: `🎉 You're going! Saved to your plans.` (save to their plans as in 3.3, create the friendship)
  - non-user taps Going: `🎉 You're going! Tap 🔔 Get a reminder to sign up for AmIFree.`
  - Can't make it: `Got it. We'll let Rachel know.`
  - tapping the same answer again: no change, popup `You've already answered.`
  - people can change their answer in a group card
- Inviter gets one DM per person who joins:
  ```
  🙌 Jun Hao joined you
  Hall Food Fest · 3 going
  ```
- Declines in a group do **not** send a DM each. The inviter sees them in the event's details (`/plans` → event), e.g. `3 going · 2 can't make it`.
- If someone changes from Going to Can't make it, remove their name from the card and send the inviter: `😕 Jun Hao can't make it to Hall Food Fest anymore`.
- `🔔 Get a reminder` stays on the group card for everyone. For existing users it just confirms the event is in their plans.

### 3.7 Scenario 5: a friend signs up through "Get a reminder"

The deep link `start=inv_<invite_id>` runs when they tap Start. Hook it into the existing `/start` handling in `app/bot/onboarding.py`:

1. Create the user (existing `amifree_ensure_user`).
2. Save the invite's event to their plans as `GOING`.
3. Link their earlier invite response to the new user id, and create the AmIFree friendship with the inviter.
4. Send this instead of the normal welcome, then continue with the existing onboarding steps (timetable link etc.):
   ```
   👋 Welcome to AmIFree!
   I keep track of events from your chats, so you
   never miss one or have to go alone.

   ✅ Saved: Hackathon Talk
   📅 Fri 24 Oct, 7–9pm · 📍 UTown Auditorium
   🔔 I'll remind you the night before.

   Tip: forward any event here to save it.
   [📅 Link my timetable]
   ```
   The night-before reminder is the existing nightly summary (`app/services/reminders.py`). If the user has reminders off, leave out the 🔔 line.

If an existing user opens the link, skip the welcome and just confirm: `✅ Hackathon Talk is in your plans.`

### 3.8 Edge cases for Part 1

- **The inviter taps their own card:** popup `This is your invite 🙂`, nothing changes.
- **Someone taps after the event started:** popup `This event has already started.`
- **The inviter changes or cancels the event:** edit every card of every invite for it. Changed: redraw with the new details. Cancelled: card shows `❌ Hackathon Talk was cancelled` with no buttons, and friends who are Going and use AmIFree get the existing change alert.
- **The same friend answers two invites to the same event** (e.g. the original and a nudge): one answer per person per invite. The latest tap wins.
- **Rate limits:** reuse `app/services/ratelimit.py` for inline queries and invite callbacks.

---

## 4. Part 2: See which friends are going

When a user forwards an event, show which of their AmIFree friends saved the same event and whether they are going. Tell friends when someone commits.

### 4.1 Privacy question (asked once per user, ever)

Ask the first time **both** of these are true: the user answers Going, Maybe or Can't make it on any event, **and** they have at least one AmIFree friend. Never ask a user with no friends yet.

```
👥 Show friends when you're going?
AmIFree friends who save the same event will see
if you're going. Nothing else is shared.

[✅ Yes]   [No]
```

Rules:
- A friend's status is shown only if **both** users said Yes.
- Only for events **both** users have saved (same `origin_key`, section 2.6). Nobody can browse a friend's plans.
- Only `GOING` and `MAYBE` are ever shown. `SAVED` (undecided), `DECLINED` and `PREVIEW` are never shown.
- Events marked `hidden_from_friends` are never shown or notified.
- Add to `/settings` (or the closest existing settings command): `Show friends when you're going: On/Off`.
- Add to the event Edit menu: `🙈 Hide from friends` / `👁 Show to friends`.
- Update the privacy text in `HELP` (`app/bot/messages.py`) with one line: `If you turn on friend sharing, AmIFree friends who save the same event see whether you're going. Nothing else is shared.`

### 4.2 What the user sees when they forward an event

Keep the existing personal event card exactly as it is (verdict line, details, checked line, `Going / Maybe / Can't make it / Edit / Save` buttons). Add **one line** after the details when friends match:

```
✅ No clashes found

Career Fair
Wed, 29 Oct 2026 · 10:00 → 29 Oct 16:00 SGT
Location: MPSH

👥 Wei Ling is going

Checked: your Going plans, busy blocks and NUSMods timetable.
[Going] [Maybe]
[Can't make it] [Edit]
[Save]
```

The 👥 line:
- `👥 Wei Ling is going`
- `👥 Wei Ling might go` (friend answered Maybe)
- several friends, Going first, then Maybe: `👥 Wei Ling and Jun Hao are going · Mei might go`
- more than 3 names: `👥 Wei Ling, Jun Hao and 2 others are going`
- no line at all if no friend qualifies

### 4.3 Who gets notified when the user answers

Wei Ling saved the event earlier. Rachel answers now.

| Wei Ling's status | Rachel taps Going | Rachel taps Maybe | Rachel taps Can't make it |
|---|---|---|---|
| Going | Both told "both going" | No message | No message |
| Maybe | Wei Ling gets `[✅ I'm going too]` | No message | No message |
| Can't make it / Saved / not answered | No message | No message | No message |

**Wei Ling is Going, Rachel taps Going:**
- Rachel:
  ```
  🎉 You and Wei Ling are both going
  Career Fair · Wed 29 Oct, 10am
  ```
- Wei Ling:
  ```
  🎉 Rachel is going to Career Fair too
  Wed 29 Oct, 10am · MPSH
  ```

**Wei Ling said Maybe, Rachel taps Going:**
- Rachel:
  ```
  ✅ You're going
  👥 Wei Ling might go. I'll let you know if she joins.
  ```
- Wei Ling:
  ```
  🙌 Rachel is going to Career Fair
  Wed 29 Oct, 10am · MPSH

  [✅ I'm going too]
  ```
- If Wei Ling taps `I'm going too`: set her event to `GOING`, edit her message to `✅ You're going`, and send Rachel `🎉 Wei Ling is going too! You're both going to Career Fair · Wed 10am`.
- If Wei Ling ignores it, she stays Maybe and nothing else is sent.

**Several friends:** one message to Rachel listing everyone going (`🎉 You're going with Wei Ling and Jun Hao`), and the right message to each friend as in the table.

### 4.4 Changes of answer

Treat every change as a new answer:
- **Moving up to Going** (from Maybe, Can't make it or Saved): send the messages in 4.3.
- **Moving down from Going** (to Maybe or Can't make it): friends who are Going get one message:
  ```
  😕 Rachel can't make it to Career Fair anymore
  You're still going.

  [👋 Ask another friend]
  ```
  If Rachel moved to Maybe, the first line is `🤔 Rachel isn't sure about Career Fair anymore`.
- Any other change (e.g. Maybe to Can't make it): no messages.
- Do not send the same friend the same message about the same event twice. Keep a small log of sent friend notifications (or reuse the existing notifications table, `003_notifications.sql`) to deduplicate.

### 4.5 Night-before reminder

In the existing nightly summary (`app/services/reminders.py`), add the names of friends going (from Part 1 invites or Part 2 matches) to that event's line, only where sharing rules allow:

```
• 10:00–16:00 Career Fair @ MPSH, with Wei Ling and Jun Hao
```

### 4.6 When no friend status appears

The card looks exactly like today's card, and nobody is notified, if any of these is true:
- the two users are not AmIFree friends
- either user said No to sharing (or has not been asked yet)
- the friend hid the event
- the friend's status is Saved, Can't make it or Preview
- the events do not match under section 2.6

---

## 5. Build order

1. **Data:** migration for invites, invite responses, friendships, sharing setting, `hidden_from_friends`, `origin_key`.
2. **Inline mode:** `InlineQueryHandler`, `ChosenInlineResultHandler`, invite card text for private vs group, `👋 Ask a friend` button on the event card.
3. **Invite callbacks** (separate `fi:` handler for inline messages): Scenarios 1, 2 and 4, card edits, popups, inviter DMs.
4. **Deep link** `start=inv_<id>` (Scenario 5).
5. **Follow-up job and closing at event start** (Scenario 3), event change and cancel updates.
6. **Part 2:** `origin_key` at forward time, privacy question, 👥 line on the card, notifications (4.3, 4.4), settings and hide toggle, nightly summary names.

Part 1 is useful on its own and should ship first.

---

## 6. Acceptance checks

Write tests in `tests/` in the same style as the existing ones (for example `tests/test_group_audit.py`, `tests/test_card_refresh.py`). At minimum:

**Part 1**
- [ ] `👋 Ask a friend` appears on saved events only, and opens the chat picker with the event loaded.
- [ ] Typing `@AmIFreeBot` lists upcoming saved events; an empty list shows the "forward an event first" item.
- [ ] Private and group invites use the right wording (based on `chat_type`).
- [ ] A friend who never started the bot can tap Going or Can't make it, and it is recorded.
- [ ] Private chat, Going: existing user gets the event saved as GOING and the "Saved to your plans" popup. Card shows details only, no buttons, no names.
- [ ] Private chat, Going, non-user: card shows the sign-up line and `Get a reminder` link.
- [ ] Private chat, Can't make it: card shows only "Thanks for letting us know. We'll tell Rachel you can't make it."
- [ ] The inviter gets "Wei Ling is in!" or "Wei Ling can't make it" with the right buttons, and `Go solo` and `I'll skip it` set the right status.
- [ ] Group card lists only people going, updates on each tap, never shows decliners. The inviter gets one DM per join and none per decline.
- [ ] `start=inv_<id>` signs the user up, saves the event as GOING, creates the friendship and shows the welcome text.
- [ ] Follow-up fires once, only with zero responses, at the right time, never later than 24 hours before, and is skipped for events under 3 hours away.
- [ ] Nudge replies count towards the original invite.
- [ ] Cards lose their buttons and say "This event has started" once the event starts. Changed or cancelled events update all cards.
- [ ] The inviter tapping their own card changes nothing.

**Part 2**
- [ ] The privacy question is asked once, only when the user has at least one AmIFree friend.
- [ ] A status is shown only when both users said Yes, both saved the same event, the status is Going or Maybe, and the event is not hidden.
- [ ] Same-event matching: the same forwarded original matches; different posts match only on identical normalised title, date and start time; anything else does not match.
- [ ] The notification table in 4.3 and the change rules in 4.4 hold for every combination, with no duplicate messages.
- [ ] `I'm going too` sets GOING and notifies the other person.
- [ ] Turning sharing off, or hiding an event, removes the 👥 line and stops notifications immediately.
- [ ] The nightly summary shows friend names only where sharing rules allow.

**Privacy (both parts)**
- [ ] No message or popup ever shows another person's timetable, calendar, busy blocks or unrelated plans.
- [ ] `/deletemydata` also deletes the user's invites, responses, friendships and sharing setting.

---

## 7. Out of scope for now

- A friend list browser, or a feed of events friends are going to.
- Friend requests or importing contacts.
- Changing the existing `/event` group card buttons.
- Matching events across users using AI.
