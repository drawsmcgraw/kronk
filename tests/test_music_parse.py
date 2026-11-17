"""The M11 sentence parser: music_resolve.parse_sentence (criteria 1 to 4).

Pure: a sentence and the real player and room names in, a Parsed out. The
players are the ones HA listed when the snapshot was recorded
(tests/fixtures/music_snapshot.json): the kitchen Voice PE in Kitchen, the
Satellite1 in Office, the basement Voice PE with no area, the Sonos Move
Derp, two Shields and a Bravia with no area.
"""
import re
from unittest.mock import patch

import pytest

import routing
from tests import music_snapshot as snapmod
from tool_service import config_loader
from tool_service import music_resolve as mr

pytestmark = pytest.mark.usefixtures("no_network")

PLAYERS = snapmod.load()["players"]


def parse(text, players=PLAYERS):
    return mr.parse_sentence(text, players)


# ── criterion 1: the fields ──────────────────────────────────────────────────

def test_parse_reads_every_field():
    p = parse("shuffle the album Destiny Waving by Ulrich Schnauss from YouTube music, in the kitchen")
    assert (p.music, p.verb, p.type, p.type_word) == (True, "shuffle", "album", "album")
    assert (p.name, p.artist, p.words) == ("Destiny Waving", "Ulrich Schnauss", "Destiny Waving by Ulrich Schnauss")
    assert (p.shuffle, p.provider) == (True, "ytmusic")
    assert (p.room, p.room_kind, p.room_said) == ("Kitchen", "area", "the kitchen")


def test_parse_is_pure_and_needs_no_players():
    p = mr.parse_sentence("play Portishead", None)
    assert (p.music, p.name, p.room, p.type, p.style_marker) == (True, "Portishead", None, None, None)


@pytest.mark.parametrize("text,verb", [
    ("play jazz", "play"), ("put on some jazz", "put on"), ("listen to Portishead", "listen to"),
    ("shuffle my playlist old man sings", "shuffle"), ("queue up Blue Lines", "queue up"),
    ("throw on some funk", "throw on"), ("start Orbital radio", "start"), ("turn on Moby radio", "turn on"),
    ("Okay Nabu, play jazz", "play"), ("please play jazz", "play"), ("Tell Kronk to play jazz", "play"),
    ("can you play jazz", "play"), ("Hey Kronk, put on some jazz", "put on"),
])
def test_request_verbs_and_lead_ins(text, verb):
    p = parse(text)
    assert p.music and p.verb == verb, p


@pytest.mark.parametrize("text,type_,word,name", [
    ("play the album Jazz", "album", "album", "Jazz"),
    ("play hotel costes albums", "album", "albums", "hotel costes"),
    ("play the song Jazz Music", "track", "song", "Jazz Music"),
    ("play the track Glory Box", "track", "track", "Glory Box"),
    ("play the artist Portishead", "artist", "artist", "Portishead"),
    ("play the band Portishead", "artist", "band", "Portishead"),
    ("play the playlist old man sings", "playlist", "playlist", "old man sings"),
    ("play K-pop radio", "radio", "radio", "K-pop"),
    ("put on the radio station Zorblax Fnordwave", "radio", "radio station", "Zorblax Fnordwave"),
    ("Start an orbital radio station on YouTube Music.", "radio", "radio station", "orbital"),
])
def test_type_words(text, type_, word, name):
    p = parse(text)
    assert (p.type, p.type_word, p.name, p.style_marker) == (type_, word, name, None), p


@pytest.mark.parametrize("text,marker", [
    ("play some jazz", "some"), ("play something chill", "something"), ("play italian music", "music"),
    ("play top Pink Floyd songs", "songs"), ("play beach vibes", "vibes"), ("play jazz", None),
    ("play Portishead", None),
])
def test_style_markers(text, marker):
    assert parse(text).style_marker == marker


def test_a_type_word_beats_a_style_marker():
    p = parse("play the song Jazz Music")
    assert (p.type, p.style_marker) == ("track", None)


@pytest.mark.parametrize("text,name", [
    ("play music by pink floyd", "pink floyd"),
    ("Play Top Songs by Weird Al Yankovic", "Weird Al Yankovic"),
    ("play something by Portishead", "Portishead"),
    ("play the best songs by Moby", "Moby"),
])
def test_songs_by_an_artist_asks_for_the_artist(text, name):
    p = parse(text)
    assert (p.by_generic, p.type, p.name, p.artist) == (True, "artist", name, None), p


