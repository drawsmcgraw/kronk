"""Work item M30, step A: production Whisper starts offline. Reads the
reference units in systemd/ as text; nothing is installed or run."""
import re
import shlex

import pytest

from tests.m18_helpers import ROOT

pytestmark = pytest.mark.usefixtures("no_network")

UNITS = ROOT / "systemd"
PROD = "wyoming-whisper.service"
DEV = "wyoming-whisper-dev.service"
REVISION = "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"


def _lines(name):
    """The unit's directives, comments dropped, continuation lines joined."""
    out, buf = [], ""
    for ln in (UNITS / name).read_text().splitlines():
        if ln.lstrip().startswith("#"):
            continue
        if ln.endswith("\\"):
            buf += ln[:-1] + " "
            continue
        out.append((buf + ln).strip())
        buf = ""
    return [ln for ln in out if ln]


def _environment(name):
    return [ln.split("=", 1)[1] for ln in _lines(name) if ln.startswith("Environment=")]


def _exec_args(name):
    (start,) = [ln.split("=", 1)[1] for ln in _lines(name) if ln.startswith("ExecStart=")]
    return shlex.split(start)


def _opt(args, flag):
    return args[args.index(flag) + 1]


@pytest.mark.parametrize("name", [PROD, DEV])
def test_both_units_start_offline(name):
    env = _environment(name)
    assert "HF_HUB_OFFLINE=1" in env
    assert "HF_HUB_DISABLE_TELEMETRY=1" in env


def test_production_comment_records_why_the_pin_and_the_upgrade_risk():
    text = (UNITS / PROD).read_text()
    comment = " ".join(ln.lstrip("# ").strip() for ln in text.splitlines() if ln.lstrip().startswith("#"))
    assert "--local-files-only" in comment
    assert REVISION in comment
    assert "mobiuslabsgmbh/faster-whisper-large-v3-turbo" in comment
    assert "by-hand upgrade" in comment


@pytest.mark.parametrize("flag", ["--model", "--data-dir", "--download-dir", "--device", "--compute-type"])
def test_production_and_dev_load_the_same_model_from_the_same_place(flag):
    assert _opt(_exec_args(PROD), flag) == _opt(_exec_args(DEV), flag)


def test_production_keeps_its_port_and_no_start_limit():
    # Tenet 9: the two Environment= lines and their comment, nothing else.
    args = _exec_args(PROD)
    assert _opt(args, "--uri") == "tcp://0.0.0.0:10300"
    assert not any(re.match(r"StartLimit", ln) for ln in _lines(PROD))
    assert "--initial-prompt" not in args
    assert not any(ln.startswith("EnvironmentFile=") for ln in _lines(PROD))
