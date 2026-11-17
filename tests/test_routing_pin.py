"""The routing pin (sprint M7, criterion 1; plan row C4).

Written and committed from the tree at 7c83f36, BEFORE routing._classify_inner
became an ordered rules table (review item O12). It pins three things, so the
refactor can be shown to change no routing outcome:

1. The 15 outcomes and their order. Every pinned utterance lands on the
   first rule, in ORDER, whose matcher accepts it, where the matchers are
   FROZEN copies of the 7c83f36 patterns kept in this file (so the order
   check does not depend on the code it checks).
2. The (route, rule) of every utterance in tests/fixtures/routing_pin.jsonl:
   the voice corpus, docs/bench/routing_eval.jsonl, every string the test
   suite fed to a routing regex at 7c83f36, the precedence cases in OVERLAPS
   and the generated variants below. Generated once by
   tests/fixtures/make_routing_pin.py; see its docstring before regenerating.
3. Each routing regex accepts exactly what its frozen copy accepts on every
   pinned utterance, so adding named groups (M7) cannot widen or narrow one.

No rule may be added, removed, reordered or widened (stopping rule, Drew
2026-11-11). A deliberate routing change updates the frozen copies and the
fixture in the same commit and says why.

Sprint M19 made the one deliberate change so far: the `lead_in` rule ("ask
Kronk to ...", decided by Drew 2026-11-11), first in the order, 16 outcomes.
Its frozen matcher is FROZEN_LEAD_IN below (the pattern built from the
shipped config/lead_in.yaml); its utterances were appended to the fixture
and every earlier row kept its (route, rule).

Sprint M14 made the second: the `messy` rule (the messy-transcript gate,
decided by Drew 2026-11-11), after `url`, 17 outcomes. Its frozen matcher is
transcript_gate.check with the shipped limits (FROZEN_GATE_LIMITS); the
counter itself is pinned by tests/test_transcript_gate.py. The 83 fixture
rows the gate calls messy changed their (route, rule) to direct/messy, in
place (69 of them were direct/default already); every other row kept its
(route, rule), and the gate's own utterances were appended.
docs/sprint/M14-build.md lists the 83.
"""
import asyncio
import itertools
import json
import re
from pathlib import Path

import pytest

import routing
import transcript_gate

PIN = Path(__file__).resolve().parent / "fixtures" / "routing_pin.jsonl"

# The 17 outcomes, in the order _classify_inner tries them (plan, "Verified
# on Kronk, 2026-11-15", M7 line; `lead_in` first since M19; `messy` after
# `url` since M14). "default" is the fall-through.
ORDER = [
    "lead_in", "talkie_explicit", "direct_override", "music_url", "url", "messy", "search_phrase",
    "weather", "playback", "now_playing", "rate", "music_another", "taste",
    "music", "mm_update", "mm_ops", "default",
]
ROUTES = {
    "lead_in": "direct", "talkie_explicit": "talkie", "direct_override": "direct", "music_url": "home",
    "url": "research", "messy": "direct", "search_phrase": "research", "weather": "home",
    "playback": "home", "now_playing": "home", "rate": "home",
    "music_another": "home", "taste": "home", "music": "home",
    "mm_update": "home", "mm_ops": "devops", "default": "direct",
}

# ── Frozen copies of routing.py's patterns at 7c83f36 ────────────────────────
# Byte-for-byte the pattern strings and flags as they stood before M7.

