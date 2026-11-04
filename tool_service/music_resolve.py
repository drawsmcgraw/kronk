"""Named-request resolution for /music (docs/plans/MUSIC_TASTE_AGENT_PLAN.md).

Before 2026-10-21 /music handed the query string to HA's
`music_assistant.play_media` and MA resolved it literally. That failed on
three shapes (INVESTIGATION_2026-10-19_ytmusic_album_misses.md):

- a provider hint left in the query — "X on YouTube Music", "X from
  Atlas" — which MA searches for as part of the title and finds nothing;
- an STT or typing misspelling — "Destiny Waving" for "Destiny Waiving",
  "Kuru Dorf Meister" for "Kruder & Dorfmeister" — where MA's search has
  no tolerance once the artist words are in the query;
- a pasted YouTube Music link, which MA can play directly by URI.

This module is pure: `normalize` cleans a query, `rank` scores MA search
results against it. main.py does the I/O (the MA search, the play, the
verify). Scoring is stdlib difflib on folded strings — no phonetic
library; the space-collapsed comparison is what catches split/merged
words ("kurudorfmeister" vs "kruderdorfmeister").
"""
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# Provider hint → MA provider domain. "Atlas" is the Synology NAS that
# backs the local-files provider.
_PROVIDERS = {
    "ytmusic": r"(?:youtube\s*music|you\s*tube\s*music|yt\s*music|youtube|you\s*tube)",
    "pandora": r"pandora",
    "spotify": r"spotify",
    "filesystem_local": r"(?:atlas|the\s+nas|nas|my\s+library|the\s+library|local\s+files|my\s+collection)",
}
_HINT_RE = re.compile(
    r"[\s,]+(?:on|from|via|using|through)\s+(?:the\s+)?(?:"
    + "|".join(f"(?P<{k}>{v})" for k, v in _PROVIDERS.items())
    + r")[\s.!?]*$",
    re.IGNORECASE,
)

# YouTube Music links → MA URIs. A domain-only URI (`ytmusic://…`) resolves
# to the configured instance (verified 2026-10-21 against MA 2.11).
_URL_RES = [
    (re.compile(r"music\.youtube\.com/playlist\?(?:.*&)?list=([\w-]+)"), "playlist"),
    (re.compile(r"music\.youtube\.com/browse/(MPREb_[\w-]+)"), "album"),
    (re.compile(r"music\.youtube\.com/(?:channel|browse)/(UC[\w-]+)"), "artist"),
    (re.compile(r"(?:music\.youtube\.com|(?:www\.)?youtube\.com)/watch\?(?:.*&)?v=([\w-]+)"), "track"),
    (re.compile(r"youtu\.be/([\w-]+)"), "track"),
]

_BY_RE = re.compile(r"^(?P<name>.+)\s+by\s+(?:the\s+)?(?:artist\s+|band\s+)?(?P<artist>.+)$", re.IGNORECASE)


@dataclass
class Query:
    text: str                         # cleaned query, as MA should search it
    provider: str | None = None       # provider domain the user named, if any
    uri: str | None = None            # direct MA URI from a pasted link
    uri_type: str | None = None       # its media type
    name: str | None = None           # "X" of "X by Y"
    artist: str | None = None         # "Y" of "X by Y"


def normalize(query: str, artist: str | None = None) -> Query:
    """Clean one spoken/typed query. Never raises; worst case returns it as-is."""
    raw = (query or "").strip()
    for rx, kind in _URL_RES:
        m = rx.search(raw)
        if m:
            return Query(text=raw, provider="ytmusic", uri=f"ytmusic://{kind}/{m.group(1)}", uri_type=kind)
    text, provider = raw, None
    # Strip repeatedly: "X from YouTube Music on Pandora" is nonsense, but
    # "X, on YouTube" then a stray period should both go.
    while True:
        m = _HINT_RE.search(text)
        if not m or m.start() == 0:
            break
        provider = provider or next(k for k in _PROVIDERS if m.group(k))
        text = text[:m.start()].rstrip(" ,")
    q = Query(text=text, provider=provider)
    if artist:
        q.name, q.artist = text, artist.strip()
    else:
        m = _BY_RE.match(text)
        if m:
            q.name, q.artist = m.group("name").strip(), m.group("artist").strip()
    return q


# ── scoring ──────────────────────────────────────────────────────────────────

