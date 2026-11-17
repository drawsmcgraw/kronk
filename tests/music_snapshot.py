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

# Music Assistant commands that only read. The recorder refuses any other
# command, and so does Replay.
READ_COMMANDS = frozenset({
    "music/search", "music/item_by_name",
    "music/radios/library_items", "music/playlists/library_items",
    "music/albums/library_items", "music/artists/library_items", "music/tracks/library_items",
})

# The satellites (scripts/voice_replay.py DEVICES): HA device id and the key
# of their Music Assistant player ('up' + MAC), which is what the players
# template answers as origin_key for that device.
DEVICE_IDS = {
    "kitchen_pe": "e0949788663255952ce7778975527882",
    "office_sat1": "ac6dd1f1c8cf1d229cda0f42224a2013",
    "basement_pe": "1b6bd3b03f90e52f08dd98dca41eac82",
}
ORIGIN_KEYS = {
    "e0949788663255952ce7778975527882": "up20f83b0ac919",
    "ac6dd1f1c8cf1d229cda0f42224a2013": "up14c19fd8d1bc",
    "1b6bd3b03f90e52f08dd98dca41eac82": "up20f83b0ab117",
}
# MUSIC_DEFAULT_PLAYER on the dev path (.env.dev): the office speaker.
DEFAULT_PLAYER = "media_player.satellite1_d8d1bc"


def key(command: str, args: dict) -> str:
    return json.dumps([command, {k: v for k, v in args.items() if not k.startswith("_")}],
                      sort_keys=True, ensure_ascii=False)


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


class Replay:
    """ma_command stand-in that answers from the snapshot."""

    def __init__(self, snap: dict, error_cls):
        self.ma = snap["ma"]
        self.error_cls = error_cls
        self.calls: list[tuple[str, dict]] = []
        self.missing: list[str] = []

    async def __call__(self, command: str, _timeout: float = 20, **args):
        assert command in READ_COMMANDS, f"not a read command: {command}"
        self.calls.append((command, args))
        k = key(command, args)
        if k not in self.ma:
            self.missing.append(k)
            raise self.error_cls("not in snapshot")
        v = self.ma[k]
        if isinstance(v, dict) and set(v) == {"__error__"}:
            raise self.error_cls(v["__error__"])
        return json.loads(json.dumps(v))           # a fresh copy: callers mutate results


_DEVICE_RE = re.compile(r"device_attr\('([0-9a-f]{32})'")


def players_answer(snap: dict, template: str) -> dict:
    """What HA's /api/template answers for tool_service's players template."""
    m = _DEVICE_RE.search(template or "")
    return {"players": snap["players"], "origin_key": ORIGIN_KEYS.get(m.group(1), "") if m else ""}


def seed_taste(db_path: Path, snap: dict) -> None:
    """A taste DB whose genre vocabulary is the household's, with no artist
    that can be picked (uri NULL) and no stations: genre picks come from the
    recorded searches only."""
    from tool_service import taste
    con = taste.db(db_path)
    with con:
        con.execute("INSERT INTO artists (key, name, uri, genres, genre_source, history, station, library, score)"
                    " VALUES ('artist:vocabulary', 'vocabulary', NULL, ?, 'fixture', 0, 0, 0, 0)",
                    (json.dumps(snap["genres"]),))
    con.close()


def origin_of(row: dict) -> tuple[str | None, str | None]:
    """(origin_area, origin_device) for a corpus row."""
    o = row.get("origin") or {}
    return o.get("area"), DEVICE_IDS.get(o.get("device") or "")


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


class _FirstPick:
    """Deterministic genre pick: always the top of the list."""
    @staticmethod
    def choices(head, weights, k):
        return [head[0]]


class WriteRefused(AssertionError):
    """Code under an offline snapshot env tried to change a player or MA."""


def offline_env(snap: dict, tmp: Path, ma=None):
    """Context manager: tool_service.main wired to the snapshot. HA answers
    only the players template (a state read or any other POST fails the
    test: under MUSIC_DRY_RUN nothing reads player state), MUSIC_DRY_RUN is
    on, every HA service call raises WriteRefused, Music Assistant is the
    Replay (or `ma`), the taste DB and file are temporary copies seeded from
    the snapshot, and the genre pick is deterministic. Yields the Replay."""
    from contextlib import ExitStack, contextmanager
    from unittest.mock import patch

    import tool_service.main as ts
    from tool_service import taste

    @contextmanager
    def cm():
        replay = ma or Replay(snap, ts.MAError)
        tmp.mkdir(parents=True, exist_ok=True)
        dbp = tmp / "taste.db"
        if dbp.exists():
            dbp.unlink()
        seed_taste(dbp, snap)
        tf_path = tmp / "taste.yaml"
        tf_path.write_text(snap["taste"])
        tf = taste.TasteFile(tf_path)

        class _Resp:
            def __init__(self, body, status_code=200):
                self._body, self.status_code, self.text = body, status_code, json.dumps(body)

            def json(self):
                return self._body

        class FakeHA:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, headers=None, json=None, **kw):
                assert url.endswith("/api/template"), f"unexpected HA POST {url}"
                return _Resp(players_answer(snap, (json or {}).get("template", "")))

            async def get(self, url, headers=None, **kw):
                raise WriteRefused(f"unexpected HA read {url} under dry run")

        async def refuse(*a, **kw):
            raise WriteRefused(f"HA service call under an offline snapshot env: {a}")

        with ExitStack() as st:
            st.enter_context(patch.object(ts.httpx, "AsyncClient", FakeHA))
            st.enter_context(patch.object(ts, "ha_call_service", new=refuse))
            st.enter_context(patch.object(ts, "ma_command", new=replay))
            st.enter_context(patch.object(ts, "HA_TOKEN", "snapshot"))
            st.enter_context(patch.object(ts, "MUSIC_DRY_RUN", True))
            st.enter_context(patch.object(ts, "MUSIC_DEFAULT_PLAYER", DEFAULT_PLAYER))
            st.enter_context(patch.object(taste, "TASTE_DB", dbp))
            st.enter_context(patch.object(taste, "TASTE", tf))
            st.enter_context(patch.object(taste, "RANDOM", _FirstPick))
            ts._LAST_PICKS.clear()
            yield replay

    return cm()


class ScriptedModel:
    """Stand-in for main.ask_style_or_named: a fixed answer (or None), no network."""

    def __init__(self, answer: str | None):
        self.answer, self.asked = answer, []

    async def __call__(self, sentence, words):
        self.asked.append(words)
        return self.answer, ({} if self.answer else {"error": "no model in this run"})


async def decide_row(row: dict, snap: dict, model_answer: str | None = None, ask_model: bool = True):
    """The M11 decision for a corpus row (main.decide_sentence), inside an
    offline_env. The model question gets `model_answer`."""
    from unittest.mock import patch
    import tool_service.main as ts
    area, device = origin_of(row)
    with patch.object(ts, "ask_style_or_named", new=ScriptedModel(model_answer)):
        return await ts.decide_sentence(row["text"], device, area, ask_model=ask_model)
