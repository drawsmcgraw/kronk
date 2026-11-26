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


def _mirror_kev(rule, tools, events):
    return {"rid": "a", "route": "home", "rule": rule, "tools": tools, "dry_run": [], "events": events}


def test_mirror_row_passes_only_when_the_update_was_not_refused():
    row = _row(rule="mm_update", tool="update_magicmirror")
    for events, ok in (([], True), (["mm_update_refused"], False)):
        kev = _mirror_kev("mm_update", ["home:update_magicmirror"], events)
        checks = {c["check"]: c["ok"] for c in vr.compare_kronk(row, vr.DEVICES["office_sat1"], False, kev)}
        assert checks["tool"] and checks["mirror update not refused"] is ok


def test_any_other_row_reaching_the_mirror_tool_must_be_refused():
    row = _row(route="direct", rule="default", tool=None)
    for events, ok in (([], False), (["mm_update_refused"], True)):
        kev = {**_mirror_kev("default", ["coordinator:ask_home", "home:update_magicmirror"], events),
               "route": "direct"}
        checks = {c["check"]: c["ok"] for c in vr.compare_kronk(row, vr.DEVICES["office_sat1"], False, kev)}
        assert checks["mirror update refused"] is ok


def test_mirror_rows_are_never_replayed_on_production(gates):
    ha = RowHA(None)
    row = {**_row(rule="mm_update", tool="update_magicmirror"), "tags": ["mirror"]}
    rec = asyncio.run(vr.replay_row(ha, "tok", {"production": True}, row, TMAP, ARGS, dict(IDLE)))
    assert rec["status"] == "skip" and ha.runs == 0


def test_log_lines_drop_lines_before_the_row(monkeypatch):
    # docker logs --since is second-granular; the previous row's line at
    # .400 must not count for a row sent at .900 (the 2026-11-16 baseline bug).
    lines = ("2026-11-16T01:23:10.400000000Z INFO:tool_service:music dry run: play on ['a']: old\n"
             "2026-11-16T01:23:10.950000000Z INFO:tool_service:music dry run: pause on ['b']: new\n")
    monkeypatch.setattr(vr, "docker", lambda *a: lines)
    t = vr.dt.datetime(2026, 11, 16, 1, 23, 10, 900000, tzinfo=vr.dt.timezone.utc).timestamp()
    assert [l.split(": ")[-1] for l in vr._log_lines("c", t)] == ["new"]


def test_direct_row_passes_only_on_the_direct_path():
    """Sprint M7: a row with expect.direct passes only if the orchestrator
    logged its tool_call with direct=true."""
    row = _row(direct=True)
    dev = vr.DEVICES["office_sat1"]
    base = {"rid": "a", "route": "home", "rule": "playback", "tools": ["home:control_music"],
            "dry_run": []}
    on = {c["check"]: c["ok"] for c in vr.compare_kronk(row, dev, False, {**base, "direct": ["control_music"]})}
    off = {c["check"]: c["ok"] for c in vr.compare_kronk(row, dev, False, {**base, "direct": []})}
    old = {c["check"]: c["ok"] for c in vr.compare_kronk(row, dev, False, base)}
    assert on["direct"] is True and off["direct"] is False and old["direct"] is False
    plain = {c["check"] for c in vr.compare_kronk(_row(), dev, False, base)}
    assert "direct" not in plain


# ── --answers (sprint M11): the new endpoint against the row and live ───────

@pytest.mark.parametrize("url", ["http://127.0.0.1:8003", "http://192.168.1.10:8103", "http://localhost"])
def test_answers_refuses_anything_but_the_dev_tool_service(url):
    args = SimpleNamespace(tool_service_url=url)
    with pytest.raises(vr.Refused, match="dev tool_service only"):
        vr.answers(args)


def test_answers_refuses_without_dry_run(monkeypatch):
    monkeypatch.setattr(vr, "http_json", lambda u, *a, **k: {"status": "ok", "music_dry_run": False})
    with pytest.raises(vr.Refused, match="dry-run is not on"):
        vr.answers(SimpleNamespace(tool_service_url="http://127.0.0.1:8103"))


def test_fold_matches_the_resolver():
    from tool_service import music_resolve
    for s in ("Hôtel Costes", "The Beatles", "Kruder & Dorfmeister", "90s, 2000's and Today's Hits", "R.E.M."):
        assert vr.fold(s) == music_resolve.fold(s)


