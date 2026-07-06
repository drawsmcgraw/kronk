"""Tests for tool_service's weather cache (hourly refresh + staleness) and
the upstream-failure handling in _fetch_weather (2026-07-05 review P0.3:
unchecked upstream responses returned 200 with an empty forecast on NWS 500s,
or a generic 500 on geocoder hiccups)."""
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


# ── Upstream-failure handling in _fetch_weather ───────────────────────────────

class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("response body is not JSON")
        return self._json


class FakeAsyncClient:
    """Stands in for httpx.AsyncClient; routes GETs by URL substring.
    Route order matters — first match wins."""

    def __init__(self, routes: dict):
        self._routes = routes

    def __call__(self, *args, **kwargs):  # the httpx.AsyncClient(...) call
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        for fragment, resp in self._routes.items():
            if fragment in url:
                return resp
        raise AssertionError(f"unexpected URL fetched: {url}")


GEO_OK = FakeResponse(200, {"results": [
    {"latitude": 39.7392, "longitude": -104.9903, "name": "Denver"},
]})
POINTS_OK = FakeResponse(200, {"properties": {
    "relativeLocation": {"properties": {"city": "Denver", "state": "CO"}},
    "forecast": "https://api.weather.gov/gridpoints/BOU/62,61/forecast",
    "forecastHourly": "https://api.weather.gov/gridpoints/BOU/62,61/forecast/hourly",
}})


def _get_weather(routes):
    with patch.object(ts.httpx, "AsyncClient", FakeAsyncClient(routes)):
        # No `with` block → no lifespan → the cache refresh loop stays off.
        return TestClient(ts.app).get("/weather", params={"location": "Denver"})


def test_weather_geocoder_500_returns_502_with_detail():
    """An Open-Meteo error used to raise JSONDecodeError → a generic 500."""
    resp = _get_weather({
        "geocoding-api": FakeResponse(500, text="<html>Internal Server Error</html>"),
    })
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "Open-Meteo geocoding failed (HTTP 500)" in detail
    assert "Internal Server Error" in detail  # body snippet survives


def test_weather_nws_forecast_500_returns_502_not_empty_forecast():
    """An NWS forecast 500 used to parse as empty periods and return 200 with
    no forecast — the model then answered from nothing."""
    resp = _get_weather({
        "geocoding-api": GEO_OK,
        "api.weather.gov/points": POINTS_OK,
        "forecast/hourly": FakeResponse(200, {"properties": {"periods": []}}),
        "62,61/forecast": FakeResponse(500, json_data={"title": "Unexpected Problem"},
                                       text='{"title": "Unexpected Problem"}'),
        "alerts": FakeResponse(200, {"features": []}),
    })
    assert resp.status_code == 502
    assert "NWS forecast fetch failed (HTTP 500)" in resp.json()["detail"]
