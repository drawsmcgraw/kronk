"""/work rows that unfold (work item M32 step 2, docs/sprint/M32-criteria.md,
"Steps 2 and 3", step 2).

What the suite can check without a JavaScript engine: the exporter's
schema-4 keys (items[].ended_at for a dropped item, items[].open_list,
decisions_owed[].text) and the agents of queued items; the route's schema;
and the page's literals and the parts of its script the plumbing points
name (one patch function for every list, the keys, the frame of a row,
the folds in sessionStorage, the order of the sections, Heard's folded
line, the banner that moves nothing, no links in titles). The patching
itself was run under gjs against a stub DOM and in headless Firefox on dev
(docs/sprint/M32-step2-build.md); a real browser is the test agent's. No
socket is opened.
"""
import json
import re
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

import work_api
from tests.test_m28_agent_tool import commit
from tests.test_m33_work_state import (  # noqa: F401  (fixtures)
    Tool, active_item, db, fake_herdr, quiet_env, repo, t, wk)
from tests.test_m34_work_db import we  # noqa: F401

pytestmark = pytest.mark.usefixtures("no_network")

REPO = Path(__file__).resolve().parent.parent
PAGE = (REPO / "orchestrator" / "static" / "work.html").read_text()
SCRIPT = PAGE.split("<script>", 1)[1]
CODE = "\n".join(line for line in SCRIPT.splitlines() if not line.lstrip().startswith("//"))
STYLE = PAGE.split("<style>", 1)[1].split("</style>", 1)[0]
MAIN = PAGE.split("<main>", 1)[1].split("</main>", 1)[0]


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


# ═══ the exporter, schema 4 (plumbing 6) ════════════════════════════════════

def test_schema_4_and_the_route_knows_it(we):
    assert we.SCHEMA == 4 and work_api.SCHEMA_KNOWN == 4


def _walk(t, repo):
    """M1 active, M2 promoted, M3 queued, M4 held, M5 dropped; matters open,
    closed and escaped."""
    active_item(t, repo, 1)
    active_item(t, repo, 2)
    commit(repo, {"docs/sprint/M2-promote.md": "x\n"})
    t("promote", "M2", "--no-tag", "--deployed", "no", "--untested", "nothing")
    t("add", "--title", "queued")
    t("add", "--title", "held")
    t("hold", "M4", "--why", "spring")
    t("add", "--title", "dropped")
    t("drop", "M5", "--why", "QZPRIVATE reason")
    ids = {}
    ids["owed"] = t.one("matter", "add", "decision", "--owner", "drew", "--item", "M1", "--text", "ship M1?",
                        "--source", "QZSOURCE", "--blocks")["matter"]
    ids["mine"] = t.one("matter", "add", "decision", "--owner", "coordinator", "--item", "M1",
                        "--text", "my call")["matter"]
    ids["left"] = t.one("matter", "add", "leftover", "--owner", "drew", "--item", "M2", "--text",
                        "a <img src=x onerror=alert(1)> and [link](https://example.invalid/)")["matter"]
    ids["untriaged"] = t.one("matter", "add", "leftover", "--owner", "coordinator", "--item", "M3",
                             "--text", "nobody's yet")["matter"]
    c = sqlite3.connect(t.db)                                       # as imported: no owner yet
    c.execute("UPDATE open_thing SET owner = NULL WHERE id = ?", (ids["untriaged"],))
    c.commit()
    c.close()
    ids["no_item"] = t.one("matter", "add", "leftover", "--owner", "drew", "--text", "QZNOITEM")["matter"]
    ids["owed_no_item"] = t.one("matter", "add", "decision", "--owner", "drew", "--text", "an open question")["matter"]
    gone = t.one("matter", "add", "decision", "--owner", "drew", "--item", "M1", "--text", "QZCLOSED")["matter"]
    t("matter", "close", gone, "--how", "yes")
    t("matter", "add", "escape", "--owner", "coordinator", "--item", "M2", "--text", "QZESCAPE")
    return ids


