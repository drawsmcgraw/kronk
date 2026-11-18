"""GET /music/stations (sprint M13): the library's Pandora stations for the
Whisper vocabulary prompt. Reads MA through ma_read only; play counts from
the taste DB opened read-only. Made-up names; no network (`no_network`)."""
import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import tool_service.main as ts
from tool_service import taste

pytestmark = pytest.mark.usefixtures("no_network")

RADIOS = [
    {"uri": "library://radio/1", "name": "Moss Radio", "provider": "library",
     "provider_mappings": [{"provider_domain": "pandora"}]},
    {"uri": "library://radio/2", "name": "Fjord Radio ", "provider": "library",
     "provider_mappings": [{"provider_domain": "pandora"}]},
    {"uri": "library://radio/3", "name": "Tuner FM", "provider": "library",
     "provider_mappings": [{"provider_domain": "radiobrowser"}]},
    {"uri": "library://radio/4", "name": "", "provider": "library",
     "provider_mappings": [{"provider_domain": "pandora"}]},
    {"uri": "library://radio/5", "name": "Quartz Radio", "provider": "library",
     "provider_mappings": [{"provider_domain": "pandora"}]},
]


@pytest.fixture
def taste_db(tmp_path, monkeypatch):
    path = tmp_path / "music_taste.db"
    con = taste.db(path)
    con.executemany("INSERT INTO stations VALUES (?,?,?,?)", [
        ("library://radio/1", "Moss Radio", "library", 0.5),
        ("library://radio/2", "Fjord Radio", "library", 2.0),
    ])
    con.commit()
    con.close()
    monkeypatch.setattr(taste, "TASTE_DB", path)
    return path


def _reads(items, sent):
    async def fake_read(cmd, _timeout=20, **a):
        sent.append((cmd, a))
        return items if a.get("offset", 0) == 0 else []
    return fake_read


def test_lists_pandora_stations_by_plays_then_name(taste_db):
    sent = []
    with patch.object(ts, "ma_read", new=_reads(RADIOS, sent)):
        r = TestClient(ts.app).get("/music/stations")
    assert r.status_code == 200
    assert r.json() == {"provider": "pandora", "stations": [
        {"name": "Fjord Radio", "uri": "library://radio/2", "plays": 2.0},
        {"name": "Moss Radio", "uri": "library://radio/1", "plays": 0.5},
        {"name": "Quartz Radio", "uri": "library://radio/5", "plays": 0.0},
    ]}
    assert [c for c, _ in sent] == ["music/radios/library_items"]


def test_reads_only_through_ma_read(taste_db):
    """ma_read refuses anything outside MA_READ_COMMANDS; the route uses no other MA call."""
    async def boom(*a, **k):
        raise AssertionError("ma_command called directly")
    sent = []
    with patch.object(ts, "ma_read", new=_reads(RADIOS, sent)), patch.object(ts, "ma_command", new=boom):
        assert TestClient(ts.app).get("/music/stations").status_code == 200
    assert "music/radios/library_items" in ts.MA_READ_COMMANDS


def test_other_provider(taste_db):
    with patch.object(ts, "ma_read", new=_reads(RADIOS, [])):
        r = TestClient(ts.app).get("/music/stations", params={"provider": "radiobrowser"})
    assert [s["name"] for s in r.json()["stations"]] == ["Tuner FM"]


def test_bad_provider_is_422():
    assert TestClient(ts.app).get("/music/stations", params={"provider": "x; drop"}).status_code == 422


def test_ma_failure_is_502():
    async def down(cmd, _timeout=20, **a):
        raise ts.MAError("Music Assistant is unreachable (OSError).")
    with patch.object(ts, "ma_read", new=down):
        r = TestClient(ts.app).get("/music/stations")
    assert r.status_code == 502 and "unreachable" in r.json()["detail"]


def test_missing_taste_db_means_zero_plays_and_creates_nothing(tmp_path, monkeypatch):
    path = tmp_path / "none.db"
    monkeypatch.setattr(taste, "TASTE_DB", path)
    with patch.object(ts, "ma_read", new=_reads(RADIOS, [])):
        r = TestClient(ts.app).get("/music/stations")
    assert r.status_code == 200
    assert [s["plays"] for s in r.json()["stations"]] == [0.0, 0.0, 0.0]
    assert [s["name"] for s in r.json()["stations"]] == ["Fjord Radio", "Moss Radio", "Quartz Radio"]
    assert not path.exists()


def test_taste_db_is_not_written(taste_db):
    before = taste_db.read_bytes()
    with patch.object(ts, "ma_read", new=_reads(RADIOS, [])):
        TestClient(ts.app).get("/music/stations")
    assert taste_db.read_bytes() == before
    con = sqlite3.connect(taste_db)
    assert con.execute("SELECT count(*) FROM stations").fetchone()[0] == 2
    con.close()
