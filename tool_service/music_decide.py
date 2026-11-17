"""The M11 decision order: is a music request a style or a named item, and
what plays (sprint M11; docs/features/voice-music-control.md).

Input: a Parsed sentence (music_resolve.parse_sentence) and a Reads object
that can look things up and nothing else. Output: an Outcome (what would
play, or why nothing will) and a Trace (each step, the candidates and their
scores, the model question). Nothing here plays, queues, rates or writes:
the module imports no Home Assistant or Music Assistant client, and the only
I/O it has is the Reads it is handed. POST /music/say plays the Outcome;
shadow mode (music_shadow.py) only logs it.

The order, as the plan has it (Kronk evaluation findings and implementation
plan.md, M11; Drew, 2026-11-11), one function per step, run in the order
config/music.yaml decision.order gives:

  markers        a type word ("album", "song", "radio") means a named item
                 of that type; a style word ("some", "music", "vibes") a style
  genre_list     an unmarked request that is a known genre is a style
  named          search and score as a named item; the fast case first (an
                 exact library name), then one search, with the provider
                 policy applied among strong candidates
  cross_type     a typed request with no strong match: the same name as
                 another type, strong matches only
  other_reading  an unknown style is retried as a name (an artist only: rule
                 7); a name with no strong match as a style if the genre
                 list knows it
  model          still unclear: one question, style or named, output
                 constrained to those two words; the answer picks the
                 branch, the words still come from the sentence

then rule 7 (a title match never beats a style reading on its own) and rule
8 (nothing resolves: say so and name the closest match). The raw words are
never passed to Music Assistant: an Outcome that plays always carries a URI
found by search, the library or MA's own station lookup.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field

try:
    from . import config_loader, music_resolve, telemetry
except ImportError:
    import config_loader
    import music_resolve
    import telemetry

logger = logging.getLogger("tool_service.music_decide")

ALL_TYPES = ["artist", "album", "track", "playlist"]
# Untyped exact-name checks, in the order a tie goes (resolver type_bonus).
# Not tracks: a title track must not beat its own album (title_track_album).
EXACT_ORDER = ["artist", "album", "playlist"]
GENERIC_TAIL = {"music", "songs", "song", "tunes", "stuff", "tracks", "vibes", "mix", "playlist", "please"}


def policy() -> "config_loader.Decision":
    """The decision policy in effect: config/music.yaml decision."""
    return config_loader.current().music.decision


class Reads:
    """What the decision may ask. main.ReadOnlyMusic implements it against
    Music Assistant (read commands only) and the taste store (read-only);
    tests implement it with stubs. Every method is a lookup."""
    resolve_kwargs: dict = {}

    async def library_exact(self, media_type: str, name: str, artist: str | None = None):
        """A library item of this type named exactly `name` (and by `artist`), or None."""
        raise NotImplementedError

    async def search_each(self, text: str, types: list[str], limit: int):
        raise NotImplementedError

    async def library_items(self, media_type: str):
        raise NotImplementedError

    async def station(self, name: str) -> dict | None:
        """MA's own name lookup for a radio: {"name", "uri", "created"}."""
        raise NotImplementedError

    def genre_known(self, words: str) -> bool:
        raise NotImplementedError

    async def genre_plan(self, words: str) -> dict | None:
        """The ranked genre picks for these words (forced), or None."""
        raise NotImplementedError

    async def ask_model(self, sentence: str, words: str) -> tuple[str | None, dict]:
        """("style" | "named" | None, details for the trace)."""
        raise NotImplementedError


@dataclass
class Outcome:
    action: str                                  # play | not_found | not_music
    reading: str | None = None                   # style | named
    reading_by: str | None = None                # the step that fixed the reading
    decided_by: str | None = None                # the step that ended the decision
    kind: str | None = None                      # genre | named
    media_type: str | None = None
    name: str | None = None
    artist: str | None = None
    provider: str | None = None                  # the chosen candidate's preferred provider
    candidate: "music_resolve.Candidate | None" = None
    station: dict | None = None
    plan: dict | None = None
    via: str | None = None                       # exact | search | station | genre
    cross_type: bool = False
    closest: "music_resolve.Candidate | None" = None
    reason: str | None = None

    @property
    def uri(self) -> str | None:
        if self.candidate:
            return self.candidate.uri
        if self.plan:
            return self.plan["picks"][self.plan["start"]].uri
        return None

    def as_dict(self) -> dict:
        d = {k: getattr(self, k) for k in ("action", "reading", "reading_by", "decided_by", "kind",
                                           "media_type", "name", "artist", "provider", "via", "reason")}
        d["uri"] = self.uri
        if self.cross_type:
            d["cross_type"] = True
        if self.station:
            d["station"] = self.station
        if self.plan:
            pick = self.plan["picks"][self.plan["start"]]
            d["pick"] = {"label": pick.label, "kind": pick.kind, "uri": pick.uri, "phrase": self.plan["phrase"]}
        if self.closest:
            d["closest"] = _cand(self.closest)
        return {k: v for k, v in d.items() if v is not None}


