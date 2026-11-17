"""The speech stream's sentence splitter (sprint M8, criteria 3, 6 and 7).

speech_stream.split_units cuts a spoken reply into units that
render.to_speech renders on their own exactly as inside the whole reply.
Pure function; no network, no app.
"""
import random
import re

import pytest

import render
import transcript_gate
from speech_stream import split_units

pytestmark = pytest.mark.usefixtures("no_network")


def ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def spoken_parts(text: str) -> list[str]:
    units, rest = split_units(text, final=True)
    assert rest == ""
    return [p for p in (render.to_speech(u) for u in units) if p]


def cuts(text: str, final: bool = False) -> list[int]:
    units, _ = split_units(text, final)
    out, pos = [], 0
    for u in units[:-1] if final else units:
        pos += len(u)
        out.append(pos)
    return out


# ── Criterion 3: the recorded and stubbed replies ────────────────────────────

# name -> (reply as the model writes it, the units it is cut into)
CASES = {
    "plain prose": (
        "A heat pump moves heat instead of making it. In winter it pulls heat from the "
        "outside air. In summer it runs the other way.",
        ["A heat pump moves heat instead of making it. ",
         "In winter it pulls heat from the outside air. ",
         "In summer it runs the other way."]),
    "markdown emphasis": (
        "It is **very efficient. Really** efficient. Use *one* setting. Then wait.",
        ["It is **very efficient. Really** efficient. ", "Use *one* setting. ", "Then wait."]),
    "list": (
        "Here are the steps:\n\n1. Evaporation: heat boils the refrigerant. It becomes a gas.\n"
        "2. Compression: the gas gets hot.\n\n- a bullet. Two sentences.\n\nThat is the cycle.",
        ["Here are the steps:\n\n",
         "1. Evaporation: heat boils the refrigerant. It becomes a gas.\n"
         "2. Compression: the gas gets hot.\n\n- a bullet. Two sentences.\n\n",
         "That is the cycle."]),
    "table": (
        "Compare them.\n| Type | COP |\n|---|---|\n| Air | 3.5 |\n| Ground | 4.5 |\nGround wins.",
        ["Compare them.\n", "| Type | COP |\n|---|---|\n| Air | 3.5 |\n| Ground | 4.5 |\n",
         "Ground wins."]),
    "code fence": (
        "Run this.\n```bash\necho hi. echo there.\nls\n```\nDone. Bye.",
        ["Run this.\n", "```bash\necho hi. echo there.\nls\n```\n", "Done. ", "Bye."]),
    "heading": (
        "## How it works. Briefly\nIt moves heat. That is all.",
        ["## How it works. Briefly\n", "It moves heat. ", "That is all."]),
    "URL": (
        "See https://www.energy.gov/heat-pumps.html. It has more. Or [the guide](https://x.org/a. b). Fine.",
        ["See https://www.energy.gov/heat-pumps.html. ", "It has more. ",
         "Or [the guide](https://x.org/a. b). ", "Fine."]),
    "numbers and units": (
        "Set it to 68 °F. That is 20 °C. It uses 3.5 kW at 7:30 p.m. Most days.",
        ["Set it to 68 °F. ", "That is 20 °C. ", "It uses 3.5 kW at 7:30 p.m. Most days."]),
    "abbreviations": (
        "Dr. Smith said so, e.g. Mr. Jones agreed. Call No. 5 today. It works.",
        ["Dr. Smith said so, e.g. Mr. Jones agreed. ", "Call No. 5 today. ", "It works."]),
    "decimals": (
        "The ratio is 3.14 here. And 98.6 there. Done.",
        ["The ratio is 3.14 here. ", "And 98.6 there. ", "Done."]),
    "ellipsis": (
        "Well... It depends. Wait… Maybe not.",
        ["Well... It depends. ", "Wait… Maybe not."]),
    "no final punctuation": (
        "It moves heat. It does not make heat",
        ["It moves heat. ", "It does not make heat"]),
    "one sentence": ("It moves heat from outside to inside.", ["It moves heat from outside to inside."]),
    "empty": ("", []),
    "initials and quotes": (
        'J. R. R. Tolkien wrote it. He said "go." Then he left. "Really?" Yes.',
        ['J. R. R. Tolkien wrote it. ', 'He said "go." Then he left. ', '"Really?" Yes.']),
    "question and exclamation": (
        "Is it cold? Yes! Wear a coat.", ["Is it cold? ", "Yes! ", "Wear a coat."]),
    "lowercase after a full stop": (
        "Use mode one. then mode two. Done.", ["Use mode one. then mode two. ", "Done."]),
    "blockquote and rule": (
        "> Quoted. Still quoted.\n---\nAfter.", ["> Quoted. Still quoted.\n", "---\n", "After."]),
    "snake_case keeps a line whole": (
        "Set my_var. Then go. Next line.\nTwo.",
        ["Set my_var. Then go. Next line.\n", "Two."]),
}