_I, _D = re.IGNORECASE, re.DOTALL
FROZEN = {
    "_TALKIE_PHRASES": re.compile(
        r"\b(ask\s+talkie|talkie[,\s]+|what\s+does\s+talkie\s+(think|say|know|believe)|"
        r"talkie'?s\s+(opinion|view|take|thoughts?|perspective)|"
        r"have\s+talkie|get\s+talkie\s+to|let\s+talkie)\b", _I),
    "_DIRECT_OVERRIDE": re.compile(
        r"don'?t\s+(use\s+)?(the\s+)?(research|search|web|internet)(\s+agent)?"
        r"|no\s+(web\s+|internet\s+)?search"
        r"|from\s+your\s+(own\s+)?(knowledge|training|memory)"
        r"|generate\s+your\s+own\s+answer"
        r"|answer\s+(it\s+)?yourself"
        r"|without\s+(searching|the\s+web|internet)"
        r"|no\s+answer\s+online"
        r"|there'?s?\s+no\s+answer\s+online", _I),
    "_MUSIC_URL_RE": re.compile(
        r"https?://music\.youtube\.com/"
        r"|^\W*(?:please\s+)?(?:play|put\s+on|listen\s+to)\b.*https?://(?:www\.)?(?:youtube\.com/watch|youtu\.be/)",
        _I | _D),
    "_URL_RE": re.compile(r'https?://'),
    "_SEARCH_PHRASES": re.compile(
        r'\b(search\s+(the\s+web|online)|web\s+search|look\s+up|look\s+it\s+up|'
        r'find(\s+me)?\s+(online|on\s+the\s+web|on\s+the\s+internet)|'
        r'google|news\s+about)\b', _I),
    "_WEATHER_RE": re.compile(
        r"what'?s?\s+(is\s+)?the\s+weather"
        r"|\bweather\b.{0,40}\b(today|tonight|tomorrow|this\s+week(end)?)\b"
        r"|\b(today|tonight|tomorrow)'?s?\s+(weather|forecast)\b"
        r"|\bforecast\s+for\s+(today|tonight|tomorrow|this\s+week(end)?)\b"
        r"|\bweather\s+forecast\b", _I),
    "_PLAYBACK_RE": re.compile(
        r"^\W*(?:please\s+)?(?:"
        r"(?:stop|pause|resume|unpause|skip|next|shut\s+up)"
        r"(?:\s+(?:the|this|that|my)?\s*(?:music|song|track|it|playback|playing))?"
        r"|(?:skip|next)\s+(?:this\s+|the\s+)?(?:song|track)"
        r"|turn\s+(?:it|that|the\s+(?:music|volume))\s+(?:up|down)"
        r"|louder|quieter|volume\s+(?:up|down)"
        r")(?:\s+please)?\W*$", _I),
    "_NOW_PLAYING_RE": re.compile(
        r"^\W*(?:"
        r"what(?:'s|\s+is)\s+(?:this|that|playing|on|(?:this|that|the)\s+song|currently\s+playing|playing\s+(?:right\s+)?now)"
        r"|what\s+song\s+is\s+(?:this|that|playing|on)"
        r"|which\s+song\s+is\s+this"
        r"|who(?:'s|\s+is)\s+(?:this|that|singing|playing)(?:\s+song)?"
        r"|who\s+(?:sings|sang|made|performs)\s+this(?:\s+song)?"
        r"|what\s+am\s+i\s+listening\s+to"
        r"|what\s+(?:song|track|artist|band)\s+is\s+this"
        r")\W*$", _I),
    "_RATE_RE": re.compile(
        r"^\W*(?:please\s+|kronk,?\s+)*(?:"
        r"thumbs?\s*(?:up|down)(?:\s+(?:on|for|to)?\s*(?:this|that|the)?\s*(?:song|track|one|station|it))?"
        r"|(?:i\s+)?(?:like|love|hate|dislike|don'?t\s+like)\s+(?:this|that)\s+(?:song|track|one|tune)"
        r"|(?:never|don'?t)\s+play\s+(?:this|that)(?:\s+(?:song|track|one))?\s+again"
        r"|(?:up|down)\s*vote(?:\s+(?:this|that)(?:\s+(?:song|track|one))?)?"
        r"|(?:more|less)\s+like\s+(?:this|that)(?:\s+(?:song|track|one))?"
        r")(?:\s+please)?\W*$", _I),
    "_ANOTHER_RE": re.compile(
        r"^\W*(?:please\s+|kronk,?\s+)*(?:"
        r"(?:no,?\s+)?not\s+(?:that|this)(?:\s+one)?"
        r"|(?:play\s+|try\s+|put\s+on\s+)?something\s+(?:else|different)"
        r"|(?:play\s+|try\s+)?(?:a\s+)?different\s+(?:one|song|station|music|genre|artist)"
        r")(?:\s+please)?"
        r"(?:\s+(?:in|on)\s+the\s+[\w' -]{1,40}?)?\W*$", _I),
    "_TASTE_RE": re.compile(
        r"^\W*(?:please\s+|kronk,?\s+)*(?:"
        r"(?:never|don'?t\s+ever|do\s+not\s+ever)\s+play\s+(?!(?:this|that|it)\b)\S"
        r"|remember\s+(?:that\s+)?(?:we|i)\s+(?:really\s+)?(?:love|like|hate|dislike|don'?t\s+like|can'?t\s+stand)\s+\S"
        r"|(?:we|i)\s+(?:really\s+)?(?:love|like|hate|dislike|don'?t\s+like|can'?t\s+stand)\s+"
        r"(?!(?:this|that|it)\b).*\b(?:music|band|artist|album|songs)\b"
        r"|when\s+(?:we|i)\s+say\s+\S.*\b(?:we|i)\s+mean\s+\S"
        r"|forget\s+(?:that\s+)?(?:we|i)\s+(?:love|like|hate|dislike)\s+\S"
        r")", _I),
    "_MUSIC_RE": re.compile(
        r"^\W*(?:please\s+|kronk,?\s+|hey\s+kronk,?\s+|tell\s+kronk\s+to\s+|can\s+you\s+|could\s+you\s+)*"
        r"(?!.*\bnews\b)(?:"
        r"(?:put\s+on|throw\s+on|listen\s+to|shuffle|queue\s+up)\b"
        r"|(?:play|start)\b.*\b(?:radio|station|music|song|songs|track|tracks|album|albums"
        r"|playlist|playlists|artist|mix|pandora|youtube|spotify|atlas|the\s+nas)\b"
        r"|play\b(?!.*\b(?:game|games|video|videos|movie|movies|podcast)\b)\s+\S"
        r")", _I | _D),
    "_MM_RE": re.compile(r'\bmagic\s*mirror\b', _I),
    "_MM_UPDATE_RE": re.compile(r'\bupdate\s+the\s+magic\s*mirror\b', _I),
}

