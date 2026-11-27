"""/work motion (work item M32 step 3, docs/sprint/M32-criteria.md, "Step 3:
motion (criteria, coordinator, 2026-11-27)").

What the suite can check without a JavaScript engine: the track's literals
(its stations, the step kinds each counts, the stage-to-station map against
the exporter's own stage keys), the parts of the script the plumbing names
(the track drawn in place from the data, the flash hung on a part's
signature, the first item under way the only one that moves, the clock on
the route's time), and the stylesheet (only transform and opacity in any
keyframes, two endless animations, both stopped by .motion-still and by
reduced motion, no transition, nothing that carries state in --text3).
What the script does with them was run under gjs against step 2's stub DOM
(docs/sprint/M32-step3-gjs/) and in headless Firefox on dev
(docs/sprint/M32-step3-build.md); what a browser draws and animates is the
test agent's. No socket is opened.
"""
import re
from pathlib import Path

import pytest

from tests.test_m34_work_db import we  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parent.parent
PAGE = (REPO / "orchestrator" / "static" / "work.html").read_text()
SCRIPT = PAGE.split("<script>", 1)[1]
CODE = "\n".join(line for line in SCRIPT.splitlines() if not line.lstrip().startswith("//"))
STYLE = PAGE.split("<style>", 1)[1].split("</style>", 1)[0]
STYLE_NC = re.sub(r"/\*.*?\*/", "", STYLE, flags=re.S)


def _literal(name):
    from tests.test_m27_work_page import _literal as lit
    return lit(PAGE, name)


def _body(name):
    i = SCRIPT.index(f"function {name}(")
    j = SCRIPT.index("{", SCRIPT.index(")", i))
    depth = 0
    for k in range(j, len(SCRIPT)):
        depth += {"{": 1, "}": -1}.get(SCRIPT[k], 0)
        if depth == 0:
            return SCRIPT[j:k + 1]
    raise AssertionError(name)


def _rules():
    """(selector, declarations) of every plain rule, media blocks opened."""
    flat = re.sub(r"@media[^{]*\{((?:[^{}]*\{[^{}]*\})*)\s*\}", r"\1", STYLE_NC)
    flat = re.sub(r"@keyframes[^{]*\{(?:[^{}]*\{[^{}]*\})*\s*\}", "", flat)
    return [(s.strip(), d) for s, d in re.findall(r"([^{}]+)\{([^{}]*)\}", flat)]


def _keyframes():
    return {m.group(1): m.group(2) for m in re.finditer(r"@keyframes\s+([\w-]+)\s*\{((?:[^{}]*\{[^{}]*\})*)\s*\}", STYLE_NC)}


# ═══ the track (plumbing 1) ═════════════════════════════════════════════════

def test_five_stations_and_the_kinds_each_counts():
    t = _literal("TRACK")
    assert t["stations"] == ["criteria", "review", "build", "test", "promote"]
    # A fix is a return to build: no station of its own.
    assert t["kinds"] == {"criteria": ["criteria"], "review": ["review"], "build": ["build", "fix"],
                          "test": ["test"], "promote": []}
    assert "{n}" in t["rounds"] and "{station}" in t["rounds"] and "{station}" in t["skipped"]
    assert set(t["verdict"]) == {"PASSES", "FAILS", "NOT DETERMINED"}
    assert t["graded"] == {"pass": "pass", "fail": "fail"}


def test_every_stage_of_the_exporter_has_a_station_or_none(we):
    m = _literal("STAGE_STATION")
    assert set(m) == set(we.STAGE_WORDS)
    stations = _literal("TRACK")["stations"]
    assert all(v is None or v in stations for v in m.values())
    assert m["waiting"] is None and m["promoted"] is None
    assert m["implementing"] == m["fixing"] == "build" and m["testing"] == "test" and m["planning"] == "criteria"
    assert m["reviewing"] == "review" and m["to_promote"] == m["promoting"] == "promote"