@pytest.mark.parametrize("name", list(CASES))
def test_units(name):
    text, want = CASES[name]
    units, rest = split_units(text, final=True)
    assert units == want
    assert rest == ""


@pytest.mark.parametrize("name", list(CASES))
def test_parts_render_as_the_whole(name):
    text, _ = CASES[name]
    assert ws(" ".join(spoken_parts(text))) == ws(render.to_speech(text))


@pytest.mark.parametrize("name", list(CASES))
def test_no_cut_is_ever_withdrawn_as_text_arrives(name):
    text, _ = CASES[name]
    final = set(cuts(text, final=True))
    for k in range(len(text) + 1):
        assert set(cuts(text[:k])) <= final, (name, k, text[:k])


# ── Criterion 6: blocks are units ────────────────────────────────────────────

def test_a_list_is_held_until_the_next_line_of_another_kind():
    text = "Steps:\n1. First thing. Really.\n2. Second.\n"
    units, rest = split_units(text)
    assert units == ["Steps:\n"] and rest.startswith("1. First")
    units, rest = split_units(text + "\n3. Third.\n\n")        # blank lines stay inside
    assert units == ["Steps:\n"]
    units, rest = split_units(text + "\n-")                     # "-" may become "- item"
    assert units == ["Steps:\n"]
    units, rest = split_units(text + "\nAfter")                  # "After" may become "After:" etc.
    assert units == ["Steps:\n"]                                 # undecided: first token incomplete
    units, rest = split_units(text + "\nAfter it")
    assert units == ["Steps:\n", "1. First thing. Really.\n2. Second.\n\n"] and rest == "After it"


def test_an_indented_line_continues_a_list():
    text = "- one\n  more about one. Yes.\n- two\nEnd."
    assert split_units(text, final=True)[0] == ["- one\n  more about one. Yes.\n- two\n", "End."]


def test_a_table_is_held_until_it_ends():
    text = "| a | b |\n|---|---|\n| 1 | 2 |\n"
    assert split_units(text)[0] == []
    assert split_units(text + "| 3 | 4 |\nNext")[0] == []          # "Next" is not decided yet
    assert split_units(text + "| 3 | 4 |\nNext one")[0] == ["| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n"]


def test_a_fence_is_held_until_it_closes():
    text = "```\nx = 1. y = 2.\n\nz\n"
    assert split_units(text)[0] == []
    assert split_units(text + "```\nok")[0] == ["```\nx = 1. y = 2.\n\nz\n```\n"]


def test_an_unclosed_block_is_released_at_the_end_rendered_as_buffered():
    for text in ("Intro.\n```\ncode. more.\n", "Intro.\n- a. b.\n- c", "Intro.\n| a |\n| b"):
        units, rest = split_units(text, final=True)
        assert len(units) == 2 and units[0] == "Intro.\n" and rest == ""
        assert ws(" ".join(spoken_parts(text))) == ws(render.to_speech(text))


# ── Criterion 7: boundaries ──────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Dr. Smith is here.", "Use it e.g. Daily or weekly.", "Mr. And Mrs. Smith came.",
    "It is 98.6 degrees.", "Visit example.com today.", "Meet at 7:30 a.m. Tomorrow.",
    "J. S. Bach wrote it.", "Track No. 5 is good.", "Hmm... Okay then.", "Wait… What?",
    "It was the U.S. Army.", 'He said "stop." And left.', "Plan B. Thanks.",
])
def test_no_cut_inside_abbreviations_decimals_initials_urls_times(text):
    assert split_units(text, final=True)[0] == [text]


def test_the_boundary_rule_is_transcript_gates():
    """One sentence rule for the repo: split_units cuts only where
    transcript_gate.ends_sentence says a sentence ends."""
    assert split_units is not None and transcript_gate.ends_sentence("Dr.", "Smith") is False
    rng = random.Random(7)
    for _ in range(300):
        text = _gen(rng)
        for pos in cuts(text, final=True):
            before = text[:pos].rstrip()
            if before.endswith("\n") or "\n" in text[len(before):pos]:
                continue                      # a line or block end, not a sentence end
            tok = before.split()[-1]
            nxt = text[pos:].split()[0]
            assert transcript_gate.ends_sentence(tok, nxt), (text, pos)


# ── Properties over generated replies ────────────────────────────────────────

_WORDS = ["heat", "pump", "air", "the", "it", "Coils", "Refrigerant", "moves", "water", "Home",
          "e.g.", "Dr.", "No.", "5", "3.5", "a.m.", "U.S.", "kW", "my_var", "https://x.org/a.b",
          "**bold**", "*it*", "`code`", "[link](https://y.org)", "...", "…", '"quote"', "(aside)",
          "|", "#tag", "_em_", "7:30", "Mr.", "St."]
