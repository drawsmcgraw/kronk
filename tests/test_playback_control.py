"""Playback control by voice — the Kronk tier (docs/plans/PLAYBACK_CONTROL_PLAN.md).

"stop" reached Kronk with no tool to act on, so it asked "Stop what?" and,
in a test, claimed "I have paused" while the music played. These tests pin
the tool_service route (each action → its HA service, stop == pause, the
effect verified, HA's message surfaced), the tool's origin payload, the
spoken mapping, and the coordinator relaying the sentence verbatim.
"""
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "orchestrator"))
os.environ.setdefault("LLM_SERVICE_URL",     "http://fake-llm:8002")
os.environ.setdefault("TOOL_SERVICE_URL",    "http://fake-tools:8003")
os.environ.setdefault("HEALTH_SERVICE_URL",  "http://fake-health:8004")
os.environ.setdefault("FINANCE_SERVICE_URL", "http://fake-finance:8005")

import tool_service.main as ts  # noqa: E402
import agents  # noqa: E402
import origin  # noqa: E402
import routing  # noqa: E402
import tools   # noqa: E402


# ── routing: a bare playback command pins to the home agent ─────────────────

@pytest.mark.parametrize("text", [
    " Stop!", "stop", "Pause.", "pause the music", "resume", "Resume the music.",
    "unpause", "skip this song", "skip the track", "next", "next track", "shut up",
    "turn it up", "turn the volume down", "louder", "quieter", "please stop", "stop playing",
])
@pytest.mark.asyncio
async def test_bare_playback_commands_pin_to_home(text):
    route, rule = await routing._classify_inner(text)
    assert (route, rule) == ("home", "playback"), text


@pytest.mark.parametrize("text", [
    "stop the timer", "what is the next holiday", "how do I stop a leak", "pause my subscription",
    "skip the intro of the book", "the show was next level", "stop by the store on the way home",
    "play the next album by aerosmith",
])
@pytest.mark.asyncio
async def test_playback_pin_rejects_non_playback(text):
    _, rule = await routing._classify_inner(text)
    assert rule != "playback", text

OFFICE = {"entity_id": "media_player.satellite1", "name": "satellite-01-ma", "area": "Office",
          "state": "playing", "type": "player", "device_key": "up14c19fd8d1bc"}
KITCHEN = {"entity_id": "media_player.kitchen_ma", "name": "kitchen voice pe", "area": "Kitchen",
           "state": "idle", "type": "player", "device_key": "up20f83b0ac919"}
OFFICE_DEV = "ac6dd1f1c8cf1d229cda0f42224a2013"


# ── tool_service /music/control ─────────────────────────────────────────────

class _Resp:
    def __init__(self, body, status_code=200):
        self._body, self.status_code, self.text = body, status_code, json.dumps(body)

    def json(self):
        return self._body


class FakeHA:
    """Reads only: template → players (with live state), state polls → the
    per-entity state/volume the fake service call set."""
    states: dict = {}
    volume: float = 0.5

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        assert url.endswith("/api/template"), url
        players = [dict(p, state=FakeHA.states.get(p["entity_id"], p["state"])) for p in (OFFICE, KITCHEN)]
        return _Resp({"players": players, "origin_key": OFFICE["device_key"] if OFFICE_DEV in json["template"] else ""})

    async def get(self, url, headers=None):
        entity = url.rsplit("/", 1)[1]
        return _Resp({"state": FakeHA.states.get(entity, "idle"),
                      "attributes": {"media_artist": "Aerosmith", "media_title": "Jaded",
                                     "volume_level": FakeHA.volume}})


