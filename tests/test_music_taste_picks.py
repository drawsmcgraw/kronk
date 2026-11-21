"""Kronk's own picks do not feed the taste profile as household taste
(sprint M23, fix 3 of docs/incidents/INVESTIGATION_2026-11-21_cracker_radio.md).

The no-phrase pick played Cracker for hours; MA's play history (the
strongest input) filled with Cracker; the next no-phrase pick was Cracker
again. The nightly build now leaves out the history rows Kronk's picks
played (taste.kronk_pick_rows). Named plays, and feedback, still count.

The fake Music Assistant below keeps MA's play history the way MA does
(media_progress: one row per item, its last play time, queue and a sticky
user_initiated flag) and answers music/recently_played_items with MA's own
filters: queue_id, played_after_timestamp, user_initiated_only. The answer
carries no time, queue or flag, as MA's does.
"""
import sqlite3
from unittest.mock import AsyncMock

import pytest

from tool_service import taste

pytestmark = pytest.mark.usefixtures("no_network")

KITCHEN, BASEMENT = "up20f83b0ac919", "up0ab117"
T = 1_795_000_000                      # the pick
H = 3600

ARTISTS = {"Cracker": "library://artist/639", "Pink Floyd": "library://artist/12",
           "Fatboy Slim": "ytmusic://artist/fs", "Orbital": "library://artist/7"}


class FakeMA:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []

    def play(self, uri, media_type, ts, queue, user_initiated, artist=None, name=None):
        """One play as MA records it: the row is upserted, the flag sticky."""
        old = self.rows.get(uri)
        self.rows[uri] = {"uri": uri, "media_type": media_type, "name": name or uri, "ts": ts, "queue": queue,
                          "user_initiated": user_initiated or bool(old and old["user_initiated"]),
                          "artist": artist}

    def artist_session(self, artist, ts, queue, n, user_initiated_container=True, start=0):
        """An artist played as MA plays it: the artist row (the container),
        then its tracks, which MA does not mark user-initiated."""
        self.play(ARTISTS[artist], "artist", ts, queue, user_initiated_container, name=artist)
        for i in range(n):
            self.play(f"library://track/{artist.lower().replace(' ', '')}{start + i}", "track",
                      ts + 240 * (i + 1), queue, False, artist=artist)
            self.play(ARTISTS[artist], "artist", ts + 240 * (i + 1), queue, False, name=artist)   # MA's credit

    async def __call__(self, command, _timeout=20, **args):
        self.calls.append((command, args))
        if command == "music/artists/library_items":
            lib = [{"name": n, "uri": u} for n, u in ARTISTS.items() if u.startswith("library://")]
            return lib[args.get("offset", 0):]
        if command == "music/radios/library_items":
            return [{"name": "Orbital Radio", "uri": "library://radio/5", "provider": "pandora"}][args.get("offset", 0):]
        if command == "music/item_by_uri":
            r = self.rows.get(args["uri"])
            return {"artists": [{"name": r["artist"], "uri": ARTISTS[r["artist"]]}]} if r and r["artist"] else {}
        if command == "music/recently_played_items":
            rows = sorted(self.rows.values(), key=lambda r: r["ts"], reverse=True)
            if "queue_id" in args:
                rows = [r for r in rows if r["queue"] == args["queue_id"]]
            if "played_after_timestamp" in args:
                rows = [r for r in rows if r["ts"] >= args["played_after_timestamp"]]
            if args.get("user_initiated_only"):
                rows = [r for r in rows if r["user_initiated"]]
            return [{"uri": r["uri"], "media_type": r["media_type"], "name": r["name"]}
                    for r in rows[:args.get("limit", 10)]]
        raise AssertionError(command)


def household_before(ma):
    """Weeks of the household's own listening, before the pick."""
    ma.artist_session("Pink Floyd", T - 10 * H, KITCHEN, 6)
    ma.play("ytmusic://track/rh", "track", T - 2 * H, KITCHEN, True, artist="Fatboy Slim")
    ma.artist_session("Orbital", T - 30 * H, BASEMENT, 3)


def add_pick(path, key, uri, ts=T, queue=KITCHEN, entity="media_player.kitchen", pid=None):
    with taste.db(path) as con:
        con.execute("INSERT INTO picks (ts, entity, phrase, key, uri, label, outcome, queue) VALUES (?,?,?,?,?,?,?,?)",
                    (ts, entity, "", key, uri, key, None, queue))


async def build(ma, path):
    summary = await taste.build_profile(ma, path, tagger=AsyncMock(return_value={}))
    with sqlite3.connect(path) as con:
        hist = {r[0]: r[1] for r in con.execute("SELECT name, history FROM artists")}
    return summary, hist, {a["name"]: a["score"] for a in taste.profile(path=path)["artists"]}