def test_open_list_holds_each_open_leftover_and_decision_with_its_text(we, t, repo):
    ids = _walk(t, repo)
    data = we.collect(repo, t.db)
    items = {i["id"]: i for i in data["items"]}
    assert all(isinstance(i["open_list"], list) for i in items.values())
    m1 = items["M1"]["open_list"]
    assert [(o["id"], o["kind"], o["owner"], o["blocks"], o["text"]) for o in m1] == [
        (ids["owed"], "decision", "drew", True, "ship M1?"), (ids["mine"], "decision", "coordinator", False, "my call")]
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", o["date"]) for o in m1)
    assert set(m1[0]) == {"id", "kind", "owner", "blocks", "date", "text"}
    # A promoted item keeps its open leftover; the text is the stored text, markup and all.
    assert [o["text"] for o in items["M2"]["open_list"]] == [
        "a <img src=x onerror=alert(1)> and [link](https://example.invalid/)"]
    assert [(o["owner"], o["text"]) for o in items["M3"]["open_list"]] == [(None, "nobody's yet")]
    assert items["M4"]["open_list"] == [] and items["M5"]["open_list"] == []
    raw = json.dumps(data)
    # A closed matter, an escape, a matter's source and a matter of no item
    # are not on any row.
    for word in ("QZCLOSED", "QZESCAPE", "QZSOURCE", "QZNOITEM"):
        assert word not in raw, word


def test_decisions_owed_carry_their_text(we, t, repo):
    ids = _walk(t, repo)
    owed = we.collect(repo, t.db)["decisions_owed"]
    assert [(o["id"], o["item"], o["blocks"], o["text"]) for o in owed] == [
        (ids["owed"], "M1", True, "ship M1?"), (ids["owed_no_item"], None, False, "an open question")]


def test_a_dropped_item_has_its_end_and_never_its_reason(we, t, repo):
    _walk(t, repo)
    data = we.collect(repo, t.db)
    items = {i["id"]: i for i in data["items"]}
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", items["M5"]["ended_at"])
    # Only a dropped item: a promoted one has an end in the table and none here.
    assert [i for i, it in items.items() if it["ended_at"] is not None] == ["M5"]
    assert "QZPRIVATE" not in json.dumps(data)


def test_a_day_that_does_not_parse_is_none_not_the_stored_value(we, t, repo):
    active_item(t, repo, 1)
    t("matter", "add", "leftover", "--owner", "drew", "--item", "M1", "--text", "x")
    c = sqlite3.connect(t.db)
    c.execute("UPDATE open_thing SET opened_at = 'QZNOTATIME'")
    c.commit()
    c.close()
    t("note", "M1", "--next", "go")                                 # a write: the dump follows
    data = we.collect(repo, t.db)
    m1 = {i["id"]: i for i in data["items"]}["M1"]
    assert [o["date"] for o in m1["open_list"]] == [None]
    assert "QZNOTATIME" not in json.dumps(m1)


def test_a_long_text_is_clipped_at_the_entry_limit(we, t, repo):
    active_item(t, repo, 1)
    t("matter", "add", "leftover", "--owner", "drew", "--item", "M1", "--text", "y" * 5000)
    text = {i["id"]: i for i in we.collect(repo, t.db)["items"]}["M1"]["open_list"][0]["text"]
    assert len(text) == we.MATTER_MAX == we.ENTRY_MAX and text.endswith("…")


def test_with_the_database_not_read_no_item_and_no_owed_text(we, t, repo, tmp_path):
    active_item(t, repo, 1)
    Path(f"{t.db}.dump.sql").unlink()
    data = we.collect(repo, t.db)
    assert data["database"]["state"] == "missing" and data["items"] == [] and data["decisions_owed"] == []


def test_an_agent_of_a_queued_or_held_item_is_on_its_row(we, t, repo, fake_herdr):
    """M34-e2e note 8 (plumbing 8)."""
    _walk(t, repo)
    for name in ("m1-build", "m3-review", "m4-test", "m5-fix1", "m2-test"):
        fake_herdr.add_agent(name, "working", repo)
    data = we.collect(repo, t.db)
    items = {i["id"]: i for i in data["items"]}
    assert [a["name"] for a in items["M3"]["agents"]] == ["m3-review"]
    assert [a["name"] for a in items["M4"]["agents"]] == ["m4-test"]
    assert {a["name"]: a["why"] for a in data["other_agents"]} == {
        "m5-fix1": "its item is dropped", "m2-test": "its item is promoted"}


