"""The spoken-reply line on the speech mount (sprint M8, criteria 14 and 15).

On a request that arrived on /voice/api/chat (origin.voice), the
coordinator's system text ends with the line in config/voice.yaml
(`spoken_reply`): the answer will be read aloud, so one to three plain
sentences. It is the last block, so the text before it is byte-identical to
a typed request's (and llama.cpp's cached prefix is unchanged). Routed and
delegated specialists never get it.

The pipeline tests drive the real FastAPI app with a scripted model that
records every system message; nothing reaches the network.
"""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("LLM_SERVICE_URL", "http://fake-llm:8002")
os.environ.setdefault("TOOL_SERVICE_URL", "http://fake-tools:8003")

import agents  # noqa: E402
import config_loader  # noqa: E402
from tests.test_direct_dispatch import (  # noqa: E402,F401  (client is a fixture)
    MOUNTS, OK, STAMP, VOICE, FakeToolService, client, send,
)

pytestmark = pytest.mark.usefixtures("no_network")

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "config"
LINE = config_loader.SPOKEN_REPLY_DEFAULT
FACTS = "[Kronk ambient facts — pinned for the test]"


class Recorder:
    """agents.llm.stream stand-in. Records (agent, system text) per call;
    the coordinator is the caller offered ask_* tools. `script` maps an
    agent name to the list of per-round actions it takes."""

    def __init__(self, script=None):
        self.calls = []
        self.script = {k: list(v) for k, v in (script or {}).items()}

    def __call__(self, messages, model, tools_arg=None):
        names = {t["function"]["name"] for t in (tools_arg or [])}
        if any(n.startswith("ask_") for n in names):
            who = "coordinator"
        elif "play_music" in names or "get_weather" in names:
            who = "home"
        else:
            who = "other"
        self.calls.append((who, messages[0]["content"]))
        action = (self.script.get(who) or ["answer"]).pop(0) if self.script.get(who) else "answer"

        async def gen():
            if action == "ask_home":
                yield {"tool_calls": [{"id": "c1", "function": {
                    "name": "ask_home", "arguments": {"task": "what's the weather"}}}]}
            elif action == "error":
                raise RuntimeError("boom")
            else:
                yield {"token": f"{who} answer."}
            yield {"usage": {}}
        return gen()

    def systems(self, who):
        return [s for w, s in self.calls if w == who]


def _run(c, mount, text, script=None):
    rec = Recorder(script)
    with patch("agents.llm.stream", new=rec), \
         patch("agents.kronk_facts", new=lambda: FACTS), \
         patch("tools.httpx.AsyncClient", new=FakeToolService(OK)):
        reply = send(c, mount, text, STAMP if mount in VOICE else None)
    return reply, rec


# ── Criterion 14: last, voice only, nothing before it changes ────────────────

QUESTION = "explain how a heat pump works"     # routing rule default: the coordinator


def test_voice_coordinator_text_is_the_typed_text_plus_the_line(client):
    _, typed = _run(client, "api_chat", QUESTION)
    _, spoken = _run(client, "voice_api_chat", QUESTION)
    [t], [s] = typed.systems("coordinator"), spoken.systems("coordinator")
    assert LINE not in t
    assert s == t + "\n\n" + LINE
    assert s.endswith(LINE)


@pytest.mark.parametrize("mount", [m for m in MOUNTS if m not in VOICE])
def test_line_absent_on_every_other_mount(client, mount):
    _, rec = _run(client, mount, QUESTION)
    [s] = rec.systems("coordinator")
    assert LINE not in s
    assert "[Spoken reply]" not in s