# The lead-in (sprint M19), as built from the shipped config/lead_in.yaml:
# [filler ...] ask Kronk [to] <request>, with a request that is not empty.
FROZEN_LEAD_IN = re.compile(
    r"[\W_]*(?:(?:could\s+you|can\s+you|please|okay|hey|ok)\b[\s,.!?;:]*)*(?:ask)[\s,]+(?:Kronk)"
    r"(?=[\s,.!?;:]|\Z)[\s,.!?;:]*(?:to\b\s*)?(?P<request>.*)", _I | _D)
_FROZEN_EMPTY_REQUEST = re.compile(r"[\W_]*(?:please[\W_]*)?", _I)


def _frozen_lead_in(text: str) -> bool:
    m = FROZEN_LEAD_IN.match(text)
    return bool(m) and not _FROZEN_EMPTY_REQUEST.fullmatch(m.group("request").strip())


# The messy gate (sprint M14) with the limits shipped in config/tuning.yaml:
# more than one sentence, or more than 20 words.
FROZEN_GATE_LIMITS = (1, 20)


def _frozen_messy(text: str) -> bool:
    return transcript_gate.check(text, *FROZEN_GATE_LIMITS) is not None


# Rule -> its matcher at 7c83f36 (lead_in: M19, messy: M14), built from the
# frozen copies.
_F = FROZEN
FROZEN_MATCHERS = {
    "lead_in":         _frozen_lead_in,
    "talkie_explicit": lambda t: _F["_TALKIE_PHRASES"].search(t),
    "direct_override": lambda t: _F["_DIRECT_OVERRIDE"].search(t),
    "music_url":       lambda t: _F["_MUSIC_URL_RE"].search(t),
    "url":             lambda t: _F["_URL_RE"].search(t),
    "messy":           _frozen_messy,
    "search_phrase":   lambda t: _F["_SEARCH_PHRASES"].search(t),
    "weather":         lambda t: _F["_WEATHER_RE"].search(t),
    "playback":        lambda t: _F["_PLAYBACK_RE"].search(t),
    "now_playing":     lambda t: _F["_NOW_PLAYING_RE"].search(t),
    "rate":            lambda t: _F["_RATE_RE"].search(t),
    "music_another":   lambda t: _F["_ANOTHER_RE"].search(t),
    "taste":           lambda t: _F["_TASTE_RE"].search(t),
    "music":           lambda t: _F["_MUSIC_RE"].search(t),
    "mm_update":       lambda t: _F["_MM_RE"].search(t) and _F["_MM_UPDATE_RE"].search(t),
    "mm_ops":          lambda t: _F["_MM_RE"].search(t),
    "default":         lambda t: True,
}

