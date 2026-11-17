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

# The 17 outcomes of routing._classify_inner, in order (plan C4 / M7; the
# lead-in first since M19; the messy gate after url since M14).
RULES = {
    "lead_in": "direct", "talkie_explicit": "talkie", "direct_override": "direct", "music_url": "home",
    "url": "research", "messy": "direct", "search_phrase": "research", "weather": "home",
    "playback": "home", "now_playing": "home", "rate": "home",
    "music_another": "home", "taste": "home", "music": "home",
    "mm_update": "home", "mm_ops": "devops", "default": "direct",
}
TIERS = {"ha_local", "kronk"}
HA_SOURCES = {"trigger", "builtin"}
TRIGGERS = {"album", "track", "artist", "audiobook", "radio", "playlist", "loose", "catchall"}
TOOLS = {None, "play_music", "control_music", "now_playing", "rate_music", "news_brief",
         "update_magicmirror"}
KINDS = {"named", "genre", "another"}
MEDIA_TYPES = {None, "artist", "album", "track", "playlist", "radio"}
PROVIDERS = {"pandora", "youtube_music", "nas"}
AREAS = {None, "Kitchen", "Office", "Basement Theater"}
DEVICES = {"Kitchen": "kitchen_pe", "Office": "office_sat1", "Basement Theater": "basement_pe"}
XFAIL_LAYERS = {"routing", "live", "parser"}
# The rules whose match fixes the whole tool call (sprint M7): their rows
# carry expect.direct = true, and Kronk runs the tool with no model call.
DIRECT_RULES = {"playback", "now_playing", "rate", "music_another", "mm_update"}
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
    # expect.direct pins the path: present (and true) exactly on the M7 rules.
    assert e.get("direct", False) is (e["rule"] in DIRECT_RULES), (
        f"{rid}: expect.direct must be true exactly for rules {sorted(DIRECT_RULES)}")

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


def test_mirror_rows_carry_the_mirror_tag():
    """scripts/voice_replay.py skips rows tagged `mirror` against production
    (MUSIC_DRY_RUN does not cover the mirror; a pinned row would start a real
    update). The skip is by tag, so every row that mentions the magic mirror
    or expects update_magicmirror must carry it (M4b e2e observation 10)."""
    untagged = [r["id"] for r in ROWS
                if (routing._MM_RE.search(r["text"])
                    or r["expect"].get("tool") == "update_magicmirror")
                and "mirror" not in r["tags"]]
    assert not untagged, f"rows missing the 'mirror' tag: {untagged}"


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


# ── Layer one: the direct call (sprint M7) ───────────────────────────────────

def _direct_params():
    # A routing xfail makes the direct check fail only on rows that expect
    # the direct path; elsewhere "no direct call" already holds.
    for r in ROWS:
        reason = r.get("xfail", {}).get("routing") if r["expect"].get("direct") else None
        marks = [pytest.mark.xfail(strict=True, reason=f"[{r['id']}] {reason}")] if reason else []
        yield pytest.param(r, id=r["id"], marks=marks)


@pytest.mark.parametrize("row", list(_direct_params()))
def test_corpus_direct_call(row):
    """A row that expects the direct path gets routing.decide's direct call
    for its tool, with its action / thumb; any other row gets none. Rows
    marked xfail.routing are strict xfails here too."""
    e = row["expect"]
    d = routing.decide(row["text"])
    if not e.get("direct"):
        assert d.direct is None, f"corpus row {row['id']}: unexpected direct call {d.direct}"
        return
    assert d.direct is not None, f"corpus row {row['id']}: {row['text']!r} has no direct call ({d.rule})"
    tool, args = d.direct
    assert tool == e["tool"], f"corpus row {row['id']}: direct tool {tool}, expected {e['tool']}"
    if "action" in e:
        assert args.get("action") == e["action"], f"corpus row {row['id']}: {args}"
    if "thumb" in e:
        assert args.get("thumb") == e["thumb"], f"corpus row {row['id']}: {args}"
    if e.get("kind") == "another":
        assert args.get("kind") == "another", f"corpus row {row['id']}: {args}"


# ── M11 parser assertions (the hook) ─────────────────────────────────────────
#
# Sprint M11 filled the hook M10 left here. Each play_music row runs through
# the M11 path offline: music_resolve.parse_sentence against the real player
# names, then music_decide.decide with Music Assistant replayed from the
# recorded snapshot (tests/music_snapshot.py; MUSIC_DRY_RUN on, HA service
# calls refused). The model question gets no answer here, so this is the
# rules' reading: what the M11 path does with the model down, and what its
# shadow does. PARSE(row) returns the fields below; the call shape changed
# from M10's PARSE(text, origin_area) because the target is checked against
# the origin device, not the area.
#
# How each field is compared (docs/features/voice-regression-corpus.md):
#   kind, media_type, shuffle   equal
#   name, artist                equal after music_resolve.fold (case, accents,
#                               punctuation, a leading "the"); the artist may
#                               be one of several credited ("X and Y")
#   provider                    null: the sentence names none; otherwise the
#                               provider the sentence named or the one chosen
#   player                      the speakers it resolves to, compared with
#                               the ones the row's player names (so "origin"
#                               and "area:Kitchen" agree for the kitchen PE,
#                               the only player in Kitchen); unknown:<words>
#                               expects the target error naming the words
# Rows with `xfail.parser` run as strict xfails.
PARSER_FIELDS = ("kind", "media_type", "name", "artist", "provider", "player", "shuffle")
_PROVIDER = {"ytmusic": "youtube_music", "filesystem_local": "nas", "pandora": "pandora"}

