"""Voice regression corpus, layer one (offline). Sprint item M10.

Every row of tests/voice_corpus.jsonl runs through routing._classify_inner and
must land on its expected (route, rule). Rows whose routing is a known failure
today carry `xfail.routing` with the reason; they run as strict xfails, so
pytest counts them as "xfailed" and turns them into a failure (XPASS strict)
the day they start passing. Then delete the marker in the same commit.

The format and the corpus rule are in docs/features/voice-regression-corpus.md.
Layer two (live, through Home Assistant) is scripts/voice_replay.py.

M11 parser hook: see "M11 parser assertions" at the bottom. The rows already
carry kind / media_type / name / artist / provider / player / shuffle.
"""
import asyncio
import json
import re
from pathlib import Path

import pytest

import routing

CORPUS = Path(__file__).resolve().parent / "voice_corpus.jsonl"
REPO = CORPUS.parent.parent

ROWS = [json.loads(line) for line in CORPUS.read_text().splitlines() if line.strip()]

# The 15 outcomes of routing._classify_inner, in order (plan C4 / M7).
RULES = {
    "talkie_explicit": "talkie", "direct_override": "direct", "music_url": "home",
    "url": "research", "search_phrase": "research", "weather": "home",
    "playback": "home", "now_playing": "home", "rate": "home",
    "music_another": "home", "taste": "home", "music": "home",
    "mm_update": "home", "mm_ops": "devops", "default": "direct",
}
TIERS = {"ha_local", "kronk"}
HA_SOURCES = {"trigger", "builtin"}
TRIGGERS = {"album", "track", "artist", "audiobook", "radio", "playlist", "loose", "catchall"}
TOOLS = {None, "play_music", "control_music", "now_playing", "rate_music", "news_brief"}
KINDS = {"named", "genre", "another"}
MEDIA_TYPES = {None, "artist", "album", "track", "playlist", "radio"}
PROVIDERS = {"pandora", "youtube_music", "nas"}
AREAS = {None, "Kitchen", "Office", "Basement Theater"}
DEVICES = {"Kitchen": "kitchen_pe", "Office": "office_sat1", "Basement Theater": "basement_pe"}
XFAIL_LAYERS = {"routing", "live", "parser"}
PLAYER_RE = re.compile(r"^(origin|area:.+|entity:media_player\.[a-z0-9_]+|unknown:.+)$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _ids(rows):
    return [r["id"] for r in rows]


# ── Schema: the format the doc promises ──────────────────────────────────────

def test_corpus_ids_unique_and_well_formed():
    ids = _ids(ROWS)
    assert len(ids) == len(set(ids)), [i for i in ids if ids.count(i) > 1]
    bad = [i for i in ids if not ID_RE.match(i)]
    assert not bad, bad


@pytest.mark.parametrize("row", ROWS, ids=_ids(ROWS))
def test_corpus_row_schema(row):
    rid = row["id"]
    assert isinstance(row["text"], str) and row["text"].strip(), rid
    assert set(row) <= {"id", "text", "origin", "expect", "source", "tags", "xfail", "note", "ref"}, rid

    o = row["origin"]
    assert o["area"] in AREAS, f"{rid}: unknown origin area {o['area']!r}"
    if o["area"] is not None:
        assert o.get("device") == DEVICES[o["area"]], rid

    e = row["expect"]
    assert e["tier"] in TIERS, rid
    if e["tier"] == "ha_local":
        assert e["ha"]["source"] in HA_SOURCES, rid
        if e["ha"]["source"] == "trigger":
            assert e["ha"].get("trigger") in TRIGGERS | {None}, rid
        else:
            assert e["ha"]["intent"].startswith("Hass"), rid
    else:
        assert "ha" not in e, rid
    assert e["rule"] in RULES, f"{rid}: unknown rule {e['rule']!r}"
    assert RULES[e["rule"]] == e["route"], f"{rid}: rule {e['rule']} never routes to {e['route']}"
    assert e["tool"] in TOOLS, rid
    if e["tool"] == "play_music":
        assert e["kind"] in KINDS, rid
        assert e["media_type"] in MEDIA_TYPES, rid
        assert e.get("provider") in PROVIDERS | {None}, rid
    if "player" in e:
        assert PLAYER_RE.match(e["player"]), f"{rid}: bad player {e['player']!r}"

    assert row["source"], rid
    for src in row["source"]:
        path = REPO / src.split("#")[0]
        assert path.exists(), f"{rid}: source {src!r} does not exist"
    assert row["tags"] and all(isinstance(t, str) for t in row["tags"]), rid

    for layer, reason in row.get("xfail", {}).items():
        assert layer in XFAIL_LAYERS, f"{rid}: unknown xfail layer {layer!r}"
        assert isinstance(reason, str) and len(reason) > 20, f"{rid}: xfail.{layer} needs a reason"


def test_corpus_holds_the_plan_named_utterances():
    """Plan M10 step 2, verbatim (criterion A2)."""
    texts = {r["text"] for r in ROWS}
    named = [
        "Play Anime Pop Radio. That's probably about it. Everything is pretty wet.",
        "Like massive attack radio",
        "play The Girl with the Sun in Her Head by Orbital",
        "shuffle my YouTube playlist anime bangers",
        "put on some jazz",
        "Stop!",
        "Pasta",
        "What's up?",
    ]
    missing = [t for t in named if t not in texts]
    assert not missing, missing
    # Recorded with the transcript's full stop; the plan's text is a prefix.
    assert any(t.startswith("Start an orbital radio station on YouTube Music") for t in texts)
    assert any("ninety's two thousands" in t for t in texts)


# ── Layer one: routing ───────────────────────────────────────────────────────

def _routing_params():
    for r in ROWS:
        reason = r.get("xfail", {}).get("routing")
        marks = [pytest.mark.xfail(strict=True, reason=f"[{r['id']}] {reason}")] if reason else []
        yield pytest.param(r, id=r["id"], marks=marks)


@pytest.mark.parametrize("row", list(_routing_params()))
def test_corpus_routing(row):
    expected = (row["expect"]["route"], row["expect"]["rule"])
    actual = asyncio.run(routing._classify_inner(row["text"]))
    assert actual == expected, (
        f"corpus row {row['id']}: {row['text']!r} routed {actual[0]}/{actual[1]}, "
        f"expected {expected[0]}/{expected[1]}"
    )


# ── M11 parser assertions (the hook) ─────────────────────────────────────────
#
# M11 writes the sentence parser in tool_service/music_resolve.py. When it
# exists, point PARSE at it (the import below picks up `parse_request` by
# default; change the name here if M11 picks another). It is called as
# PARSE(text, origin_area) and must return a dict carrying any of the keys in
# PARSER_FIELDS. For each row whose expected tool is play_music, every field
# the row states is compared; rows with `xfail.parser` run as strict xfails.
# Until PARSE exists these tests are not collected, so they add no skips.
PARSER_FIELDS = ("kind", "media_type", "name", "artist", "provider", "player", "shuffle")

try:  # pragma: no cover - activates with M11
    from tool_service.music_resolve import parse_request as PARSE
except ImportError:
    PARSE = None


def _parser_params():
    for r in ROWS:
        if r["expect"]["tool"] != "play_music":
            continue
        reason = r.get("xfail", {}).get("parser")
        marks = [pytest.mark.xfail(strict=True, reason=f"[{r['id']}] {reason}")] if reason else []
        yield pytest.param(r, id=r["id"], marks=marks)


if PARSE is not None:  # pragma: no cover - activates with M11
    @pytest.mark.parametrize("row", list(_parser_params()))
    def test_corpus_parser(row):
        got = PARSE(row["text"], row["origin"]["area"])
        want = {k: row["expect"][k] for k in PARSER_FIELDS if k in row["expect"]}
        diff = {k: (want[k], got.get(k)) for k in want if got.get(k) != want[k]}
        assert not diff, f"corpus row {row['id']}: {row['text']!r} parsed wrong (want, got): {diff}"


def test_parser_rows_carry_the_m11_fields():
    """Every play_music row states at least the kind and the name (or says it
    is unknown), so M11 has something to assert from day one."""
    rows = [r for r in ROWS if r["expect"]["tool"] == "play_music"]
    assert rows
    for r in rows:
        assert "kind" in r["expect"] and "name" in r["expect"] and "player" in r["expect"], r["id"]
