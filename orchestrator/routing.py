"""Phase-1 request router: deterministic shortcuts; everything else → the
coordinator.

The LLM classifier (gemma-3-4b) was removed 2026-08-18 — see
docs/plans/COORDINATOR_ROUTING_PLAN.md. Routing is now structural: a
narrow shortcut pins a query to its specialist, and every unmatched query
goes to the coordinator, which answers directly or delegates via its
ask_* tools. A shortcut miss costs seconds (one coordinator hop), never a
wrong lane — so shortcuts are precision-first: they pin only on a named
household entity, an explicit user-stated method, or domain vocabulary
with context.

Since sprint M7 the rules are an ordered table (RULES, below), and five of
them carry a direct tool call that the pipeline runs with no model at all —
docs/features/direct-dispatch.md. Since sprint M19 the first rule is the
spoken lead-in ("ask Kronk to ..."), which skips every other rule and hands
the rest of the sentence to the coordinator — docs/features/lead-in.md.
Since sprint M14 the `messy` rule (the messy-transcript gate) sends a request
with more than one sentence, or too many words, to the coordinator ahead of
every fast path that reads the text as one clean command —
docs/features/messy-transcript-gate.md.
"""
import functools
import re
from dataclasses import dataclass
from typing import Callable

import config_loader
import telemetry
import transcript_gate
from events import emit

_URL_RE = re.compile(r'https?://')

# A YouTube Music link is a play request, not a page to read: the research
# agent fetched one and chatted about it (2026-10-19,
# INVESTIGATION_2026-10-19_ytmusic_album_misses.md). tool_service turns the
# link into an MA URI. Plain youtube.com / youtu.be links pin only with a
# play verb — "summarize this video" stays with research.
_MUSIC_URL_RE = re.compile(
    r"https?://music\.youtube\.com/"
    r"|^\W*(?:please\s+)?(?:play|put\s+on|listen\s+to)\b.*https?://(?:www\.)?(?:youtube\.com/watch|youtu\.be/)",
    re.IGNORECASE | re.DOTALL,
)

# Talkie is a vintage 1930s character model — only invoke it when explicitly named.
# Checked first so it takes priority over all other routing.
_TALKIE_PHRASES = re.compile(
    r"\b(ask\s+talkie|talkie[,\s]+|what\s+does\s+talkie\s+(think|say|know|believe)|"
    r"talkie'?s\s+(opinion|view|take|thoughts?|perspective)|"
    r"have\s+talkie|get\s+talkie\s+to|let\s+talkie)\b",
    re.IGNORECASE,
)

# "Clear my history" intent — handled by the transports BEFORE routing (it
# needs the requesting client's session id, which the router doesn't have).
# Lives here beside its sibling patterns so all deterministic phrase-matching
# is in one place. Deliberately strict: bare "start over" alone could be a
# legitimate request inside a task, so every variant names the conversation.
CLEAR_HISTORY_RE = re.compile(
    r"\b(clear|erase|delete|forget|wipe|reset)\s+(my\s+|the\s+|our\s+|this\s+)?"
    r"(history|conversation|chat( history)?|context)\b"
    r"|\bstart\s+(over|fresh)\s+(with\s+a\s+)?(new|clean|fresh)\s+(conversation|chat|history)\b",
    re.IGNORECASE,
)

# If the user explicitly says not to search / use the research agent, route direct
# regardless of other signals. Checked before search phrases so "don't search for
# that" doesn't accidentally match the search-phrase pattern.
_DIRECT_OVERRIDE = re.compile(
    r"don'?t\s+(use\s+)?(the\s+)?(research|search|web|internet)(\s+agent)?"
    r"|no\s+(web\s+|internet\s+)?search"
    r"|from\s+your\s+(own\s+)?(knowledge|training|memory)"
    r"|generate\s+your\s+own\s+answer"
    r"|answer\s+(it\s+)?yourself"
    r"|without\s+(searching|the\s+web|internet)"
    r"|no\s+answer\s+online"
    r"|there'?s?\s+no\s+answer\s+online",
    re.IGNORECASE,
)

