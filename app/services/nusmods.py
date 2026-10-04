"""NUSMods v1 class numbers, v2 indices and v3 lesson IDs; never pick a class."""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import httpx

from app.models.timetable import BusyBlock
from app.schemas.event import SG

API = "https://api.nusmods.com/v2"
TYPES = {
    "DLEC": "Design Lecture",
    "LAB": "Laboratory",
    "LEC": "Lecture",
    "PLAB": "Packaged Laboratory",
    "PLEC": "Packaged Lecture",
    "PTUT": "Packaged Tutorial",
    "REC": "Recitation",
    "SEC": "Sectional Teaching",
    "SEM": "Seminar-Style Module Class",
    "TUT": "Tutorial",
    "TUT2": "Tutorial Type 2",
    "TUT3": "Tutorial Type 3",
    "WS": "Workshop",
}
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
CALENDAR = json.loads((Path(__file__).parents[1] / "data" / "academic-calendar.json").read_text())


class ImportError(ValueError):
    pass


@dataclass
class ImportResult:
    blocks: list[BusyBlock]
    academic_year: str
    semester: int
    coverage_start: datetime
    coverage_end: datetime


def academic_year_for(reference: datetime) -> str:
    local = reference.astimezone(SG)
    first_year = local.year if local.month >= 8 else local.year - 1
    return f"{first_year}-{first_year + 1}"


def parse_link(link: str) -> tuple[int, dict[str, str]]:
    if len(link) > 12000:
        raise ImportError("That link is too long. Send the NUSMods Share/Sync link.")
    p = urlparse(link.strip())
    match = re.fullmatch(r"/timetable/sem-([1-4])/share/?", p.path)
    if (
        p.scheme not in ("https", "http")
        or p.netloc not in ("nusmods.com", "www.nusmods.com")
        or not match
    ):
        raise ImportError(
            "Send a NUSMods Share/Sync link: https://nusmods.com/timetable/sem-1/share?..."
        )
    pairs = parse_qsl(p.query, keep_blank_values=True)
    if any(k == "ta" and v for k, v in pairs):
        raise ImportError(
            "Teaching-assistant links need a separate import. Send your student timetable link without TA selections."
        )
    modules = {}
    for k, v in pairs:
        if k in ("hidden", "ta"):
            continue
        if not re.fullmatch(r"[A-Z]{2,8}\d{4}[A-Z]{0,3}", k) or k in modules:
            raise ImportError(
                "The share link has an invalid or duplicate course. Generate a fresh link in NUSMods."
            )
        modules[k] = v
    if not modules or len(modules) > 40:
        raise ImportError(
            "Add your courses in NUSMods and generate a new Share/Sync link (up to 40 courses)."
        )
    return int(match[1]), modules


def lesson_id(lesson: dict) -> str:
    weeks = lesson["weeks"]
    if isinstance(weeks, list):
        serialized = "_".join(map(str, weeks)) if weeks else "_"
    else:
        serialized = f"{weeks['start']}_{weeks['end']}_{weeks.get('weekInterval', 0)}"
        if "weeks" in weeks:
            serialized += "_" + ("_".join(map(str, weeks["weeks"])) or "_")
    return "|".join(
        (
            lesson["classNo"],
            lesson["day"][:3].upper(),
            lesson["startTime"],
            lesson["endTime"],
            lesson["venue"],
            serialized,
        )
    )


def selected_lessons(config: str, timetable: list[dict]) -> list[dict]:
    if not timetable:
        if config:
            raise ImportError("This course has no published lessons. Refresh the share link.")
        return []
    selected = []
    seen_types = set()
    if "(" in config:
        parts = re.findall(r"([A-Z0-9]+):\(([^()]*)\)", config)
        if not parts or re.sub(r"([A-Z0-9]+):\(([^()]*)\)", "", config).strip(";,"):
            raise ImportError("Invalid lesson selections. Generate a fresh NUSMods link.")
        groups = [(t, values.split(","), True) for t, values in parts]
    else:
        groups = []
        for part in config.split(","):
            if ":" not in part:
                raise ImportError(
                    "The link is missing class selections. Choose all classes in NUSMods first."
                )
            t, value = part.split(":", 1)
            groups.append((t, [value], False))
    for abbreviation, values, modern in groups:
        lesson_type = TYPES.get(abbreviation)
        if not lesson_type or lesson_type in seen_types:
            raise ImportError("Unknown or duplicate lesson type. Generate a fresh NUSMods link.")
        seen_types.add(lesson_type)
        matches = []
        for value in values:
            if modern and value.isdigit():
                index = int(value)
                if index >= len(timetable):
                    raise ImportError("The timetable changed. Generate a fresh NUSMods link.")
                matches.append(timetable[index])
            elif modern:
                matches.extend(x for x in timetable if lesson_id(x) == value)
            else:
                matches.extend(
                    x for x in timetable if x["classNo"] == value and x["lessonType"] == lesson_type
                )
        if not matches or any(x["lessonType"] != lesson_type for x in matches):
            raise ImportError("A selected class no longer exists. Refresh your NUSMods link.")
        if modern:
            class_nos = {x["classNo"] for x in matches}
            full_group = [
                x for x in timetable if x["lessonType"] == lesson_type and x["classNo"] in class_nos
            ]
            if len(class_nos) != 1 or {lesson_id(x) for x in full_group} != {
                lesson_id(x) for x in matches
            }:
                raise ImportError(
                    "The timetable changed or multiple classes were selected. Generate a fresh student timetable link."
                )
        selected.extend(matches)
    if seen_types != {x["lessonType"] for x in timetable}:
        raise ImportError(
            "Some class types are missing. Select every class in NUSMods and resend the share link."
        )
    return selected


