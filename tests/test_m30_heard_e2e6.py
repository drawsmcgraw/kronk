"""M30 step C, findings of the test in docs/sprint/M30-e2e6.md.

Each test states what should hold and is marked xfail (strict) while it
does not; the fix removes the mark. Nothing here opens a socket or touches
a live file: the guard's functions are called with made-up stat values, and
the route reads a copy built in tmp_path. Every sentence is invented.
"""
from unittest.mock import patch

import pytest

from tests import conftest as C
from tests import test_m30_heard as H

pytestmark = pytest.mark.usefixtures("no_network")

RECORD = "/x/home/.local/state/kronk-voice/voice_runs.db"
# Two runs of the live timer, a minute apart, as _exporter_runs gives them.
RUNS = [(999.8, 1000.3), (1059.8, 1060.3)]


def _ns(t: float) -> int:
    return int(t * 10**9)


@pytest.mark.xfail(strict=True, reason="B1 of M30-e2e6: a record deleted during the run is taken for the timer's own "
                                       "change, because its last mtime was the timer's")
def test_e2e6_the_guard_reports_the_record_deleted_during_the_run():
    """The timer writes the record on every run, so the record's mtime at
    the start of a test run always falls inside one of the timer's runs. A
    test that deletes the record must still fail the run."""
    before = {RECORD: (_ns(1000.1), 692224)}
    after = {RECORD: None}
    with patch.object(C, "_exporter_runs", return_value=RUNS):
        assert C._voice_changes_not_the_timers(before, after) == [RECORD]


@pytest.mark.xfail(strict=True, reason="B1 of M30-e2e6: a test's write of a file the timer rewrites every minute is "
                                       "hidden by the timer's next run; mtime and size alone cannot tell them apart")
def test_e2e6_the_guard_reports_a_write_the_timer_then_wrote_over(tmp_path):
    """A test writes the record between two runs of the timer (here: a
    stand-in file gains bytes at 1030); the timer's next run then writes the
    file too (1060.1). What the guard sees at the end is a file whose mtime
    is the timer's, and it passes. The size the timer left is not the size
    the timer's own writes account for; whatever the fix reads, this change
    must be reported."""
    before = {RECORD: (_ns(1000.1), 692224)}
    after = {RECORD: (_ns(1060.1), 696320)}         # a page more than before, last touched by the timer
    with patch.object(C, "_exporter_runs", return_value=RUNS):
        assert C._voice_changes_not_the_timers(before, after) == [RECORD]


@pytest.mark.xfail(strict=True, reason="S1 of M30-e2e6: OverflowError from sqlite3 and datetime is not caught: HTTP 500")
@pytest.mark.parametrize("params", [
    "?changed_after=99999999999999999999999&known_rows=1&known_built_s=1",
    "?changed_after=0&known_rows=1&known_built_s=1e18",
    "?changed_after=0&known_rows=1&known_built_s=-1e18",
])
def test_e2e6_a_number_out_of_range_is_a_400_not_a_500(files, params):
    """S1 of M30-e2e6: a parameter SQLite or datetime cannot hold is a bad
    parameter, answered 400 with the reason like every other."""
    db, _ = files
    H.write_copy(db, [H.run("R1", H.NOW - 100, 1)])
    from fastapi.testclient import TestClient
    import orchestrator.main as orch
    r = TestClient(orch.app, raise_server_exceptions=False).get(f"/api/voice_runs{params}")
    assert r.status_code == 400, r.status_code


files = H.files