def test_x_by_y_and_quotes():
    p = parse('play "Don\'t stop me now" by queen on the Satellite 1')
    assert (p.name, p.artist, p.room, p.room_kind) == ("Don't stop me now", "queen", "satellite 1 ma", "player")


@pytest.mark.parametrize("text,provider", [
    ("shuffle my YouTube playlist anime bangers", "ytmusic"),
    ("Shuffle the YouTube music playlist, anime bangers.", "ytmusic"),
    ("Play French Dinner Radio on Pandora.", "pandora"),
    ("Play hotel costes albums from Atlas, on the kitchen", "filesystem_local"),
    ("play Blue Lines from the NAS", "filesystem_local"),
    ("play Blue Lines", None),
])
def test_named_provider(text, provider):
    assert parse(text).provider == provider


def test_shuffle_comes_from_the_verb():
    assert parse("shuffle the playlist old man sings").shuffle is True
    assert parse("play the playlist old man sings").shuffle is False


# ── criterion 2: rooms from the right-hand end, against real names ──────────

def test_title_with_in_keeps_its_title_and_targets_the_origin():
    p = parse("play The Girl with the Sun in Her Head by Orbital")
    assert (p.name, p.artist, p.room) == ("The Girl with the Sun in Her Head", "Orbital", None)


def test_title_with_in_and_a_room_at_the_end():
    p = parse("play The Girl with the Sun in Her Head by Orbital in the office")
    assert (p.name, p.artist, p.room, p.room_kind) == ("The Girl with the Sun in Her Head", "Orbital", "Office", "area")


def test_only_the_right_hand_clause_is_a_room():
    p = parse("play Toys in the Attic by Aerosmith in the kitchen")
    assert (p.name, p.artist, p.room) == ("Toys in the Attic", "Aerosmith", "Kitchen")


@pytest.mark.parametrize("text,name", [
    ("play Hell's Kitchen", "Hell's Kitchen"),                 # a room word, no preposition
    ("play Office Space", "Office Space"),
    ("play Lost in the Kitchen Tapes", "Lost in the Kitchen Tapes"),   # a room inside a longer clause
    ("Play Handcuffed To A Fence In Mississippi Radio", "Handcuffed To A Fence In Mississippi"),
])
def test_room_words_inside_a_title_stay_in_the_title(text, name):
    p = parse(text)
    assert (p.name, p.room) == (name, None), p


def test_the_basement_player_with_no_area_is_reached_by_its_name():
    p = parse("play some jazz on the basement voice pe ma")
    assert (p.room, p.room_kind) == ("basement voice pe ma", "player")
    assert p.words == "some jazz"


@pytest.mark.parametrize("text,room,kind", [
    ("play pink floyd on the sonos move", "Sonos Move Derp", "player"),             # part of a name
    ("Play K-pop radio on Sonos Move Dirt.", "Sonos Move Derp", "player"),          # a near match
    ("Play top Pink Floyd songs on the Sonos Move Derp speaker.", "Sonos Move Derp", "player"),
    ("play jazz in the Office", "Office", "area"),
    ("play pink floyd on the blue group", "blue group", "unknown"),                 # said as a target
])
def test_room_matching(text, room, kind):
    p = parse(text)
    assert (p.room, p.room_kind) == (room, kind), p


def test_a_room_and_a_provider_in_either_order():
    a = parse("Play deep chill house radio from Pandora in the kitchen")
    b = parse("Play the album Destiny Waving by Ulrich Schnauss from YouTube music, in the kitchen")
    c = parse("play Blue Lines in the office on YouTube Music")
    assert (a.room, a.provider, a.name) == ("Kitchen", "pandora", "deep chill house")
    assert (b.room, b.provider) == ("Kitchen", "ytmusic")
    assert (c.room, c.provider, c.name) == ("Office", "ytmusic", "Blue Lines")


def test_match_room_never_takes_a_name_inside_the_clause():
    assert mr.match_room("the kitchen tapes", PLAYERS, 0.85) is None
    assert mr.match_room("the kitchen", PLAYERS, 0.85) == ("Kitchen", "area")


def test_room_key_is_what_resolve_players_compares():
    import tool_service.main as ts
    for s in ("the Office speaker", "Sonos-Move_Derp players", "My Kitchen room"):
        assert ts._norm(s) == mr.room_key(s)


