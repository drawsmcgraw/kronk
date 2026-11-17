"""The M11 decision order (tool_service/music_decide.py), criteria 5 to 11.

Each step is tested on its own terms with a stub Reads: an in-memory
library, search results keyed by text, a genre list and a scripted model.
The stub counts every search, so the provider policy can be shown to make
one search per type, never one per provider.
"""
import asyncio
import json
import logging
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tool_service import config_loader, music_decide as md, music_resolve as mr, taste, telemetry

pytestmark = pytest.mark.usefixtures("no_network")


def item(name, media_type, providers=("filesystem_local",), artist=None, uri=None, library=True):
    prov = list(providers)
    uri = uri or (f"library://{media_type}/{name.lower().replace(' ', '_')}" if library
                  else f"{prov[0]}://{media_type}/{name.lower().replace(' ', '_')}")
    d = {"uri": uri, "name": name, "media_type": media_type, "provider": "library" if library else prov[0],
         "provider_mappings": [{"provider_domain": p} for p in prov]}
    if artist:
        d["artists"] = [{"name": artist}]
    return d


KEYS = {"artist": "artists", "album": "albums", "track": "tracks", "playlist": "playlists", "radio": "radio"}


class StubReads(md.Reads):
    resolve_kwargs = {"library_scored": {"radio", "playlist"}, "all_types": md.ALL_TYPES,
                      "search_limit": 10, "artist_limit": 50}

    def __init__(self, items=(), library=(), genres=(), stations=None, model=None, plans=True):
        self.items = list(items)            # what search finds (by word overlap)
        self.library = list(library)        # what the library holds (exact-name lookups, listings)
        self.genres = set(genres)
        self.stations = stations or {}
        self.model_answer = model
        self.plans = plans
        self.searches, self.exact_calls, self.model_calls, self.listings = [], [], [], []

    async def library_exact(self, media_type, name, artist=None):
        self.exact_calls.append((media_type, name))
        for it in self.library:
            if it["media_type"] == media_type and mr.fold(it["name"]) == mr.fold(name):
                if artist and not any(mr.fold(a["name"]) == mr.fold(artist) for a in it.get("artists", [])):
                    continue
                return mr.candidates_from_search({KEYS[media_type]: [it]})[0]
        return None

    async def search_each(self, text, types, limit):
        self.searches.append((text, tuple(types)))
        words = set(mr.fold(text).split())
        out = {}
        for it in self.items + self.library:
            if it["media_type"] in types and words & set(mr.fold(it["name"] + " " + " ".join(
                    a["name"] for a in it.get("artists", []))).split()):
                out.setdefault(KEYS[it["media_type"]], []).append(it)
        return out

    async def library_items(self, media_type):
        self.listings.append(media_type)
        return {KEYS[media_type]: [it for it in self.library if it["media_type"] == media_type]}

    async def station(self, name):
        return self.stations.get(name)

    def genre_known(self, words):
        phrase = taste.genre_phrase(words)
        return bool(phrase) and (phrase in self.genres or all(
            any(w in g.split() for g in self.genres) for w in phrase.split()))

    async def genre_plan(self, words):
        if not self.plans:
            return None
        phrase = taste.genre_phrase(words)
        pick = taste.Pick("station", f"library://radio/{phrase or 'mix'}", f"{phrase.title()} Radio",
                          "k", 1.0, "radio")
        return {"phrase": phrase, "picks": [pick], "start": 0}

    async def ask_model(self, sentence, words):
        self.model_calls.append(words)
        if isinstance(self.model_answer, Exception):
            return None, {"error": str(self.model_answer)}
        return self.model_answer, {}


def run(text, reads, **kw):
    p = mr.parse_sentence(text, [])
    return asyncio.run(md.decide(p, reads, **kw))


GENRES = {"jazz", "deep house", "house", "chill", "trip hop", "electronic"}
PORTISHEAD = item("Portishead", "artist")
GLORY_BOX = item("Glory Box", "track", artist="Portishead")
JAZZ_MUSIC = item("Jazz Music", "track", artist="De-Phazz")


