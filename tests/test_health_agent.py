"""
Tests for health_service agent query endpoint and CSV import.

Success criteria:
- /api/query returns a snapshot when DB has data
- /api/query returns {"status": "no_data"} when DB is empty
- /api/import/csv inserts activity rows from a valid Garmin CSV
- /api/import/csv handles malformed rows gracefully (skips, doesn't crash)
"""
import csv
import io
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_health_db(tmp_path, monkeypatch, use_health_service):
    """Wire health_service to use a temporary DB file."""
    db_path = tmp_path / "health.db"
    monkeypatch.setenv("HEALTH_DB_PATH", str(db_path))
    import db as db_mod  # resolves to health_service/db.py via use_health_service fixture
    db_mod.init_db()
    return db_path


@pytest.fixture
def health_client(tmp_health_db):
    """TestClient for health_service with a fresh temp DB."""
    with patch("apscheduler.schedulers.background.BackgroundScheduler.start"):
        import health_service.main as main_mod
        with TestClient(main_mod.app) as c:
            yield c


@pytest.fixture
def seeded_health_db(tmp_health_db):
    """Insert one week of synthetic health data."""
    import db as db_mod  # resolves to health_service/db.py via use_health_service fixture
    today = date.today()
    with db_mod.get_conn() as conn:
        for i in range(7):
            d = (today - timedelta(days=i)).isoformat()
            conn.execute("""
                INSERT OR REPLACE INTO daily_summary
                (date, steps, calories_total, calories_active, distance_meters,
                 resting_hr, avg_stress, max_stress, body_battery_high, body_battery_low, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (d, 8000 + i * 100, 2000, 500, 6000, 58 + i, 30, 60, 80, 20, "2026-01-01T00:00:00"))

            conn.execute("""
                INSERT OR REPLACE INTO sleep
                (date, start_time, end_time, duration_seconds, deep_seconds,
                 light_seconds, rem_seconds, awake_seconds, score, avg_hrv, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (d, "2026-01-01T23:00:00", "2026-01-02T07:00:00",
                  28800, 5400, 12600, 7200, 3600, 75 + i, 45.0, "2026-01-01T00:00:00"))

    return tmp_health_db


# ── Tests: /api/query ─────────────────────────────────────────────────────────

def test_query_returns_no_data_when_empty(health_client):
    """Empty DB → {"status": "no_data"}."""
    resp = health_client.get("/api/query")
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_data"


def test_query_returns_snapshot_when_seeded(seeded_health_db, health_client):
    """Seeded DB → status ok, non-empty daily_summary and sleep."""
    resp = health_client.get("/api/query")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert len(data["daily_summary"]) > 0
    assert len(data["sleep"]) > 0


def test_query_snapshot_includes_expected_fields(seeded_health_db, health_client):
    """Snapshot must include all fields the LLM context depends on."""
    resp = health_client.get("/api/query")
    data = resp.json()
    assert "daily_summary" in data
    assert "sleep" in data
    assert "hrv" in data
    assert "activities" in data
    assert "as_of" in data


def test_query_sleep_enriched_with_hours(seeded_health_db, health_client):
    """Sleep rows should have duration_hours computed."""
    resp = health_client.get("/api/query")
    sleep = resp.json()["sleep"]
    assert len(sleep) > 0
    assert "duration_hours" in sleep[0]
    assert sleep[0]["duration_hours"] == pytest.approx(8.0, abs=0.1)


# ── Tests: /api/import/csv ────────────────────────────────────────────────────

def _make_garmin_csv(rows: list[dict]) -> bytes:
    """Build a Garmin-style activities CSV in memory."""
    fieldnames = [
        "Activity ID", "Activity Type", "Date", "Title",
        "Distance", "Calories", "Time", "Avg HR", "Max HR",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def test_import_csv_inserts_activities(health_client, use_health_service):
    """Valid CSV rows should be inserted into the activities table."""
    import db as db_mod

    csv_data = _make_garmin_csv([
        {
            "Activity ID": "12345",
            "Activity Type": "Running",
            "Date": "2026-01-15 07:30:00",
            "Title": "Morning Run",
            "Distance": "5.2",
            "Calories": "420",
            "Time": "00:28:15",
            "Avg HR": "155",
            "Max HR": "172",
        },
        {
            "Activity ID": "12346",
            "Activity Type": "Cycling",
            "Date": "2026-01-16 08:00:00",
            "Title": "Ride",
            "Distance": "30.0",
            "Calories": "600",
            "Time": "01:15:00",
            "Avg HR": "140",
            "Max HR": "165",
        },
    ])

    resp = health_client.post(
        "/api/import/csv",
        files={"file": ("activities.csv", csv_data, "text/csv")},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert result["inserted"] == 2
    assert result["skipped"] == 0

    import db as db_mod  # ensure we have the health db module
    activities = db_mod.get_activities(limit=10)
    assert len(activities) == 2
    names = {a["name"] for a in activities}
    assert "Morning Run" in names


def test_import_csv_skips_rows_without_date(health_client):
    """Rows with no parseable date should be counted as skipped, not crash."""
    csv_data = _make_garmin_csv([
        {
            "Activity ID": "99999",
            "Activity Type": "Running",
            "Date": "",   # no date
            "Title": "No Date Run",
            "Distance": "5.0",
            "Calories": "400",
            "Time": "00:25:00",
            "Avg HR": "150",
            "Max HR": "170",
        },
    ])

    resp = health_client.post(
        "/api/import/csv",
        files={"file": ("activities.csv", csv_data, "text/csv")},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert result["inserted"] == 0
    assert result["skipped"] == 1


def test_import_csv_rejects_non_csv(health_client):
    """Uploading a non-CSV file should return 422."""
    resp = health_client.post(
        "/api/import/csv",
        files={"file": ("document.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert resp.status_code == 422


def test_import_csv_duration_parsing(health_client):
    """HH:MM:SS duration strings should convert correctly to seconds."""
    import health_service.main as main_mod
    assert main_mod._parse_duration("01:30:00") == 5400
    assert main_mod._parse_duration("00:28:15") == 1695
    assert main_mod._parse_duration("05:00") == 300
    assert main_mod._parse_duration("") is None