def test_the_real_export_carries_leftover_and_decision_text():
    """2d's second half, on whatever export the host last wrote to dev's
    data (read only): every open entry and owed decision has a text key."""
    p = REPO / "data" / "dev" / "work.json"
    if not p.is_file():
        pytest.skip("no dev export here")
    d = json.loads(p.read_text())
    if d.get("schema", 0) < 4 or (d.get("database") or {}).get("state") != "ok":
        pytest.skip(f"dev's export is schema {d.get('schema')}, database {(d.get('database') or {}).get('state')}")
    entries = [o for i in d["items"] for o in i["open_list"]]
    assert entries and all(isinstance(o["text"], str) and o["text"] for o in entries)
    assert all("text" in o for o in d["decisions_owed"])


# ═══ the page (plumbing 1 to 5, 7 to 9) ═════════════════════════════════════

def test_one_patch_function_for_every_list():
    """Plumbing 1: Under way, the Queue, Done, Dropped, the Backlog's sections
    and entries, Shipped: all through patchList, none cleared and rebuilt."""
    assert CODE.count("function patchList(") == 1
    for fn, box in (("renderUnderWay", "under-way"), ("renderQueue", "queue"), ("renderDone", "done"),
                    ("renderDropped", "dropped"), ("renderBacklog", "backlog")):
        body = _body(fn)
        assert f"patchList(document.getElementById('{box}')" in body, fn
        assert f"clear(document.getElementById('{box}'))" not in body or fn == "renderQueue", fn
    assert "patchList(document.getElementById('shipped')" in _body("renderDone")
    assert "patchList(n._card" in _body("renderBacklog")
    # A kept node keeps its parts: a part is drawn again only when its signature changed.
    pp = _body("patchParts")
    assert "if (p._sig === sig) continue;" in pp and "p._sig = sig;" in pp
    pl = _body("patchList")
    assert "kept.get(r.key)" in pl and "longestRun(" in pl and "if (!stay.has(i)) box.insertBefore(nodes[i], next);" in pl
    assert "innerHTML" not in CODE and "replaceChild" not in CODE and "replaceWith" not in CODE


def test_the_keys():
    """Done by promotion and id (not id@line: null for every promotion since
    M34); Backlog entries by title, not by line (review 6)."""
    assert "itemRow(`uw:${id}`, 'card', true," in _body("renderUnderWay")
    assert "itemRow(`q:${id}`, 'queue-row', false," in _body("renderQueue")
    assert "`done:${r.promotion}:${r.id}`" in _body("doneRow")
    assert "itemRow(`drop:${it.id}`, 'done-row', false," in _body("renderDropped")
    assert "key: `sec:${s.section}`" in _body("renderBacklog")
    er = _body("entryRow")
    assert "const key = `${prefix}:${e.title ?? ''}`;" in er and "line: undefined" in er
    assert "`deploy:${e.promotion" in _body("noItemRow")
    assert "uniqueKeys(" in _body("renderBacklog") and "`${r.key}#${n}`" in _body("uniqueKeys")


def test_a_row_is_a_fixed_frame_with_a_slot_for_the_track():
    rf = _body("rowFrame")
    for part in ("head: sum.appendChild(el('span', 'row-head'))", "stage: sum.appendChild(el('span', 'row-stage'))",
                 "track: sum.appendChild(el('span', 'row-track'))", "body: det.appendChild(el('div', 'row-body'))"):
        assert part in rf, part
    # Step 3 draws the track (tests/test_m32_step3.py); a row given none
    # leaves the slot empty and hidden.
    assert ".row-track:empty { display: none; }" in STYLE
    ir = _body("itemRow")
    assert "head: [" in ir and "stage: [stage[0], stage[1]]" in ir and "body: [bodySig(it, owed)" in ir
    assert "if (o.track) parts.track = o.track;" in ir


