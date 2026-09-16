"""Shuffle through Kronk's play_music tool (2026-09-16).

"Shuffle my YouTube playlist X" fell through the blueprint to Kronk, whose
tool had no shuffle at all, so the playlist played front to back. The tool
now carries a `shuffle` flag; tool_service sets it on the player (see
test_error_surfacing) and reports the player's own state back, which is
what gets spoken.
"""
from types import SimpleNamespace

import pytest

import agents
import tools


class _Client:
    def __init__(self, shuffle_reported):
        self.sent = None
        self._shuffle = shuffle_reported

    async def post(self, url, json=None):
        self.sent = (url, json)
        return SimpleNamespace(status_code=200, json=lambda: {
            "player": "the Kitchen speaker", "title": "City Ruins", "artist": "Keiichi Okabe",
            "shuffle": self._shuffle})


@pytest.mark.asyncio
async def test_shuffle_travels_and_is_spoken():
    c = _Client(shuffle_reported=True)
    out = await tools._tool_play_music(c, {"query": "Video Games Pretty Songs",
                                           "media_type": "playlist", "shuffle": True})
    assert c.sent[1] == {"query": "Video Games Pretty Songs", "media_type": "playlist", "shuffle": True}
    assert out == "[Music playing: City Ruins by Keiichi Okabe on the Kitchen speaker, shuffled]"
    assert agents._terminal_speech(out, tool="play_music") == \
        "Now playing City Ruins by Keiichi Okabe on the Kitchen speaker, shuffled."


@pytest.mark.asyncio
async def test_shuffle_omitted_or_false_is_not_sent():
    c = _Client(shuffle_reported=False)
    out = await tools._tool_play_music(c, {"query": "jazz", "shuffle": False})
    assert c.sent[1] == {"query": "jazz"}
    assert "shuffled" not in out


@pytest.mark.asyncio
async def test_spoken_state_comes_from_the_player_not_the_request():
    # Asked to shuffle, but the player did not take it: the reply must not claim it did.
    c = _Client(shuffle_reported=False)
    out = await tools._tool_play_music(c, {"query": "jazz", "shuffle": True})
    assert c.sent[1] == {"query": "jazz", "shuffle": True}
    assert "shuffled" not in out


def test_narration_says_shuffling():
    assert agents._tool_narration("play_music", {"query": "X", "shuffle": True}) == "shuffling X"
    assert agents._tool_narration("play_music", {"query": "X"}) == "putting on X"
