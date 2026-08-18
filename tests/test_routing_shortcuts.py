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