def test_the_body_unfolds_to_what_why_decisions_leftovers_and_steps():
    b = _body("bodyInto")
    for needle in ("it.handoff", "ROW_TEXT.what", "it.shipped", "ROW_TEXT.shipped", "it.source", "ROW_TEXT.why",
                   "ROW_TEXT.waits_on_drew", "ROW_TEXT.owed", "it.open_list", "ROW_TEXT.open", "stepsBlock(it, stepsFold)"):
        assert needle in b, needle
    # A part with nothing to say is not drawn: what only with a handoff, why only with a source.
    assert "if (it.handoff) mdText(part(body, ROW_TEXT.what)" in b and "if (it.source) mdText(part(body, ROW_TEXT.why)" in b
    t = _literal("ROW_TEXT")
    assert (t["what"], t["why"], t["steps"]) == ("What", "Why", "Steps")
    # Every key bodyInto reads is in its signature.
    sig_line = SCRIPT.split("const bodySig = ", 1)[1].split(";\n", 1)[0]
    for k in ("it.handoff", "it.source", "it.shipped", "it.steps", "it.uncommitted", "it.open_list", "owed",
              "it.waits_on", "it.roadmap", "live.zone"):
        assert k in sig_line, k


def test_a_step_shows_kind_round_agent_verdict_or_grade_and_its_times():
    s = _body("stepRow")
    for needle in ("ROW_TEXT.step_round", "stepTimes(s)", "verdictText(v)", "ROW_TEXT.graded", "s.agent", "s.file"):
        assert needle in s, needle
    assert "if (s.agent) row.appendChild(el('span', 'mono', s.agent));" in s       # every step's agent, not only a running one
    t = _body("stepTimes")
    assert "fmtTime(s.started_at)" in t and "houseString(d, { hour: '2-digit', minute: '2-digit' })" in t


def test_leftover_and_decision_text_is_set_as_text():
    m = _body("matterRow")
    assert "el('div', 'matter-text', m.text)" in m and "mdText" not in m
    assert "matterRow({ ...d, kind: 'decision', owner: 'drew' })" in _body("bodyInto")
    assert set(_literal("MATTER_KIND")) == {"leftover", "decision"}
    assert set(_literal("OWNER_WORD")) == {"drew", "coordinator"}


def test_titles_and_labels_make_no_link():
    """Plumbing 8 (M34-e2e note 11): mdText makes a link only when asked, and
    it is asked only for the body of a ROADMAP entry."""
    md = _body("mdText")
    assert "if (links === true && url.startsWith(MD_LINK.prefix)) {" in md
    calls = re.findall(r"mdText\(([^;\n]*)\)", CODE)
    linked = [c for c in calls if re.search(r",\s*true\s*$", c)]
    assert linked and all(c.startswith(("det.appendChild(el('div', 'entry-rest'))", "box, f.head", "box, e.text")) for c in linked), linked
    assert "f.head, true" not in _body("entryInto").split("const sum = mdText(", 1)[1].split(";", 1)[0]


def test_folds_the_item_under_way_open_the_rest_closed_kept_in_session_storage():
    assert _literal("FOLD") == {"store": "kronk.work.folds"}
    assert "sessionStorage.getItem(FOLD.store)" in _body("foldsLoad") and "sessionStorage.setItem(FOLD.store" in _body("foldsSave")
    f = _body("foldable")
    assert "det.open = v === undefined ? !!byDefault : v;" in f
    # One listener, on the way down (toggle does not bubble); only a fold that differs from its default is kept.
    fi = _body("foldsInit")
    assert "addEventListener('toggle'" in fi and "}, true);" in fi
    assert "folds.delete(d.dataset.fold); else folds.set(d.dataset.fold, d.open);" in fi
    assert "foldable(document.getElementById('dropped-fold'), 'dropped', false);" in fi
    # Only the item under way is open by default; Heard is never kept.
    assert len(re.findall(r"itemRow\([^)]*?, true,", CODE)) == 1
    assert "data-fold" not in PAGE.split('<details class="heard"', 1)[1].split("</details>", 1)[0]
    assert "localStorage" not in _body("foldsLoad") + _body("foldsSave")


