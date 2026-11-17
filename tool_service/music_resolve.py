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

try:
    from . import config_loader
except ImportError:
    import config_loader


def policy() -> "config_loader.Resolver":
    """The resolver thresholds in effect: config/music.yaml resolver
    (sprint M21). Read at use, so a reload applies to the next request."""
    return config_loader.current().music.resolver

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

# Spoken numbers → digits. STT writes "ninety's two thousands" where the
# catalogue says "90s, 2000s" (INVESTIGATION_2026-11-07_ma_pandora_and_ui.md:
# the spelled-out query scored 0.51–0.63 against "90s, 2000s, Today's Hits
# Radio", the digit form 0.91), and the model passing the query does not
# reliably convert them. The digit form is scored *alongside* the query as
# said, never instead of it, so a name that is spelled out for real
# ("Three Dog Night", "Twenty One Pilots") still matches itself.
_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split(), start=2)}
_TENS_STEM = {"twent": 20, "thirt": 30, "fort": 40, "fift": 50, "sixt": 60,
              "sevent": 70, "eight": 80, "ninet": 90}
_U19 = "|".join(sorted(_UNITS, key=len, reverse=True))
_U9 = "one|two|three|four|five|six|seven|eight|nine"
_T = "|".join(_TENS)
_SEP = r"[\s-]+"
_NUMERAL_RULES = [
    # "two thousands", "the two thousand's" → 2000s
    (re.compile(r"\btwo" + _SEP + r"thousand'?s\b", re.I), lambda m: "2000s"),
    # "ninety's", "nineties", "ninetys" → 90s
    (re.compile(r"\b(" + "|".join(_TENS_STEM) + r")(?:ies|y'?s)\b", re.I),
     lambda m: f"{_TENS_STEM[m.group(1).lower()]}s"),
    # "nineteen eighty four" → 1984, "nineteen ninety" → 1990
    (re.compile(r"\bnineteen" + _SEP + r"(" + _T + r")(?:" + _SEP + r"(" + _U9 + r"))?\b", re.I),
     lambda m: str(1900 + _TENS[m.group(1).lower()] + (_UNITS[m.group(2).lower()] if m.group(2) else 0))),
    # "two thousand (and) seven" → 2007
    (re.compile(r"\btwo" + _SEP + r"thousand(?:" + _SEP + r"and)?" + _SEP + r"(" + _U19 + r")\b", re.I),
     lambda m: str(2000 + _UNITS[m.group(1).lower()])),
    (re.compile(r"\btwo" + _SEP + r"thousand\b", re.I), lambda m: "2000"),
    # "twenty one" → 21, then the lone words
    (re.compile(r"\b(" + _T + r")" + _SEP + r"(" + _U9 + r")\b", re.I),
     lambda m: str(_TENS[m.group(1).lower()] + _UNITS[m.group(2).lower()])),
    (re.compile(r"\b(" + _T + r")\b", re.I), lambda m: str(_TENS[m.group(1).lower()])),
    (re.compile(r"\b(" + _U19 + r")\b", re.I), lambda m: str(_UNITS[m.group(1).lower()])),
]


# A radio request in the user's own words: "play <name> radio [on Pandora]
# [in the kitchen]" or "play the radio station <name> [in the kitchen]".
# "radio"/"station" must close the name, or "radio station"/"station" open
# it, so "play Radio Ga Ga by Queen" is not a station request.
_VERB = r"^\W*(?:(?:ok(?:ay)?|hey)\s+\w+[,\s]+)?(?:please\s+)?(?:play|put\s+on|listen\s+to|start|turn\s+on)\s+"
# After the station word: a place or provider ("on Pandora", "in the
# kitchen"), or the end of the sentence — anything after a full stop is
# other speech the transcript caught ("Play Anime Pop Radio. That's probably
# about it…", INVESTIGATION_2026-11-08_anime_pop_radio_hang.md).
_TAIL = r"(?:\s+(?:on|from|via|in|using)\b.*|[.!?]\s.*)?[\s.!?]*$"
_RADIO_AFTER = re.compile(_VERB + r"(?:the\s+|my\s+|some\s+)?(?P<name>.+?),?\s+(?:radio(?:\s+station)?|station)" + _TAIL, re.I)
_RADIO_BEFORE = re.compile(_VERB + r"(?:the\s+)?(?:radio\s+station|station)\s+(?P<name>.+?)"
                           r"(?:\s+(?:in|on)\s+(?:the\s+)?\S.*)?[\s.!?]*$", re.I)