# ── (1) markers ──────────────────────────────────────────────────────────────

def test_a_type_word_decides_named_even_for_a_genre_word():
    r = StubReads(library=[item("Jazz", "album", artist="Queen")], genres=GENRES)
    o, t = run("play the album Jazz", r)
    assert (o.action, o.kind, o.media_type, o.name, o.reading_by) == ("play", "named", "album", "Jazz", "markers")
    assert [s for s in t["steps"] if s["step"] == "genre_list"][0]["skipped"]


def test_a_style_marker_decides_style():
    o, t = run("play some jazz", StubReads(genres=GENRES))
    assert (o.action, o.kind, o.name, o.reading_by, o.decided_by) == ("play", "genre", "jazz", "markers", "genre_list")


def test_songs_by_an_artist_is_named():
    o, _ = run("play music by Portishead", StubReads(library=[PORTISHEAD], genres=GENRES))
    assert (o.kind, o.media_type, o.name, o.reading_by) == ("named", "artist", "Portishead", "markers")


# ── (2) genre list; a type word overrides ────────────────────────────────────

@pytest.mark.parametrize("text,name", [("play jazz", "jazz"), ("play deep house", "deep house"),
                                       ("play some deep house chill music", "deep house chill")])
def test_a_known_genre_is_a_style(text, name):
    r = StubReads(items=[JAZZ_MUSIC, item("Deep House", "playlist", ("ytmusic",), library=False)], genres=GENRES)
    o, t = run(text, r)
    assert (o.kind, o.name, o.media_type) == ("genre", name, None)
    assert r.searches == [] and r.exact_calls == []            # decided before any search


def test_an_unmarked_name_that_is_no_genre_is_named():
    o, t = run("play Portishead", StubReads(library=[PORTISHEAD], genres=GENRES))
    assert (o.kind, o.media_type, o.name, o.decided_by, o.reading_by) == \
        ("named", "artist", "Portishead", "named", "named")


def test_title_containing_a_genre_word_both_ways():
    """Rule 7 and the type-word override (plan M11 corpus rows)."""
    r = StubReads(library=[JAZZ_MUSIC], genres=GENRES)
    o, _ = run("play jazz music", r)
    assert (o.kind, o.name) == ("genre", "jazz")
    o, _ = run("play the song Jazz Music", StubReads(library=[JAZZ_MUSIC], genres=GENRES))
    assert (o.kind, o.media_type, o.name, o.artist) == ("named", "track", "Jazz Music", "De-Phazz")
    o, _ = run("play Burning Down the House",
               StubReads(items=[item("Burning Down the House", "track", ("ytmusic",), "Talking Heads", library=False)],
                         genres=GENRES))
    assert (o.kind, o.media_type, o.name) == ("named", "track", "Burning Down the House")


# ── (3) named: the fast case, search, the provider policy ───────────────────

def test_the_fast_case_is_an_exact_library_name_before_any_search():
    r = StubReads(library=[PORTISHEAD], genres=GENRES)
    o, t = run("play Portishead", r)
    assert o.via == "exact" and r.searches == [] and t["searches"] == 0
    assert r.exact_calls[0] == ("artist", "Portishead")


def test_a_station_by_exact_library_name_including_the_radio_suffix():
    r = StubReads(library=[item("Mushroom Jazz Radio", "radio", ("pandora",))], genres=GENRES)
    o, _ = run("play Mushroom Jazz radio", r)
    assert (o.media_type, o.name, o.via, o.station["name"]) == ("radio", "Mushroom Jazz Radio", "exact",
                                                                "Mushroom Jazz Radio")
    assert r.searches == []


NAS_ALBUM = item("Blue Lines", "album", ("filesystem_local",), "Massive Attack", uri="filesystem_local://album/bl")
YTM_ALBUM = item("Blue Lines", "album", ("ytmusic",), "Massive Attack", uri="ytmusic://album/bl", library=False)


