# AmIFree final report: project references (P1–P11)

All links point to commit `2bd435a` on `main` (18 Nov 2026), so they show exactly the code the report describes even if the repo changes later. The repo is public, so examiners can open them without signing in.

Base: `https://github.com/ceggaway/amifree/blob/2bd435a90783e26cfe3716cf23a65062d0a38202/`

## Before you paste: check each label

P1, P2 and P11 match what your notes say they cover. **P3–P10 are my best guess at what Sections 3–4 cite.** For every `[P#]` in the report, check that the sentence is about that row's topic. If it isn't, swap in the row that fits, then renumber so the list runs in order of first use.

| Label | Topic the sentence should be about |
|---|---|
| P1 | Overall product, workflows, privacy design |
| P2 | Telegram bot behaviour, group interactions, opt-in |
| P3 | Turning text / forwards / images / voice into event cards |
| P4 | Clash checking against timetable and plans |
| P5 | NUSMods timetable import |
| P6 | Group event cards, RSVPs, headcount |
| P7 | `/whosfree` availability polls |
| P8 | Automatic nudges / chasing non-responders |
| P9 | Mini App and its authentication |
| P10 | System architecture and database |
| P11 | Calendar privacy, encrypted links, data deletion |

## Reference list (paste into the report)

[P1] AmIFree Development Team. (2026). *AmIFree: Telegram-based scheduling assistant — README* [Source code documentation]. GitHub. https://github.com/ceggaway/amifree/blob/2bd435a90783e26cfe3716cf23a65062d0a38202/README.md

[P2] AmIFree Development Team. (2026). *Telegram bot handlers (app/bot/handlers.py) and group features (docs/group-features.md)* [Source code]. GitHub. https://github.com/ceggaway/amifree/blob/2bd435a90783e26cfe3716cf23a65062d0a38202/app/bot/handlers.py ; https://github.com/ceggaway/amifree/blob/2bd435a90783e26cfe3716cf23a65062d0a38202/docs/group-features.md

[P3] AmIFree Development Team. (2026). *Event extraction from text, images and voice (app/services/event_extractor.py, image_reader.py, voice_reader.py)* [Source code]. GitHub. https://github.com/ceggaway/amifree/tree/2bd435a90783e26cfe3716cf23a65062d0a38202/app/services

[P4] AmIFree Development Team. (2026). *Clash checking (app/services/availability.py)* [Source code]. GitHub. https://github.com/ceggaway/amifree/blob/2bd435a90783e26cfe3716cf23a65062d0a38202/app/services/availability.py#L37

[P5] AmIFree Development Team. (2026). *NUSMods timetable import (app/services/nusmods.py)* [Source code]. GitHub. https://github.com/ceggaway/amifree/blob/2bd435a90783e26cfe3716cf23a65062d0a38202/app/services/nusmods.py

[P6] AmIFree Development Team. (2026). *Group event cards and RSVPs (app/services/group_events.py)* [Source code]. GitHub. https://github.com/ceggaway/amifree/blob/2bd435a90783e26cfe3716cf23a65062d0a38202/app/services/group_events.py

[P7] AmIFree Development Team. (2026). *Availability polls for /whosfree (app/services/polls.py)* [Source code]. GitHub. https://github.com/ceggaway/amifree/blob/2bd435a90783e26cfe3716cf23a65062d0a38202/app/services/polls.py

[P8] AmIFree Development Team. (2026). *Automatic nudges for non-responders (app/services/chase.py)* [Source code]. GitHub. https://github.com/ceggaway/amifree/blob/2bd435a90783e26cfe3716cf23a65062d0a38202/app/services/chase.py

[P9] AmIFree Development Team. (2026). *Telegram Mini App (web/src) and Mini App authentication (app/api/auth.py)* [Source code]. GitHub. https://github.com/ceggaway/amifree/tree/2bd435a90783e26cfe3716cf23a65062d0a38202/web/src ; https://github.com/ceggaway/amifree/blob/2bd435a90783e26cfe3716cf23a65062d0a38202/app/api/auth.py#L19

[P10] AmIFree Development Team. (2026). *Backend entry point and database schema (app/main.py, app/db/schema.sql)* [Source code]. GitHub. https://github.com/ceggaway/amifree/blob/2bd435a90783e26cfe3716cf23a65062d0a38202/app/main.py ; https://github.com/ceggaway/amifree/blob/2bd435a90783e26cfe3716cf23a65062d0a38202/app/db/schema.sql

[P11] AmIFree Development Team. (2026). *Privacy and security: encrypted calendar links (app/services/personal_calendar.py) and data deletion (README, "Privacy by design")* [Source code]. GitHub. https://github.com/ceggaway/amifree/blob/2bd435a90783e26cfe3716cf23a65062d0a38202/app/services/personal_calendar.py#L85-L106 ; https://github.com/ceggaway/amifree/blob/2bd435a90783e26cfe3716cf23a65062d0a38202/README.md#privacy-by-design

## What the code backs up (safe to claim)

- **Opt-in before schedules count in `/whosfree`** (P1, P2, P7): the README says a schedule is read only after the member fills in availability or taps *Count me in*.
- **Group cards show counts, not names or reasons** (P1, P2), except *Who's going?*, which lists people who tapped Going.
- **Privacy mode stays on**: the bot doesn't see ordinary group messages unless an admin turns on `/detect on` (P1, P2).
- **Personal calendar links are stored encrypted** with Fernet, and only busy times are read, never titles, places or attendees (P11, `personal_calendar.py`).
- **`/deletemydata` removes** the user's timetable, plans, busy blocks, poll answers, names, notifications and analytics (P1, P11).
- **Mini App requests are verified** against Telegram's signed init data (P9, `auth.py` line 19).
- **Clash checking is deterministic Python; no LLM decides availability** (P1, P4).

## Notes

Commit `2bd435a` includes the README fix: the status line says AmIFree is live with real users, and the calendar line no longer says it never reads outside calendars. If you change the repo before submitting and want the links to show the change, replace the commit ID in every link with the output of `git rev-parse origin/main`.

"Busy day" is still a live feature (`app/bot/messages.py`, `BUSY_DAY_THRESHOLD`), so you can cite it.
