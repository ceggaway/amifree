# CS3216 final report: references and fixes

Checked against `CS3216 final proj.pdf` (19 pages) and the repo at commit `705b8ad` on `main` (18 Nov 2026).

## 1. Every P-citation checked against the code

The report links each P-number to a file. The links are correct, and every claim they support matches the code, apart from the P3 and P6 notes below. Other text fixes are in part 3.

| Ref | Cited in | File the report links | Claim checked | Result |
|---|---|---|---|---|
| P1 | §3, 4.1, 4.3, 4.4, 4.5, 7 | `README.md` | One FastAPI backend for bot + API + Mini App; privacy rules; `/deletemydata` scope | ✅ (README status line fixed 18 Nov) |
| P2 | §4.1, 4.3, 4.5 | `docs/group-features.md` | Group cards, private clash check, `/whosfree` opt-in, admin-enabled detection that waits for confirmation | ✅ |
| P3 | §4.1, 4.3 | `app/services/event_extractor.py` | Structured output; update/cancel intent; deterministic dates; times must appear in the message | ✅, but the "time must appear in the message" check is in `date_resolution.py` (`in_source`, line 238). Add that link |
| P4 | §4.2 | `migrations/002_group_events.sql` | `group_events` stored once; unique `(chat_id, source_message_id)`; one response per member; `version` rejects stale edits | ✅ |
| P5 | §4.2, 4.4 | `migrations/003_notifications.sql` | `notification_logs` with `dedupe_key`, `attempts`, retry time, `sent` status | ✅ |
| P6 | §4.2 | `migrations/004_polls.sql` | `polls`, `poll_participants`, `poll_votes` | ✅. `calendar_busy_blocks` (006) and `poll_availability` (010) are not in this file. Use `all_migrations.sql` if you want one link that covers every table |
| P7 | §4.2, 4.4 | `app/api/auth.py` | Signed `initData` check, short-lived session token | ✅ |
| P8 | §4.3, 4.4 | `app/services/availability.py` | `start_a < end_b and end_a > start_b`; saved plans shown as possible conflicts | ✅ (line 33) |
| P9 | §4.4 | `.github/workflows/ci.yml` | Lint, backend tests, SQL checks, frontend tests, build | ✅ |
| P10 | §4.4 | `docs/verification.md` | 679 Python tests, 28 SQL checks, 45 frontend tests on 19 Oct | ✅ |
| P11 | §4.5 | `app/services/personal_calendar.py` | Calendar links encrypted (Fernet); only busy times are stored | ✅ |

## 2. Replace the references paragraph (page 19)

The P-links in the report point to `blob/main`, so they will change whenever the repo does. Swap each one for the pinned link below (in Google Docs, right-click each P-link → Edit link), and replace the "Project source code" paragraph with:

> **Project source code and documentation.** [P1–P11] All from the ceggaway/amifree repository, pinned to commit 705b8ad (18 November 2026), so each link shows the code as described here:
> [P1] README.md · [P2] docs/group-features.md · [P3] app/services/event_extractor.py and date_resolution.py · [P4] app/db/migrations/002_group_events.sql · [P5] 003_notifications.sql · [P6] 004_polls.sql · [P7] app/api/auth.py · [P8] app/services/availability.py · [P9] .github/workflows/ci.yml · [P10] docs/verification.md · [P11] app/services/personal_calendar.py

Pinned links:

