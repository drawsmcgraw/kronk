"""Sentence-level streaming on the speech mount (sprint M8).

/voice/api/chat used to collect the whole reply, run render.to_speech once
and send one chunk, so Home Assistant could not start speaking until the
model had finished. With VOICE_STREAM=on the reply leaves in units that
render.to_speech renders on their own exactly as it renders them inside the
whole reply, so the text spoken is the buffered path's text, just sooner.

split_units() is the one place the reply is cut. It is pure: give it the
raw (markdown) text received so far and it returns the complete units and
the incomplete rest. A unit is

- one sentence of a prose line (cut mid-line only where it is safe, below),
- one whole line of any other kind (heading, block quote, rule), or
- one whole block: a list, a table or a code fence, from its first line to
  the line that ends it. Nothing inside a block is a unit of its own, so a
  block is released only once it has ended (or when the stream ends).

Rules (each exists because render.to_speech would render the part and the
whole differently otherwise, or because a later character could change the
decision, and a unit once sent cannot be unsent):

1. A unit ends at a sentence end inside a prose line, at the end of a
   non-block line, at the end of a block, or at the end of the stream. A
   cut is only made once the next unit has started (its first non-space
   character has arrived). A mid-line cut falls on the next sentence's
   first character; a line or block cut on the start of the line that
   holds the next content, so blank lines stay with the unit before it and
   indentation with the unit it belongs to.
2. Sentence ends are transcript_gate.ends_sentence (abbreviations such as
   "Dr." and "e.g.", initials, "a.m.", "No. 5", ellipses, a full stop
   before a lowercase word, decimals and URLs, which have no space after
   their full stop), plus: the token's last character is ".", "?" or "!"
   (not a closing quote or bracket, which to_speech would follow with a
   full stop), and the next token starts, after opening quotes or
   brackets, with a letter or digit and is not a list marker ("2.").
3. A mid-line cut needs an even number of "*", "`" and "_" delimiter runs
   in the unit's part of the line ("**" is one run), so emphasis and inline
   code are never cut open.
4. Any cut needs as many "[" as "]" in the unit, so a link is never cut.
5. Mid-line cuts only in prose lines: not in headings (to_speech gives a
   heading line one closing colon), block quotes, rules, table rows, list
   items or fences.
6. A block ends at the first line of another kind; that line must have
   started far enough to know its kind (its first token is complete, or
   the line is), so a "-" that may still become "- item" closes nothing.

What to_speech still sees differently: nothing in the cases of
tests/test_speech_stream.py; the property test there renders thousands of
generated replies both ways. Whitespace between units is not compared:
each unit is rendered (and stripped) on its own.
"""
from __future__ import annotations

import re

from transcript_gate import OPENERS, ends_sentence

_LIST_ITEM = re.compile(r"[ \t]*(?:[-*+•]|\d{1,3}[.)])[ \t]+")
_HR = re.compile(r"[ \t]*([-*_][ \t]*){3,}")      # render._HR, one line
_LIST_MARKER = re.compile(r"\d{1,3}[.)]")
_TOKEN = re.compile(r"\S+")


def _kind(line: str) -> str:
    """fence | blank | table | list | indent | line | prose."""
    if line.startswith("```"):
        return "fence"
    s = line.lstrip(" \t")
    if not s:
        return "blank"
    if s.startswith("|"):
        return "table"
    if _LIST_ITEM.match(line):
        return "list"
    if line[0] in " \t":
        return "indent"
    if s.startswith(("#", ">")) or _HR.fullmatch(line):
        return "line"
    return "prose"


def _decided(line: str, complete: bool) -> bool:
    """Enough of `line` has arrived to know its kind for good."""
    if complete:
        return True
    s = line.lstrip(" \t")
    if not s:
        return False
    if s[0] in "|#>`" or line[0] in " \t":
        return True
    return bool(re.match(r"\S+[ \t]", s))      # first token complete


_NON_SPACE = re.compile(r"\S")


def _next_line(text: str, k: int) -> int | None:
    """Start of the line holding the first non-space character at or after
    k, or None while there is none. Line and block cuts fall here, so a
    unit keeps its first line's indentation: "  #x" is not a heading, "#x"
    is."""
    m = _NON_SPACE.search(text, k)
    return text.rfind("\n", 0, m.start()) + 1 if m else None


def _next_ok(tok: str, whole: bool) -> bool:
    """The token after a sentence end may start a unit. `whole`: the token
    is complete (whitespace or the line end follows it). A token that
    starts with a digit must be whole: "1" may still become "1."."""
    core = tok.lstrip(OPENERS)
    if not core or not core[0].isalnum():
        return False
    if core[0].isdigit() and not whole:
        return False
    return not _LIST_MARKER.fullmatch(tok)


_RUNS = {c: re.compile(re.escape(c) + "+") for c in "*`_"}


def _inline_closed(part: str) -> bool:
    """An even number of "*", "`" and "_" delimiter runs: "**a" is open."""
    return all(len(r.findall(part)) % 2 == 0 for r in _RUNS.values())


def split_units(text: str, final: bool = False) -> tuple[list[str], str]:
    """Cut `text` into complete speech units (module docstring).

    Returns (units, rest) with "".join(units) + rest == text. With
    final=True (the stream has ended) everything is in units and rest is
    "": the incomplete tail, an unclosed block included, is the last unit.
    """
    cuts: list[int] = []
    unit_start = 0

    def try_cut(at: int | None) -> bool:
        nonlocal unit_start
        if at is None or at <= unit_start:
            return False
        if text.count("[", unit_start, at) != text.count("]", unit_start, at):
            return False                                    # rule 4
        cuts.append(at)
        unit_start = at
        return True

    block: str | None = None          # "list" | "table" | "fence"
    i, n = 0, len(text)
    while i < n:
        j = text.find("\n", i)
        complete = j >= 0
        end = j if complete else n
        line = text[i:end]
        after = end + 1 if complete else n
        done = complete or final          # the line has all its characters
        kind = _kind(line)

        if block == "fence":
            if kind == "fence" and done:
                block = None
                try_cut(_next_line(text, after))
            i = after
            continue

        if block in ("list", "table"):
            same = kind == block or kind == "blank" or (block == "list" and kind == "indent")
            if same:
                i = after
                continue
            if not _decided(line, done):
                break                                        # rule 6: wait
            block = None
            try_cut(_next_line(text, i))
            # fall through: this line starts something new

        if kind == "fence":
            block = "fence"
            i = after
            continue
        if kind in ("list", "table"):
            block = kind
            i = after
            continue

        if kind == "prose":
            toks = list(_TOKEN.finditer(line))
            for a, b in zip(toks, toks[1:]):
                tok, nxt = a.group(), b.group()
                whole = done or b.end() < len(line)
                if tok[-1] not in ".!?" or not _next_ok(nxt, whole) or not ends_sentence(tok, nxt):
                    continue
                if not _inline_closed(text[max(unit_start, i): i + a.end()]):
                    continue                                 # rule 3
                try_cut(i + b.start())

        if complete:
            try_cut(_next_line(text, after))
        i = after

    bounds = [0, *cuts]
    units = [text[a:b] for a, b in zip(bounds, bounds[1:])]
    rest = text[bounds[-1]:]
    if final:
        if rest:
            units.append(rest)
        rest = ""
    return units, rest