def test_provider_policy_prefers_the_nas_among_strong_candidates():
    r = StubReads(items=[YTM_ALBUM, NAS_ALBUM], genres=GENRES)
    o, t = run("play the album Blue Lines by Massive Attack", r)
    assert (o.candidate.uri, o.provider) == ("filesystem_local://album/bl", "filesystem_local")
    # one search per query variant ("X by Y" and X) for the one type; never one per provider
    assert r.searches == [("Blue Lines by Massive Attack", ("album",)), ("Blue Lines", ("album",))]
    assert t["searches"] == 2


def test_a_poor_nas_match_never_beats_a_good_youtube_match():
    poor = item("Blue", "album", ("filesystem_local",), "Massive Attack", uri="filesystem_local://album/boot")
    r = StubReads(items=[poor, YTM_ALBUM], genres=GENRES)
    o, t = run("play the album Blue Lines by Massive Attack", r)
    assert o.candidate.uri == "ytmusic://album/bl"
    scores = {c["uri"]: c["score"] for c in t["candidates"]}
    d = config_loader.current().music.decision
    # strong enough to play on its own, not good enough to win on provider
    assert d.strong_min_score <= scores["filesystem_local://album/boot"] < d.provider_min_score


def test_a_provider_named_in_the_sentence_goes_first():
    r = StubReads(items=[YTM_ALBUM, NAS_ALBUM], genres=GENRES)
    o, _ = run("play the album Blue Lines by Massive Attack on YouTube Music", r)
    assert (o.candidate.uri, o.provider) == ("ytmusic://album/bl", "ytmusic")
    assert len(r.searches) == 2


def test_radio_prefers_pandora():
    ytm = item("Trip Hop Radio", "radio", ("ytmusic",), uri="ytmusic://radio/th", library=False)
    pan = item("Trip Hop Radio", "radio", ("pandora",), uri="pandora://radio/th", library=False)
    r = StubReads(items=[ytm, pan], genres=GENRES)
    o, _ = run("play the station Trip Hop Radio", r)
    assert (o.candidate.uri, o.station["uri"]) == ("pandora://radio/th", "pandora://radio/th")


def test_prefer_provider_unit():
    def c(name, score, prov, mt="album"):
        x = mr.Candidate(uri=f"{prov}://{mt}/{name}", media_type=mt, name=name, providers={prov})
        x.score = score
        return x
    nas_good, ytm_good, nas_poor = c("a", 0.97, "filesystem_local"), c("b", 1.0, "ytmusic"), c("c", 0.8, "filesystem_local")
    order = ["filesystem_local", "ytmusic"]
    assert mr.prefer_provider([ytm_good, nas_good], 0.75, order, 0.95) is nas_good
    assert mr.prefer_provider([ytm_good, nas_poor], 0.75, order, 0.95) is ytm_good
    assert mr.prefer_provider([nas_poor], 0.75, order, 0.95) is nas_poor           # strong, plays
    assert mr.prefer_provider([c("d", 0.7, "ytmusic")], 0.75, order, 0.95) is None
    assert mr.prefer_provider([ytm_good, c("e", 0.99, "filesystem_local", "track")], 0.75,
                              {"album": order}, 0.95) is ytm_good                 # same type only


def test_the_provider_order_is_read_from_config():
    r = StubReads(items=[YTM_ALBUM, NAS_ALBUM], genres=GENRES)
    order = config_loader.current().music.decision.provider_order
    with patch.object(order, "album", ["ytmusic", "filesystem_local"]):
        o, _ = run("play the album Blue Lines by Massive Attack", r)
    assert o.candidate.uri == "ytmusic://album/bl"


def test_radio_with_no_library_station_asks_mas_station_lookup():
    r = StubReads(genres=GENRES, stations={"K-pop": {"name": "K-Pop Radio", "uri": "pandora://radio/seed:kp",
                                                     "created": True}})
    o, _ = run("play K-pop radio", r)
    assert (o.via, o.name, o.station) == ("station", "K-Pop Radio",
                                          {"name": "K-Pop Radio", "uri": "pandora://radio/seed:kp", "created": True})