@pytest.fixture
def env():
    FakeHA.states = {OFFICE["entity_id"]: "playing", KITCHEN["entity_id"]: "idle"}
    FakeHA.volume = 0.5
    e = types.SimpleNamespace(calls=[], fail=None, effect=True)

    async def no_sleep(_):
        return None

    async def fake_call(domain, service, service_data=None, target=None):
        e.calls.append((domain, service, target))
        if e.fail:
            raise ts.HAServiceError(e.fail)
        if not e.effect:
            return
        for ent in target["entity_id"]:
            if service == "media_pause":
                FakeHA.states[ent] = "paused"
            elif service == "media_play":
                FakeHA.states[ent] = "playing"
            elif service == "media_next_track":
                FakeHA.states[ent] = "playing"
            elif service == "volume_up":
                FakeHA.volume = min(1.0, FakeHA.volume + 0.1)
            elif service == "volume_down":
                FakeHA.volume = max(0.0, FakeHA.volume - 0.1)

    with patch.object(ts.httpx, "AsyncClient", FakeHA), \
         patch.object(ts.asyncio, "sleep", new=no_sleep), \
         patch.object(ts, "ha_call_service", new=fake_call), \
         patch.object(ts, "HA_TOKEN", "t"), \
         patch.object(ts, "MUSIC_DEFAULT_PLAYER", KITCHEN["entity_id"]), \
         patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 3):
        yield e


def _ctl(**body):
    return TestClient(ts.app).post("/music/control", json=body)


def test_pause_on_own_device(env):
    r = _ctl(action="pause", origin_device=OFFICE_DEV, origin_area="Office")
    assert r.status_code == 200, r.text
    assert r.json()["detail"] == "Paused on the Office speaker."
    assert env.calls == [("media_player", "media_pause", {"entity_id": [OFFICE["entity_id"]]})]


def test_stop_means_pause(env):
    r = _ctl(action="stop", origin_device=OFFICE_DEV, origin_area="Office")
    assert r.status_code == 200 and r.json()["detail"] == "Paused on the Office speaker."
    assert env.calls[0][1] == "media_pause"


def test_resume_and_next_map_to_their_services(env):
    FakeHA.states[OFFICE["entity_id"]] = "paused"
    r = _ctl(action="resume", origin_area="Office")
    assert r.status_code == 200 and r.json()["detail"] == "Resumed on the Office speaker."
    assert env.calls[-1][1] == "media_play"
    r = _ctl(action="next", origin_area="Office")
    assert r.status_code == 200 and r.json()["detail"] == "Skipped on the Office speaker."
    assert env.calls[-1][1] == "media_next_track"


def test_resume_from_idle_works_when_the_queue_resumes(env):
    """MA reports a Sendspin player `idle` after pause (observed 2026-09-10)
    but media_play resumes its queue — so resume has no precondition and
    the verify poll decides."""
    FakeHA.states[OFFICE["entity_id"]] = "idle"
    r = _ctl(action="resume", origin_area="Office")
    assert r.status_code == 200 and r.json()["detail"] == "Resumed on the Office speaker."


def test_resume_with_nothing_to_resume_is_an_honest_failure(env):
    FakeHA.states[OFFICE["entity_id"]] = "idle"
    env.effect = False                                    # HA accepts, nothing starts
    r = _ctl(action="resume", origin_area="Office")
    assert r.status_code == 502 and "did not resume" in r.json()["detail"]


def test_volume_is_verified_by_the_level_moving(env):
    r = _ctl(action="volume_up", origin_area="Office")
    assert r.status_code == 200 and r.json()["detail"] == "Turned up to 60 percent on the Office speaker."
    FakeHA.volume = 1.0
    r = _ctl(action="volume_up", origin_area="Office")
    assert r.status_code == 200 and "already at full volume" in r.json()["detail"]


def test_pause_on_a_paused_speaker_is_a_calm_noop(env):
    """A 'pause' four seconds after a 'stop' (2026-09-14) was answered
    'I couldn't do that' — pausing nothing is a no-op, not a failure."""
    for action in ("pause", "stop"):
        r = _ctl(action=action, player="kitchen")          # kitchen is idle
        assert r.status_code == 200, r.text
        assert r.json()["detail"] == "The Kitchen speaker is already paused."
    assert env.calls == []                                 # no service call made


def test_skip_and_volume_with_nothing_playing_are_refused_clearly(env):
    for action in ("next", "volume_up"):
        r = _ctl(action=action, player="kitchen")
        assert r.status_code == 409
        assert r.json()["detail"] == "Nothing is playing on the Kitchen speaker."
    assert env.calls == []


