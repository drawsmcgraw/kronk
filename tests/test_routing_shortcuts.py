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
    "search for the latest BIOS version",
    "look up the weather in Tokyo",
    "look it up",
    "google the answer",
    "what is the latest python release",
])
def test_search_phrases_match(text):
    assert routing._SEARCH_PHRASES.search(text)


# Known misroute class (hot-chicken session 2026-06-12): meta-questions
# about Kronk's own past actions contain the word 'search' and shortcut to
# the research agent. Pinned as a KNOWN LIMITATION — if someone fixes the
# regex, flip these expectations.
@pytest.mark.parametrize("text", [
    "what search terms did you try?",
])
def test_search_phrase_known_meta_question_limitation(text):
    assert routing._SEARCH_PHRASES.search(text)  # documents current behavior


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