def radio_name(said: str | None) -> str | None:
    """The station name when `said` asks for a radio station, else None."""
    for rx in (_RADIO_AFTER, _RADIO_BEFORE):
        m = rx.match(said or "")
        if m and m.group("name").strip(" ,"):
            return m.group("name").strip(" ,")
    return None


def numerals(text: str | None) -> str:
    """Spoken numbers in `text` written as digits; everything else untouched."""
    out = text or ""
    for rx, fn in _NUMERAL_RULES:
        out = rx.sub(fn, out)
    return out


_BY_RE = re.compile(r"^(?P<name>.+)\s+by\s+(?:the\s+)?(?:artist\s+|band\s+)?(?P<artist>.+)$", re.IGNORECASE)


@dataclass
class Query:
    text: str                         # cleaned query, as MA should search it
    provider: str | None = None       # provider domain the user named, if any
    uri: str | None = None            # direct MA URI from a pasted link
    uri_type: str | None = None       # its media type
    name: str | None = None           # "X" of "X by Y"
    artist: str | None = None         # "Y" of "X by Y"

    def digits(self) -> "Query | None":
        """The same query with spoken numbers as digits, or None when it has none."""
        alt = Query(text=numerals(self.text), provider=self.provider, uri=self.uri,
                    uri_type=self.uri_type, name=self.name and numerals(self.name),
                    artist=self.artist and numerals(self.artist))
        return alt if (alt.text, alt.name, alt.artist) != (self.text, self.name, self.artist) else None


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


# Small preferences when two candidates score the same (music.yaml
# resolver): type_bonus, a whole album over its title track and the artist
# over a same-named album; provider_bonus, the user named this provider;
# library_bonus, we own it (a near-tie goes to the household's copy).


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


# With no artist to confirm it, a near match must cover every word
# (music.yaml resolver.coverage_min): "Destiny Waving" alone scored 0.92
# against "Destiny Wave" by a stranger ("waving"→"wave" 0.60 per word);
# "Destiny Waiving" covers it at 0.92. 0.80, not 0.85: one misheard vowel
# in a six-letter word is 0.83 ("costas"→"costes", the "Hotel Costas"
# playlist miss, 2026-11-08).


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
    p = policy()
    if c.media_type in type(p.type_bonus).model_fields:
        s += getattr(p.type_bonus, c.media_type)
    if q.provider and q.provider in c.providers:
        s += p.provider_bonus
    if c.in_library:
        s += p.library_bonus
    if not q.artist and fold(full) != fold(c.name) and word_coverage(full, c) < p.coverage_min:
        s = min(s, p.play_min_score - 0.01)     # after the bonuses, so none can lift it back
    return s


def rank(cands: list[Candidate], q: Query, media_type: str | None = None,
         also: list[Query] | tuple = ()) -> list[Candidate]:
    """Scored, best first. A typed request only considers that type. Each
    candidate scores its best against `q`, the extra readings in `also`
    (the name cut from the user's own sentence), and their digit forms."""
    pool = [c for c in cands if not media_type or c.media_type == media_type]
    queries = [x for q0 in (q, *also) for x in (q0, q0.digits()) if x is not None]
    for c in pool:
        c.score = max(score(c, x) for x in queries)
    return sorted(pool, key=lambda c: c.score, reverse=True)


# Below music.yaml resolver.play_min_score a match is a guess, not a
# resolution. Tuned on the logged misses (tests/test_music_resolve.py):
# every real target scores ≥ 0.82, gibberish ("zzqx florbent nonexistent")
# stays under 0.4.