def lesson_dates(lesson: dict, semester_start: date, semester: int) -> list[date]:
    day = DAYS.index(lesson["day"])
    weeks = lesson["weeks"]
    if isinstance(weeks, list):
        maximum = 13 if semester in (1, 2) else 6
        if any(not isinstance(w, int) or not 1 <= w <= maximum for w in weeks):
            raise ImportError("Unsupported lesson weeks; please check your NUSMods timetable.")
        return [
            semester_start
            + timedelta(weeks=w - 1 + (1 if semester in (1, 2) and w >= 7 else 0), days=day)
            for w in weeks
        ]
    start, end = date.fromisoformat(weeks["start"]), date.fromisoformat(weeks["end"])
    if end < start or (end - start).days > 366:
        raise ImportError("Unsupported lesson date range.")
    interval = weeks.get("weekInterval", 1)
    if not isinstance(interval, int) or interval <= 0:
        raise ImportError("Unsupported lesson week interval.")
    dates = []
    for offset in range((end - start).days + 1):
        current = start + timedelta(days=offset)
        week = offset // 7 + 1
        active = week in weeks["weeks"] if "weeks" in weeks else (week - 1) % interval == 0
        if current.weekday() == day and active:
            dates.append(current)
    return dates


class NUSModsImporter:
    def __init__(self, client: httpx.AsyncClient, academic_year: str = ""):
        self.client = client
        self.academic_year = academic_year

    async def import_link(self, link: str, reference: datetime) -> ImportResult:
        semester, modules = parse_link(link)
        year = self.academic_year or academic_year_for(reference)
        start_parts = CALENDAR.get(year.replace("-", "/"), {}).get(str(semester), {}).get("start")
        if not start_parts:
            raise ImportError(
                "Academic calendar unavailable. Ask the operator to refresh the bundled calendar."
            )
        start = date(*start_parts)
        blocks = []
        # At most 40 small requests, serial to respect NUSMods API load.
        for code, config in modules.items():
            response = await self.client.get(f"{API}/{year}/modules/{code}.json")
            if response.status_code == 404:
                raise ImportError(
                    f"{code} is unavailable for {year}. Check your timetable and academic year."
                )
            response.raise_for_status()
            data = response.json()
            sem = next((s for s in data["semesterData"] if s["semester"] == semester), None)
            if not sem:
                raise ImportError(f"{code} is unavailable in semester {semester}.")
            for lesson in selected_lessons(config, sem["timetable"]):
                for d in lesson_dates(lesson, start, semester):

                    def at(raw, lesson_date=d):
                        return datetime.combine(lesson_date, time(int(raw[:2]), int(raw[2:])), SG)

                    blocks.append(
                        BusyBlock(
                            title=f"{code} {lesson['lessonType']} ({lesson['classNo']})",
                            start_at=at(lesson["startTime"]),
                            end_at=at(lesson["endTime"]),
                            location=lesson.get("venue"),
                        )
                    )
            if sem.get("examDate"):
                duration = sem.get("examDuration")
                if not isinstance(duration, (int, float)) or duration <= 0:
                    raise ImportError(
                        f"{code} has an exam but no valid duration. Retry when NUSMods updates it."
                    )
                exam = datetime.fromisoformat(sem["examDate"].replace("Z", "+00:00"))
                if exam.tzinfo is None:
                    exam = exam.replace(tzinfo=SG)
                blocks.append(
                    BusyBlock(
                        title=f"{code} Exam",
                        start_at=exam,
                        end_at=exam + timedelta(minutes=duration),
                    )
                )
        coverage_start = datetime.combine(start, time(), SG)
        coverage_end = coverage_start + timedelta(weeks=18 if semester in (1, 2) else 7)
        if blocks:
            coverage_start = min(coverage_start, min(b.start_at for b in blocks))
            coverage_end = max(coverage_end, max(b.end_at for b in blocks))
        return ImportResult(blocks, year, semester, coverage_start, coverage_end)
