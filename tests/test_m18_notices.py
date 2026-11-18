"""Sprint M18, criteria 8 and 12: the notice after a run, what happens when
HA cannot take it (the status file, the /services warning, the hourly
retry), `ack`, and GET /api/upgrades. The notifier is always stubbed."""
import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import upgrades_api
from tests.m18_helpers import NOW, ROOT, FakeOps, U, base_files, gh_release, make_repo, rules, standard_web

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture
def house(tmp_path, monkeypatch):
    monkeypatch.setenv("KRONK_UPGRADE_STATE", str(tmp_path / "state"))
    repo = make_repo(tmp_path, base_files())
    return FakeOps(repo, standard_web()), tmp_path / "state"


def status(sd):
    return json.loads((sd / "status.json").read_text())


def client(path):
    upgrades_api.STATUS_FILE = str(path)
    app = FastAPI()
    app.include_router(upgrades_api.router)
    return TestClient(app)


# ── the notice text ──────────────────────────────────────────────────────

def test_notice_text_names_from_to_and_what_was_rolled_back_and_why():
    O = U.Outcome
    title, body = U.notice_text(
        [O("tempo", "3.1.1", "3.1.2", "kept"),
         O("grafana", "13.2.4", "13.2.6", "rolled_back", "http://localhost:3000/api/health: HTTP 500"),
         O("litellm", "v1.88.1", "v1.88.6", "not_started", "house busy: media_player.kitchen is playing")],
        [U.Plan({"name": "nginx"}, "skip", detail="pin '1.29-alpine' is not an exact version")],
        "data/upgrades/runs/x-minor.log")
    assert title == "Kronk upgrade: 1 upgraded, 1 rolled back"
    assert body.splitlines() == [
        "tempo 3.1.1 -> 3.1.2: upgraded",
        "grafana 13.2.4 -> 13.2.6: rolled back to 13.2.4 (http://localhost:3000/api/health: HTTP 500)",
        "litellm v1.88.1 -> v1.88.6: not started (house busy: media_player.kitchen is playing)",
        "Skipped: nginx (pin '1.29-alpine' is not an exact version)",
        "Log: data/upgrades/runs/x-minor.log",
    ]


def test_title_when_nothing_was_kept():
    title, _ = U.notice_text([U.Outcome("tempo", "3.1.1", "3.1.2", "rolled_back", "x")], [], "l")
    assert title == "Kronk upgrade: 1 rolled back"


def test_rollback_failed_title_leads():
    title, body = U.notice_text([U.Outcome("homeassistant", "2026.5.4", "2026.5.5", "rollback_failed", "HA API: 502")], [], "l")
    assert title == "Kronk upgrade: ROLLBACK FAILED (homeassistant)"
    assert "needs you" in body and "python3 scripts/upgrades.py ack" in body


# ── delivery, failure, retry ─────────────────────────────────────────────

def _notice(i="n1"):
    return {"id": i, "tag": "kronk-upgrades", "title": "Kronk upgrade: 1 upgraded", "body": "tempo 3.1.1 -> 3.1.2: upgraded",
            "created": NOW.isoformat()}


def test_a_notice_ha_does_not_take_is_kept_and_warned_then_retried(house):
    ops, sd = house
    st, log = {}, U.RunLog(None)
    ops.notify_ok = False
    assert U.deliver(ops, st, _notice(), NOW, log) is False
    assert [n["id"] for n in st["pending_notices"]] == ["n1"] and st["pending_notices"][0]["attempts"] == 1
    assert any(a["id"] == "notice:n1" and "Notice not delivered" in a["text"] for a in st["attention"])
    assert U.retry_pending(ops, st, NOW, log) == 1                  # HA still down
    assert st["pending_notices"][0]["attempts"] == 2
    ops.notify_ok = True
    assert U.retry_pending(ops, st, NOW, log) == 0                  # HA back
    assert st["pending_notices"] == [] and st["attention"] == []
    assert len(ops.notices) == 3


def test_a_notifier_that_raises_counts_as_not_delivered(house):
    ops, sd = house

    def boom(*a):
        raise OSError("bash missing")
    ops.notify = boom
    st = {}
    assert U.deliver(ops, st, _notice(), NOW, U.RunLog(None)) is False
    assert st["pending_notices"]


def test_the_notices_command_retries_under_the_lock(house):
    ops, sd = house
    sd.mkdir()
    U.save_status(sd, {"pending_notices": [_notice()], "attention": [{"id": "notice:n1", "text": "x"}]}, NOW)
    assert U.main(["notices"], ops=ops) == 0
    assert status(sd)["pending_notices"] == [] and status(sd)["attention"] == []
    assert [t for _, t, _ in ops.notices] == ["Kronk upgrade: 1 upgraded"]


def test_the_notices_command_does_nothing_when_nothing_is_pending(house):
    ops, sd = house
    assert U.main(["notices"], ops=ops) == 0
    assert ops.notices == [] and not sd.exists()