# ── criterion 3: non-music "play", with routing's exclusions ─────────────────

@pytest.mark.parametrize("text", [
    "play the news", "put on the news", "play the news in the kitchen", "play a game with me",
    "play the movie Inception in the living room", "play a video of cats", "play my podcast",
    "start a timer for ten minutes", "set a timer", "what's playing",
])
def test_not_music(text):
    p = parse(text)
    assert p.music is False and p.reason, p


@pytest.mark.parametrize("text", [
    "play the soundtrack album from the movie Inception",   # a music noun wins, as in routing
    "play video game pretty songs", "shuffle the playlist video game pretty songs",
])
def test_music_nouns_win_over_the_exclusions_as_in_routing(text):
    assert parse(text).music is True


def _routing_words(pattern: str, group_re: str) -> set[str]:
    m = re.search(group_re, pattern)
    assert m, group_re
    return {w.replace(r"\s+", " ") for w in m.group(1).split("|")}


def test_exclusion_lists_agree_with_routing():
    """One list each side (orchestrator and tool_service build separate
    images); this test keeps them equal."""
    pat = routing._MUSIC_RE.pattern
    assert _routing_words(pat, r"\(\?!\.\*\\b\(\?:([a-z|]+)\)\\b\)\\s\+\\S") == set(mr.NOT_MUSIC_AFTER_PLAY)
    assert set(re.findall(r"\(\?!\.\*\\b(\w+)\\b\)", pat)) == set(mr.NOT_MUSIC_ALWAYS)
    nouns = re.search(r"\\b\(\?:(radio\|.*?)\)\\b", pat, re.S).group(1)
    assert {w.replace(r"\s+", " ") for w in re.sub(r'"\s*r?"', "", nouns).split("|")} == set(mr.MUSIC_NOUNS)


@pytest.mark.parametrize("text", [
    "play the news", "play a game with me", "play the movie Inception", "play a video", "play my podcast",
    "play jazz", "put on a movie", "play the music from the movie Inception", "start a timer",
    "start orbital radio", "shuffle my videos", "play video game pretty songs",
])
def test_parser_and_routing_agree_on_what_is_music(text):
    """Where routing's pin calls a request-verb sentence music, so does the
    parser, and the other way round."""
    assert parse(text).music == bool(routing._MUSIC_RE.search(text)), text


# ── criterion 4: radio_name and messy transcripts ────────────────────────────

def test_radio_name_still_stops_at_the_full_stop():
    assert mr.radio_name("Play Anime Pop Radio. That's probably about it.") == "Anime Pop"
    p = parse("Play Anime Pop Radio. That's probably about it. Everything is pretty wet.")
    assert (p.type, p.name, p.radio_name) == ("radio", "Anime Pop", "Anime Pop")


def test_the_parser_does_not_trim_a_messy_transcript():
    """Only a station is cut at the full stop (radio_name); anything else
    keeps the words. Messy input is the M14 gate's to route, not ours."""
    p = parse("Play Portishead. Thanks, that's all for now.")
    assert p.words == "Portishead. Thanks, that's all for now"


def test_room_after_a_station_and_trailing_speech():
    p = parse("Play Anime Pop Radio. That's probably about it. Everything is pretty wet, "
              "put it on the basement voice pe ma.")
    assert (p.name, p.room, p.room_kind) == ("Anime Pop", "basement voice pe ma", "player")


# ── the words come from config/music.yaml ────────────────────────────────────

def test_parser_words_are_read_from_config():
    words = config_loader.current().music.parser
    assert parse("play beach vibes").style_marker == "vibes"
    with patch.object(words, "style_markers", ["some", "something", "music", "songs"]):
        assert parse("play beach vibes").style_marker is None
    with patch.object(words, "type_words", {**words.type_words, "record": "album"}):
        assert parse("play the record Blue Lines").type == "album"


@pytest.mark.parametrize("bad", [
    {"style_markers": ["Some"]}, {"style_markers": ["some", "some"]}, {"fillers": [" top"]},
    {"type_words": {"album": "cd"}}, {"type_words": {"Album": "album"}}, {"room_min_score": 1.5},
    {"style_markers": []},
])
def test_parser_section_is_validated(bad):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        config_loader.Music.model_validate({"parser": bad})
