"""tool_service routes removed with a feature stay removed (negative tests)."""

from fastapi.testclient import TestClient


def test_hottub_route_is_gone():
    """Sprint M1b removed the hot tub feature: GET /hottub is a plain 404 and
    the module no longer names the monitor's status file."""
    import tool_service.main as ts
    r = TestClient(ts.app).get("/hottub")
    assert r.status_code == 404
    assert not any(name.startswith("HOTTUB") for name in vars(ts))
