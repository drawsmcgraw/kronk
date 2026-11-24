"""/api/work and /work (work item M27, criteria 10 to 12).

/api/work reads data/work.json and says how fresh it is: fresh, stale
(older than 180 s, the outline still returned and marked), missing,
unreadable, or of a schema the orchestrator does not know. It calls
nothing: no model, no LiteLLM, no herdr, no git.

The page's own state-to-text decisions are JSON literals in work.html
(SOURCE_BANNER, STAGE_CLASS, VERDICT_CLASS, STEP_LABEL, AGENT_TEXT); these
tests read them as JSON and check them against the states the route and
the exporter produce. The suite has no JavaScript runtime: nothing here
runs the page's script (the DOM building, the fetch, the time formatting).
That part was checked by rendering /dev/work in a browser
(docs/sprint/M27-build.md). No socket is opened.
"""
import json
import re
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

import work_api

pytestmark = pytest.mark.usefixtures("no_network")

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "orchestrator" / "static"
PAGE = (STATIC / "work.html").read_text()
SERVICES = (STATIC / "services.html").read_text()
SYSTEMD = REPO / "systemd"


def _literal(html: str, name: str):
    m = re.search(rf"const {name} = \{{", html)
    assert m, f"{name} is not a literal in the page"
    i, depth = m.end() - 1, 0
    for j in range(i, len(html)):
        depth += {"{": 1, "}": -1}.get(html[j], 0)
        if depth == 0:
            return json.loads(html[i:j + 1])
    raise AssertionError(f"{name}: unbalanced braces")


def _outline(ts, schema=1, **extra):
    d = {"schema": schema, "ts": ts, "made_at": "2026-11-24T18:00:00-05:00",
         "repo": {"branch": "main", "head": "abc1234", "detached": False, "rebase": False, "error": None},
         "herdr": {"known": True, "seen": 2, "reason": None, "elsewhere": 0,
                   "coordinator": {"status": "working", "word": "working", "reason": None}},
         "items": [{"id": "M27", "title": "<img src=x onerror=alert(1)>", "steps": [], "uncommitted": [],
                    "stage": {"stage": "testing", "words": "testing", "detail": "testing, agent m27-test working",
                              "blocked": False, "by": "agents"}}],
         "under_way": ["M27"], "other_agents": [], "backlog": [], "done": [], "shipped_unlinked": [],
         "not_read": [], "errors": []}
    d.update(extra)
    return d


@pytest.fixture
def work_file(tmp_path):
    p = tmp_path / "work.json"
    with patch.object(work_api, "WORK_PATH", p):
        yield p


class _NoClient:
    def __init__(self, *a, **kw):
        raise AssertionError("/api/work built an httpx client")


def _get():
    import orchestrator.main as orch
    with patch.object(httpx, "AsyncClient", _NoClient), \
         patch("subprocess.run", side_effect=AssertionError("/api/work ran a command")), \
         patch("subprocess.Popen", side_effect=AssertionError("/api/work ran a command")):
        r = TestClient(orch.app).get("/api/work")
    assert r.status_code == 200
    return r.json()


# ── criterion 10: staleness ────────────────────────────────────────────────

def test_fresh(work_file):
    work_file.write_text(json.dumps(_outline(time.time() - 5)))
    d = _get()
    assert d["source"]["state"] == "fresh" and d["source"]["reason"] is None
    assert 4 <= d["source"]["age_s"] <= 10 and d["source"]["max_age_s"] == 180
    assert d["work"]["under_way"] == ["M27"] and d["schema_known"] == 1


def test_stale_still_carries_the_outline(work_file):
    work_file.write_text(json.dumps(_outline(time.time() - 400)))
    d = _get()
    assert d["source"]["state"] == "stale"
    assert d["source"]["reason"].startswith("stale: work.json was written 40")
    assert "older than 180 s is stale" in d["source"]["reason"]
    assert d["work"]["items"][0]["id"] == "M27"


def test_just_inside_and_just_past_180_s(work_file):
    now = 1_800_000_000.0
    work_file.write_text(json.dumps(_outline(now - 180)))
    assert work_api.work_source(now)["source"]["state"] == "fresh"
    work_file.write_text(json.dumps(_outline(now - 181)))
    assert work_api.work_source(now)["source"]["state"] == "stale"


def test_missing(work_file):
    d = _get()
    assert d["source"]["state"] == "missing" and d["work"] is None
    assert "systemd/kronk-work-export.timer" in d["source"]["reason"]