def test_the_page_order():
    """Plumbing 4 (review 3.9): the state line and live mark, Under way,
    Queue, Heard, Done, Dropped, Backlog."""
    marks = ['id="state-line"', ">Under way<", 'id="queue-section"', 'id="heard"', ">Done<", 'id="dropped-section"',
             ">Backlog<", ">Not read by the exporter<"]
    at = [MAIN.index(m) for m in marks]
    assert at == sorted(at), dict(zip(marks, at))
    assert 'class="header-dot beat" id="beat"' in PAGE.split("<main>", 1)[0]          # the live mark, in the header


def test_the_dropped_list_is_folded_after_done():
    sec = MAIN.split('<section id="dropped-section" hidden>', 1)[1].split("</section>", 1)[0]
    assert '<details class="fold-list" id="dropped-fold">' in sec and "<details open" not in sec
    d = _body("renderDropped")
    assert "it.state === 'dropped'" in d and "houseDay(it.ended_at)" in d and "ROW_TEXT.dropped" in d
    assert "ended_why" not in CODE
    assert "toLocaleDateString('en-CA', { timeZone: z.name })" in _body("houseDay")


def test_an_agent_of_a_queued_item_is_on_its_row():
    assert "for (const a of arr(it.agents)) meta.appendChild(el('span', 'mono', fill(QUEUE_TEXT.agent" in _body("renderQueue")
    assert _literal("QUEUE_TEXT")["agent"] == "{name}: {status}"


def test_heards_folded_line_has_the_newest_runs_words_and_time():
    """Plumbing 5 (Drew #89): after the counts, set as text; asked for on
    every tick (limit=1, no filter), folded or open."""
    h = _literal("HEARD")
    assert "{words}" in h["newest"] and "{state}" in h["newest_no_words"]
    tick = _body("heardTick")
    assert "heardFetch(new URLSearchParams({ limit: '1' }))" in tick and "heard.newest = arr(top.runs)[0] || null" in tick
    assert "limit: '0'" not in CODE
    st = SCRIPT.split("function heardStatus(", 1)[1].split("\nfunction ", 1)[0]
    assert st.index("HEARD.summary") < st.index("heardNewestPart(h)") < st.index("HEARD.as_of")
    np = _body("heardNewestPart")
    assert "box.textContent = n.heard_state === 'text'" in np and "HEARD.heard_text[n.heard_state]" in np
    assert "houseShort(n.local_time)" in np


def test_the_banner_of_a_failed_poll_moves_nothing():
    """Plumbing 9, M32-e2e note 4: it floats over the page, outside <main>."""
    assert 'id="live-banner"' not in MAIN and 'id="reload-note"' not in MAIN
    floats = PAGE.split('<div class="float-banners" id="float-banners">', 1)[1].split("</div>\n</div>", 1)[0]
    assert 'id="live-banner"' in floats and 'id="reload-note"' in floats
    assert re.search(r"\.float-banners \{ position: fixed;", STYLE)
    assert "below" not in _literal("LIVE_TEXT")["no_answer"]           # it is at the bottom now


def test_heard_asks_at_once_after_a_cut():
    """Plumbing 9, M32-e2e note 6."""
    a = _body("liveAnswered")
    assert a.index("if (live.fail) heard.lastEnd = -Infinity;") < a.index("live.fail = null;")


def test_the_page_reads_the_schema_4_keys():
    for needle in ("it.open_list", "it.ended_at", "w.decisions_owed", "m.text", "it.agents"):
        assert needle in CODE, needle


def test_the_rows_add_no_motion():
    """Step 3 brings motion, in its own block (tests/test_m32_step3.py); the
    rows' styles add none."""
    rows = STYLE.split("/* A failed poll's banner", 1)[1].split("/* The track and its motion", 1)[0]
    assert "infinite" not in rows and "@keyframes" not in rows and "animation" not in rows