- P1 https://github.com/ceggaway/amifree/blob/705b8adb25ee5e58cfef510fc2dfd49e78fed9d2/README.md
- P2 https://github.com/ceggaway/amifree/blob/705b8adb25ee5e58cfef510fc2dfd49e78fed9d2/docs/group-features.md
- P3 https://github.com/ceggaway/amifree/blob/705b8adb25ee5e58cfef510fc2dfd49e78fed9d2/app/services/event_extractor.py
- P3 (add) https://github.com/ceggaway/amifree/blob/705b8adb25ee5e58cfef510fc2dfd49e78fed9d2/app/services/date_resolution.py#L238
- P4 https://github.com/ceggaway/amifree/blob/705b8adb25ee5e58cfef510fc2dfd49e78fed9d2/app/db/migrations/002_group_events.sql
- P5 https://github.com/ceggaway/amifree/blob/705b8adb25ee5e58cfef510fc2dfd49e78fed9d2/app/db/migrations/003_notifications.sql
- P6 https://github.com/ceggaway/amifree/blob/705b8adb25ee5e58cfef510fc2dfd49e78fed9d2/app/db/migrations/004_polls.sql
- P7 https://github.com/ceggaway/amifree/blob/705b8adb25ee5e58cfef510fc2dfd49e78fed9d2/app/api/auth.py
- P8 https://github.com/ceggaway/amifree/blob/705b8adb25ee5e58cfef510fc2dfd49e78fed9d2/app/services/availability.py#L33
- P9 https://github.com/ceggaway/amifree/blob/705b8adb25ee5e58cfef510fc2dfd49e78fed9d2/.github/workflows/ci.yml
- P10 https://github.com/ceggaway/amifree/blob/705b8adb25ee5e58cfef510fc2dfd49e78fed9d2/docs/verification.md
- P11 https://github.com/ceggaway/amifree/blob/705b8adb25ee5e58cfef510fc2dfd49e78fed9d2/app/services/personal_calendar.py#L85-L106
- §3 / §7 repo link: https://github.com/ceggaway/amifree

All links checked and open (HTTP 200).

## 3. Text fixes in the report

| Where | Current text | Change to | Why |
|---|---|---|---|
| §4.4, end of last paragraph | "…actual deployment and real-user reliability require separate evidence. [P7, P9, P10]" | "…actual deployment and real-user reliability are evidenced separately in Section 8 (131 users; 142 of 158 reminders delivered). [P7, P9, P10]" | Section 8 now has the live evidence, so point to it |
| §5.4, 2nd paragraph | "Removing the separate command reduced…" | "Removing /block from the command menu reduced… (It still works as a hidden shortcut, and busy times can be typed directly, e.g. "busy sat 2-6pm".)" | Code: `/block` still works but is gone from the menu. This settles the "/block discrepancy" item on p.19 |
| §5.5 | "Schedule an eventand Find a time" | "Schedule an event and Find a time" | Missing space |
| §8.5 STePS paragraph | "No STePS event is recorded in the data yet… Once its date is known…" | Use 12 Nov (Section 7 gives this date) and report signups on 12–15 Nov | The date is known; §7 and §8.5 currently contradict each other |
| §10 table | "ceggaway (name to confirm)" | "Cedric Tay Shi Han" | GitHub account ceggaway is Cedric. Commit counts checked to 9 Nov: Angel 125, Cedric 57, Goh Rou Shuen 15 (two accounts) ✅ |
| §10 Libraries | "…python-dateutil and cryptography." | "…python-dateutil, cryptography, openai and posthog." | Both are in `requirements.txt` |
| §11 Monetisation | "pay 250 Telegram Stars per group" | "pay 250 Telegram Stars per group for 30 days" | `pro_days = 30` in config |
| p.19 references | "main branch reviewed 15 November 2026 (latest retrieved commit d3c4ebb8…)" | Use the paragraph in part 2 | README was fixed after d3c4ebb |

## 4. Placeholders still in the report: delete or fill before submitting

- **Duplicate headings with "[Angel to insert…]" notes**: §8 (p.11), §10 (p.16), §11 (p.17). The real sections follow each one, so delete the placeholder heading and note.
- **"Open question" bullets**: §8.2 (campaign dates), §8.5 (STePS date and targets), §10 (names, mascot credit), §11 (OpenAI spend).
- **Figure placeholders**: Figures 3–10, the Instagram URL, and the demo video link. Figures 11 and 12 come before 3–10 in the text, so renumber them in order of appearance.
- **§7 milestone table**: "M1: Insert the original milestone-plan page".
- **§9.3**: "[Insert verified STePS contact record…]".
- **p.19 "Evidence still required before submission" paragraph**: delete it once the items above are done. The README item is already done (README fixed 18 Nov), and the /block item is covered by the §5.4 fix.
