"""Checker logic of scripts/coordinator_model_bench.py — no network.

The bench's verdicts are only as good as its checkers; these pin what
"pass", "leak" and "composite answered" mean so a future probe edit can't
silently loosen the scoreboard.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("LLM_SERVICE_URL",     "http://fake-llm:8002")
os.environ.setdefault("TOOL_SERVICE_URL",    "http://fake-tools:8003")
os.environ.setdefault("HEALTH_SERVICE_URL",  "http://fake-health:8004")
os.environ.setdefault("FINANCE_SERVICE_URL", "http://fake-finance:8005")

_SPEC = importlib.util.spec_from_file_location(
    "coordinator_model_bench",
    Path(__file__).resolve().parent.parent / "scripts" / "coordinator_model_bench.py",
)
bench = importlib.util.module_from_spec(_SPEC)
sys.modules["coordinator_model_bench"] = bench
_SPEC.loader.exec_module(bench)


def _msg(content="", calls=None):
    tcs = [{"id": f"c{i}", "type": "function",
            "function": {"name": n, "arguments": json.dumps(a)}}
           for i, (n, a) in enumerate(calls or [])]
    return {"role": "assistant", "content": content, "tool_calls": tcs or None}


# ── regime fidelity: prompt + menu come from the orchestrator, not a copy ──

def test_system_prompt_is_the_production_coordinator_prompt():
    from agents import COORDINATOR
    sp = bench.system_prompt()
    assert sp.startswith(COORDINATOR.system_prompt)
    assert "[Kronk ambient facts" in sp          # kronk_facts() stamp present


def test_tool_menu_is_the_coordinator_menu():
    names = [d["function"]["name"] for d in bench.tool_defs()]
    assert "ask_home" in names and "ask_research" in names and "news_brief" in names
    assert "get_weather" not in names            # specialists' tools never leak up


# ── checkers ────────────────────────────────────────────────────────────────

def test_has_call_matches_name_and_args():
    m = _msg(calls=[("news_brief", {"refresh": True})])
    assert bench.has_call(m, "news_brief")
    assert bench.has_call(m, "news_brief", refresh=True)
    assert not bench.has_call(m, "news_brief", refresh=False)
    assert not bench.has_call(m, "ask_home")


def test_has_call_rejects_malformed_arguments():
    m = {"tool_calls": [{"function": {"name": "ask_home", "arguments": "{not json"}}]}
    assert not bench.has_call(m, "ask_home")


def test_leak_detector_catches_deliberation_and_raw_tool_syntax():
    assert bench.leaked("The user is asking for the weather. The available tool is ask_home.")
    assert bench.leaked("<ifm|think>\nlet me see</ifm|think>\nIt is sunny.")
    assert bench.leaked("<ifm|tool_calls>\n<ifm|tool_call>{\"name\": \"ask_home\"}")
    assert bench.leaked('{"name": "ask_home", "arguments": {"query": "weather"}}')
    assert not bench.leaked("Denver is in the Mountain Time Zone (MT).")
    assert not bench.leaked("")


def test_markdown_and_placeholder_checks():
    assert bench.is_markdown_list("- deodorize\n- clean\n- bake\n")
    assert bench.is_markdown_list("1. a\n2. b\n3. c")
    assert not bench.is_markdown_list("Use it to deodorize, clean, and bake.")
    assert bench.no_placeholders("A heat pump moves heat rather than making it.")
    assert not bench.no_placeholders("You produced [kWh] worth about [rate].")
    assert not bench.no_placeholders("*winks* Sure thing.")


# ── the scripted composite loop ─────────────────────────────────────────────

def _fake_transport(script):
    """script: list of assistant messages to return, in order."""
    it = iter(script)

    def transport(base, messages, tools):
        msg = next(it)
        return ({"choices": [{"message": msg, "finish_reason": "stop"}],
                 "timings": {"predicted_per_second": 50.0}}, 0.5)
    return transport


def test_composite_passes_when_both_specialists_gathered_and_arithmetic_done(monkeypatch):
    monkeypatch.setitem(bench.COMPOSITE, "repeats", 1)
    script = [
        _msg(calls=[("ask_home", {"query": "kWh yesterday"})]),
        _msg(calls=[("ask_research", {"query": "electricity rate Laurel MD"})]),
        _msg("3.0 kWh at 16.18 ¢/kWh is about $0.49."),
    ]
    runs = bench.run_composite("http://x", "sys", transport=_fake_transport(script))
    assert runs[0]["ok"] and runs[0]["turns"] == 3
    assert runs[0]["calls"] == ["ask_home", "ask_research"]


def test_composite_fails_on_half_answer(monkeypatch):
    monkeypatch.setitem(bench.COMPOSITE, "repeats", 1)
    script = [
        _msg(calls=[("ask_home", {"query": "kWh yesterday"})]),
        _msg("The panels produced 3.0 kWh yesterday."),   # never fetched the rate
    ]
    runs = bench.run_composite("http://x", "sys", transport=_fake_transport(script))
    assert not runs[0]["ok"] and runs[0]["calls"] == ["ask_home"]


def test_composite_feeds_canned_tool_results_back(monkeypatch):
    monkeypatch.setitem(bench.COMPOSITE, "repeats", 1)
    seen = []

    def transport(base, messages, tools):
        seen.append([m["role"] for m in messages])
        if len(seen) == 1:
            return ({"choices": [{"message": _msg(calls=[("ask_home", {}), ("ask_research", {})]),
                                  "finish_reason": "tool_calls"}], "timings": {}}, 0.1)
        return ({"choices": [{"message": _msg("$0.49"), "finish_reason": "stop"}], "timings": {}}, 0.1)

    runs = bench.run_composite("http://x", "sys", transport=transport)
    assert runs[0]["ok"]
    # second call carries: system, user, assistant(tool_calls), tool, tool
    assert seen[1] == ["system", "user", "assistant", "tool", "tool"]


def test_scoreboard_counts_passes_and_leaks():
    results = {"label": "t", "stamp": "s", "models": {"m": {
        **{p["id"]: [{"ok": True, "leak": False, "gen_tps": 40, "elapsed_s": 1, "reasoning_chars": 10}]
           for p in bench.PROBES},
        bench.COMPOSITE["id"]: [{"ok": False, "leak": True, "gen_tps": 40, "elapsed_s": 9, "reasoning_chars": 0}],
    }}}
    md = bench.scoreboard(results)
    assert "| m |" in md and "0/1" in md and "| 1 |" in md   # one leak counted
