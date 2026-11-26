"""Work item M35: the date and time Kronk is told are the house's.

Criterion 1: kronk_facts() states the instant in the house's zone
(KRONK_TZ, default America/New_York), EST or EDT, the right hour either
side of both daylight changes. Criterion 2: a KRONK_TZ that does not load
falls back to the default with one warning and never raises; if the
default does not load either, the line says UTC. Point 2 of the review:
the forecast's hourly labels are in the offset each period carries.

Every instant is passed as UTC through agents._utc_now, the one seam. No
test reads or sets the process TZ: the result must not depend on the zone
of the machine the suite runs on.
"""
import logging
import os
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

import pytest
from fastapi.testclient import TestClient

import agents
import tool_service.main as ts

from tests.test_tool_service_weather import (
    GEO_OK, POINTS_OK, FakeAsyncClient, FakeResponse,
)


def _utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fresh_warnings():
    agents._tz_warned.clear()
    yield
    agents._tz_warned.clear()


def _facts(instant, kronk_tz=None):
    """kronk_facts() at a UTC instant; KRONK_TZ unset when kronk_tz is None.
    patch.dict puts the environment back as it was."""
    with patch.dict(os.environ, {"LOCATION": "Laurel, MD"}), \
         patch.object(agents, "_utc_now", lambda: instant):
        os.environ.pop("KRONK_TZ", None)
        if kronk_tz is not None:
            os.environ["KRONK_TZ"] = kronk_tz
        return agents.kronk_facts()


def _line(facts):
    return facts.splitlines()[1]


# ── criterion 1 ───────────────────────────────────────────────────────────────

def test_the_reported_evening_is_wednesday_in_est():
    """The incident: 03:20 UTC on the 26th is 10:20 PM on Wednesday the 25th."""
    assert _line(_facts(_utc(2026, 11, 26, 3, 20))) == \
        "- Today is Wednesday, November 25, 2026; the local time is 10:20 PM EST."


def test_a_summer_instant_is_edt():
    assert _line(_facts(_utc(2026, 7, 4, 16, 30))) == \
        "- Today is Saturday, July 4, 2026; the local time is 12:30 PM EDT."


@pytest.mark.parametrize("instant, line", [
    # Spring forward, 2026-03-08: 1:59 AM EST, then 3:00 AM EDT.
    (_utc(2026, 3, 8, 6, 59),
     "- Today is Sunday, March 8, 2026; the local time is 1:59 AM EST."),
    (_utc(2026, 3, 8, 7, 0),
     "- Today is Sunday, March 8, 2026; the local time is 3:00 AM EDT."),
    # Fall back, 2026-11-01: 1:59 AM EDT, then 1:00 AM EST.
    (_utc(2026, 11, 1, 5, 59),
     "- Today is Sunday, November 1, 2026; the local time is 1:59 AM EDT."),
    (_utc(2026, 11, 1, 6, 0),
     "- Today is Sunday, November 1, 2026; the local time is 1:00 AM EST."),
])
def test_either_side_of_the_daylight_changes(instant, line):
    assert _line(_facts(instant)) == line


def test_kronk_tz_names_another_zone():
    assert _line(_facts(_utc(2026, 11, 26, 3, 20), "America/Denver")) == \
        "- Today is Wednesday, November 25, 2026; the local time is 8:20 PM MST."


def test_the_seam_is_an_aware_utc_instant():
    now = agents._utc_now()
    assert now.tzinfo is timezone.utc


def test_the_rest_of_the_facts_are_unchanged():
    facts = _facts(_utc(2026, 11, 26, 3, 20))
    assert facts.startswith("[Kronk ambient facts — assume these unless the user says otherwise]\n")
    assert "- Home location: Laurel, MD\n" in facts
    assert facts.endswith("against today's date above — never guess the date from memory.")


# ── criterion 2 ───────────────────────────────────────────────────────────────

EST_LINE = "- Today is Wednesday, November 25, 2026; the local time is 10:20 PM EST."


@pytest.mark.parametrize("bad", ["Nope/Zone", "../etc/passwd", "/etc/localtime", "EST5EDT/../x"])
def test_a_bad_kronk_tz_falls_back_with_one_warning(bad, caplog):
    caplog.set_level(logging.WARNING, logger="agents")
    for _ in range(3):
        assert _line(_facts(_utc(2026, 11, 26, 3, 20), bad)) == EST_LINE
    warnings = [r for r in caplog.records if r.name == "agents"]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert warnings[0].levelno == logging.WARNING
    assert repr(bad) in warnings[0].getMessage()
    assert "America/New_York" in warnings[0].getMessage()


