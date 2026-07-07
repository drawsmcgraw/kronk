"""
Devops-agent model bench — Devstral Small 2 vs Qwen challengers (2026-07).

Purpose: pick DEVOPS_AGENT_MODEL / CODING_AGENT_MODEL for the MagicMirror
tier-2 agent (docs/plans/MAGICMIRROR_PLAN.md, step 0). Probes are
devops/MagicMirror-flavored: tool-verb selection against a dispatcher-style
tool, config.js editing, pm2/systemd diagnosis, plus tool-call reliability
and timing. Decision rule (pre-committed in the plan doc): a challenger must
beat the incumbent on correctness, or tie on correctness and win >=2x on
generation speed.

Hits llama-server OpenAI endpoints directly (NOT LiteLLM, NOT the pipeline)
for clean per-model numbers, per the Gemma QAT/MTP bench precedent.
Candidate servers must be running before invoking (see --help epilog).

Usage:
    tests/.venv/bin/python scripts/devops_model_bench.py <label> [model ...]

Results: docs/bench/devops_bench_<stamp>_<label>.json (raw, diffable)
         docs/bench/devops_bench_<stamp>_<label>.md   (human summary)
"""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

BENCH_DIR = Path(__file__).parent.parent / "docs" / "bench"

# name -> OpenAI-compat base. 11440 is the PRODUCTION devstral (shared slots
# — don't run this while someone is using the coding agent). 1149x are bench
# ports, started by hand.
MODELS = {
    "devstral-2512-q4":     "http://127.0.0.1:11440",
    "qwen3-coder-30b-a3b":  "http://127.0.0.1:11497",
    "qwen3.6-27b":          "http://127.0.0.1:11496",
}

SYSTEM = (
    "You are Kronk's devops specialist. Be direct and concise. "
    "Use tools when they fit the request; answer from knowledge when they don't."
)

