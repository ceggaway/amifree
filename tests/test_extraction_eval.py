"""Offline checks for the live extraction eval: the dataset is well formed and scoring is right."""

import asyncio

import pytest

from app.schemas.event import OtherTime
from evals.extraction.run import (
    HELDOUT,
    Prediction,
    load_cases,
    local,
    predict,
    reference_of,
    run_case,
    score,
    summarize,
)
from tests.conftest import extraction

CASES = load_cases()
OUTCOMES = {"event", "multi", "recurring", "clarify", "update", "cancel", "not_event"}


@pytest.mark.parametrize(
    "cases,size",
    [(CASES, 50), (load_cases(HELDOUT), 30), (load_cases(HELDOUT.with_name("heldout2.jsonl")), 30)],
)
def test_dataset_is_well_formed(cases, size):
    assert len(cases) >= size
    for case in cases:
        exp = case["expected"]
        assert exp["outcome"] in OUTCOMES, case["id"]
        if exp["outcome"] == "event":
            assert local(exp["end"]) > local(exp["start"]), case["id"]
            assert local(exp["start"]) >= reference_of(case).replace(hour=0, minute=0), case["id"]
            assert exp["title_any"], case["id"]
        if exp["outcome"] == "recurring":
            assert exp["weekdays"] and exp["title_any"], case["id"]
            assert local(exp["end"]) > local(exp["start"]), case["id"]
        if exp["outcome"] == "multi":
            assert exp["relation"] in ("separate", "alternatives"), case["id"]
            assert len(exp["times"]) >= 2 and exp["title_any"], case["id"]
            for start, end in exp["times"]:
                assert local(end) > local(start), case["id"]


def by_id(case_id):
    return next(c for c in CASES if c["id"] == case_id)


def test_correct_event_scores_correct():
    case = by_id("basic-01")
    parsed = extraction(
        title="CCA dinner", date_text="Friday", start_time_text="7", end_time_text="9pm"
    )
    pred = predict(parsed, case["text"], reference_of(case))
    assert pred.start.startswith("2026-10-16T19:00")
    result = score(case, pred)
    assert result["verdict"] == "correct"
    assert result["location_ok"] and result["title_ok"]


def test_wrong_time_is_critical():
    case = by_id("basic-01")
    pred = Prediction(outcome="event", start="2026-10-16T07:00+08:00", end="2026-10-16T21:00+08:00")
    assert score(case, pred)["verdict"] == "wrong_time"


def test_saving_an_unclear_message_is_critical():
    assert score(by_id("clarify-06"), Prediction(outcome="event"))["verdict"] == "false_save"


def two_trainings():
    other = OtherTime(
        title=None,
        date_text="Thu",
        start_time_text="6",
        end_time_text="8pm",
        end_date_text=None,
        location=None,
    )
    return extraction(
        title="Training",
        date_text="Tue",
        start_time_text="7",
        end_time_text="9pm",
        location="UTown",
        relation="separate",
        other_times=[other],
    )


def test_a_picker_with_every_time_scores_correct():
    case = by_id("times-01")
    pred = predict(two_trainings(), case["text"], reference_of(case))
    assert (pred.outcome, pred.relation) == ("multi", "separate")
    assert score(case, pred)["verdict"] == "correct"


def test_picker_scoring():
    case = by_id("times-01")
    tue = ["2026-10-13T19:00+08:00", "2026-10-13T21:00+08:00"]
    thu = ["2026-10-15T18:00+08:00", "2026-10-15T20:00+08:00"]
    wrong = ["2026-10-15T06:00+08:00", "2026-10-15T08:00+08:00"]

    def verdict(**fields):
        return score(case, Prediction(outcome="multi", **fields))["verdict"]

    assert verdict(relation="separate", times=[tue, wrong]) == "wrong_time"
    assert verdict(relation="separate", times=[tue]) == "dropped_time"
    assert verdict(relation="alternatives", times=[tue, thu]) == "wrong_relation"
    assert score(case, Prediction(outcome="event"))["verdict"] == "dropped_time"
    assert score(by_id("basic-01"), Prediction(outcome="multi"))["verdict"] == "false_split"


def test_a_recurring_message_is_never_split():
    case = by_id("times-09")  # "Badminton Tuesdays and Thursdays 7-9pm at USC"
    pred = predict(two_trainings(), case["text"], reference_of(case))
    assert pred.outcome != "multi"


def test_parser_rejection_is_attributed_to_the_parser():
    case = by_id("basic-03")  # "11.30pm" is in the message, but does the parser accept it?
    parsed = extraction(
        title="Hall supper",
        date_text="15 Oct",
        start_time_text="10pm",
        end_time_text="11.30pm",
        location="Raffles Hall dining hall",
    )
    pred = predict(parsed, case["text"], reference_of(case))
    if pred.outcome == "clarify":
        assert pred.clarify_reason == "parse_failed"
        assert score(case, pred)["verdict"] == "over_clarify"


def test_invented_text_is_attributed_to_the_model():
    case = by_id("basic-01")
    pred = predict(extraction(date_text="2026-10-16"), case["text"], reference_of(case))
    assert pred.clarify_reason == "verify_failed"


def test_kinds():
    assert predict(extraction(kind="update"), "x", reference_of(CASES[0])).outcome == "update"
    assert predict(extraction(kind="unknown"), "x", reference_of(CASES[0])).outcome == "not_event"