def test_ha_error_message_is_surfaced(env):
    env.fail = "Entity media_player.satellite1 does not support this service."
    r = _ctl(action="pause", origin_area="Office")
    assert r.status_code == 502 and r.json()["detail"] == env.fail


def test_no_effect_is_a_failure_not_a_claim(env):
    env.effect = False                                    # HA said yes, nothing changed
    r = _ctl(action="pause", origin_area="Office")
    assert r.status_code == 502 and "did not pause" in r.json()["detail"]


def test_unknown_action(env):
    r = _ctl(action="rewind", origin_area="Office")
    assert r.status_code == 400 and "rewind" in r.json()["detail"]


# ── orchestrator side ───────────────────────────────────────────────────────

class _Client:
    def __init__(self, status=200, body=None):
        self.sent, self._status, self._body = None, status, body or {"detail": "Paused on the Office speaker."}

    async def post(self, url, json=None):
        self.sent = (url, json)
        return types.SimpleNamespace(status_code=self._status, json=lambda: self._body, text=json_dumps(self._body))


def json_dumps(o):
    return json.dumps(o)


@pytest.mark.asyncio
async def test_tool_payload_carries_action_and_origin():
    c = _Client()
    with origin.scope(origin.Origin(OFFICE_DEV, "Office")):
        out = await tools._tool_control_music(c, {"action": "stop"})
    assert c.sent == ("http://fake-tools:8003/music/control",
                      {"action": "stop", "origin_device": OFFICE_DEV, "origin_area": "Office"})
    assert out == "[Music control: Paused on the Office speaker.]"


@pytest.mark.asyncio
async def test_tool_failure_tells_the_model_not_to_claim():
    c = _Client(status=409, body={"detail": "Nothing is playing on the Office speaker."})
    out = await tools._tool_control_music(c, {"action": "pause"})
    assert out.startswith("[Could not control music: Nothing is playing on the Office speaker.]")
    assert "Do NOT claim it worked" in out


def test_terminal_speech_mappings():
    assert agents._terminal_speech("[Music control: Paused on the Office speaker.]", tool="control_music") \
        == "Paused on the Office speaker."
    assert agents._terminal_speech("[Could not control music: Nothing is playing on the Office speaker.]\nThe action FAILED",
                                   tool="control_music") \
        == "I couldn't do that. Nothing is playing on the Office speaker."


def test_home_agent_carries_the_tool_as_terminal():
    home = agents.AGENTS["home"]
    assert "control_music" in home.tool_names and "control_music" in home.terminal_tools
    names = [d["function"]["name"] for d in home.tool_defs()]
    assert "control_music" in names
    assert "stop" in agents.COORDINATOR.tool_defs()[[d["function"]["name"] for d in agents.COORDINATOR.tool_defs()].index("ask_home")]["function"]["description"].lower()


@pytest.mark.asyncio
async def test_stop_through_coordinator_is_relayed_verbatim():
    coord_prompt = agents.COORDINATOR.system_prompt[:60]
    home_prompt = agents.AGENTS["home"].system_prompt[:60]
    coord, home = [], []

    async def fake_stream(messages, model, tools=None):
        system = messages[0]["content"]
        if system.startswith(coord_prompt):
            coord.append(1)
            yield {"tool_calls": [{"id": "c1", "function": {"name": "ask_home", "arguments": {"query": "stop"}}}]}
        elif system.startswith(home_prompt):
            home.append(1)
            yield {"tool_calls": [{"id": "h1", "function": {"name": "control_music", "arguments": {"action": "stop"}}}]}
        yield {"usage": {}}

    async def fake_execute(name, args):
        assert (name, args) == ("control_music", {"action": "stop"})
        return "[Music control: Paused on the Basement Theater speaker.]"

    with patch("agents.llm.stream", new=fake_stream), patch("agents.tools.execute", new=fake_execute):
        events = [ev async for ev in agents.run_stream(agents.COORDINATOR, "Stop!", [])]
    text = "".join(e["text"] for e in events if e["type"] == "token")
    assert text == "Paused on the Basement Theater speaker."     # ends with a period → mic closes
    assert len(coord) == 1 and len(home) == 1                    # no synthesis round anywhere