# ── Utterances beyond the corpus, the eval set and the suite ────────────────

# Precedence: each matches two or more rules, so the earlier one must win.
OVERLAPS = [
    "ask talkie, don't search the web",                         # talkie > direct_override
    "what does talkie think about https://example.com",          # talkie > url
    "have talkie play some jazz",                               # talkie > music
    "don't search, play https://music.youtube.com/watch?v=abc",  # direct_override > music_url
    "from your own knowledge, what is https://example.com",     # direct_override > url
    "answer it yourself: look up the weather tomorrow",          # direct_override > search, weather
    "https://music.youtube.com/watch?v=abc",                    # music_url > url
    "play https://www.youtube.com/watch?v=abc",                 # music_url > url, music
    "summarize https://www.youtube.com/watch?v=abc",            # url, not music_url
    "look up https://example.com",                              # url > search_phrase
    "update the magic mirror from https://example.com",         # url > mm_update
    "look up the weather tomorrow",                             # search_phrase > weather
    "look up the magic mirror update",                          # search_phrase > mm_ops
    "google play some music",                                   # search_phrase > music
    "what's the weather on the magic mirror",                   # weather > mm_ops
    "play the weather forecast",                                # weather > music
    "what's the weather today in the office",                   # weather
    "play something else",                                      # music_another > music
    "put on something different",                               # music_another > music
    "play a different station in the office",                   # music_another > music
    "never play nickelback",                                    # taste
    "never play this again",                                    # rate, not taste
    "i don't like this song",                                   # rate, not taste
    "we love the band portishead",                              # taste
    "play the magic mirror song",                               # music > mm_ops
    "put on update the magic mirror",                           # music > mm_update
    "update the magic mirror",                                  # mm_update > mm_ops
    "please update the magic mirror and tell me what changed",  # mm_update (unanchored)
    "update magic mirror",                                      # mm_ops, not mm_update
    "restart the magic mirror",                                 # mm_ops
    "stop the timer",                                           # default
    "pause my subscription",                                    # default
    "what is the next holiday",                                 # default
    "pause the music in the office",                            # default (room named)
    "skip this song in the kitchen",                            # default (room named)
    "what's playing in the kitchen",                            # default (room named)
    "thumbs up in the office",                                  # default (room named)
    # sprint M19: the lead-in wins over every rule it overlaps
    "ask Kronk to pause",                                       # lead_in > playback
    "ask Kronk to play some jazz",                              # lead_in > music
    "ask Kronk what song is this",                              # lead_in > now_playing
    "ask Kronk to search the web for the best pizza in town",   # lead_in > search_phrase
    "ask Kronk to update the magic mirror",                     # lead_in > mm_update
    "ask Kronk to ask talkie about the moon",                   # lead_in > talkie
    "ask Kronk to answer it yourself",                          # lead_in > direct_override
    "ask Kronk what's the weather tomorrow",                    # lead_in > weather
    "ask Kronk to thumbs up",                                   # lead_in > rate
    "ask Kronk to play something else",                         # lead_in > music_another, music
    "ask Kronk to play https://music.youtube.com/watch?v=abc",  # lead_in > music_url, url
    "ask Kronk to never play nickelback",                       # lead_in > taste
    "ask Kronk to restart the magic mirror",                    # lead_in > mm_ops
    "what happens if I ask Kronk to pause",                     # default (not at the start)
    "tell Kronk to pause",                                      # default (not the lead-in verb)
    "ask Kronk",                                                # default (nothing after it)
    "ask Kronk to.",                                            # default (nothing after it)
    # sprint M14: the gate sits after the typed-link rules, before the rest
    "Play Anime Pop Radio. That's probably about it.",          # messy > music
    "Pause. Thank you.",                                        # messy (playback is anchored anyway)
    "Thumbs up!  I know but you missed the best part.",         # messy (rate is anchored anyway)
    "Never play Nickelback. Anyway, what's for dinner?",        # messy > taste
    "Look up the score. Then play some jazz.",                  # messy > search_phrase
    "What's the weather tomorrow? Play some jazz too.",         # messy > weather
    "Update the magic mirror. And play some jazz.",             # messy > mm_update
    "Thanks. Update the magic mirror.",                         # messy > mm_update
    "Restart the magic mirror. It froze again.",                # messy > mm_ops
    "play some jazz that is probably about it everything is pretty wet I just needed to make some space okay so yeah",  # messy (22 words) > music
    "Ask Kronk to play jazz. That's all.",                      # lead_in > messy
    "Ask talkie about the moon. Then the stars.",               # talkie > messy
    "Don't search the web. Just tell me. Why is the sky blue?",  # direct_override > messy
    "Play this. Here: https://music.youtube.com/watch?v=abc",         # music_url > messy
    "Summarize this. Here: https://example.com/a",                    # url > messy
    # look-alikes the gate calls clean (sprint M14 criterion 3)
    "Play Mr. Brightside",                                      # music
    "Play St. Vincent radio",                                   # music
    "Play R.E.M.",                                              # music
    "Play R.E.M. on the kitchen speaker",                       # music
    "play Dr. Dre",                                             # music
    "Play U2.",                                                 # music
    "Play Mambo No. 5 by Lou Bega",                             # music
    "Play Hotel Costas Radio. in the Kitchen",                  # music (the blueprint's room suffix)
    "Play... Anime Pop Radio",                                  # music (ellipsis)
    "Play Panic! at the Disco",                                 # music ("!" before lowercase)
    "Set an alarm for 7 a.m. tomorrow",                         # default
    "Is 98.6 a fever?",                                         # default
    "Wake me at 7.30.",                                         # default
]


