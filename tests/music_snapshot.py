"""A recorded snapshot of Music Assistant and Home Assistant reads, for
offline music tests (sprint M11).

tests/fixtures/music_snapshot.json holds what the real services answered on
the day it was recorded (scripts/music_snapshot_record.py, reads only):

  players   the Music Assistant players HA listed (the /music template)
  genres    the genre tags in the household's taste DB (no artist names)
  taste     the household's music_taste.yaml
  ma        Music Assistant read commands -> trimmed results, keyed by
            key(command, args)

Tests replay it: `Replay` stands in for tool_service.main.ma_command and
answers only what was recorded. A read the snapshot does not hold raises
MAError("not in snapshot"), which the code under test treats as Music
Assistant being unreachable, and the call is listed in `Replay.missing`, so
a test that needs it fails loudly instead of passing on a guess.

Nothing here reaches the network.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "music_snapshot.json"

# Moved to tool_service/corpus_check.py in sprint M21 step 2, so the
# /settings page's corpus check runs the same code as the test suite; the
# names stay importable from here.
from tool_service.corpus_check import (  # noqa: E402,F401
    DEFAULT_PLAYER, DEVICE_IDS, ORIGIN_KEYS, READ_COMMANDS, Replay, ScriptedModel, WriteRefused, _FirstPick,
    decide_row, key, offline_env, origin_of, players_answer, seed_taste,
)


def _trim_item(it: dict) -> dict:
    """The fields the resolver, the station lookup and the genre picks read."""
    out = {k: it[k] for k in ("uri", "name", "media_type", "provider", "item_id") if k in it}
    if it.get("provider_mappings"):
        out["provider_mappings"] = [{"provider_domain": m.get("provider_domain")}
                                    for m in it["provider_mappings"] if isinstance(m, dict)]
    if it.get("artists"):
        out["artists"] = [{"name": a.get("name")} for a in it["artists"] if isinstance(a, dict)]
    return out


def trim(result):
    if isinstance(result, list):
        return [_trim_item(x) if isinstance(x, dict) else x for x in result]
    if isinstance(result, dict):
        if "uri" in result:                       # one item (music/item_by_name)
            return _trim_item(result)
        return {k: (trim(v) if isinstance(v, list) else v) for k, v in result.items()
                if k in ("artists", "albums", "tracks", "playlists", "radio", "items", "count", "total")}
    return result


def load() -> dict:
    return json.loads(FIXTURE.read_text())


def spoken_player(row: dict, snap: dict) -> str | None:
    """The speaker or room a model would pass as play_music's `player`, from
    the row's expected target."""
    p = row["expect"].get("player") or "origin"
    if p == "origin":
        return None
    kind, _, val = p.partition(":")
    if kind == "area":
        return val.lower()
    if kind == "entity":
        return next((x["name"] for x in snap["players"] if x["entity_id"] == val), val)
    return val


def live_args(row: dict, snap: dict) -> dict | None:
    """A deterministic stand-in for the model's play_music arguments, built
    from the row's expectations, plus what the orchestrator adds (said,
    origin). None when the row names nothing to play."""
    e = row["expect"]
    if e.get("tool") != "play_music" or not e.get("name"):
        return None
    query = e["name"] + (f" by {e['artist']}" if e.get("artist") else "")
    body = {"query": query, "said": row["text"]}
    if e.get("media_type"):
        body["media_type"] = e["media_type"]
    if e.get("kind") in ("named", "genre", "another"):
        body["kind"] = e["kind"]
    if e.get("shuffle"):
        body["shuffle"] = True
    if (p := spoken_player(row, snap)):
        body["player"] = p
    area, device = origin_of(row)
    if area:
        body["origin_area"] = area
    if device:
        body["origin_device"] = device
    return body


def corpus_rows() -> list[dict]:
    path = Path(__file__).resolve().parent / "voice_corpus.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def genres_from_db(path: Path) -> list[str]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        out = set()
        for (g,) in con.execute("SELECT genres FROM artists WHERE genres != '[]'"):
            out.update(x for x in json.loads(g) if isinstance(x, str))
        return sorted(out)
    finally:
        con.close()
