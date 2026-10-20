"""/feedback: anonymous by default, with an opt-in to attach the sender's handle."""

from app.bot.delivery import telegram_profile


def labels(reply):
    return [b.text for row in reply.keyboard.inline_keyboard for b in row]


async def test_feedback_with_text_asks_how_to_send(service_factory):
    service, _ = service_factory()
    reply = (await service.command(1, "feedback", "the guide confused me"))[0]
    assert "Send this feedback?" in reply.text
    assert labels(reply) == ["🕶 Send anonymously", "👤 Send with my @handle", "Cancel"]


async def test_anonymous_feedback_stores_no_identity(service_factory):
    service, repo = service_factory()
    telegram_profile.set(("angelbu", "Angel"))  # set, but anonymous must ignore it
    await service.command(1, "feedback", "add dark mode please")
    done = (await service.callback(1, "fb:anon"))[0]
    assert "Thanks" in done.text and done.edit
    assert repo.feedbacks == [{"message": "add dark mode please", "username": None, "name": None}]


async def test_named_feedback_attaches_the_handle(service_factory):
    service, repo = service_factory()
    telegram_profile.set(("angelbu", "Angel"))
    await service.command(1, "feedback", "love it")
    (await service.callback(1, "fb:named"))[0]
    assert repo.feedbacks[0]["username"] == "angelbu"
    assert repo.feedbacks[0]["name"] == "Angel"


async def test_feedback_typed_as_a_second_message(service_factory, reference):
    service, repo = service_factory()
    prompt = (await service.command(1, "feedback"))[0]
    assert "Type your feedback" in prompt.text
    ask = (await service.message(1, 9, "please add a widget", reference))[0]
    assert "Send this feedback?" in ask.text
    await service.callback(1, "fb:anon")
    assert repo.feedbacks[0]["message"] == "please add a widget"


async def test_feedback_cancel_sends_nothing(service_factory):
    service, repo = service_factory()
    await service.command(1, "feedback", "never mind")
    out = (await service.callback(1, "fb:cancel"))[0]
    assert "nothing sent" in out.text.lower()
    assert repo.feedbacks == []
