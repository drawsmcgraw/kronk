"""Pandora thumbs by voice (2026-10-06, MA_LOCAL_PANDORA_FEATURES_PLAN):
tool_service /music/rate → MA `pandora/feedback` (our provider patch), the
rate_music terminal tool, its speech, and the routing pin.
"""
import json
import types
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import agents
import routing
import tool_service.main as ts
import tools
from tests.test_playback_control import KITCHEN, OFFICE, OFFICE_DEV, FakeHA, _Resp


# ── routing pin ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "thumbs up", "Thumbs down.", "thumbs up this song", "thumbs down on this one",
    "I love this song", "I hate this song", "don't like this track", "never play this again",
    "Never play this song again.", "upvote", "downvote this", "more like this", "less like this one",
    "please thumbs up", "Kronk, thumbs down",
])
@pytest.mark.asyncio
async def test_rate_phrasings_pin_to_home(text):
    assert await routing._classify_inner(text) == ("home", "rate"), text


@pytest.mark.parametrize("text", [
    "I like this", "thumbs up emoji meaning", "what does thumbs up mean in Greece",
    "play this song again", "skip this song", "what song is this", "stop",
])
@pytest.mark.asyncio
async def test_rate_pin_rejects(text):
    _, rule = await routing._classify_inner(text)
    assert rule != "rate", text


# ── tool_service /music/rate ─────────────────────────────────────────────────

class RateFakeHA(FakeHA):
    """FakeHA whose state polls carry a content id and a title that changes on skip."""
    content: dict = {}
    titles: dict = {}

    async def get(self, url, headers=None):
        entity = url.rsplit("/", 1)[1]
        return _Resp({"state": FakeHA.states.get(entity, "idle"),
                      "attributes": {"media_artist": "Orbital", "media_title": RateFakeHA.titles.get(entity, "Halcyon"),
                                     "media_album_name": "Orbital Radio",
                                     "media_content_id": RateFakeHA.content.get(entity, ""),
                                     "volume_level": 0.5}})


@pytest.fixture
def env():
    FakeHA.states = {OFFICE["entity_id"]: "playing", KITCHEN["entity_id"]: "idle"}
    RateFakeHA.content = {OFFICE["entity_id"]: "pandora://track/TR:123"}
    RateFakeHA.titles = {OFFICE["entity_id"]: "Halcyon"}
    e = types.SimpleNamespace(ha_calls=[], ma_calls=[], ma_fail=None, ma_reply=None, skip_effect=True)

    async def no_sleep(_):
        return None

    async def fake_ha_call(domain, service, service_data=None, target=None):
        e.ha_calls.append((domain, service, target))
        if service == "media_next_track" and e.skip_effect:
            for ent in target["entity_id"]:
                RateFakeHA.titles[ent] = "Belfast"

    async def fake_ma(command, **args):
        e.ma_calls.append((command, args))
        if e.ma_fail:
            raise ts.MAError(e.ma_fail)
        return e.ma_reply if e.ma_reply is not None else {
            "song": "Halcyon + On + On", "artist": "Orbital", "station": "Orbital Radio", "positive": args["positive"]}

    with patch.object(ts.httpx, "AsyncClient", RateFakeHA), \
         patch.object(ts.asyncio, "sleep", new=no_sleep), \
         patch.object(ts, "ha_call_service", new=fake_ha_call), \
         patch.object(ts, "ma_command", new=fake_ma), \
         patch.object(ts, "HA_TOKEN", "t"), \
         patch.object(ts, "MUSIC_DEFAULT_PLAYER", KITCHEN["entity_id"]), \
         patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 3):
        yield e


def _rate(body):
    return TestClient(ts.app).post("/music/rate", json={"origin_device": OFFICE_DEV, "origin_area": "Office", **body})


def test_thumbs_up_rates_the_playing_pandora_track(env):
    resp = _rate({"thumb": "up"})
    assert resp.status_code == 200, resp.text
    assert env.ma_calls == [("pandora/feedback", {"item_id": "TR:123", "positive": True})]
    assert env.ha_calls == []                                   # no skip on thumbs up
    assert resp.json()["detail"] == "Thumbs up for Halcyon + On + On by Orbital on Orbital Radio."


def test_thumbs_down_rates_then_skips_and_verifies(env):
    resp = _rate({"thumb": "down"})
    assert resp.status_code == 200, resp.text
    assert env.ma_calls[0][1]["positive"] is False
    assert env.ha_calls == [("media_player", "media_next_track", {"entity_id": [OFFICE["entity_id"]]})]
    assert resp.json()["skipped"] is True
    assert resp.json()["detail"] == "Thumbs down."                    # short: the next song is already playing
    assert resp.json()["next"] == {"song": "Belfast", "artist": "Orbital"}
    assert resp.json()["song"] == "Halcyon + On + On"


def test_thumbs_down_whose_skip_does_not_take_says_so(env):
    env.skip_effect = False
    resp = _rate({"thumb": "down"})
    assert resp.status_code == 200
    assert resp.json()["skipped"] is False
    assert resp.json()["detail"] == "Thumbs down, but the skip didn't take."


