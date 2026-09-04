"""
Coordinator model bench — same-regime comparison of coordinator candidates.

Born 2026-09-03 for K2 Horizon vs Gemma 4 (docs/plans/MODEL_BENCH_K2_HORIZON_PLAN.md),
built to outlive that question: any llama-server on a bench port can be
scored against the incumbent with the SAME system prompt, ambient-facts
stamp and ask_* menu the production coordinator gets — imported from
orchestrator/agents.py, not copied (one source of truth).

Regime fidelity, deliberately:
  - No sampling params in the payload (orchestrator/llm.py sends none;
    server-side defaults/flags apply, exactly as in production).
  - llama-server hit directly (not LiteLLM, not the pipeline) for clean
    per-model timings — the Gemma QAT/MTP and devops bench precedent.
  - Probes repeat, and the scoreboard counts, because 4B-class behaviour
    is probabilistic (the 2026-08-18 calibration lesson).

Decision rule (pre-committed in the plan doc): a challenger must beat the
incumbent on correctness, or tie on correctness and win >=2x on
generation speed.

Usage:
    tests/.venv/bin/python scripts/coordinator_model_bench.py <label> [model ...]
    tests/.venv/bin/python scripts/coordinator_model_bench.py --list

Candidate servers must be running first — start recipes are in the plan
doc. Results: docs/bench/coord_bench_<stamp>_<label>.json (raw, diffable)
                 docs/bench/coord_bench_<stamp>_<label>.md   (scoreboard)
"""

import json
import os
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
BENCH_DIR = REPO / "docs" / "bench"

# Importing the orchestrator's agent registry needs its service URLs to be
# *set*, not reachable — nothing is called at import time.
sys.path.insert(0, str(REPO / "orchestrator"))
for _k, _v in {
    "LLM_SERVICE_URL": "http://bench-unused:8002",
    "TOOL_SERVICE_URL": "http://bench-unused:8003",
    "HEALTH_SERVICE_URL": "http://bench-unused:8004",
    "FINANCE_SERVICE_URL": "http://bench-unused:8005",
}.items():
    os.environ.setdefault(_k, _v)

from agents import COORDINATOR, kronk_facts  # noqa: E402

# name -> OpenAI-compat base. ALL are bench ports started by hand (recipes in
# the plan doc) — the incumbent too: an isolated E4B copy with the production
# flags on 11497, so production slots and live users are never involved.
MODELS = {
    "gemma-4-e4b":        "http://127.0.0.1:11497",
    "gemma-4-12b":        "http://127.0.0.1:11491",
    "gemma-4-26b-a4b":    "http://127.0.0.1:11492",
    "k2-horizon-7b-q8":   "http://127.0.0.1:11493",
    "k2-horizon-3.7b":    "http://127.0.0.1:11494",
    "k2-mova-36b-a4b":    "http://127.0.0.1:11495",
    "k2-horizon-7b-q4":   "http://127.0.0.1:11496",
}

MAX_TOKENS = 2000     # generous: a runaway reasoning channel shows up as length-stop, not a hang
TIMEOUT_S = 300

# Canned specialist answers for the scripted composite probe. The numbers
# are the ones from the 2026-08-18 verified run (3.0 kWh × 16.18 ¢ = $0.49).
CANNED = {
    "ask_home": "Yesterday the solar panels produced 3.0 kWh.",
    "ask_research": "The residential electricity rate for Laurel, MD (BGE) is 16.18 cents per kWh.",
}
CANNED_DEFAULT = "I don't have that information."
COMPOSITE_ANSWERS = ("0.49", "49 cents", "49¢", "$.49", "0.48", "0.50")  # rounding slack

_LEAK_RES = [
    re.compile(r"^\s*(the user (is asking|wants|asked)|okay, the user|let me think|i need to (figure|determine|check))", re.I),
    re.compile(r"<\s*/?\s*(ifm\|)?think", re.I),
    re.compile(r"<\s*/?\s*(ifm\|)?tool_calls?\b", re.I),
    re.compile(r"\{\s*\"name\"\s*:\s*\"(ask_|news_brief)"),
]