# Explicit search phrases that reliably indicate a research task.
# Precision rule (COORDINATOR_ROUTING_PLAN, 2026-08-18): a shortcut pins only
# on an explicit user-stated METHOD — so `search` requires its qualifier
# ("search the web/online"); bare "search"/"search for" hijacked local asks
# ("search my shopping list") and "what is the latest" is recency, not
# method. Unmatched phrasings fall to the coordinator: slow-but-right.
_SEARCH_PHRASES = re.compile(
    r'\b(search\s+(the\s+web|online)|web\s+search|look\s+up|look\s+it\s+up|'
    r'find(\s+me)?\s+(online|on\s+the\s+web|on\s+the\s+internet)|'
    r'google|news\s+about)\b',
    re.IGNORECASE,
)

# Weather queries belong to the home agent (NWS tool + prompt-injected cache).
# Contextual forms only (precision rule): a weather word plus a question
# shape or a timeframe. Keeps the 2026-07-05 forecast-misroute fix and the
# frequent voice phrasings while releasing "AMD's revenue forecast" /
# "weather the storm" to the coordinator. Checked AFTER _SEARCH_PHRASES on
# purpose: explicit "look up the weather in Tokyo" keeps routing to
# research, which works for non-US locations (NWS is US-only).
_WEATHER_RE = re.compile(
    r"what'?s?\s+(is\s+)?the\s+weather"
    r"|\bweather\b.{0,40}\b(today|tonight|tomorrow|this\s+week(end)?)\b"
    r"|\b(today|tonight|tomorrow)'?s?\s+(weather|forecast)\b"
    r"|\bforecast\s+for\s+(today|tonight|tomorrow|this\s+week(end)?)\b"
    r"|\bweather\s+forecast\b",
    re.IGNORECASE,
)

# The solar TOPIC pin was removed 2026-08-18 (same day it was narrowed):
# solar queries are frequently composite ("money's worth of solar…"), a
# topic regex can't see compositeness, and the escalation net under the pin
# proved probabilistic at 4B (1-for-2 on identical inputs — traces
# 1e6d1603 vs 143bbc62). All solar questions now ride the coordinator,
# which reaches home via ask_home: composites compose reliably; status
# checks pay one delegation hop (~+4 s). Lesson recorded in
# docs/features/coordinator-default-routing.md: METHOD pins (user states
# the how) are sound; TOPIC pins only survive on frequency + simplicity
# grounds (weather, mirror).

# "magic mirror" is a multi-agent entity: home owns the fast named-safe
# UPDATE (a terminal tool); devops owns arbitrary diagnostics/ops (the
# remote_exec loop). The update pin fires ONLY on the exact command phrase
# — questions *about* updates ("did the magic mirror update break
# anything?") must not reach a mutation tool (2026-08-18 audit). Everything
# else mirror → devops.
#
# _MM_UPDATE_RE is also the only sentence that may run update_magicmirror
# (Drew, 2026-11-16, sprint M4b): tools._tool_update_magicmirror refuses
# unless the user's own words for the turn (origin.said) match it, so no
# other route — the coordinator, an escalation, fetched page text — can
# start an update. "magicmirror" is accepted so speech-to-text spacing
# cannot trip it. Defined here once; both users call asks_for_mirror_update.
_MM_RE        = re.compile(r'\bmagic\s*mirror\b', re.IGNORECASE)
_MM_UPDATE_RE = re.compile(r'\bupdate\s+the\s+magic\s*mirror\b', re.IGNORECASE)


def asks_for_mirror_update(text: str | None) -> bool:
    """True when `text` contains the pinned mirror-update sentence."""
    return bool(text) and _MM_UPDATE_RE.search(text) is not None