def variants() -> list[str]:
    """Generated spellings of the five anchored commands M7 dispatches
    directly, and their near misses. Deterministic."""
    out: list[str] = []

    def product(*parts):
        for combo in itertools.product(*parts):
            out.append("".join(combo))

    # playback
    product(["", "please ", "Please, ", " "],
            ["stop", "Pause", "resume", "unpause", "skip", "next", "shut up", "louder",
             "quieter", "volume up", "volume down", "turn it up", "turn it down",
             "turn that up", "turn the music down", "turn the volume up", "mute", "play"],
            ["", " the music", " this song", " that track", " my playback", " it",
             " playing", " the song", " music", " in the office", " the timer",
             " my subscription"],
            ["", " please", ".", "!"])
    # rate
    product(["", "please ", "kronk, ", "Kronk "],
            ["thumbs up", "thumb up", "thumbsup", "thumbs down", "thumbs up on this song",
             "thumbs down for that track", "thumbs up to this one", "thumbs up the station",
             "thumbs down it", "like this song", "i like this song", "love that track",
             "I love this one", "hate this tune", "dislike this song", "don't like this song",
             "dont like that one", "i don't like this song", "never play this again",
             "never play this song again", "don't play that one again", "upvote", "up vote",
             "downvote", "down vote this song", "upvote that one", "more like this",
             "more like this song", "less like that", "less like that one", "i like this",
             "I love it", "thumbs sideways", "thumbs up in the office"],
            ["", " please", ".", "!"])
    # now_playing
    product(["", " "],
            ["what's this", "what is this", "what's playing", "what's on", "what's that song",
             "what is the song", "what's currently playing", "what's playing right now",
             "what's playing now", "what song is this", "what song is playing",
             "which song is this", "who's this", "who is that", "who's singing",
             "who is playing", "who is this song", "who sings this", "who sang this song",
             "who made this", "who performs this song", "what am i listening to",
             "what track is this", "what artist is this", "what band is this",
             "what's the weather", "what song is playing in the kitchen",
             "what's playing on the kitchen", "whats playing"],
            ["", "?", ".", "!"])
    # music_another
    product(["", "please ", "kronk, "],
            ["not that", "no not that", "no, not this", "not that one", "something else",
             "play something else", "try something different", "put on something else",
             "different song", "a different one", "play a different station",
             "try a different genre", "different artist", "play another one", "another one"],
            ["", " please"],
            ["", " in the office", " in the kitchen", " on the basement theater",
             " in the living room"],
            ["", ".", "!"])
    # mm_update / mm_ops
    product(["", "okay ", "can you ", "please "],
            ["update the magic mirror", "update the magicmirror", "Update the Magic Mirror",
             "update magic mirror", "upgrade the magic mirror", "did the magic mirror update",
             "update the magic mirror and tell me the weather", "update the  magic mirror"],
            ["", " please", ".", "?"])
    # lead_in (sprint M19) and its near misses
    product(["", " ", "hey, ", "Okay, ", "OK ", "please ", "can you ", "Could you ", "so "],
            ["ask Kronk to ", "Ask Kronk ", "ask kronk, ", "ASK KRONK TO ", "tell Kronk to ",
             "ask Kronky to ", "ask Kronk's "],
            ["pause", "play some jazz", "what song is this", "update the magic mirror",
             "", "please", "."],
            ["", "?", "."])
    # the messy gate (sprint M14): a command for each rule behind the gate,
    # alone and with a second sentence, a lowercase tail, or a run-on tail
    product(["", " "],
            ["Pause", "Stop!", "What song is this?", "Thumbs up", "Not that",
             "Never play Nickelback", "Play Anime Pop Radio", "Put on some jazz",
             "Look up the score", "What's the weather tomorrow?",
             "Update the magic mirror", "Restart the magic mirror"],
            ["", ".", ". That's probably about it.", ". in the Kitchen",
             "  Thank you.", "? Okay.",
             " that is probably about it everything is pretty wet I just needed to make some space"])
    return out