# ── the sentence parser (sprint M11) ─────────────────────────────────────────
# POST /music/say reads the user's own sentence instead of a model's
# arguments. parse_sentence() is pure: the sentence, the real player and
# room names (HA's players template), nothing else. It decides no reading:
# the decision order (music_decide.py) does that from what it finds here.
#
# Rooms and players are matched from the right-hand end of the sentence
# against real names, never with a greedy "(in|on) {room}" slot: the
# blueprint's slot cut "The Girl with the Sun in Her Head by Orbital" into
# a title "The Girl with the Sun" and a room "Her Head by Orbital"
# (voice-music-control.md, 2026-09-16). The parser does not trim messy
# transcripts (sprint M14 sends those to the coordinator); radio_name()
# keeps cutting a station at a full stop.

def parser_words() -> "config_loader.Parser":
    """The parser's words in effect: config/music.yaml parser."""
    return config_loader.current().music.parser


def room_key(s: str | None) -> str:
    """Case/punctuation-insensitive key: 'the Office speaker' → 'office'.
    (main.resolve_players compares names with this.)"""
    s = " ".join((s or "").lower().replace("-", " ").replace("_", " ").split())
    s = re.sub(r"^(the|my)\s+", "", s)
    s = re.sub(r"\s+(speakers?|players?|room)$", "", s)
    return s


# Lead-ins the request verb may follow: the routing pin's (_MUSIC_RE) and
# radio_name()'s ("okay nabu, ...").
_LEAD = (r"^\W*(?:(?:ok(?:ay)?|hey)\s+\w+[,\s]+)?"
         r"(?:(?:please|kronk,?|hey\s+kronk,?|tell\s+kronk\s+to|can\s+you|could\s+you)\s+)*")
_REQUEST_VERB_RE = re.compile(
    _LEAD + r"(?P<verb>play|put\s+on|throw\s+on|listen\s+to|shuffle|queue\s+up|start|turn\s+on)\b[\s,:]*(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL)
# What routing._MUSIC_RE excludes (criterion 3; tests/test_music_parse.py
# checks the lists against the routing pattern): the news anywhere; a game,
# video, movie or podcast after a bare "play" with no music noun.
NOT_MUSIC_ALWAYS = ("news",)
NOT_MUSIC_AFTER_PLAY = ("game", "games", "video", "videos", "movie", "movies", "podcast")
MUSIC_NOUNS = ("radio", "station", "music", "song", "songs", "track", "tracks", "album", "albums",
               "playlist", "playlists", "artist", "mix", "pandora", "youtube", "spotify", "atlas", "the nas")
# Verbs that only ever mean music; "play" may mean a game, "start" a timer.
_MUSIC_ONLY_VERBS = {"put on", "throw on", "listen to", "shuffle", "queue up"}
_PROVIDER_WORD = r"(?:youtube\s*music|you\s*tube\s*music|yt\s*music|youtube|you\s*tube|pandora|spotify|atlas|the\s+nas|nas)"
_DEVICE_NOUNS = {"speaker", "speakers", "player", "players", "group", "room"}
_ROOM_PREP_RE = re.compile(r"(?:^|[\s,])(?:in|on)\s+", re.IGNORECASE)
_TAIL_PUNCT = " \t\n,.!?;:"
# Words after the request that ask for nothing: "please", "to play it",
# "for me" ("start an orbital radio to play on YouTube music").
_PLEASE_RE = re.compile(r"[\s,]+(?:please|(?:and|to)\s+play(?:\s+it)?|for\s+(?:me|us))$", re.IGNORECASE)


def _has_word(text: str, words) -> bool:
    return any(re.search(rf"\b{re.escape(w)}\b", text, re.IGNORECASE) for w in words)


def _provider_of(word: str) -> str:
    return next(k for k, v in _PROVIDERS.items() if re.fullmatch(v, word.strip(), re.IGNORECASE))


@dataclass
class Parsed:
    """What a sentence says, before any reading is decided."""
    sentence: str
    music: bool = True
    reason: str | None = None          # why it is not a music request
    verb: str | None = None
    words: str = ""                    # the request: no verb, room, provider or type word
    type: str | None = None            # media type a type word named
    type_word: str | None = None
    style_marker: str | None = None
    name: str | None = None            # X of "X by Y", else the words
    artist: str | None = None          # Y of "X by Y"
    by_generic: bool = False           # "songs by Y", "music by Y": Y is the artist asked for
    shuffle: bool = False
    provider: str | None = None        # provider domain the sentence named
    room: str | None = None            # the real area or player name matched
    room_kind: str | None = None       # area | player | unknown
    room_said: str | None = None       # the words that named it
    radio_name: str | None = None      # radio_name(sentence), when it names a station

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, "", False) or k == "music"}