# ── (4) cross-type retry ─────────────────────────────────────────────────────

def test_a_typed_request_with_no_match_finds_the_name_as_another_type():
    r = StubReads(items=[GLORY_BOX], genres=GENRES)
    o, t = run("play the album Glory Box", r)
    assert (o.decided_by, o.cross_type, o.media_type, o.name) == ("cross_type", True, "track", "Glory Box")


def test_cross_type_takes_only_a_strong_match():
    r = StubReads(items=[item("Glory Days", "track", ("ytmusic",), "Bruce Springsteen", library=False)], genres=GENRES)
    o, _ = run("play the album Glory Box", r)
    assert (o.action, o.decided_by) == ("not_found", "not_found")


# ── (5) the other reading ────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["play some Portishead", "play top Portishead songs"])
def test_an_unknown_style_is_retried_as_an_artist(text):
    o, _ = run(text, StubReads(library=[PORTISHEAD], genres=GENRES))
    assert (o.kind, o.media_type, o.name, o.decided_by, o.reading) == ("named", "artist", "Portishead",
                                                                       "other_reading", "named")


def test_an_unknown_style_with_nothing_found_stays_a_style():
    o, _ = run("play italian music", StubReads(items=[GLORY_BOX], genres=GENRES))
    assert (o.kind, o.name, o.decided_by) == ("genre", "italian", "other_reading")


def test_a_name_with_no_strong_match_is_a_style_if_the_genre_list_knows_it():
    o, _ = run("play the album deep house", StubReads(genres=GENRES))
    assert (o.kind, o.name, o.decided_by) == ("genre", "deep house", "other_reading")


# ── (6) the model question, and (7) rule 7 ───────────────────────────────────

COSTES = item("Hotel Costes", "playlist", ("ytmusic",))


def test_a_style_request_with_only_a_title_match_asks_the_model():
    r = StubReads(library=[COSTES], genres=GENRES, model="named")
    o, t = run("play some Hotel Costes", r)
    assert (o.kind, o.media_type, o.decided_by) == ("named", "playlist", "model")
    assert r.model_calls == ["hotel costes"] and t["model"]["asked"] is True


def test_the_models_style_answer_keeps_the_style():
    o, _ = run("play some Hotel Costes", StubReads(library=[COSTES], genres=GENRES, model="style"))
    assert (o.kind, o.decided_by) == ("genre", "model")


@pytest.mark.parametrize("model", [None, "maybe", RuntimeError("LiteLLM unreachable")])
def test_rule_7_a_lone_title_never_beats_a_style_without_the_model(model):
    """Model down, off the two values, or failing: the non-model reading,
    which for a style request is the style (rule 7)."""
    o, t = run("play some Hotel Costes", StubReads(library=[COSTES], genres=GENRES, model=model))
    assert (o.kind, o.decided_by) == ("genre", "rule7")


def test_rule_7_with_the_model_switched_off():
    r = StubReads(library=[COSTES], genres=GENRES, model="named")
    with patch.object(config_loader.current().music.decision.model, "enabled", False):
        o, t = run("play some Hotel Costes", r)
    assert (o.kind, o.decided_by, r.model_calls, t["model"]["would_ask"]) == ("genre", "rule7", [], True)


def test_an_unmarked_unknown_name_with_no_match_asks_the_model():
    r = StubReads(items=[GLORY_BOX], genres=GENRES, model="style")
    o, _ = run("play italian", r)
    assert (o.kind, o.name, o.decided_by) == ("genre", "italian", "model")
    r = StubReads(items=[GLORY_BOX], genres=GENRES, model="named")
    o, _ = run("play zorblax fnordwave", r)
    assert (o.action, o.decided_by) == ("not_found", "model")


def test_model_down_on_an_unmarked_name_fails_plainly():
    o, t = run("play zorblax fnordwave", StubReads(genres=GENRES, model=RuntimeError("timeout")))
    assert (o.action, o.decided_by) == ("not_found", "not_found")
    assert t["model"]["error"] == "timeout"


