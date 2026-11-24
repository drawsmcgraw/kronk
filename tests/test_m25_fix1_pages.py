"""The state-to-label decisions of /resources' memory bar and /services'
dots (M25 fix round 1: S2, S3, N9).

The suite has no JavaScript runtime, and this round adds none (N10 stays
open: nothing here runs the pages' script). What is pinned instead:

- The decisions are JSON literals in each page's script (MEM_BAR_LABELS,
  HEALTH_DOT, SERVER_TAG). These tests read them as JSON and assert what
  each state says.
- The script selects them by the payload's own fields, checked as text.
- The payload carries every field those labels are built from, in each
  state, with the reason naming the file and its age.
No socket is opened.
"""
import json
import re
from pathlib import Path

import pytest

import servers
from tests.test_servers_gpu_mem import NOW, _build, _by_name, _gpu, _props

pytestmark = pytest.mark.usefixtures("no_network")

STATIC = Path(__file__).resolve().parent.parent / "orchestrator" / "static"
RESOURCES = (STATIC / "resources.html").read_text()
SERVICES = (STATIC / "services.html").read_text()


def _literal(html: str, name: str):
    """The JSON object assigned to `const <name> =` in the page's script."""
    m = re.search(rf"const {name} = \{{", html)
    assert m, f"{name} is not a literal in the page"
    i, depth = m.end() - 1, 0
    for j in range(i, len(html)):
        depth += {"{": 1, "}": -1}.get(html[j], 0)
        if depth == 0:
            return json.loads(html[i:j + 1])
    raise AssertionError(f"{name}: unbalanced braces")


def _function(html: str, name: str) -> str:
    m = re.search(rf"function {name}\(.*?\n(  )?\}}\n", html, re.S)
    assert m, f"no function {name}"
    return m.group(0)


# ── /resources: the memory bar (S2) ───────────────────────────────────────

LABELS = _literal(RESOURCES, "MEM_BAR_LABELS")


def test_memory_bar_has_a_label_set_for_fresh_and_for_withheld():
    assert set(LABELS) == {"fresh", "withheld"}
    for v in LABELS.values():
        assert set(v) == {"proc", "other", "note"}


def test_withheld_memory_file_shows_no_process_and_says_the_segment_is_all_used_ram():
    w = LABELS["withheld"]
    assert w["proc"] is None
    assert w["other"] == "Used RAM, all of it (per-process GPU memory not known)"
    assert "System / other" not in json.dumps(LABELS)
    # Fix round 2: the claim is about GTT, which is system RAM; VRAM is a
    # 512 MiB firmware carve-out outside MemTotal.
    assert w["note"] == ("Per-process GPU memory is not known (gpu_mem.json, below), so it is "
                         "not split out. GTT is system RAM: the GTT in use (below) is inside used RAM.")


FRESH_NOTE = ("Per-process segments are GPU memory (GTT + VRAM). Other used RAM is used RAM "
              "minus them. A card's RAM figure includes memory-mapped model files, which the "
              "kernel counts as available, so the cards' RAM need not fit inside Other used RAM.")


def test_fresh_legend_says_gpu_memory_and_what_the_other_segment_is():
    f = LABELS["fresh"]
    assert f["proc"] == "{name} GPU memory"
    assert f["other"] == "Other used RAM"
    assert f["note"] == FRESH_NOTE


def test_fresh_note_does_not_claim_the_cards_ram_is_inside_the_other_segment():
    # Fix round 2, B1: a card's RAM is RSS, which counts mapped model files
    # (page cache, counted as available). On the host the five cards' RAM
    # summed to 34.2 GiB against a 28.6 GiB segment; Talkie's mapped model
    # file was 13.2 GiB of its 14.9 GiB. The round-1 note said otherwise.
    note = LABELS["fresh"]["note"]
    for wrong in ("each process's own RAM", "caches", "holds", "the system,"):
        assert wrong not in note, wrong
    assert "memory-mapped model files" in note and "counts as available" in note
    assert "need not fit" in note


