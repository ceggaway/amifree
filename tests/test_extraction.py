import json
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI

from app.schemas.event import SG
from app.services.date_resolution import Clarification, resolve_date, validate_extraction
from app.services.event_extractor import EventExtractor, ExtractionError
from tests.conftest import extraction

CASES = json.loads((Path(__file__).parent / "fixtures" / "event_messages.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_representative_messages(case, reference):
    parsed = extraction(**case["extraction"])
    if case.get("question"):
        with pytest.raises(Clarification, match=case["question"]):
            validate_extraction(parsed, case["text"], reference)
    else:
        draft = validate_extraction(parsed, case["text"], reference)
        assert draft.start_at.isoformat() == case["start"]
        assert draft.end_at.isoformat() == case["end"]


def test_refuses_fabricated_date(reference):
    with pytest.raises(Clarification, match="verify"):
        validate_extraction(
            extraction(date_text="2026-10-09"), "CCA dinner Friday 7–9pm at Vivo", reference
        )


def test_visible_default_duration(reference):
    parsed = extraction(start_time_text="7pm", end_time_text=None)
    draft = validate_extraction(
        parsed, "CCA dinner Friday 7pm at Vivo", reference, default_minutes=60
    )
    assert draft.assumed_duration
    assert (draft.end_at - draft.start_at).total_seconds() == 3600


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("this Friday", date(2026, 10, 9)),
        ("next Friday", date(2026, 10, 9)),
        ("tomorrow", date(2026, 10, 5)),
        ("31/12", date(2026, 12, 31)),
        ("2 Jan", date(2027, 1, 2)),
        ("2026-10-10", date(2026, 10, 10)),
    ],
)
def test_date_rules(raw, expected, reference):
    assert resolve_date(raw, reference) == expected


def test_next_weekday_is_next_calendar_week():
    assert resolve_date("next Friday", datetime(2026, 10, 5, tzinfo=SG)) == date(2026, 10, 16)


def test_original_timestamp_not_forward_time():
    original = datetime(2026, 9, 30, 23, 59, tzinfo=SG)
    assert resolve_date("tomorrow", original) == date(2026, 10, 1)


async def test_openai_sdk_structured_output_request(reference):
    parsed = extraction()

    def handler(request):
        body = json.loads(request.content)
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["strict"] is True
        assert body["store"] is False
        assert "never decide availability" in body["input"][0]["content"].lower()
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 0,
                "model": "gpt-4o-mini",
                "status": "completed",
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": parsed.model_dump_json(),
                                "annotations": [],
                            }
                        ],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with AsyncOpenAI(api_key="test", http_client=http) as client:
            result = await EventExtractor(client, "gpt-4o-mini").extract(
                "CCA dinner Friday 7–9pm at Vivo", reference
            )
            assert result == parsed


async def test_refusal_is_failure(reference):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 0,
                "model": "gpt-4o-mini",
                "status": "completed",
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "refusal", "refusal": "Cannot extract"}],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with AsyncOpenAI(api_key="test", http_client=http) as client:
            with pytest.raises(ExtractionError):
                await EventExtractor(client, "gpt-4o-mini").extract("unknown", reference)


async def test_clarification_never_sends_a_stale_ambiguity_back(reference):
    """Regression: a kept 'unclear AM/PM' flag made every answer re-ask the same question."""
    sent = []

    class Responses:
        async def parse(self, **kwargs):
            sent.append(json.loads(kwargs["input"][1]["content"]))

            class Result:
                output_parsed = extraction()

            return Result()

    class Client:
        responses = Responses()

    previous = extraction(ambiguity="unclear AM/PM", date_text=None).model_dump(mode="json")
    await EventExtractor(Client(), "m").extract("this saturday", reference, previous)
    draft = sent[0]["original_draft"]
    assert draft["ambiguity"] is None
    assert draft["start_time_text"] == "7" and draft["title"] == "CCA dinner"
    assert previous["ambiguity"] == "unclear AM/PM"  # caller's pending state is untouched