def system_prompt() -> str:
    """Exactly what run_stream() builds for the coordinator (minus the
    FRIENDLY phrasing block, which only changes error wording)."""
    return COORDINATOR.system_prompt + "\n\n" + kronk_facts()


def tool_defs() -> list[dict]:
    return COORDINATOR.tool_defs()


def _calls(msg: dict) -> list[tuple[str, dict]]:
    out = []
    for tc in msg.get("tool_calls") or []:
        name = tc.get("function", {}).get("name")
        try:
            args = json.loads(tc["function"].get("arguments") or "{}")
        except (ValueError, KeyError, TypeError):
            args = None  # malformed arguments JSON
        out.append((name, args))
    return out


def has_call(msg: dict, name: str, **want) -> bool:
    for n, args in _calls(msg):
        if n != name or args is None:
            continue
        if all(args.get(k) == v for k, v in want.items()):
            return True
    return False


def call_names(msg: dict) -> list[str]:
    return [n for n, _ in _calls(msg)]


def leaked(content: str) -> bool:
    """Deliberation or raw tool-call syntax in the user-visible content."""
    return any(r.search(content or "") for r in _LEAK_RES)


def is_markdown_list(content: str) -> bool:
    return len(re.findall(r"^\s*(?:[-*•]|\d+[.)])\s+\S", content or "", re.M)) >= 3


def no_placeholders(content: str) -> bool:
    return not re.search(r"\[[A-Za-z ]{2,20}\]|\*[a-z]+\*", content or "")


# ── probes ──────────────────────────────────────────────────────────────────
# Single-turn probe: id, label, prompt, tools, repeats, check(msg, content) -> (ok, note)
PROBES = [
    {
        "id": "weather_delegate",
        "label": "Personal-data delegation: weather -> ask_home (5 runs)",
        "prompt": "What is the weather right now?",
        "tools": True, "repeats": 5,
        "check": lambda m, c: (has_call(m, "ask_home"), f"calls={call_names(m)}"),
    },
    {
        "id": "shopping_delegate",
        "label": "Personal-data delegation: shopping list -> ask_home",
        "prompt": "What's on my shopping list?",
        "tools": True, "repeats": 3,
        "check": lambda m, c: (has_call(m, "ask_home"), f"calls={call_names(m)}"),
    },
    {
        "id": "no_spurious",
        "label": "Pure knowledge: no delegation (5 runs)",
        "prompt": "What time zone is Denver in?",
        "tools": True, "repeats": 5,
        "check": lambda m, c: (
            not m.get("tool_calls") and "mountain" in (c or "").lower(),
            f"calls={call_names(m)} mountain={'mountain' in (c or '').lower()}",
        ),
    },
    {
        "id": "news_brief_terminal",
        "label": "Terminal tool: news brief -> news_brief (no refresh)",
        "prompt": "Give me a news brief.",
        "tools": True, "repeats": 3,
        "check": lambda m, c: (
            has_call(m, "news_brief") and not any(
                (a or {}).get("refresh") for n, a in _calls(m) if n == "news_brief"),
            f"calls={[(n, a) for n, a in _calls(m)]}",
        ),
    },
    {
        "id": "news_refresh_flag",
        "label": "Refresh discrimination: 'update the news feed' -> refresh=true",
        "prompt": "Update the news feed and then give me the brief.",
        "tools": True, "repeats": 3,
        "check": lambda m, c: (
            has_call(m, "news_brief", refresh=True),
            f"calls={[(n, a) for n, a in _calls(m)]}",
        ),
    },
    {
        "id": "knowledge_prose",
        "label": "Direct answer discipline: one paragraph, no placeholders/emotes",
        "prompt": "Explain how a heat pump works in one paragraph.",
        "tools": True, "repeats": 2,
        "check": lambda m, c: (
            not m.get("tool_calls") and no_placeholders(c) and len(c.strip()) > 200
            and c.strip().count("\n\n") <= 1,
            f"calls={call_names(m)} len={len(c or '')}",
        ),
    },
    {
        "id": "markdown_list",
        "label": "Markdown discipline: a list renders as a list",
        "prompt": "List three household uses for baking soda.",
        "tools": True, "repeats": 2,
        "check": lambda m, c: (not m.get("tool_calls") and is_markdown_list(c),
                               f"calls={call_names(m)} list={is_markdown_list(c)}"),
    },
]