# Bare playback commands ("Stop!", "pause the music", "skip this song",
# "louder") pin to the home agent, which owns control_music. A METHOD pin
# in the plan's sense: the whole utterance IS the command, so there is no
# compositeness to lose. Added 2026-09-10 after the coordinator answered a
# bare "Stop!" with "Stop what?" (INVESTIGATION_2026-09-10_voice_stop).
# Anchored start-to-end on purpose: "stop the timer", "pause my
# subscription", "what is the next holiday" must not match.
# Sprint M7: the match also fixes control_music's action, so the rule calls
# the tool directly (RULES below). Each branch carries one named group;
# _PLAYBACK_ACTIONS maps it to the action. "stop" means pause (the home
# prompt's rule and tool_service's: pause keeps the queue for "resume").
_PLAYBACK_RE = re.compile(
    r"^\W*(?:please\s+)?(?:"
    r"(?:(?P<pause>stop|pause|shut\s+up)|(?P<resume>resume|unpause)|(?P<next>skip|next))"
    r"(?:\s+(?:the|this|that|my)?\s*(?:music|song|track|it|playback|playing))?"
    r"|(?P<next_track>skip|next)\s+(?:this\s+|the\s+)?(?:song|track)"
    r"|turn\s+(?:it|that|the\s+(?:music|volume))\s+(?:(?P<turn_up>up)|(?P<turn_down>down))"
    r"|(?P<louder>louder)|(?P<quieter>quieter)"
    r"|volume\s+(?:(?P<volume_up>up)|(?P<volume_down>down))"
    r")(?:\s+please)?\W*$",
    re.IGNORECASE,
)
_PLAYBACK_ACTIONS = {
    "pause": "pause", "resume": "resume", "next": "next", "next_track": "next",
    "turn_up": "volume_up", "louder": "volume_up", "volume_up": "volume_up",
    "turn_down": "volume_down", "quieter": "volume_down", "volume_down": "volume_down",
}

# Music requests pin to the home agent, which owns play_music. Added
# 2026-10-05 after the coordinator answered blueprint misses itself with no
# tool call: "Like massive attack radio" → a copy of the previous turn's
# failure text (2026-10-04); "Start an orbital radio station on YouTube
# Music" → "I am unable to play specific streams from YouTube Music"
# (2026-10-05, INVESTIGATION_2026-10-04_radio_failures.md). The prompt's
# PLAYBACK rule did not hold; the loop does (tenet 5). Three shapes, all
# anchored to a request verb at the start:
#   - verbs that only ever mean music (put on, listen to, shuffle, queue
#     up, throw on) need no noun: "put on some jazz";
#   - "play"/"start" + a music noun or source anywhere after: "start an
#     orbital radio station on youtube music";
#   - bare "play <object>" ("play daft punk") unless the object is a game,
#     video, movie or the news — HA's built-in intent usually takes bare
#     "play X" before Kronk sees it; this covers the prefixed/mangled rest.
# "news" is excluded everywhere (news_brief is the coordinator's tool).
# A STT-mangled verb ("like massive attack radio") is a documented miss —
# it still rides the coordinator. Bare "start X" without a music noun is
# left alone on purpose (timers).
_MUSIC_RE = re.compile(
    r"^\W*(?:please\s+|kronk,?\s+|hey\s+kronk,?\s+|tell\s+kronk\s+to\s+|can\s+you\s+|could\s+you\s+)*"
    r"(?!.*\bnews\b)(?:"
    r"(?:put\s+on|throw\s+on|listen\s+to|shuffle|queue\s+up)\b"
    r"|(?:play|start)\b.*\b(?:radio|station|music|song|songs|track|tracks|album|albums"
    r"|playlist|playlists|artist|mix|pandora|youtube|spotify|atlas|the\s+nas)\b"
    r"|play\b(?!.*\b(?:game|games|video|videos|movie|movies|podcast)\b)\s+\S"
    r")",
    re.IGNORECASE | re.DOTALL,
)