@pytest.mark.parametrize("text", ["{not json", "[]", '{"schema": 1}', '{"schema": 1, "ts": "soon"}'])
def test_unreadable(work_file, text):
    work_file.write_text(text)
    d = _get()
    assert d["source"]["state"] == "unreadable" and d["work"] is None
    assert d["source"]["reason"].startswith("work.json cannot be read")


@pytest.mark.parametrize("schema", [None, 0, "1", True, 1.5])
def test_unknown_schema(work_file, schema):
    o = _outline(time.time())
    if schema is None:
        del o["schema"]
    else:
        o["schema"] = schema
    work_file.write_text(json.dumps(o))
    d = _get()
    assert d["source"]["state"] == "unknown_schema" and d["work"] is None
    assert f"has schema {schema!r}" in d["source"]["reason"] and "reads schema 1" in d["source"]["reason"]


def test_newer_schema_is_read_with_a_note(work_file):
    work_file.write_text(json.dumps(_outline(time.time(), schema=2, added_later=True)))
    d = _get()
    assert d["source"]["state"] == "fresh" and d["work"]["added_later"] is True
    assert d["source"]["schema_note"] == "work.json has schema 2, newer than this page's 1: keys added since are not shown"


def test_routes_are_registered():
    import orchestrator.main as orch
    paths = {getattr(r, "path", None) for r in orch.app.routes}
    assert {"/work", "/api/work"} <= paths


# ── criterion 11: the page's decisions ─────────────────────────────────────

def test_source_banner_covers_every_state_the_route_gives():
    banner = _literal(PAGE, "SOURCE_BANNER")
    assert set(banner) == {"fresh", "stale", "missing", "unreadable", "unknown_schema"}
    assert banner["fresh"] is None
    assert banner["stale"]["word"] == "STALE" and banner["stale"]["show"] is True
    assert "{age} s ago" in banner["stale"]["text"] and "last known state" in banner["stale"]["text"]
    for k in ("missing", "unreadable", "unknown_schema"):
        assert banner[k]["show"] is False and banner[k]["text"] == "{reason}" and banner[k]["bad"] is True
    assert banner["unknown_schema"]["word"] == "UNKNOWN SCHEMA"


def _exporter():
    import importlib.util
    import os
    path = os.environ.get("M27_WORK_EXPORT", REPO / "scripts" / "work_export.py")
    spec = importlib.util.spec_from_file_location("m27_we_page", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_stage_classes_cover_every_stage_the_exporter_gives():
    we = _exporter()
    assert set(_literal(PAGE, "STAGE_CLASS")) == set(we.STAGE_WORDS)
    assert set(_literal(PAGE, "VERDICT_CLASS")) == {"PASSES", "FAILS", "NOT DETERMINED"}
    labels = _literal(PAGE, "STEP_LABEL")
    assert set(labels) == {"criteria", "review", "build", "test", "fix", "other"}
    assert labels["test"] == "test {n}" and labels["fix"] == "fix {n}" and labels["other"] == "{name}"


def test_agent_text():
    t = _literal(PAGE, "AGENT_TEXT")
    assert t["known"] == "herdr: {seen} agents seen"
    assert t["coordinator"] == "coordinator: {word}"
    assert "agent state not known" in t["not_known"]


def test_repo_text_goes_in_as_text():
    script = PAGE.split("<script>", 1)[1]
    assert not re.search(r"\.(innerHTML|outerHTML|insertAdjacentHTML)\b|document\.write", script)
    assert ".textContent = String(text)" in script


def test_relative_urls_only():
    assert "fetch('api/work')" in PAGE
    assert not re.search(r"""(?:href|src)=["']/""", PAGE)
    assert not re.search(r"""fetch\(\s*['"`]/""", PAGE)


def test_services_lists_the_page():
    m = re.search(r"\{[^{}]*name: 'Work'[^{}]*\}", SERVICES)
    assert m and "href: '/work'" in m.group(0) and "probe: null" in m.group(0)


# ── units ──────────────────────────────────────────────────────────────────

def test_unit_files():
    svc = (SYSTEMD / "kronk-work-export.service").read_text()
    tmr = (SYSTEMD / "kronk-work-export.timer").read_text()
    assert "Type=oneshot" in svc and "TimeoutStartSec=30" in svc
    assert re.search(r"^ExecStart=/usr/bin/python3 -B \S+/scripts/work_export\.py$", svc, re.M)
    for op in ("install:", "check:", "one-shot:", "remove:"):
        assert op in svc, op
    assert "OnUnitActiveSec=60" in tmr and "WantedBy=timers.target" in tmr