try:  # the M11 path; absent before M11
    from tests import music_snapshot as _snap
    from tool_service import music_resolve as _mr
    import tool_service.main as _ts
    _SNAP = _snap.load()
except (ImportError, FileNotFoundError):  # pragma: no cover
    _snap = None


def PARSE(row, tmp_path):
    """The M11 path's reading of a corpus row, offline. A lead-in row is read
    as the orchestrator hands it on: origin.said is the request without "ask
    Kronk to" (sprint M19), so that is the sentence the M11 path gets."""
    said = routing.lead_in_request(row["text"]) or row["text"]
    with _snap.offline_env(_SNAP, tmp_path):
        d = asyncio.run(_snap.decide_row({**row, "text": said}, _SNAP, model_answer=None))
    o, p = d.outcome, d.parsed
    return {"kind": o.kind, "media_type": o.media_type, "name": o.name, "artist": o.artist,
            "provider": {"named": p.provider, "chosen": o.provider}, "shuffle": p.shuffle,
            "player": {"entities": d.entities, "error": d.error}, "action": o.action,
            "decided_by": o.decided_by}


def _expected_entities(row):
    players = [_ts._norm_player(x) for x in _SNAP["players"]]
    want = row["expect"]["player"]
    if want.startswith("entity:"):
        return sorted([want.split(":", 1)[1]])
    if want.startswith("area:"):
        return sorted(t["entity_id"] for t in _ts._by_area(players, want.split(":", 1)[1]))
    area, device = _snap.origin_of(row)
    targets, _ = _ts.resolve_players(None, area, players, _snap.DEFAULT_PLAYER,
                                     origin_key=_snap.ORIGIN_KEYS.get(device or ""))
    return sorted(t["entity_id"] for t in targets)


def parser_diff(row, got):
    want = {k: row["expect"][k] for k in PARSER_FIELDS if k in row["expect"]}
    diff = {}
    for k, w in want.items():
        g = got[k]
        if k == "name":
            ok = (w is None and g is None) or (w is not None and g is not None and _mr.fold(w) == _mr.fold(g))
        elif k == "artist":   # one of the credited artists ("Ulrich Schnauss and Mark Peters")
            ok = (w is None and g is None) or (w is not None and g is not None and (
                _mr.fold(w) == _mr.fold(g) or _mr.fold(w) in [_mr.fold(a) for a in g.split(" and ")]))
        elif k == "provider":
            named, chosen = _PROVIDER.get(g["named"]), _PROVIDER.get(g["chosen"])
            ok = named is None if w is None else w in (named, chosen)
            g = {"named": named, "chosen": chosen}
        elif k == "player":
            if w.startswith("unknown:"):
                ok = bool(g["error"]) and w.split(":", 1)[1].lower() in g["error"].lower()
            else:
                ok = g["entities"] is not None and sorted(g["entities"]) == _expected_entities(row)
        else:
            ok = g == w
        if not ok:
            diff[k] = (w, g)
    return diff


def _parser_params():
    for r in ROWS:
        if r["expect"]["tool"] != "play_music":
            continue
        reason = r.get("xfail", {}).get("parser")
        marks = [pytest.mark.xfail(strict=True, reason=f"[{r['id']}] {reason}")] if reason else []
        yield pytest.param(r, id=r["id"], marks=marks)


if _snap is not None:
    @pytest.mark.usefixtures("no_network")
    @pytest.mark.parametrize("row", list(_parser_params()))
    def test_corpus_parser(row, tmp_path):
        got = PARSE(row, tmp_path)
        diff = parser_diff(row, got)
        assert not diff, (f"corpus row {row['id']}: {row['text']!r} read wrong (want, got): {diff}; "
                          f"decided_by {got['decided_by']}")

    @pytest.mark.parametrize("row", [r for r in ROWS if "non-music" in r["tags"]], ids=lambda r: r["id"])
    def test_corpus_parser_says_not_music(row):
        p = _mr.parse_sentence(row["text"], _SNAP["players"])
        assert p.music is False, f"corpus row {row['id']}: {row['text']!r} parsed as music"


def test_parser_rows_carry_the_m11_fields():
    """Every play_music row states at least the kind and the name (or says it
    is unknown), so M11 has something to assert from day one."""
    rows = [r for r in ROWS if r["expect"]["tool"] == "play_music"]
    assert rows
    for r in rows:
        assert "kind" in r["expect"] and "name" in r["expect"] and "player" in r["expect"], r["id"]