# Thumbs on the playing track pin to the home agent's rate_music tool
# (2026-10-06, MA_LOCAL_PANDORA_FEATURES_PLAN). Anchored; the whole utterance
# is the verdict. "I like this" alone is left to the coordinator — too
# ambiguous without "song"/"one"/"track".
# Sprint M7: the named group that matched fixes rate_music's thumb
# (_RATE_THUMBS), so the rule calls the tool directly.
_RATE_RE = re.compile(
    r"^\W*(?:please\s+|kronk,?\s+)*(?:"
    r"thumbs?\s*(?:(?P<thumbs_up>up)|(?P<thumbs_down>down))"
    r"(?:\s+(?:on|for|to)?\s*(?:this|that|the)?\s*(?:song|track|one|station|it))?"
    r"|(?:i\s+)?(?:(?P<like>like|love)|(?P<dislike>hate|dislike|don'?t\s+like))"
    r"\s+(?:this|that)\s+(?:song|track|one|tune)"
    r"|(?P<never_again>(?:never|don'?t)\s+play\s+(?:this|that)(?:\s+(?:song|track|one))?\s+again)"
    r"|(?:(?P<upvote>up)|(?P<downvote>down))\s*vote(?:\s+(?:this|that)(?:\s+(?:song|track|one))?)?"
    r"|(?:(?P<more>more)|(?P<less>less))\s+like\s+(?:this|that)(?:\s+(?:song|track|one))?"
    r")(?:\s+please)?\W*$",
    re.IGNORECASE,
)
_RATE_THUMBS = {
    "thumbs_up": "up", "like": "up", "upvote": "up", "more": "up",
    "thumbs_down": "down", "dislike": "down", "never_again": "down", "downvote": "down",
    "less": "down",
}

# "Not that" after Kronk picked a genre plays the next pick (MUSIC_TASTE_
# AGENT_PLAN step 5). Anchored: the whole utterance is the rejection.
_ANOTHER_RE = re.compile(
    r"^\W*(?:please\s+|kronk,?\s+)*(?:"
    r"(?:no,?\s+)?not\s+(?:that|this)(?:\s+one)?"
    r"|(?:play\s+|try\s+|put\s+on\s+)?something\s+(?:else|different)"
    r"|(?:play\s+|try\s+)?(?:a\s+)?different\s+(?:one|song|station|music|genre|artist)"
    r")(?:\s+please)?"
    # the music blueprint appends the satellite's room (KRONK change 9);
    # the `room` group is passed on as play_music's player (sprint M7)
    r"(?:\s+(?:in|on)\s+the\s+(?P<room>[\w' -]{1,40}?))?\W*$",
    re.IGNORECASE,
)

# Stated household taste pins to the home agent's remember_music_preference
# (MUSIC_TASTE_AGENT_PLAN step 3). Precision-first: "never play X" (not
# this/that — those are thumbs, _RATE_RE), "remember (that) we love X",
# "we love X" only with a music noun ("we love the band X"), and "when I
# say X I mean Y". Bare "we love Ulrich Schnauss" rides the coordinator,
# which reaches the tool through ask_home.
_TASTE_RE = re.compile(
    r"^\W*(?:please\s+|kronk,?\s+)*(?:"
    r"(?:never|don'?t\s+ever|do\s+not\s+ever)\s+play\s+(?!(?:this|that|it)\b)\S"
    r"|remember\s+(?:that\s+)?(?:we|i)\s+(?:really\s+)?(?:love|like|hate|dislike|don'?t\s+like|can'?t\s+stand)\s+\S"
    r"|(?:we|i)\s+(?:really\s+)?(?:love|like|hate|dislike|don'?t\s+like|can'?t\s+stand)\s+"
    r"(?!(?:this|that|it)\b).*\b(?:music|band|artist|album|songs)\b"
    r"|when\s+(?:we|i)\s+say\s+\S.*\b(?:we|i)\s+mean\s+\S"
    r"|forget\s+(?:that\s+)?(?:we|i)\s+(?:love|like|hate|dislike)\s+\S"
    r")",
    re.IGNORECASE,
)

# "What song is this?" and its siblings pin to the home agent's now_playing
# tool (2026-09-14, MUSIC_ACCURACY_PLAN). Anchored; the "what's" branch is
# limited to this/that/playing/song so "what's the weather" never matches.
_NOW_PLAYING_RE = re.compile(
    r"^\W*(?:"
    r"what(?:'s|\s+is)\s+(?:this|that|playing|on|(?:this|that|the)\s+song|currently\s+playing|playing\s+(?:right\s+)?now)"
    r"|what\s+song\s+is\s+(?:this|that|playing|on)"
    r"|which\s+song\s+is\s+this"
    r"|who(?:'s|\s+is)\s+(?:this|that|singing|playing)(?:\s+song)?"
    r"|who\s+(?:sings|sang|made|performs)\s+this(?:\s+song)?"
    r"|what\s+am\s+i\s+listening\s+to"
    r"|what\s+(?:song|track|artist|band)\s+is\s+this"
    r")\W*$",
    re.IGNORECASE,
)