ROW = {"expect": {"kind": "named", "media_type": "album", "name": "Destiny Waiving", "artist": "Ulrich Schnauss",
                  "provider": None, "player": "origin"}}
DEV = {"player": "media_player.satellite1_d8d1bc"}


def test_compare_answer_reads_the_new_path_like_layer_one():
    new = {"ok": True, "kind": "named", "media_type": "album", "name": "DESTINY WAIVING",
           "artist": "Ulrich Schnauss and Mark Peters", "named_provider": None, "provider": "ytmusic",
           "targets": ["media_player.satellite1_d8d1bc"]}
    assert all(c["ok"] for c in vr.compare_answer(ROW, new, [], DEV))
    bad = {**new, "named_provider": "ytmusic", "targets": ["media_player.x"]}
    assert {c["check"] for c in vr.compare_answer(ROW, bad, [], DEV) if not c["ok"]} == {"provider", "player"}


def test_agree_with_live():
    a = {"ok": True, "kind": "named", "uri": "x://1", "targets": ["p"], "media_type": "album", "name": "A"}
    assert vr.agree_with_live(a, dict(a)) == (True, "same item")
    assert vr.agree_with_live(a, {**a, "uri": "y://2", "name": "B"}) == (False, "different item")
    assert vr.agree_with_live({"ok": False}, {"ok": False}) == (True, "both fail")
    assert vr.agree_with_live({**a, "kind": "genre", "name": "jazz"},
                              {**a, "kind": "genre", "name": "Jazz", "uri": "z"}) == (True, "same style")


# ── M36: --history, follow-ups posted with their history to the dev shims ────

HROW = {"id": "h1", "text": "play that album, in the kitchen", "origin": {"area": None},
        "history": [{"role": "user", "content": "play the album pulse by pink floyd"},
                    {"role": "assistant", "content": "The closest I found was Pulse by Pink Floyd."}],
        "expect": {"tier": "kronk", "route": "home", "rule": "music", "tool": "play_music", "name": "Pulse"},
        "tags": ["music", "needs-context"]}


@pytest.mark.parametrize("url", ["http://localhost", "http://localhost/", "http://localhost/api",
                                 "https://localhost/dev", "http://192.168.1.5/dev", "http://kronk.local/dev",
                                 "http://localhost/dev/../"])
def test_history_refuses_anything_but_the_dev_kronk_here(url):
    with pytest.raises(vr.Refused):
        vr.history_target(url)


def test_history_target_is_the_dev_pair():
    t = vr.history_target("http://localhost/dev/")
    assert t["status_url"] == "http://localhost/dev/api/status"
    assert t["orchestrator"] == "kronk-orchestrator-dev-1" and t["tool_service"] == "kronk-tool_service-dev-1"


@pytest.fixture
def hist(tmp_path, monkeypatch):
    """--history with every outside call faked: the dry-run answers come
    from `state['dry']` (one per status read), posts are recorded."""
    corpus = tmp_path / "rows.jsonl"
    corpus.write_text(json.dumps(HROW) + "\n" + json.dumps({**HROW, "id": "nohist", "history": None}) + "\n")
    state = {"dry": [], "posts": []}

    def http_json(url, token=None, timeout=10):
        assert url == "http://localhost/dev/api/status", url
        on = state["dry"].pop(0) if state["dry"] else True
        return {"tool_service": {"music_dry_run": on}}

    def post_json(url, body, timeout=60):
        state["posts"].append((url, body))
        return 200, {"message": {"role": "assistant", "content": "Playing Pulse in the kitchen."}}

    monkeypatch.setattr(vr, "http_json", http_json)
    monkeypatch.setattr(vr, "post_json", post_json)
    monkeypatch.setattr(vr, "require_text_logs", lambda target: None)
    monkeypatch.setattr(vr, "ha_token", lambda: "t")
    monkeypatch.setattr(vr, "player_snapshot", lambda token: {"media_player.x": {"state": "idle"}})
    monkeypatch.setattr(vr, "kronk_evidence", lambda *a, **k: {
        "rid": "r", "route": "home", "rule": "music", "tools": ["home:play_music"], "direct": [],
        "trace_id": "t", "events": [], "dry_run": [],
        "music_requests": ["query='Pulse by Pink Floyd' media_type='album' player='kitchen'"]})
    monkeypatch.setattr(vr, "tool_service_calls", lambda *a: ["POST /music 200"])
    args = SimpleNamespace(kronk_url="http://localhost/dev", corpus=str(corpus), id=None, tag=None,
                           repeat=2, label="t", out=str(tmp_path / "out.json"), timeout=5)
    return state, args, tmp_path


