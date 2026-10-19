"""A poster with one time range and a room code like S16-06-118 is one event."""

from tests.test_ai_guards import extraction

POSTER = """👁🤖 See, Detect, Understand: An Introduction to Computer Vision ✨

Ever wondered how computers can “see” and understand the world? 👀 Join us for an introductory workshop to explore how machines interpret images, recognise objects, and extract meaningful information! 🚀

During the session, you’ll get to:

🔹 Discover how computers process images, from pixels to visual features

🔹 Explore image classification, object detection, segmentation, and landmark detection

🔹 Learn how pretrained vision models solve real-world problems

🔹 Build, train, and test your own hand gesture classifier in a hands-on project!

📅 Date: 21 October 2026
🕖 Time: 7:00 PM – 9:00 PM
📍 Venue: S16-06-118
💻 Mode: In person only

🔗 Sign up by scanning the QR code on the poster or using this link!
https://au.cglink.me/2sg/r384467

See you there! 💡"""


async def test_a_one_event_poster_is_not_called_two_events(service_factory, reference):
    service, _ = service_factory(
        extraction(
            title="Intro to Computer Vision",
            date_text="21 October 2026",
            start_time_text="7:00 PM",
            end_time_text="9:00 PM",
            location="S16-06-118",
        )
    )
    reply = await service.message(1, 1, POSTER, reference)
    assert "more than one event" not in reply[0].text
    assert "Intro to Computer Vision" in reply[0].text


def test_the_same_hours_written_two_ways_are_one_range():
    from app.services.date_resolution import time_ranges

    assert len(time_ranges("7-9pm\n7:00 PM – 9:00 PM\n1900-2100")) == 1
    assert len(time_ranges("Bake sale 11am-2pm, games 2-6pm")) == 2