# The spoken lead-in (sprint M19; Drew, 2026-11-11): "ask Kronk to <request>"
# is the one way to skip every rule below and hand a request to the model.
# Shape: [filler ...] <verb> <name> [to] <request>, at the start of the
# utterance, any casing. The words live in config/lead_in.yaml
# (config_loader.LeadIn); each is escaped, so no config entry is a pattern.
# Anchored on purpose: "what happens if I ask Kronk to pause" is not a
# lead-in and is routed by the rest of the table. The name must end at a
# separator, so "ask Kronk's opinion" is not one either. "to" is optional
# and needs a word boundary: "ask Kronk tonight's forecast" keeps "tonight's".
_LEAD_IN_SEP = r"[\s,.!?;:]"
# With nothing after the lead-in (punctuation, or a lone "please") there is
# no request to hand on: not a lead-in, so the utterance is routed as usual.
_EMPTY_REQUEST = re.compile(r"[\W_]*(?:please[\W_]*)?", re.IGNORECASE)


def _alternatives(phrases) -> str:
    """One regex alternation of `phrases`, longest first, each escaped, with
    any run of whitespace between its words."""
    words = sorted(phrases, key=len, reverse=True)
    return "|".join(r"\s+".join(re.escape(w) for w in p.split()) for p in words)


@functools.lru_cache(maxsize=8)
def _lead_in_pattern(verbs: tuple, names: tuple, fillers: tuple) -> re.Pattern:
    filler = (rf"(?:(?:{_alternatives(fillers)})\b{_LEAD_IN_SEP}*)*" if fillers else "")
    return re.compile(
        rf"[\W_]*{filler}(?:{_alternatives(verbs)})[\s,]+(?:{_alternatives(names)})"
        rf"(?={_LEAD_IN_SEP}|\Z){_LEAD_IN_SEP}*(?:to\b\s*)?(?P<request>.*)",
        re.IGNORECASE | re.DOTALL,
    )


def lead_in_pattern() -> re.Pattern:
    """The lead-in pattern for the words in effect (config_loader.current()),
    so a config reload is followed on the next request."""
    li = config_loader.current().lead_in
    return _lead_in_pattern(tuple(li.verbs), tuple(li.names), tuple(li.fillers))


def lead_in_request(text: str | None) -> str | None:
    """The request after a spoken lead-in at the start of `text`, or None
    when `text` does not start with one or nothing follows it."""
    m = lead_in_pattern().match(text or "")
    if not m:
        return None
    request = m.group("request").strip()
    return None if _EMPTY_REQUEST.fullmatch(request) else request


# The messy-transcript gate (sprint M14; Drew, 2026-11-11): messy input goes
# to the model, the rules handle clean input only. transcript_gate.check()
# is the only place "messy" is decided; the limits are config/tuning.yaml's
# transcript_gate, read on each call so a reload is followed. Nothing is
# trimmed: the coordinator gets the whole transcript.
def messy(text: str | None) -> transcript_gate.Messy | None:
    """Why `text` is messy (more sentences or more words than the limits
    in effect), or None when it is clean or the gate is off."""
    g = config_loader.current().tuning.transcript_gate
    return transcript_gate.check(text, g.max_sentences, g.max_words)


def asks_to_clear_history(text: str | None) -> bool:
    """The transports' "clear my history" intercept, which runs before
    routing: CLEAR_HISTORY_RE anywhere in a request the gate calls clean.
    A messy one ("Play jazz. Forget the conversation.") goes to the
    pipeline like any other (sprint M14)."""
    return bool(text) and CLEAR_HISTORY_RE.search(text) is not None and messy(text) is None