# Empty while make_routing_pin.py imports this module to build the file.
PIN_ROWS = ([json.loads(line) for line in PIN.read_text().splitlines() if line.strip()]
            if PIN.exists() else [])


def _first_frozen(text: str) -> str:
    return next(rule for rule in ORDER if FROZEN_MATCHERS[rule](text))


# ── 1. The outcomes and their order ──────────────────────────────────────────

def test_seventeen_outcomes_and_their_routes():
    assert len(ORDER) == 17 and len(set(ORDER)) == 17
    assert set(ROUTES) == set(ORDER)


def test_every_outcome_is_pinned():
    seen = {r["rule"] for r in PIN_ROWS}
    assert seen == set(ORDER), set(ORDER) - seen


def test_every_overlap_and_variant_is_pinned():
    texts = {r["text"] for r in PIN_ROWS}
    missing = [t for t in OVERLAPS + variants() if t not in texts]
    assert not missing, missing[:10]


def test_outcome_is_the_first_matching_rule_in_order():
    """Order pin: for every utterance, the rule it gets is the first in
    ORDER whose frozen 7c83f36 matcher accepts it."""
    wrong = []
    for r in PIN_ROWS:
        want = _first_frozen(r["text"])
        got = asyncio.run(routing._classify_inner(r["text"]))
        if got != (ROUTES[want], want):
            wrong.append((r["text"], want, got))
    assert not wrong, wrong[:10]


def test_order_matters_on_the_overlaps():
    """Each OVERLAPS line matches more than one rule (or is a pinned
    negative), so a reorder would change its outcome."""
    multi = [t for t in OVERLAPS
             if sum(bool(FROZEN_MATCHERS[r](t)) for r in ORDER[:-1]) >= 2]
    assert len(multi) >= 20, len(multi)


