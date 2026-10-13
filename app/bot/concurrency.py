"""Run different people's updates at the same time, and each person's strictly in order.

One slow extraction or picture read used to hold up every other user, because updates were
handled one at a time. Updates are now serialised per user and per chat only: a quick
follow-up from the same person still waits for their previous message, and two members
tapping the same group card still take turns.
"""

import asyncio
from contextlib import asynccontextmanager

from telegram.ext import BaseUpdateProcessor

MAX_CONCURRENT_UPDATES = 32


class KeyedLocks:
    """A lock per key, created on demand and dropped once nobody holds or waits for it."""

    def __init__(self):
        self._locks: dict[object, list] = {}  # key -> [lock, number of holders and waiters]

    @asynccontextmanager
    async def hold(self, keys):
        # Sorted, so two updates that share keys always take them in the same order.
        ordered = sorted(set(keys), key=repr)
        counted, acquired = [], []
        try:
            for key in ordered:
                entry = self._locks.setdefault(key, [asyncio.Lock(), 0])
                entry[1] += 1
                counted.append(key)
                await entry[0].acquire()  # may be cancelled: counted, but not acquired
                acquired.append(key)
            yield
        finally:
            for key in reversed(acquired):
                self._locks[key][0].release()
            for key in counted:
                entry = self._locks[key]
                entry[1] -= 1
                if entry[1] == 0:
                    del self._locks[key]

    def __len__(self):
        return len(self._locks)


def update_keys(update) -> list:
    """What an update must not overlap with: its sender's other updates, and its chat's."""
    keys = []
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    if user is not None:
        keys.append(("user", user.id))
    if chat is not None and (user is None or chat.id != user.id):
        keys.append(("chat", chat.id))
    return keys or [("update", getattr(update, "update_id", id(update)))]


class PerUserProcessor(BaseUpdateProcessor):
    """Polling mode: up to MAX_CONCURRENT_UPDATES at once, ordered per user and per chat."""

    def __init__(self, max_concurrent_updates: int = MAX_CONCURRENT_UPDATES):
        super().__init__(max_concurrent_updates)
        self.locks = KeyedLocks()

    async def do_process_update(self, update, coroutine):
        try:
            async with self.locks.hold(update_keys(update)):
                await coroutine
        finally:
            # Cancelled while waiting its turn: close it rather than leave it never awaited.
            close = getattr(coroutine, "close", None)
            if close:
                close()

    async def initialize(self):
        pass

    async def shutdown(self):
        pass
