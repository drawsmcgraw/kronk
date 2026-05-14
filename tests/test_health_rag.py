"""
Tests for health RAG — chunker and vector store.

Test categories:
1. Chunker unit tests  — pure Python, zero external dependencies
2. Vector store tests  — require chromadb + fastembed; skipped if absent
"""
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEALTH_SVC = os.path.join(REPO_ROOT, "health_service")

if HEALTH_SVC not in sys.path:
    sys.path.insert(0, HEALTH_SVC)

from chunker import chunk_activity, chunk_daily, chunk_hrv, chunk_sleep


# ── 1. Chunker unit tests ─────────────────────────────────────────────────────

def test_chunk_daily_basic():
    r = {
        "date": "2025-12-27", "steps": 8719, "calories_total": 2274,
        "calories_active": 222, "distance_meters": 6960, "resting_hr": 58,
        "avg_stress": 32, "body_battery_high": 85, "body_battery_low": 20,
    }
    text = chunk_daily(r)
    assert "2025-12-27" in text
    assert "8,719 steps" in text
    assert "58 bpm" in text
    assert "20–85" in text


def test_chunk_daily_missing_fields():
    text = chunk_daily({"date": "2025-01-01"})
    assert "2025-01-01" in text
    assert "no data recorded" in text


def test_chunk_sleep_basic():
    r = {
        "date": "2025-06-11", "duration_seconds": 26040,
        "deep_seconds": 7260, "rem_seconds": 6240,
        "light_seconds": 12540, "awake_seconds": 0, "score": 92,
    }
    text = chunk_sleep(r)
    assert "2025-06-11" in text
    assert "score 92" in text
    assert "7h" in text


def test_chunk_sleep_zero_duration():
    text = chunk_sleep({"date": "2025-01-01", "duration_seconds": 0})
    assert "2025-01-01" in text
    assert "Sleep on" in text


def test_chunk_hrv_basic():
    r = {
        "date": "2025-03-15", "last_night": 45.3, "weekly_avg": 48.0,
        "baseline_low": 42.0, "baseline_high": 58.0, "status": "BALANCED",
    }
    text = chunk_hrv(r)
    assert "2025-03-15" in text
    assert "45ms" in text
    assert "BALANCED" in text


def test_chunk_hrv_null_fields():
    text = chunk_hrv({"date": "2025-01-01", "last_night": None, "weekly_avg": None})
    assert "2025-01-01" in text
    assert "HRV on" in text


def test_chunk_activity_basic():
    r = {
        "date": "2025-03-10", "name": "Morning Run", "type": "running",
        "duration_seconds": 2730, "distance_meters": 8200,
        "avg_hr": 142, "max_hr": 168, "calories": 520,
    }
    text = chunk_activity(r)
    assert "2025-03-10" in text
    assert "Morning Run" in text
    assert "8.2 km" in text
    assert "142 bpm" in text


def test_chunk_activity_no_distance():
    r = {"date": "2025-01-01", "name": "Strength Training", "duration_seconds": 3600, "avg_hr": 110}
    text = chunk_activity(r)
    assert "Strength Training" in text
    assert "1h 0m" in text


def test_chunk_activity_uses_type_when_no_name():
    r = {"date": "2025-01-01", "type": "cycling", "duration_seconds": 1800}
    text = chunk_activity(r)
    assert "cycling" in text


# ── 2. Vector store tests ─────────────────────────────────────────────────────

chromadb = pytest.importorskip("chromadb", reason="chromadb not installed — skipping vector store tests")
fastembed = pytest.importorskip("fastembed", reason="fastembed not installed — skipping vector store tests")


@pytest.fixture
def tmp_vs(tmp_path, monkeypatch):
    """Fresh vector store backed by a temp directory."""
    import vector_store as vs
    monkeypatch.setenv("HEALTH_DB_PATH", str(tmp_path / "health.db"))
    vs._collection = None
    vs._client = None
    vs._model = None
    yield vs
    vs._collection = None
    vs._client = None
    vs._model = None


def test_upsert_and_search(tmp_vs):
    chunks = [
        {"id": "2025-12-27_daily",
         "text": "Daily wellness on 2025-12-27: 8,719 steps, resting HR 58 bpm, body battery 20–85.",
         "metadata": {"date": "2025-12-27", "type": "daily"}},
        {"id": "2025-12-28_sleep",
         "text": "Sleep on 2025-12-28: 7h 14m, deep 2h 1m, REM 1h 44m, sleep score 92/100.",
         "metadata": {"date": "2025-12-28", "type": "sleep"}},
        {"id": "2025-12-29_daily",
         "text": "Daily wellness on 2025-12-29: 12,000 steps, resting HR 55 bpm, avg stress 20.",
         "metadata": {"date": "2025-12-29", "type": "daily"}},
    ]
    tmp_vs.upsert_chunks(chunks)
    assert tmp_vs.chunk_count() == 3

    results = tmp_vs.search("deep sleep quality and REM", n_results=2)
    assert len(results) > 0
    assert any("sleep" in r["text"].lower() for r in results)


def test_upsert_is_idempotent(tmp_vs):
    chunk = {
        "id": "2025-01-01_daily",
        "text": "Daily wellness on 2025-01-01: 5,000 steps.",
        "metadata": {"date": "2025-01-01", "type": "daily"},
    }
    tmp_vs.upsert_chunks([chunk])
    tmp_vs.upsert_chunks([chunk])
    assert tmp_vs.chunk_count() == 1


def test_upsert_updates_existing(tmp_vs):
    tmp_vs.upsert_chunks([{
        "id": "2025-01-01_daily", "text": "old text",
        "metadata": {"date": "2025-01-01", "type": "daily"},
    }])
    tmp_vs.upsert_chunks([{
        "id": "2025-01-01_daily", "text": "updated: 12,000 steps resting HR 55",
        "metadata": {"date": "2025-01-01", "type": "daily"},
    }])
    results = tmp_vs.search("steps heart rate", n_results=1)
    assert results[0]["text"].startswith("updated:")


def test_search_date_filter(tmp_vs):
    chunks = [
        {"id": "2025-01-01_daily", "text": "Daily on 2025-01-01: 5,000 steps high stress.",
         "metadata": {"date": "2025-01-01", "type": "daily"}},
        {"id": "2025-06-15_daily", "text": "Daily on 2025-06-15: 5,000 steps high stress.",
         "metadata": {"date": "2025-06-15", "type": "daily"}},
        {"id": "2025-12-31_daily", "text": "Daily on 2025-12-31: 5,000 steps high stress.",
         "metadata": {"date": "2025-12-31", "type": "daily"}},
    ]
    tmp_vs.upsert_chunks(chunks)
    results = tmp_vs.search("high stress", n_results=10, start_date="2025-06-01", end_date="2025-12-01")
    assert len(results) == 1
    assert results[0]["metadata"]["date"] == "2025-06-15"


def test_search_empty_store_returns_empty(tmp_vs):
    results = tmp_vs.search("anything at all")
    assert results == []


def test_search_score_is_bounded(tmp_vs):
    tmp_vs.upsert_chunks([{
        "id": "2025-01-01_sleep",
        "text": "Sleep on 2025-01-01: 8h, sleep score 85/100.",
        "metadata": {"date": "2025-01-01", "type": "sleep"},
    }])
    results = tmp_vs.search("sleep score")
    assert all(0.0 <= r["score"] <= 1.0 for r in results)
