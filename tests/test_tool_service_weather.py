"""Tests for tool_service's weather cache (hourly refresh + staleness)."""
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import tool_service.main as ts


@pytest.fixture
def client(tmp_path):
    # Don't let the lifespan refresh loop hit the real NWS API.
    async def fake_fetch(location):
        return {"location": location, "current": "72°F", "summary": "sunny", "alerts": []}

    with patch.object(ts, "_fetch_weather", new=fake_fetch), \
         patch.object(ts, "WEATHER_CACHE_FILE", tmp_path / "weather_cache.json"), \
         patch.object(ts, "_weather_cache", {}):
        with TestClient(ts.app) as c:
            yield c


def test_cached_endpoint_404_before_population():
    # TestClient without a `with` block runs no lifespan → cache stays empty.
    ts._weather_cache.clear()
    resp = TestClient(ts.app).get("/weather/cached")
    assert resp.status_code == 404


def test_cached_endpoint_serves_after_refresh(client):
    resp = client.get("/weather/cached")
    assert resp.status_code == 200
    data = resp.json()
    assert data["location"] == ts.HOME_LOCATION
    assert "age_s" in data and data["age_s"] >= 0
    assert data["summary"] == "sunny"


def test_cache_age_reflects_fetch_time(client):
    # Backdate the cache and confirm age_s reflects it.
    ts._weather_cache["fetched_at"] = time.time() - 5000
    resp = client.get("/weather/cached")
    assert resp.json()["age_s"] >= 4999


def test_cache_persists_to_disk(client, tmp_path):
    # The lifespan refresh should have written the persisted copy.
    files = list(tmp_path.glob("weather_cache.json"))
    assert files and files[0].read_text().strip().startswith("{")