@pytest.mark.asyncio
async def test_a_kronk_pick_does_not_raise_its_artists_weight(tmp_path):
    """The pick's plays are left out: the profile is the one the household's
    own listening gives, as if the pick had never played."""
    without = FakeMA()
    household_before(without)
    _, hist0, score0 = await build(without, tmp_path / "a.db")

    picked = FakeMA()
    household_before(picked)
    picked.artist_session("Cracker", T, KITCHEN, 20)          # hours of Kronk's Cracker
    add_pick(tmp_path / "b.db", "artist:cracker", ARTISTS["Cracker"])
    summary, hist1, score1 = await build(picked, tmp_path / "b.db")

    assert summary["kronk_pick_items"] == 21                    # the artist row and its 20 tracks
    assert hist1 == hist0 and score1 == score0
    assert hist1["Cracker"] == 0.0


@pytest.mark.asyncio
async def test_the_picks_own_item_marked_before_the_pick_is_recorded_is_kronks(tmp_path):
    """A pick is recorded after its play is verified; MA marks the artist
    played when the play starts, a second or more before. Pick 19: the
    artist row at 13:40:56, the pick at 13:40:57, and no track played
    through, so no later credit moved the row."""
    ma = FakeMA()
    household_before(ma)
    ma.artist_session("Cracker", T - 1, KITCHEN, 0)
    add_pick(tmp_path / "t.db", "artist:cracker", ARTISTS["Cracker"])
    summary, hist, _ = await build(ma, tmp_path / "t.db")
    assert summary["kronk_pick_items"] == 1 and hist["Cracker"] == 0.0


@pytest.mark.asyncio
async def test_without_the_rule_the_pick_tops_the_profile(tmp_path):
    """The incident's loop, for contrast: the same history with no pick
    recorded puts Cracker on top."""
    ma = FakeMA()
    household_before(ma)
    ma.artist_session("Cracker", T, KITCHEN, 20)
    _, _, score = await build(ma, tmp_path / "t.db")
    assert max(score, key=score.get) == "Cracker"


@pytest.mark.asyncio
async def test_a_named_play_raises_its_artists_weight(tmp_path):
    base = FakeMA()
    household_before(base)
    _, hist0, _ = await build(base, tmp_path / "a.db")
    named = FakeMA()
    household_before(named)
    named.play("library://track/cracker-low", "track", T + 5 * H, BASEMENT, True, artist="Cracker")
    _, hist1, _ = await build(named, tmp_path / "b.db")
    assert hist0["Cracker"] == 0.0 and hist1["Cracker"] > 0.0


@pytest.mark.asyncio
async def test_a_named_play_inside_the_picks_window_still_counts(tmp_path):
    """The household asked for a Cracker song by name on the same speaker,
    an hour into Kronk's Cracker: that row is user-initiated and counts."""
    ma = FakeMA()
    household_before(ma)
    ma.artist_session("Cracker", T, KITCHEN, 10)
    ma.play("library://track/cracker-asked", "track", T + H, KITCHEN, True, artist="Cracker")
    add_pick(tmp_path / "t.db", "artist:cracker", ARTISTS["Cracker"])
    summary, hist, _ = await build(ma, tmp_path / "t.db")
    assert summary["kronk_pick_items"] == 11
    assert hist["Cracker"] > 0.0


@pytest.mark.asyncio
async def test_other_artists_played_after_the_pick_still_count(tmp_path):
    """Fatboy Slim asked for by name after the pick, on the same queue, while
    the pick's queue went on: only Cracker's rows are Kronk's."""
    ma = FakeMA()
    household_before(ma)
    ma.artist_session("Cracker", T, KITCHEN, 5)
    ma.play("ytmusic://track/rh", "track", T + 2 * H, KITCHEN, True, artist="Fatboy Slim")
    add_pick(tmp_path / "t.db", "artist:cracker", ARTISTS["Cracker"])
    _, hist, _ = await build(ma, tmp_path / "t.db")
    assert hist["Fatboy Slim"] > 0.9


@pytest.mark.asyncio
async def test_the_same_artist_on_another_queue_still_counts(tmp_path):
    ma = FakeMA()
    household_before(ma)
    ma.artist_session("Cracker", T, KITCHEN, 5)
    ma.artist_session("Cracker", T + H, BASEMENT, 5, start=100)     # the household's, in the basement
    add_pick(tmp_path / "t.db", "artist:cracker", ARTISTS["Cracker"])
    summary, hist, _ = await build(ma, tmp_path / "t.db")
    # MA keeps one row per item: the artist row now says basement, so it counts too
    assert summary["kronk_pick_items"] == 5 and hist["Cracker"] > 0.0


@pytest.mark.asyncio
async def test_plays_after_the_window_still_count(tmp_path):
    ma = FakeMA()
    household_before(ma)
    ma.artist_session("Cracker", T, KITCHEN, 5)
    ma.artist_session("Cracker", T + 48 * H, KITCHEN, 5, start=100)  # two days later, by name
    add_pick(tmp_path / "t.db", "artist:cracker", ARTISTS["Cracker"])
    summary, hist, _ = await build(ma, tmp_path / "t.db")
    assert summary["kronk_pick_items"] == 5 and hist["Cracker"] > 0.0