def test_line_stays_last_after_a_specialist_failure_block(client):
    """A routed specialist that fails falls through to the coordinator with
    a failure block appended to system_extra; the spoken line still comes
    after it, and the text before the line equals the typed request's."""
    script = {"home": ["error"]}
    # The failure block is worded by error style (friendly on voice by
    # default), so both requests are rendered in the same style here.
    with patch.dict(os.environ, {"ERROR_STYLE": "friendly", "ERROR_STYLE_VOICE": "friendly"}):
        _, typed = _run(client, "api_chat", "play some jazz", script)
        _, spoken = _run(client, "voice_api_chat", "play some jazz", script)
    [t], [s] = typed.systems("coordinator"), spoken.systems("coordinator")
    assert "home" in t and LINE not in t
    assert s == t + "\n\n" + LINE


# ── Criterion 15: the coordinator only ───────────────────────────────────────

def test_routed_specialist_does_not_get_the_line(client):
    _, rec = _run(client, "voice_api_chat", "play some jazz")    # rule music -> home
    assert rec.systems("home"), rec.calls
    assert all(LINE not in s for s in rec.systems("home"))
    assert not rec.systems("coordinator")


def test_delegated_specialist_does_not_get_the_line(client):
    _, rec = _run(client, "voice_api_chat", QUESTION,
                  script={"coordinator": ["ask_home", "answer"]})
    coord, home = rec.systems("coordinator"), rec.systems("home")
    assert len(coord) == 2 and all(s.endswith(LINE) for s in coord)
    assert home and all(LINE not in s for s in home)


def test_the_line_does_not_change_the_reply_path(client):
    """Same model output, same spoken reply, with and without the line: the
    line is model input only."""
    reply_on, _ = _run(client, "voice_api_chat", QUESTION)
    with patch.object(config_loader.current().voice, "spoken_reply", None):
        reply_off, rec = _run(client, "voice_api_chat", QUESTION)
    assert reply_on == reply_off == "coordinator answer."
    [s] = rec.systems("coordinator")
    assert LINE not in s


# ── config/voice.yaml (M21 rules) ────────────────────────────────────────────

def test_builtin_default_equals_the_shipped_file():
    cfg = config_loader.load(CONFIG)
    assert cfg.voice == config_loader.Voice()
    assert cfg.voice.spoken_reply == LINE
    assert "voice.yaml" in cfg.digests


def test_running_config_has_the_line():
    assert config_loader.current().voice.spoken_reply == LINE


def _with_voice(tmp_path, text):
    import shutil
    base = tmp_path / "config"
    shutil.copytree(CONFIG, base)
    (base / "voice.yaml").write_text(text)
    return base


def test_null_leaves_the_line_out(tmp_path):
    cfg = config_loader.load(_with_voice(tmp_path, "spoken_reply: null\n"))
    assert cfg.voice.spoken_reply is None


@pytest.mark.parametrize("text, needle", [
    ("spoken_reply: |\n  two\n  lines\n", "spoken_reply"),
    ("spoken_reply: '" + "x" * 401 + "'\n", "spoken_reply"),
    ("spoken_reply: '   '\n", "spoken_reply"),
    ("spoken_reply: 3\n", "spoken_reply"),
    ("spoken_replyy: hi\n", "unknown key"),
])
def test_bad_values_are_rejected_with_their_line(tmp_path, text, needle):
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.load(_with_voice(tmp_path, text))
    probs = [p for p in e.value.problems if p.file == "voice.yaml"]
    assert probs and needle in probs[0].message and probs[0].line == 1


def test_empty_file_is_a_warning_and_defaults(tmp_path):
    cfg = config_loader.load(_with_voice(tmp_path, "# nothing\n"))
    assert cfg.voice.spoken_reply == LINE
    assert any(w.file == "voice.yaml" for w in cfg.warnings)


def test_reload_reports_the_change(tmp_path, monkeypatch):
    base = _with_voice(tmp_path, "spoken_reply: Keep it short.\n")
    old = config_loader.current()
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(base))
    try:
        out = config_loader.reload()
        assert out["ok"], out
        assert any(c.startswith("voice.yaml:spoken_reply:") for c in out["changed"]), out
        assert config_loader.current().voice.spoken_reply == "Keep it short."
    finally:
        config_loader._set(old)
