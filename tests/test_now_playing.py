"""now_playing + the station-first radio sentence (docs/plans/MUSIC_ACCURACY_PLAN.md)."""
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
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

KITCHEN = {"entity_id": "media_player.kitchen_ma", "name": "kitchen voice pe", "area": "Kitchen",
           "state": "playing", "type": "player", "device_key": "up20f83b0ac919"}
OFFICE = {"entity_id": "media_player.satellite1", "name": "satellite-01-ma", "area": "Office",
          "state": "idle", "type": "player", "device_key": "up14c19fd8d1bc"}
KITCHEN_DEV = "e0949788663255952ce7778975527882"


# ── describe_playing: the sentence ──────────────────────────────────────────

def _state(state="playing", **attrs):
    return {"state": state, "attributes": attrs}


def test_sentence_names_track_artist_album_and_source():
    s = _state(media_title="Ratatung (Tung Tung Tung Sahur)", media_artist="Horror Skunx",
               media_album_name="Ratatung", media_content_id="ytmusic--kGmHoQDn://track/OF0G7_7sjkM")
    assert ts.describe_playing(s, "the Kitchen speaker") == \
        "Playing: Ratatung (Tung Tung Tung Sahur) by Horror Skunx, from the album Ratatung on the Kitchen speaker, from YouTube Music."


def test_sentence_for_stations_and_nas():
    # a library radio (how a Pandora station shows up once collected): album = station name,
    # the library scheme hides the provider → no source
    s = _state(media_title="Zankyosanka - From THE FIRST TAKE", media_artist="Aimer",
               media_album_name="Anime Pop", media_content_id="library://radio/15")
    assert ts.describe_playing(s, "the Kitchen speaker") == \
        "Playing: Zankyosanka - From THE FIRST TAKE by Aimer, on the Anime Pop station, on the Kitchen speaker."
    # a provider-native station keeps its source
    s = _state(media_title="Ayumi", media_artist="Yoshida Brothers", media_content_id="pandora://radio/15")
    assert ts.describe_playing(s, "the Basement Theater speaker") == \
        "Playing: Ayumi by Yoshida Brothers, on a radio station, on the Basement Theater speaker, from Pandora."
    s = _state(media_title="Across the Universe", media_artist="Fiona Apple", media_content_id="filesystem_local://track/abc")
    assert "from the NAS." in ts.describe_playing(s, "the Office speaker")


def test_sentence_when_nothing_or_unknown():
    assert ts.describe_playing(_state("idle"), "the Kitchen speaker") == "Nothing is playing on the Kitchen speaker."
    assert ts.describe_playing(_state("playing"), "the Kitchen speaker") == \
        "Something is playing on the Kitchen speaker, but the speaker didn't say what."
    assert ts.describe_playing(_state("paused", media_title="X", media_artist="Y"), "the Kitchen speaker") == \
        "Paused: X by Y on the Kitchen speaker."


# ── the route ───────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, body, status_code=200):
        self._body, self.status_code, self.text = body, status_code, json.dumps(body)

    def json(self):
        return self._body


class FakeHA:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        assert url.endswith("/api/template")
        return _Resp({"players": [KITCHEN, OFFICE],
                      "origin_key": KITCHEN["device_key"] if KITCHEN_DEV in json["template"] else ""})

    async def get(self, url, headers=None):
        entity = url.rsplit("/", 1)[1]
        if entity == KITCHEN["entity_id"]:
            return _Resp(_state(media_title="Soul Food (Radio Version)", media_artist="Goodie Mob",
                                media_content_id="ytmusic--x://track/uCUUN8xElyA"))
        return _Resp(_state("idle"))


@pytest.fixture
def env():
    with patch.object(ts.httpx, "AsyncClient", FakeHA), \
         patch.object(ts, "HA_TOKEN", "t"), \
         patch.object(ts, "MUSIC_DEFAULT_PLAYER", OFFICE["entity_id"]):
        yield


def test_route_reads_own_device(env):
    r = TestClient(ts.app).post("/music/now_playing", json={"origin_device": KITCHEN_DEV, "origin_area": "Kitchen"})
    assert r.status_code == 200
    assert r.json()["detail"] == "Playing: Soul Food (Radio Version) by Goodie Mob on the Kitchen speaker, from YouTube Music."
    assert r.json()["title"] == "Soul Food (Radio Version)"


def test_route_named_room_and_nothing_playing(env):
    r = TestClient(ts.app).post("/music/now_playing", json={"player": "the office"})
    assert r.status_code == 200 and r.json()["detail"] == "Nothing is playing on the Office speaker."


# ── orchestrator side ───────────────────────────────────────────────────────

class _Client:
    def __init__(self, status=200, body=None):
        self.sent, self._status, self._body = None, status, body or {"detail": "Playing: X by Y on the Kitchen speaker."}

    async def post(self, url, json=None):
        self.sent = (url, json)
        return types.SimpleNamespace(status_code=self._status, json=lambda: self._body, text=str(self._body))


@pytest.mark.asyncio
async def test_tool_carries_origin_and_speaks_verbatim():
    c = _Client()
    with origin.scope(origin.Origin(KITCHEN_DEV, "Kitchen")):
        out = await tools._tool_now_playing(c, {})
    assert c.sent == ("http://fake-tools:8003/music/now_playing",
                      {"origin_device": KITCHEN_DEV, "origin_area": "Kitchen"})
    assert agents._terminal_speech(out, tool="now_playing") == "Playing: X by Y on the Kitchen speaker."


@pytest.mark.asyncio
async def test_tool_failure_forbids_guessing():
    c = _Client(status=503, body={"detail": "the Kitchen speaker is unavailable."})
    out = await tools._tool_now_playing(c, {})
    assert "Do NOT guess" in out
    assert agents._terminal_speech(out, tool="now_playing") == "I couldn't tell. the Kitchen speaker is unavailable."


def test_home_agent_carries_now_playing_as_terminal():
    home = agents.AGENTS["home"]
    assert "now_playing" in home.tool_names and "now_playing" in home.terminal_tools


# ── routing pin ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "What song is this", " What's playing?", "what is this", "who is this", "Who sings this?",
    "what song is playing", "what am I listening to", "what's this song", "what artist is this",
    "what's playing right now",
])
@pytest.mark.asyncio
async def test_now_playing_questions_pin_to_home(text):
    route, rule = await routing._classify_inner(text)
    assert (route, rule) == ("home", "now_playing"), text


@pytest.mark.parametrize("text", [
    "what's the weather", "what is the capital of France", "who is the president", "what's up",
    "what song did the Beatles release first", "who is this actor in the movie",
])
@pytest.mark.asyncio
async def test_now_playing_pin_rejects_other_questions(text):
    _, rule = await routing._classify_inner(text)
    assert rule != "now_playing", text


# ── the fork carries the station-first sentence ─────────────────────────────

def test_fork_radio_trigger_has_station_first_sentence():
    class L(yaml.SafeLoader):
        pass
    L.add_constructor("!input", lambda l, n: {"__input__": l.construct_scalar(n)})
    fork = yaml.load(open(Path(__file__).resolve().parent.parent / "ha/blueprints/mass_assist_kronk.yaml"), Loader=L)
    sentences = fork["blueprint"]["input"]["trigger_response_settings"]["input"]["radio_trigger"]["default"]
    assert len(sentences) == 2
    assert "{media_name} (radio|station)" in sentences[1] and "pandora" in sentences[1]
    assert "((radio station)|(radio)|(station)) {media_name}" in sentences[0]   # upstream's kept
