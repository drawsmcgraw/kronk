"""Real errors reach the speaker (docs/plans/ERROR_SURFACING_PLAN.md).

Three drops were found for "play Portishead Radio" (Pandora 429 underneath):
HA's REST API answers a bare 500, tool_service spoke a generic sentence,
and the coordinator reworded the specialist's terminal sentence. These
tests pin each fix: the websocket service call carries HA's message, the
music route speaks it (after still verifying playback), and a delegated
terminal result passes through the coordinator verbatim.
"""
import asyncio
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

MA_MSG = "Playback failed for Portishead Radio - no more tracks available"


# ── ha_call_service: the websocket carries HA's message ─────────────────────

class _FakeWS:
    """Scripted HA websocket: auth handshake, then one call_service result."""
    def __init__(self, result: dict, hello="auth_required", auth="auth_ok"):
        self._result = result
        self.sent: list[dict] = []
        self._inbox = [json.dumps({"type": hello})]
        self._auth = auth

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, raw):
        msg = json.loads(raw)
        self.sent.append(msg)
        if msg["type"] == "auth":
            self._inbox.append(json.dumps({"type": self._auth}))
        elif msg["type"] == "call_service":
            self._inbox.append(json.dumps({"type": "event", "id": 99}))   # unrelated frame, must be skipped
            self._inbox.append(json.dumps({"id": msg["id"], "type": "result", **self._result}))

    async def recv(self):
        return self._inbox.pop(0)


def _install_fake_websockets(ws: _FakeWS):
    mod = types.ModuleType("websockets")
    mod.connect = lambda *a, **kw: ws
    return patch.dict(sys.modules, {"websockets": mod})


@pytest.mark.asyncio
async def test_call_service_success_sends_target_and_data():
    ws = _FakeWS({"success": True, "result": {}})
    with _install_fake_websockets(ws), patch.object(ts, "HA_TOKEN", "tok"):
        await ts.ha_call_service("music_assistant", "play_media", {"media_id": "x"},
                                 target={"entity_id": ["media_player.a"]})
    call = [m for m in ws.sent if m["type"] == "call_service"][0]
    assert call["domain"] == "music_assistant" and call["service"] == "play_media"
    assert call["service_data"] == {"media_id": "x"} and call["target"] == {"entity_id": ["media_player.a"]}
    assert ws.sent[0] == {"type": "auth", "access_token": "tok"}


@pytest.mark.asyncio
async def test_call_service_failure_raises_with_ha_message():
    ws = _FakeWS({"success": False, "error": {"code": "home_assistant_error", "message": MA_MSG}})
    with _install_fake_websockets(ws), patch.object(ts, "HA_TOKEN", "tok"):
        with pytest.raises(ts.HAServiceError) as ei:
            await ts.ha_call_service("music_assistant", "play_media", {"media_id": "x"})
    assert str(ei.value) == MA_MSG


@pytest.mark.asyncio
async def test_call_service_bad_token_is_specific():
    ws = _FakeWS({"success": True}, auth="auth_invalid")
    with _install_fake_websockets(ws), patch.object(ts, "HA_TOKEN", "tok"):
        with pytest.raises(ts.HAServiceError) as ei:
            await ts.ha_call_service("x", "y")
    assert "rejected the token" in str(ei.value)


@pytest.mark.asyncio
async def test_call_service_unreachable_is_specific():
    mod = types.ModuleType("websockets")

    def boom(*a, **kw):
        raise OSError("connect refused")
    mod.connect = boom
    with patch.dict(sys.modules, {"websockets": mod}):
        with pytest.raises(ts.HAServiceError) as ei:
            await ts.ha_call_service("x", "y")
    assert "unreachable" in str(ei.value)


# ── /music: HA's message is what gets spoken; playback is still verified ───

PLAYERS = [{"entity_id": "media_player.kitchen_ma", "name": "kitchen voice pe", "area": "Kitchen",
            "state": "idle", "type": "player", "device_key": "up1"}]


class _Resp:
    def __init__(self, body, status_code=200):
        self._body, self.status_code, self.text = body, status_code, json.dumps(body)

    def json(self):
        return self._body


class FakeHA:
    """httpx stand-in for the reads (template + state polls)."""
    playing = False
    shuffle = False          # the player's own shuffle attribute

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        assert url.endswith("/api/template"), url          # play_media no longer goes over REST
        return _Resp({"players": PLAYERS, "origin_key": ""})

    async def get(self, url, headers=None):
        return _Resp({"state": "playing" if FakeHA.playing else "idle",
                      "attributes": {"media_artist": "Portishead", "media_title": "Glory Box",
                                     "shuffle": FakeHA.shuffle}})


