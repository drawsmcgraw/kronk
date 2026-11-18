"""The voice corpus's offline music layer, as one module for the test suite
and the running service (sprint M21 step 2, criterion 12).

tests/test_voice_corpus.py runs every play_music row of
tests/voice_corpus.jsonl through the M11 path offline: the sentence parser
and the decision order (main.decide_sentence), with Music Assistant and
Home Assistant replayed from tests/fixtures/music_snapshot.json, dry run
on, every HA service call refused. Until step 2 that machinery lived in
tests/music_snapshot.py; it lives here now so the /settings page can run
the same check against a candidate music.yaml before a save. The test
suite imports it from here (tests/music_snapshot.py re-exports it), so the
check the page runs is the check the suite runs.

In the service, the check runs in a child process (`python corpus_check.py`
with a JSON request on stdin, run by main's POST /config/corpus): it
patches module globals of main and taste, which must never happen inside
the live service, and the child blocks every socket before it imports
anything, so the run reaches no network and plays nothing. The image
carries the corpus and the snapshot at /app/corpus/ (Dockerfile, from the
repo's tests/ at the same commit).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

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

PARSER_FIELDS = ("kind", "media_type", "name", "artist", "provider", "player", "shuffle")
PROVIDER = {"ytmusic": "youtube_music", "filesystem_local": "nas", "pandora": "pandora"}


def _modules():
    """(main, taste, music_resolve, config_loader): package imports in tests,
    flat in the image."""
    try:
        from . import config_loader, main, music_resolve, taste
    except ImportError:
        import config_loader
        import main
        import music_resolve
        import taste
    return main, taste, music_resolve, config_loader


def key(command: str, args: dict) -> str:
    return json.dumps([command, {k: v for k, v in args.items() if not k.startswith("_")}],
                      sort_keys=True, ensure_ascii=False)


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
    _, taste, _, _ = _modules()
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

    ts, taste, _, _ = _modules()

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
    ts, _, _, _ = _modules()
    area, device = origin_of(row)
    with patch.object(ts, "ask_style_or_named", new=ScriptedModel(model_answer)):
        return await ts.decide_sentence(row["text"], device, area, ask_model=ask_model)


def reading(d) -> dict:
    """The fields of a decision the corpus compares (PARSE in the test)."""
    o, p = d.outcome, d.parsed
    return {"kind": o.kind, "media_type": o.media_type, "name": o.name, "artist": o.artist,
            "provider": {"named": p.provider, "chosen": o.provider}, "shuffle": p.shuffle,
            "player": {"entities": d.entities, "error": d.error}, "action": o.action,
            "decided_by": o.decided_by}


def expected_entities(row, snap):
    ts, _, _, _ = _modules()
    players = [ts._norm_player(x) for x in snap["players"]]
    want = row["expect"]["player"]
    if want.startswith("entity:"):
        return sorted([want.split(":", 1)[1]])
    if want.startswith("area:"):
        return sorted(t["entity_id"] for t in ts._by_area(players, want.split(":", 1)[1]))
    area, device = origin_of(row)
    targets, _ = ts.resolve_players(None, area, players, DEFAULT_PLAYER, origin_key=ORIGIN_KEYS.get(device or ""))
    return sorted(t["entity_id"] for t in targets)


def parser_diff(row, got, snap):
    """{field: (wanted, got)} for each field of the row's expectation the
    reading misses (docs/features/voice-regression-corpus.md has the rules)."""
    _, _, mr, _ = _modules()
    want = {k: row["expect"][k] for k in PARSER_FIELDS if k in row["expect"]}
    diff = {}
    for k, w in want.items():
        g = got[k]
        if k == "name":
            ok = (w is None and g is None) or (w is not None and g is not None and mr.fold(w) == mr.fold(g))
        elif k == "artist":   # one of the credited artists ("Ulrich Schnauss and Mark Peters")
            ok = (w is None and g is None) or (w is not None and g is not None and (
                mr.fold(w) == mr.fold(g) or mr.fold(w) in [mr.fold(a) for a in g.split(" and ")]))
        elif k == "provider":
            named, chosen = PROVIDER.get(g["named"]), PROVIDER.get(g["chosen"])
            ok = named is None if w is None else w in (named, chosen)
            g = {"named": named, "chosen": chosen}
        elif k == "player":
            if w.startswith("unknown:"):
                ok = bool(g["error"]) and w.split(":", 1)[1].lower() in g["error"].lower()
            else:
                ok = g["entities"] is not None and sorted(g["entities"]) == expected_entities(row, snap)
        else:
            ok = g == w
        if not ok:
            diff[k] = (w, g)
    return diff


# ── the check for /settings ──────────────────────────────────────────────────

def _summary(got: dict) -> str:
    prov = got["provider"]
    player = got["player"]["error"] or ",".join(got["player"]["entities"] or [])
    return (f"{got['kind']} {got['media_type'] or '-'} name={got['name']!r} artist={got['artist']!r} "
            f"provider={prov['chosen'] or prov['named'] or '-'} shuffle={got['shuffle']} player={player} "
            f"(by {got['decided_by']})")


async def run_rows(rows: list[dict], snap: dict, music, said: dict, tmp: Path) -> dict:
    """Each play_music row (and each non-music row's parse) under the music
    policy `music` (a config_loader.Music), with `said` overriding a row's
    sentence. {row id: {"outcome": str, "ok": bool, "why": str}}."""
    ts, _, mr, config_loader = _modules()
    config_loader._current = config_loader.Config(music=music, source="files")
    out = {}
    with offline_env(snap, tmp):
        for r in rows:
            text = said.get(r["id"], r["text"])
            if r["expect"]["tool"] == "play_music":
                d = await decide_row({**r, "text": text}, snap, model_answer=None)
                got = reading(d)
                diff = parser_diff(r, got, snap)
                out[r["id"]] = {"outcome": _summary(got), "ok": not diff,
                                "why": "; ".join(f"{k}: want {w!r}, got {g!r}" for k, (w, g) in diff.items())}
            elif "non-music" in r.get("tags", []):
                music_ = mr.parse_sentence(text, snap["players"]).music
                out[r["id"]] = {"outcome": f"parsed as music={music_}", "ok": music_ is False,
                                "why": "parsed as music" if music_ else ""}
    return out


def _block_network() -> None:
    import socket

    def refuse(*a, **kw):
        raise OSError("the corpus check reaches no network")
    for fn in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo",
               "create_connection"):
        setattr(socket, fn, refuse)
    real = socket.socket

    class NoInet(real):
        def __init__(self, family=socket.AF_INET, *a, **kw):
            if family in (socket.AF_INET, socket.AF_INET6):
                raise OSError("the corpus check reaches no network")
            super().__init__(family, *a, **kw)
    socket.socket = NoInet


def main() -> int:
    """Child-process entry: a JSON request on stdin, a JSON answer on stdout.
    {"corpus": path, "snapshot": path, "runs": {name: {"music": <dump> |
    null, "music_yaml": text | null, "said": {id: text}}}}."""
    import asyncio
    import os
    import sys
    import tempfile
    _block_network()
    req = json.loads(sys.stdin.read())
    rows = [json.loads(x) for x in Path(req["corpus"]).read_text().splitlines() if x.strip()]
    snap = json.loads(Path(req["snapshot"]).read_text())
    _, _, _, config_loader = _modules()
    answer = {}
    with tempfile.TemporaryDirectory(prefix="corpus-") as tmp:
        for name, run in req["runs"].items():
            if run.get("music_yaml") is not None:
                data, lines = config_loader.parse_yaml(run["music_yaml"], config_loader.MUSIC_FILE)
                music = config_loader.validate(config_loader.Music, data, lines, config_loader.MUSIC_FILE)
            else:
                music = config_loader.Music.model_validate(run["music"])
            answer[name] = asyncio.run(run_rows(rows, snap, music, run.get("said") or {}, Path(tmp) / name))
    sys.stdout.write(json.dumps(answer))
    return 0


if __name__ == "__main__":
    import os
    import sys
    import tempfile
    # Before any tool_service module is imported: no telemetry export, dry
    # run, no shadow, and the taste store in a temporary directory (never
    # the service's /data).
    # -I (isolated, implies -P on Python 3.11+) leaves this directory off
    # sys.path; the service's modules are beside this file.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    _scratch = tempfile.mkdtemp(prefix="corpus-taste-")
    os.environ.update({"TELEMETRY_ENABLED": "false", "MUSIC_DRY_RUN": "1", "MUSIC_TASTE_ENABLED": "0",
                       "NEWS_ENABLED": "0", "SOLAR_POLL_ENABLED": "0",
                       "MUSIC_TASTE_FILE": os.path.join(_scratch, "taste.yaml"),
                       "MUSIC_TASTE_DB": os.path.join(_scratch, "taste.db")})
    os.environ.pop("MUSIC_SHADOW", None)
    try:
        code = main()
    finally:
        import shutil
        shutil.rmtree(_scratch, ignore_errors=True)
    sys.exit(code)