def _cand(c: "music_resolve.Candidate") -> dict:
    return {"name": c.name, "artist": c.artist, "media_type": c.media_type, "uri": c.uri,
            "providers": sorted(c.providers), "score": round(c.score, 3)}


@dataclass
class State:
    p: "music_resolve.Parsed"
    reads: Reads
    pol: "config_loader.Decision"
    ask: bool = True                              # False in shadow: record, never ask
    reading: str | None = None
    reading_by: str | None = None
    genre_known: bool | None = None
    named_tried: bool = False
    best: "music_resolve.Candidate | None" = None
    title_only: "music_resolve.Candidate | None" = None
    outcome: Outcome | None = None
    steps: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    model: dict = field(default_factory=dict)
    searches: int = 0

    def note(self, step: str, **kw) -> None:
        self.steps.append({"step": step, **{k: v for k, v in kw.items() if v is not None}})

    def set_reading(self, reading: str, by: str) -> None:
        if self.reading is None:
            self.reading, self.reading_by = reading, by

    def keep_best(self, c) -> None:
        if c is not None and (self.best is None or c.score > self.best.score):
            self.best = c


# ── words ────────────────────────────────────────────────────────────────────

def style_phrase(p: "music_resolve.Parsed", words: "config_loader.Parser | None" = None) -> str:
    """The request without style words and fillers: 'some good deep house
    chill music' → 'deep house chill', 'top Pink Floyd songs' → 'pink floyd'."""
    w = words or music_resolve.parser_words()
    s = music_resolve.fold(p.words)
    lead = [m for m in w.style_markers if m in ("some", "something")] + list(w.fillers)
    tail = set(GENERIC_TAIL) | {m for m in w.style_markers if m not in ("some", "something")}
    prev = None
    while s != prev:
        prev = s
        s = music_resolve.strip_fillers(s, lead)
        parts = s.split()
        if parts and parts[-1] in tail:
            s = " ".join(parts[:-1])
    return s


def _order(pol, p) -> dict[str, list[str]]:
    """The provider order per type, the named provider first."""
    out = pol.provider_order.model_dump()
    if p.provider:
        out = {t: [p.provider] + [x for x in v if x != p.provider] for t, v in out.items()}
    return out


# ── lookups the steps share ──────────────────────────────────────────────────

async def _exact(st: State, name: str, artist: str | None, types: list[str]):
    """The fast case (plan M11 implement step 2): an exact library name,
    before any search. Types in EXACT_ORDER when untyped."""
    if not name:
        return None
    names = [name] + ([f"{name} Radio"] if types == ["radio"] and not name.lower().endswith(" radio") else [])
    tries = [(t, n) for t in types for n in names]
    found = await asyncio.gather(*(st.reads.library_exact(t, n, artist) for t, n in tries))
    for c in found:
        if c is not None:
            c.score = max(c.score, 1.0)
            st.candidates.append({"via": "exact", **_cand(c)})
            return c
    return None


