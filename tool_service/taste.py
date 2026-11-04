"""Household music taste (docs/plans/MUSIC_TASTE_AGENT_PLAN.md).

Two stores, explicit over learned:

- TOLD — `music_taste.yaml`, one operator-editable file. Kronk writes to the
  same file when the household says "we love X" / "never play X" / "when I
  say chill I mean downtempo". Round-trip YAML (ruamel) so Kronk's writes
  keep the operator's comments and ordering. Re-read whenever its mtime
  changes; a file that no longer parses keeps the last good version and the
  error is reported (GET /music/taste, and the log) rather than silently
  emptying the household's taste.
- LEARNED — `music_taste.db` (SQLite), rebuilt nightly from Music
  Assistant: play history (recency-weighted, the strongest signal), Pandora
  stations, then the NAS library (weakest — operator decision 2026-10-21).
  Derived only: delete the file and the next build recreates it, except
  the `tags` (model genre guesses, kept so they're made once) and the
  `feedback` / `picks` tables (what the household did with Kronk's picks).

MA's play history carries no timestamps and no player (measured
2026-10-21), so the learned profile has no room/time patterns; standing
rules in the file cover that.
"""
import asyncio
import io
import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

try:
    from . import music_resolve
except ImportError:
    import music_resolve

logger = logging.getLogger("tool_service.taste")

TASTE_FILE = Path(os.getenv("MUSIC_TASTE_FILE", "/data/music_taste.yaml"))

DEFAULT_TASTE = """\
# Kronk's household music taste. Edit freely — Kronk re-reads this file
# whenever it changes. Kronk also writes here when you tell it things:
#   "we love Ulrich Schnauss"            → love
#   "never play Nickelback"              → never
#   "forget that we love X"              → removed from love/never
#   "when I say chill I mean deep house" → genres
# Anything here beats what Kronk learns from listening.
# Docs: docs/features/music-taste.md

# Artists, albums, or genres the household loves. Favored when Kronk picks.
love: []

# Never chosen when Kronk picks. Asking for one by name still plays it.
never: []

# What a genre word means in this house. Asking for the word makes Kronk
# pick from these genres.
genres:
  chill: [deep house, downtempo, chillout]

# Standing rules for "play some music" when no genre is named. The first
# rule that matches wins. All conditions optional:
#   days: [mon, tue, wed, thu, fri, sat, sun]
#   hours: "07-11"        (local time, start inclusive, end exclusive)
#   room: Kitchen         (the room of the speaker that will play)
# Example:
#   - {days: [sat, sun], hours: "07-11", room: Kitchen, play: jazz}
rules: []
"""

_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_HOURS_RE = re.compile(r"^\s*(\d{1,2})\s*-\s*(\d{1,2})\s*$")


class TasteError(ValueError):
    """The taste file is unusable; str(e) says where and why."""


def _yaml() -> YAML:
    y = YAML()                    # round-trip: comments and order survive
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 100
    return y


def _str_list(v, where: str) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, (str, int, float)) for x in v):
        raise TasteError(f"{where} must be a list of names")
    return [str(x).strip() for x in v if str(x).strip()]


def validate(doc) -> dict:
    """Shape-check a parsed taste document; return a plain normalized dict."""
    if doc is None:
        doc = {}
    if not isinstance(doc, dict):
        raise TasteError("the file must be a mapping with love / never / genres / rules")
    out = {"love": _str_list(doc.get("love"), "love"),
           "never": _str_list(doc.get("never"), "never"),
           "genres": {}, "rules": []}
    genres = doc.get("genres") or {}
    if not isinstance(genres, dict):
        raise TasteError("genres must map a word to a list of genres")
    for word, meaning in genres.items():
        if isinstance(meaning, str):
            meaning = [meaning]
        out["genres"][music_resolve.fold(str(word))] = _str_list(meaning, f"genres.{word}")
    rules = doc.get("rules") or []
    if not isinstance(rules, list):
        raise TasteError("rules must be a list")
    for i, r in enumerate(rules):
        if not isinstance(r, dict) or not str(r.get("play") or "").strip():
            raise TasteError(f"rules[{i}] needs a 'play'")
        days = [str(d).lower()[:3] for d in _str_list(r.get("days"), f"rules[{i}].days")]
        if any(d not in _DAYS for d in days):
            raise TasteError(f"rules[{i}].days must be from {', '.join(_DAYS)}")
        hours = None
        if r.get("hours") is not None:
            m = _HOURS_RE.match(str(r["hours"]))
            if not m or not (0 <= int(m.group(1)) <= 24 and 0 <= int(m.group(2)) <= 24):
                raise TasteError(f"rules[{i}].hours must look like \"07-11\"")
            hours = (int(m.group(1)), int(m.group(2)))
        out["rules"].append({"play": str(r["play"]).strip(), "days": days, "hours": hours,
                             "room": str(r["room"]).strip() if r.get("room") else None})
    return out