def match_room(said: str, players: list[dict], min_score: float) -> tuple[str, str] | None:
    """(real name, 'player' | 'area') for a room or speaker clause, or None.
    Exact name first, then exact area (the blueprint's order), then the
    clause as part of a name ("the sonos" → "Sonos Move Derp"), then a near
    match. Never a name inside the clause: "the kitchen tapes" is a title,
    not the Kitchen."""
    key = room_key(said)
    if not key:
        return None
    names = [(p["name"], "player") for p in players if p.get("name")]
    areas = sorted({(p["area"], "area") for p in players if p.get("area")})
    for pool in (names, areas):
        hit = [x for x in pool if room_key(x[0]) == key]
        if len(hit) == 1:
            return hit[0]
    for pool in (names, areas):
        hit = [x for x in pool if key in room_key(x[0])]
        if len(hit) == 1:
            return hit[0]
    scored = sorted(((similarity(key, room_key(n)), (n, k)) for n, k in names + areas), reverse=True)
    if scored and scored[0][0] >= min_score and (len(scored) == 1 or scored[1][0] < scored[0][0]):
        return scored[0][1]
    return None


def _strip_tail(text: str) -> str:
    text = text.strip(_TAIL_PUNCT + "\"“”")
    while (m := _PLEASE_RE.search(text)):
        text = text[:m.start()].strip(_TAIL_PUNCT)
    return text


def _room_clause(text: str, players: list[dict], min_score: float):
    """(text before the clause, real name, kind, words said) for a room or
    speaker named at the right-hand end, else None. Prepositions are tried
    from the right; each candidate clause runs to the end of the text."""
    for m in reversed(list(_ROOM_PREP_RE.finditer(text))):
        said = _strip_tail(text[m.end():])
        if not said:
            continue
        hit = match_room(said, players, min_score)
        if hit:
            return text[:m.start()], hit[0], hit[1], said
        words = room_key(re.sub(r"^(the|my)\s+", "", said.lower())).split()
        raw = said.lower().split()
        if raw and raw[-1] in _DEVICE_NOUNS and 1 <= len(words) <= 4 and not _has_word(said, ["by"]):
            # "on the blue group": a speaker that does not exist. Said as a
            # target, so it is one; resolution names what exists.
            return text[:m.start()], room_key(said), "unknown", said
    return None


_IN_PROVIDER_RE = re.compile(
    rf"^(?P<pre>(?:the|my|a|an)\s+)?(?P<prov>{_PROVIDER_WORD})\s+(?=(?:playlist|station|radio|album|track|song|artist)\b)",
    re.IGNORECASE)


def _type_word(text: str, type_words: dict[str, str]):
    """(type, word, text without it) for a type word that opens or closes
    the request ("the album X", "X radio", "the radio station X"), else None."""
    words = sorted(type_words, key=len, reverse=True)
    alt = "|".join(re.escape(w) for w in words)
    lead = re.match(rf"^(?:(?:the|my|a|an)\s+)?(?P<w>radio\s+station|{alt})\b[\s,:]*(?P<rest>.+)$", text, re.I)
    if lead and lead.group("rest").strip(_TAIL_PUNCT):
        w = lead.group("w").lower()
        return ("radio" if w.startswith("radio") else type_words[w]), w, lead.group("rest")
    trail = re.match(rf"^(?P<rest>.+?)[\s,]+(?P<w>radio\s+station|{alt})$", text, re.I)
    if trail and trail.group("rest").strip(_TAIL_PUNCT):
        w = trail.group("w").lower()
        return ("radio" if w.startswith("radio") else type_words[w]), w, trail.group("rest")
    return None


