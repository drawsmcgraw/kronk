"""orchestrator/origin.py — the voice-satellite origin stamp and its
request-scoped propagation (docs/plans/VOICE_MUSIC_ORIGIN_KRONK_PLAN.md)."""
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "orchestrator"))
os.environ.setdefault("LLM_SERVICE_URL",     "http://fake-llm:8002")
os.environ.setdefault("TOOL_SERVICE_URL",    "http://fake-tools:8003")
os.environ.setdefault("HEALTH_SERVICE_URL",  "http://fake-health:8004")
os.environ.setdefault("FINANCE_SERVICE_URL", "http://fake-finance:8005")

import origin  # noqa: E402
import tools   # noqa: E402

STAMP = "[kronk-origin] device=ac6dd1f1c8cf1d229cda0f42224a2013 area=Office"
HA_PROMPT = ("You are a voice assistant for Home Assistant.\nAnswer in plain text.\n"
             + STAMP + "\nCurrent time is 15:41:00.")


# ── parsing ─────────────────────────────────────────────────────────────────

def test_parse_reads_device_and_area():
    o = origin.parse(HA_PROMPT)
    assert o == origin.Origin("ac6dd1f1c8cf1d229cda0f42224a2013", "Office")


@pytest.mark.parametrize("text", [None, "", "You are a voice assistant.", "[kronk-origin] device= area=",
                                  "[kronk-origin] device=None area=None"])
def test_parse_absent_or_empty_is_none(text):
    assert origin.parse(text) is None


def test_parse_device_without_area():
    # a satellite with no HA area assigned: device known, room unknown
    o = origin.parse("[kronk-origin] device=e0949788663255952ce7778975527882 area=None")
    assert o == origin.Origin("e0949788663255952ce7778975527882", None)


def test_from_messages_reads_only_system_messages():
    msgs = [{"role": "user", "content": STAMP},                         # a user can't stamp
            {"role": "system", "content": HA_PROMPT},
            {"role": "user", "content": "play some jazz"}]
    assert origin.from_messages(msgs) == origin.Origin("ac6dd1f1c8cf1d229cda0f42224a2013", "Office")
    assert origin.from_messages(msgs[:1] + msgs[2:]) is None


def test_from_messages_accepts_pydantic_like_objects():
    msgs = [SimpleNamespace(role="system", content=HA_PROMPT)]
    assert origin.from_messages(msgs).area == "Office"


def test_from_messages_web_ui_shape_has_no_origin():
    assert origin.from_messages([{"role": "user", "content": "hi"}]) is None
    assert origin.from_messages([]) is None


# ── request-scoped propagation ──────────────────────────────────────────────

def test_scope_sets_and_resets():
    assert origin.current.get() is None
    with origin.scope(origin.Origin("d", "Office")):
        assert origin.current.get().area == "Office"
    assert origin.current.get() is None


@pytest.mark.asyncio
async def test_concurrent_requests_keep_their_own_origin():
    seen = {}

    async def request(name, area):
        with origin.scope(origin.Origin(name, area)):
            await asyncio.sleep(0.01)          # interleave
            async def nested():                 # coordinator -> ask_home -> tool
                await asyncio.sleep(0.01)
                return origin.current.get().area
            seen[name] = await nested()

    await asyncio.gather(request("a", "Office"), request("b", "Kitchen"), request("c", None))
    assert seen == {"a": "Office", "b": "Kitchen", "c": None}
    assert origin.current.get() is None


# ── the tool carries it ─────────────────────────────────────────────────────

class _Client:
    def __init__(self):
        self.sent = None

    async def post(self, url, json=None):
        self.sent = (url, json)
        return SimpleNamespace(status_code=200,
                               json=lambda: {"player": "the Office speaker", "title": "t", "artist": "a"})


@pytest.mark.asyncio
async def test_play_music_payload_carries_origin():
    c = _Client()
    with origin.scope(origin.Origin("ac6dd1f1c8cf1d229cda0f42224a2013", "Office")):
        out = await tools._tool_play_music(c, {"query": "jazz"})
    url, payload = c.sent
    assert url.endswith("/music")
    assert payload == {"query": "jazz", "origin_device": "ac6dd1f1c8cf1d229cda0f42224a2013",
                       "origin_area": "Office"}
    assert "Music playing" in out


@pytest.mark.asyncio
async def test_play_music_payload_without_origin_is_unchanged():
    c = _Client()
    await tools._tool_play_music(c, {"query": "jazz", "player": "kitchen"})
    assert c.sent[1] == {"query": "jazz", "player": "kitchen"}


@pytest.mark.asyncio
async def test_play_music_spoken_player_still_travels_with_origin():
    c = _Client()
    with origin.scope(origin.Origin("dev", None)):
        await tools._tool_play_music(c, {"query": "jazz", "player": "the office"})
    assert c.sent[1] == {"query": "jazz", "player": "the office", "origin_device": "dev"}
