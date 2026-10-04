import re

from app.schemas.event import Event, Extraction


def match_updates(extraction: Extraction, events: list[Event]) -> list[Event]:
    """Require an explicit title; vague follow-ups always need a user selection."""
    query = extraction.match_title or extraction.title
    if not query:
        return events[:10]
    words = set(re.findall(r"\w+", query.casefold()))
    matches = [e for e in events if words and words <= set(re.findall(r"\w+", e.title.casefold()))]
    return matches[:10]
