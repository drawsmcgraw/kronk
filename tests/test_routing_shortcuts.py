"""Table-driven tests pinning the router's deterministic regex shortcuts.

These regexes route BEFORE any LLM call — a false positive hijacks the
request, a false negative costs a router round. Observed failure classes
from production sessions are pinned here so they can't regress silently.
"""
import pytest

import routing


# ── CLEAR_HISTORY_RE: voice/UI "wipe my conversation" intent ────────────────

@pytest.mark.parametrize("text", [
    "clear my history",
    "Clear my history please",
    "forget this conversation",
    "erase the chat",
    "wipe my context",
    "reset our conversation",
    "start over with a fresh conversation",
])
def test_clear_history_matches(text):
    assert routing.CLEAR_HISTORY_RE.search(text)


@pytest.mark.parametrize("text", [
    "tell me about the history of Rome",          # 'history' alone is content
    "start over",                                  # ambiguous mid-task phrase
    "what conversations do whales have?",
    "clear skies tomorrow?",
])
def test_clear_history_rejects(text):
    assert not routing.CLEAR_HISTORY_RE.search(text)


# ── _SEARCH_PHRASES: explicit research routing ──────────────────────────────

@pytest.mark.parametrize("text", [
    "search the web for the latest BIOS version",
    "look up the weather in Tokyo",
    "look it up",
    "google the answer",
])
def test_search_phrases_match(text):
    assert routing._SEARCH_PHRASES.search(text)
# 2026-08-18: "search for …" / "what is the latest …" removed from the
# must-match corpus — bare-verb and recency phrasings fall to the
# coordinator now (COORDINATOR_ROUTING_PLAN precision rule). The
# hot-chicken meta-question limitation ("what search terms did you try?")
# is fixed by the same change — see the target-behavior section below.


# ── _WEATHER_RE: weather/forecast → home (incident 2026-07-05) ──────────────
# "what is tomorrow's forecast?" routed to research via the LLM classifier
# (despite 'forecast' in home's routing hint) and burned its whole tool
# budget on repeated web searches.

@pytest.mark.parametrize("text", [
    "what is tomorrow's forecast?",
    "what's the weather like",
    "will the weather be nice this weekend?",
])
def test_weather_matches(text):
    assert routing._WEATHER_RE.search(text)
# 2026-08-18: "give me the forecast" removed — bare 'forecast' released
# (precision rule); that phrasing rides the coordinator → ask_home now.


@pytest.mark.parametrize("text", [
    "the forecastle of the ship",   # word boundary
    "is it going to rain tomorrow?",  # deliberately NOT matched — the
                                      # coordinator handles it; widen the
                                      # regex only with trace evidence
])
def test_weather_rejects(text):
    assert not routing._WEATHER_RE.search(text)


# ── Solar: the topic pin is GONE (2026-08-18, evening) ──────────────────────
# The pin was narrowed in the morning, then removed the same day: solar
# questions are frequently composite ("money's worth…"), topic regexes
# can't see compositeness, and the escalation net proved probabilistic at
# 4B (1-for-2 on identical inputs, traces 1e6d1603/143bbc62). ALL solar
# queries now ride the coordinator, which reaches home via ask_home.

@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "what's the solar panel status",                              # single-domain
    "how are my inverters doing",
    "how much money's worth did the solar panels produce yesterday?",  # composite
])
async def test_solar_queries_ride_the_coordinator(text):
    route, rule = await routing._classify_inner(text)
    assert route == "direct"
    assert rule == "default"
    assert not hasattr(routing, "_SOLAR_RE")   # the topic pin stays dead
# ── magic mirror: multi-agent split (update→home, else→devops) ───────────────

@pytest.mark.parametrize("text", [
    "update the magic mirror",
    "can you update the magic mirror software",
])
def test_mm_update_matches(text):
    assert routing._MM_RE.search(text) and routing._MM_UPDATE_RE.search(text)


@pytest.mark.parametrize("text", [
    "what's the uptime of the magic mirror",
    "why is the magic mirror showing an error",
    "restart the magicmirror",          # a mutation, but NOT the update phrase →
    "show me the magic mirror logs",    # devops; phase-B gate handles restart
    "upgrade the magicmirror",          # 2026-08-18: only the exact phrase
                                        # "update the magic mirror" pins the
                                        # terminal update tool → devops lane
])
def test_mm_ops_matches_but_not_update(text):
    assert routing._MM_RE.search(text)
    assert not routing._MM_UPDATE_RE.search(text)


