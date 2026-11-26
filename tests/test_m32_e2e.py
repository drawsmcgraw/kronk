"""M32 step 1, found by the end-to-end test (docs/sprint/M32-e2e.md, S1).

kronk-work-export.service carries systemd's default start limit: 5 starts in
10 s. systemd/kronk-work-export.path starts it on every work-state write, and
the timer starts it every minute. The sixth start inside ten seconds is
refused ("Start request repeated too quickly", result start-limit-hit); the
path unit then fails with unit-start-limit-hit and stays failed until someone
restarts it, so every later write waits for the timer again and nothing says
so. Measured on scratch copies of both units (systemd 257): five single
writes four seconds apart and then ten writes in 0.56 s left the path unit
failed; with StartLimitIntervalSec=0 on the scratch service, ten such bursts
each exported their last write.

This test reads the reference unit files; it starts nothing.
"""
import re
from pathlib import Path

import pytest

SYSTEMD = Path(__file__).resolve().parent.parent / "systemd"


def _section(text: str, name: str) -> str:
    m = re.search(rf"^\[{name}\]\n(.*?)(?=^\[|\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


@pytest.mark.xfail(strict=True, reason="M32-e2e S1: the export service keeps systemd's default start limit, so a "
                                       "burst of work-state writes leaves the path unit failed for good. If the "
                                       "cure is another one (a debounce, a limit on the path unit), change this "
                                       "test to state it.")
def test_a_burst_of_writes_cannot_leave_the_path_unit_failed():
    unit = _section((SYSTEMD / "kronk-work-export.service").read_text(), "Unit")
    assert re.search(r"^StartLimitIntervalSec=0\s*$", unit, re.M), \
        "kronk-work-export.service has the default start limit (5 starts in 10 s)"