@pytest.mark.asyncio
async def test_a_pick_without_a_queue_is_read_on_every_queue(tmp_path):
    """Picks recorded before M23 (ids 18 and 19 in production) have no queue."""
    ma = FakeMA()
    household_before(ma)
    ma.artist_session("Cracker", T, KITCHEN, 8)
    add_pick(tmp_path / "t.db", "artist:cracker", ARTISTS["Cracker"], queue=None)
    summary, hist, _ = await build(ma, tmp_path / "t.db")
    assert summary["kronk_pick_items"] == 9 and hist["Cracker"] == 0.0
    asked = [a for c, a in ma.calls if c == "music/recently_played_items" and "played_after_timestamp" in a]
    assert asked and all("queue_id" not in a for a in asked)


@pytest.mark.asyncio
async def test_a_station_pick_leaves_out_the_station(tmp_path):
    ma = FakeMA()
    household_before(ma)
    ma.play("library://radio/5", "radio", T, KITCHEN, True, name="Orbital Radio")
    add_pick(tmp_path / "t.db", "uri:library://radio/5", "library://radio/5")
    summary, _, _ = await build(ma, tmp_path / "t.db")
    assert summary["kronk_pick_items"] == 1
    with sqlite3.connect(tmp_path / "t.db") as con:
        assert con.execute("SELECT plays FROM stations WHERE uri='library://radio/5'").fetchone()[0] == 0.0


@pytest.mark.asyncio
async def test_feedback_on_a_pick_still_counts(tmp_path):
    ma = FakeMA()
    household_before(ma)
    ma.artist_session("Cracker", T, KITCHEN, 5)
    add_pick(tmp_path / "t.db", "artist:cracker", ARTISTS["Cracker"])
    _, _, before = await build(ma, tmp_path / "t.db")
    taste.record_feedback("artist:cracker", 0.5, "thumb_up", tmp_path / "t.db")
    _, _, after = await build(ma, tmp_path / "t.db")
    assert after["Cracker"] == pytest.approx(before["Cracker"] + 0.5)


@pytest.mark.asyncio
async def test_no_picks_asks_nothing_more(tmp_path):
    ma = FakeMA()
    household_before(ma)
    summary, _, _ = await build(ma, tmp_path / "t.db")
    assert summary["kronk_pick_items"] == 0
    assert [a for c, a in ma.calls if c == "music/recently_played_items"] == [{"limit": taste.HISTORY_LIMIT}]


def test_record_pick_keeps_the_queue(tmp_path):
    p = taste.Pick("artist", ARTISTS["Cracker"], "Cracker", "artist:cracker", 4.6, "artist")
    pid = taste.record_pick("media_player.kitchen", "", p, tmp_path / "t.db", queue=KITCHEN)
    with taste.db(tmp_path / "t.db") as con:
        assert con.execute("SELECT queue FROM picks WHERE id=?", (pid,)).fetchone()[0] == KITCHEN


def test_an_old_taste_db_gains_the_queue_column(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE picks (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, entity TEXT, phrase TEXT, "
                "key TEXT, uri TEXT, label TEXT, outcome TEXT)")
    con.execute("INSERT INTO picks (ts, entity, phrase, key, uri, label, outcome) VALUES (1,'e','','artist:x','u','x',NULL)")
    con.commit()
    con.close()
    with taste.db(path) as con:
        row = con.execute("SELECT * FROM picks").fetchone()
    assert row["queue"] is None and row["key"] == "artist:x"


def test_a_read_only_old_taste_db_still_opens_and_picks(tmp_path):
    """The dev pair reads production's taste DB through a read-only mount.
    The first cut of the migration raised there, and dev picked with no
    profile at all ("0 picks"); the DB must open as it is."""
    import os
    path = tmp_path / "ro.db"
    con = sqlite3.connect(path)
    con.executescript(taste._SCHEMA.replace(", outcome TEXT, queue TEXT)", ", outcome TEXT)"))
    con.execute("INSERT INTO artists VALUES ('cracker','Cracker','library://artist/639','[\"rock\"]','ma',1,0,1,3.0)")
    con.commit()
    con.close()
    os.chmod(path, 0o444)
    try:
        with taste.db(path) as ro:
            assert "queue" not in {r["name"] for r in ro.execute("PRAGMA table_info(picks)")}
            picks = taste.local_genre_picks([], ro, {})
        assert [p.label for p in picks] == ["Cracker"]
        assert taste._recent_picks(path) == []
    finally:
        os.chmod(path, 0o644)


def test_the_genre_pick_records_the_players_queue(genre_env):
    """/music passes the player's active_queue (HA's state attribute) to
    record_pick."""
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    import tool_service.main as ts
    from tests.test_error_surfacing import FakeHA
    orig = FakeHA.get

    async def get(self, url, headers=None):
        r = await orig(self, url, headers)
        r._body["attributes"]["active_queue"] = KITCHEN
        return r
    with patch.object(FakeHA, "get", get):
        r = TestClient(ts.app).post("/music", json={"query": "some deep house", "kind": "genre"})
    assert r.status_code == 200, r.text
    with taste.db(genre_env.db) as con:
        assert [x[0] for x in con.execute("SELECT queue FROM picks")] == [KITCHEN]


from tests.test_error_surfacing import music_env  # noqa: E402,F401  (fixture)
from tests.test_music_taste import genre_env  # noqa: E402,F401  (fixture)