# ── _DIRECT_OVERRIDE: explicit "don't search" ───────────────────────────────

@pytest.mark.parametrize("text", [
    "don't search for it, just answer",
    "no web search please",
    "answer from your own knowledge",
    "without searching, what do you think?",
])
def test_direct_override_matches(text):
    assert routing._DIRECT_OVERRIDE.search(text)


# ── _TALKIE_PHRASES: explicit-invocation persona ────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("ask talkie about the moon", True),
    ("what does talkie think of jazz?", True),
    ("talkie's opinion on radio", True),
    ("we talked yesterday", False),
    # NOTE: bare "talkie " mid-sentence DOES match (loose by design — the
    # persona is harmless when invoked accidentally); plural doesn't.
    ("talkies were early sound films", False),
])
def test_talkie_phrases(text, expected):
    assert bool(routing._TALKIE_PHRASES.search(text)) is expected


# ═══ TARGET BEHAVIOR — docs/plans/COORDINATOR_ROUTING_PLAN.md ═══════════════
# Written 2026-08-18 BEFORE implementation as xfail(strict=True) — the
# plan's proof harness. Step-1 markers flipped (deleted) 2026-08-18 when the
# shortcut narrowing landed: these now pin the fixed behavior permanently.

# Offenders: bare topic nouns no longer pin (precision rule (c)). With the
# solar pin now fully removed, these ride the coordinator like everything
# else — pinned at classify level.
@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "when is the next solar eclipse?",
    "how many planets are in the solar system?",
])
async def test_plan_solar_offenders_no_longer_pin(text):
    route, _ = await routing._classify_inner(text)
    assert route == "direct"


@pytest.mark.parametrize("text", [
    "what's the revenue forecast for AMD?",   # was a pinned KNOWN LIMITATION
    "we'll weather the storm and carry on",
])
def test_plan_weather_offenders_no_longer_pin(text):
    assert not routing._WEATHER_RE.search(text)


@pytest.mark.parametrize("text", [
    "search my shopping list for milk",       # bare 'search' hijack
    "what search terms did you try?",         # was a pinned KNOWN LIMITATION
    "what is the latest python release",      # recency ≠ method
])
def test_plan_search_offenders_no_longer_pin(text):
    assert not routing._SEARCH_PHRASES.search(text)


# Mirror update pin fires only on the exact command phrase — questions
# ABOUT updates never reach the terminal update tool (a mutation path).
@pytest.mark.parametrize("text", [
    "did the magic mirror update break anything?",
    "the magic mirror needs an update, right?",
])
def test_plan_mm_update_questions_no_longer_pin(text):
    assert not routing._MM_UPDATE_RE.search(text)


# Regression guards: phrasings the narrowed patterns MUST keep. These pass
# today too (no xfail) — they exist so step 1 can't over-narrow.
@pytest.mark.parametrize("text", [
    "what's the weather today",
    "what is tomorrow's forecast?",
])
def test_plan_weather_keepers_still_pin(text):
    assert routing._WEATHER_RE.search(text)


# (The solar "keeper" guards from the morning's narrowing were deleted with
# the pin itself — there is nothing left to keep pinned.)


@pytest.mark.parametrize("text", [
    "search the web for BGE electricity rates",
    "look it up",
    "google the answer",
])
def test_plan_search_keepers_still_pin(text):
    assert routing._SEARCH_PHRASES.search(text)


def test_plan_mm_update_exact_phrase_still_pins():
    assert routing._MM_RE.search("update the magic mirror")
    assert routing._MM_UPDATE_RE.search("update the magic mirror")


# ── Step 2: route collapse — unmatched queries go to the coordinator with
# NO LLM router call. Uses the motivating trace phrasing (rid 7d62ffb3,
# 2026-08-17), which LLM-routed to research and answered wrong. Flipped
# 2026-08-18 when the router deletion landed.

