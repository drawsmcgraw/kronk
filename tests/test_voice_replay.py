"""Unit tests for scripts/voice_replay.py's safety gates and comparisons (M10).

The live replay is only ever run against the dev path with dry-run on, so the
refusal paths (dry-run off, production, HA-local rows) are pinned here
instead of being provoked on the box.
"""
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "voice_replay", Path(__file__).resolve().parent.parent / "scripts" / "voice_replay.py")
vr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vr)

TRIGGER_TPL = "(play|put on|listen to) {media_name}"
TMAP = {vr.norm(TRIGGER_TPL): "catchall"}


# ── classify_debug: what HA's local pass would do ───────────────────────────

def test_no_match_reaches_kronk():
    assert vr.classify_debug(None, TMAP)["local"] is False


def test_partial_match_reaches_kronk():
    r = {"match": False, "intent": {"name": "HassGetState"}}
    assert vr.classify_debug(r, TMAP)["local"] is False


def test_trigger_is_local_and_named():
    d = vr.classify_debug({"match": True, "source": "trigger", "sentence_template": TRIGGER_TPL}, TMAP)
    assert d["local"] is True and d["source"] == "trigger" and d["trigger"] == "catchall"


def test_strict_builtin_is_local():
    d = vr.classify_debug({"match": True, "source": "builtin", "fuzzy_match": False,
                           "intent": {"name": "HassMediaPause"}}, TMAP)
    assert d["local"] is True and d["intent"] == "HassMediaPause"


def test_fuzzy_builtin_is_not_local_but_flagged():
    d = vr.classify_debug({"match": True, "source": "builtin", "fuzzy_match": True,
                           "intent": {"name": "HassMediaNext"}}, TMAP)
    assert d["local"] is False and d["fuzzy"] is True


# ── the dry-run gate ─────────────────────────────────────────────────────────

TARGET = {"pipeline": "kronk-dev", "status_url": "http://localhost/dev/api/status"}


@pytest.mark.parametrize("status", [
    {"tool_service": {"status": "ok", "music_dry_run": False}},
    {"tool_service": {"status": "ok"}},                 # pre-M17 tool_service: no key
    {"tool_service": {"music_dry_run": "true"}},        # only the boolean counts
    {},
])
def test_dry_run_gate_refuses(monkeypatch, status):
    monkeypatch.setattr(vr, "http_json", lambda url, token=None, timeout=10: status)
    with pytest.raises(vr.Refused):
        vr.require_dry_run(TARGET)