# ── The rules table (review item O12, sprint M7) ─────────────────────────────
#
# Routing is this ordered table: the first rule whose matcher accepts the
# text decides the route. `default` is last and accepts everything — no LLM
# classifier: everything unmatched goes to the coordinator, which answers
# directly or delegates via its ask_* tools. Structural routing (tenet 5): a
# 4B model can't pick the wrong specialist if specialists aren't on its menu.
#
# Matchers look the patterns up when called, not when the table is built,
# so a test that swaps a pattern (tests/test_mm_update_pin.py) is followed.
# The order and every outcome are pinned by tests/test_routing_pin.py; no
# rule is added, removed, reordered or widened without captured data
# showing the same failure several times (stopping rule, Drew 2026-11-11).

#
# Direct dispatch (sprint M7). For five rules the match alone fixes the tool
# call, so `direct` builds (tool_name, args) from the match's named groups
# and the pipeline runs that tool with no model call
# (main._run_pipeline, docs/features/agents-as-tools-routing.md). Every
# other rule has direct=None and goes to its agent as before.
#
# The lead-in (sprint M19) is the one rule with `request`: it builds, from
# the match, the text the model is given in place of the utterance (the
# request without the lead-in). main._run_pipeline gives the coordinator
# that text and sets origin.said to it.
#
# The messy gate (sprint M14) is the one rule with `detail`: why it fired
# (which half, the counts, the limits), for its event and span.

DirectCall = tuple[str, dict]


@dataclass(frozen=True)
class Rule:
    name: str                      # the `rule` in events, spans and the corpus
    route: str                     # an agent name, or "direct" (the coordinator)
    match: Callable[[str], object]  # truthy when the rule applies
    direct: Callable[[object], DirectCall] | None = None   # match -> the tool call
    request: Callable[[object], str] | None = None         # match -> the model's text
    detail: Callable[[object], dict] | None = None         # match -> why, for event and span


@dataclass(frozen=True)
class Decision:
    route: str
    rule: str
    direct: DirectCall | None = None   # (tool_name, args) to run without a model
    request: str | None = None         # the model's text, when not the utterance
    detail: dict | None = None         # why the rule fired (the messy gate)


def _group(m: re.Match, table: dict[str, str]) -> str:
    """The value for the one named group of `table` that took part in m."""
    hit = [table[k] for k, v in m.groupdict().items() if v is not None and k in table]
    if len(hit) != 1:
        raise AssertionError(f"expected one action group in {m.re.pattern[:40]!r}: {m.groupdict()}")
    return hit[0]


def _another_call(m: re.Match) -> DirectCall:
    # query "another" per the tool definition; a room the user (or the
    # blueprint) named is the player, as the home prompt has the model pass it.
    args = {"query": "another", "kind": "another"}
    if m.group("room"):
        args["player"] = m.group("room")
    return "play_music", args


