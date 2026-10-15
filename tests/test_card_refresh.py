"""Group cards are edited at most once per interval; a burst becomes one edit."""

import asyncio

from app.bot.handlers import CardRefresher


async def test_burst_of_changes_becomes_two_edits():
    edits = []

    async def edit(key):
        edits.append(key)

    refresher = CardRefresher(0.05, edit)
    await refresher.refresh("card")  # shown at once
    for _ in range(5):
        await refresher.refresh("card")  # combined into one later edit
    assert edits == ["card"]
    await asyncio.sleep(0.12)
    assert edits == ["card", "card"]
    await refresher.refresh("other")  # another card is not held back
    assert edits[-1] == "other"
    await asyncio.sleep(0.07)
    await refresher.refresh("card")  # the interval has passed: immediate again
    assert edits.count("card") == 3


async def test_interval_zero_edits_every_time():
    edits = []

    async def edit(key):
        edits.append(key)

    refresher = CardRefresher(0, edit)
    for _ in range(3):
        await refresher.refresh("card")
    assert edits == ["card"] * 3
