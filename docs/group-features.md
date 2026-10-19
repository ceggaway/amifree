# Group features

What each group feature does, where it lives, and how to run it. Operators should also read
the Group features table in the README.

## Where things live

| Feature | Code | Data (migration 009 unless noted) |
| --- | --- | --- |
| Event cards, Who's going?, check-in, ended state | `app/services/group_events.py`, `app/bot/messages.py::group_card` | `group_events.ended_at`, `attendance` |
| Edit once, everyone updates | `amifree_update_group_event` (migrations 002, 003, 009) | `event_changes`, `notification_logs` |
| `/whosfree` poll, reply-by deadline, auto-suggest | `app/services/polls.py` | `polls.deadline_at` |
| Auto-chase | `app/services/chase.py` | `notification_logs` (kind `NUDGE`) |
| "Still coming?" | `GroupEventService.enqueue_confirmations`, `confirm_attendance` | `attendance_checks`, kind `CONFIRM` |
| Recurring events | `app/services/recurrence.py`, `GroupEventService.create_series` | `event_series`, `group_events.series_id` |
| Heatmap | `PollService.heatmap`, `GET /api/polls/{id}/heatmap`, `web/src/pages/Heatmap.tsx` | none (computed) |
| Stats, export, attendance list | `GroupEventService.stats`, `export`, `attendance_panel` | `attendance`, `group_members.display_name` |
| Clubs Pro (Telegram Stars) | `app/services/pro.py`, payment handlers in `app/bot/handlers.py` | `group_plans`, `pro_payments` (migration 010) |
| Members' own availability | `PollService.my_grid`, `save_grid`, `web/src/pages/MyTimes.tsx` | `poll_availability` (migration 010) |
| Usual weekly times | `app/services/usual_times.py`, `/usual` | `weekly_availability` (migration 011) |
| Event detection | `app/services/detection.py` | `group_settings`, `event_suggestions` (author: migration 014) |

## The card's buttons

Going / Maybe / Can't, Am I free?, Who's going?, I'm here, and **⚙️ Manage**. Manage opens the
organiser's private menu (`manage_keyboard`: Edit, Cancel event, Nudge members, Who's confirmed,
Mark who came); anyone else is refused privately. Cards posted before this change still carry
the old Edit / Cancel event / Nudge / Status / Save buttons, and those keep working. `/help`
(and `/start`) inside a group shows a short guide, and is listed in the group command menu.

## The Mini App for a group

`app/services/group_hub.py` (`GroupHub`) runs the group pages of the Mini App, and
`app/services/app_links.py` builds their links. Routes in `app/api/routes.py`:

| Route | Who | Does |
| --- | --- | --- |
| `GET /api/groups/{code}` | members | upcoming events (one per series) and open polls |
| `POST /api/groups/{code}/events` | members | posts a card, or a weekly series (`weekdays`, `until`) |
| `POST /api/groups/{code}/polls` | members | posts a `/whosfree` poll |
| `GET /api/group-events/{id}` | members | the event, your answer, your clash check, who's going |
| `POST …/rsvp`, `…/check-in`, `…/going-to-all` | members | answer, check in, Going to every session |
| `PATCH …`, `POST …/cancel`, `…/series`, `…/nudge`, `PUT …/attendance` | organiser | edit, cancel, apply to later sessions, nudge, mark who came |

`{code}` is the chat id as `n<digits>` (negative) or `p<digits>`. Membership is asked of Telegram
and cached for five minutes (30 seconds for a "no"). Someone it lets in is recorded as a group
member, with the first name from their signed sign-in, so headcounts, nudges and "Who's going?"
include people who only ever use the app. Forms carry a `request_id`, so a double tap posts one
card. A card the bot can't post (removed from the group) is cancelled again, so nothing silent
is left behind.

## What runs on the tick

`Notifier.run()` (every 60 seconds in-process, or `POST /internal/jobs/run`) now also:

1. queues "still coming?" messages for events starting in 4–30 hours;
2. redraws finished cards as ended;
3. posts the cards of series sessions starting within four days;
4. opens polls whose reply-by time has passed (when three schedules are known; otherwise the
   card says why no slots appeared);
5. removes unanswered detection suggestions older than 10 minutes.

Each step is isolated: one failing is logged and the rest still run.

## Operational notes

- Run one worker. Job runs take turns inside a process (an in-process lock), and Telegram update
  de-duplication is per database, but card posting and the edit throttle are per process.
- A session whose card could not be posted because the bot was removed from the group gets
  `message_id = -1`. It is never retried, chased, asked about or redrawn.
- Changing or stopping many sessions of a series sends each member one summary message, not one
  per session.
- Card edits go through a throttle (`CARD_EDIT_INTERVAL_SECONDS`), and Telegram's
  "retry after" replies are honoured, so a burst of RSVPs never shows an error to members.
- Commands addressed to another bot (`/stats@OtherBot`) are ignored.
- Event detection never replies to, or retries, an ordinary message, even when something fails.

## Decisions worth knowing

- **RSVP buttons stay Going / Maybe / Can't.** The UX doc proposes Going / Not going; the
  existing flows and tests use Maybe, so it was kept.
- **Poll auto-suggest happens at the reply-by time, not at the third join.** Suggesting the
  moment three people join would fix the slots before the rest of the group has had a chance.
  The organiser's Suggest button still works at any time.
- **Fill mine is the one-tap confirm.** It answers every slot from the member's schedule in
  one tap; they can change single answers afterwards.
- **A series is materialised.** Every session is a normal card row, so RSVPs, chase,
  "still coming?", attendance and export need no special cases. Sessions without a posted
  card are never chased or asked about.
- **Everything is free first.** `EXPORT_REQUIRES_PRO=false` by default. The Stars purchase flow is
  built and tested, so turning it on needs only that setting (and a price in `PRO_PRICE_STARS`).
- **Attendance is two ways:** members tap **I'm here**, and the organiser can tick names in a
  private panel (`/attendance`), including "everyone who said Going came".
- **Only the author or an admin decides a detected plan.** Make event card and Ignore work for
  the person who wrote the message and for group admins; anyone else gets a private refusal.
  The author is kept as a Telegram id (they may never have used AmIFree) for the suggestion's
  10 minutes.
- **A submitted grid replaces the schedule for those cells.** Cells a member leaves unticked
  count as busy, on top of what their schedule blocks. Ticked cells the schedule blocks are
  dropped on save.

## Monitoring

Set `ALERT_TELEGRAM_ID` to your Telegram id. The bot then messages you when a background step
(`confirmations`, `ended`, `posted`, `suggested`, `cleaned`) fails three ticks in a row, or when
the message queue's oldest item is more than 15 minutes old. Each alert repeats at most once per
`ALERT_COOLDOWN_MINUTES`. `GET /internal/jobs/stats` shows queue counts and `step_failures`.

## Scale

List queries read in pages of 1,000 (up to 50 pages), so a group above 1,000 active members is
read in full. Per-person nudges and DMs are sent one at a time, so a very large group's first
chase takes a while but never drops anyone.

## Refunds

Stars payments are recorded in `pro_payments` (one row per Telegram charge). A buyer sends
`/refund` in private chat: the newest payment inside `REFUND_WINDOW_DAYS` is returned with
Telegram's `refundStarPayment`, marked refunded once, and the days it bought come off the
group's Pro time. A payment that cannot be matched to a group is refunded at once. If Telegram
refuses, nothing changes and the buyer can try again; "already refunded" counts as done.

## Not built yet

- Nothing from the original list. Possible next steps: editing usual times in the Mini App,
  and one-off exceptions to them ("not this Tuesday").
