# Representative event messages

These 14 cases are automated in `tests/fixtures/event_messages.json` and `tests/test_extraction.py`. Reference: **4 October 2026, 12:00 Asia/Singapore**. Structured extraction results are fixtures; validation and date resolution execute real application code. Live LLM quality remains to be evaluated after credentials are supplied.

| Message | Expected validated result |
| --- | --- |
| CCA dinner Friday 7–9pm at Vivo | 9 Oct, 19:00–21:00 |
| Project meeting tomorrow 14:00–15:30 at COM1 | 5 Oct, 14:00–15:30 |
| Hackathon 2026-10-17 9am–6pm | 17 Oct, 09:00–18:00 |
| Study group 10/10/2026 10:00–12:00 | 10 Oct, 10:00–12:00 |
| Hall supper 12 Oct 2026 8pm–10pm | 12 Oct, 20:00–22:00 |
| Lunch Friday 11–1pm | 9 Oct, 11:00–13:00 |
| Movie tonight 7:30pm–9:45pm | 4 Oct, 19:30–21:45 |
| Dinner Friday at Vivo | Ask for start time; no commitment |
| Dinner 7–9pm at Vivo | Ask for date; no commitment |
| Dinner Friday 7pm at Vivo | Ask for end time; no silent default |
| Dinner Friday or Saturday 7–9pm at Vivo | Ask which day |
| Games Friday 11pm to Saturday 1am | 9 Oct 23:00–10 Oct 01:00 |
| Games Friday 11pm–1am | Ask for explicit end date |
| Dinner Friday 7–9 at Vivo | Ask AM/PM |

Additional flow tests cover a moved dinner, ambiguous matching dinners, cancellation, corrected dates, invalid dates, stale confirmation buttons, and new clashes appearing while an update awaits confirmation. Forwarded relative-date tests preserve the original message timestamp even when the event is forwarded or clarified later.
