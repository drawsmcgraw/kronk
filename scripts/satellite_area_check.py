#!/usr/bin/env python3
"""List every voice satellite whose two HA devices are not in one area.

    python3 scripts/satellite_area_check.py [--json]

A satellite is two Home Assistant devices: the ESPHome device that hears
(its `assist_satellite` entity) and the Music Assistant device that plays.
The join is the MAC: the MA device's identifier is 'up' + the MAC without
colons, the same join the Kronk music blueprint (fork change 1) and
tool_service's players template use. Both tiers expect the two to share an
area ("Adding a satellite", docs/features/voice-music-control.md); the
basement pair did not, and every unnamed basement hand-off asked Kronk for
a room with no player (docs/incidents/INVESTIGATION_2026-11-19_basement_aerosmith.md).

Reads only: one POST to HA's /api/template (it renders a template and
changes nothing), over HA_URL (default http://localhost:8123) with HA_TOKEN
from the environment or .env. The token is never printed. The template is
SATELLITE_TEMPLATE below; tests/test_satellite_area_check.py renders it over
a fake registry.

Per satellite: its area, its MA player(s) and their area. Status:
  ok          both in the same area
  mismatch    the MA player is in another area, or in none
  no_area     the ESPHome device is in no area
  no_player   no MA player carries this satellite's MAC
Exit 0 when every satellite is ok, 1 when any is not, 2 when HA could not
be read.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Area is read per entity (area_name(entity)), as both tiers read it: an
# entity without its own area is in its device's area.
SATELLITE_TEMPLATE = (
    "{% set ma = integration_entities('music_assistant') | select('match', 'media_player\\\\.') | list %}"
    "{% set ns = namespace(out=[]) %}"
    "{% for s in states.assist_satellite %}"
    "{% set d = device_id(s.entity_id) %}"
    "{% set conns = (device_attr(d, 'connections') | list) if d else [] %}"
    "{% set mac = conns | selectattr(0, 'eq', 'mac') | map(attribute=1) | first | default('') %}"
    "{% set key = ('up' ~ (mac | replace(':', ''))) if mac else '' %}"
    "{% set pl = namespace(out=[]) %}"
    "{% for e in ma if key %}"
    "{% set md = device_id(e) %}"
    "{% set ids = (device_attr(md, 'identifiers') | list) if md else [] %}"
    "{% if ids | selectattr(1, 'eq', key) | list %}"
    "{% set pl.out = pl.out + [{'entity_id': e, 'name': state_attr(e, 'friendly_name'), 'area': area_name(e)}] %}"
    "{% endif %}"
    "{% endfor %}"
    "{% set ns.out = ns.out + [{'satellite': s.entity_id, "
    "'device': (device_attr(d, 'name_by_user') or device_attr(d, 'name')) if d else none, "
    "'area': area_name(d) if d else none, 'mac': mac, 'players': pl.out}] %}"
    "{% endfor %}"
    "{{ ns.out | to_json }}"
)


def classify(sat: dict) -> str:
    if not sat.get("area"):
        return "no_area"
    players = sat.get("players") or []
    if not players:
        return "no_player"
    return "ok" if all(p.get("area") == sat["area"] for p in players) else "mismatch"


def check(rows: list[dict]) -> list[dict]:
    """Each satellite with its status, sorted by satellite entity id."""
    return [{**r, "status": classify(r)} for r in sorted(rows, key=lambda r: r.get("satellite") or "")]


def _token() -> str | None:
    if os.environ.get("HA_TOKEN"):
        return os.environ["HA_TOKEN"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            m = re.match(r"^HA_TOKEN=(.*)$", line)
            if m:
                return m.group(1).strip().strip("'\"")
    return None


def read_satellites(ha_url: str, token: str) -> list[dict]:
    body = json.dumps({"template": SATELLITE_TEMPLATE}).encode()
    req = urllib.request.Request(f"{ha_url}/api/template", data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode())
    if not isinstance(data, list):
        raise ValueError("the satellite template did not answer a list")
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="print the rows as JSON")
    args = ap.parse_args()
    token = _token()
    if not token:
        print("satellite_area_check: HA_TOKEN is not set (environment or .env)", file=sys.stderr)
        return 2
    ha_url = os.environ.get("HA_URL", "http://localhost:8123").rstrip("/")
    try:
        rows = check(read_satellites(ha_url, token))
    except (urllib.error.URLError, ValueError, json.JSONDecodeError) as e:
        print(f"satellite_area_check: could not read HA at {ha_url}: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
    else:
        for r in rows:
            players = "; ".join(f"{p['name']} ({p['entity_id']}) area={p['area'] or '-'}"
                                for p in r["players"]) or "-"
            print(f"{r['status']:<10} {r['satellite']}  device={r['device']!r} area={r['area'] or '-'}  "
                  f"MA player: {players}")
        bad = [r for r in rows if r["status"] != "ok"]
        print(f"{len(rows)} satellites, {len(bad)} not ok")
    return 0 if all(r["status"] == "ok" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