def test_summary_and_live_path_with_fake_client():
    case = by_id("basic-01")
    parsed = extraction(
        title="CCA dinner",
        date_text="Friday",
        start_time_text="7",
        end_time_text="9pm",
        location="Vivo",
    )

    class Usage:
        input_tokens, output_tokens = 300, 40

    class Result:
        output_parsed, usage = parsed, Usage()

    class Responses:
        async def parse(self, **kwargs):
            assert kwargs["text_format"].__name__ == "Extraction"
            return Result()

    class Client:
        responses = Responses()

    row = asyncio.run(run_case(Client(), "test-model", case, 0, asyncio.Semaphore(1)))
    assert row["verdict"] == "correct" and row["input_tokens"] == 300
    summary = summarize([row], [case], 1)
    assert summary["overall_accuracy"].startswith("100%")
    assert summary["critical_errors"].startswith("0%")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


class ScriptedExtractor:
    def __init__(self, *answers):
        self.answers, self.previous = list(answers), []

    async def extract(self, text, reference, previous=None):
        self.previous.append(previous)
        return self.answers.pop(0)


def test_multi_turn_conversation_reaches_the_event():
    from evals.extraction.run import converse

    case = by_id("multi-01")
    bot = ScriptedExtractor(
        extraction(
            title="jb101 makan",
            date_text=None,
            start_time_text="730pm",
            end_time_text=None,
            location=None,
        ),
        extraction(
            title="jb101 makan",
            date_text="this saturday",
            start_time_text="730pm",
            end_time_text=None,
            location=None,
        ),
        extraction(
            title="jb101 makan",
            date_text="this saturday",
            start_time_text="730pm",
            end_time_text="10pm",
            location=None,
        ),
    )
    pred = asyncio.run(converse(bot, case))
    assert pred.asks == ["date", "end", None]
    assert score(case, pred)["verdict"] == "correct"
    # Like the bot, "this saturday" and "till 10pm" answer its own questions without the model.
    assert bot.previous == [None] and len(bot.answers) == 2


def test_an_answer_the_code_cannot_read_goes_to_the_model():
    from evals.extraction.run import converse

    case = by_id("multi-04")  # Coffee chat Wed 3pm -> "till 4pm"
    first = extraction(
        title="Coffee chat", date_text="Wed", start_time_text="3pm", end_time_text=None
    )
    case = {**case, "turns": [case["turns"][0], "ends when the boss says so"]}
    bot = ScriptedExtractor(first, first)
    asyncio.run(converse(bot, case))
    assert bot.previous[1]["start_time_text"] == "3pm"  # the draft is carried forward


def test_tuesdays_and_thursdays_get_the_recurring_offer():
    case = by_id("times-09")  # "Badminton Tuesdays and Thursdays 7-9pm at USC"
    parsed = extraction(
        title="Badminton",
        date_text=None,
        start_time_text="7",
        end_time_text="9pm",
        location="USC",
        ambiguity="recurring",
    )
    pred = predict(parsed, case["text"], reference_of(case))
    assert (pred.outcome, pred.weekdays) == ("recurring", [1, 3])
    assert score(case, pred)["verdict"] == "correct"
    wrong = Prediction(outcome="recurring", start=pred.start, end=pred.end, weekdays=[1])
    assert score(case, wrong)["verdict"] == "wrong_days"


def test_a_quick_block_never_reaches_the_model():
    from evals.extraction.run import converse

    case = by_id("clarify-07")  # "Yoga every Tuesday 7-8pm at UTown gym"
    bot = ScriptedExtractor()
    pred = asyncio.run(converse(bot, case))
    assert bot.previous == []  # the bot reads it as a weekly block before the model
    assert (pred.outcome, pred.weekdays) == ("recurring", [1])
    assert score(case, pred)["verdict"] == "correct"


def test_the_timezone_question_counts_as_asking_about_it():
    case = by_id("clarify-06")  # "Call with mentor Friday 8-9pm UK time"
    parsed = extraction(
        title="Call with mentor", date_text="Friday", start_time_text="8", end_time_text="9pm"
    )
    pred = predict(parsed, case["text"], reference_of(case))
    assert pred.clarify_reason == "timezone"
    assert score(case, pred)["verdict"] == "correct"


def test_eval_catches_the_repeating_question_bug():
    from evals.extraction.run import converse

    case = by_id("multi-04")  # Coffee chat Wed 3pm -> "till 4pm"
    stuck = extraction(
        title="Coffee chat",
        date_text="Wed",
        start_time_text="3pm",
        end_time_text=None,
        ambiguity="unclear AM/PM",
    )
    pred = asyncio.run(converse(ScriptedExtractor(stuck, stuck), case))
    assert pred.asks == ["ambiguity", "ambiguity"]
    assert score(case, pred)["verdict"] == "over_clarify"


def test_asking_the_wrong_question_is_not_correct():
    case = by_id("single-01")
    wrong = predict(extraction(ambiguity="unclear AM/PM"), case["text"], reference_of(case))
    assert score(case, wrong)["verdict"] == "wrong_question"
    right = predict(
        extraction(date_text=None, start_time_text="730pm", end_time_text=None),
        case["text"],
        reference_of(case),
    )
    assert score(case, right)["verdict"] == "correct"
