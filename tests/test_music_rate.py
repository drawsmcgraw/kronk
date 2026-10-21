"""Pandora thumbs by voice (2026-10-06, MA_LOCAL_PANDORA_FEATURES_PLAN):
tool_service /music/rate → MA `music/favorites/set_item` (like/dislike, which
the Pandora provider forwards as thumbs), the rate_music terminal tool, its
speech, and the routing pin.
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
    e = types.SimpleNamespace(ha_calls=[], ma_calls=[], ma_fail=None, skip_effect=True, spawned=[])

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
        return None                                             # set_item returns nothing

    def fake_like(uri, what):
        return ("like", uri, what)                              # stands in for the coroutine

    with patch.object(ts.httpx, "AsyncClient", RateFakeHA), \
         patch.object(ts, "like_in_background", new=fake_like), \
         patch.object(ts, "_spawn", new=e.spawned.append), \
         patch.object(ts.asyncio, "sleep", new=no_sleep), \
         patch.object(ts, "ha_call_service", new=fake_ha_call), \
         patch.object(ts, "ma_command", new=fake_ma), \
         patch.object(ts, "HA_TOKEN", "t"), \
         patch.object(ts, "MUSIC_DEFAULT_PLAYER", KITCHEN["entity_id"]), \
         patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 3):
        yield e


def _rate(body):
    return TestClient(ts.app).post("/music/rate", json={"origin_device": OFFICE_DEV, "origin_area": "Office", **body})


def test_thumbs_up_hands_the_like_off_and_answers_at_once(env):
    resp = _rate({"thumb": "up"})
    assert resp.status_code == 200, resp.text
    assert env.spawned == [("like", "pandora://track/TR:123", "Halcyon by Orbital")]
    assert env.ma_calls == []                                   # nothing awaited on MA
    assert env.ha_calls == []                                   # no skip on thumbs up
    assert resp.json()["detail"] == "Thumbs up for Halcyon by Orbital."
    assert resp.json()["confirmed"] is False


def test_thumbs_down_rates_then_skips_and_verifies(env):
    resp = _rate({"thumb": "down"})
    assert resp.status_code == 200, resp.text
    assert env.ma_calls == [("music/favorites/set_item", {"item": "pandora://track/TR:123", "favorite": False})]
    assert env.ha_calls == [("media_player", "media_next_track", {"entity_id": [OFFICE["entity_id"]]})]
    assert resp.json()["skipped"] is True
    assert resp.json()["detail"] == "Thumbs down."                    # short: the next song is already playing
    assert resp.json()["next"] == {"song": "Belfast", "artist": "Orbital"}
    assert resp.json()["song"] == "Halcyon"


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
    resp = _rate({"thumb": "down"})
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Pandora does not allow feedback on track TR:123"


def test_dislike_is_sent_before_the_skip(env):
    """The dislike goes out while the disliked track is still current, then the skip."""
    order = []
    env_ma, env_ha = ts.ma_command, ts.ha_call_service

    async def ma(command, **args):
        order.append("ma"); return await env_ma(command, **args)

    async def ha(*a, **k):
        order.append("skip"); return await env_ha(*a, **k)

    with patch.object(ts, "ma_command", new=ma), patch.object(ts, "ha_call_service", new=ha):
        assert _rate({"thumb": "down"}).status_code == 200
    assert order == ["ma", "skip"]


def test_dislike_failure_is_not_skipped_over(env):
    env.ma_fail = "Track TR:123 is not in any retained Pandora fragment"
    resp = _rate({"thumb": "down"})
    assert resp.status_code == 502
    assert env.ha_calls == []                                   # no skip when MA refused the dislike


def test_bad_thumb_value(env):
    assert _rate({"thumb": "sideways"}).status_code == 400


# ── the background like and the operator alert ──────────────────────────────

@pytest.fixture
def like_env():
    e = types.SimpleNamespace(ma_calls=[], ma_fail=None, notes=[], notify_fail=None)

    async def fake_ma(command, _timeout=20, **args):
        e.ma_calls.append((command, _timeout, args))
        if e.ma_fail:
            raise e.ma_fail

    async def fake_ha_call(domain, service, service_data=None, target=None):
        if e.notify_fail:
            raise ts.HAServiceError(e.notify_fail)
        e.notes.append((domain, service, service_data))

    with patch.object(ts, "ma_command", new=fake_ma), \
         patch.object(ts, "ha_call_service", new=fake_ha_call), \
         patch.object(ts, "HA_NOTIFY_SERVICE", "notify/mobile_app_test_phone"):
        yield e


@pytest.mark.asyncio
async def test_background_like_waits_out_mas_metadata_scan_and_stays_quiet(like_env):
    await ts.like_in_background("pandora://track/TR:1", "Halcyon by Orbital")
    assert like_env.ma_calls == [("music/favorites/set_item", ts.MA_LIKE_TIMEOUT_S,
                                  {"item": "pandora://track/TR:1", "favorite": True})]
    assert ts.MA_LIKE_TIMEOUT_S >= 60                            # a like measured 24.6–31.1 s
    assert like_env.notes == []                                 # success: nobody is bothered


@pytest.mark.asyncio
async def test_failed_background_like_pushes_the_operator_with_mas_error(like_env):
    like_env.ma_fail = ts.MAError("Track TR:1 is not in any retained Pandora fragment")
    await ts.like_in_background("pandora://track/TR:1", "Halcyon by Orbital")
    assert like_env.notes == [("notify", "mobile_app_test_phone", {
        "title": "Kronk: thumbs up failed",
        "message": "Halcyon by Orbital (pandora://track/TR:1): Track TR:1 is not in any retained Pandora fragment",
        "data": {"tag": "kronk-music-rate"}})]


@pytest.mark.asyncio
async def test_unexpected_errors_reach_the_operator_too(like_env):
    like_env.ma_fail = RuntimeError()
    await ts.like_in_background("pandora://track/TR:1", "Halcyon")
    assert like_env.notes[0][2]["message"] == "Halcyon (pandora://track/TR:1): RuntimeError"


@pytest.mark.asyncio
async def test_a_failed_notification_is_logged_not_raised(like_env, caplog):
    like_env.ma_fail = ts.MAError("boom")
    like_env.notify_fail = "Unable to find service notify.mobile_app_test_phone"
    await ts.like_in_background("pandora://track/TR:1", "Halcyon")   # must not raise
    assert "operator notification failed" in caplog.text and "boom" in caplog.text


@pytest.mark.asyncio
async def test_spawn_keeps_a_reference_until_the_task_finishes():
    import asyncio
    done = asyncio.Event()

    async def work():
        await done.wait()

    task = ts._spawn(work())
    assert task in ts._BACKGROUND_TASKS
    done.set(); await task; await asyncio.sleep(0)
    assert task not in ts._BACKGROUND_TASKS


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
    connect = _connect_with({"auth": {"result": {"ok": True}}, "music/favorites/set_item": {"result": None}})
    with patch.object(ts.websockets, "connect", new=connect), patch.object(ts, "MA_TOKEN", "tok"), \
         patch.object(ts, "MA_URL", "http://ma:8095"):
        out = await ts.ma_command("music/favorites/set_item", item="pandora://track/TR:1", favorite=True)
    assert out is None
    assert connect.url == "ws://ma:8095/ws"
    sent = [json.loads(m) for m in connect.ws.sent]
    assert sent[0]["command"] == "auth" and sent[0]["args"] == {"token": "tok"}
    assert sent[1]["command"] == "music/favorites/set_item"
    assert sent[1]["args"] == {"item": "pandora://track/TR:1", "favorite": True}


@pytest.mark.asyncio
async def test_ma_command_raises_the_servers_details_on_error():
    connect = _connect_with({"auth": {"result": True},
                             "music/favorites/set_item": {"error_code": "media_not_found", "details": "Track TR:9 is not in any retained Pandora fragment"}})
    with patch.object(ts.websockets, "connect", new=connect), patch.object(ts, "MA_TOKEN", "tok"):
        with pytest.raises(ts.MAError) as ei:
            await ts.ma_command("music/favorites/set_item", item="pandora://track/TR:9", favorite=True)
    assert str(ei.value) == "Track TR:9 is not in any retained Pandora fragment"


@pytest.mark.asyncio
async def test_ma_command_without_a_token_is_a_named_failure():
    with patch.object(ts, "MA_TOKEN", ""):
        with pytest.raises(ts.MAError, match="MA_TOKEN not configured"):
            await ts.ma_command("providers")


@pytest.mark.asyncio
async def test_ma_command_timeout_is_ours_not_an_ma_argument():
    connect = _connect_with({"auth": {"result": {"ok": True}}, "music/favorites/set_item": {"result": None}})
    with patch.object(ts.websockets, "connect", new=connect), patch.object(ts, "MA_TOKEN", "tok"), \
         patch.object(ts, "MA_URL", "http://ma:8095"):
        await ts.ma_command("music/favorites/set_item", _timeout=5, item="pandora://track/TR:1", favorite=True)
    sent = [json.loads(m) for m in connect.ws.sent]
    assert sent[1]["args"] == {"item": "pandora://track/TR:1", "favorite": True}