def test_history_refuses_without_dry_run_and_sends_nothing(hist):
    state, args, _ = hist
    state["dry"] = [False]
    with pytest.raises(vr.Refused):
        vr.history_check(args)
    assert state["posts"] == []


def test_history_posts_the_history_then_the_text_to_both_shims(hist):
    state, args, tmp = hist
    assert vr.history_check(args) == 0
    assert [u for u, _ in state["posts"]] == ["http://localhost/dev/api/chat",
                                              "http://localhost/dev/voice/api/chat"] * 2
    body = state["posts"][0][1]
    assert body["stream"] is False
    assert body["messages"] == HROW["history"] + [{"role": "user", "content": HROW["text"]}]
    out = json.loads((tmp / "out.json").read_text())
    assert out["summary"]["claims_without_tool"] == 0
    assert out["summary"]["per_row_route"]["h1 /api/chat"]["pass"] == 2


def test_history_dry_run_lost_mid_run_stops_before_the_next_post(hist):
    state, args, _ = hist
    state["dry"] = [True, True, False]     # start, first request, then off
    assert vr.history_check(args) == 3
    assert len(state["posts"]) == 1


def test_history_player_change_stops(hist, monkeypatch):
    state, args, _ = hist
    snaps = iter([{"p": 1}, {"p": 1}, {"p": 2}, {"p": 2}])
    monkeypatch.setattr(vr, "player_snapshot", lambda token: next(snaps))
    assert vr.history_check(args) == 3
    assert len(state["posts"]) == 1


def _kev(tools, music=()):
    return {"tools": list(tools), "music_requests": list(music), "dry_run": []}


def test_compare_history_tool_and_subject():
    ok = vr.compare_history(HROW, _kev(["home:play_music"], ["query='Pulse by Pink Floyd'"]), [], "x")
    assert all(c["ok"] for c in ok)
    wrong = vr.compare_history(HROW, _kev(["home:play_music"], ["query='Abbey Road'"]), [], "x")
    assert [c["check"] for c in wrong if not c["ok"]] == ["subject"]


def test_compare_history_subject_from_a_tool_route():
    row = {**HROW, "expect": {"tool": "get_weather", "mention": "Boston"}}
    checks = vr.compare_history(row, _kev(["home:get_weather"]), ["GET /weather?location=Boston, MA 200"], "x")
    assert all(c["ok"] for c in checks)


def test_compare_history_counts_a_claim_with_no_tool_call():
    checks = vr.compare_history(HROW, _kev([]), [], "I've started playing the album Pulse in the kitchen.")
    claim = next(c for c in checks if c["check"] == "no claim without a tool call")
    assert claim["ok"] is False
    # The same words after a real tool call are not a false claim.
    checks = vr.compare_history(HROW, _kev(["home:play_music"], ["Pulse"]), [], "Now playing Pulse.")
    assert all(c["ok"] for c in checks)
    # Asking which album is a failure of the row, not a claim.
    checks = vr.compare_history(HROW, _kev([]), [], "I need to know the name of the album.")
    assert next(c for c in checks if c["check"] == "no claim without a tool call")["ok"] is True


def test_compare_history_not_tools():
    row = {**HROW, "expect": {"tool": None, "not_tools": ["play_music"]}}
    bad = vr.compare_history(row, _kev(["home:play_music"]), [], "x")
    assert not next(c for c in bad if c["check"] == "not play_music")["ok"]


def test_tool_service_calls_reads_the_access_log(monkeypatch):
    monkeypatch.setattr(vr, "_log_lines", lambda c, t: [
        'INFO:     172.18.0.6:1 - "GET /weather/cached HTTP/1.1" 200 OK',
        'INFO:     172.18.0.6:1 - "GET /health HTTP/1.1" 200 OK',
        'INFO:     172.18.0.6:1 - "GET /weather?location=Boston%2C+MA HTTP/1.1" 200 OK',
        'INFO:     172.18.0.6:1 - "POST /music HTTP/1.1" 502 Bad Gateway'])
    assert vr.tool_service_calls("c", 0) == ["GET /weather?location=Boston, MA 200", "POST /music 502"]
