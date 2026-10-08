"""Music requests pin to the home agent (2026-10-05, MUSIC_ROUTING_PIN_PLAN).

The three phrasings from the incidents are the positives; the negatives
protect the coordinator's own tools (news brief) and the other pins.
"""
import pytest

import routing


@pytest.mark.parametrize("text", [
    " Start an orbital radio station on YouTube Music.",          # 2026-10-05, coordinator refused
    " Tell Kronk to start an orbital radio to play on YouTube music.",
    "play orbital radio",
    "Play the album Chronologic by Caravan Palace",
    "put on some jazz",
    "shuffle my youtube playlist video games pretty songs",
    "play the french lounge playlist on atlas",
    "listen to massive attack on pandora",
    "please play some music",
    "Kronk, play daft punk",
    "queue up the song halcyon by orbital",
    "play something from the nas",
])
@pytest.mark.asyncio
async def test_music_requests_pin_to_home(text):
    assert await routing._classify_inner(text) == ("home", "music"), text


@pytest.mark.parametrize("text", [
    "play the news",                      # news_brief belongs to the coordinator
    "play me the morning news brief",
    "play a game with me",
    "play a video about volcanoes",
    "start a timer for ten minutes",
    "start the dishwasher",
    "what's playing",                     # now_playing pin, not music
    "stop",                               # playback pin
    "what is the weather",                # weather pin
    "who played the song in the movie",   # not a request verb at the start
    "the band will play a station in Denver",
    "like massive attack radio",          # STT-mangled verb: documented miss
])
@pytest.mark.asyncio
async def test_music_pin_rejects(text):
    _, rule = await routing._classify_inner(text)
    assert rule != "music", text


@pytest.mark.asyncio
async def test_other_pins_keep_precedence():
    assert await routing._classify_inner("stop") == ("home", "playback")
    assert await routing._classify_inner("what song is this") == ("home", "now_playing")
    assert await routing._classify_inner("what's the weather like") == ("home", "weather")
