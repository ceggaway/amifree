"""Offline checks for the live extraction eval: the dataset is well formed and scoring is right."""

import asyncio

import pytest

from evals.extraction.run import (
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
OUTCOMES = {"event", "clarify", "update", "cancel", "not_event"}


def test_dataset_is_well_formed():
    assert len(CASES) >= 50
    for case in CASES:
        exp = case["expected"]
        assert exp["outcome"] in OUTCOMES, case["id"]
        if exp["outcome"] == "event":
            assert local(exp["end"]) > local(exp["start"]), case["id"]
            assert local(exp["start"]) >= reference_of(case).replace(hour=0, minute=0), case["id"]
            assert exp["title_any"], case["id"]


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
    assert score(by_id("clarify-04"), Prediction(outcome="event"))["verdict"] == "false_save"


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
    assert bot.previous[1]["start_time_text"] == "730pm"  # the draft is carried forward


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