def strip_fillers(text: str, fillers: list[str]) -> str:
    """Leading filler words off: 'some good deep house' with 'good' → 'deep house'."""
    alt = "|".join(re.escape(f) for f in sorted(fillers, key=len, reverse=True))
    prev = None
    while prev != text:
        prev = text
        text = re.sub(rf"^(?:{alt})\s+", "", text, flags=re.IGNORECASE).strip()
    return text


_GENERIC_ITEMS = {"music", "songs", "song", "tracks", "stuff", "something", "anything", "hits"}


def parse_sentence(sentence: str, players: list[dict] | None = None,
                   words: "config_loader.Parser | None" = None) -> Parsed:
    """Read one sentence. Never raises; never calls anything."""
    w = words or parser_words()
    players = players or []
    p = Parsed(sentence=sentence or "")
    m = _REQUEST_VERB_RE.match(p.sentence)
    if not m:
        p.music, p.reason = False, "no request verb at the start"
        return p
    p.verb = " ".join(m.group("verb").lower().split())
    rest = m.group("rest")
    if _has_word(rest, NOT_MUSIC_ALWAYS):
        p.music, p.reason = False, "the news is not music"
        return p
    has_noun = _has_word(rest, MUSIC_NOUNS)
    if p.verb == "play" and not has_noun and _has_word(rest, NOT_MUSIC_AFTER_PLAY):
        p.music, p.reason = False, "a game, video, movie or podcast is not music"
        return p
    if p.verb in ("start", "turn on") and not has_noun:
        p.music, p.reason = False, f"'{p.verb}' without a music word is not a music request"
        return p
    p.shuffle = p.verb == "shuffle"
    p.radio_name = radio_name(p.sentence)

    # The right-hand end: a provider clause and a room clause, in either
    # order ("... from YouTube Music, in the kitchen").
    rest = _strip_tail(rest)
    for _ in range(2):
        hm = _HINT_RE.search(" " + rest)
        if hm and hm.start() > 0 and not p.provider:
            p.provider = next(k for k in _PROVIDERS if hm.group(k))
            rest = _strip_tail((" " + rest)[:hm.start()])
            continue
        if not p.room and (rc := _room_clause(rest, players, w.room_min_score)):
            rest, p.room, p.room_kind, p.room_said = _strip_tail(rc[0]), rc[1], rc[2], rc[3]
            continue
        break

    # A provider inside the request: "my YouTube playlist anime bangers".
    if (im := _IN_PROVIDER_RE.match(rest)):
        p.provider = p.provider or _provider_of(im.group("prov"))
        rest = rest[im.end():]
    if (tw := _type_word(rest, w.type_words)):
        p.type, p.type_word, rest = tw[0], tw[1], tw[2]
    rest = _strip_tail(re.sub(r"^(?:(?:a|an|my)\s+)", "", rest.strip(), flags=re.I))
    if p.radio_name and p.type in (None, "radio"):
        # The station the sentence names (radio_name, the live path's own
        # reading): it stops at a full stop, so trailing speech is not part
        # of the name ("Play Anime Pop Radio. That's probably about it ...").
        p.type, p.type_word = "radio", p.type_word or "radio"
        rest = _strip_tail(re.sub(r"^(?:(?:a|an|my|the)\s+)", "", p.radio_name, flags=re.I))
    rest = re.sub(r"\s+", " ", re.sub(r"[\"“”]", "", rest)).strip()
    p.words = rest

    bm = _BY_RE.match(rest)
    if bm:
        x, y = bm.group("name").strip("\"“” ,"), bm.group("artist").strip("\"“” ,")
        if strip_fillers(fold(x), w.fillers) in _GENERIC_ITEMS:
            p.by_generic, p.name, p.type = True, y, p.type or "artist"
        else:
            p.name, p.artist = x, y
    else:
        p.name = rest or None
    if not p.type and not p.by_generic:
        first, last = (fold(rest).split() or [""])[0], (fold(rest).split() or [""])[-1]
        if first in ("some", "something") and first in w.style_markers:
            p.style_marker = first
        elif last in w.style_markers and last not in ("some", "something"):
            p.style_marker = last
    return p