# ── /resources: the stats line's word when the memory file is not fresh (S1)

MEM_STAT_WORD = _literal(RESOURCES, "MEM_STAT_WORD")


def test_stats_line_says_the_sources_own_state():
    # One word per state that is not fresh, each its own; never "stale"
    # for all three (fix round 2, S1).
    assert MEM_STAT_WORD == {"stale": "stale", "missing": "missing", "unreadable": "unreadable"}
    assert "MEM_STAT_WORD[gpuMemSource?.state] || 'not known'" in _function(RESOURCES, "memStatWord")
    body = _function(RESOURCES, "renderMemBar")
    assert "memStatWord(srv.sources?.gpu_mem)" in body
    assert '<span class="stale">stale</span>' not in body


def test_every_state_the_payload_can_carry_has_its_word(tmp_path):
    def state():
        return _build(tmp_path, None, _props())["sources"]["gpu_mem"]["state"]
    seen = {_build(tmp_path, _gpu(ts=NOW - 200), _props())["sources"]["gpu_mem"]["state"],
            _build(tmp_path, _gpu(ts=NOW + 3600), _props())["sources"]["gpu_mem"]["state"]}
    (tmp_path / "gpu_mem.json").unlink()
    seen.add(state())
    (tmp_path / "gpu_mem.json").write_text("{ torn")
    seen.add(state())
    assert set(seen) == set(MEM_STAT_WORD)


def test_the_bar_takes_fresh_labels_only_from_a_fresh_memory_file():
    body = _function(RESOURCES, "memBarLabels")
    assert "gpuMemSource?.state === 'fresh' ? 'fresh' : 'withheld'" in body
    seg = _function(RESOURCES, "memBarSegments")
    # Withheld: no per-process segment, whatever the rows carry.
    assert "L.proc == null ? [] :" in seg
    assert "memBarLabels(srv.sources?.gpu_mem)" in seg
    assert "memBarSegments(sys, srv)" in _function(RESOURCES, "renderMemBar")


@pytest.mark.parametrize("ts,state", [(NOW - 10, "fresh"), (NOW - 200, "stale")])
def test_payload_carries_what_the_bar_reads(tmp_path, ts, state):
    out = _build(tmp_path, _gpu(ts=ts), _props())
    assert out["sources"]["gpu_mem"]["state"] == state
    gpu = [s["gpu_bytes"] for s in out["servers"]] + [o["gpu_bytes"] for o in out["other_processes"]]
    if state == "fresh":
        assert all(isinstance(b, int) and b > 0 for b in gpu)
    else:
        # Withheld: no row carries a GPU figure the bar could split out.
        assert gpu == [None, None, None]


def test_missing_and_unreadable_memory_file_are_withheld_too(tmp_path):
    out = _build(tmp_path, None, _props())
    assert out["sources"]["gpu_mem"]["state"] == "missing"
    (tmp_path / "gpu_mem.json").write_text("{ torn")
    out = _build(tmp_path, None, _props())
    # Neither is "fresh", so the page takes the withheld labels.
    assert out["sources"]["gpu_mem"]["state"] == "unreadable"


# ── /services: three states (S3) ──────────────────────────────────────────

HEALTH_DOT = _literal(SERVICES, "HEALTH_DOT")
SERVER_TAG = _literal(SERVICES, "SERVER_TAG")


def test_health_dot_has_three_states_and_unknown_is_not_down():
    assert HEALTH_DOT == {"answering": "up", "not_answering": "down", "unknown": "unknown"}
    # Every health the payload can carry has a dot.
    assert set(HEALTH_DOT) == {servers.ANSWERING, servers.NOT_ANSWERING, servers.UNKNOWN}
    assert ".status-dot.unknown" in SERVICES