RULES: tuple[Rule, ...] = (
    # The lead-in first (sprint M19): it skips every rule after it, Talkie's
    # included ("ask Kronk to ..." asks Kronk). The coordinator gets the
    # request without the lead-in, with its full tool menu.
    Rule("lead_in",         "direct",   lambda t: lead_in_request(t),
         request=lambda req: req),
    # Talkie next: it takes priority over all other routing.
    Rule("talkie_explicit", "talkie", lambda t: _TALKIE_PHRASES.search(t)),
    Rule("direct_override", "direct", lambda t: _DIRECT_OVERRIDE.search(t)),
    Rule("music_url",       "home",     lambda t: _MUSIC_URL_RE.search(t)),
    Rule("url",             "research", lambda t: _URL_RE.search(t)),
    # The messy-transcript gate (sprint M14). The five rules above stay
    # ahead of it because none reads the text as one clean command: the
    # lead-in and direct_override already go to the coordinator, Talkie is
    # named explicitly and is not on the coordinator's menu, and a typed
    # link is the request's object wherever it sits. Every rule below reads
    # the utterance as a single command (anchored, or a phrase anywhere
    # taken to be the request's method or topic), so a transcript with
    # trailing speech or several requests goes to the coordinator, whole.
    Rule("messy",           "direct",   lambda t: messy(t),
         detail=lambda m: m.as_dict()),
    Rule("search_phrase",   "research", lambda t: _SEARCH_PHRASES.search(t)),
    Rule("weather",         "home",     lambda t: _WEATHER_RE.search(t)),
    Rule("playback",        "home",     lambda t: _PLAYBACK_RE.search(t),
         direct=lambda m: ("control_music", {"action": _group(m, _PLAYBACK_ACTIONS)})),
    Rule("now_playing",     "home",     lambda t: _NOW_PLAYING_RE.search(t),
         direct=lambda m: ("now_playing", {})),
    Rule("rate",            "home",     lambda t: _RATE_RE.search(t),
         direct=lambda m: ("rate_music", {"thumb": _group(m, _RATE_THUMBS)})),
    Rule("music_another",   "home",     lambda t: _ANOTHER_RE.search(t),
         direct=_another_call),
    Rule("taste",           "home",     lambda t: _TASTE_RE.search(t)),
    Rule("music",           "home",     lambda t: _MUSIC_RE.search(t)),
    # The magic mirror: the pinned update sentence is home's fast terminal
    # tool; anything else about the mirror is devops' remote_exec loop.
    # update_magicmirror's own guard (origin.said) still decides whether
    # the update runs: the direct call goes through tools.execute.
    Rule("mm_update",       "home",
         lambda t: _MM_RE.search(t) and asks_for_mirror_update(t),
         direct=lambda m: ("update_magicmirror", {})),
    Rule("mm_ops",          "devops",   lambda t: _MM_RE.search(t)),
    Rule("default",         "direct",   lambda t: True),
)


def decide(text: str) -> Decision:
    """The routing decision for `text`: the first rule in RULES that
    accepts it, with its direct call when it has one. Pure: no event, no
    span (classify() records those), so the pipeline can call it before
    taking the model lock."""
    for rule in RULES:
        if m := rule.match(text):
            return Decision(rule.route, rule.name,
                            rule.direct(m) if rule.direct else None,
                            rule.request(m) if rule.request else None,
                            rule.detail(m) if rule.detail else None)
    raise AssertionError("unreachable: the default rule accepts everything")


async def classify(text: str, prior_history: list[dict]) -> str:
    """Return a route: a shortcut-pinned specialist, else "direct" (the
    coordinator).

    prior_history is unused since the LLM classifier was removed — kept for
    transport API stability (both shims and /message call this signature).
    """
    span = telemetry.root().child_span("routing.decide", input=text[:200])
    try:
        d = _record(decide(text), text)
        meta = {"rule": d.rule}
        if d.direct:
            meta["direct"] = d.direct[0]
        if d.request is not None:
            # The lead-in (M19): how much of the utterance was cut off.
            meta["lead_in_chars"] = len(text) - len(d.request)
        if d.detail:
            # The messy gate (M14): which half fired, the counts, the limits.
            meta.update({f"messy_{k}": v for k, v in d.detail.items()})
        span.end(output=d.route, metadata=meta)
        return d.route
    except Exception as e:
        span.end(level="ERROR", status_message=str(e)[:200])
        raise


def _record(d: Decision, text: str) -> Decision:
    """Log the decision: `route_shortcut` for a pinned rule (with
    direct=true and the tool when it dispatches directly), `route` for the
    default."""
    if d.rule == "default":
        emit("route", text_preview=text[:60], route=d.route, rule=d.rule)
    elif d.direct:
        emit("route_shortcut", rule=d.rule, route=d.route, direct=True, tool=d.direct[0])
    elif d.detail:
        # The messy gate (M14): reason, sentences, words, max_sentences, max_words.
        emit("route_shortcut", rule=d.rule, route=d.route, **d.detail)
    else:
        emit("route_shortcut", rule=d.rule, route=d.route)
    return d


async def _classify_inner(text: str) -> tuple[str, str]:
    """Returns (route, rule) where rule names the deciding mechanism, and
    logs the decision."""
    d = _record(decide(text), text)
    return d.route, d.rule
