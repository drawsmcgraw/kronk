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

STATUS_OK = (True, "KRONK-OK status rev=abc123 version=2.32.0 pm2=online",
             {"rev": "abc123", "version": "2.32.0", "pm2": "online"})


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
    assert data["current_version"] == "2.32.0"
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
    assert data["live"]["pm2"] == "online"
    assert data["last_update"]["fields"]["new"] == "b"


@pytest.mark.asyncio
async def test_run_mm_update_persists_outcome(monkeypatch, tmp_path):
    """The background task's result must land on disk — it's what
    /magicmirror/status reports after the voice ack already went out."""
    last = tmp_path / "mm_update_last.json"

    async def fake_ssh(verb, timeout_s):
        assert verb == "update"
        return (True, "KRONK-OK update old=abc new=def backup=mm-backup-1.tar.gz",
                {"old": "abc", "new": "def", "backup": "mm-backup-1.tar.gz"})

    monkeypatch.setattr(ts, "_ssh_mm", fake_ssh)
    monkeypatch.setattr(ts, "MM_LAST_FILE", last)
    await ts._run_mm_update()
    saved = json.loads(last.read_text())
    assert saved["ok"] is True
    assert saved["fields"]["new"] == "def"


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