def test_the_page_computes_no_stage():
    """The current station is the exporter's stage looked up, nothing else."""
    t = _body("trackOf")
    assert "STAGE_STATION[(it.stage || {}).stage]" in t
    assert t.count("STAGE_STATION") == 1 and "stage.words" not in t and "detail" not in t


def test_a_station_is_filled_by_kind_and_state_not_place():
    t = _body("trackOf")
    assert "arr(TRACK.kinds[st]).includes(s.kind)" in t
    assert "mine.some(s => s.state === 'finished')" in t
    assert "s.state !== 'stopped'" in t                       # a stopped step is not counted
    assert "done.promote = !!promoted" in t
    # Rounds, not extra stations: a count over one is the station's label.
    assert "count[st] > 1 ? TRACK.rounds" in t
    assert not re.search(r"steps\[\s*\d", t)                  # never by position


def test_a_test_station_takes_graded_before_verdict_and_a_skipped_review_is_hollow():
    t = _body("trackOf")
    g, v = t.index("last.graded ?"), t.index("TRACK.verdict[")
    assert g < v
    assert "st === 'review' && past ? 'skipped'" in t
    assert "done.build || done.test || open.build || open.test || curAt > TRACK.stations.indexOf('review')" in t
    rules = dict(_rules())
    assert "border-style: dashed" in rules[".stn.skipped .stn-dot"] and "transparent" in rules[".stn.skipped .stn-dot"]


def test_the_track_is_on_the_item_under_way_and_on_done_rows_only():
    assert "track: trackPart(it, true, false)" in _body("renderUnderWay")
    assert "track: trackPart(it, false, true)" in _body("doneRow")
    assert CODE.count("trackPart(") == 3                         # its definition and those two
    for f in ("renderQueue", "renderDropped", "noItemRow"):
        assert "trackPart" not in _body(f), f
    assert ".row-track:empty { display: none; }" in STYLE


def test_the_track_is_drawn_in_place():
    """Its stations keep their nodes, so a running pulse is the same node
    after an update (criterion 3c); patchParts clears every other part."""
    tp = _body("trackPart")
    assert "if (!p._stn)" in tp and "}, true];" in tp
    pp = _body("patchParts")
    assert "if (!inPlace) clear(p);" in pp
    assert "if (o.track) parts.track = o.track;" in _body("itemRow")


# ═══ motion (plumbing 2 to 4) ═══════════════════════════════════════════════

def test_only_transform_and_opacity_are_animated():
    kf = _keyframes()
    assert {"stn-pulse", "stn-stripe", "row-flash", "stn-fade", "beat", "cover-show"} <= set(kf)
    for name, body in kf.items():
        props = {p.strip() for p in re.findall(r"([\w-]+)\s*:", body)}
        assert props <= {"opacity", "transform"}, (name, props)
    assert "transition" not in STYLE_NC
    assert "background-position" not in STYLE_NC


def test_two_endless_animations_the_pulse_and_the_stripe():
    endless = [(s, d) for s, d in _rules() if "infinite" in d]
    assert [s for s, _ in endless] == [".stn.moving .stn-dot", ".stn.moving.working::after"]
    pulse = _keyframes()["stn-pulse"]
    # The pulse dips below its resting brightness and never rises above it.
    vals = [float(x) for x in re.findall(r"opacity:\s*([\d.]+)", pulse)]
    assert max(vals) == 1 and min(vals) < 1
    # The stripe: a pseudo-element moved by transform inside overflow: hidden.
    assert "overflow: hidden" in dict(_rules())[".stn"]
    assert "transform" in _keyframes()["stn-stripe"]


def test_endless_motion_stops_when_still_and_under_reduced_motion():
    rules = _rules()
    still = [(s, d) for s, d in rules if s.startswith(".motion-still")]
    assert (".motion-still .stn.moving .stn-dot", " animation: none; ") in still
    assert any(s == ".motion-still .stn.moving.working::after" and "animation: none" in d and "display: none" in d
               for s, d in still)
    media = re.search(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n  \}", STYLE_NC, re.S).group(1)
    for sel in (".stn.moving .stn-dot", ".stn.fade-in", "details.row.flash > summary::after", ".stn.moving.working::after"):
        assert sel in media, sel
    assert media.count("animation: none") == 2 and "display: none" in media
    m = _literal("MOTION")
    assert m == {"still_after_ms": 30000, "still_class": "motion-still"}
    rm = _body("renderMotion")
    assert "now - live.answeredAt > MOTION.still_after_ms" in rm and "!live.fresh" in rm
    assert "live.fresh = resp.source.state === 'fresh';" in _body("liveAnswered")
    assert "renderMotion(now);" in _body("renderLive")


