"""Sprint M18, criterion 13: the reference timer units in systemd/. Read as
text; nothing is installed or run."""
import configparser
import re

import pytest

from tests.m18_helpers import ROOT

pytestmark = pytest.mark.usefixtures("no_network")

UNITS = ROOT / "systemd"


def unit(name):
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read_string("\n".join(ln for ln in (UNITS / name).read_text().splitlines() if not ln.lstrip().startswith("#")))
    return cp


@pytest.mark.parametrize("name, calendar, persistent", [
    ("kronk-upgrade-minor.timer", "*-*-01..07 03:30", "true"),
    ("kronk-upgrade-major-check.timer", "*-01,04,07,10-08 04:30", "true"),
    ("kronk-upgrade-notices.timer", "hourly", None),
])
def test_timers(name, calendar, persistent):
    t = unit(name)
    assert t["Timer"]["OnCalendar"] == calendar
    assert t["Timer"].get("Persistent") == persistent
    assert t["Install"]["WantedBy"] == "timers.target"


def test_the_monthly_and_quarterly_runs_happen_while_the_house_sleeps():
    for name in ("kronk-upgrade-minor.timer", "kronk-upgrade-major-check.timer"):
        hh = int(re.search(r" (\d\d):\d\d$", unit(name)["Timer"]["OnCalendar"]).group(1))
        assert 2 <= hh <= 5, name


@pytest.mark.parametrize("name, script", [
    ("kronk-upgrade-minor.service", "scripts/upgrade_minor.sh"),
    ("kronk-upgrade-major-check.service", "scripts/upgrade_major_check.sh"),
    ("kronk-upgrade-notices.service", "scripts/upgrades.py"),
])
def test_services_run_the_repo_scripts(name, script):
    s = unit(name)["Service"]
    assert s["Type"] == "oneshot"
    assert script in s["ExecStart"]
    assert (ROOT / script).exists()
    # The branch is not in the units (sprint M18 fix 3, H6): the rules file,
    # or the install's file outside the repo through a drop-in.
    assert not re.search(r"^Environment=KRONK_UPGRADE_BRANCH", (UNITS / name).read_text(), re.M)


def test_the_operations_block_has_install_pause_and_remove():
    text = (UNITS / "kronk-upgrade-minor.service").read_text()
    for word in ("install:", "check:", "dry run:", "pause:", "log:", "remove:"):
        assert word in text
    assert "systemctl --user enable --now kronk-upgrade-minor.timer kronk-upgrade-major-check.timer kronk-upgrade-notices.timer" in text
    assert "systemctl --user disable --now kronk-upgrade-minor.timer kronk-upgrade-major-check.timer kronk-upgrade-notices.timer" in text


def test_wrappers_are_executable_and_call_the_one_script():
    for name, sub in (("upgrade_minor.sh", "minor"), ("upgrade_major_check.sh", "major")):
        p = ROOT / "scripts" / name
        assert p.stat().st_mode & 0o111
        assert f'upgrades.py" {sub} "$@"' in p.read_text()
