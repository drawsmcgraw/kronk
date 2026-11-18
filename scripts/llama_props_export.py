#!/usr/bin/env python3
"""Export what each running llama.cpp server reports, for /settings (sprint
M21 step 2). Runs ON THE HOST (systemd user timer llama-props-export.timer,
every 60 s): the servers listen on the host's 127.0.0.1, which the
containers cannot reach, so the file is the bridge, as for gpu_mem.json.

For each running llama-*.service unit: its MainPID's command line (the
flags actually in effect: --ctx-size, --reasoning-budget, --cache-reuse,
--swa-full, --parallel, -ngl, the draft model) and the server's own
GET http://127.0.0.1:<port>/props (n_ctx, total_slots and the default
sampling parameters). Reads only. Standard library only.

    llama_props_export.py [--out FILE ...] [--print]

Each --out file is written atomically (temp file, rename). Default: the
repo's data/llama_props.json and data/dev/llama_props.json (production's
and dev's orchestrators read /data/llama_props.json).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = [REPO / "data" / "llama_props.json", REPO / "data" / "dev" / "llama_props.json"]
SAMPLING_KEYS = ("temperature", "top_k", "top_p", "min_p", "typical_p", "repeat_penalty", "repeat_last_n",
                 "presence_penalty", "frequency_penalty", "mirostat", "dry_multiplier", "xtc_probability",
                 "samplers", "n_predict", "reasoning_format")
FLAG_NAMES = {"--ctx-size": "ctx_size", "-c": "ctx_size", "--reasoning-budget": "reasoning_budget",
              "--cache-reuse": "cache_reuse", "--parallel": "parallel", "-np": "parallel",
              "-ngl": "gpu_layers", "--n-gpu-layers": "gpu_layers", "--model-draft": "model_draft",
              "--spec-type": "spec_type", "--flash-attn": "flash_attn", "--port": "port", "-m": "model",
              "--model": "model", "--chat-template-file": "chat_template_file"}
BOOL_FLAGS = {"--swa-full": "swa_full", "--no-warmup": "no_warmup", "--jinja": "jinja"}


def units() -> list[tuple[str, int]]:
    r = subprocess.run(["systemctl", "--user", "list-units", "--type=service", "--state=running",
                        "--no-legend", "--plain"], capture_output=True, text=True, timeout=10)
    out = []
    for line in r.stdout.splitlines():
        name = line.split()[0] if line.split() else ""
        if not name.startswith("llama-"):
            continue
        pid = subprocess.run(["systemctl", "--user", "show", "-p", "MainPID", "--value", name],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        if pid.isdigit() and int(pid) > 0:
            out.append((name, int(pid)))
    return out


def flags(pid: int) -> dict:
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return {}
    argv = [a.decode("utf-8", "replace") for a in argv if a]
    out: dict = {}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in BOOL_FLAGS:
            out[BOOL_FLAGS[a]] = True
        elif a in FLAG_NAMES and i + 1 < len(argv):
            out[FLAG_NAMES[a]] = argv[i + 1]
            i += 1
        i += 1
    for k in ("model", "model_draft", "chat_template_file"):
        if k in out:
            out[k] = os.path.basename(out[k])
    return out


def props(port: int) -> dict:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/props", timeout=5) as r:
            data = json.loads(r.read())
    except Exception as e:
        return {"error": f"GET /props failed: {type(e).__name__}"}
    gen = data.get("default_generation_settings") or {}
    params = gen.get("params") or gen
    return {"n_ctx": gen.get("n_ctx"), "total_slots": data.get("total_slots"),
            "model_alias": data.get("model_alias"),
            "sampling": {k: params[k] for k in SAMPLING_KEYS if k in params}}


def collect() -> dict:
    servers = []
    for unit, pid in units():
        f = flags(pid)
        entry = {"unit": unit, "flags": f}
        port = f.get("port")
        if port and str(port).isdigit():
            entry["port"] = int(port)
            entry.update(props(int(port)))
        servers.append(entry)
    return {"ts": time.time(), "servers": servers}


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
        f.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, action="append", help="file to write (repeatable)")
    ap.add_argument("--print", action="store_true", help="print the JSON instead of writing")
    a = ap.parse_args(argv)
    data = collect()
    if a.print:
        json.dump(data, sys.stdout, indent=1)
        print()
        return 0
    for p in a.out or DEFAULT_OUT:
        write(p, data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
