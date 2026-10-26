"""Evaluate how well the live LLM reads event messages.

Each case in cases.jsonl goes through the same path as a forwarded message in the bot:
EventExtractor (OpenAI) -> validate_extraction (deterministic date/time rules). The result
is compared with a hand-labelled expected outcome.

    python -m evals.extraction.run                      # one pass over every case
    python -m evals.extraction.run --repeats 3          # also measures consistency
    python -m evals.extraction.run --only time-01,real-05
    python -m evals.extraction.run --system-prompt my_prompt.txt   # A/B a new prompt

Needs OPENAI_API_KEY in .env (Supabase and Telegram are not used). Writes a JSON file with
every raw extraction and a Markdown report to evals/extraction/results/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from app.schemas.event import SG, Extraction
from app.services import event_extractor
from app.services.date_resolution import (
    Clarification,
    asked_field,
    fill_answer,
    infer_meridiem,
    next_weekday_name,
    single_event_guard,
    validate_extraction,
    weekly_weekdays,
)
from app.services.multi_time import fold_answer, is_multi, read_times, settle_times
from app.services.quick_blocks import parse_block

HERE = Path(__file__).parent
CASES = HERE / "cases.jsonl"
DEFAULT_REFERENCE = "2026-10-12T10:00:00+08:00"  # Monday, 10am Singapore time
CRITICAL = {"wrong_time", "false_save"}
# The bot's default_event_duration_minutes: a picker estimates a missing end time.
PICKER_DEFAULT_MINUTES = 120


# ---------- loading ----------


def load_cases(path: Path = CASES) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case ids in " + str(path))
    return cases


def reference_of(case: dict) -> datetime:
    return datetime.fromisoformat(case.get("reference", DEFAULT_REFERENCE)).astimezone(SG)


def local(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=SG)


# ---------- prediction and scoring (pure, unit-tested offline) ----------


@dataclass
class Prediction:
    outcome: str  # event | multi | recurring | clarify | update | cancel | not_event | error
    start: str | None = None
    end: str | None = None
    title: str | None = None
    location: str | None = None
    clarification: str | None = None
    clarify_reason: str | None = (
        None  # model_ambiguity | missing_field | verify_failed | parse_failed
    )
    extraction: dict | None = None
    asks: list[str | None] | None = None  # what the bot asked about after each turn
    # multi: "separate" or "alternatives", and [start, end] of every time in the picker.
    relation: str | None = None
    times: list[list[str]] | None = None
    # recurring: the repeating weekdays (Monday 0); start/end are the first session.
    weekdays: list[int] | None = None


def asked_about(pred: Prediction) -> str | None:
    """Which question the bot asked: date, start, end, ampm, ambiguity, verify or other."""
    if pred.outcome != "clarify":
        return None
    message = pred.clarification or ""
    if pred.clarify_reason in ("model_ambiguity", "multi_event_guard", "timezone"):
        return "ambiguity"
    if pred.clarify_reason == "verify_failed":
        return "verify"
    for needle, label in (
        ("What date", "date"),
        ("event start", "start"),
        ("event end", "end"),
        ("AM or PM", "ampm"),
    ):
        if needle in message:
            return label
    return "other"


def clarify_reason(message: str) -> str:
    if message.startswith("Please clarify the event date and time:"):
        return "model_ambiguity"  # the model itself flagged it
    if "couldn't verify" in message:
        return "verify_failed"  # model returned text that isn't in the message
    if "another timezone" in message:
        return "timezone"  # the deterministic timezone check asked, whatever the model said
    if message.startswith(("What date", "What time does this event")):
        return "missing_field"  # model left a critical field empty
    return "parse_failed"  # model's text was present but the date/time parser rejected it


def predict(
    extraction: Extraction,
    text: str,
    reference: datetime,
    clarifying: bool = False,
    answer: str | None = None,
) -> Prediction:
    """Mirror SchedulingService.message for a new draft (fresh message or clarification)."""
    raw = extraction.model_dump(mode="json")
    if clarifying:
        extraction = fold_answer(extraction)
    else:
        extraction = settle_times(extraction, text)
    extraction = event_extractor.settle_kind(extraction, clarifying, text)
    multi = not clarifying and is_multi(extraction, text)
    if clarifying:
        extraction = event_extractor.settle_ambiguity(extraction, answer or text)
    elif extraction.kind == "new" and not multi:
        try:
            single_event_guard(text)
        except Clarification as exc:
            return Prediction(
                outcome="clarify",
                clarification=str(exc),
                clarify_reason="multi_event_guard",
                extraction=raw,
            )
    if multi:
        times = read_times(extraction, text, reference, PICKER_DEFAULT_MINUTES)
        if not times.drafts:
            return Prediction(
                outcome="clarify",
                clarification="I couldn't read the times in that message.",
                clarify_reason="multi_unread",
                extraction=raw,
            )
        if len(times.drafts) > 1 or times.unread:
            return Prediction(
                outcome="multi",
                relation=extraction.relation,
                times=[[d.start_at.isoformat(), d.end_at.isoformat()] for d in times.drafts],
                title=times.drafts[0].title,
                location=times.drafts[0].location,
                extraction=raw,
            )
    extraction = fold_answer(extraction, answer=False)
    if extraction.kind in ("update", "cancel"):
        return Prediction(outcome=extraction.kind, title=extraction.title, extraction=raw)
    if extraction.kind == "unknown":
        return Prediction(outcome="not_event", extraction=raw)
    if extraction.kind == "deadline":
        return Prediction(outcome="deadline", title=extraction.title, extraction=raw)
    weekly = [] if clarifying else weekly_weekdays(text)
    if weekly:
        # Like the bot: "Tuesdays and Thursdays" is the sooner of those days, then the
        # "Just the next one / Every Tue & Thu" offer once the hours are known.
        day = next_weekday_name(weekly, reference)
        extraction = extraction.model_copy(update={"date_text": day, "ambiguity": None})
        text = f"{text}\n{day}"
        if extraction.start_time_text:
            try:
                draft = validate_extraction(
                    extraction, text, reference, default_minutes=PICKER_DEFAULT_MINUTES
                )
            except Clarification:
                pass  # e.g. no end time: the usual questions below
            else:
                return Prediction(
                    outcome="recurring",
                    start=draft.start_at.isoformat(),
                    end=draft.end_at.isoformat(),
                    title=draft.title,
                    location=draft.location,
                    weekdays=weekly,
                    extraction=raw,
                )
    try:
        draft = validate_extraction(extraction, text, reference, default_minutes=0)
    except Clarification as exc:
        return Prediction(
            outcome="clarify",
            clarification=str(exc),
            clarify_reason=clarify_reason(str(exc)),
            title=extraction.title,
            location=extraction.location,
            extraction=raw,
        )
    return Prediction(
        outcome="event",
        start=draft.start_at.isoformat(),
        end=draft.end_at.isoformat(),
        title=draft.title,
        location=draft.location,
        extraction=raw,
    )


def matches_any(value: str | None, options: list[str] | None) -> bool | None:
    if options is None:
        return None
    return bool(value) and any(o.casefold() in value.casefold() for o in options)


def score(case: dict, pred: Prediction) -> dict:
    exp = case["expected"]
    want, got = exp["outcome"], pred.outcome
    result = {"verdict": "", "time_ok": None, "location_ok": None, "title_ok": None}
    if got == "error":
        result["verdict"] = "error"
    elif want == "event" and got == "event":
        ok = local(exp["start"]) == local(pred.start) and local(exp["end"]) == local(pred.end)
        result["time_ok"] = ok
        result["location_ok"] = matches_any(pred.location, exp.get("location_any"))
        result["title_ok"] = matches_any(pred.title, exp.get("title_any"))
        result["verdict"] = "correct" if ok else "wrong_time"
    elif want == "recurring" and got == "recurring":
        ok = local(exp["start"]) == local(pred.start) and local(exp["end"]) == local(pred.end)
        result["time_ok"] = ok
        result["title_ok"] = matches_any(pred.title, exp.get("title_any"))
        if not ok:
            result["verdict"] = "wrong_time"
        elif pred.weekdays != exp["weekdays"]:
            result["verdict"] = "wrong_days"
        else:
            result["verdict"] = "correct"
    elif want == "multi" and got == "multi":
        wanted = {(local(s), local(e)) for s, e in exp["times"]}
        seen = {(local(s), local(e)) for s, e in pred.times}
        result["time_ok"] = wanted == seen
        result["title_ok"] = matches_any(pred.title, exp.get("title_any"))
        if seen - wanted:
            result["verdict"] = "wrong_time"  # the picker offers a time nobody wrote
        elif seen != wanted:
            result["verdict"] = "dropped_time"
        elif pred.relation != exp["relation"]:
            result["verdict"] = "wrong_relation"  # "and" read as "or", or the reverse
        else:
            result["verdict"] = "correct"
    elif want == "multi" and got == "event":
        result["verdict"] = "dropped_time"  # one card for a message with several times
    elif got == "multi":
        result["verdict"] = "false_split"  # one event (or a question) shown as a picker
    elif want == got:
        result["verdict"] = "correct"
    elif got == "event":
        result["verdict"] = (
            "false_save"  # would have offered a card for something it should ask about
        )
    elif want == "event" and got == "clarify":
        result["verdict"] = "over_clarify"
    elif want == "event" and got == "not_event":
        result["verdict"] = "missed_event"
    else:
        result["verdict"] = "wrong_kind"
    # The right time under the wrong name ("Event" for "Training") is still a miss: the
    # user can't tell what the plan is.
    if result["verdict"] == "correct" and result["title_ok"] is False:
        result["verdict"] = "wrong_title"
    # Asking is only right if it asks about the right thing.
    if result["verdict"] == "correct":
        asks = pred.asks or [asked_about(pred)]
        expected_turns = exp.get("asks") or []
        for allowed, seen in zip(expected_turns, asks, strict=False):
            if seen not in allowed:
                result["verdict"] = "wrong_question"
        if want == "clarify" and exp.get("asks_any") and asks[-1] not in exp["asks_any"]:
            result["verdict"] = "wrong_question"
    return result


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}% ({n}/{d})" if d else "n/a"


def summarize(rows: list[dict], cases: list[dict], repeats: int) -> dict:
    by_id = {c["id"]: c for c in cases}
    verdicts = Counter(r["verdict"] for r in rows)
    total = len(rows)
    events = [r for r in rows if by_id[r["id"]]["expected"]["outcome"] == "event"]
    clarifies = [r for r in rows if by_id[r["id"]]["expected"]["outcome"] == "clarify"]
    pickers = [r for r in rows if by_id[r["id"]]["expected"]["outcome"] == "multi"]
    timed = [r for r in events if r["time_ok"]]
    with_loc = [r for r in timed if r["location_ok"] is not None]
    lat = sorted(r["latency_ms"] for r in rows if r.get("latency_ms") is not None)
    categories = defaultdict(lambda: [0, 0])
    for r in rows:
        cat = by_id[r["id"]]["category"]
        categories[cat][1] += 1
        categories[cat][0] += r["verdict"] == "correct"
    over = [r for r in rows if r["verdict"] == "over_clarify"]
    consistency = None
    if repeats > 1:
        outcomes = defaultdict(set)
        for r in rows:
            times = tuple(map(tuple, r.get("times") or ()))
            outcomes[r["id"]].add((r["outcome"], r.get("start"), r.get("end"), times))
        consistency = pct(sum(len(v) == 1 for v in outcomes.values()), len(outcomes))
    return {
        "cases": len(cases),
        "runs": total,
        "overall_accuracy": pct(verdicts["correct"], total),
        "critical_errors": pct(sum(verdicts[v] for v in CRITICAL), total),
        "event_time_accuracy": pct(len(timed), len(events)),
        "over_clarify_rate": pct(verdicts["over_clarify"], len(events)),
        "over_clarify_causes": dict(Counter(r["clarify_reason"] for r in over)),
        "clarify_recall": pct(sum(r["outcome"] == "clarify" for r in clarifies), len(clarifies)),
        "picker_accuracy": pct(sum(r["verdict"] == "correct" for r in pickers), len(pickers)),
        "location_accuracy": pct(sum(bool(r["location_ok"]) for r in with_loc), len(with_loc)),
        "verdicts": dict(verdicts),
        "by_category": {k: pct(*v) for k, v in sorted(categories.items())},
        "consistency_across_repeats": consistency,
        "latency_ms_p50": lat[len(lat) // 2] if lat else None,
        "latency_ms_p95": lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None,
        "latency_ms_mean": round(statistics.mean(lat)) if lat else None,
        "input_tokens": sum(r.get("input_tokens") or 0 for r in rows),
        "output_tokens": sum(r.get("output_tokens") or 0 for r in rows),
    }


def report(summary: dict, rows: list[dict], cases: list[dict], meta: dict) -> str:
    by_id = {c["id"]: c for c in cases}
    lines = [
        f"# Extraction eval: {meta['model']}",
        "",
        f"Run {meta['started']} · {summary['cases']} cases × {meta['repeats']} · reference {DEFAULT_REFERENCE} (Mon)"
        + (f" · prompt `{meta['system_prompt']}`" if meta.get("system_prompt") else ""),
        "",
        *(
            [
                f"> ⚠️ **{summary['verdicts']['error']} of {summary['runs']} runs failed to reach OpenAI** "
                "(key, credits or network). Scores below are not meaningful until this is 0.",
                "",
            ]
            if summary["verdicts"].get("error")
            else []
        ),
        "| Metric | Value | Meaning |",
        "| --- | --- | --- |",
        f"| Overall accuracy | {summary['overall_accuracy']} | Did what a careful human would do |",
        f"| **Critical errors** | **{summary['critical_errors']}** | Wrong time shown, or a card offered when it should have asked |",
        f"| Event time accuracy | {summary['event_time_accuracy']} | Clear invitations resolved to the exact start and end |",
        f"| Over-clarify rate | {summary['over_clarify_rate']} | Clear invitations where the bot asked anyway (friction) |",
        f"| Clarify recall | {summary['clarify_recall']} | Unclear messages where the bot correctly asked |",
        f"| Picker accuracy | {summary['picker_accuracy']} | Messages with several times: every time, and/or read right |",
        f"| Location accuracy | {summary['location_accuracy']} | Venue captured, on correctly timed events |",
        f"| Consistency | {summary['consistency_across_repeats'] or 'run with --repeats 3'} | Same answer every repeat |",
        f"| Latency p50 / p95 | {summary['latency_ms_p50']} / {summary['latency_ms_p95']} ms | One OpenAI call |",
        f"| Tokens in / out | {summary['input_tokens']} / {summary['output_tokens']} | Whole run |",
        "",
        "Over-clarify causes: "
        + (", ".join(f"{k} {v}" for k, v in summary["over_clarify_causes"].items()) or "none")
        + ". `parse_failed` = fix the date/time parser; `verify_failed` / `missing_field` / `model_ambiguity` = fix the prompt or model.",
        "",
        "## By category",
        "",
        "| Category | Correct |",
        "| --- | --- |",
        *[f"| {k} | {v} |" for k, v in summary["by_category"].items()],
        "",
        "## Failures",
        "",
    ]
    failures = [r for r in rows if r["verdict"] != "correct"]
    if not failures:
        lines.append("None.")
    for r in failures:
        exp = by_id[r["id"]]["expected"]
        want = exp["outcome"] + (
            f" {exp['start']} → {exp['end']}" if exp["outcome"] in ("event", "recurring") else ""
        )
        if exp["outcome"] == "recurring":
            want += f" weekdays {exp['weekdays']}"
        if exp["outcome"] == "multi":
            want += f" {exp['relation']} " + ", ".join(f"{s} → {e}" for s, e in exp["times"])
        got = r["outcome"] + (f" {r['start'][:16]} → {r['end'][:16]}" if r.get("start") else "")
        if r.get("times"):
            got += f" {r['relation']} " + ", ".join(f"{s[:16]} → {e[:16]}" for s, e in r["times"])
        detail = r.get("clarification") or r.get("error") or ""
        case = by_id[r["id"]]
        text = " → ".join(case.get("turns") or [case["text"]]).replace("\n", " ⏎ ")
        lines += [
            f"**{r['id']}** · `{r['verdict']}`"
            + (f" · {r['clarify_reason']}" if r.get("clarify_reason") else ""),
            f"- Message: {text[:200]}",
            f"- Expected: {want}"
            + (
                f" · asks {exp.get('asks') or exp.get('asks_any')}"
                if exp.get("asks") or exp.get("asks_any")
                else ""
            ),
            f"- Got: {got}"
            + (f" · asked {r['asks']}" if r.get("asks") else "")
            + (f" — “{detail[:160]}”" if detail else ""),
            f"- Model fields: `{json.dumps({k: v for k, v in (r.get('extraction') or {}).items() if v}, ensure_ascii=False)}`",
            "",
        ]
    return "\n".join(lines)


# ---------- live run ----------


class _RecordingResponses:
    def __init__(self, inner, sink: dict):
        self._inner, self._sink = inner, sink

    async def parse(self, **kwargs):
        result = await self._inner.parse(**kwargs)
        usage = getattr(result, "usage", None)
        for name in ("input_tokens", "output_tokens"):
            self._sink[name] = (self._sink.get(name) or 0) + (getattr(usage, name, 0) or 0)
        return result


class _RecordingClient:
    def __init__(self, client, sink: dict):
        self.responses = _RecordingResponses(client.responses, sink)


def quick_block(text: str, reference: datetime) -> Prediction | None:
    """A message the bot reads as a quick block ("Yoga every Tuesday 7-8pm") before the model.

    A weekly one is the same weekly busy time as the recurring offer, so it scores as one.
    """
    try:
        rule = parse_block(text, reference)
    except Clarification as exc:
        return Prediction(outcome="clarify", clarification=str(exc), clarify_reason="quick_block")
    if rule is None:
        return None
    if rule.kind == "ONCE":
        return Prediction(
            outcome="event",
            start=rule.start_at.isoformat(),
            end=rule.end_at.isoformat(),
            title=rule.title,
        )
    return Prediction(
        outcome="recurring",
        start=datetime.combine(rule.start_date, rule.start_time, SG).isoformat(),
        end=datetime.combine(rule.start_date, rule.end_time, SG).isoformat(),
        title=rule.title,
        weekdays=[rule.weekday],
    )


async def converse(extractor, case: dict) -> Prediction:
    """One message, or a back-and-forth where the user answers each clarifying question.

    Mirrors SchedulingService.message: the draft's previous extraction is sent with each
    answer, and validation sees all the user's messages so far.
    """
    turns = case.get("turns") or [case["text"]]
    reference, previous, asked, sent, asks = reference_of(case), None, None, [], []
    quick = quick_block(turns[0], reference)
    if quick is not None:
        return quick  # like the bot, the model never sees it
    for i, text in enumerate(turns):
        extraction = None
        if previous:
            # Like the bot: "8" after a 7pm start is 8pm, and a short answer to its own
            # question ("till 4pm", "Saturday") is filled in without the model.
            draft = Extraction.model_validate(previous)
            text = infer_meridiem(draft, asked, text)
            if asked:
                extraction = fill_answer(draft, asked, text, reference)
        if extraction is None:
            extraction = await extractor.extract(text, reference, previous)
        sent.append(text)
        pred = predict(extraction, "\n".join(sent), reference, clarifying=i > 0, answer=text)
        asks.append(asked_about(pred))
        if pred.outcome != "clarify" or i == len(turns) - 1:
            break
        previous = extraction.model_dump(mode="json")
        asked = asked_field(pred.clarification or "")
    pred.asks = asks
    return pred


async def run_case(client, model: str, case: dict, repeat: int, gate: asyncio.Semaphore) -> dict:
    sink: dict = {}
    extractor = event_extractor.EventExtractor(_RecordingClient(client, sink), model)
    async with gate:
        started = time.monotonic()
        try:
            pred = await converse(extractor, case)
            error = None
        except Exception as exc:  # report and keep going
            pred, error = Prediction(outcome="error"), f"{type(exc).__name__}: {exc}"[:300]
        latency = round((time.monotonic() - started) * 1000)
    row = {"id": case["id"], "repeat": repeat, **asdict(pred), **score(case, pred)}
    row.update(latency_ms=latency, error=error, **sink)
    return row


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--model", help="defaults to OPENAI_MODEL from .env")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--only", help="comma-separated case ids")
    ap.add_argument("--category", help="run one category only")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--system-prompt", help="file with a replacement system prompt to A/B test")
    ap.add_argument("--out", default=str(HERE / "results"))
    args = ap.parse_args(argv)

    from openai import AsyncOpenAI

    from app.config import Settings

    settings = Settings()
    key = settings.openai_api_key.get_secret_value()
    if not key:
        print("OPENAI_API_KEY is empty. Add it to .env (run from the amifree/ folder).")
        return 2
    model = args.model or settings.openai_model
    if args.system_prompt:
        event_extractor.SYSTEM = Path(args.system_prompt).read_text()

    cases = load_cases()
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    if not cases:
        print("No cases selected.")
        return 2

    client = AsyncOpenAI(api_key=key, max_retries=3, timeout=60)
    gate = asyncio.Semaphore(args.concurrency)
    started = datetime.now(SG)
    print(f"Running {len(cases)} cases × {args.repeats} on {model} …", file=sys.stderr)
    rows = await asyncio.gather(
        *(run_case(client, model, c, r, gate) for r in range(args.repeats) for c in cases)
    )
    rows.sort(key=lambda r: (r["id"], r["repeat"]))

    summary = summarize(rows, cases, args.repeats)
    meta = {
        "model": model,
        "repeats": args.repeats,
        "started": started.strftime("%Y-%m-%d %H:%M"),
        "system_prompt": args.system_prompt,
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{started:%Y%m%d-%H%M}-{model.replace('/', '_')}"
    (out / f"{stem}.json").write_text(
        json.dumps({"meta": meta, "summary": summary, "rows": rows}, indent=2, ensure_ascii=False)
    )
    md = report(summary, rows, cases, meta)
    (out / f"{stem}.md").write_text(md)
    print(md)
    print(f"\nSaved {out / stem}.json and .md", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