@pytest.fixture
def music_env():
    FakeHA.playing = False
    FakeHA.shuffle = False
    env = types.SimpleNamespace(calls=[], fail=None)

    async def no_sleep(_):
        return None

    async def fake_call(domain, service, service_data=None, target=None):
        env.calls.append((domain, service, service_data, target))
        if env.fail:
            raise ts.HAServiceError(env.fail)
        if service == "shuffle_set":                 # the player takes the setting
            FakeHA.shuffle = service_data["shuffle"]

    with patch.object(ts.httpx, "AsyncClient", FakeHA), \
         patch.object(ts.asyncio, "sleep", new=no_sleep), \
         patch.object(ts, "ha_call_service", new=fake_call), \
         patch.object(ts, "HA_TOKEN", "t"), \
         patch.object(ts, "MUSIC_DEFAULT_PLAYER", "media_player.kitchen_ma"), \
         patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 0):
        yield env


def test_music_failure_speaks_ha_message(music_env):
    music_env.fail = MA_MSG
    resp = TestClient(ts.app).post("/music", json={"query": "Portishead Radio", "media_type": "radio"})
    assert resp.status_code == 502
    assert resp.json()["detail"] == MA_MSG                      # verbatim, no "(HTTP 500)"
    domain, service, data, target = music_env.calls[0]
    assert (domain, service) == ("music_assistant", "play_media")
    assert data == {"media_id": "Portishead Radio", "media_type": "radio"}
    assert target == {"entity_id": ["media_player.kitchen_ma"]}


def test_music_failure_without_subject_gets_a_prefix(music_env):
    music_env.fail = "Home Assistant is unreachable (OSError)."
    resp = TestClient(ts.app).post("/music", json={"query": "jazz"})
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Home Assistant is unreachable (OSError)."
    music_env.fail = "Unknown error"
    resp = TestClient(ts.app).post("/music", json={"query": "jazz"})
    assert resp.json()["detail"] == "Playback failed: Unknown error"


def test_music_failure_unwraps_ma_list_repr(music_env):
    """MA: "Could not resolve ['Zorblax Radio'] to playable media item" — a
    TTS engine would read the brackets and quotes aloud."""
    music_env.fail = "Could not resolve ['Zorblax Radio'] to playable media item"
    resp = TestClient(ts.app).post("/music", json={"query": "Zorblax Radio", "media_type": "radio"})
    assert resp.json()["detail"] == "Playback failed: Could not resolve Zorblax Radio to playable media item"


def test_music_failed_call_but_player_plays_is_success(music_env):
    """MA has failed the call and played anyway (2026-09-04, 'put on some jazz')."""
    music_env.fail = MA_MSG
    FakeHA.playing = True
    with patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 5):
        resp = TestClient(ts.app).post("/music", json={"query": "jazz"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "playing" and resp.json()["title"] == "Glory Box"


def test_music_success_path_unchanged(music_env):
    FakeHA.playing = True
    with patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 5):
        resp = TestClient(ts.app).post("/music", json={"query": "Portishead"})
    assert resp.status_code == 200 and resp.json()["player"] == "the Kitchen speaker"


# ── Shuffle: a player setting, set explicitly on every play (2026-09-16) ────

def test_music_shuffle_requested_sets_the_player_and_reports_it(music_env):
    FakeHA.playing = True
    with patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 5):
        resp = TestClient(ts.app).post("/music", json={"query": "Video Games Pretty Songs",
                                                       "media_type": "playlist", "shuffle": True})
    assert resp.status_code == 200
    assert resp.json()["shuffle"] is True                    # read back from the player
    services = [(d, sv, data) for d, sv, data, _ in music_env.calls]
    assert services[0][:2] == ("music_assistant", "play_media")
    assert services[1] == ("media_player", "shuffle_set", {"shuffle": True})
    assert music_env.calls[1][3] == {"entity_id": ["media_player.kitchen_ma"]}


def test_music_without_shuffle_turns_a_shuffling_player_off(music_env):
    FakeHA.playing = True
    FakeHA.shuffle = True                                     # left on by an earlier request
    with patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 5):
        resp = TestClient(ts.app).post("/music", json={"query": "Daisies of the Galaxy", "media_type": "album"})
    assert resp.status_code == 200
    assert resp.json()["shuffle"] is False
    assert [sv for _, sv, _, _ in music_env.calls] == ["play_media", "shuffle_set"]
    assert music_env.calls[1][2] == {"shuffle": False}


def test_music_no_shuffle_call_when_the_player_already_matches(music_env):
    FakeHA.playing = True
    with patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 5):
        resp = TestClient(ts.app).post("/music", json={"query": "jazz"})
    assert resp.status_code == 200 and resp.json()["shuffle"] is False
    assert [sv for _, sv, _, _ in music_env.calls] == ["play_media"]


# ── coordinator passthrough: a delegated terminal result is relayed verbatim ─

