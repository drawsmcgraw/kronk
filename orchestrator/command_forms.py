"""The spoken command forms that more than one service reads.

The routing table (routing.py) pins these utterances to the home agent's
music tools. voice_tracer (sprint M12) reads the same forms to recognise a
correction: "not that", a thumbs down, a stop or pause, said right after a
voice request (voice_tracer/marks.py). One copy, so the two cannot drift
(tenet 8). Pure `re`, no imports from the rest of the orchestrator: the
voice_tracer image copies this one file (docker-compose.yml,
additional_contexts).

routing.py keeps its private names (_PLAYBACK_RE, ...) as aliases.
"""
import re

# Bare playback commands ("Stop!", "pause the music", "skip this song",
# "louder") pin to the home agent, which owns control_music. A METHOD pin
# in the plan's sense: the whole utterance IS the command, so there is no
# compositeness to lose. Added 2026-09-10 after the coordinator answered a
# bare "Stop!" with "Stop what?" (INVESTIGATION_2026-09-10_voice_stop).
# Anchored start-to-end on purpose: "stop the timer", "pause my
# subscription", "what is the next holiday" must not match.
# Sprint M7: the match also fixes control_music's action, so the rule calls
# the tool directly (routing.RULES). Each branch carries one named group;
# PLAYBACK_ACTIONS maps it to the action. "stop" means pause (the home
# prompt's rule and tool_service's: pause keeps the queue for "resume").
PLAYBACK_RE = re.compile(
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
PLAYBACK_ACTIONS = {
    "pause": "pause", "resume": "resume", "next": "next", "next_track": "next",
    "turn_up": "volume_up", "louder": "volume_up", "volume_up": "volume_up",
    "turn_down": "volume_down", "quieter": "volume_down", "volume_down": "volume_down",
}

# Thumbs on the playing track pin to the home agent's rate_music tool
# (2026-10-06, MA_LOCAL_PANDORA_FEATURES_PLAN). Anchored; the whole utterance
# is the verdict. "I like this" alone is left to the coordinator — too
# ambiguous without "song"/"one"/"track".
# Sprint M7: the named group that matched fixes rate_music's thumb
# (RATE_THUMBS), so the rule calls the tool directly.
RATE_RE = re.compile(
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
RATE_THUMBS = {
    "thumbs_up": "up", "like": "up", "upvote": "up", "more": "up",
    "thumbs_down": "down", "dislike": "down", "never_again": "down", "downvote": "down",
    "less": "down",
}

# "Not that" after Kronk picked a genre plays the next pick (MUSIC_TASTE_
# AGENT_PLAN step 5). Anchored: the whole utterance is the rejection.
ANOTHER_RE = re.compile(
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


def named_value(m: "re.Match", table: dict[str, str]) -> str | None:
    """The value for the one named group of `table` that took part in m,
    or None when not exactly one did."""
    hit = [table[k] for k, v in m.groupdict().items() if v is not None and k in table]
    return hit[0] if len(hit) == 1 else None


def command_of(text: str) -> tuple[str, str | None] | None:
    """(rule name, value) when the whole utterance is one of these forms:
    ("playback", "pause"|"resume"|"next"|"volume_up"|"volume_down"),
    ("rate", "up"|"down"), ("music_another", None). None otherwise. The
    rule names are routing.py's, so a voice run Kronk answered (its
    routing.decide span) and one HA answered (its transcript) compare."""
    if not text:
        return None
    if m := PLAYBACK_RE.search(text):
        return "playback", named_value(m, PLAYBACK_ACTIONS)
    if m := RATE_RE.search(text):
        return "rate", named_value(m, RATE_THUMBS)
    if ANOTHER_RE.search(text):
        return "music_another", None
    return None