async def _resolve(st: State, name: str, artist: str | None, media_type: str | None,
                   types: list[str] | None = None):
    """(winner, Resolution): exact library name, then one search per type
    and variant, scored, the provider policy among strong candidates. A
    radio with no strong match then asks MA's own station lookup (a Pandora
    catalogue station is created on its first play)."""
    if not name:
        return None, None, None
    if types is None:
        exact = await _exact(st, name, artist, [media_type] if media_type else EXACT_ORDER)
        if exact:
            return exact, None, "exact"
    # The query text keeps "X by Y" whole, as /music's does (the model's
    # query carries it): scored against the title alone, a stranger's
    # "DESTINY WAVE" passed for "Destiny Waving by Ulrich Schnauss" (M11
    # dev check, 2026-11-17); with the artist in the text it falls below
    # the line and the artist's own works are searched (stage 2).
    nq = music_resolve.normalize(f"{name} by {artist}" if artist else name)
    nq.provider = nq.provider or st.p.provider
    also = [music_resolve.normalize(f"{name} Radio")] if media_type == "radio" and \
        not name.lower().endswith(" radio") else []
    counted = st.reads.search_each

    async def search_each(text, ts, limit):
        st.searches += len(ts)
        return await counted(text, ts, limit)

    order = _order(st.pol, st.p)
    res = await music_resolve.resolve_named(
        nq, media_type, also, search_each=search_each, library_items=st.reads.library_items,
        types=types, provider_order=order, strong_min=st.pol.strong_min_score,
        prefer_min=st.pol.provider_min_score, **st.reads.resolve_kwargs)
    for c in res.ranked[:5]:
        st.candidates.append({"via": "search", **_cand(c)})
    st.keep_best(res.best)
    if res.winner and media_type is None:
        res.winner = title_track_album(res.winner, res.ranked, st.pol.strong_min_score)
    if res.winner:
        return res.winner, res, "search"
    if media_type == "radio" and types is None:
        station = await st.reads.station(name)
        if station:
            c = music_resolve.Candidate(uri=station["uri"], media_type="radio", name=station["name"],
                                        providers={"pandora"}, score=1.0)
            st.candidates.append({"via": "station", **_cand(c), "created": station.get("created")})
            return c, res, "station"
    return None, res, "search"


def title_track_album(winner, ranked, threshold):
    """An untyped request whose winner is a track, with a strong album of the
    same name and artist among the candidates, plays the album: the
    resolver's type bonus is there for "a whole album over its title track",
    and the library bonus on an owned track outweighs it ("Toys in the Attic
    by Aerosmith": the NAS track 1.03, the album 1.015; M11 dev shadow)."""
    if winner.media_type != "track":
        return winner
    key = (music_resolve.fold(winner.name), music_resolve.fold(winner.artist))
    for c in ranked:
        if c.media_type == "album" and c.score >= threshold and \
                (music_resolve.fold(c.name), music_resolve.fold(c.artist)) == key:
            return c
    return winner


async def finish_named(st: State, c, step: str, via: str, cross: bool = False) -> None:
    order = _order(st.pol, st.p).get(c.media_type, [])
    provider = next((x for x in order if x in c.providers), next(iter(sorted(c.providers)), None))
    station = None
    if c.media_type == "radio" and "pandora" in c.providers:
        station = {"name": c.name, "uri": c.uri,
                   "created": next((x.get("created") for x in st.candidates
                                    if x.get("via") == "station" and x["uri"] == c.uri), False) or False}
    if st.reading != "named":
        st.reading, st.reading_by = "named", step
    st.outcome = Outcome("play", reading="named", reading_by=st.reading_by, decided_by=step, kind="named",
                         media_type=c.media_type, name=c.name, artist=c.artist, provider=provider,
                         candidate=c, station=station, via=via, cross_type=cross)


async def finish_style(st: State, step: str) -> None:
    if st.reading != "style":
        st.reading, st.reading_by = "style", step
    phrase = style_phrase(st.p)
    plan = await st.reads.genre_plan(st.p.words)
    if plan:
        st.outcome = Outcome("play", reading="style", reading_by=st.reading_by, decided_by=step, kind="genre",
                             name=plan.get("phrase") or phrase, plan=plan, via="genre")
    else:
        st.outcome = Outcome("not_found", reading="style", reading_by=st.reading_by, decided_by=step,
                             kind="genre", name=phrase,
                             reason=f"nothing to play for {phrase or 'that'}")


# ── the steps ────────────────────────────────────────────────────────────────

async def step_markers(st: State) -> None:
    """(1) Marker words decide the reading outright."""
    p = st.p
    if p.type or p.by_generic:
        st.set_reading("named", "markers")
    elif p.style_marker:
        st.set_reading("style", "markers")
    st.note("markers", type=p.type, type_word=p.type_word, style_marker=p.style_marker,
            by_generic=p.by_generic or None, reading=st.reading)


