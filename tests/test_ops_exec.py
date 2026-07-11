"""Tests for the general-ops read-only classifier + registry + /ops/exec.

The classifier IS the safety boundary (docs/plans/MAGICMIRROR_PLAN.md
"General ops agent"). Phase A must run read-only commands and refuse
everything that could mutate, chain, redirect, or substitute — pinned here
because a regression here is a security hole, not a cosmetic bug."""
import json

import pytest
from fastapi.testclient import TestClient

import tool_service.ops as ops


# ── classifier: allow ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    "uptime",
    "uptime -p",
    "/usr/bin/uptime",                         # absolute path tolerated
    "systemctl --user status magicmirror",
    "systemctl --user is-active magicmirror",
    "journalctl --user -u magicmirror -n 50",
    "git log --oneline -5",
    "git rev-parse --short HEAD",
    "df -h",
    "free -m",
    "ps aux",
    "ps aux | grep node",                      # pipe between read programs
    "cat ~/MagicMirror/config/config.js | grep module",
    "vcgencmd measure_temp",                   # Pi-specific, useful
    "npm ls --depth=0",
])
def test_readonly_allowed(cmd):
    ok, reason = ops.classify_readonly(cmd)
    assert ok, f"{cmd!r} should be allowed, got: {reason}"


# ── classifier: refuse ────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmd,needle", [
    ("reboot",                          "allowlist"),
    ("sudo reboot",                     "sudo"),
    ("rm -rf ~/MagicMirror",            "allowlist"),
    ("systemctl --user restart magicmirror", "read-only subcommand"),
    ("git pull",                        "read-only subcommand"),
    ("git reset --hard",               "read-only subcommand"),
    ("npm install",                    "read-only subcommand"),
    ("uptime; rm -rf /",               "chaining"),           # ;
    ("uptime && reboot",               "chaining"),           # &&
    ("cat /etc/passwd > /tmp/x",       "redirection"),        # >
    ("echo $(reboot)",                 "substitution"),       # $(
    ("cat `whoami`",                   "`"),                  # backtick
    ("uptime & ",                      "background"),         # &
    ("ps aux | rm -rf /",              "allowlist"),          # bad program in pipe
    ("journalctl --vacuum-size=1M",    "forbidden"),          # mutating flag
    ("sed -i s/a/b/ config.js",        "allowlist"),          # sed not allowed
    ("FOO=bar uptime",                 "env-assignment"),
    ("",                               "empty"),
])
def test_mutations_and_injections_refused(cmd, needle):
    ok, reason = ops.classify_readonly(cmd)
    assert not ok, f"{cmd!r} should be refused"
    assert needle in reason, f"reason {reason!r} should mention {needle!r}"


def test_dual_use_program_needs_readonly_subcommand():
    assert ops.classify_readonly("systemctl --user show magicmirror")[0]
    assert not ops.classify_readonly("systemctl --user mask magicmirror")[0]
    assert ops.classify_readonly("docker ps")[0]
    assert not ops.classify_readonly("docker rm x")[0]


# ── registry ──────────────────────────────────────────────────────────────────

def test_registry_env_fallback(monkeypatch):
    monkeypatch.setattr(ops, "OPS_HOSTS_FILE", ops.Path("/nonexistent.json"))
    monkeypatch.setenv("MM_SSH_TARGET", "pi@mirror")
    monkeypatch.setenv("MM_SSH_KEY", "/keys/k")
    reg = ops.load_registry()
    assert reg["magicmirror"]["ssh_target"] == "pi@mirror"


def test_registry_file_wins(monkeypatch, tmp_path):
    f = tmp_path / "hosts.json"
    f.write_text(json.dumps({"nas": {"ssh_target": "u@nas", "key": "/keys/n"}}))
    monkeypatch.setattr(ops, "OPS_HOSTS_FILE", f)
    monkeypatch.delenv("MM_SSH_TARGET", raising=False)
    reg = ops.load_registry()
    assert "nas" in reg


def test_audit_writes_line(monkeypatch, tmp_path):
    log = tmp_path / "audit.log"
    monkeypatch.setattr(ops, "OPS_AUDIT_LOG", log)
    ops.audit_exec("magicmirror", "uptime", 0, 42, allowed=True)
    ops.audit_exec("magicmirror", "reboot", None, 0, allowed=False, note="refused")
    lines = [json.loads(l) for l in log.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[0]["allowed"] is True and lines[1]["allowed"] is False


# ── /ops/exec route ───────────────────────────────────────────────────────────

@pytest.fixture
def ops_client(tmp_path, monkeypatch):
    import tool_service.main as main_mod
    monkeypatch.setattr(main_mod.ops, "OPS_AUDIT_LOG", tmp_path / "audit.log")
    monkeypatch.setattr(main_mod.ops, "OPS_HOSTS_FILE", tmp_path / "nohosts.json")
    monkeypatch.setenv("MM_SSH_TARGET", "pi@mirror")
    monkeypatch.setenv("MM_SSH_KEY", str(tmp_path / "key"))
    (tmp_path / "key").write_text("k")
    return TestClient(main_mod.app)


def test_ops_exec_runs_readonly(ops_client, monkeypatch):
    import tool_service.main as main_mod

    async def fake_run(cmd, timeout_s):
        assert cmd[-2:] == ["pi@mirror", "uptime -p"]   # host + command last
        return 0, "up 3 days, 4 hours"
    monkeypatch.setattr(main_mod, "_run", fake_run)

    r = ops_client.post("/ops/exec", json={"host": "magicmirror", "command": "uptime -p"})
    assert r.status_code == 200
    body = r.json()
    assert body["exit_code"] == 0
    assert "up 3 days" in body["output"]


def test_ops_exec_refuses_mutation_with_422(ops_client):
    r = ops_client.post("/ops/exec",
                        json={"host": "magicmirror", "command": "sudo reboot"})
    assert r.status_code == 422
    assert "refused" in r.json()["detail"]


def test_ops_exec_unknown_host_404(ops_client):
    r = ops_client.post("/ops/exec", json={"host": "kronk", "command": "uptime"})
    assert r.status_code == 404
    assert "unknown host" in r.json()["detail"]
