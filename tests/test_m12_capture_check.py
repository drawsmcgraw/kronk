"""Sprint M12, criterion 7: the end-date check for the two-week audio
capture (scripts/voice_capture_check.py), with an injected clock, a stubbed
notifier and stubbed reads of HA. Nothing here reaches docker or HA."""
import datetime as dt
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("no_network")

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("voice_capture_check_m12", ROOT / "scripts" / "voice_capture_check.py")
C = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = C
_spec.loader.exec_module(C)

END = dt.date(2026, 12, 1)
ON_YAML = """default_config:
rest_command: !include rest_command.yaml
assist_pipeline:
  debug_recording_dir: /voice_recordings
"""
OFF_YAML = """default_config:
# Voice audio capture, switched OFF.
# assist_pipeline:
#   debug_recording_dir: /voice_recordings
rest_command: !include rest_command.yaml
"""


class Pushes:
    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def __call__(self, tag, title, body):
        self.sent.append((tag, title, body))
        return self.ok


def _run(tmp_path, today, yaml_text=ON_YAML, push=None, started=1.0, recordings=None):
    push = push or Pushes()
    rec = recordings or (tmp_path / "rec")
    action = C.run(END, today, tmp_path / "state" / "s.json", rec, push,
                   read_config=lambda: yaml_text, started_at=lambda: started)
    return action, push


def test_capture_configured_reads_only_an_uncommented_key_in_the_block():
    assert C.capture_configured(ON_YAML) is True
    assert C.capture_configured(OFF_YAML) is False
    assert C.capture_configured("assist_pipeline:\n  # debug_recording_dir: /x\n") is False
    assert C.capture_configured("other:\n  debug_recording_dir: /x\n") is False


@pytest.mark.parametrize("today", [dt.date(2026, 11, 17), dt.date(2026, 11, 30)])
def test_nothing_before_the_end_date(tmp_path, today):
    action, push = _run(tmp_path, today)
    assert action == "before_end" and push.sent == []


def test_from_the_end_date_once_a_day_while_on(tmp_path):
    action, push = _run(tmp_path, END)
    assert action == "notify" and len(push.sent) == 1
    tag, title, body = push.sent[0]
    assert tag == "kronk-voice-capture" and "turn voice audio capture off" in title
    assert "2026-12-01" in body and "voice-capture.md" in body and "restart" in body
    # same day again (a manual run, a Persistent catch-up): no second push
    action, push2 = _run(tmp_path, END)
    assert action == "already_today" and push2.sent == []
    # next day: again
    action, push3 = _run(tmp_path, END + dt.timedelta(days=1))
    assert action == "notify" and len(push3.sent) == 1


def test_nothing_once_capture_is_off(tmp_path):
    action, push = _run(tmp_path, END + dt.timedelta(days=3), yaml_text=OFF_YAML)
    assert action == "off" and push.sent == []


def test_file_changed_but_ha_not_restarted_still_counts_as_on(tmp_path):
    rec = tmp_path / "rec" / "dev" / "kronk" / "1"
    rec.mkdir(parents=True)
    (rec / "01_stt-x.wav").write_bytes(b"RIFF")
    os.utime(rec / "01_stt-x.wav", (2000.0, 2000.0))
    action, push = _run(tmp_path, END, yaml_text=OFF_YAML, started=1000.0)
    assert action == "notify" and "recorded since it last started" in push.sent[0][2]
    action, push = _run(tmp_path, END + dt.timedelta(days=1), yaml_text=OFF_YAML, started=3000.0)
    assert action == "off" and push.sent == []          # restarted after the last recording


def test_unreadable_state_is_said_not_hidden(tmp_path):
    def boom():
        raise RuntimeError("docker: permission denied")
    push = Pushes()
    action = C.run(END, END, tmp_path / "s.json", tmp_path / "nope", push,
                   read_config=boom, started_at=boom)
    assert action == "notify" and "could not tell" in push.sent[0][2]


def test_failed_push_is_retried_the_same_day(tmp_path):
    action, push = _run(tmp_path, END, push=Pushes(ok=False))
    assert action == "notify_failed"
    action, push = _run(tmp_path, END)
    assert action == "notify" and len(push.sent) == 1


def test_main_refuses_an_unset_end_date(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VOICE_CAPTURE_END", "@END_DATE@")
    push = Pushes()
    assert C.main(["--state", str(tmp_path)], today=END, notify=push) == 2
    assert push.sent == [] and "not a date" in capsys.readouterr().err


def test_main_test_push(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_CAPTURE_END", "2026-12-01")
    push = Pushes()
    assert C.main(["--test-push", "--state", str(tmp_path)], today=dt.date(2026, 11, 17), notify=push) == 0
    assert len(push.sent) == 1 and "TEST" in push.sent[0][1]


def test_main_uses_the_state_directory_from_systemd(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_CAPTURE_END", "2026-12-01")
    monkeypatch.setenv("STATE_DIRECTORY", str(tmp_path / "st"))
    monkeypatch.setattr(C, "read_live_config", lambda: ON_YAML)
    monkeypatch.setattr(C, "ha_started_at", lambda: 1.0)
    monkeypatch.setenv("VOICE_RECORDINGS_DIR", str(tmp_path / "rec"))
    push = Pushes()
    # main passes the module-level readers as defaults at definition time; run() is what main calls
    assert C.run(END, END, C.state_path(None), tmp_path / "rec", push,
                 read_config=C.read_live_config, started_at=C.ha_started_at) == "notify"
    assert json.loads((tmp_path / "st" / "voice_capture_check.json").read_text())["last_notified"] == "2026-12-01"


def test_units_carry_the_placeholder_and_the_script():
    svc = (ROOT / "systemd" / "kronk-voice-capture-check.service").read_text()
    timer = (ROOT / "systemd" / "kronk-voice-capture-check.timer").read_text()
    assert "Environment=VOICE_CAPTURE_END=@END_DATE@" in svc
    assert "scripts/voice_capture_check.py" in svc and "StateDirectory=kronk-voice-capture" in svc
    assert "OnCalendar=*-*-* 09:00" in timer and "Persistent=true" in timer
    assert "restart" not in svc.split("[Service]")[1].lower()