def test_the_card_that_moves_is_the_first_being_worked():
    """Ruling on step 3's test, B1: with more than one item under way, the
    card that moves is the first whose current station has an agent
    working; if none has, the first with a current station. One station
    moves, so still at most two endless effects."""
    rm = _body("renderMoving")
    assert "const pick = cur.find(n => n.classList.contains('working')) || cur[0] || null;" in rm
    assert "const want = n === pick;" in rm                    # one station on the whole of Under way
    assert "first" not in rm and "i++" not in rm               # never by place alone
    assert "renderMoving();" in _body("renderUnderWay")
    # "working" is set on a current station only while an agent works and the stage is not blocked.
    assert "s.state === 'cur' && clock && !blocked ? 'working' : null" in _body("trackPart")
    # Which card moves is not in the track's signature: becoming first is no flash.
    assert "return [sig(t, clock, blocked), p => {" in _body("trackPart")


def test_the_flash_hangs_on_a_part_signature_and_never_on_first_load():
    pl = _body("patchList")
    assert "const first = !box._patched;" in pl and "box._patched = true;" in pl
    assert "const changed = patchParts(n, r.parts || {});" in pl
    assert "if (!first && (r.flash === 'change' ? made || changed : r.flash === 'new' && made)) flashes.push(n);" in pl
    assert "once(n, 'flash')" in pl and CODE.count("'flash'") == 1
    pp = _body("patchParts")
    assert "if (p._sig === sig) continue;" in pp and "if (p._sig !== undefined) changed = true;" in pp
    assert "flash: 'change'" in _body("renderUnderWay") and "flash: 'new'" in _body("doneRow")
    for f in ("renderQueue", "renderDropped", "renderBacklog"):
        assert "flash" not in _body(f), f
    assert "box._patched = undefined;" in _body("renderEmpty")
    # The lines set to the same text on every export (M32-step2-e2e note 2)
    # are outside every patched list.
    for line in ("under-way-note", "queue-note", "dropped-count", "src-line"):
        assert f"getElementById('{line}').textContent" in CODE, line


def test_a_verdict_fades_in_once_on_a_change():
    tp = _body("trackPart")
    assert "p._vkey !== undefined && p._vkey !== vkey && s.state === 'done' && s.grade != null" in tp
    assert "if (fade) once(n, 'fade-in');" in tp
    assert "animation: stn-fade 1.2s ease-out 1;" in STYLE
    assert "animation: row-flash 1.8s ease-out 1;" in STYLE


def test_the_agents_time_counts_on_the_routes_clock():
    ct = _body("clockText")
    assert "Number(live.ts) + age - st" in ct and "liveAge(now)" in ct
    assert "Date.now" not in CODE and "new Date()" not in CODE
    assert "renderClocks(now);" in _body("renderLive")
    rules = dict(_rules())
    clock = rules[".stn-clock"]
    for d in ("font-variant-numeric: tabular-nums", "contain: content", "width: 8ch", "display: inline-block"):
        assert d in clock, d
    ac = _body("agentClock")
    assert "x.status === 'working'" in ac and "x.state === 'started' && x.agent === a.name" in ac


# ═══ contrast (plumbing 5) ══════════════════════════════════════════════════

