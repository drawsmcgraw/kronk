"""Shadow mode for the M11 decision (tool_service/music_shadow.py), criteria
14 to 17 and the shadow half of 22: the live answer is the same with shadow
on, the comparison is logged, nothing the shadow does can play."""
import ast
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import tool_service.main as ts
from tests import music_snapshot as snapmod
from tool_service import music_shadow

pytestmark = pytest.mark.usefixtures("no_network")

ROOT = Path(__file__).resolve().parent.parent
SNAP = snapmod.load()
PIN = json.loads((ROOT / "tests" / "fixtures" / "music_live_pin.json").read_text())
ROWS = [r for r in snapmod.corpus_rows() if snapmod.live_args(r, SNAP) is not None]


def test_shadow_is_off_by_default_in_code_and_in_production_compose():
    assert music_shadow.ENABLED is False                 # the test env sets no MUSIC_SHADOW
    prod = (ROOT / "docker-compose.yml").read_text().split("\n  tool_service:")[1].split("\n  litellm:")[0]
    lines = [line.strip() for line in prod.splitlines() if "MUSIC_SHADOW" in line]
    assert lines and all(line.startswith("#") for line in lines), lines


def test_dev_turns_it_on_and_logs_under_data_dev():
    dev = (ROOT / "docker-compose.yml").read_text().split("  tool_service-dev:")[1].split("\n  orchestrator-dev:")[0]
    assert "- MUSIC_SHADOW=1" in dev and "- ./data/dev:/data" in dev


@pytest.fixture
def shadow_on(tmp_path):
    log = tmp_path / "shadow.jsonl"
    writes = []

    async def no_service_call(*a, **kw):
        writes.append(a)
        raise snapmod.WriteRefused(str(a))

    class NeverAsk:
        async def __call__(self, *a):
            raise AssertionError("the shadow asked the model")
    with snapmod.offline_env(SNAP, tmp_path / "env") as replay, \
         patch.object(music_shadow, "ENABLED", True), patch.object(music_shadow, "LOG_PATH", log), \
         patch.object(ts, "ha_call_service", new=no_service_call), \
         patch.object(ts, "ask_style_or_named", new=NeverAsk()):
        replay.writes, replay.log = writes, log
        yield replay


def _post(body):
    async def go():
        try:
            out = await ts.play_music(ts.MusicRequest(**body))
            code = 200
        except HTTPException as e:
            out, code = {"detail": e.detail}, e.status_code
        await music_shadow.drain()
        return code, out
    return asyncio.run(go())


def _records(log):
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


# ── criterion 15 and 22: the live answer does not change ────────────────────

@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_live_answer_is_the_pinned_one_with_shadow_on(row, shadow_on):
    body = snapmod.live_args(row, SNAP)
    code, answer = _post(body)
    assert (code, answer) == (PIN[row["id"]]["status"], PIN[row["id"]]["answer"])
    recs = _records(shadow_on.log)
    assert len(recs) == 1 and recs[0]["sentence"] == row["text"]
    assert "agree" in recs[0] and not (recs[0].get("new") or {}).get("error"), recs[0]
    assert shadow_on.writes == []                         # no HA service call, live or shadow
    assert not shadow_on.missing, shadow_on.missing       # every read was a recorded read


def test_the_record_carries_both_outcomes(shadow_on):
    _post({"query": "Portishead", "media_type": "artist", "said": "play Portishead",
           "origin_device": snapmod.DEVICE_IDS["kitchen_pe"], "origin_area": "Kitchen"})
    r = _records(shadow_on.log)[0]
    assert r["v"] == 1 and r["sentence"] == "play Portishead"
    assert r["origin"] == {"area": "Kitchen", "device": snapmod.DEVICE_IDS["kitchen_pe"]}
    assert r["live"]["uri"] == r["new"]["uri"] == "library://artist/1251"
    assert r["live"]["targets"] == r["new"]["targets"] == ["media_player.home_assistant_voice_0ac919_pe_01"]
    assert (r["agree"], r["why"], r["new"]["decided_by"]) == (True, "same item", "named")
    assert isinstance(r["live"]["ms"], int) and isinstance(r["new"]["ms"], int)
    assert r["new"]["candidates"] and r["new"]["parsed"]["name"] == "Portishead"


def test_a_shadow_failure_never_surfaces(shadow_on):
    async def boom(*a, **kw):
        raise RuntimeError("decision exploded")
    with patch.object(ts, "decide_sentence", new=boom):
        code, answer = _post({"query": "Portishead", "media_type": "artist", "said": "play Portishead"})
    assert code == 200 and answer["status"] == "dry_run"
    r = _records(shadow_on.log)[0]
    assert r["new"]["error"] == "RuntimeError: decision exploded" and "agree" not in r


def test_a_slow_shadow_times_out_and_is_logged(shadow_on):
    async def slow(*a, **kw):
        await asyncio.sleep(5)
    with patch.object(ts, "decide_sentence", new=slow), patch.object(music_shadow, "TIMEOUT_S", 0.05):
        code, _ = _post({"query": "Portishead", "media_type": "artist", "said": "play Portishead"})
    assert code == 200 and _records(shadow_on.log)[0]["new"]["error"] == "timed out after 0.05s"


def test_the_shadow_starts_after_the_live_answer(shadow_on):
    order = []
    real_live, real_decide = ts._play_music_live, ts.decide_sentence

    async def live(req):
        out = await real_live(req)
        order.append("live answered")
        return out

    async def decide(*a, **kw):
        order.append("shadow started")
        return await real_decide(*a, **kw)
    with patch.object(ts, "_play_music_live", new=live), patch.object(ts, "decide_sentence", new=decide):
        _post({"query": "Portishead", "media_type": "artist", "said": "play Portishead"})
    assert order == ["live answered", "shadow started"]


