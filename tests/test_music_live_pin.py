"""Pin of today's live /music behaviour for every play_music corpus row
(sprint M11, criterion 22).

M11 builds a second music path beside the live one and shares the resolver
with it. The live path must not change: not the reading, not the provider
it picks, not the literal-play fallback. This test runs each corpus row
through POST /music with Music Assistant replaced by the recorded snapshot
(tests/music_snapshot.py), MUSIC_DRY_RUN on and every HA service call
refused, and compares the answer and the exact Music Assistant reads with
tests/fixtures/music_live_pin.json, written from the code before M11's
first change.

The model's arguments are a deterministic stand-in built from the row
(music_snapshot.live_args): the pin is about what tool_service does with a
request, not about what the model writes.

Regenerate only on purpose (a deliberate change to the live path):
    KRONK_WRITE_MUSIC_PIN=1 ./scripts/run_tests.sh tests/test_music_live_pin.py
"""
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import tool_service.main as ts
from tests import music_snapshot as snapmod

pytestmark = pytest.mark.usefixtures("no_network")

PIN = Path(__file__).resolve().parent / "fixtures" / "music_live_pin.json"
SNAP = snapmod.load()
ROWS = [r for r in snapmod.corpus_rows() if snapmod.live_args(r, SNAP) is not None]
PINNED = json.loads(PIN.read_text()) if PIN.exists() else {}


def run_live(row: dict, tmp: Path) -> dict:
    body = snapmod.live_args(row, SNAP)
    with snapmod.offline_env(SNAP, tmp) as replay:
        r = TestClient(ts.app).post("/music", json=body)
    out = {"request": body, "status": r.status_code, "answer": r.json(),
           "ma_reads": [[c, a] for c, a in replay.calls]}
    if replay.missing:
        out["missing_from_snapshot"] = replay.missing
    return out


@pytest.mark.skipif(not os.environ.get("KRONK_WRITE_MUSIC_PIN"), reason="writes the pin; set KRONK_WRITE_MUSIC_PIN=1")
def test_write_pin(tmp_path):
    PIN.write_text(json.dumps({r["id"]: run_live(r, tmp_path / r["id"]) for r in ROWS},
                              indent=1, ensure_ascii=False) + "\n")


def test_every_play_row_is_pinned():
    assert PINNED, f"{PIN} missing: write it with KRONK_WRITE_MUSIC_PIN=1"
    assert sorted(PINNED) == sorted(r["id"] for r in ROWS)


def test_pin_holds_no_snapshot_misses():
    """Every pinned row ran on recorded reads only."""
    assert not [rid for rid, v in PINNED.items() if v.get("missing_from_snapshot")]


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_live_music_path_unchanged(row, tmp_path):
    got = run_live(row, tmp_path)
    want = PINNED.get(row["id"])
    assert want is not None, f"{row['id']} is not pinned"
    assert got == want, f"live /music changed for {row['id']}: {row['text']!r}"


class _EmptyMA:
    """Music Assistant answering every read with nothing, or failing."""
    def __init__(self, fail: bool):
        self.fail, self.calls, self.missing = fail, [], []

    async def __call__(self, command, _timeout=20, **args):
        self.calls.append((command, args))
        if self.fail:
            raise ts.MAError("Music Assistant is unreachable (OSError).")
        return [] if command.endswith("library_items") else ({} if command == "music/search" else None)


@pytest.mark.parametrize("fail", [False, True], ids=["no-candidates", "ma-unreachable"])
def test_literal_fallback_unchanged(fail, tmp_path):
    """The live path still passes the words to Music Assistant when search
    finds no candidate or MA cannot be searched (main.py, the literal-play
    fallback). M11 removes it only from the new path."""
    with snapmod.offline_env(SNAP, tmp_path, ma=_EmptyMA(fail)):
        r = TestClient(ts.app).post("/music", json={"query": "Zorblax Fnordwave", "media_type": "album"})
    assert r.status_code == 200, r.text
    assert r.json()["would_play"] == {"media_id": "Zorblax Fnordwave", "media_type": "album"}
    assert r.json()["detail"] == ("Dry run: would play the album Zorblax Fnordwave on the Office speaker. "
                                  "Nothing was sent to the speaker.")
