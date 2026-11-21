"""scripts/satellite_area_check.py: its template, rendered over a fake
registry, and its verdicts (sprint M22, criterion 2). Nothing here reaches
HA; the script itself is run by hand against the real one."""
import importlib.util
import json
from pathlib import Path

from tests import ha_template_env as hte
from tests.test_blueprint_handoff import _registry

_SPEC = importlib.util.spec_from_file_location(
    "satellite_area_check", Path(__file__).resolve().parent.parent / "scripts" / "satellite_area_check.py")
sac = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sac)


def _rows(reg):
    out = hte.environment(reg).from_string(sac.SATELLITE_TEMPLATE).render()
    return {r["satellite"]: r for r in sac.check(json.loads(out))}


def test_the_basement_before_fix_1_is_a_mismatch():
    rows = _rows(_registry(None))
    b = rows["assist_satellite.basement_pe"]
    assert b["status"] == "mismatch" and b["area"] == "Basement Theater"
    assert b["players"] == [{"entity_id": "media_player.home_assistant_voice_0ab117",
                             "name": "basement voice pe ma", "area": None}]
    assert rows["assist_satellite.kitchen_pe"]["status"] == "ok"
    assert rows["assist_satellite.office_sat1"]["status"] == "ok"


def test_after_fix_1_the_basement_is_ok():
    assert _rows(_registry("basement_theater"))["assist_satellite.basement_pe"]["status"] == "ok"


def test_a_satellite_without_its_own_player_is_listed_as_such():
    rows = _rows(_registry(None))
    assert rows["assist_satellite.office_sat2"]["status"] == "no_player"
    assert rows["assist_satellite.garage_sat"]["status"] == "no_player"


def test_a_player_in_another_room_is_a_mismatch():
    reg = _registry("kitchen")
    assert _rows(reg)["assist_satellite.basement_pe"]["status"] == "mismatch"


def test_a_satellite_in_no_area():
    reg = _registry("basement_theater")
    reg.devices["basement_pe-esp"].area_id = None
    reg.devices["basement_pe-ma"].area_id = None
    assert _rows(reg)["assist_satellite.basement_pe"]["status"] == "no_area"


def test_the_script_only_renders_a_template():
    src = (Path(__file__).resolve().parent.parent / "scripts" / "satellite_area_check.py").read_text()
    assert src.count("urllib.request.Request(") == 1 and '/api/template"' in src
    assert "print(token" not in src and "HA_TOKEN=" not in sac.SATELLITE_TEMPLATE
