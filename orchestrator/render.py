"""Render profiles: canonical markdown inside, per-surface rendering at the
transport boundary (docs/plans/RENDER_PROFILES_PLAN.md).

Everything inside the pipeline — agents, tools, cached briefs, history —
produces one canonical form, markdown allowed. Surfaces that display text
pass it through; surfaces that SPEAK it run to_speech(), a deterministic
strip. Speech is an explicit client declaration (the /voice mount), never
an inference from protocol — an Ollama-dialect client may be a screen.
"""
import re

_FENCE      = re.compile(r"^```[^\n]*$", re.M)
_IMAGE      = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK       = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HR         = re.compile(r"^\s*([-*_]\s*){3,}$", re.M)
_HEADER     = re.compile(r"^#{1,6}\s*(.+?)\s*$", re.M)
_BOLDITALIC = re.compile(r"\*{1,3}([^*\n]+)\*{1,3}")
# Underscore emphasis only at word edges — snake_case identifiers survive.
_UNDERSCORE = re.compile(r"(?<![\w])_{1,3}([^_\n]+)_{1,3}(?![\w])")
_INLINECODE = re.compile(r"`([^`\n]*)`")
_BLOCKQUOTE = re.compile(r"^\s*>\s?", re.M)
_TABLE_SEP  = re.compile(r"^[ \t]*\|?[ \t:|]*-[- \t:|]*\|?[ \t]*$\n?", re.M)
_TABLE_CELL = re.compile(r"\s*\|\s*")
_BULLET     = re.compile(r"^\s*[-*+•]\s+", re.M)


def _header_to_spoken(m: re.Match) -> str:
    # A spoken header needs a longer beat than a sentence gap. Measured
    # against live piper 2026-08-25: "Tech & AI." pauses ~200 ms (same as
    # any sentence boundary), a blank line adds nothing (~120 ms), but
    # "Tech & AI:" pauses ~420 ms. Colon wins; punctuation isn't spoken.
    title = m.group(1).strip()
    return title if title[-1:] in ".!?:" else title + ":"


def to_speech(text: str) -> str:
    """Deterministic markdown → plain speech text. Idempotent; a plain-text
    input passes through unchanged (modulo whitespace collapsing)."""
    out = text
    out = _FENCE.sub("", out)
    out = _IMAGE.sub(r"\1", out)
    out = _LINK.sub(r"\1", out)
    out = _HR.sub("", out)
    out = _HEADER.sub(_header_to_spoken, out)
    out = _BOLDITALIC.sub(r"\1", out)
    out = _UNDERSCORE.sub(r"\1", out)
    out = _INLINECODE.sub(r"\1", out)
    out = _BLOCKQUOTE.sub("", out)
    out = _TABLE_SEP.sub("", out)
    out = "\n".join(
        _TABLE_CELL.sub(", ", line).strip(", ") if "|" in line else line
        for line in out.split("\n")
    )
    out = _BULLET.sub("", out)
    # Every spoken line must end at a boundary the synthesizer respects —
    # bold lead lines lose their ** and would otherwise run straight into
    # the next sentence with no pause at all.
    out = "\n".join(
        line + "." if line.strip() and line.rstrip()[-1:] not in ".!?:,;" else line
        for line in out.split("\n")
    )
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()