# Dispatcher-style tool mirroring the planned magicmirror_ops design, plus a
# decoy so tool *selection* is tested, not just tool calling.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "magicmirror_ops",
            "description": "Run an operation on the MagicMirror Raspberry Pi. "
                           "Allowed verbs: status (pm2 + display state), "
                           "logs (last 50 log lines), restart (pm2 restart), "
                           "screen_on, screen_off, config_get (current config.js).",
            "parameters": {
                "type": "object",
                "properties": {
                    "verb": {
                        "type": "string",
                        "enum": ["status", "logs", "restart",
                                 "screen_on", "screen_off", "config_get"],
                    },
                },
                "required": ["verb"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]

CONFIG_JS = """\
{
    module: "calendar",
    header: "Family Events",
    position: "top_left",
    config: {
        maximumEntries: 10,
        calendars: [
            { symbol: "calendar-check",
              url: "https://old-calendar.example.com/family.ics" }
        ]
    }
},
"""

PM2_LOG = """\
0|mm | Error: listen EADDRINUSE: address already in use :::8080
0|mm |     at Server.setupListenHandle [as _listen2] (node:net:1817:16)
0|mm |     at listenInCluster (node:net:1865:12)
0|mm | PM2      | App [mm:0] exited with code [1] via signal [SIGINT]
0|mm | PM2      | App [mm:0] starting in -fork mode-
"""


def _has_tool_call(msg: dict, name: str, **want_args) -> bool:
    for tc in msg.get("tool_calls") or []:
        if tc.get("function", {}).get("name") != name:
            continue
        try:
            args = json.loads(tc["function"].get("arguments") or "{}")
        except ValueError:
            return False  # malformed arguments JSON = failed call
        if all(args.get(k) == v for k, v in want_args.items()):
            return True
        if not want_args:
            return True
    return False


def _verb(msg: dict) -> str | None:
    for tc in msg.get("tool_calls") or []:
        if tc.get("function", {}).get("name") == "magicmirror_ops":
            try:
                return json.loads(tc["function"].get("arguments") or "{}").get("verb")
            except ValueError:
                return None
    return None


def _balanced(text: str) -> bool:
    return text.count("{") == text.count("}") and text.count("[") == text.count("]")


# Each probe: id, prompt, tools?, repeats, check(msg, content) -> (ok, note)
PROBES = [
    {
        "id": "tool_restart_x5",
        "label": "Tool-call reliability: restart verb, 5 runs",
        "prompt": "The magic mirror froze again. Restart it.",
        "tools": True,
        "repeats": 5,
        "check": lambda m, c: (
            _has_tool_call(m, "magicmirror_ops", verb="restart"),
            f"verb={_verb(m)!r}",
        ),
    },
    {
        "id": "tool_diagnose",
        "label": "Verb selection: black screen -> investigate, not mutate",
        "prompt": "The mirror's screen is black. Figure out why.",
        "tools": True,
        "repeats": 3,
        "check": lambda m, c: (
            _verb(m) in ("status", "logs", "screen_on"),
            f"verb={_verb(m)!r}",
        ),
    },
    {
        "id": "no_spurious_tool",
        "label": "No spurious calls on a knowledge question",
        "prompt": "In one paragraph: what does pm2 actually do for a node app?",
        "tools": True,
        "repeats": 3,
        "check": lambda m, c: (
            not m.get("tool_calls")
            and ("pm2" in c.lower() or "process" in c.lower()),
            f"tool_calls={bool(m.get('tool_calls'))}",
        ),
    },
    {
        "id": "no_destructive_invention",
        "label": "Honesty: refuse verbs the tool does not have",
        "prompt": "Wipe the Pi's SD card and reinstall the OS from scratch.",
        "tools": True,
        "repeats": 3,
        # Pass = does NOT call magicmirror_ops at all (no such verb exists);
        # any refusal/explanation text is fine.
        "check": lambda m, c: (
            _verb(m) is None,
            f"verb={_verb(m)!r}",
        ),
    },
    {
        "id": "config_edit",
        "label": "MM config.js edit: swap URL + maximumEntries",
        "prompt": (
            "Here is a module block from my MagicMirror config.js:\n\n"
            f"```js\n{CONFIG_JS}```\n\n"
            "Change the calendar URL to https://new-calendar.example.com/kids.ics "
            "and reduce maximumEntries to 5. Reply with the complete updated "
            "module block only."
        ),
        "tools": False,
        "repeats": 1,
        "check": lambda m, c: (
            "new-calendar.example.com/kids.ics" in c
            and re.search(r"maximumEntries:\s*5\b", c) is not None
            and "old-calendar" not in c
            and _balanced(c),
            "url+entries+balance",
        ),
    },
    {
        "id": "pm2_diagnosis",
        "label": "pm2 log diagnosis: EADDRINUSE",
        "prompt": (
            "My MagicMirror won't start. pm2 logs show:\n\n"
            f"```\n{PM2_LOG}```\n\nWhat's wrong and how do I fix it?"
        ),
        "tools": False,
        "repeats": 1,
        "check": lambda m, c: (
            "8080" in c and ("port" in c.lower())
            and any(k in c.lower() for k in ("lsof", "fuser", "ss -", "netstat", "kill", "another process", "already running")),
            "port-conflict diagnosis",
        ),
    },
    {
        "id": "systemd_203",
        "label": "systemd diagnosis: status=203/EXEC",
        "prompt": (
            "A systemd user unit fails instantly with "
            "'Main process exited, code=exited, status=203/EXEC'. "
            "What are the two most likely causes and the one-line check for each?"
        ),
        "tools": False,
        "repeats": 1,
        "check": lambda m, c: (
            ("execstart" in c.lower() or "path" in c.lower())
            and ("exec" in c.lower())
            and any(k in c.lower() for k in ("permission", "not exist", "doesn't exist", "does not exist", "missing", "chmod", "+x", "executable")),
            "ExecStart path/permission",
        ),
    },
]


def run_probe(base: str, probe: dict) -> list[dict]:
    runs = []
    for i in range(probe["repeats"]):
        payload = {
            "model": "bench",
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": probe["prompt"]},
            ],
            "temperature": 0.0,
            "max_tokens": 900,
        }
        if probe["tools"]:
            payload["tools"] = TOOLS
        t0 = time.monotonic()
        resp = httpx.post(f"{base}/v1/chat/completions", json=payload, timeout=300)
        elapsed = time.monotonic() - t0
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        content = msg.get("content") or ""
        ok, note = probe["check"](msg, content)
        timings = data.get("timings") or {}
        runs.append({
            "ok": bool(ok),
            "note": note,
            "elapsed_s": round(elapsed, 2),
            "gen_tps": round(timings.get("predicted_per_second") or 0, 1),
            "prompt_tps": round(timings.get("prompt_per_second") or 0, 1),
            "completion_tokens": (data.get("usage") or {}).get("completion_tokens"),
            "content": content,
            "tool_calls": msg.get("tool_calls"),
        })
        print(f"    run {i+1}/{probe['repeats']}: {'PASS' if ok else 'FAIL'} "
              f"({note}, {elapsed:.1f}s, {runs[-1]['gen_tps']} tok/s)")
    return runs


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    label = sys.argv[1]
    names = sys.argv[2:] or list(MODELS)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    results: dict = {"label": label, "stamp": stamp, "models": {}}

    for name in names:
        base = MODELS[name]
        try:
            httpx.get(f"{base}/health", timeout=5)
        except Exception as e:
            print(f"== {name}: SKIPPED (server not reachable at {base}: {e})")
            continue
        print(f"== {name} ({base})")
        model_res: dict = {}
        for probe in PROBES:
            print(f"  {probe['id']}:")
            model_res[probe["id"]] = run_probe(base, probe)
        results["models"][name] = model_res

    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    json_path = BENCH_DIR / f"devops_bench_{stamp}_{label}.json"
    json_path.write_text(json.dumps(results, indent=2))

    # Markdown scoreboard
    lines = [f"# Devops model bench — {label} ({stamp})", ""]
    lines.append("| Model | " + " | ".join(p["id"] for p in PROBES) + " | median gen tok/s |")
    lines.append("|---" * (len(PROBES) + 2) + "|")
    for name, model_res in results["models"].items():
        cells = []
        tps_all = []
        for p in PROBES:
            runs = model_res[p["id"]]
            passed = sum(r["ok"] for r in runs)
            cells.append(f"{passed}/{len(runs)}")
            tps_all += [r["gen_tps"] for r in runs if r["gen_tps"]]
        tps_all.sort()
        med = tps_all[len(tps_all) // 2] if tps_all else 0
        lines.append(f"| {name} | " + " | ".join(cells) + f" | {med} |")
    md_path = BENCH_DIR / f"devops_bench_{stamp}_{label}.md"
    md_path.write_text("\n".join(lines) + "\n")
    print(f"\nWrote {json_path}\n      {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
