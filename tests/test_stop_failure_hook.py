"""The Claude Code StopFailure hook (decision #69): one dry-run push per
error type per cooldown, always exit 0, nothing sent in the suite."""
import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "stop_failure_notify.sh"


def run(event: dict, tmp_path: Path, **env):
    e = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "KRONK_NOTIFY_DRY_RUN": "1",
         "KRONK_ALERT_STATE_DIR": str(tmp_path / "state"), **env}
    return subprocess.run(["bash", str(SCRIPT)], input=json.dumps(event), text=True,
                          capture_output=True, env=e, timeout=20)


EVENT = {"session_id": "33266f1c-5e32-41ab", "cwd": "/home/x/git-repos/drawsmcgraw/kronk",
         "hook_event_name": "StopFailure", "error": "rate_limit",
         "error_details": "API Error: Rate limit reached"}


def test_a_failure_is_one_push_with_the_error_and_session(tmp_path):
    r = run(EVENT, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "DRY RUN tag=claude-stopfailure" in r.stdout
    assert "title=Claude session stopped: rate_limit" in r.stdout
    assert "API Error: Rate limit reached (session 33266f1c, kronk)" in r.stdout


def test_a_second_failure_of_the_same_type_inside_the_cooldown_is_suppressed(tmp_path):
    assert "DRY RUN" in run(EVENT, tmp_path).stdout
    r = run(EVENT, tmp_path)
    assert r.returncode == 0 and "DRY RUN" not in r.stdout and "suppressed [rate_limit]" in r.stdout
    other = run({**EVENT, "error": "server_error"}, tmp_path)
    assert "title=Claude session stopped: server_error" in other.stdout


def test_cooldown_zero_sends_every_time(tmp_path):
    run(EVENT, tmp_path, COOLDOWN_SEC="0")
    assert "DRY RUN" in run(EVENT, tmp_path, COOLDOWN_SEC="0").stdout


def test_missing_fields_and_bad_input_still_exit_zero(tmp_path):
    r = run({"hook_event_name": "StopFailure"}, tmp_path)
    assert r.returncode == 0
    assert "title=Claude session stopped: unknown" in r.stdout
    assert "The turn ended on an API error." in r.stdout
    bad = subprocess.run(["bash", str(SCRIPT)], input="not json", text=True, capture_output=True,
                         env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "KRONK_NOTIFY_DRY_RUN": "1",
                              "KRONK_ALERT_STATE_DIR": str(tmp_path / "s")}, timeout=20)
    assert bad.returncode == 0


def test_a_long_message_is_cut_and_an_odd_error_type_makes_a_safe_state_name(tmp_path):
    r = run({**EVENT, "error": "../../etc/x", "error_details": "y" * 500}, tmp_path)
    assert r.returncode == 0
    line = next(l for l in r.stdout.splitlines() if l.startswith("message="))
    assert line.count("y") == 200
    names = [p.name for p in (tmp_path / "state").iterdir()]
    assert names and all("/" not in n and ".." not in n.replace("stopfailure.", "", 1) for n in names)