def test_when_ha_is_what_failed_the_result_is_still_on_services(house):
    """HA's patch upgrade fails and so does the notice (HA is down): the
    status file carries both, and /api/upgrades shows them as warnings."""
    ops, sd = house
    ops.web.table["https://ghcr.io/v2/home-assistant/home-assistant/tags/list"] = {"tags": ["2026.5.4", "2026.5.5"]}
    ops.web.table["https://api.github.com/repos/home-assistant/core/releases?"] = [gh_release("2026.5.5", 20)]
    ops.bad_versions = {"2026.5.5"}
    ops.cannot_return = {"2026.5.4"}
    ops.notify_ok = False
    assert U.minor_run(ops, rules(["homeassistant"]), sd) == 2
    st = status(sd)
    assert st["rollback_failed"]["component"] == "homeassistant"
    # M18 fix 1 (F2): the state comes first and says HA is down, in those words.
    assert st["pending_notices"][0]["title"] == "Kronk upgrade: ROLLBACK FAILED (homeassistant): Home Assistant is down"
    assert st["pending_notices"][0]["body"].startswith(
        "homeassistant 2026.5.4 -> 2026.5.5: ROLLBACK FAILED; running is nothing (homeassistant is stopped; "
        "Home Assistant is down); git has 2026.5.4; needs you")
    r = client(sd / "status.json").get("/api/upgrades").json()
    assert r["present"] is True and r["rollback_failed"]["component"] == "homeassistant"
    assert any("rollback FAILED for homeassistant" in w for w in r["warnings"])
    assert any("Notice not delivered" in w for w in r["warnings"])
    assert r["pending_notices"] == 1 and r["ack"] == "python3 scripts/upgrades.py ack"


def test_ack_clears_the_warning_and_keeps_a_record(house):
    ops, sd = house
    sd.mkdir()
    U.save_status(sd, {"attention": [{"id": "rolled_back:2026-12", "text": "grafana rolled back"}],
                       "rollback_failed": {"component": "tempo"}}, NOW)
    assert U.main(["ack"], ops=ops) == 0
    st = status(sd)
    assert st["attention"] == [] and st["rollback_failed"] is None
    assert st["acknowledged"]["attention"][0]["text"] == "grafana rolled back"
    r = client(sd / "status.json").get("/api/upgrades").json()
    assert r["warnings"] == [] and r["ack"] is None


# ── GET /api/upgrades ────────────────────────────────────────────────────

def test_api_without_a_status_file(tmp_path):
    r = client(tmp_path / "none.json").get("/api/upgrades")
    assert r.status_code == 200
    assert r.json()["present"] is False and r.json()["warnings"] == []


def test_api_with_an_unreadable_status_file_warns(tmp_path):
    (tmp_path / "s.json").write_text("{not json")
    r = client(tmp_path / "s.json").get("/api/upgrades").json()
    assert r["present"] is False and "unreadable" in r["warnings"][0]


def test_api_reports_the_last_run_and_the_major_report(tmp_path):
    (tmp_path / "s.json").write_text(json.dumps({
        "last_run": {"kind": "minor", "at": "2026-12-01T08:31:00+00:00", "result": "upgraded", "summary": "Kronk upgrade: 2 upgraded"},
        "major": {"report": "docs/upgrades/2027-01-08.md", "components": ["homeassistant 2026.5.4 -> 2026.12.1"]}}))
    r = client(tmp_path / "s.json").get("/api/upgrades").json()
    assert r["warnings"] == [] and r["last_run"]["result"] == "upgraded"
    assert r["major"]["report"] == "docs/upgrades/2027-01-08.md"


def test_api_only_reads():
    routes = [(r.path, sorted(r.methods)) for r in upgrades_api.router.routes]
    assert routes == [("/api/upgrades", ["GET"])]
    src = (ROOT / "orchestrator" / "upgrades_api.py").read_text()
    assert "write_text" not in src and "open(" not in src


def test_main_mounts_the_router_and_the_page_shows_it():
    assert "app.include_router(upgrades_api.router)" in (ROOT / "orchestrator" / "main.py").read_text()
    page = (ROOT / "orchestrator" / "static" / "services.html").read_text()
    assert 'id="upgrade-grid"' in page and "fetch('/api/upgrades')" in page and "loadUpgrades()" in page
    assert "WARNING: " in page and "clear with: " in page


def test_the_install_test_notice_goes_through_the_notifier_and_changes_nothing(house):
    ops, sd = house
    assert U.main(["test-notice"], ops=ops) == 0
    (tag, title, body), = ops.notices
    assert (tag, title) == ("kronk-upgrades", "Kronk: TEST upgrade notice") and "Nothing was upgraded" in body
    assert not sd.exists()
    ops.notify_ok = False
    assert U.main(["test-notice"], ops=ops) == 1
