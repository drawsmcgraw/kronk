"""Tests for tool_service /search error paths (2026-07-05 review P1.6:
every failure mode collapsed to 'Search service unavailable' or a generic
500 — no status, no body, nothing in the logs)."""
from unittest.mock import patch

from fastapi.testclient import TestClient

import tool_service.main as ts
from tests.test_tool_service_weather import FakeAsyncClient, FakeResponse


def _search(routes_or_client):
    client = (routes_or_client if callable(routes_or_client)
              else FakeAsyncClient(routes_or_client))
    with patch.object(ts.httpx, "AsyncClient", client):
        # No `with` block → no lifespan → weather refresh loop stays off.
        return TestClient(ts.app).get("/search", params={"q": "test query"})


def test_search_searxng_500_returns_502_with_status_and_body():
    resp = _search({"/search": FakeResponse(500, text="<html>upstream exploded</html>")})
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "SearXNG returned HTTP 500" in detail
    assert "upstream exploded" in detail


def test_search_searxng_non_json_200_returns_502():
    resp = _search({"/search": FakeResponse(200, text="<html>a proxy login page</html>")})
    assert resp.status_code == 502
    assert "non-JSON response" in resp.json()["detail"]


def test_search_network_failure_returns_502_with_cause():
    import httpx as real_httpx

    class ExplodingClient:
        def __call__(self, *a, **kw):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kw):
            raise real_httpx.ConnectError("connection refused")

    resp = _search(ExplodingClient())
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "Could not reach SearXNG" in detail
    assert "ConnectError" in detail