def test_dry_run_gate_refuses_unreachable(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(vr, "http_json", boom)
    with pytest.raises(vr.Refused):
        vr.require_dry_run(TARGET)


def test_dry_run_gate_passes(monkeypatch):
    monkeypatch.setattr(vr, "http_json",
                        lambda url, token=None, timeout=10: {"tool_service": {"music_dry_run": True}})
    assert vr.require_dry_run(TARGET)["music_dry_run"] is True


# ── the production guard ─────────────────────────────────────────────────────

PIPELINES = {"preferred_pipeline": "P1", "pipelines": [
    {"name": "kronk", "id": "P1", "conversation_engine": "conversation.ollama_conversation"},
    {"name": "kronk-dev", "id": "P2", "conversation_engine": "conversation.kronk_dev"},
    {"name": "Home Assistant", "id": "P3", "conversation_engine": "conversation.home_assistant"},
    {"name": "other", "id": "P4", "conversation_engine": "conversation.other"},
]}
ENTRIES = {"data": {"entries": [
    {"entry_id": "E1", "domain": "ollama", "data": {"url": "http://localhost/voice"}},
    {"entry_id": "E2", "domain": "ollama", "data": {"url": "http://localhost/dev/voice"}},
    {"entry_id": "E4", "domain": "openai_conversation", "data": {}},
]}}
ENTITY_ENTRY = {"conversation.ollama_conversation": "E1", "conversation.kronk_dev": "E2",
                "conversation.other": "E4"}


class FakeHA:
    def __init__(self):
        self.calls = []

    async def call(self, type_, **kw):
        self.calls.append(type_)
        if type_ == "assist_pipeline/pipeline/list":
            return PIPELINES
        if type_ == "config/entity_registry/get":
            return {"config_entry_id": ENTITY_ENTRY[kw["entity_id"]]}
        raise AssertionError(type_)


def _args(**kw):
    base = dict(pipeline="kronk-dev", allow_production=False,
                orchestrator_container=None, tool_service_container=None)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def storage(monkeypatch):
    monkeypatch.setattr(vr, "docker", lambda *a: json.dumps(ENTRIES))


def test_dev_pipeline_resolves_to_dev_containers(storage):
    t = asyncio.run(vr.resolve_target(FakeHA(), "tok", _args()))
    assert t["production"] is False
    assert t["status_url"] == "http://localhost/dev/api/status"
    assert t["orchestrator"] == "kronk-orchestrator-dev-1"
    assert t["tool_service"] == "kronk-tool_service-dev-1"


def test_production_refused_without_override(storage):
    with pytest.raises(vr.Refused, match="production"):
        asyncio.run(vr.resolve_target(FakeHA(), "tok", _args(pipeline="kronk")))


def test_production_by_id_refused(storage):
    with pytest.raises(vr.Refused, match="production"):
        asyncio.run(vr.resolve_target(FakeHA(), "tok", _args(pipeline="P1")))


def test_production_override_still_goes_through_the_gate(storage):
    t = asyncio.run(vr.resolve_target(FakeHA(), "tok", _args(pipeline="kronk", allow_production=True)))
    assert t["production"] is True and t["status_url"] == "http://localhost/api/status"


def test_ha_engine_refused(storage):
    with pytest.raises(vr.Refused, match="locally"):
        asyncio.run(vr.resolve_target(FakeHA(), "tok", _args(pipeline="Home Assistant")))


def test_non_kronk_engine_refused(storage):
    with pytest.raises(vr.Refused, match="Ollama"):
        asyncio.run(vr.resolve_target(FakeHA(), "tok", _args(pipeline="other")))


# ── replay_row never executes an HA-local row ────────────────────────────────

class RowHA:
    """Debug answers with `debug`; any pipeline run is a test failure unless allowed."""
    def __init__(self, debug):
        self.debug = debug
        self.runs = 0

    async def call(self, type_, **kw):
        if type_ == "conversation/agent/homeassistant/debug":
            return {"results": [self.debug]}
        if type_ == "assist_pipeline/pipeline_debug/list":
            return {"pipeline_runs": []}
        raise AssertionError(type_)

    async def run_intent(self, *a, **k):
        self.runs += 1
        return [{"type": "run-start", "data": {"conversation_id": "C"}, "timestamp": "2026"},
                {"type": "intent-end", "data": {"processed_locally": False, "intent_output": {}}},
                {"type": "run-end"}]


def _row(tier="kronk", **expect):
    e = {"tier": tier, "route": "home", "rule": "playback", "tool": "control_music", "action": "pause",
         "player": "origin", **expect}
    if tier == "ha_local":
        e.setdefault("ha", {"source": "builtin", "intent": "HassMediaPause"})
    return {"id": "r1", "text": "pause", "origin": {"area": "Office", "device": "office_sat1"},
            "expect": e, "tags": ["playback"], "source": ["x"]}


ARGS = SimpleNamespace(device="office_sat1", check_only=False, timeout=5)
IDLE = {"media_player.satellite1_d8d1bc": {"state": "idle", "media_title": None,
                                           "media_artist": None, "media_content_id": None}}


@pytest.fixture
def gates(monkeypatch):
    monkeypatch.setattr(vr, "require_dry_run", lambda target: {"music_dry_run": True})
    monkeypatch.setattr(vr, "player_snapshot", lambda token: dict(IDLE))
    monkeypatch.setattr(vr, "kronk_evidence", lambda *a, **k: {
        "rid": "abc", "route": "home", "rule": "playback", "tools": ["home:control_music"],
        "trace_id": "T", "events": [], "dry_run": [], "music_requests": []})


@pytest.mark.parametrize("debug", [
    {"match": True, "source": "trigger", "sentence_template": TRIGGER_TPL},
    {"match": True, "source": "builtin", "fuzzy_match": False, "intent": {"name": "HassMediaPause"}},
])
def test_local_rows_are_checked_not_executed(gates, debug):
    ha = RowHA(debug)
    rec = asyncio.run(vr.replay_row(ha, "tok", {}, _row("ha_local"), TMAP, ARGS, dict(IDLE)))
    assert ha.runs == 0 and rec["method"] == "checked"


def test_kronk_row_is_executed(gates):
    ha = RowHA(None)
    rec = asyncio.run(vr.replay_row(ha, "tok", {"pipeline_id": "P2", "orchestrator": "o", "tool_service": "t"},
                                    _row(), TMAP, ARGS, dict(IDLE)))
    assert ha.runs == 1 and rec["status"] == "pass" and rec["trace_id"] == "T"


def test_fuzzy_row_skipped_while_a_player_plays(gates, monkeypatch):
    playing = {"media_player.satellite1_d8d1bc": {**IDLE["media_player.satellite1_d8d1bc"], "state": "playing"}}
    monkeypatch.setattr(vr, "player_snapshot", lambda token: dict(playing))
    ha = RowHA({"match": True, "source": "builtin", "fuzzy_match": True, "intent": {"name": "HassMediaNext"}})
    rec = asyncio.run(vr.replay_row(ha, "tok", {}, _row(), TMAP, ARGS, dict(playing)))
    assert ha.runs == 0 and rec["status"] == "skip"


def test_dry_run_lost_mid_run_stops_before_executing(gates, monkeypatch):
    def off(target):
        raise vr.Refused("dry-run off")
    monkeypatch.setattr(vr, "require_dry_run", off)
    ha = RowHA(None)
    with pytest.raises(vr.Stopped):
        asyncio.run(vr.replay_row(ha, "tok", {}, _row(), TMAP, ARGS, dict(IDLE)))
    assert ha.runs == 0


def test_player_change_before_a_row_stops(gates, monkeypatch):
    moved = {"media_player.satellite1_d8d1bc": {**IDLE["media_player.satellite1_d8d1bc"], "state": "playing"}}
    monkeypatch.setattr(vr, "player_snapshot", lambda token: dict(moved))
    ha = RowHA(None)
    with pytest.raises(vr.Stopped):
        asyncio.run(vr.replay_row(ha, "tok", {}, _row(), TMAP, ARGS, dict(IDLE)))
    assert ha.runs == 0


def test_skip_tags_never_reach_ha(gates):
    ha = RowHA(None)
    row = {**_row(), "tags": ["needs-context"]}
    rec = asyncio.run(vr.replay_row(ha, "tok", {}, row, TMAP, ARGS, dict(IDLE)))
    assert rec["status"] == "skip" and ha.runs == 0


# ── comparisons and statuses ─────────────────────────────────────────────────

def test_status_xfail_and_xpass():
    row = {"xfail": {"live": "known"}}
    assert vr.status_of(row, [{"ok": False}])[0] == "xfail"
    assert vr.status_of(row, [{"ok": True}])[0] == "xpass"
    assert vr.status_of({}, [{"ok": False}])[0] == "fail"
    assert vr.status_of({"xfail": {"routing": "known"}}, [{"ok": False}])[0] == "xfail"
    assert vr.status_of({"xfail": {"parser": "known"}}, [{"ok": False}])[0] == "fail"


def test_compare_kronk_checks_player_and_action_from_dry_run():
    dev = vr.DEVICES["office_sat1"]
    kev = {"rid": "a", "route": "home", "rule": "playback", "tools": ["home:control_music"],
           "dry_run": [{"action": "pause", "targets": ["media_player.home_assistant_voice_0ab117"]}]}
    checks = {c["check"]: c["ok"] for c in vr.compare_kronk(_row(), dev, False, kev)}
    assert checks == {"tier": True, "rule": True, "tool": True, "action": True, "player": False}


def test_compare_kronk_local_answer_is_a_tier_failure():
    checks = vr.compare_kronk(_row(), vr.DEVICES["office_sat1"], True,
                              {"rid": None, "route": None, "rule": None, "tools": [], "dry_run": []})
    assert not checks[0]["ok"]


def test_snapshot_diff():
    a = {"m.a": {"state": "idle"}, "m.b": {"state": "idle"}}
    b = {"m.a": {"state": "idle"}, "m.b": {"state": "playing"}}
    assert [d["entity_id"] for d in vr.snapshot_diff(a, b)] == ["m.b"]
    assert vr.snapshot_diff(a, dict(a)) == []


def test_null_tool_means_no_music_tool_may_fire():
    row = _row(tool=None, action="shuffle")
    kev = {"rid": "a", "route": "home", "rule": "playback", "tools": ["home:control_music"], "dry_run": []}
    checks = {c["check"]: c["ok"] for c in vr.compare_kronk(row, vr.DEVICES["office_sat1"], False, kev)}
    assert checks["no music tool"] is False


def test_log_lines_drop_lines_before_the_row(monkeypatch):
    # docker logs --since is second-granular; the previous row's line at
    # .400 must not count for a row sent at .900 (the 2026-11-16 baseline bug).
    lines = ("2026-11-16T01:23:10.400000000Z INFO:tool_service:music dry run: play on ['a']: old\n"
             "2026-11-16T01:23:10.950000000Z INFO:tool_service:music dry run: pause on ['b']: new\n")
    monkeypatch.setattr(vr, "docker", lambda *a: lines)
    t = vr.dt.datetime(2026, 11, 16, 1, 23, 10, 900000, tzinfo=vr.dt.timezone.utc).timestamp()
    assert [l.split(": ")[-1] for l in vr._log_lines("c", t)] == ["new"]