def test_a_typed_request_never_asks_the_model():
    r = StubReads(genres=GENRES, model="style")
    o, _ = run("play the album zorblax fnordwave", r)
    assert r.model_calls == [] and o.action == "not_found"


def test_shadow_records_would_ask_and_never_asks():
    r = StubReads(library=[COSTES], genres=GENRES, model="named")
    o, t = run("play some Hotel Costes", r, ask_model=False)
    assert r.model_calls == [] and t["model"] == {"asked": False, "would_ask": True, "why": "shadow never asks"}
    assert o.decided_by == "rule7"


def test_the_model_step_moves_earlier_by_configuration():
    """Drew's design principle (2026-11-11): the order is a setting. Late
    (default) the model is asked after the search; early, before it."""
    late = StubReads(items=[GLORY_BOX], genres=GENRES, model="style")
    o, t = run("play italian", late)
    assert [s["step"] for s in t["steps"]] == ["markers", "genre_list", "named", "cross_type",
                                               "other_reading", "model"]
    assert late.searches                                        # searched first
    early = StubReads(items=[GLORY_BOX], genres=GENRES, model="style")
    d = config_loader.current().music.decision
    with patch.object(d, "order", ["markers", "genre_list", "model", "named", "cross_type", "other_reading"]):
        o2, t2 = run("play italian", early)
    assert [s["step"] for s in t2["steps"]] == ["markers", "genre_list", "model"]
    assert early.searches == [] and early.exact_calls == [] and o2.decided_by == "model"


def test_the_order_setting_is_validated():
    from pydantic import ValidationError
    for bad in (["markers", "named", "genre_list", "cross_type", "other_reading", "model"],
                ["markers", "genre_list", "named", "cross_type", "other_reading"],
                ["model", "markers", "genre_list", "named", "cross_type", "other_reading", "model"]):
        with pytest.raises(ValidationError):
            config_loader.Decision.model_validate({"order": bad})
    config_loader.Decision.model_validate({"order": ["model", "markers", "genre_list", "named", "cross_type",
                                                     "other_reading"]})


# ── (8) nothing resolves ─────────────────────────────────────────────────────

def test_nothing_resolves_names_the_closest_match_and_plays_nothing():
    near = item("Glory Box Live", "track", ("ytmusic",), "Portishead", library=False)
    r = StubReads(items=[near], genres=GENRES, model=RuntimeError("down"))
    o, _ = run("play glorry bocks", r)
    assert o.action == "not_found" and o.uri is None
    assert o.closest is None or o.closest.name == "Glory Box Live"


def test_an_outcome_that_plays_always_carries_a_uri_never_the_words():
    for text, reads in [("play Portishead", StubReads(library=[PORTISHEAD], genres=GENRES)),
                        ("play some jazz", StubReads(genres=GENRES)),
                        ("play the album Glory Box", StubReads(items=[GLORY_BOX], genres=GENRES))]:
        o, _ = run(text, reads)
        assert o.action == "play" and o.uri and "://" in o.uri


def test_a_style_with_no_picks_fails_plainly():
    o, _ = run("play some jazz", StubReads(genres=GENRES, plans=False))
    assert (o.action, o.kind, o.reason) == ("not_found", "genre", "nothing to play for jazz")


def test_not_music_is_not_decided():
    r = StubReads(genres=GENRES)
    o, t = run("play the news", r)
    assert (o.action, o.decided_by) == ("not_music", "parser") and r.searches == [] and t["steps"] == []


# ── (11) the record ──────────────────────────────────────────────────────────

@pytest.fixture
def spans():
    exporter = InMemorySpanExporter()
    telemetry.enable_for_tests(exporter)
    yield exporter
    telemetry.ENABLED = False