_ENDS = [".", ".", ".", "?", "!", "", ":", '."', ".)", "..."]


def _sentence(rng):
    words = [rng.choice(_WORDS) for _ in range(rng.randint(1, 7))]
    words[0] = words[0][:1].upper() + words[0][1:] if rng.random() < 0.8 else words[0]
    return " ".join(words) + rng.choice(_ENDS)


def _gen(rng) -> str:
    out = []
    for _ in range(rng.randint(1, 8)):
        r = rng.random()
        if r < 0.45:
            out.append(" ".join(_sentence(rng) for _ in range(rng.randint(1, 4))))
        elif r < 0.55:
            out.append(rng.choice(["#", "##", "###"]) + " " + _sentence(rng))
        elif r < 0.67:
            marker = rng.choice(["-", "*", "1.", "2)", "•"])
            out.append("\n".join(f"{marker} {_sentence(rng)}" + (f"\n  {_sentence(rng)}" if rng.random() < 0.3 else "")
                                 for _ in range(rng.randint(1, 3))))
        elif r < 0.75:
            rows = ["| " + " | ".join(rng.choice(_WORDS) for _ in range(2)) + " |" for _ in range(rng.randint(1, 3))]
            out.append("\n".join([rows[0], "|---|---|", *rows[1:]]))
        elif r < 0.82:
            body = "\n".join(_sentence(rng) for _ in range(rng.randint(1, 3)))
            out.append("```" + rng.choice(["", "python"]) + "\n" + body + ("\n```" if rng.random() < 0.8 else ""))
        elif r < 0.88:
            out.append("> " + _sentence(rng) + " " + _sentence(rng))
        elif r < 0.92:
            out.append(rng.choice(["---", "***", "- - -", "___"]))
        else:
            out.append(_sentence(rng) + "\n" + _sentence(rng))
        out.append(rng.choice(["\n", "\n\n", "\n\n\n", " "]) if rng.random() < 0.9 else "")
    text = "".join(out)
    return text.rstrip() if rng.random() < 0.7 else text


GENERATED = [_gen(random.Random(seed)) for seed in range(1500)]


def test_generated_replies_render_the_same_cut_or_whole():
    bad = []
    for text in GENERATED:
        if ws(" ".join(spoken_parts(text))) != ws(render.to_speech(text)):
            bad.append(text)
    assert not bad, f"{len(bad)} of {len(GENERATED)} differ; first: {bad[0]!r}"


def test_generated_replies_join_back_and_never_withdraw_a_cut():
    for text in GENERATED[:400]:
        units, rest = split_units(text, final=True)
        assert "".join(units) == text and rest == ""
        final = set(cuts(text, final=True))
        for k in range(0, len(text) + 1, 3):
            u, r = split_units(text[:k])
            assert "".join(u) + r == text[:k]
            assert set(cuts(text[:k])) <= final, (text, k)


def test_generated_replies_are_cut_somewhere():
    """Not a degenerate splitter: most multi-sentence replies get cut."""
    multi = [t for t in GENERATED if render.to_speech(t).count(". ") >= 2]
    cut = [t for t in multi if len(split_units(t, final=True)[0]) > 1]
    assert len(cut) > 0.8 * len(multi)


# ── Recorded replies (criterion 3) ───────────────────────────────────────────

def _recorded():
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parent / "fixtures" / "m8" / "replies.json"
    return [r["reply"] for r in json.loads(path.read_text())["replies"]]


def test_recorded_dev_replies_render_the_same_cut_or_whole():
    """100 answers gemma-4-e4b gave on the dev path (before and after the
    spoken-reply line), as the buffered path spoke them."""
    replies = _recorded()
    assert len(replies) == 100
    for text in replies:
        assert ws(" ".join(spoken_parts(text))) == ws(render.to_speech(text)), text
    assert sum(len(split_units(t, final=True)[0]) > 1 for t in replies) >= 95


# ── The one known difference (listed in docs/sprint/M8-build.md) ─────────────

def test_known_difference_space_before_an_added_full_stop():
    """render.to_speech gives a line that contains "|" its cell treatment,
    whose .strip(", ") also drops the line's trailing spaces before the
    closing full stop is added. A sentence cut off such a line no longer
    holds the "|", so it keeps its trailing space: "Moves ." against
    "Moves.". Whitespace only, and only on a non-table line with a "|" and
    trailing spaces; no word changes."""
    text = "Ratio 3 | 4 here. Moves "
    parts = " ".join(spoken_parts(text))
    whole = render.to_speech(text)
    assert (parts, whole) == ("Ratio 3, 4 here. Moves .", "Ratio 3, 4 here. Moves.")
    assert re.sub(r"\s+\.", ".", parts) == whole