COMPOSITE = {
    "id": "composite_solar",
    "label": "Composite: money's-worth yesterday = ask_home (kWh) + ask_research (rate) + arithmetic (5 runs)",
    "prompt": "How much money's worth did the solar panels produce yesterday?",
    "repeats": 5,
    "max_turns": 4,
}


# ── transport ───────────────────────────────────────────────────────────────

def chat(base: str, messages: list[dict], tools: bool) -> tuple[dict, float]:
    payload = {"model": "bench", "messages": messages, "max_tokens": MAX_TOKENS}
    if tools:
        payload["tools"] = tool_defs()
        payload["tool_choice"] = "auto"
    t0 = time.monotonic()
    resp = httpx.post(f"{base}/v1/chat/completions", json=payload, timeout=TIMEOUT_S)
    elapsed = time.monotonic() - t0
    resp.raise_for_status()
    return resp.json(), elapsed


def _run_record(data: dict, elapsed: float, ok: bool, note: str) -> dict:
    msg = data["choices"][0]["message"]
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""
    timings = data.get("timings") or {}
    usage = data.get("usage") or {}
    return {
        "ok": bool(ok),
        "note": note,
        "leak": leaked(content),
        "finish_reason": data["choices"][0].get("finish_reason"),
        "elapsed_s": round(elapsed, 2),
        "gen_tps": round(timings.get("predicted_per_second") or 0, 1),
        "prompt_tps": round(timings.get("prompt_per_second") or 0, 1),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "reasoning_chars": len(reasoning),
        "content": content,
        "reasoning_content": reasoning[:2000],
        "tool_calls": msg.get("tool_calls"),
    }


def run_probe(base: str, probe: dict, sysprompt: str) -> list[dict]:
    runs = []
    for i in range(probe["repeats"]):
        messages = [{"role": "system", "content": sysprompt},
                    {"role": "user", "content": probe["prompt"]}]
        data, elapsed = chat(base, messages, probe["tools"])
        msg = data["choices"][0]["message"]
        ok, note = probe["check"](msg, msg.get("content") or "")
        rec = _run_record(data, elapsed, ok, note)
        runs.append(rec)
        print(f"    run {i+1}/{probe['repeats']}: {'PASS' if ok else 'FAIL'} "
              f"({note}, {elapsed:.1f}s, {rec['gen_tps']} tok/s"
              f"{', LEAK' if rec['leak'] else ''})")
    return runs


def run_composite(base: str, sysprompt: str, transport=chat) -> list[dict]:
    """Scripted multi-turn: the model must gather kWh AND rate, then do the
    arithmetic. Canned specialist replies stand in for the ask_* tools."""
    runs = []
    for i in range(COMPOSITE["repeats"]):
        messages = [{"role": "system", "content": sysprompt},
                    {"role": "user", "content": COMPOSITE["prompt"]}]
        called: list[str] = []
        total_elapsed = 0.0
        turns = 0
        leak = False
        gen_tps: list[float] = []
        reasoning_chars = 0
        content = ""
        data = None
        while turns < COMPOSITE["max_turns"]:
            data, elapsed = transport(base, messages, True)
            turns += 1
            total_elapsed += elapsed
            msg = data["choices"][0]["message"]
            content = msg.get("content") or ""
            leak = leak or leaked(content)
            reasoning_chars += len(msg.get("reasoning_content") or "")
            t = (data.get("timings") or {}).get("predicted_per_second")
            if t:
                gen_tps.append(t)
            calls = msg.get("tool_calls") or []
            if not calls:
                break
            messages.append({"role": "assistant", "content": msg.get("content") or None,
                             "tool_calls": calls})
            for tc in calls:
                name = tc.get("function", {}).get("name")
                called.append(name)
                messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                                 "content": CANNED.get(name, CANNED_DEFAULT)})
        gathered = "ask_home" in called and "ask_research" in called
        answered = any(a in content for a in COMPOSITE_ANSWERS)
        ok = gathered and answered
        note = f"calls={called} turns={turns} answered={answered}"
        runs.append({
            "ok": ok, "note": note, "leak": leak, "turns": turns,
            "calls": called, "elapsed_s": round(total_elapsed, 2),
            "gen_tps": round(statistics.median(gen_tps), 1) if gen_tps else 0,
            "reasoning_chars": reasoning_chars,
            "finish_reason": data["choices"][0].get("finish_reason") if data else None,
            "content": content,
        })
        print(f"    run {i+1}/{COMPOSITE['repeats']}: {'PASS' if ok else 'FAIL'} "
              f"({note}, {total_elapsed:.1f}s{', LEAK' if leak else ''})")
    return runs


