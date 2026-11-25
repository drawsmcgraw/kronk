"""/resources shows only measured facts (M25): the activity query, the
catalog with names and addresses only, and the two pages.

Activity: per model, calls and tokens from this orchestrator's llm_metrics
with timestamp >= now - 24 h (the boundary row counts), "last used" over
the whole table, times out as ISO 8601 with Z (criterion 5). No socket is
opened.
"""
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

import metrics
import servers

pytestmark = pytest.mark.usefixtures("no_network")

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "orchestrator" / "static"
NOW = datetime(2026, 11, 24, 19, 0, 0, tzinfo=timezone.utc)
CAT = ["gemma-4-e4b", "talkie"]


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "metrics.db"
    monkeypatch.setattr(metrics, "METRICS_DB", path)
    metrics._init_db()

    def add(model, when, prompt=0, completion=0, agent="coordinator"):
        with sqlite3.connect(path) as c:
            c.execute("INSERT INTO llm_metrics (timestamp, agent, model, prompt_tokens, completion_tokens) "
                      "VALUES (?, ?, ?, ?, ?)", (when.strftime("%Y-%m-%d %H:%M:%S"), agent, model, prompt, completion))
    return add


def _by(out):
    return {m["model"]: m for m in out["models"]}


def test_no_rows(db):
    out = metrics.activity(CAT, now=NOW)
    assert [m["model"] for m in out["models"]] == CAT
    for m in out["models"]:
        assert (m["requests"], m["prompt_tokens"], m["completion_tokens"], m["last_used"]) == (0, 0, 0, None)
    assert out["not_in_catalog"] == []
    assert (out["since"], out["now"], out["window_hours"]) == ("2026-11-23T19:00:00Z", "2026-11-24T19:00:00Z", 24)
    assert "this Kronk" in out["source"]


def test_rows_only_older_than_the_window_count_nothing_but_last_used_shows(db):
    db("talkie", datetime(2026, 8, 29, 14, 2, 3), 900, 40)
    db("talkie", NOW - timedelta(hours=24, seconds=1), 100, 10)
    d = _by(metrics.activity(CAT, now=NOW))["talkie"]
    assert (d["requests"], d["prompt_tokens"], d["completion_tokens"]) == (0, 0, 0)
    assert d["last_used"] == "2026-11-23T18:59:59Z"


def test_a_row_exactly_on_the_boundary_counts(db):
    db("gemma-4-e4b", NOW - timedelta(hours=24), 7, 3)
    db("gemma-4-e4b", NOW - timedelta(minutes=5), 1000, 50)
    g = _by(metrics.activity(CAT, now=NOW))["gemma-4-e4b"]
    assert (g["requests"], g["prompt_tokens"], g["completion_tokens"]) == (2, 1007, 53)
    assert g["last_used"] == "2026-11-24T18:55:00Z"


def test_zero_token_rows_are_calls(db):
    db("talkie", NOW - timedelta(hours=1))
    db("talkie", NOW - timedelta(hours=2))
    t = _by(metrics.activity(CAT, now=NOW))["talkie"]
    assert (t["requests"], t["prompt_tokens"], t["completion_tokens"]) == (2, 0, 0)


def test_models_outside_the_catalog(db):
    db("qwen-old", NOW - timedelta(hours=3), 50, 5)    # inside the window: shown apart
    db("qwen-old", NOW - timedelta(hours=4), 10, 1)
    db("gemma-3-4b", NOW - timedelta(days=90), 10, 1)  # none inside: left out
    out = metrics.activity(CAT, now=NOW)
    assert [m["model"] for m in out["models"]] == CAT
    assert out["not_in_catalog"] == [{"model": "qwen-old", "requests": 2, "prompt_tokens": 60,
                                      "completion_tokens": 6, "last_used": "2026-11-24T16:00:00Z"}]


def test_now_defaults_to_utc_now_and_other_zones_convert(db):
    db("gemma-4-e4b", datetime.now(timezone.utc) - timedelta(minutes=1), 5, 1)
    assert _by(metrics.activity(CAT))["gemma-4-e4b"]["requests"] == 1
    est = NOW.astimezone(timezone(timedelta(hours=-5)))
    assert metrics.activity(CAT, now=est)["now"] == "2026-11-24T19:00:00Z"


def test_api_activity_route(db):
    import orchestrator.main as orch
    db("gemma-4-e4b", datetime.now(timezone.utc) - timedelta(minutes=1), 5, 1)
    with patch("servers.load_catalog", return_value=[{"name": n, "api_base": "", "port": None} for n in CAT]):
        data = TestClient(orch.app).get("/api/activity").json()
    assert _by(data)["gemma-4-e4b"]["requests"] == 1


def test_api_activity_without_a_database_fails_loud(tmp_path, monkeypatch):
    import orchestrator.main as orch
    monkeypatch.setattr(metrics, "METRICS_DB", tmp_path / "absent.db")
    resp = TestClient(orch.app).get("/api/activity")
    assert resp.status_code == 503
    assert "absent.db could not be read" in resp.json()["error"]
    assert not (tmp_path / "absent.db").exists()  # read-only: the query never creates it


# ── the catalog: names and addresses only (criterion 1) ──────────────────

def test_catalog_has_no_kronk_block():
    text = (REPO / "litellm" / "config.yaml").read_text()
    assert "kronk:" not in text
    for entry in yaml.safe_load(text)["model_list"]:
        assert set(entry) == {"model_name", "litellm_params"}, entry["model_name"]


def test_load_catalog_gives_name_api_base_and_port_only():
    with patch.object(servers, "LITELLM_CONFIG_PATH", REPO / "litellm" / "config.yaml"):
        cat = servers.load_catalog()
    assert [c["name"] for c in cat] == CAT
    assert all(set(c) == {"name", "api_base", "port"} for c in cat)
    assert {c["port"] for c in cat} == {11438, 11441}


# ── the pages (criterion 10, the parts a file can show) ───────────────────

def _api_urls(page):
    """Every API path the page's script names in a string literal."""
    return set(re.findall(r"""['"`](/?api/[a-z_/]+)""", (STATIC / page).read_text()))


def test_resources_fetches_relative_urls_only():
    html = (STATIC / "resources.html").read_text()
    assert _api_urls("resources.html") == {"api/activity", "api/servers", "api/system"}
    # Its own stylesheet and script too, so /dev/resources is dev's page throughout.
    assert 'href="static/kronk.css"' in html and 'src="static/js/kronk.js"' in html
    assert "/api/metrics" not in html


def test_services_fetches_its_server_data_relative():
    urls = _api_urls("services.html")
    assert {"api/servers", "api/agents", "api/config"} <= urls
    # Absolute on purpose (one status file per house, sprints M16 and M18).
    assert {u for u in urls if u.startswith("/")} == {"/api/upgrades", "/api/backups"}


@pytest.mark.parametrize("page", ["resources.html", "services.html"])
def test_pages_read_no_hand_written_field(page):
    html = (STATIC / page).read_text()
    for gone in ("(est.)", "measured_gb", "vram_gb", "ctx_k", "s.quant", "sv.quant", "s.params",
                 "sv.params", "healthy", "avg_tok_s", "avg_ttft_ms"):
        assert gone not in html, f"{page} still reads {gone}"
    assert "GiB" in html