def test_a_live_failure_is_shadowed_too(shadow_on):
    code, _ = _post({"query": "pink floyd", "media_type": "artist", "player": "blue group",
                     "said": "play pink floyd on the blue group"})
    r = _records(shadow_on.log)[0]
    assert code == 400 and r["live"]["status"] == "error" and r["new"]["target_error"].startswith("I don't know")
    assert (r["agree"], r["why"]) == (True, "both fail")


@pytest.mark.parametrize("body,why", [
    ({"query": "Portishead"}, "no sentence"),
    ({"query": "another", "kind": "another", "said": "not that"}, "'not that'"),
])
def test_requests_with_nothing_to_decide_are_recorded_as_skipped(shadow_on, body, why):
    _post(body)
    r = _records(shadow_on.log)[0]
    assert r["skipped"].startswith(why) and "new" not in r


def test_shadow_off_writes_nothing(tmp_path):
    log = tmp_path / "shadow.jsonl"
    with snapmod.offline_env(SNAP, tmp_path / "env"), patch.object(music_shadow, "LOG_PATH", log):
        _post({"query": "Portishead", "media_type": "artist", "said": "play Portishead"})
    assert not log.exists()


# ── criterion 14: playing is impossible by construction ─────────────────────

FORBIDDEN = ("ha_call_service", "play_media", "ma_command", "shuffle_set", "favorites", "media_pause",
             "media_next_track", "volume_", "player_queues", "httpx", "websockets", "record_pick",
             "set_pick_outcome", "record_feedback")


@pytest.mark.parametrize("module", ["music_shadow.py", "music_decide.py"])
def test_shadow_and_decision_modules_cannot_reach_a_write(module):
    src = (ROOT / "tool_service" / module).read_text()
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {a.name for a in node.names} | ({node.module} if node.module else set())
    assert imported <= {"__future__", "annotations", "asyncio", "json", "logging", "os", "re", "time",
                        "pathlib", "Path", "dataclasses", "dataclass", "field", "config_loader",
                        "music_resolve", "telemetry"}, imported
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    body = ast.get_docstring(tree) or ""
    code = code.replace(body, "")
    assert not [w for w in FORBIDDEN if w in code], [w for w in FORBIDDEN if w in code]


def test_the_shadow_is_handed_only_decide_sentence_without_the_model():
    src = (ROOT / "tool_service" / "main.py").read_text()
    calls = [line.strip() for line in src.splitlines() if "music_shadow.schedule(" in line]
    assert calls and all(line.endswith("_shadow_decide)") or "_shadow_decide" in line or line.endswith(",")
                         for line in calls)
    body = src.split("async def _shadow_decide(")[1].split("\nasync def ")[0]
    assert "decide_sentence(sentence, origin_device, origin_area, ask_model=False)" in body


# ── compare() ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("live,new,want", [
    ({"status": "dry_run", "kind": "named", "uri": "x://a/1", "targets": ["p"]},
     {"action": "play", "kind": "named", "uri": "x://a/1", "targets": ["p"]}, (True, "same item")),
    ({"status": "playing", "kind": "named", "media_type": "album", "name": "Blue Lines", "targets": ["p"]},
     {"action": "play", "kind": "named", "media_type": "album", "name": "blue lines", "uri": "y://a/2",
      "targets": ["p"]}, (True, "same type and name")),
    ({"status": "dry_run", "kind": "genre", "name": "jazz", "targets": ["p"]},
     {"action": "play", "kind": "genre", "name": "Jazz", "targets": ["p"]}, (True, "same style")),
    ({"status": "dry_run", "kind": "named", "uri": "x://t/1", "targets": ["p"]},
     {"action": "play", "kind": "genre", "name": "jazz", "targets": ["p"]}, (False, "live read named, new read genre")),
    ({"status": "dry_run", "kind": "named", "uri": "x://a/1", "targets": ["p"]},
     {"action": "play", "kind": "named", "uri": "x://a/1", "targets": ["q"]}, (False, "different speaker")),
    ({"status": "error"}, {"action": "not_found"}, (True, "both fail")),
    ({"status": "dry_run", "kind": "named", "uri": "x"}, {"action": "not_found"}, (False, "only the live path plays")),
    ({"status": "error"}, {"action": "play", "kind": "named", "uri": "x://a"}, (False, "only the new path plays")),
])
def test_compare(live, new, want):
    assert music_shadow.compare(live, new) == want


# ── the review script ────────────────────────────────────────────────────────

def test_report_summarises_a_log(tmp_path, shadow_on):
    for body in ({"query": "Portishead", "media_type": "artist", "said": "play Portishead"},
                 {"query": "jazz", "kind": "genre", "said": "put on some jazz"},
                 {"query": "Glory Box", "media_type": "album", "said": "play Portishead"},
                 {"query": "Portishead"}):
        _post(body)
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "music_shadow_report.py"), str(shadow_on.log),
                          "--json"], capture_output=True, text=True, check=True)
    s = json.loads(out.stdout)
    assert (s["shadowed"], s["compared"], s["skipped"]) == (4, 3, 1)
    assert s["agree"] >= 2 and s["decided_by"] and "model_would_ask" in s
    assert all({"sentence", "live", "new", "why"} <= set(d) for d in s["disagreements"])
    text = subprocess.run([sys.executable, str(ROOT / "scripts" / "music_shadow_report.py"), str(shadow_on.log)],
                          capture_output=True, text=True, check=True).stdout
    assert "agreement:" in text and "decided_by:" in text and "model question would have fired:" in text
