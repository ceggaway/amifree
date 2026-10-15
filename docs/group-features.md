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
| Stats and export | `GroupEventService.stats`, `export` | `group_plans`, `group_members.display_name` |
| Event detection | `app/services/detection.py` | `group_settings`, `event_suggestions` |

## What runs on the tick

`Notifier.run()` (every 60 seconds in-process, or `POST /internal/jobs/run`) now also:

1. queues "still coming?" messages for events starting in 4–30 hours;
2. redraws finished cards as ended;
3. posts the cards of series sessions starting within four days;
4. opens polls whose reply-by time has passed (when three schedules are known);
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
- **Pro is a flag, not a payment.** `group_plans.tier` or `PRO_CHAT_IDS`. Payments can set the
  same row later.
- **Manual attendance ticking is not built.** Attendance is self check-in ("I'm here").

## Not built yet

- Members editing their own availability grid in the Mini App (the heatmap is read-only).
- The organiser ticking names after the event.
- Telegram Stars (or any payment) for Clubs Pro.
