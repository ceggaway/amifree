# Extraction eval: gpt-4o-mini

Run 2026-10-30 20:17 · 42 cases × 3 · reference 2026-10-12T10:00:00+08:00 (Mon) · cases `evals/extraction/heldout.jsonl`

| Metric | Value | Meaning |
| --- | --- | --- |
| Overall accuracy | 86% (108/126) | Did what a careful human would do |
| **Critical errors** | **2% (2/126)** | Wrong time shown, or a card offered when it should have asked |
| Event time accuracy | 91% (63/69) | Clear invitations resolved to the exact start and end |
| Over-clarify rate | 4% (3/69) | Clear invitations where the bot asked anyway (friction) |
| Clarify recall | 78% (14/18) | Unclear messages where the bot correctly asked |
| Picker accuracy | 100% (9/9) | Messages with several times: every time, and/or read right |
| Location accuracy | 94% (48/51) | Venue captured, on correctly timed events |
| Consistency | 95% (40/42) | Same answer every repeat |
| Latency p50 / p95 | 2870 / 4454 ms | One OpenAI call |
| Tokens in / out | 277605 / 8461 | Whole run |

Over-clarify causes: parse_failed 3. `parse_failed` = fix the date/time parser; `verify_failed` / `missing_field` / `model_ambiguity` = fix the prompt or model.

## By category

| Category | Correct |
| --- | --- |
| event | 93% (42/45) |
| injection | 100% (6/6) |
| multi_time | 100% (9/9) |
| multi_turn | 44% (4/9) |
| not_event | 100% (12/12) |
| real_world | 100% (9/9) |
| recurring | 50% (3/6) |
| should_clarify | 61% (11/18) |
| update_cancel | 100% (12/12) |

## Failures

**ho-clarify-01** · `wrong_kind`
- Message: Team dinner next week, venue TBC, will update on time
- Expected: clarify · asks ['date', 'start']
- Got: not_event · asked [None]
- Model fields: `{"kind": "unknown", "title": "Team dinner"}`

**ho-clarify-01** · `wrong_kind`
- Message: Team dinner next week, venue TBC, will update on time
- Expected: clarify · asks ['date', 'start']
- Got: not_event · asked [None]
- Model fields: `{"kind": "unknown", "title": "Team dinner", "relation": "one"}`

**ho-clarify-02** · `wrong_question` · model_ambiguity
- Message: Group call on Thursday, time TBD
- Expected: clarify · asks ['start']
- Got: clarify · asked ['ambiguity'] — “Please clarify the event date and time: vague time”
- Model fields: `{"kind": "unknown", "date_text": "Thursday", "ambiguity": "vague time"}`

**ho-clarify-02** · `wrong_question` · model_ambiguity
- Message: Group call on Thursday, time TBD
- Expected: clarify · asks ['start']
- Got: clarify · asked ['ambiguity'] — “Please clarify the event date and time: TBD”
- Model fields: `{"kind": "unknown", "title": "Group call", "date_text": "Thursday", "ambiguity": "TBD"}`

**ho-clarify-02** · `wrong_question` · model_ambiguity
- Message: Group call on Thursday, time TBD
- Expected: clarify · asks ['start']
- Got: clarify · asked ['ambiguity'] — “Please clarify the event date and time: TBD”
- Model fields: `{"kind": "unknown", "title": "Group call", "date_text": "Thursday", "ambiguity": "TBD", "relation": "one"}`

**ho-clarify-04** · `false_save`
- Message: Webinar Thursday 9-10pm New York time
- Expected: clarify · asks ['ambiguity']
- Got: event 2026-10-15T21:00 → 2026-10-15T22:00 · asked [None]
- Model fields: `{"kind": "new", "title": "Webinar", "date_text": "Thursday", "start_time_text": "9", "end_time_text": "10pm", "relation": "one"}`

**ho-clarify-04** · `false_save`
- Message: Webinar Thursday 9-10pm New York time
- Expected: clarify · asks ['ambiguity']
- Got: event 2026-10-15T21:00 → 2026-10-15T22:00 · asked [None]
- Model fields: `{"kind": "new", "title": "Webinar", "date_text": "Thursday", "start_time_text": "9", "end_time_text": "10pm", "relation": "one"}`