def _props(base: str) -> dict:
    """Provenance: model path + build from llama-server, if it answers."""
    try:
        p = httpx.get(f"{base}/props", timeout=5).json()
        return {"model_path": p.get("model_path"), "build_info": p.get("build_info"),
                "n_ctx": (p.get("default_generation_settings") or {}).get("n_ctx")}
    except Exception:
        return {}


def _median(xs):
    xs = [x for x in xs if x]
    return round(statistics.median(xs), 1) if xs else 0


def scoreboard(results: dict) -> str:
    ids = [p["id"] for p in PROBES] + [COMPOSITE["id"]]
    lines = [f"# Coordinator model bench — {results['label']} ({results['stamp']})", ""]
    lines.append("| Model | " + " | ".join(ids) +
                 " | leaks | med gen tok/s | med composite s | med reasoning chars |")
    lines.append("|---" * (len(ids) + 5) + "|")
    for name, res in results["models"].items():
        cells, leaks, tps, rchars = [], 0, [], []
        for pid in ids:
            runs = res.get(pid, [])
            cells.append(f"{sum(r['ok'] for r in runs)}/{len(runs)}")
            leaks += sum(r.get("leak", False) for r in runs)
            tps += [r["gen_tps"] for r in runs]
            rchars += [r.get("reasoning_chars", 0) for r in runs]
        comp = [r["elapsed_s"] for r in res.get(COMPOSITE["id"], [])]
        lines.append(f"| {name} | " + " | ".join(cells) +
                     f" | {leaks} | {_median(tps)} | {_median(comp)} | {_median(rchars)} |")
    lines += ["", "Rule: beat the incumbent on correctness, or tie and win >=2x on gen tok/s. "
              "`leaks` counts runs where deliberation or raw tool-call syntax reached `content`."]
    return "\n".join(lines) + "\n"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    if sys.argv[1] == "--list":
        print("models:", ", ".join(MODELS))
        print("probes:", ", ".join(p["id"] for p in PROBES), "+", COMPOSITE["id"])
        print(f"tools: {[d['function']['name'] for d in tool_defs()]}")
        return 0
    label = sys.argv[1]
    names = sys.argv[2:] or list(MODELS)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    sysprompt = system_prompt()
    results: dict = {"label": label, "stamp": stamp, "system_prompt": sysprompt,
                     "tools": [d["function"]["name"] for d in tool_defs()],
                     "max_tokens": MAX_TOKENS, "models": {}}

    for name in names:
        base = MODELS[name]
        try:
            httpx.get(f"{base}/health", timeout=5)
        except Exception as e:
            print(f"== {name}: SKIPPED (server not reachable at {base}: {e})")
            continue
        print(f"== {name} ({base})")
        res: dict = {"_props": _props(base)}
        for probe in PROBES:
            print(f"  {probe['id']}:")
            res[probe["id"]] = run_probe(base, probe, sysprompt)
        print(f"  {COMPOSITE['id']}:")
        res[COMPOSITE["id"]] = run_composite(base, sysprompt)
        results["models"][name] = res

    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    json_path = BENCH_DIR / f"coord_bench_{stamp}_{label}.json"
    json_path.write_text(json.dumps(results, indent=2))
    md_path = BENCH_DIR / f"coord_bench_{stamp}_{label}.md"
    md_path.write_text(scoreboard(results))
    print(f"\n{scoreboard(results)}\nWrote {json_path}\n      {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