def test_nothing_that_carries_state_is_drawn_in_text3():
    """--text3 is left on separators, the fixed explanatory notes, the
    "… more" of a folded entry and a fold's marker."""
    users = sorted({s for s, d in _rules() if "var(--text3)" in d})
    assert users == sorted([".state-line .sep", ".entry .more", ".note-line", "details.heard > summary::before"])
    # The dropped count sits in a note-line and is state: drawn in --text2.
    assert 'class="note-line count" id="dropped-count"' in PAGE
    assert "color: var(--text2)" in dict(_rules())["details.fold-list > summary .count"]
    # And no note-line is filled from the data but that count.
    for m in re.finditer(r"el\('[a-z]+', 'note-line'", CODE):
        pytest.fail(f"a note-line drawn from data: {m.group(0)}")


# ═══ the rulings on step 3's test (M32-criteria.md) ═════════════════════════

def test_note2_a_blocked_station_never_moves_and_keeps_its_red():
    rm = _body("renderMoving")
    assert "n.classList.contains('cur') && !n.classList.contains('blocked')" in rm
    tp = _body("trackPart")
    assert "s.state === 'cur' && blocked ? 'blocked' : null" in tp
    rules = dict(_rules())
    assert "var(--red)" in rules[".stn.cur.blocked"] and "var(--red)" in rules[".stn.cur.blocked .stn-dot"]
    # No endless rule names a blocked station: the pulse and the stripe hang on "moving" alone.
    for sel, d in _rules():
        if "infinite" in d:
            assert "blocked" not in sel, sel


def test_b2_an_unfinished_round_is_never_drawn_with_an_earlier_grade():
    t = _body("trackOf")
    assert "open[st] = (latestRound(mine) || {}).state === 'started';" in t
    # Not current and unfinished comes before filled: an earlier finished round never fills it.
    assert "i === curAt ? 'cur' : open[st] ? 'started' : done[st] ? 'done'" in t
    # No grade on a station that is not filled.
    assert "grade: state === 'done' ? grade : null" in t
    # The count is the rounds started: started steps are counted, stopped ones not.
    assert "count[st] = mine.length;" in t and "s.state !== 'stopped'" in t
    # The grade is the latest finished round's, by the same rule.
    assert "const last = latestRound(tests);" in t
    lr = _body("latestRound")
    assert "(s.started_at || '') >= (last.started_at || '')" in lr
    rules = dict(_rules())
    dot = rules[".stn.started .stn-dot"]
    for colour in ("--green", "--red", "--amber", "--blue", "--text3"):
        assert colour not in dot + rules[".stn.started"], colour
    # The grade class is put on a filled station only.
    assert "s.state === 'done' ? s.grade : null" in _body("trackPart")


def test_s1_the_clock_box_does_not_wrap():
    clock = dict(_rules())[".stn-clock"]
    assert "white-space: nowrap" in clock and "min-width: 8ch" in clock
    assert _literal("TRACK")["no_start"] == "start not recorded"


def test_the_card_under_way_shows_its_last_five_steps_and_folds_the_rest():
    assert _literal("STEPS_FOLD") == {"shown": 5, "key": "{row}:steps"}
    t = _literal("ROW_TEXT")
    assert t["earlier_one"] == "1 earlier step" and t["earlier_many"] == "{n} earlier steps"
    sb = _body("stepsBlock")
    assert "const cut = fold ? Math.max(0, all.length - STEPS_FOLD.shown) : 0;" in sb
    # One fold, closed by default, kept like any other (foldable: sessionStorage, the one toggle listener).
    assert "foldable(box.appendChild(el('details', 'steps-earlier')), fold, false)" in sb
    assert sb.count("foldable(") == 1 and "all.slice(0, cut)" in sb and "all.slice(cut)" in sb
    assert "...arr(it.steps), ...arr(it.uncommitted)" in sb
    # Only the item under way's card: Done rows show every step.
    assert "stepsFold: true" in _body("renderUnderWay") and "stepsFold" not in _body("doneRow")
    ir = _body("itemRow")
    assert "fill(STEPS_FOLD.key, { row: key })" in ir and "bodyInto(p, it, owed, stepsFold)" in ir
    assert "stepsBlock(it, stepsFold)" in _body("bodyInto")
    # Its marker in --text2 like the other folds' (generic details summary), never --text3.
    assert "var(--text3)" not in "".join(d for s_, d in _rules() if "steps-earlier" in s_)
