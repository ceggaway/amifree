from datetime import date, datetime

import httpx
import pytest

from app.schemas.event import SG
from app.services.nusmods import (
    ImportError,
    NUSModsImporter,
    lesson_dates,
    lesson_id,
    parse_link,
    selected_lessons,
)

LESSON = {
    "classNo": "1",
    "day": "Monday",
    "lessonType": "Lecture",
    "startTime": "1830",
    "endTime": "2030",
    "venue": "COM1",
    "weeks": [1, 6, 7, 13],
}


@pytest.mark.parametrize("config", ["LEC:1", "LEC:(0)", "LEC:(" + lesson_id(LESSON) + ")"])
def test_three_link_formats(config):
    assert selected_lessons(config, [LESSON]) == [LESSON]


def test_recess_and_alternating_weeks():
    dates = lesson_dates(LESSON, date(2026, 8, 10), 1)
    assert dates == [date(2026, 8, 10), date(2026, 9, 14), date(2026, 9, 28), date(2026, 11, 9)]
    assert date(2026, 9, 21) not in dates


def test_week_range_and_special_term():
    lesson = LESSON | {"weeks": {"start": "2026-01-12", "end": "2026-02-09", "weekInterval": 2}}
    assert lesson_dates(lesson, date(2026, 1, 12), 2) == [
        date(2026, 1, 12),
        date(2026, 1, 26),
        date(2026, 2, 9),
    ]
    lesson = LESSON | {"weeks": [1, 6]}
    assert lesson_dates(lesson, date(2026, 5, 11), 3) == [date(2026, 5, 11), date(2026, 6, 15)]


@pytest.mark.parametrize(
    "link",
    [
        "https://evil.com/timetable/sem-1/share?CS3216=LEC:1",
        "https://nusmods.com.evil.com/timetable/sem-1/share?CS3216=LEC:1",
        "https://nusmods.com/timetable/sem-1/share",
        "https://nusmods.com/timetable/sem-1/share?CS3216=LEC:1&CS3216=LEC:2",
        "https://nusmods.com/timetable/sem-1/share?CS3216=LEC:1&ta=CS3216",
    ],
)
def test_reject_invalid_and_untrusted_urls(link):
    with pytest.raises(ImportError):
        parse_link(link)


def test_incomplete_and_stale_classes_rejected():
    tutorial = LESSON | {"lessonType": "Tutorial"}
    with pytest.raises(ImportError, match="missing"):
        selected_lessons("LEC:1", [LESSON, tutorial])
    with pytest.raises(ImportError, match="no longer"):
        selected_lessons("LEC:99", [LESSON])
    with pytest.raises(ImportError):
        selected_lessons("LEC:(99)", [LESSON])


async def test_api_import_classes_exam_and_hidden(reference):
    def handler(request):
        assert str(request.url) == "https://api.nusmods.com/v2/2026-2027/modules/CS3216.json"
        return httpx.Response(
            200,
            json={
                "semesterData": [
                    {
                        "semester": 1,
                        "timetable": [LESSON],
                        "examDate": "2026-11-25T09:00:00+08:00",
                        "examDuration": 120,
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await NUSModsImporter(client).import_link(
            "https://nusmods.com/timetable/sem-1/share?CS3216=LEC:1&hidden=CS3216", reference
        )
    assert len(result.blocks) == 5
    assert result.blocks[-1].title == "CS3216 Exam"
    assert result.blocks[-1].end_at == datetime(2026, 11, 25, 11, tzinfo=SG)
    assert result.coverage_start == datetime(2026, 8, 10, tzinfo=SG)


async def test_malformed_api_response_and_missing_exam_duration(reference):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "semesterData": [
                        {
                            "semester": 1,
                            "timetable": [LESSON],
                            "examDate": "2026-11-25T09:00:00+08:00",
                        }
                    ]
                },
            )
        )
    ) as client:
        with pytest.raises(ImportError, match="duration"):
            await NUSModsImporter(client).import_link(
                "https://nusmods.com/timetable/sem-1/share?CS3216=LEC:1", reference
            )