def test_an_empty_kronk_tz_is_the_default_without_a_warning(caplog):
    caplog.set_level(logging.WARNING, logger="agents")
    assert _line(_facts(_utc(2026, 11, 26, 3, 20), "")) == EST_LINE
    assert [r for r in caplog.records if r.name == "agents"] == []


def test_without_a_zone_database_the_line_says_utc(caplog):
    """The default zone does not load (an image without /usr/share/zoneinfo):
    the line goes out in UTC, says UTC, and the error is logged once."""
    def no_zones(name):
        raise ZoneInfoNotFoundError(f"No time zone found with key {name}")

    caplog.set_level(logging.WARNING, logger="agents")
    with patch.object(agents, "ZoneInfo", no_zones):
        for _ in range(2):
            assert _line(_facts(_utc(2026, 11, 26, 3, 20))) == \
                "- Today is Thursday, November 26, 2026; the local time is 3:20 AM UTC."
    errors = [r for r in caplog.records if r.name == "agents"]
    assert len(errors) == 1
    assert errors[0].levelno == logging.ERROR
    assert "UTC" in errors[0].getMessage()


def test_a_bad_kronk_tz_and_no_zone_database(caplog):
    def no_zones(name):
        raise ZoneInfoNotFoundError(f"No time zone found with key {name}")

    caplog.set_level(logging.WARNING, logger="agents")
    with patch.object(agents, "ZoneInfo", no_zones):
        assert _line(_facts(_utc(2026, 11, 26, 3, 20), "Nope/Zone")).endswith("3:20 AM UTC.")
    levels = sorted(r.levelno for r in caplog.records if r.name == "agents")
    assert levels == [logging.WARNING, logging.ERROR]


# ── the forecast's hourly labels (review point 2) ─────────────────────────────

def _hourly(*periods):
    return FakeResponse(200, {"properties": {"periods": [
        {"startTime": start, "temperature": temp, "shortForecast": "Clear"}
        for start, temp in periods
    ]}})


def test_hourly_labels_keep_each_periods_own_offset():
    """Each period is labelled with the hour written on it, in its own offset:
    not converted to the container's zone (UTC), which put the house's 10 PM
    at "3 AM"."""
    routes = {
        "geocoding-api": GEO_OK,
        "api.weather.gov/points": POINTS_OK,
        "forecast/hourly": _hourly(("2026-11-25T22:00:00-05:00", 41),
                                   ("2026-11-25T20:00:00-07:00", 33)),
        "62,61/forecast": FakeResponse(200, {"properties": {"periods": []}}),
        "alerts": FakeResponse(200, {"features": []}),
    }
    with patch.object(ts.httpx, "AsyncClient", FakeAsyncClient(routes)):
        resp = TestClient(ts.app).get("/weather", params={"location": "Denver"})
    assert resp.status_code == 200
    summary = resp.json()["summary"]
    assert "10 PM: 41°F, Clear" in summary
    assert "8 PM: 33°F, Clear" in summary
    assert "3 AM" not in summary


@pytest.mark.parametrize("start, label", [
    ("2026-11-25T22:00:00-05:00", "10 PM"),
    ("2026-07-04T09:00:00-04:00", "9 AM"),
    ("2026-11-25T20:00:00-07:00", "8 PM"),
    ("not a time", "not a time"),
    ("", ""),
])
def test_hour_label(start, label):
    assert ts._hour_label(start) == label


# ── wiring (ruling: production compose, .env.dev.example, /settings) ─────────

def test_the_zone_is_wired_where_the_ruling_says():
    import re
    from pathlib import Path

    import settings_view

    repo = Path(__file__).resolve().parent.parent
    rows = {name: default for name, default, _ in settings_view.ENV}
    assert rows["KRONK_TZ"] == agents.HOUSE_TZ_DEFAULT == "America/New_York"
    compose = (repo / "docker-compose.yml").read_text()
    assert re.findall(r"^\s+- KRONK_TZ=(\S+)$", compose, re.M) == [agents.HOUSE_TZ_DEFAULT]
    example = (repo / ".env.dev.example").read_text()
    assert re.findall(r"^KRONK_TZ=(\S+)$", example, re.M) == [agents.HOUSE_TZ_DEFAULT]