def test_plan_no_llm_router_remains():
    """Structural proof: routing.py no longer imports the LLM client at
    all — there is no code path that could consult a routing model."""
    assert not hasattr(routing, "llm")
    assert not hasattr(routing, "ROUTER_MODEL")


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "how much money's worth have the panels produced so far today?",
    "what's a good pasta recipe?",
])
async def test_plan_unmatched_routes_to_coordinator(text):
    route, rule = await routing._classify_inner(text)
    assert route == "direct"
    assert rule == "default"


# ── Direct dispatch (sprint M7): utterance → (tool, args) ───────────────────
# Five rules fix the whole tool call from the match's named groups; the
# pipeline runs it with no model call. The words are the plan's table
# (M7, "Scope"); the "regex extra" lines are spellings today's regexes
# already accepted that the table does not list, with what they map to.

PB = "control_music"
DIRECT_TABLE = [
    # playback → control_music
    ("stop", PB, {"action": "pause"}),
    ("Stop!", PB, {"action": "pause"}),
    (" Stop!", PB, {"action": "pause"}),
    ("stop the music", PB, {"action": "pause"}),
    ("pause", PB, {"action": "pause"}),
    ("Pause.", PB, {"action": "pause"}),
    ("please pause the music", PB, {"action": "pause"}),
    ("pause it please", PB, {"action": "pause"}),
    ("shut up", PB, {"action": "pause"}),
    ("resume", PB, {"action": "resume"}),
    ("Resume the music.", PB, {"action": "resume"}),
    ("unpause", PB, {"action": "resume"}),
    ("skip", PB, {"action": "next"}),
    ("Skip!", PB, {"action": "next"}),
    ("skip this song", PB, {"action": "next"}),
    ("next", PB, {"action": "next"}),
    ("next track", PB, {"action": "next"}),             # regex extra
    ("skip the song", PB, {"action": "next"}),          # regex extra
    ("louder", PB, {"action": "volume_up"}),
    ("turn it up", PB, {"action": "volume_up"}),
    ("turn that up", PB, {"action": "volume_up"}),      # regex extra
    ("turn the music up", PB, {"action": "volume_up"}), # regex extra
    ("turn the volume up", PB, {"action": "volume_up"}),  # regex extra
    ("volume up", PB, {"action": "volume_up"}),
    ("quieter", PB, {"action": "volume_down"}),
    ("Turn it down.", PB, {"action": "volume_down"}),
    ("turn the volume down", PB, {"action": "volume_down"}),  # regex extra
    ("volume down", PB, {"action": "volume_down"}),
    # now_playing → now_playing, no arguments
    ("what song is this", "now_playing", {}),
    ("What's playing?", "now_playing", {}),
    ("Who is this?", "now_playing", {}),
    ("who sings this song", "now_playing", {}),
    ("what am I listening to", "now_playing", {}),
    # rate → rate_music
    ("thumbs up", "rate_music", {"thumb": "up"}),
    ("Thumbs up.", "rate_music", {"thumb": "up"}),
    ("thumbs up on this song", "rate_music", {"thumb": "up"}),
    ("thumb up", "rate_music", {"thumb": "up"}),           # regex extra
    ("I like this song", "rate_music", {"thumb": "up"}),
    ("love this one", "rate_music", {"thumb": "up"}),
    ("upvote", "rate_music", {"thumb": "up"}),
    ("up vote this song", "rate_music", {"thumb": "up"}),  # regex extra
    ("more like this", "rate_music", {"thumb": "up"}),
    ("kronk, more like this song please", "rate_music", {"thumb": "up"}),
    ("thumbs down", "rate_music", {"thumb": "down"}),
    ("Thumbs down.", "rate_music", {"thumb": "down"}),
    ("I hate this song", "rate_music", {"thumb": "down"}),
    ("dislike this track", "rate_music", {"thumb": "down"}),
    ("I don't like this song", "rate_music", {"thumb": "down"}),
    ("never play this again", "rate_music", {"thumb": "down"}),
    ("never play this song again", "rate_music", {"thumb": "down"}),
    ("don't play that one again", "rate_music", {"thumb": "down"}),  # regex extra
    ("downvote", "rate_music", {"thumb": "down"}),
    ("less like this", "rate_music", {"thumb": "down"}),
    # music_another → play_music kind another; a named room is the player
    ("not that", "play_music", {"query": "another", "kind": "another"}),
    ("no, not this one", "play_music", {"query": "another", "kind": "another"}),
    ("play something else", "play_music", {"query": "another", "kind": "another"}),
    ("try something different", "play_music", {"query": "another", "kind": "another"}),
    ("a different song please", "play_music", {"query": "another", "kind": "another"}),
    ("not that in the office", "play_music",
     {"query": "another", "kind": "another", "player": "office"}),
    ("something else in the Basement Theater.", "play_music",
     {"query": "another", "kind": "another", "player": "Basement Theater"}),
    ("not that on the Satellite1 speaker", "play_music",
     {"query": "another", "kind": "another", "player": "Satellite1 speaker"}),
    # mm_update → update_magicmirror, no arguments (its own guard still runs)
    ("update the magic mirror", "update_magicmirror", {}),
    ("Update the magicmirror", "update_magicmirror", {}),
    ("Okay, update the magic mirror please", "update_magicmirror", {}),
]