def _streams(coordinator_calls: list, home_calls: list, synth_text="Rewritten by the coordinator."):
    """fake llm.stream that tells agents apart by their system prompt and
    counts rounds per agent."""
    coord_prompt = agents.COORDINATOR.system_prompt[:60]
    home_prompt = agents.AGENTS["home"].system_prompt[:60]

    async def fake_stream(messages, model, tools=None):
        system = messages[0]["content"]
        if system.startswith(coord_prompt):
            coordinator_calls.append(messages)
            if len(coordinator_calls) == 1:
                yield {"tool_calls": [{"id": "c1", "function": {"name": "ask_home",
                                                                 "arguments": {"query": "play Portishead Radio"}}}]}
            else:
                yield {"token": synth_text}
        elif system.startswith(home_prompt):
            home_calls.append(messages)
            if len(home_calls) == 1:
                yield {"tool_calls": [{"id": "h1", "function": {"name": "play_music",
                                                                 "arguments": {"query": "Portishead Radio",
                                                                               "media_type": "radio"}}}]}
            else:
                yield {"token": "home synthesis"}
        else:
            raise AssertionError("unexpected agent")
        yield {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    return fake_stream


@pytest.mark.asyncio
async def test_delegated_terminal_failure_passes_through_verbatim():
    coord, home = [], []

    async def fake_execute(name, args):
        assert name == "play_music"
        return f"[Could not play music: {MA_MSG}]\nPlayback FAILED — tell the user it failed and why."

    with patch("agents.llm.stream", new=_streams(coord, home)), \
         patch("agents.tools.execute", new=fake_execute):
        events = [ev async for ev in agents.run_stream(agents.COORDINATOR, "play Portishead Radio", [])]

    text = "".join(e["text"] for e in events if e["type"] == "token")
    assert text == f"I couldn't play that. {MA_MSG}"           # the specialist's sentence, untouched
    assert len(coord) == 1 and len(home) == 1                  # no synthesis round anywhere
    done = [e for e in events if e["type"] == "done"][0]
    assert done["ok"] is True and done["terminal"] == "ask_home"


@pytest.mark.asyncio
async def test_delegated_terminal_success_passes_through_verbatim():
    coord, home = [], []

    async def fake_execute(name, args):
        return "[Music playing: Glory Box by Portishead on the Kitchen speaker]"

    with patch("agents.llm.stream", new=_streams(coord, home)), \
         patch("agents.tools.execute", new=fake_execute):
        events = [ev async for ev in agents.run_stream(agents.COORDINATOR, "play Portishead", [])]

    text = "".join(e["text"] for e in events if e["type"] == "token")
    assert text == "Now playing Glory Box by Portishead on the Kitchen speaker."
    assert len(coord) == 1


@pytest.mark.asyncio
async def test_non_terminal_delegation_still_synthesizes():
    """A specialist answering with ordinary text is still relayed by the
    coordinator's own round — only terminal results skip it."""
    coord, home = [], []
    coord_prompt = agents.COORDINATOR.system_prompt[:60]

    async def fake_stream(messages, model, tools=None):
        if messages[0]["content"].startswith(coord_prompt):
            coord.append(1)
            if len(coord) == 1:
                yield {"tool_calls": [{"id": "c1", "function": {"name": "ask_home",
                                                                 "arguments": {"query": "hot tub temp"}}}]}
            else:
                yield {"token": "The hot tub is at 102 degrees."}
        else:
            home.append(1)
            yield {"token": "102°F, heater on."}
        yield {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    with patch("agents.llm.stream", new=fake_stream):
        events = [ev async for ev in agents.run_stream(agents.COORDINATOR, "how warm is the hot tub", [])]

    text = "".join(e["text"] for e in events if e["type"] == "token")
    assert text == "The hot tub is at 102 degrees."
    assert len(coord) == 2 and len(home) == 1
    assert "terminal" not in [e for e in events if e["type"] == "done"][0]


@pytest.mark.asyncio
async def test_run_delegated_reports_terminal_flag():
    async def fake_stream(messages, model, tools=None):
        yield {"tool_calls": [{"id": "h1", "function": {"name": "play_music", "arguments": {"query": "x"}}}]}
        yield {"usage": {}}

    async def fake_execute(name, args):
        return "[Music playing: x on the Office speaker]"

    with patch("agents.llm.stream", new=fake_stream), patch("agents.tools.execute", new=fake_execute):
        text, terminal = await agents.run_delegated(agents.AGENTS["home"], "play x", [])
    assert terminal is True and text.startswith("Now playing x")
    # run() keeps its old contract
    with patch("agents.llm.stream", new=fake_stream), patch("agents.tools.execute", new=fake_execute):
        assert await agents.run(agents.AGENTS["home"], "play x", []) == text
