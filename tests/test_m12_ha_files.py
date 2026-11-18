"""Sprint M12, criterion 4: the repo's Home Assistant configuration turns
audio capture on into a directory outside the config volume and the repo,
and the HA compose file mounts it and gives HA time to shut down cleanly.
The dev pair records text by a compose setting; production takes the
switch from .env, default off. Nothing is installed by these files."""
import re
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.usefixtures("no_network")

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("vcc_m12_files", ROOT / "scripts" / "voice_capture_check.py")
C = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = C
_spec.loader.exec_module(C)


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)   # HA's !include tags


def test_ha_configuration_turns_capture_on():
    text = (ROOT / "ha" / "configuration.yaml").read_text()
    cfg = yaml.load(text, Loader=_Loader)
    assert cfg["assist_pipeline"] == {"debug_recording_dir": "/voice_recordings"}
    assert C.capture_configured(text)
    assert "rest_command" in cfg and "default_config" in cfg          # M17's lines kept


def test_ha_compose_mount_and_grace_period():
    svc = yaml.safe_load((ROOT / "docker-compose.ha.yml").read_text())["services"]["homeassistant"]
    assert svc["stop_grace_period"] == "2m"
    # Pinned to an exact version (M12 left it at 2026.5.4). Since M18 the
    # monthly patch run may move it within the month, so the test checks
    # the shape the upgrade rules accept, not one value.
    image, _, tag = svc["image"].rpartition(":")
    assert image == "ghcr.io/home-assistant/home-assistant"
    assert re.fullmatch(r"\d{4}\.\d{1,2}\.\d+", tag), svc["image"]
    assert "ha-config:/config" in svc["volumes"]
    bind = [v for v in svc["volumes"] if isinstance(v, dict)]
    assert bind == [{"type": "bind", "source": "/home/drew/kronk-voice-recordings",
                     "target": "/voice_recordings", "bind": {"create_host_path": False}}]
    assert not Path(bind[0]["source"]).resolve().is_relative_to(ROOT)    # outside the repo


def test_dev_pair_records_text_and_production_follows_env():
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    for dev in ("orchestrator-dev", "tool_service-dev"):
        assert "TELEMETRY_RECORD_TEXT=true" in services[dev]["environment"]
    for prod in ("orchestrator", "tool_service", "voice_tracer"):
        assert "TELEMETRY_RECORD_TEXT=${TELEMETRY_RECORD_TEXT:-false}" in services[prod]["environment"]
    build = services["voice_tracer"]["build"]
    assert build["additional_contexts"] == {"orchestrator": "./orchestrator"}