# ── 2. The snapshot ──────────────────────────────────────────────────────────

def test_pinned_route_and_rule_unchanged():
    wrong = []
    for r in PIN_ROWS:
        got = asyncio.run(routing._classify_inner(r["text"]))
        if got != (r["route"], r["rule"]):
            wrong.append((r["text"], (r["route"], r["rule"]), got))
    assert not wrong, f"{len(wrong)} pinned utterances changed: {wrong[:10]}"


def test_classify_returns_the_pinned_route():
    """routing.classify() (the pipeline's caller) returns the bare route."""
    wrong = []
    for r in PIN_ROWS:
        got = asyncio.run(routing.classify(r["text"], []))
        if got != r["route"]:
            wrong.append((r["text"], r["route"], got))
    assert not wrong, wrong[:10]


# ── 3. No regex widened or narrowed ──────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(FROZEN))
def test_regex_accepts_what_its_frozen_copy_accepts(name):
    live = getattr(routing, name)
    frozen = FROZEN[name]
    assert bool(live.flags & re.IGNORECASE) == bool(frozen.flags & re.IGNORECASE)
    diff = [r["text"] for r in PIN_ROWS
            if bool(live.search(r["text"])) != bool(frozen.search(r["text"]))]
    assert not diff, f"{name} differs from 7c83f36 on: {diff[:10]}"


def test_gate_limits_are_the_frozen_ones():
    """The limits in effect (the shipped config/tuning.yaml) are the frozen
    ones, so the gate decides every pinned utterance as the pin says
    (sprint M14)."""
    import config_loader
    g = config_loader.current().tuning.transcript_gate
    assert (g.max_sentences, g.max_words) == FROZEN_GATE_LIMITS
    diff = [r["text"] for r in PIN_ROWS if (routing.messy(r["text"]) is not None) != _frozen_messy(r["text"])]
    assert not diff, diff[:10]


def test_lead_in_matches_its_frozen_copy():
    """The lead-in pattern built from the shipped config/lead_in.yaml is the
    frozen one, and accepts (with a non-empty request) exactly what the
    frozen copy accepts on every pinned utterance (sprint M19)."""
    assert routing.lead_in_pattern().pattern == FROZEN_LEAD_IN.pattern
    assert routing.lead_in_pattern().flags & (re.I | re.S) == re.I | re.S
    assert routing._EMPTY_REQUEST.pattern == _FROZEN_EMPTY_REQUEST.pattern
    diff = [r["text"] for r in PIN_ROWS
            if (routing.lead_in_request(r["text"]) is not None) != _frozen_lead_in(r["text"])]
    assert not diff, diff[:10]


# ── 4. The table itself (O12, added with the refactor) ───────────────────────

def test_rules_table_is_the_seventeen_outcomes_in_order():
    assert [r.name for r in routing.RULES] == ORDER
    assert {r.name: r.route for r in routing.RULES} == ROUTES


def test_decision_events_keep_their_shape(caplog, monkeypatch):
    """A pinned rule logs route_shortcut {rule, route}; the default logs
    route {text_preview, route, rule}, as before the table. text_preview
    holds the words only with TELEMETRY_RECORD_TEXT on (M12)."""
    monkeypatch.setattr(routing.telemetry, "RECORD_TEXT", True)
    caplog.set_level("INFO", logger="kronk.events")
    asyncio.run(routing._classify_inner("what's the weather like"))
    asyncio.run(routing._classify_inner("hello there"))
    evs = [json.loads(r.getMessage()) for r in caplog.records if r.name == "kronk.events"]
    short, default = evs[-2], evs[-1]
    assert {k: short[k] for k in ("event", "rule", "route")} == {
        "event": "route_shortcut", "rule": "weather", "route": "home"}
    assert set(short) - {"ts", "rid", "trace_id"} == {"event", "rule", "route"}
    assert {k: default[k] for k in ("event", "text_preview", "route", "rule")} == {
        "event": "route", "text_preview": "hello there", "route": "direct", "rule": "default"}
