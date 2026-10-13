from app.services.ratelimit import RateLimiter
from app.services.scheduling import SLOW_DOWN
from tests.conftest import extraction


def test_window_slides_and_keys_are_independent():
    now = [0.0]
    limiter = RateLimiter(2, 10, clock=lambda: now[0])
    assert limiter.allow("a") and limiter.allow("a") and not limiter.allow("a")
    assert limiter.allow("b")
    now[0] = 10.1
    assert limiter.allow("a")


def test_memory_is_bounded():
    limiter = RateLimiter(1, 60, clock=lambda: 0.0, max_keys=10)
    for key in range(100):
        limiter.allow(key)
    assert len(limiter.hits) <= 10


async def test_a_flood_of_messages_stops_reaching_the_extractor(service_factory, reference):
    service, repo = service_factory(*[extraction() for _ in range(15)])
    replies = [
        (await service.message(1, n, "CCA dinner Friday 7-9pm at Vivo", reference))[0].text
        for n in range(2, 18)
    ]
    assert SLOW_DOWN not in replies[:15] and replies[15] == SLOW_DOWN
    assert len(service.extractor.calls) == 15  # the 16th never reached the LLM
    # Another person is not affected.
    service.extractor.items.append(extraction())
    assert (await service.message(2, 2, "CCA dinner Friday 7-9pm at Vivo", reference))[
        0
    ].text != SLOW_DOWN