def test_not_a_pandora_track_is_refused_before_calling_ma(env):
    RateFakeHA.content[OFFICE["entity_id"]] = "library://track/5420"
    resp = _rate({"thumb": "up"})
    assert resp.status_code == 409
    assert "not a Pandora station" in resp.json()["detail"]
    assert env.ma_calls == []


def test_nothing_playing_is_refused(env):
    FakeHA.states[OFFICE["entity_id"]] = "idle"
    resp = _rate({"thumb": "up"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Nothing is playing on the Office speaker."


def test_ma_error_text_is_surfaced_verbatim(env):
    env.ma_fail = "Pandora does not allow feedback on track TR:123"
    resp = _rate({"thumb": "up"})
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Pandora does not allow feedback on track TR:123"


def test_reply_falls_back_to_ha_attributes_when_ma_returns_little(env):
    env.ma_reply = {"positive": True}
    resp = _rate({"thumb": "up"})
    assert resp.json()["detail"] == "Thumbs up for Halcyon by Orbital on Orbital Radio."


def test_bad_thumb_value(env):
    assert _rate({"thumb": "sideways"}).status_code == 400


# ── the tool and its speech ──────────────────────────────────────────────────

class _Client:
    def __init__(self, status=200, body=None):
        self.sent = None; self._status = status; self._body = body or {"detail": "Thumbs up for X by Y on Z."}

    async def post(self, url, json=None):
        self.sent = (url, json)
        return SimpleNamespace(status_code=self._status, json=lambda: self._body, text=json_dumps(self._body))


def json_dumps(b):
    return json.dumps(b)


@pytest.mark.asyncio
async def test_tool_payload_and_speech():
    c = _Client()
    out = await tools._tool_rate_music(c, {"thumb": "up"})
    assert c.sent[0].endswith("/music/rate") and c.sent[1] == {"thumb": "up"}
    assert out == "[Music rated: Thumbs up for X by Y on Z.]"
    assert agents._terminal_speech(out, tool="rate_music") == "Thumbs up for X by Y on Z."


@pytest.mark.asyncio
async def test_tool_failure_tells_the_model_not_to_claim():
    c = _Client(status=409, body={"detail": "That's not a Pandora station — I can only rate Pandora tracks."})
    out = await tools._tool_rate_music(c, {"thumb": "down"})
    assert out.startswith("[Could not rate music: That's not a Pandora station")
    assert "Do NOT claim" in out
    assert agents._terminal_speech(out.split("\n")[0], tool="rate_music").startswith("I couldn't rate that. That's not a Pandora station")


def test_home_agent_carries_rate_music_as_terminal():
    home = agents.AGENTS["home"]
    assert "rate_music" in home.tool_names and "rate_music" in home.terminal_tools
    assert agents._tool_narration("rate_music", {"thumb": "down"}) == "thumbs down, skipping..."


# ── ma_command wire format (the import/protocol layer the endpoint tests mock) ─

class _FakeWS:
    """A scripted MA websocket: greeting, then replies keyed by message_id."""
    def __init__(self, replies):
        self.sent = []; self._replies = replies; self._greeted = False

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    async def recv(self):
        if not self._greeted:
            self._greeted = True
            return json.dumps({"server_id": "x", "server_version": "2.11.0b2", "schema_version": 30})
        last = json.loads(self.sent[-1])
        reply = self._replies[last["command"]]
        return json.dumps({"message_id": last["message_id"], **reply})

    async def send(self, data): self.sent.append(data)


def _connect_with(replies):
    def connect(url, **kw):
        connect.url = url; connect.ws = _FakeWS(replies); return connect.ws
    return connect


@pytest.mark.asyncio
async def test_ma_command_authenticates_then_sends_the_command():
    connect = _connect_with({"auth": {"result": {"ok": True}}, "pandora/feedback": {"result": {"song": "S"}}})
    with patch.object(ts.websockets, "connect", new=connect), patch.object(ts, "MA_TOKEN", "tok"), \
         patch.object(ts, "MA_URL", "http://ma:8095"):
        out = await ts.ma_command("pandora/feedback", item_id="TR:1", positive=True)
    assert out == {"song": "S"}
    assert connect.url == "ws://ma:8095/ws"
    sent = [json.loads(m) for m in connect.ws.sent]
    assert sent[0]["command"] == "auth" and sent[0]["args"] == {"token": "tok"}
    assert sent[1]["command"] == "pandora/feedback" and sent[1]["args"] == {"item_id": "TR:1", "positive": True}


@pytest.mark.asyncio
async def test_ma_command_raises_the_servers_details_on_error():
    connect = _connect_with({"auth": {"result": True},
                             "pandora/feedback": {"error_code": "media_not_found", "details": "Track TR:9 is not in any retained Pandora fragment"}})
    with patch.object(ts.websockets, "connect", new=connect), patch.object(ts, "MA_TOKEN", "tok"):
        with pytest.raises(ts.MAError) as ei:
            await ts.ma_command("pandora/feedback", item_id="TR:9", positive=False)
    assert str(ei.value) == "Track TR:9 is not in any retained Pandora fragment"


@pytest.mark.asyncio
async def test_ma_command_without_a_token_is_a_named_failure():
    with patch.object(ts, "MA_TOKEN", ""):
        with pytest.raises(ts.MAError, match="MA_TOKEN not configured"):
            await ts.ma_command("providers")