def test_the_decision_is_a_span_and_a_log_event(spans, caplog, monkeypatch):
    from opentelemetry.trace import SpanKind
    monkeypatch.setattr(telemetry, "RECORD_TEXT", True)     # the full line is the text-on shape (M12)
    caplog.set_level(logging.INFO, logger="tool_service.music_decide")
    with telemetry._tracer.start_as_current_span("POST /music/say", kind=SpanKind.SERVER):
        o, t = run("play the album Blue Lines by Massive Attack",
                   StubReads(items=[YTM_ALBUM, NAS_ALBUM], genres=GENRES))
    sp = [s for s in spans.get_finished_spans() if s.name == "music.decide"][-1]
    a = dict(sp.attributes)
    assert (a["music.decided_by"], a["music.reading"], a["music.reading_by"]) == ("named", "named", "markers")
    assert a["music.uri"] == "filesystem_local://album/bl" and a["music.model_asked"] is False
    cands = json.loads(a["music.candidates"])
    assert {c["uri"] for c in cands} >= {"filesystem_local://album/bl", "ytmusic://album/bl"}
    assert all("score" in c for c in cands)
    rec = [r for r in caplog.records if r.getMessage().startswith("music decision ")][-1]
    body = json.loads(rec.getMessage()[len("music decision "):])
    assert body["outcome"]["decided_by"] == "named" and body["candidates"] and "steps" in body


# ── found on dev, 2026-11-17 ─────────────────────────────────────────────────

def test_x_by_y_is_scored_with_the_artist_in_the_text():
    """'Destiny Waving by Ulrich Schnauss' picked a stranger's 'DESTINY WAVE'
    when the query text was the title alone; /music keeps 'X by Y' whole."""
    stranger = item("DESTINY WAVE", "album", ("ytmusic",), "PEAKY TRIANGLE", library=False)
    right = item("Destiny Waiving", "album", ("ytmusic",), "Ulrich Schnauss", library=False)
    r = StubReads(items=[stranger, right], genres=GENRES)
    o, t = run("play the album Destiny Waving by Ulrich Schnauss", r)
    assert (o.name, o.artist) == ("Destiny Waiving", "Ulrich Schnauss")
    assert r.searches[0] == ("Destiny Waving by Ulrich Schnauss", ("album",))


LESSON = item("Italian Lesson No. 3", "track", ("filesystem_local",), "Space Ghost")


def test_a_weak_title_on_an_unmarked_request_goes_to_the_model():
    r = StubReads(library=[LESSON], genres=GENRES, model="style")
    o, t = run("play italian", r)
    assert (o.kind, o.name, o.decided_by) == ("genre", "italian", "model") and r.model_calls == ["italian"]
    assert [s for s in t["steps"] if s["step"] == "named"][0]["weak_title"]["name"] == "Italian Lesson No. 3"


def test_a_weak_title_without_a_model_answer_keeps_the_named_reading():
    """No model (down, or shadow): what /music does today stands."""
    o, t = run("play italian", StubReads(library=[LESSON], genres=GENRES, model=None))
    assert (o.kind, o.name, o.decided_by) == ("named", "Italian Lesson No. 3", "named")
    o, t = run("play italian", StubReads(library=[LESSON], genres=GENRES, model="named"), ask_model=False)
    assert o.name == "Italian Lesson No. 3" and t["model"]["would_ask"] is True


def test_a_whole_name_is_not_a_weak_title():
    o, _ = run("play Burning Down the House",
               StubReads(items=[item("Burning Down The House", "track", ("ytmusic",), "Talking Heads", library=False)],
                         genres=GENRES, model="style"))
    assert (o.kind, o.decided_by) == ("named", "named")


def test_an_untyped_title_track_yields_to_its_album():
    track = item("Toys In The Attic", "track", ("filesystem_local",), "Aerosmith")       # in the library
    album = item("Toys in the Attic", "album", ("ytmusic",), "Aerosmith", library=False)
    r = StubReads(library=[track], items=[album], genres=GENRES)
    o, _ = run("Play toys in the attic by aerosmith", r)
    assert (o.media_type, o.name) == ("album", "Toys in the Attic")
    o, _ = run("play the song Toys in the Attic by Aerosmith", StubReads(library=[track], items=[album], genres=GENRES))
    assert o.media_type == "track"                                                   # a type word decides