**ho-event-08** · `over_clarify` · parse_failed
- Message: Lunch w the exco tmr 12.30-1.30 at Fine Food
- Expected: event 2026-10-13T12:30 → 2026-10-13T13:30
- Got: clarify · asked ['ampm'] — “Is that AM or PM? Send, for example, 7pm.”
- Model fields: `{"kind": "new", "title": "Lunch with the exco", "date_text": "13 Oct", "start_time_text": "12:30", "end_time_text": "1:30", "location": "Fine Food"}`

**ho-event-08** · `over_clarify` · parse_failed
- Message: Lunch w the exco tmr 12.30-1.30 at Fine Food
- Expected: event 2026-10-13T12:30 → 2026-10-13T13:30
- Got: clarify · asked ['ampm'] — “Is that AM or PM? Send, for example, 7pm.”
- Model fields: `{"kind": "new", "title": "Lunch with the exco", "date_text": "13 Oct", "start_time_text": "12:30", "end_time_text": "1:30", "location": "Fine Food"}`

**ho-event-08** · `over_clarify` · parse_failed
- Message: Lunch w the exco tmr 12.30-1.30 at Fine Food
- Expected: event 2026-10-13T12:30 → 2026-10-13T13:30
- Got: clarify · asked ['ampm'] — “Is that AM or PM? Send, for example, 7pm.”
- Model fields: `{"kind": "new", "title": "Lunch with the exco", "date_text": "13 Oct", "start_time_text": "12:30", "end_time_text": "1:30", "location": "Fine Food"}`

**ho-recurring-01** · `wrong_kind` · quick_block
- Message: Choir practice every Wednesday 6.30-8.30pm at UCC
- Expected: recurring 2026-10-14T18:30 → 2026-10-14T20:30 weekdays [2]
- Got: clarify — “Please send a valid time.”
- Model fields: `{}`

**ho-recurring-01** · `wrong_kind` · quick_block
- Message: Choir practice every Wednesday 6.30-8.30pm at UCC
- Expected: recurring 2026-10-14T18:30 → 2026-10-14T20:30 weekdays [2]
- Got: clarify — “Please send a valid time.”
- Model fields: `{}`

**ho-recurring-01** · `wrong_kind` · quick_block
- Message: Choir practice every Wednesday 6.30-8.30pm at UCC
- Expected: recurring 2026-10-14T18:30 → 2026-10-14T20:30 weekdays [2]
- Got: clarify — “Please send a valid time.”
- Model fields: `{}`

**ho-turns-01** · `missed_event`
- Message: Supper at the Deck → Thursday 10pm → 11pm
- Expected: event 2026-10-15T22:00 → 2026-10-15T23:00 · asks [['date', 'start'], ['end']]
- Got: not_event · asked [None]
- Model fields: `{"kind": "unknown"}`

**ho-turns-01** · `missed_event`
- Message: Supper at the Deck → Thursday 10pm → 11pm
- Expected: event 2026-10-15T22:00 → 2026-10-15T23:00 · asks [['date', 'start'], ['end']]
- Got: not_event · asked [None]
- Model fields: `{"kind": "unknown", "title": "Supper at the Deck"}`

**ho-turns-01** · `missed_event`
- Message: Supper at the Deck → Thursday 10pm → 11pm
- Expected: event 2026-10-15T22:00 → 2026-10-15T23:00 · asks [['date', 'start'], ['end']]
- Got: not_event · asked [None]
- Model fields: `{"kind": "unknown", "title": "Supper at the Deck"}`

**ho-turns-03** · `wrong_question`
- Message: Mock interview 2pm → Wed 21 Oct → 3pm
- Expected: event 2026-10-21T14:00 → 2026-10-21T15:00 · asks [['date', 'end'], ['date', 'end']]
- Got: event 2026-10-21T14:00 → 2026-10-21T15:00 · asked ['other', 'end', None]
- Model fields: `{"kind": "new", "title": "Mock interview", "date_text": "Wed 21 Oct", "start_time_text": "2", "end_time_text": "3pm"}`

**ho-turns-03** · `wrong_question`
- Message: Mock interview 2pm → Wed 21 Oct → 3pm
- Expected: event 2026-10-21T14:00 → 2026-10-21T15:00 · asks [['date', 'end'], ['date', 'end']]
- Got: event 2026-10-21T14:00 → 2026-10-21T15:00 · asked ['other', 'end', None]
- Model fields: `{"kind": "new", "title": "Mock interview", "date_text": "Wed 21 Oct", "start_time_text": "2", "end_time_text": "3pm"}`