def fold(s: str | None) -> str:
    """Lowercase, strip accents and punctuation, '&' → 'and', drop a leading 'the'."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[4:] if s.startswith("the ") else s


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def similarity(a: str | None, b: str | None) -> float:
    """0..1. Max of the token-level and space-collapsed ratios, with 'and'
    dropped from the collapsed form ('Kruder & Dorfmeister' ≈ 'Kuru Dorf Meister')."""
    fa, fb = fold(a), fold(b)
    if not fa or not fb:
        return 0.0
    if fa == fb:
        return 1.0
    squash = lambda s: re.sub(r"\band\b|\s", "", s)  # noqa: E731
    return max(_ratio(fa, fb), _ratio(squash(fa), squash(fb)))


def prefix_similarity(q: str | None, name: str | None) -> float:
    """`q` against the same number of leading words of `name`, discounted —
    'Hotel Costes 3' vs 'Hôtel Costes 3 - Etage 3'. Never beats an exact match."""
    fq, fn = fold(q).split(), fold(name).split()
    if not fq or len(fn) <= len(fq):
        return 0.0
    return 0.92 * similarity(" ".join(fq), " ".join(fn[:len(fq)]))


@dataclass
class Candidate:
    uri: str
    media_type: str
    name: str
    artists: list[str] = field(default_factory=list)
    providers: set[str] = field(default_factory=set)
    in_library: bool = False
    score: float = 0.0

    @property
    def artist(self) -> str | None:
        return " and ".join(self.artists) if self.artists else None


# Small preferences when two candidates score the same: a whole album over
# its title track, the artist over a same-named album.
_TYPE_BONUS = {"artist": 0.02, "album": 0.015, "playlist": 0.01, "track": 0.0, "radio": 0.0}
PROVIDER_BONUS = 0.05     # the user named this provider
LIBRARY_BONUS = 0.03      # we own it (a near-tie goes to the household's copy)


def candidates_from_search(result: dict | None) -> list[Candidate]:
    """MA `music/search` result → flat, URI-deduplicated candidates."""
    out: dict[str, Candidate] = {}
    for key in ("artists", "albums", "tracks", "playlists", "radio"):
        for it in (result or {}).get(key) or []:
            uri = it.get("uri")
            if not uri or uri in out:
                continue
            providers = {it.get("provider")} | {m.get("provider_domain") for m in it.get("provider_mappings") or []}
            providers = {p.split("--")[0] for p in providers if p}
            out[uri] = Candidate(
                uri=uri,
                media_type=it.get("media_type") or key.rstrip("s"),
                name=it.get("name") or "",
                artists=[a.get("name", "") for a in it.get("artists") or [] if a.get("name")],
                providers=providers,
                in_library=uri.startswith("library://"),
            )
    return list(out.values())


def _digits(s: str | None) -> set[str]:
    return set(re.findall(r"\d+", fold(s)))


def name_similarity(q: str | None, name: str | None) -> float:
    """Title match. A number the query names and the title lacks is a
    different record — 'Hotel Costes 3' is not 'Hôtel Costes' or '… 6'."""
    s = max(similarity(q, name), prefix_similarity(q, name))
    missing = _digits(q) - _digits(name)
    return s * 0.8 if missing else s


def word_coverage(q: str | None, c: Candidate) -> float:
    """How well the candidate covers every word of the request: the worst
    query word's best match among the candidate's name and artist words."""
    # "by" and "and" are glue, not content: "90s, 2000's and today's hits"
    # names "90s, 2000s, Today's Hits Radio" (similarity() drops "and" too).
    words = [w for w in fold(q).split() if w not in ("by", "and")]
    pool = fold(" ".join([c.name] + c.artists)).split()
    if not words or not pool:
        return 0.0
    return min(max(_ratio(w, p) for p in pool) for w in words)


# With no artist to confirm it, a near match must cover every word:
# "Destiny Waving" alone scored 0.92 against "Destiny Wave" by a stranger
# ("waving"→"wave" 0.80); "Destiny Waiving" covers it at 0.92.
COVERAGE_MIN = 0.85


def score(c: Candidate, q: Query) -> float:
    full = q.text
    s = name_similarity(full, c.name)
    if c.artists:
        # Discounted when the request named an artist: the split comparison
        # below is the precise one, and this one let the right artist pull a
        # wrong title over the line.
        k = 0.9 if q.artist else 1.0
        s = max(s, k * similarity(full, f"{c.name} {c.artists[0]}"),
                k * similarity(full, f"{c.name} by {c.artists[0]}"))
    if q.artist:
        if c.media_type == "artist":
            s = max(s, 0.85 * similarity(q.artist, c.name))
        elif c.artists:
            # Collapsed credit too: 'Ulrich Schnauss and Mark Peters'.
            art_s = max([similarity(q.artist, a) for a in c.artists] + [similarity(q.artist, c.artist)])
            # Multiplicative: the right artist cannot carry the wrong title
            # ('Far Away Trains Passing By' for 'Destiny Waving').
            s = max(s, name_similarity(q.name, c.name) * (0.75 + 0.25 * art_s))
    s += _TYPE_BONUS.get(c.media_type, 0.0)
    if q.provider and q.provider in c.providers:
        s += PROVIDER_BONUS
    if c.in_library:
        s += LIBRARY_BONUS
    if not q.artist and fold(full) != fold(c.name) and word_coverage(full, c) < COVERAGE_MIN:
        s = min(s, PLAY_MIN_SCORE - 0.01)       # after the bonuses, so none can lift it back
    return s


def rank(cands: list[Candidate], q: Query, media_type: str | None = None) -> list[Candidate]:
    """Scored, best first. A typed request only considers that type."""
    pool = [c for c in cands if not media_type or c.media_type == media_type]
    for c in pool:
        c.score = score(c, q)
    return sorted(pool, key=lambda c: c.score, reverse=True)


# Below this a match is a guess, not a resolution. Tuned on the logged
# misses (tests/test_music_resolve.py): every real target scores ≥ 0.82,
# gibberish ("zzqx florbent nonexistent") stays under 0.4.
PLAY_MIN_SCORE = 0.75
