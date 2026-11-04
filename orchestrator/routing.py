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
"""
import re

import telemetry
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
_MM_RE        = re.compile(r'\bmagic\s*mirror\b', re.IGNORECASE)
_MM_UPDATE_RE = re.compile(r'\bupdate\s+the\s+magic\s*mirror\b', re.IGNORECASE)

# Bare playback commands ("Stop!", "pause the music", "skip this song",
# "louder") pin to the home agent, which owns control_music. A METHOD pin
# in the plan's sense: the whole utterance IS the command, so there is no
# compositeness to lose. Added 2026-09-10 after the coordinator answered a
# bare "Stop!" with "Stop what?" (INVESTIGATION_2026-09-10_voice_stop).
# Anchored start-to-end on purpose: "stop the timer", "pause my
# subscription", "what is the next holiday" must not match.
_PLAYBACK_RE = re.compile(
    r"^\W*(?:please\s+)?(?:"
    r"(?:stop|pause|resume|unpause|skip|next|shut\s+up)"
    r"(?:\s+(?:the|this|that|my)?\s*(?:music|song|track|it|playback|playing))?"
    r"|(?:skip|next)\s+(?:this\s+|the\s+)?(?:song|track)"
    r"|turn\s+(?:it|that|the\s+(?:music|volume))\s+(?:up|down)"
    r"|louder|quieter|volume\s+(?:up|down)"
    r")(?:\s+please)?\W*$",
    re.IGNORECASE,
)

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
_RATE_RE = re.compile(
    r"^\W*(?:please\s+|kronk,?\s+)*(?:"
    r"thumbs?\s*(?:up|down)(?:\s+(?:on|for|to)?\s*(?:this|that|the)?\s*(?:song|track|one|station|it))?"
    r"|(?:i\s+)?(?:like|love|hate|dislike|don'?t\s+like)\s+(?:this|that)\s+(?:song|track|one|tune)"
    r"|(?:never|don'?t)\s+play\s+(?:this|that)(?:\s+(?:song|track|one))?\s+again"
    r"|(?:up|down)\s*vote(?:\s+(?:this|that)(?:\s+(?:song|track|one))?)?"
    r"|(?:more|less)\s+like\s+(?:this|that)(?:\s+(?:song|track|one))?"
    r")(?:\s+please)?\W*$",
    re.IGNORECASE,
)

# "Not that" after Kronk picked a genre plays the next pick (MUSIC_TASTE_
# AGENT_PLAN step 5). Anchored: the whole utterance is the rejection.
_ANOTHER_RE = re.compile(
    r"^\W*(?:please\s+|kronk,?\s+)*(?:"
    r"(?:no,?\s+)?not\s+(?:that|this)(?:\s+one)?"
    r"|(?:play\s+|try\s+|put\s+on\s+)?something\s+(?:else|different)"
    r"|(?:play\s+|try\s+)?(?:a\s+)?different\s+(?:one|song|station|music|genre|artist)"
    r")(?:\s+please)?"
    # the music blueprint appends the satellite's room (KRONK change 9)
    r"(?:\s+(?:in|on)\s+the\s+[\w' -]{1,40}?)?\W*$",
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


async def classify(text: str, prior_history: list[dict]) -> str:
    """Return a route: a shortcut-pinned specialist, else "direct" (the
    coordinator).

    prior_history is unused since the LLM classifier was removed — kept for
    transport API stability (both shims and /message call this signature).
    """
    span = telemetry.root().child_span("routing.decide", input=text[:200])
    try:
        route, rule = await _classify_inner(text)
        span.end(output=route, metadata={"rule": rule})
        return route
    except Exception as e:
        span.end(level="ERROR", status_message=str(e)[:200])
        raise


async def _classify_inner(text: str) -> tuple[str, str]:
    """Returns (route, rule) where rule names the deciding mechanism."""
    if _TALKIE_PHRASES.search(text):
        emit("route_shortcut", rule="talkie_explicit", route="talkie")
        return "talkie", "talkie_explicit"
    if _DIRECT_OVERRIDE.search(text):
        emit("route_shortcut", rule="direct_override", route="direct")
        return "direct", "direct_override"
    if _MUSIC_URL_RE.search(text):
        emit("route_shortcut", rule="music_url", route="home")
        return "home", "music_url"
    if _URL_RE.search(text):
        emit("route_shortcut", rule="url", route="research")
        return "research", "url"
    if _SEARCH_PHRASES.search(text):
        emit("route_shortcut", rule="search_phrase", route="research")
        return "research", "search_phrase"
    if _WEATHER_RE.search(text):
        emit("route_shortcut", rule="weather", route="home")
        return "home", "weather"
    if _PLAYBACK_RE.search(text):
        emit("route_shortcut", rule="playback", route="home")
        return "home", "playback"
    if _NOW_PLAYING_RE.search(text):
        emit("route_shortcut", rule="now_playing", route="home")
        return "home", "now_playing"
    if _RATE_RE.search(text):
        emit("route_shortcut", rule="rate", route="home")
        return "home", "rate"
    if _ANOTHER_RE.search(text):
        emit("route_shortcut", rule="music_another", route="home")
        return "home", "music_another"
    if _TASTE_RE.search(text):
        emit("route_shortcut", rule="taste", route="home")
        return "home", "taste"
    if _MUSIC_RE.search(text):
        emit("route_shortcut", rule="music", route="home")
        return "home", "music"
    if _MM_RE.search(text):
        if _MM_UPDATE_RE.search(text):
            emit("route_shortcut", rule="mm_update", route="home")
            return "home", "mm_update"       # fast terminal tool
        emit("route_shortcut", rule="mm_ops", route="devops")
        return "devops", "mm_ops"            # remote_exec diagnostics loop

    # No LLM classifier: everything unmatched goes to the coordinator, which
    # answers directly or delegates via its ask_* tools. Structural routing
    # (tenet 5): a 4B model can't pick the wrong specialist if specialists
    # aren't on its menu.
    emit("route", text_preview=text[:60], route="direct", rule="default")
    return "direct", "default"
