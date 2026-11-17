#!/usr/bin/env python3
"""Record the Music Assistant reads the offline music tests replay (sprint M11).

    tests/.venv/bin/python scripts/music_snapshot_record.py [--path live|new|both] [--id ROW ...]

Runs every play_music row of tests/voice_corpus.jsonl through tool_service's
music code with MUSIC_DRY_RUN on and Home Assistant's service calls refused,
against the real Music Assistant, and stores each read it makes in
tests/fixtures/music_snapshot.json (tests/music_snapshot.py describes the
format). Reads only: the Music Assistant client used here refuses any
command outside music_snapshot.READ_COMMANDS (search, name lookup, library
listings), and HA is asked for nothing but the players template. Rows run
one at a time with a pause between them.

Merging: a read already in the snapshot is kept as recorded and not asked
again, so re-running for new rows never changes what older tests saw.
`--refresh` re-asks everything (and changes the pinned results).

--path live  the live /music path, with stand-in model arguments
             (music_snapshot.live_args); what tests/test_music_live_pin.py pins
--path new   the M11 decision order (POST /music/say's decision, no play)

Needs MA_TOKEN and HA_TOKEN (environment or .env). MA at MA_URL (default
http://localhost:8095), HA at HA_URL (default http://localhost:8123).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "orchestrator"))


def _env_from_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        m = re.match(r"^(HA_TOKEN|MA_TOKEN)=(.*)$", line)
        if m and not os.environ.get(m.group(1)):
            os.environ[m.group(1)] = m.group(2).strip().strip("'\"")


_env_from_dotenv()
os.environ.setdefault("MA_URL", "http://localhost:8095")
os.environ.setdefault("KRONK_CONFIG_DIR", str(ROOT / "config"))
_TMP = Path(tempfile.mkdtemp(prefix="kronk-snapshot-"))
os.environ["MUSIC_TASTE_DB"] = str(_TMP / "taste.db")        # never the household's
os.environ["MUSIC_TASTE_FILE"] = str(_TMP / "taste.yaml")

from tests import music_snapshot as snapmod  # noqa: E402
import tool_service.main as ts  # noqa: E402

HA_URL = os.environ.get("HA_URL", "http://localhost:8123")
ROW_PAUSE_S = 0.5


def read_players() -> list[dict]:
    """The players template, read-only, exactly as /music asks it."""
    body = json.dumps({"template": ts._players_template(None)}).encode()
    req = urllib.request.Request(f"{HA_URL}/api/template", data=body, method="POST", headers={
        "Authorization": f"Bearer {os.environ['HA_TOKEN']}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        players, _ = ts.parse_player_payload(r.read().decode())
    return players


class Recorder:
    """ma_command that asks the real MA for reads only and records them."""

    def __init__(self, snap: dict, real, refresh: bool):
        self.snap, self.real, self.refresh = snap, real, refresh
        self.asked = self.reused = 0

    async def __call__(self, command: str, _timeout: float = 20, **args):
        if command not in snapmod.READ_COMMANDS:
            raise snapmod.WriteRefused(f"recorder refuses {command}")
        k = snapmod.key(command, args)
        if k not in self.snap["ma"] or self.refresh:
            self.asked += 1
            try:
                self.snap["ma"][k] = snapmod.trim(await self.real(command, _timeout=_timeout, **args))
            except ts.MAError as e:
                self.snap["ma"][k] = {"__error__": str(e)}
        else:
            self.reused += 1
        v = self.snap["ma"][k]
        if isinstance(v, dict) and set(v) == {"__error__"}:
            raise ts.MAError(v["__error__"])
        return json.loads(json.dumps(v))


async def run_live(row: dict, snap: dict) -> None:
    body = snapmod.live_args(row, snap)
    if body is None:
        return
    try:
        await ts.play_music(ts.MusicRequest(**body))
    except ts.HTTPException:
        pass


async def run_new(row: dict, snap: dict) -> None:
    # Every branch the model question can send it down, so each one's reads
    # are recorded: no answer, "style", "named". No model is called.
    for answer in (None, "style", "named"):
        try:
            await snapmod.decide_row(row, snap, model_answer=answer)
        except ts.HTTPException:
            pass


async def main_async(args) -> int:
    snap = snapmod.load() if snapmod.FIXTURE.exists() else {"meta": {}, "players": [], "genres": [], "taste": "", "ma": {}}
    if args.refresh or not snap["players"]:
        snap["players"] = read_players()
    if args.refresh or not snap["genres"]:
        snap["genres"] = snapmod.genres_from_db(ROOT / "data" / "music_taste.db")
    if args.refresh or not snap["taste"]:
        snap["taste"] = (ROOT / "data" / "music_taste.yaml").read_text()
    rows = snapmod.corpus_rows()            # live: play_music rows only (run_live skips the rest)
    if args.id:
        rows = [r for r in rows if r["id"] in set(args.id)]
    rec = Recorder(snap, ts.ma_command, args.refresh)
    paths = ["live", "new"] if args.path == "both" else [args.path]
    t0 = time.monotonic()
    with snapmod.offline_env(snap, _TMP / "env", ma=rec):
        for i, row in enumerate(rows):
            for p in paths:
                await (run_live if p == "live" else run_new)(row, snap)
            print(f"  {i + 1}/{len(rows)} {row['id']}: asked {rec.asked}, reused {rec.reused}", flush=True)
            await asyncio.sleep(ROW_PAUSE_S)
    snap["meta"] = {**snap.get("meta", {}), "recorded": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "rows": len(rows), "paths": sorted(set(snap.get("meta", {}).get("paths", [])) | set(paths))}
    snap["ma"] = dict(sorted(snap["ma"].items()))
    snapmod.FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    snapmod.FIXTURE.write_text(json.dumps(snap, indent=0, ensure_ascii=False, sort_keys=False) + "\n")
    print(f"music_snapshot_record: {len(rows)} rows, {rec.asked} reads asked, {rec.reused} reused, "
          f"{len(snap['ma'])} in snapshot, {time.monotonic() - t0:.0f} s; wrote {snapmod.FIXTURE}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--path", choices=["live", "new", "both"], default="live")
    ap.add_argument("--id", action="append", help="only this corpus row (repeatable)")
    ap.add_argument("--refresh", action="store_true", help="re-ask every read (changes pinned results)")
    try:
        return asyncio.run(main_async(ap.parse_args()))
    finally:
        import shutil
        shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