DIRECT_RULES = {"playback", "now_playing", "rate", "music_another", "mm_update"}


@pytest.mark.parametrize("text,tool,args", DIRECT_TABLE, ids=[t for t, _, _ in DIRECT_TABLE])
def test_direct_call_from_the_match(text, tool, args):
    d = routing.decide(text)
    assert d.route == "home" and d.rule in DIRECT_RULES, d
    assert d.direct == (tool, args)


@pytest.mark.parametrize("text,rule", [
    # documented negatives (plan M7 Verify; _PLAYBACK_RE's comment)
    ("stop the timer", "default"),
    ("pause my subscription", "default"),
    ("what is the next holiday", "default"),
    # a named room keeps the request on the model path (plan M7 Tradeoff)
    ("pause the music in the office", "default"),
    ("skip this song in the kitchen", "default"),
    ("what's playing in the kitchen", "default"),
    ("thumbs up in the office", "default"),
    # rules outside M7's scope never dispatch directly
    ("play another one", "music"),          # not _ANOTHER_RE; see M7-build.md
    ("play some jazz", "music"),
    ("never play nickelback", "taste"),
    ("what's the weather like", "weather"),
    ("look up the magic mirror", "search_phrase"),
    ("update magic mirror", "mm_ops"),
    ("what's the uptime of the magic mirror", "mm_ops"),
    ("I like this", "default"),
    ("hello", "default"),
])
def test_no_direct_call_off_the_five_rules(text, rule):
    d = routing.decide(text)
    assert d.rule == rule and d.direct is None, d


def test_exactly_the_five_rules_carry_a_direct_call():
    assert {r.name for r in routing.RULES if r.direct} == DIRECT_RULES


def test_every_pinned_utterance_decides_with_a_direct_call_iff_in_scope():
    """Over the 6415-utterance routing pin: decide() never raises (one
    action group per match), and a direct call exists exactly for the five
    rules."""
    import json
    from pathlib import Path
    pin = Path(__file__).resolve().parent / "fixtures" / "routing_pin.jsonl"
    for line in pin.read_text().splitlines():
        row = json.loads(line)
        d = routing.decide(row["text"])
        assert (d.route, d.rule) == (row["route"], row["rule"]), row
        assert (d.direct is not None) == (d.rule in DIRECT_RULES), row


def test_direct_tools_are_home_terminal_tools():
    """The direct call runs a tool the home agent holds and ends the turn
    on, so the direct path and the agent path end the same way."""
    import agents
    home = agents.AGENTS["home"]
    for text, tool, _ in DIRECT_TABLE:
        assert tool in home.terminal_tools, tool
        assert tool in {d["function"]["name"] for d in home.tool_defs()}, tool


def test_direct_args_are_fresh_per_call():
    a, b = routing.decide("not that").direct, routing.decide("not that").direct
    a[1]["player"] = "x"
    assert "player" not in b[1]


@pytest.mark.asyncio
async def test_direct_decision_event_carries_direct_true(caplog):
    import json
    caplog.set_level("INFO", logger="kronk.events")
    assert await routing.classify("pause", []) == "home"
    ev = [json.loads(r.getMessage()) for r in caplog.records if r.name == "kronk.events"][-1]
    assert {k: ev[k] for k in ("event", "rule", "route", "direct", "tool")} == {
        "event": "route_shortcut", "rule": "playback", "route": "home",
        "direct": True, "tool": "control_music"}