async def step_genre_list(st: State) -> None:
    """(2) Unmarked or style-marked: a known genre is a style. A type word
    overrides ("play the album Jazz" is the album)."""
    if st.reading == "named":
        return st.note("genre_list", skipped="a type word decided it")
    st.genre_known = bool(st.reads.genre_known(st.p.words))
    st.note("genre_list", known=st.genre_known, words=st.p.words)
    if st.genre_known:
        await finish_style(st, "genre_list")


def weak_title(st: State, c, text: str) -> bool:
    """An unmarked request ("play italian") matched only part of a name
    ("Italian Lesson No. 3"): a strong score from the prefix rule, not a
    match of the whole name. Rule 7's weak title: it never decides alone;
    the model question does, and without it the named reading stands."""
    p = st.p
    if p.type or p.by_generic or p.style_marker or p.artist:
        return False
    return music_resolve.similarity(text, c.name) < st.pol.title_min_score


async def step_named(st: State) -> None:
    """(3) Not a style: the resolver, as a named item."""
    if st.reading == "style":
        return st.note("named", skipped="read as a style")
    p = st.p
    st.named_tried = True
    winner, res, via = await _resolve(st, p.name or p.words, p.artist, p.type)
    if winner and weak_title(st, winner, p.name or p.words):
        st.title_only = winner
        return st.note("named", via=via, strong=True, weak_title=_cand(winner))
    st.note("named", via=via, strong=bool(winner), best=_cand(st.best) if st.best else None)
    if winner:
        await finish_named(st, winner, "named", via)


async def step_cross_type(st: State) -> None:
    """(4) A typed request with no strong match: the same name as another
    type. Only a strong match is taken, and the answer says what was found."""
    p = st.p
    if not p.type or st.reading == "style" or not st.named_tried or p.type == "radio":
        return st.note("cross_type", skipped="not a typed named request without a match"
                       if p.type != "radio" else "a station is only a station")
    others = [t for t in ALL_TYPES if t != p.type]
    winner, res, via = await _resolve(st, p.name or p.words, p.artist, None, types=others)
    st.note("cross_type", types=others, strong=bool(winner))
    if winner:
        await finish_named(st, winner, "cross_type", via, cross=True)


async def step_other_reading(st: State) -> None:
    """(5) The other reading, once. A style the genre list does not know
    ("play some Portishead") is retried as a name: a strong artist plays;
    a strong title only is left unclear (rule 7: it never wins on its own);
    nothing strong keeps the style. A name with no strong match is a style
    only if the genre list knows it."""
    p = st.p
    if st.reading == "style":
        phrase = style_phrase(p)
        if not phrase:
            st.note("other_reading", retried=None, kept="style")
            return await finish_style(st, "other_reading")
        winner, res, via = await _resolve(st, phrase, None, None)
        if winner and winner.media_type == "artist":
            st.note("other_reading", retried=phrase, found="artist")
            return await finish_named(st, winner, "other_reading", via)
        if winner:
            st.title_only = winner
            return st.note("other_reading", retried=phrase, found=f"{winner.media_type} only (unclear)")
        st.note("other_reading", retried=phrase, found=None, kept="style")
        return await finish_style(st, "other_reading")
    if st.named_tried:
        if st.genre_known is None:
            st.genre_known = bool(st.reads.genre_known(p.words))
        st.note("other_reading", genre_known=st.genre_known)
        if st.genre_known:
            await finish_style(st, "other_reading")
        return
    st.note("other_reading", skipped="nothing to retry yet")


def unclear(st: State) -> bool:
    """Whether the model question would help at this point. A type word is
    never unclear; a strong title for a style request is; so is an unmarked
    request with no reading yet or with no strong match."""
    p = st.p
    if st.outcome or p.type or p.by_generic:
        return False
    if st.title_only is not None:
        return True
    if st.reading is None and not st.named_tried:
        return True                      # placed early: nothing has read it yet
    return st.reading != "style" and st.named_tried     # searched, nothing strong