def test_server_tags():
    assert SERVER_TAG == {
        "state_unknown": "state unknown",
        "not_running": "not running",
        "health_unknown": "health unknown",
        "answering": "running",
        "not_answering": "running, not answering",
    }


def test_server_view_keys_by_state_then_health():
    body = _function(SERVICES, "serverView")
    for line in ("s.state === 'unknown' ? 'state_unknown'",
                 "s.state === 'not_running' ? 'not_running'",
                 "s.health === 'unknown' ? 'health_unknown'",
                 "s.health === 'answering' ? 'answering' : 'not_answering'",
                 "dot: HEALTH_DOT[s.health] || 'unknown'"):
        assert line in body, line
    # The reasons: state's when not running, health's when not answering.
    assert "s.state !== 'running' && s.state_reason" in body
    assert "s.health !== 'answering' && s.health_reason" in body


def test_agent_dots_follow_their_server_in_three_states():
    body = _function(SERVICES, "agentView")
    assert "dot: 'unknown'" in body and "serverView(server)" in body
    # No two-state dot is left on a model server or an agent.
    assert not re.search(r"setDot\(`s-(model|agent)-", SERVICES)
    assert "setDotState(`s-agent-${i}`, v.dot" in SERVICES
    assert "setDotState(`s-model-${i}`, views[i].dot" in SERVICES


def test_payload_reasons_name_the_file_and_its_age(tmp_path):
    """The fields serverView reads, in each of the states /services shows."""
    # Props file stale: running, health unknown, the reason names the file and age.
    s = _by_name(_build(tmp_path, _gpu(), _props(ts=NOW - 400)))
    for row in s.values():
        assert (row["state"], row["health"]) == ("running", "unknown")
        assert row["health_reason"].startswith("stale: llama_props.json was written 400 s ago")
    # Memory file stale: state unknown, its reason names the file and age.
    s = _by_name(_build(tmp_path, _gpu(ts=NOW - 200), _props()))
    for row in s.values():
        assert row["state"] == "unknown" and row["health"] == "answering"
        assert row["state_reason"].startswith("stale: gpu_mem.json was written 200 s ago")
    # Running and not answering: the reason says which file lacks the port.
    props = _props()
    props["servers"] = [x for x in props["servers"] if x["port"] != 11438]
    g = _by_name(_build(tmp_path, _gpu(), props))["gemma-4-e4b"]
    assert (g["state"], g["health"]) == ("running", "not_answering")
    assert g["health_reason"] == "not answering: port 11438 is not in llama_props.json (written 20 s ago)"
    # Both missing: two reasons, one per file.
    (tmp_path / "none").mkdir()
    s = _by_name(_build(tmp_path / "none", None, None))
    for row in s.values():
        assert (row["state"], row["health"]) == ("unknown", "unknown")
        assert "gpu_mem.json does not exist" in row["state_reason"]
        assert "llama_props.json does not exist" in row["health_reason"]


# ── /resources: a /props error once on the card (N9) ──────────────────────

def test_props_error_reason_is_one_string_for_context_slots_and_health(tmp_path):
    """The page prints it once when n_ctx and total_slots share a reason
    and the health reason is that same string."""
    props = _props()
    err = {"unit": "llama-talkie.service", "flags": {"model": "talkie-1930-13b-it-Q8_0.gguf", "port": "11441"},
           "port": 11441, "error": "GET /props failed: TimeoutError", "model_bytes": 14_114_014_848}
    props["servers"] = [x for x in props["servers"] if x["port"] != 11441] + [err]
    t = _by_name(_build(tmp_path, _gpu(), props))["talkie"]
    assert t["reasons"]["n_ctx"] == t["reasons"]["total_slots"] == t["health_reason"] == err["error"]
    assert "r.n_ctx === r.total_slots ? r.n_ctx : null" in RESOURCES
    assert "sv.health_reason !== propsWhy" in RESOURCES
