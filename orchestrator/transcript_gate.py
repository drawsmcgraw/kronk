"""The messy-transcript gate (sprint M14 step 1; Drew, 2026-11-11).

Messy input goes to the model; the rules handle clean input only. A
transcript with more sentences than `max_sentences`, or more words than
`max_words` (config/tuning.yaml, `transcript_gate`), is messy: routing.py's
`messy` rule sends it to the coordinator with the whole transcript, ahead of
every rule-based fast path. Nothing here trims or rewrites the text; it only
counts. docs/features/messy-transcript-gate.md.

How sentences are counted (the rules, all of them):

1. The text is split on whitespace. A token with no letter or digit
   ("-", "...", "?") is not a word.
2. Every transcript with a word in it has one sentence. Each token that
   ends a sentence and has another word somewhere after it adds one. So a
   single trailing ".", "?" or "!" never counts, nor does punctuation with
   no word after it ("Play jazz. ?").
3. A token ends a sentence when, after any closing quotes or brackets, its
   last character is ".", "?" or "!", except:
   a. an ellipsis: the token ends in ".." or "…" ("Play... jazz");
   b. initials and dotted abbreviations: the token is one or more
      single letters each followed by "." ("J.", "R.E.M.", "a.m.", "D.C.");
   c. a word from ABBREVIATIONS followed by "." ("Mr.", "St.", "Dr.");
   d. "No." when the next token starts with a digit ("No. 5");
   e. the next token starts with a lowercase letter. Whisper starts every
      sentence with a capital, and the music blueprint appends its room
      after the spoken full stop: "Play Hotel Costas Radio. in the
      Kitchen" is one request (13 such requests reached production
      between 2026-09-14 and 2026-11-17). Also "Panic! at the Disco".
   A "." with no space after it is inside a token and never counts
   ("98.6", "7.30", "music.youtube.com").

What it cannot tell apart (by design; the gate is a counter, not a parser):

- A name with "?" or "!" in it followed by a capital: "Godspeed You!
  Black Emperor" is two sentences ("Panic! at the Disco" is one, rule 3e).
  A name that ends in "." before more words: "Play U2. Thanks" is two
  sentences (right); "Plan B. Thanks" is one (one letter before ".": read
  as an initial).
- A sentence that ends on an initial or listed abbreviation: "Set an
  alarm for 7 a.m. Thanks." is one sentence.
- A second sentence that starts lowercase ("pause. play jazz", typed):
  one sentence (rule 3e). Typed text is not Whisper's.
- Run-on speech with no punctuation is one sentence: only the word count
  sees it.
- Whisper's punctuation itself: a full stop it leaves out is not counted,
  and one it adds inside a name ("Play Anime. Pop Radio") is. Case is not
  read at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Abbreviations that are written with a full stop inside a request and are
# not the end of a sentence. Compared without case and without the ".".
# Only words seen in music, station or place names; each costs a missed
# sentence break when a sentence really does end on it.
ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "mt", "ft", "feat", "vs", "vol", "op",
})
# Abbreviations that count as one only before a number ("No. 5", "Op. 27"
# is already in ABBREVIATIONS): "No. Not that one." is two sentences.
_NUMBER_ABBREVIATIONS = frozenset({"no"})

_WORD = re.compile(r"[^\W_]")                  # a letter or a digit
_CLOSERS = "\"')]}”’»"                         # closing quotes and brackets
_OPENERS = "\"'([{“‘«"                         # opening quotes and brackets
_INITIALS = re.compile(r"(?:[^\W\d_]\.)+")     # J.  R.E.M.  a.m.


def _ends_sentence(token: str, next_token: str) -> bool:
    core = token.rstrip(_CLOSERS)
    if not core or core[-1] not in ".?!…":
        return False
    if next_token.lstrip(_OPENERS)[:1].islower():
        return False                                   # rule 3e
    if core.endswith("..") or core.endswith("…"):
        return False                                   # rule 3a, an ellipsis
    if core[-1] != ".":
        return True                                    # "?" or "!"
    if core[-2:-1] in ("?", "!"):
        return True                                    # "?." / "!."
    bare = core.lstrip(_OPENERS)
    if _INITIALS.fullmatch(bare):
        return False                                   # rule 3b
    word = bare[:-1].casefold()
    if word in ABBREVIATIONS:
        return False                                   # rule 3c
    if word in _NUMBER_ABBREVIATIONS and next_token[:1].isdigit():
        return False                                   # rule 3d
    return True


def count_words(text: str | None) -> int:
    """Whitespace-separated tokens that hold a letter or a digit."""
    return sum(1 for t in (text or "").split() if _WORD.search(t))


def count_sentences(text: str | None) -> int:
    """Sentences in `text` by the rules in the module docstring; 0 when
    `text` holds no word."""
    tokens = (text or "").split()
    has_word = [bool(_WORD.search(t)) for t in tokens]
    if not any(has_word):
        return 0
    # word_after[i]: a word appears somewhere after token i
    word_after = [False] * len(tokens)
    seen = False
    for i in range(len(tokens) - 1, -1, -1):
        word_after[i] = seen
        seen = seen or has_word[i]
    return 1 + sum(1 for i in range(len(tokens) - 1)
                   if word_after[i] and _ends_sentence(tokens[i], tokens[i + 1]))


@dataclass(frozen=True)
class Messy:
    """Why the gate called a transcript messy: one or both halves, with
    the counts and the limits in effect."""
    sentences: int
    words: int
    max_sentences: int | None
    max_words: int | None

    @property
    def reasons(self) -> list[str]:
        out = []
        if self.max_sentences is not None and self.sentences > self.max_sentences:
            out.append("sentences")
        if self.max_words is not None and self.words > self.max_words:
            out.append("words")
        return out

    @property
    def reason(self) -> str:
        """'sentences', 'words' or 'sentences+words'."""
        return "+".join(self.reasons)

    def as_dict(self) -> dict:
        return {"reason": self.reason, "sentences": self.sentences, "words": self.words,
                "max_sentences": self.max_sentences, "max_words": self.max_words}


def check(text: str | None, max_sentences: int | None, max_words: int | None) -> Messy | None:
    """The gate's decision for `text`: a Messy when it has more sentences
    than max_sentences or more words than max_words, else None. A limit of
    None switches that half off. This is the only place "messy" is
    decided; routing.messy() calls it with the limits in effect."""
    if max_sentences is None and max_words is None:
        return None
    m = Messy(count_sentences(text), count_words(text), max_sentences, max_words)
    return m if m.reasons else None