async def step_model(st: State) -> None:
    """(6) One model question, style or named. The model never writes
    arguments: its answer picks the branch. Down, slow or off the two
    words: the rules' own reading stands."""
    m = st.pol.model
    if not unclear(st):
        return st.note("model", skipped="not unclear")
    if not m.enabled:
        st.model = {"asked": False, "would_ask": True, "why": "disabled"}
        return st.note("model", skipped="disabled", would_ask=True)
    if not st.ask:
        st.model = {"asked": False, "would_ask": True, "why": "shadow never asks"}
        return st.note("model", skipped="shadow", would_ask=True)
    words = style_phrase(st.p) or st.p.words
    t0 = time.monotonic()
    answer, info = await st.reads.ask_model(st.p.sentence, words)
    st.model = {"asked": True, "answer": answer, "ms": round((time.monotonic() - t0) * 1000), **info}
    st.note("model", answer=answer, ms=st.model["ms"], error=info.get("error"))
    if answer not in ("style", "named"):
        return
    if st.title_only is not None:
        if answer == "named":
            return await finish_named(st, st.title_only, "model", "search")
        return await finish_style(st, "model")
    if answer == "style":
        st.reading, st.reading_by = "style", "model"
        return await finish_style(st, "model")
    st.reading, st.reading_by = "named", "model"
    if st.named_tried:
        return finish_not_found(st, "model")


def finish_not_found(st: State, step: str) -> None:
    """(8) Nothing resolves: say so plainly and name the closest match."""
    hint = music_resolve.policy().hint_min_score
    closest = st.best if st.best is not None and st.best.score >= hint else None
    st.outcome = Outcome("not_found", reading=st.reading or "named", reading_by=st.reading_by, decided_by=step,
                         kind="named", media_type=st.p.type, name=st.p.name or st.p.words,
                         artist=st.p.artist, closest=closest, reason="no strong match")


STEPS = {"markers": step_markers, "genre_list": step_genre_list, "named": step_named,
         "cross_type": step_cross_type, "other_reading": step_other_reading, "model": step_model}


async def decide(p: "music_resolve.Parsed", reads: Reads, *, ask_model: bool = True,
                 pol: "config_loader.Decision | None" = None) -> tuple[Outcome, dict]:
    """Run the decision order on a parsed sentence. Never raises for a
    lookup that fails: Reads answer None and the steps go on."""
    pol = pol or policy()
    t0 = time.monotonic()
    st = State(p=p, reads=reads, pol=pol, ask=ask_model)
    with telemetry.span("music.decide", **{"music.order": ",".join(pol.order)}) as sp:
        if not p.music:
            st.outcome = Outcome("not_music", decided_by="parser", reason=p.reason)
        else:
            for name in pol.order:
                await STEPS[name](st)
                if st.outcome:
                    break
            if not st.outcome:
                if st.title_only is not None and st.reading == "style":
                    # (7) A weak or lone title match never beats a style reading.
                    st.note("rule7", title=_cand(st.title_only))
                    await finish_style(st, "rule7")
                elif st.title_only is not None:
                    # An unmarked request whose only match is a weak title,
                    # and no model answer: the named reading stands, as on /music.
                    await finish_named(st, st.title_only, "named", "search")
                elif st.reading == "style":
                    await finish_style(st, st.reading_by or "markers")
                else:
                    finish_not_found(st, "not_found")
        o = st.outcome
        trace = {"steps": st.steps, "candidates": st.candidates[:12], "model": st.model,
                 "searches": st.searches, "ms": round((time.monotonic() - t0) * 1000)}
        sp.set(**{"music.decided_by": o.decided_by, "music.reading": o.reading,
                  "music.reading_by": o.reading_by, "music.action": o.action,
                  "music.media_type": o.media_type, "music.uri": o.uri,
                  "music.model_asked": bool(st.model.get("asked")),
                  "music.model_would_ask": bool(st.model.get("would_ask")),
                  "music.searches": st.searches,
                  "music.candidates": json.dumps(trace["candidates"][:5], ensure_ascii=False)[:1000]})
    if telemetry.RECORD_TEXT:
        logger.info("music decision %s", json.dumps({"outcome": o.as_dict(), **trace}, ensure_ascii=False,
                                                    default=str)[:4000])
    else:   # M12: names, phrases and the steps quote what was said; only the shape
        logger.info("music decision %s", json.dumps({
            "outcome": {k: getattr(o, k, None) for k in ("action", "reading", "reading_by", "decided_by",
                                                         "kind", "media_type", "provider", "via")},
            "searches": trace.get("searches"), "candidates": len(trace.get("candidates") or []),
            "ms": trace.get("ms")}, default=str))
    return o, trace
