"""scripts/searxng_canary.sh — the alert must say WHICH check failed.

2026-11-09: the weekly canary pushed "Kronk search canary failed" when both
search probes had passed and only the image pin was 36 days old; the
operator read it as "the search tool is failing". The script is run for
real here with `docker` and `curl` replaced by stubs on PATH: the stub
docker prints the probe JSON the real one would, the stub curl answers the
Docker Hub tag query and records the HA notify payload.
"""
import json
import os
import shlex
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "searxng_canary.sh"

GOOD_PROBE = {"status": 200, "engines": ["duckduckgo", "yahoo"], "unresponsive": [],
              "hits": 5, "n": 5, "detail": None}
JUNK_PROBE = {"status": 200, "engines": ["bing"], "unresponsive": ["duckduckgo: CAPTCHA"],
              "hits": 0, "n": 5, "detail": None}


def _write_exe(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run_canary(tmp_path: Path, probe: dict, pin: str, newest: str) -> tuple[subprocess.CompletedProcess, dict | None, dict]:
    """Run the script in a fake repo; return (proc, notify payload or None, state)."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "lib").symlink_to(REPO / "scripts" / "lib")
    (repo / "docker-compose.yml").write_text(f"services:\n  searxng:\n    image: searxng/searxng:{pin}\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    notify_file = tmp_path / "notify.json"
    _write_exe(bin_dir / "docker", f"cat >/dev/null\necho {shlex.quote(json.dumps(probe))}\n")
    _write_exe(bin_dir / "curl", f"""
if [[ "$*" == *hub.docker.com* ]]; then
  echo {shlex.quote(json.dumps({"results": [{"name": newest}, {"name": "latest"}]}))}; exit 0
fi
# HA notify: the payload follows -d
while [[ $# -gt 0 ]]; do [[ "$1" == "-d" ]] && {{ printf '%s' "$2" > {shlex.quote(str(notify_file))}; }}; shift; done
exit 0
""")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "KRONK_REPO_DIR": str(repo), "STATE_FILE": str(tmp_path / "state.json"),
           "HA_TOKEN": "test-token"}
    env.pop("CANARY_FORCE_ALERT", None)
    proc = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
    payload = json.loads(notify_file.read_text()) if notify_file.exists() else None
    state = json.loads((tmp_path / "state.json").read_text())
    return proc, payload, state


pytestmark = pytest.mark.skipif(
    subprocess.run(["bash", "-c", "command -v jq"], capture_output=True).returncode != 0,
    reason="jq not installed")


def test_stale_pin_with_passing_search_says_so_in_the_title(tmp_path):
    proc, payload, state = _run_canary(tmp_path, GOOD_PROBE, pin="2026.10.4-d48c4b555", newest="2026.11.9-57ff753ad")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert state["result"] == "fail"
    assert payload is not None, "no HA push recorded"
    assert "pin stale" in payload["title"]
    assert "search OK" in payload["title"]
    assert "FAILING" not in payload["title"]
    assert "36 days behind" in payload["message"]
    assert payload["data"]["tag"] == "searxng-canary"


def test_junk_search_results_title_names_search(tmp_path):
    proc, payload, state = _run_canary(tmp_path, JUNK_PROBE, pin="2026.11.9-57ff753ad", newest="2026.11.9-57ff753ad")
    assert proc.returncode == 1
    assert payload is not None
    assert "search FAILING" in payload["title"]
    assert "pin" not in payload["title"]
    assert "0/5 results mention" in payload["message"]


def test_both_failing_title_names_both(tmp_path):
    proc, payload, _ = _run_canary(tmp_path, JUNK_PROBE, pin="2026.10.4-d48c4b555", newest="2026.11.9-57ff753ad")
    assert proc.returncode == 1
    assert "search FAILING" in payload["title"]
    assert "pin stale" in payload["title"]


def test_all_good_passes_without_a_push(tmp_path):
    proc, payload, state = _run_canary(tmp_path, GOOD_PROBE, pin="2026.11.9-57ff753ad", newest="2026.11.9-57ff753ad")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert payload is None
    assert state == {"last_run": state["last_run"], "result": "pass", "pin": "2026.11.9-57ff753ad"}
    assert "PASS" in proc.stdout
