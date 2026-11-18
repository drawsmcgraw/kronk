#!/usr/bin/env python3
"""Validate a live config directory against a release's own loaders
(sprint M21 step 2; used by scripts/deploy_tag.sh before a deploy).

    config_check.py --code <release dir> --dir <live config dir>

Runs each service's loader from <release dir> (orchestrator/config_loader.py
and tool_service/config_loader.py, the tag's own schema) on <live config
dir>, each in its own interpreter so the two modules named config_loader
never meet. Prints one line per problem, `config/<file>:<line>: <message>`,
and exits 0 when both load, 1 when either does not, 2 on a usage error.
Reads only; reaches no network (telemetry is off in the child).

A release older than M21 step 1 has no loaders and reads no config/: the
check says so and passes.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SERVICES = ("orchestrator", "tool_service")

_CHILD = r"""
import json, sys
from pathlib import Path
code, live, service = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
if service == "orchestrator":
    sys.path.insert(0, code + "/orchestrator")
    import config_loader
else:
    sys.path.insert(0, code)
    from tool_service import config_loader
try:
    cfg = config_loader.load(Path(live))
    print(json.dumps({"ok": True, "problems": [], "warnings": [str(w) for w in cfg.warnings]}))
except config_loader.ConfigInvalid as e:
    print(json.dumps({"ok": False, "problems": [str(p) for p in e.problems], "warnings": []}))
"""


def check(code: Path, live: Path) -> tuple[bool, list[str]]:
    lines, ok = [], True
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "KRONK_", "OTEL_"))}
    env["TELEMETRY_ENABLED"] = "false"
    for service in SERVICES:
        if not (code / service / "config_loader.py").is_file():
            lines.append(f"{service}: this release has no config loader (older than M21 step 1); "
                         "it reads no config/ and the live directory does not affect it")
            continue
        r = subprocess.run([sys.executable, "-I", "-c", _CHILD, str(code), str(live), service],
                           capture_output=True, text=True, env=env, timeout=120, cwd=str(live))
        try:
            out = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            ok = False
            lines.append(f"{service}: the check could not run (exit {r.returncode}): "
                         f"{(r.stderr.strip().splitlines() or ['no output'])[-1]}")
            continue
        if not out["ok"]:
            ok = False
        lines += [f"{service}: {p}" for p in out["problems"]]
        lines += [f"{service}: warning: {w}" for w in out["warnings"]]
    return ok, lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--code", required=True, type=Path, help="release (or repo) directory")
    ap.add_argument("--dir", required=True, type=Path, help="live config directory")
    a = ap.parse_args(argv)
    if not a.dir.is_dir():
        print(f"config_check: {a.dir} is not a directory", file=sys.stderr)
        return 2
    ok, lines = check(a.code.resolve(), a.dir.resolve())
    for line in lines:
        print(line)
    print("valid" if ok else "INVALID")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
