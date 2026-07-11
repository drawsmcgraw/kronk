"""Tests for the MagicMirror tier-1 updater (docs/plans/MAGICMIRROR_PLAN.md).

Covers the tool_service /magicmirror routes (preflight-then-background
pattern), the KRONK-OK/KRONK-FAIL contract parsing, and the orchestrator
tool + terminal-speech wiring. The SSH transport itself is exercised only
through _ssh_mm mocks — the real thing needs the Pi (operator live-test)."""
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import tool_service.main as ts


# ── KRONK status-line contract ────────────────────────────────────────────────

def test_parse_kronk_line_ok_with_fields():
    raw = "some npm noise\nKRONK-OK update old=abc123 new=def456 backup=mm-backup-x.tar.gz\n"
    ok, line, fields = ts._parse_kronk_line(raw)
    assert ok is True
    assert fields == {"old": "abc123", "new": "def456", "backup": "mm-backup-x.tar.gz"}


def test_parse_kronk_line_fail_and_missing():
    ok, line, _ = ts._parse_kronk_line("KRONK-FAIL update step=git-pull merge conflict")
    assert ok is False and "git-pull" in line
    ok, line, _ = ts._parse_kronk_line("script ran but printed nothing structured")
    assert ok is False
    assert "no KRONK status line" in line


def test_parse_kronk_line_uses_last_status_line():
    """A rollback hint inside earlier output must not shadow the final verdict."""
    raw = "KRONK-FAIL update step=x oops\nretrying...\nKRONK-OK update old=a new=b"
    ok, _, fields = ts._parse_kronk_line(raw)
    assert ok is True and fields["new"] == "b"


# ── routes ────────────────────────────────────────────────────────────────────

STATUS_OK = (True, "KRONK-OK status rev=abc123 version=2.34.0 service=active",
             {"rev": "abc123", "version": "2.34.0", "service": "active"})


def test_update_route_preflights_then_starts_background(monkeypatch):
    scheduled = []

    async def fake_ssh(verb, timeout_s):
        assert verb == "status"  # the preflight
        return STATUS_OK

    async def fake_update():
        scheduled.append(True)

    monkeypatch.setattr(ts, "_ssh_mm", fake_ssh)
    monkeypatch.setattr(ts, "_run_mm_update", fake_update)
    resp = TestClient(ts.app).post("/magicmirror/update")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "started"
    assert data["current_version"] == "2.34.0"
    assert "backup" in data["message"]


def test_update_route_unreachable_mirror_is_specific_502(monkeypatch):
    async def fake_ssh(verb, timeout_s):
        return (False, "could not reach the mirror at kronk@mirror.local: "
                       "ssh: connect to host mirror.local port 22: No route to host", {})

    monkeypatch.setattr(ts, "_ssh_mm", fake_ssh)
    resp = TestClient(ts.app).post("/magicmirror/update")
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "preflight" in detail.lower()
    assert "No route to host" in detail  # tenet 7: the real cause survives


def test_update_route_missing_key_is_actionable(monkeypatch):
    async def fake_ssh(verb, timeout_s):
        return (False, "SSH key not found at /keys/kronk-mm-update — mount ./secrets/mm", {})

    monkeypatch.setattr(ts, "_ssh_mm", fake_ssh)
    resp = TestClient(ts.app).post("/magicmirror/update")
    assert resp.status_code == 502
    assert "SSH key not found" in resp.json()["detail"]


def test_status_route_includes_last_update_outcome(monkeypatch, tmp_path):
    last = tmp_path / "mm_update_last.json"
    last.write_text(json.dumps({"ok": True, "detail": "KRONK-OK update old=a new=b",
                                "fields": {"old": "a", "new": "b"}, "finished_at": 1}))

    async def fake_ssh(verb, timeout_s):
        return STATUS_OK

    monkeypatch.setattr(ts, "_ssh_mm", fake_ssh)
    monkeypatch.setattr(ts, "MM_LAST_FILE", last)
    resp = TestClient(ts.app).get("/magicmirror/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["live"]["service"] == "active"
    assert data["last_update"]["fields"]["new"] == "b"


@pytest.mark.asyncio
async def test_run_mm_update_persists_outcome(monkeypatch, tmp_path):
    """The background task's result must land on disk — it's what
    /magicmirror/status reports after the voice ack already went out."""
    last = tmp_path / "mm_update_last.json"

    async def fake_ssh(verb, timeout_s):
        assert verb == "update"
        return (True, "KRONK-OK update old=abc new=def backup=mm-backup-1.tar.gz "
                      "mods_ok=8 mods_skipped=3 mods_failed=0",
                {"old": "abc", "new": "def", "backup": "mm-backup-1.tar.gz",
                 "mods_ok": "8", "mods_skipped": "3", "mods_failed": "0"})

    announced = []

    async def fake_announce(message, satellite=None):
        announced.append(message)
        return True

    monkeypatch.setattr(ts, "_ssh_mm", fake_ssh)
    monkeypatch.setattr(ts, "_ha_announce", fake_announce)
    monkeypatch.setattr(ts, "MM_LAST_FILE", last)
    await ts._run_mm_update()
    saved = json.loads(last.read_text())
    assert saved["ok"] is True
    assert saved["fields"]["new"] == "def"
    # the completion announcement closed the loop
    assert announced == ["The magic mirror updated to version def, 8 modules refreshed."]


# ── completion-announcement speech rendering ─────────────────────────────────

def test_mm_update_speech_success_variants():
    # prefers the friendly semver (version=) over the git rev (new=) — the
    # live 2026-07-11 run spoke a hash ("version 4b4a59534") before this fix
    ok = ts._mm_update_speech(
        True, {"new": "4b4a595", "version": "2.34.1", "mods_ok": "7",
               "mods_failed": "0"}, "")
    assert ok == "The magic mirror updated to version 2.34.1, 7 modules refreshed."
    # partial module failure is surfaced, not hidden
    partial = ts._mm_update_speech(
        True, {"new": "2.34.0", "mods_ok": "6", "mods_failed": "2"}, "")
    assert "2 modules had trouble" in partial
    # no module churn → no module clause
    plain = ts._mm_update_speech(True, {"new": "2.34.0", "mods_ok": "0"}, "")
    assert plain == "The magic mirror updated to version 2.34.0."


def test_mm_update_speech_failure_keeps_state_and_offers_rollback():
    """Locked decision: failure keeps the bad state, never auto-rolls-back,
    and invites an explicit rollback."""
    s = ts._mm_update_speech(
        False, {}, "KRONK-FAIL update step=npm-install dependency install failed")
    assert "failed at the npm install step" in s
    assert "kept a backup" in s
    assert "roll it back" in s
    assert "rolled" not in s.replace("roll it back", "")  # no auto-rollback claim


@pytest.mark.asyncio
async def test_announce_is_non_fatal(monkeypatch):
    """A failed announce must never break the update flow — the status file
    is the source of truth, announce is only a notification."""
    async def boom(*a, **k):
        raise RuntimeError("HA down")

    class Boom:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, *a, **k): raise RuntimeError("HA unreachable")

    monkeypatch.setattr(ts, "HA_TOKEN", "x")
    monkeypatch.setattr(ts.httpx, "AsyncClient", Boom)
    assert await ts._ha_announce("test") is False   # returns, does not raise


