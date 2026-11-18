# CS3216 final report: fill-ins for the unfinished parts

Paste-ready text for each placeholder in `CS3216 final proj.pdf`. Where the repo had the answer, it's filled in. Anything only the team knows is in **[square brackets]**. The text fixes and pinned P-links are in `report-references.md`.

---

## 1. Delete these

| Page | Delete |
|---|---|
| p.11 | The first "8. User acquisition, activation and usage results" heading and the "[Angel to insert Section 8…]" note under it. The real §8 follows |
| p.16 | The first "10. Individual contributions…" heading and its "[Angel to insert…]" note |
| p.17 | The first "11. Limitations…" heading and its "[Angel to insert…]" note |
| p.19 | The whole "Evidence still required before submission" paragraph, once sections 2–4 below are done |

## 2. Figures

There's no Figure 1 or 2; Figures 11 and 12 come first, in §4. **Renumber: Figure 11 → Figure 1, Figure 12 → Figure 2.** 3–10 keep their numbers.

| Fig | Image to use | Caption (paste) |
|---|---|---|
| 3 (§5.2) | Two screenshots of /start: one from a test account on an old build, or rebuild it from the text below; one from the bot today | **Figure 3. Onboarding before and after.** *Before (11 Oct):* four feature bullets, then a request to paste a NUSMods or calendar link before anything else. *After:* one line on what AmIFree does, a **Try an example** button that shows a sample clash check with nothing saved, and calendar setup offered only after the first plan. |
| 4 (§5.3) | `app/assets/ios/ios_step1.png`–`ios_step4.png` side by side (or `app/assets/google/step5_dialog.png`) | **Figure 4. Revised calendar guide (iPhone).** Each step is its own message with a numbered instruction, a screenshot marking where to tap, and Next/Back buttons. Introduced 19–25 Oct, replacing one long block of text. |
| 5 (§5.4) | Screenshot of a plan card, then the same card after tapping Save | **Figure 5. Event card before and after saving.** The availability verdict comes first, then the title and details. Saving edits the same message in place instead of sending a new one. |
| 6 (§5.5) | `marketing/final-poster/shots/home.png` | **Figure 6. Mini App group home.** *Schedule an event* (time known) and *Find a time* (collect availability first) are the two main actions. *Pending* lists anything waiting on the viewer. |
| 7 (§6.2) | Screenshot of the post as sent in a channel. Text is in `marketing/telegram-post.md` | **Figure 7. Main Telegram promo post.** It opens with familiar problems (double-booking, lost venues, "when is everyone free?"), then splits into *Your plans* and *Group plans*, ending with one call to action: open @amifree_devv_bot. |
| 8 (§6.3) | Still from the Instagram reel | **Figure 8. Instagram reel still.** **[Insert reel URL]** |
| 9 (§6.3) | `marketing/AmIFree-Poster-A3-v2.jpg` beside **[photo of the poster up at Central Library]** | **Figure 9. A3 poster and its placement at Central Library.** A short feature list and a large QR code that opens the bot. |
| 10 (§6.4) | A frame from `marketing/demo/amifree-demo-split.mp4` (pause it and take a screenshot) | **Figure 10. Demo video shown at the STePS booth.** Split screen: a forwarded invite turned into a checked card on one side, and a group RSVP card updating live on the other. **[Insert video link: upload the mp4 to YouTube (unlisted) or Google Drive]** |

## 3. Open questions

### §8.2: which campaign ran on each spike day?

Replace the "Open question" bullet with:

> The first three spikes, **21 Oct** (19 signups), **29 Oct** (33) and **2 Nov** (14), were user-testing rounds, when we sent the bot to friends, project groups and Telegram chats and asked them to try it. The fourth, **11 Nov** (12), was STePS, where the giveaway also launched. Timing is the only link, so these are associations, not attributions.

### §8.5: STePS

Results from the Supabase query (signups by day, Singapore time; "connected" means NUSMods, a calendar link or Google sharing, as in §8.1):

| Day | Signups | Connected a calendar |
|---|---|---|
| 9 Nov | 2 | 0 |
| 10 Nov | 2 | 0 |
| **11 Nov (STePS)** | **12** | **2** |
| 12 Nov | 1 | 0 |
| 13–15 Nov | 0 | 0 |

Replace the STePS paragraph and its "Open question" bullet with:

> **STePS (11 Nov).** 12 people started AmIFree on STePS day, against 2 a day on 9–10 Nov, making it the fourth-largest signup day of the pilot. The giveaway launched the same day, so the two effects can't be separated. 2 of the 12 (17%) connected a calendar, the same rate as the pilot overall. The lift did not last: 1 signup followed on 12 Nov and none on 13–15 Nov. STePS also coincided with the outage in §8.6, which ended at 14:07 that day, so visitors who tried a plan before then got no reply.

**The STePS date is 11 Nov, not 12 Nov.** Change it in:
- §7 milestone table: "12 Nov: STePS demonstration and outreach" → "11 Nov".
- §8.6 outage bullet: add "This overlapped with STePS on 11 Nov."

**One number to check (§8.6):** it says 27 people pressed /start during the outage (10 Nov 14:28 to 11 Nov 14:07). The query found at most 14 new signups across 10–11 Nov combined, so the 27 must include repeat presses or existing users. Reword it to "/start was pressed 27 times", or ask Angel which it counts.

For the targets question, replace the bullet with:

> Targets came from the original proposal: 100 sign-ups, 40 weekly users and five active groups (§7). Results: 131 sign-ups (met), a peak of 33 weekly active users (not met) and 7 group chats with events from 5 organisers (met on count, though driven by a few organisers).

### §10: names and credits

- "ceggaway (name to confirm)" → **Cedric Tay Shi Han**.
- Replace the mascot bullet with:

> **Assets.** The AmIFree mascot was made by **[name / tool]**. Posters use the Anton and Bricolage Grotesque typefaces (Google Fonts, SIL Open Font License). Instagram reels were made with Dreamable AI. Screenshots are of AmIFree itself.

- The "Open question" about work outside the code: **[add pitch deck, user interviews, STePS booth duties per person]**.

### §11: OpenAI spend

Get it from platform.openai.com → Usage, for 11 Oct–14 Nov. Replace the bullet with:

> The pilot spent **US$[X]** on OpenAI, about **US$[X ÷ 61]** per active user. Most of it was `gpt-4o-mini` reading plans, plus `gpt-4o-mini-transcribe` for voice notes.

## 4. Only the team can fill these in

| Where | What's needed |
|---|---|
| §7 milestone table, row 2 | Whether the two organiser trial agreements happened. If not, keep the current cautious wording |
| §7 footnote (M1) | The milestone page from the proposal or Progress Report 2, as an appendix image |
| §9.3 | The STePS contact record: group, role, date, concern and follow-up status. If there isn't one, delete the placeholder; the paragraph above it already says the status is prospective |
| Fig 3, 5, 7, 8, 9 | Screenshots and photos listed in section 2 |

## 5. Already checked in the repo (no change needed)

- **§5.2**: the calendar flow does ask "Are your classes included in this calendar?" after the user picks a calendar (`app/bot/onboarding.py`, line 416).
- **§5.3**: step-by-step guides with screenshots landed on 19–25 Oct (commits `9dbc207`, `b79ea0f`, `5777a26`), which matches the timeline in the text.