class TasteFile:
    def __init__(self, path: Path = TASTE_FILE):
        self.path = Path(path)
        self._mtime: float | None = None
        self._data = validate({})
        self.error: str | None = None
        self._lock = threading.Lock()

    def _ensure(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(DEFAULT_TASTE)
            logger.info("taste file created with defaults at %s", self.path)

    def load(self) -> dict:
        """Current taste. Cheap when the file hasn't changed."""
        with self._lock:
            try:
                self._ensure()
                mtime = self.path.stat().st_mtime
            except OSError as e:
                self.error = f"can't read {self.path}: {e}"
                logger.error("taste file: %s", self.error)
                return self._data
            if mtime == self._mtime:
                return self._data
            try:
                data = validate(_yaml().load(self.path.read_text()))
            except (YAMLError, TasteError) as e:
                self.error = f"{self.path.name} is not valid, keeping the last good version: {e}"
                logger.error("taste file: %s", self.error)
            else:
                self._data, self.error = data, None
            self._mtime = mtime
            return self._data

    # ── writes ───────────────────────────────────────────────────────────────
    def update(self, action: str, value: str, meaning: list[str] | None = None) -> str:
        """Apply one told preference and return the sentence to speak.
        Raises TasteError when the file can't be safely edited."""
        value = (value or "").strip().strip(".")
        if not value:
            raise TasteError("Tell me what — an artist, album, or genre.")
        with self._lock:
            self._ensure()
            y = _yaml()
            try:
                doc = y.load(self.path.read_text())
                validate(doc)
            except (YAMLError, TasteError) as e:
                # Never overwrite a file we can't parse — that would destroy
                # the operator's edits.
                raise TasteError(f"The taste file has an error, so I didn't change it: {e}") from e
            if doc is None:
                doc = y.load(DEFAULT_TASTE)
            for key in ("love", "never"):
                if doc.get(key) is None:
                    doc[key] = []
            key = music_resolve.fold(value)

            def drop(lst) -> bool:
                hit = [x for x in lst if music_resolve.fold(str(x)) == key]
                for x in hit:
                    lst.remove(x)
                return bool(hit)

            if action == "love":
                drop(doc["never"])
                if not any(music_resolve.fold(str(x)) == key for x in doc["love"]):
                    doc["love"].append(value)
                said = f"Got it — you love {value}. I'll favor it when I pick."
            elif action == "never":
                drop(doc["love"])
                if not any(music_resolve.fold(str(x)) == key for x in doc["never"]):
                    doc["never"].append(value)
                said = f"Got it — I won't pick {value}. Ask for it by name and I'll still play it."
            elif action == "forget":
                hit = drop(doc["love"]) | drop(doc["never"])
                said = (f"Forgotten — {value} is off the list." if hit
                        else f"{value} wasn't on the love or never list.")
            elif action == "means":
                parts = [m.strip() for m in (meaning or []) if m and m.strip()]
                if not parts:
                    raise TasteError(f"Tell me what {value} means — which genres.")
                if doc.get("genres") is None:
                    doc["genres"] = {}
                for k in [k for k in doc["genres"] if music_resolve.fold(str(k)) == key]:
                    del doc["genres"][k]
                doc["genres"][value.lower()] = parts
                said = f"Got it — {value} means {', '.join(parts)}."
            else:
                raise TasteError(f"Unknown preference action '{action}'.")
            buf = io.StringIO()
            y.dump(doc, buf)
            tmp = self.path.with_suffix(".yaml.tmp")
            tmp.write_text(buf.getvalue())
            os.replace(tmp, self.path)
            self._mtime = None                       # re-read on next load
        logger.info("taste file: %s %r %s", action, value, meaning or "")
        return said


# ── reads used by the pickers ───────────────────────────────────────────────

def matches_any(name: str | None, names: list[str]) -> bool:
    k = music_resolve.fold(name)
    return bool(k) and any(music_resolve.fold(n) == k for n in names)


def rule_for(taste: dict, now, room: str | None) -> str | None:
    """The first standing rule matching this moment and room, or None."""
    day = _DAYS[now.weekday()]
    for r in taste["rules"]:
        if r["days"] and day not in r["days"]:
            continue
        if r["hours"]:
            start, end = r["hours"]
            if not (start <= now.hour < end if start <= end else now.hour >= start or now.hour < end):
                continue
        if r["room"] and music_resolve.fold(r["room"]) != music_resolve.fold(room):
            continue
        return r["play"]
    return None


TASTE = TasteFile()


# ── the learned half ─────────────────────────────────────────────────────────

TASTE_DB        = Path(os.getenv("MUSIC_TASTE_DB", "/data/music_taste.db"))
TASTE_TZ        = os.getenv("MUSIC_TASTE_TZ", "America/New_York")
TASTE_BUILD_AT  = os.getenv("MUSIC_TASTE_BUILD_AT", "03:30")       # local, daily
TASTE_LLM_URL   = os.getenv("MUSIC_TASTE_LLM_URL", "http://host.docker.internal:8002/v1")
TASTE_TAG_MODEL = os.getenv("MUSIC_TASTE_TAG_MODEL", "gemma-4-e4b")
TAG_TOP_N       = int(os.getenv("MUSIC_TASTE_TAG_TOP_N", "300"))   # tag at most this many untagged artists
HISTORY_LIMIT   = 500
HISTORY_DECAY   = 0.99     # weight of the n-th most recent play: 0.99**n
CONCURRENCY     = 8

# Weights of the learned score (explicit love/never apply at pick time).
W_HISTORY = 3.0            # scaled so the most-played artist scores 3
W_STATION = 1.0            # a Pandora station seeded on the artist
W_LIBRARY = 0.3            # it's on the NAS

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artists (key TEXT PRIMARY KEY, name TEXT, uri TEXT, genres TEXT,
    genre_source TEXT, history REAL, station INTEGER, library INTEGER, score REAL);
CREATE TABLE IF NOT EXISTS stations (uri TEXT PRIMARY KEY, name TEXT, provider TEXT, plays REAL);
CREATE TABLE IF NOT EXISTS tags (key TEXT PRIMARY KEY, name TEXT, genres TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS feedback (key TEXT, delta REAL, reason TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS picks (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, entity TEXT,
    phrase TEXT, key TEXT, uri TEXT, label TEXT, outcome TEXT);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def db(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or TASTE_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def artist_key(name: str | None) -> str:
    return "artist:" + music_resolve.fold(name)


def feedback_totals(con: sqlite3.Connection) -> dict[str, float]:
    return {r["key"]: r["t"] for r in con.execute("SELECT key, SUM(delta) t FROM feedback GROUP BY key")}


def record_feedback(key: str, delta: float, reason: str, path: Path | None = None) -> None:
    with db(path) as con:
        con.execute("INSERT INTO feedback VALUES (?,?,?,?)", (key, delta, reason, time.time()))
    logger.info("taste feedback %s %+.2f (%s)", key, delta, reason)


async def _gather_limited(coros, limit: int = CONCURRENCY):
    sem = asyncio.Semaphore(limit)

    async def one(c):
        async with sem:
            try:
                return await c
            except Exception as e:                  # one bad item never sinks a build
                return e
    return await asyncio.gather(*(one(c) for c in coros))


async def _paged(ma, command: str, page: int = 500) -> list[dict]:
    out, offset = [], 0
    while True:
        batch = await ma(command, limit=page, offset=offset) or []
        out += batch
        if len(batch) < page:
            return out
        offset += page


# Badly tagged NAS files surface as "artists" named "00", "01" or
# "[02] Memories Erased in the Storm-…" (first build, 2026-10-21): a bare
# number, or a name that starts with a track number.
_JUNK_ARTIST = re.compile(r"^\s*(?:\d{1,2}\s*$|0\d[\s._-]|[\[(]?\d{1,3}[\])]?\s*[-._\]]|\[\d{1,3}\])")


def _genres_of(item) -> list[str]:
    if not isinstance(item, dict):
        return []
    return [str(g).lower() for g in ((item.get("metadata") or {}).get("genres") or []) if g]


async def tag_artists(names: list[str], client: httpx.AsyncClient | None = None) -> dict[str, list[str]]:
    """Genre guesses for artists MA has no genres for — one local model call
    per 25 names. Returns {name: [genres]}; unknown artists get []. Errors
    are logged and skipped: an untagged artist just can't be picked by genre."""
    out: dict[str, list[str]] = {}
    own = client is None
    client = client or httpx.AsyncClient()
    try:
        for i in range(0, len(names), 25):
            batch = names[i:i + 25]
            prompt = (
                "For each music artist below, give 1 to 3 genres, lowercase, using common genre "
                "names (e.g. \"deep house\", \"downtempo\", \"trip hop\", \"indie rock\", \"jazz\", "
                "\"j-pop\", \"video game soundtrack\"). If you do not know the artist, give an "
                "empty list — do not guess. Reply with ONLY a JSON object mapping each artist "
                "name exactly as given to its list.\n\n" + "\n".join(batch))
            try:
                resp = await client.post(f"{TASTE_LLM_URL}/chat/completions", timeout=120, json={
                    "model": TASTE_TAG_MODEL, "temperature": 0.1, "max_tokens": 1500,
                    "messages": [{"role": "user", "content": prompt}]})
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"]["content"]
                m = re.search(r"\{.*\}", text, re.DOTALL)
                parsed = json.loads(m.group(0)) if m else {}
            except Exception as e:
                logger.warning("taste: genre tagging batch %d failed: %s", i // 25, str(e)[:200])
                continue
            wanted = {music_resolve.fold(n): n for n in batch}
            for k, v in parsed.items():
                n = wanted.get(music_resolve.fold(k))
                if n and isinstance(v, list):
                    out[n] = [str(g).lower().strip() for g in v if str(g).strip()][:3]
    finally:
        if own:
            await client.aclose()
    return out


async def build_profile(ma, path: Path | None = None, tagger=tag_artists) -> dict:
    """Rebuild the learned tables from MA. `ma` is main.ma_command."""
    t0 = time.time()
    artists: dict[str, dict] = {}

    def entry(name: str, uri: str | None = None) -> dict:
        k = music_resolve.fold(name) if not _JUNK_ARTIST.match(name or "") else ""
        if not k:
            return {"name": name, "uri": None, "genres": [], "genre_source": None,     # discarded
                    "history": 0.0, "station": 0, "library": 0}
        a = artists.setdefault(k, {"name": name, "uri": None, "genres": [], "genre_source": None,
                                   "history": 0.0, "station": 0, "library": 0})
        if uri and (not a["uri"] or uri.startswith("library://")):
            a["uri"] = uri
        return a

    # 1. Library artists (weakest signal) and their MA genres.
    lib = await _paged(ma, "music/artists/library_items")
    full = await _gather_limited([ma("music/item_by_uri", uri=a["uri"]) for a in lib])
    for a, f in zip(lib, full):
        e = entry(a.get("name") or "", a.get("uri"))
        e["library"] = 1
        g = _genres_of(f)
        if g:
            e["genres"], e["genre_source"] = g, "ma"

    # 2. Play history (strongest): newest first, recency-weighted. Track and
    # album items need a full fetch for their artists.
    hist = await ma("music/recently_played_items", limit=HISTORY_LIMIT) or []
    need = [h for h in hist if h.get("media_type") in ("track", "album")]
    fetched = dict(zip([h["uri"] for h in need],
                       await _gather_limited([ma("music/item_by_uri", uri=h["uri"]) for h in need])))
    station_plays: dict[str, float] = {}
    for n, h in enumerate(hist):
        w = HISTORY_DECAY ** n
        mt = h.get("media_type")
        if mt == "artist":
            entry(h.get("name") or "", h.get("uri"))["history"] += w
        elif mt in ("track", "album"):
            f = fetched.get(h["uri"])
            for a in (f.get("artists") or [])[:2] if isinstance(f, dict) else []:
                if a.get("name"):
                    entry(a["name"], a.get("uri"))["history"] += w
        elif mt == "radio":
            station_plays[music_resolve.fold(h.get("name"))] = station_plays.get(
                music_resolve.fold(h.get("name")), 0.0) + w

    # 3. Stations: a station named after an artist marks that artist.
    radios = await _paged(ma, "music/radios/library_items")
    stations = []
    for r in radios:
        name = r.get("name") or ""
        stations.append((r.get("uri"), name, r.get("provider"), station_plays.get(music_resolve.fold(name), 0.0)))
        seed = re.sub(r"\s+radio$", "", name, flags=re.IGNORECASE).strip()
        k = music_resolve.fold(seed)
        if k in artists:
            artists[k]["station"] = 1

    # 4. Genres for the history artists MA didn't tag: MA first, then the
    # model, cached in `tags` so each artist is asked about once.
    con = db(path)
    try:
        cached = {r["key"]: json.loads(r["genres"]) for r in con.execute("SELECT key, genres FROM tags")}
        untagged = [a for a in artists.values() if not a["genres"] and a["history"] > 0 and a["uri"]
                    and not a["uri"].startswith("library://")]
        more = await _gather_limited([ma("music/item_by_uri", uri=a["uri"]) for a in untagged])
        for a, f in zip(untagged, more):
            g = _genres_of(f)
            if g:
                a["genres"], a["genre_source"] = g, "ma"
        for k, a in artists.items():
            if not a["genres"] and k in cached:
                a["genres"], a["genre_source"] = cached[k], "model"
        top = sorted((a for k, a in artists.items() if not a["genres"] and k not in cached),
                     key=lambda a: (a["history"], a["station"], a["library"]), reverse=True)[:TAG_TOP_N]
        top = [a for a in top if a["history"] > 0 or a["station"]] + \
              [a for a in top if not (a["history"] > 0 or a["station"])][:max(0, TAG_TOP_N // 3)]
        tagged = await tagger([a["name"] for a in top]) if top else {}
        now = time.time()
        for a in top:
            g = tagged.get(a["name"])
            if g is not None:
                con.execute("INSERT OR REPLACE INTO tags VALUES (?,?,?,?)",
                            (music_resolve.fold(a["name"]), a["name"], json.dumps(g), now))
                if g:
                    a["genres"], a["genre_source"] = g, "model"

        # 5. Score and store.
        max_hist = max((a["history"] for a in artists.values()), default=0.0) or 1.0
        with con:
            con.execute("DELETE FROM artists")
            con.execute("DELETE FROM stations")
            for k, a in artists.items():
                sc = W_HISTORY * a["history"] / max_hist + W_STATION * a["station"] + W_LIBRARY * a["library"]
                con.execute("INSERT INTO artists VALUES (?,?,?,?,?,?,?,?,?)",
                            (k, a["name"], a["uri"], json.dumps(a["genres"]), a["genre_source"],
                             a["history"], a["station"], a["library"], sc))
            con.executemany("INSERT OR REPLACE INTO stations VALUES (?,?,?,?)", [s for s in stations if s[0]])
            summary = {
                "built_at": time.time(), "seconds": round(time.time() - t0, 1),
                "artists": len(artists), "with_genres": sum(1 for a in artists.values() if a["genres"]),
                "history_items": len(hist), "stations": len(stations), "model_tagged": len(tagged),
            }
            con.execute("INSERT OR REPLACE INTO meta VALUES ('last_build', ?)", (json.dumps(summary),))
    finally:
        con.close()
    logger.info("taste profile built: %s", summary)
    return summary


def last_build(path: Path | None = None) -> dict | None:
    with db(path) as con:
        r = con.execute("SELECT v FROM meta WHERE k='last_build'").fetchone()
    return json.loads(r["v"]) if r else None


def profile(limit: int = 30, path: Path | None = None) -> dict:
    """Top of the learned profile, for GET /music/taste/profile."""
    with db(path) as con:
        fb = feedback_totals(con)
        rows = con.execute("SELECT * FROM artists").fetchall()
        stations = con.execute("SELECT name, plays FROM stations ORDER BY plays DESC, name LIMIT 20").fetchall()
    arts = sorted(({"name": r["name"], "score": round(r["score"] + fb.get("artist:" + r["key"], 0.0), 2),
                    "genres": json.loads(r["genres"]), "genre_source": r["genre_source"]} for r in rows),
                  key=lambda a: a["score"], reverse=True)
    return {"last_build": last_build(path), "artists": arts[:limit], "stations": [dict(r) for r in stations]}


def _next_build_time(now: datetime) -> datetime:
    hh, mm = (int(x) for x in TASTE_BUILD_AT.split(":"))
    nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return nxt if nxt > now else nxt + timedelta(days=1)


_BUILD_LOCK = asyncio.Lock()


async def rebuild(ma, force: bool = True) -> dict | None:
    """One build at a time; with force=False, skip when the profile is fresh."""
    async with _BUILD_LOCK:
        lb = last_build()
        if not force and lb and time.time() - lb["built_at"] < 20 * 3600:
            return None
        return await build_profile(ma)


async def refresh_loop(ma) -> None:
    """Build once at startup if the profile is missing or a day stale, then
    nightly at MUSIC_TASTE_BUILD_AT local time. A failed build keeps the old
    profile and retries at the next slot."""
    await asyncio.sleep(120)                     # let MA and the LLMs settle after a restart
    while True:
        try:
            await rebuild(ma, force=False)
        except Exception as e:
            logger.error("taste profile build failed (keeping the previous one): %s", str(e)[:300])
        now = datetime.now(ZoneInfo(TASTE_TZ))
        await asyncio.sleep(max(60.0, (_next_build_time(now) - now).total_seconds()))


# ── genre picks (MUSIC_TASTE_AGENT_PLAN step 5) ─────────────────────────────
# "Play some deep house" is not a title to search for — MA played an album
# literally called "Some Chill Music". A genre request picks from what this
# household plays, filtered to the genre, and announces the pick.

_FILLER = re.compile(r"^(?:(?:some|a\s+bit\s+of|a\s+little(?:\s+bit\s+of)?|any|more|good|nice|great|"
                     r"something|anything|me|us|play)\s+)+", re.IGNORECASE)
_GENERIC = re.compile(r"(?:\s+|^)(?:music|songs?|tunes|stuff|tracks|vibes?|mix|playlist|please)$", re.IGNORECASE)
GENERIC_WORDS = {"", "music", "something", "anything", "songs", "tunes", "stuff"}

# Enough to recognize a genre request before the learned vocabulary exists.
BUILTIN_GENRES = {
    "ambient", "blues", "bluegrass", "bossa nova", "chillout", "chill", "classical", "country", "dance",
    "deep house", "disco", "downtempo", "drum and bass", "dubstep", "edm", "electronic", "electro swing",
    "folk", "funk", "gospel", "hip hop", "house", "indie", "indie rock", "jazz", "j pop", "k pop", "latin",
    "lofi", "lo fi", "lounge", "metal", "new age", "opera", "pop", "punk", "r and b", "rap", "reggae",
    "rock", "classic rock", "soul", "soundtrack", "synthwave", "techno", "trance", "trip hop", "world",
    "acid jazz", "smooth jazz", "progressive rock", "grunge", "shoegaze", "video game soundtrack",
}

RANDOM = __import__("random").Random()


def genre_phrase(text: str | None) -> str:
    """'some deep house chill music' → 'deep house chill'; 'music' → ''."""
    s = music_resolve.fold(text)
    prev = None
    while s != prev:
        prev = s
        s = _FILLER.sub("", s).strip()
        s = _GENERIC.sub("", s).strip()
    return "" if s in GENERIC_WORDS else s


def _tokens(s: str) -> set[str]:
    return set(music_resolve.fold(s).split())


def term_matches(term: str, genre: str) -> bool:
    """'deep house' ~ 'deep house'; 'trip hop' ~ 'trip-hop'; 'house' ~ 'deep house'."""
    t, g = _tokens(term), _tokens(genre)
    return bool(t) and bool(g) and (t <= g or g <= t)


def genre_terms(phrase: str, taste_doc: dict) -> list[str]:
    """The household's meaning of the phrase first, then the phrase itself."""
    terms: list[str] = []
    gmap = taste_doc.get("genres") or {}
    if phrase in gmap:
        terms += gmap[phrase]
    else:
        for word, meaning in gmap.items():                  # "deep chill" → chill's meaning too
            if word and re.search(rf"\b{re.escape(word)}\b", phrase):
                terms += meaning
    if phrase and phrase not in terms:
        terms.append(phrase)
    return list(dict.fromkeys(t for t in terms if t))


def vocabulary(con: sqlite3.Connection, taste_doc: dict) -> set[str]:
    vocab = set(BUILTIN_GENRES) | set((taste_doc.get("genres") or {}).keys())
    for r in con.execute("SELECT genres FROM artists WHERE genres != '[]'"):
        vocab.update(music_resolve.fold(g) for g in json.loads(r["genres"]))
    return vocab


def is_genre(phrase: str, con: sqlite3.Connection, taste_doc: dict) -> bool:
    if not phrase:
        return False
    vocab = vocabulary(con, taste_doc)
    if phrase in vocab:
        return True
    # every word of the phrase is part of some genre: "deep chill house"
    words = phrase.split()
    return all(any(w in v.split() for v in vocab) for w in words)


class Pick:
    __slots__ = ("kind", "uri", "label", "key", "score", "media_type", "radio_mode", "provider", "name")

    def __init__(self, kind, uri, label, key, score, media_type, radio_mode=False, provider=None, name=None):
        self.kind, self.uri, self.label, self.key, self.score = kind, uri, label, key, score
        self.media_type, self.radio_mode, self.provider = media_type, radio_mode, provider
        self.name = name or label

    def play_data(self) -> dict:
        """play_media service data. An artist known only from Pandora history
        has a pandora://artist URI, which can't seed artist radio — play it
        by name and let MA find it in the library or YouTube Music."""
        mid = self.name if self.kind == "artist" and self.uri.startswith("pandora://") else self.uri
        data = {"media_id": mid, "media_type": self.media_type}
        if self.radio_mode:
            data["radio_mode"] = True
        return data

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}

    def __repr__(self):
        return f"Pick({self.kind} {self.label!r} {self.score:.2f})"


LOVE_BONUS = 2.0
# Artist picks as MA artist radio (a mix seeded on the artist) or as the
# artist's own tracks, shuffled. Radio took 11.3 s to start vs 1.0 s plain
# (measured 2026-10-21, basement Voice PE) — past the voice budget once the
# LLM's ~5 s is added (tenet 12), so plain is the default.
ARTIST_RADIO = os.getenv("MUSIC_GENRE_ARTIST_RADIO", "0") == "1"


def local_genre_picks(terms: list[str], con: sqlite3.Connection, taste_doc: dict) -> list[Pick]:
    """Artists tagged with the genre (as artist radio) and our own stations
    named for it. An empty `terms` means no genre: the household's top artists."""
    fb = feedback_totals(con)
    love, never = taste_doc.get("love") or [], taste_doc.get("never") or []
    picks: list[Pick] = []
    for r in con.execute("SELECT * FROM artists"):
        genres = json.loads(r["genres"])
        if terms and not any(term_matches(t, g) for t in terms for g in genres):
            continue
        if not r["uri"] or matches_any(r["name"], never) or any(matches_any(g, never) for g in genres):
            continue
        sc = r["score"] + fb.get("artist:" + r["key"], 0.0)
        if matches_any(r["name"], love):
            sc += LOVE_BONUS
        picks.append(Pick("artist", r["uri"], f"{r['name']} radio" if ARTIST_RADIO else r["name"],
                          "artist:" + r["key"], sc, "artist", radio_mode=ARTIST_RADIO, name=r["name"]))
    for r in con.execute("SELECT * FROM stations"):
        if not terms or not any(term_matches(t, re.sub(r"(?i)\s+radio$", "", r["name"])) for t in terms):
            continue
        if matches_any(r["name"], never):
            continue
        picks.append(Pick("station", r["uri"], r["name"], "uri:" + r["uri"],
                          1.0 + 2.0 * r["plays"] + fb.get("uri:" + r["uri"], 0.0), "radio", provider=r["provider"]))
    if not terms:                                     # "play some music": the top of the profile
        picks = [p for p in picks if p.kind == "artist"]
    return sorted(picks, key=lambda p: p.score, reverse=True)


# Search results rank below anything the household has played or owns
# (a library-only artist scores W_LIBRARY = 0.3): the first live deep house
# request played a stranger's YTM playlist over our own deep house artists
# (2026-10-21). They still serve a genre the household has nothing in.
STATION_BASE  = 0.28     # an existing station found by search
PLAYLIST_BASE = 0.25
SEED_BASE     = 0.15     # a Pandora station that playing would create


def provider_genre_picks(terms: list[str], results: list[dict | None], con: sqlite3.Connection,
                         taste_doc: dict) -> list[Pick]:
    """Genre playlists and stations from an MA search (YTM, Pandora). Only
    items whose name carries the genre — a search result is not a match.
    A Pandora `seed:` item is a station the account doesn't have; playing
    it creates one, so it ranks below everything that already exists."""
    fb = feedback_totals(con) if con else {}
    never = taste_doc.get("never") or []
    out: dict[str, Pick] = {}
    for res in results:
        for key, mt in (("playlists", "playlist"), ("radio", "radio")):
            for it in (res or {}).get(key) or []:
                uri, name = it.get("uri"), it.get("name") or ""
                if not uri or uri in out or matches_any(name, never):
                    continue
                if not any(term_matches(t, name) or _tokens(t) <= _tokens(name) for t in terms):
                    continue
                provider = (it.get("provider") or "").split("--")[0]
                seed = str(it.get("item_id", "")).startswith("seed:")
                base = SEED_BASE if seed else (STATION_BASE if mt == "radio" else PLAYLIST_BASE)
                out[uri] = Pick("playlist" if mt == "playlist" else "station", uri, name, "uri:" + uri,
                                base + fb.get("uri:" + uri, 0.0), mt, provider=provider)
    return list(out.values())


def choose(picks: list[Pick], rng=None, top: int = 3) -> list[Pick]:
    """Order to try: one of the top `top`, weighted by score, then the rest
    in score order. A DJ that plays the same record every time isn't one."""
    if not picks:
        return []
    rng = rng or RANDOM
    best: dict[str, Pick] = {}                 # one entry per URI — library and search can both find it
    for p in picks:
        if p.uri not in best or p.score > best[p.uri].score:
            best[p.uri] = p
    ranked = sorted(best.values(), key=lambda p: p.score, reverse=True)
    head = ranked[:top]
    weights = [max(p.score, 0.05) for p in head]
    first = rng.choices(head, weights=weights, k=1)[0]
    return [first] + [p for p in ranked if p is not first]


def record_pick(entity: str, phrase: str, pick: Pick, path: Path | None = None) -> int:
    with db(path) as con:
        cur = con.execute("INSERT INTO picks (ts, entity, phrase, key, uri, label, outcome) VALUES (?,?,?,?,?,?,?)",
                          (time.time(), entity, phrase, pick.key, pick.uri, pick.label, None))
        return cur.lastrowid


def set_pick_outcome(pick_id: int, outcome: str, delta: float, key: str, path: Path | None = None) -> bool:
    """First outcome wins: a pick the watcher already scored is not scored
    again by a later "not that" (or the other way round)."""
    with db(path) as con:
        if con.execute("UPDATE picks SET outcome=? WHERE id=? AND outcome IS NULL",
                       (outcome, pick_id)).rowcount != 1:
            return False
        con.execute("INSERT INTO feedback VALUES (?,?,?,?)", (key, delta, outcome, time.time()))
    logger.info("taste pick %d %s → %s %+.2f", pick_id, outcome, key, delta)
    return True