# ── staging transport (scp then run) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_ssh_mm_stages_script_then_runs_verb(monkeypatch, tmp_path):
    """The general-key model: scp the canonical script, then run it by path.
    Pin the sequence — mkdir, scp, then the chmod+run — so a regression that
    drops staging (back to the dead forced-command assumption) fails."""
    key = tmp_path / "key"; key.write_text("k")
    script = tmp_path / "mm-update.sh"; script.write_text("#!/bin/bash\n")
    monkeypatch.setattr(ts, "MM_SSH_KEY", str(key))
    monkeypatch.setattr(ts, "MM_SCRIPT", str(script))

    calls = []

    async def fake_run(cmd, timeout_s):
        calls.append(cmd)
        if cmd[0] == "scp":
            return 0, ""
        if "mm-update.sh status" in " ".join(cmd):
            return 0, "KRONK-OK status rev=abc version=2.34.0 service=active"
        return 0, ""   # the mkdir

    monkeypatch.setattr(ts, "_run", fake_run)
    ok, line, fields = await ts._ssh_mm("status", 20)
    assert ok is True
    assert fields["service"] == "active"
    progs = [c[0] for c in calls]
    assert progs == ["ssh", "scp", "ssh"]            # mkdir, stage, run
    assert "mm-update.sh status" in " ".join(calls[-1])


@pytest.mark.asyncio
async def test_ssh_mm_missing_script_is_actionable(monkeypatch, tmp_path):
    key = tmp_path / "key"; key.write_text("k")
    monkeypatch.setattr(ts, "MM_SSH_KEY", str(key))
    monkeypatch.setattr(ts, "MM_SCRIPT", str(tmp_path / "nope.sh"))
    ok, line, _ = await ts._ssh_mm("status", 20)
    assert ok is False
    assert "updater script not found" in line


# ── orchestrator wiring ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_update_magicmirror_success_and_failure():
    import tools

    class OkResp:
        status_code = 200
        def json(self):
            return {"status": "started", "current_version": "2.32.0",
                    "message": "updating from version 2.32.0 — a full backup "
                               "is taken first; this takes a few minutes"}

    class FailResp:
        status_code = 502
        text = ""
        def json(self):
            return {"detail": "Mirror preflight failed: could not reach the mirror"}

    class Client:
        def __init__(self, resp): self._resp = resp
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, *a, **kw): return self._resp

    with patch("tools.httpx.AsyncClient", return_value=Client(OkResp())):
        result = await tools.execute("update_magicmirror", {})
    assert result.startswith("[Magic mirror update started: ")
    assert "backup" in result

    with patch("tools.httpx.AsyncClient", return_value=Client(FailResp())):
        result = await tools.execute("update_magicmirror", {})
    assert "Could not update the magic mirror" in result
    assert "could not reach the mirror" in result
    assert "Do NOT claim the mirror is updating" in result


def test_terminal_speech_mappings_for_magicmirror():
    import agents
    ok = agents._terminal_speech(
        "[Magic mirror update started: updating from version 2.32.0 — a full "
        "backup is taken first; this takes a few minutes]"
    )
    assert ok == ("The magic mirror is updating from version 2.32.0 — a full "
                  "backup is taken first; this takes a few minutes.")
    fail = agents._terminal_speech(
        "[Could not update the magic mirror: Mirror preflight failed: no route to host]"
    )
    assert fail.startswith("I couldn't update the magic mirror. ")
    assert "no route to host" in fail


def test_home_agent_has_terminal_magicmirror_tool():
    import agents
    home = agents.AGENTS["home"]
    assert "update_magicmirror" in home.tool_names
    assert "update_magicmirror" in home.terminal_tools
