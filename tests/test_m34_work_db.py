"""/work drawn from the work-state database (work item M34,
docs/sprint/M34-criteria.md, "Revised after the review"; the criteria
numbers below are that section's).

scripts/work.py gains overview(), one read-only function that `resume`
prints from and the exporter serialises (plumbing 5). The exporter reads
the dump work.py writes beside the database, never the database (plumbing
1). These pin: overview() on rows the tool made; the file agrees with
`resume` item by item (criterion 1); every difference from the schema-1
reader over the same commit has a named class (2); an item walked through
its life shows at each step the stage `resume` gives (3); no lock, no file
beside the dump, under 0.5 s, writes not delayed, two runs leave one whole
file (5); exactly the allowed columns reach the file (6); every state of an
unusable dump (8); the suite never reads the live database or its dump (10).

Repos and databases are temporary (the M33 tests' fixtures); herdr is the
M28 tests' fake. No test names the live database or its dump, and none
opens a socket. M27_WORK_EXPORT points these at a scratch exporter, as it
does the M27 tests; the work.py beside it is the one it loads.
"""
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.test_m28_agent_tool import commit, git
from tests.test_m33_work_state import (  # noqa: F401  (fixtures)
    CRIT, Tool, active_item, codes_all, db, fake_herdr, quiet_env, repo, t, wk)

pytestmark = pytest.mark.usefixtures("no_network")

REPO = Path(__file__).resolve().parent.parent
EXPORT = Path(os.environ.get("M27_WORK_EXPORT", REPO / "scripts" / "work_export.py"))
WORK_TOOL = EXPORT.parent / "work.py"
BASE_PATH = "/usr/bin:/bin"
LIVE_DIR = Path.home() / ".local" / "state" / "kronk-work"


@pytest.fixture
def we():
    spec = importlib.util.spec_from_file_location("m34_work_export", EXPORT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def gathered(wk, t):
    return wk.gather(wk.Ctx(t.db, t.repo, None, ["resume"]))


def test_overview_is_what_resume_prints(wk, t, repo, fake_herdr):
    active_item(t, repo)
    t("add", "--title", "two", "--after", "M1")
    t("add", "--title", "three")
    fake_herdr.add_agent("m1-build", "working", repo)
    fake_herdr.add_agent("m9-test", "working", repo)          # no item M9: still parsed to it
    t("step", "start", "M1", "build", "--agent", "m1-build")
    t("matter", "add", "decision", "--owner", "drew", "--item", "M1", "--text", "first?", "--blocks")
    t("matter", "add", "decision", "--owner", "drew", "--text", "second?")
    t("matter", "add", "decision", "--owner", "coordinator", "--item", "M2", "--text", "mine")
    t("matter", "add", "leftover", "--owner", "drew", "--item", "M1", "--text", "a")
    t("matter", "add", "leftover", "--owner", "coordinator", "--item", "M1", "--text", "b")
    gone = t.one("matter", "add", "leftover", "--owner", "coordinator", "--item", "M1", "--text", "c")
    t("matter", "close", gone["matter"], "--how", "done")
    v = wk.overview(gathered(wk, t))

    assert [x["item"]["id"] for x in v["active"]] == ["M1"]
    (m1,) = v["active"]
    assert m1["stage"]["stage"] == "implementing" and m1["stage"] == v["stages"]["M1"]
    assert [a["name"] for a in m1["agents"]] == ["m1-build"]
    assert [(s["kind"], s["agent"]) for s in m1["started"]] == [("build", "m1-build")]
    assert m1["last"]["kind"] == "criteria"
    assert set(v["stages"]) == {"M1", "M2", "M3"}
    assert v["agents"]["M9"][0]["role"] == "test"
    assert [(q["id"], q["blocked_by"] is not None, q["next"]) for q in v["queue"]] == \
        [("M2", True, False), ("M3", False, True)]
    assert [m["text"] for m in v["decisions"]] == ["first?", "second?"]
    assert [m["text"] for m in v["other_decisions"]] == ["mine"]
    assert [m["text"] for m in v["leftovers"]["drew"]] == ["a"]
    assert [m["text"] for m in v["leftovers"]["coordinator"]] == ["b"]
    assert v["untriaged"] == []
    assert v["counts"]["M1"] == {"leftovers": {"drew": 1, "coordinator": 1, "not_triaged": 0},
                                 "decisions": {"drew": 1, "coordinator": 0, "not_triaged": 0}}
    assert v["counts"][None]["decisions"]["drew"] == 1
    assert v["counts"]["M2"]["decisions"]["coordinator"] == 1
    assert v["escape_window"] == [] and v["escapes"] == []

    text = t.text("resume")
    assert "== Under way: 1" in text and "stage: implementing (implementing, agent m1-build working)" in text
    assert "== Owed by Drew: 2 open decision(s)" in text and "== Open leftovers: 2 triaged, 0 not yet" in text


def test_overview_escapes_and_the_window(wk, t, repo, fake_herdr):
    for n in range(1, 13):
        active_item(t, repo, n)
        commit(repo, {f"docs/sprint/M{n}-promote.md": "x\n"})
        t("promote", f"M{n}", "--no-tag", "--deployed", "yes")
    t("matter", "add", "escape", "--owner", "coordinator", "--item", "M1", "--text", "old")
    t("matter", "add", "escape", "--owner", "coordinator", "--item", "M12", "--text", "new one")
    v = wk.overview(gathered(wk, t))
    assert v["escape_window"] == [f"M{n}" for n in range(12, 2, -1)]
    assert [m["text"] for m in v["escapes"]] == ["new one"]
    assert v["active"] == [] and all(s["stage"] == "promoted" for s in v["stages"].values())


def test_overview_reads_nothing_but_what_it_is_given(wk):
    g = {"items": [{"id": "M1", "state": "queued", "title": "x"}], "steps": [], "promotions": [], "pitems": [],
         "matters": [], "queue": [], "tags": {}, "branch_commits": None,
         "herdr": {"known": False, "by_name": {}}}
    v = wk.overview(g)
    assert v["stages"]["M1"]["detail"] == "no finished step recorded" and v["active"] == []


# ═══ the file against `resume` (criteria 1 and 3) ══════════════════════════

RESUME_ITEM = re.compile(r"^(M\S+)  tier \S+  ")
RESUME_STAGE = re.compile(r"^    stage: ([^(]*?)(?: \((.*)\))?$")
RESUME_QUEUE = re.compile(r"^(\d+)\. (M\S+)  tier \S+  .*?(  <- next)?$")
RESUME_AFTER = re.compile(r"^    after: (M\S+)(?: \((.*)\))?$")


def resume_view(text: str) -> dict:
    """What `work.py resume` says, read back: the items under way with
    their stage, the queue in order with next, what holds each and why it
    is held, and the counts."""
    v = {"active": {}, "queue": [], "owed": None, "leftovers": {}, "escapes": None}
    section, cur = None, None
    for line in text.splitlines():
        if line.startswith("== "):
            section, cur = line, None
            m = re.match(r"== Owed by Drew: (\d+) open", line)
            if m:
                v["owed"] = int(m.group(1))
            m = re.match(r"== Open leftovers: (\d+) triaged, (\d+) not yet triaged", line)
            if m:
                v["leftovers"]["not_triaged"] = int(m.group(2))
            m = re.match(r"== Escapes within the last \d+ promoted items \(.*\): (\d+)$", line)
            if m:
                v["escapes"] = int(m.group(1))
            continue
        if section and section.startswith("== Under way"):
            m = RESUME_ITEM.match(line)
            if m:
                cur = m.group(1)
            m = RESUME_STAGE.match(line)
            if m and cur:
                v["active"][cur] = (m.group(1), m.group(2))
        elif section and section.startswith("== Queue"):
            m = RESUME_QUEUE.match(line)
            if m:
                cur = {"id": m.group(2), "next": bool(m.group(3)), "blocked_by": None, "held_why": None}
                v["queue"].append(cur)
            elif cur and line.startswith("    HELD: "):
                cur["held_why"] = line[len("    HELD: "):]
            elif cur and RESUME_AFTER.match(line):
                why = RESUME_AFTER.match(line).group(2)
                cur["blocked_by"] = None if why == "promoted" else why
        elif section and section.startswith("== Open leftovers"):
            m = re.match(r"^(drew|coordinator): (\d+)$", line)
            if m:
                v["leftovers"][m.group(1)] = int(m.group(2))
    return v


def disagreements(data: dict, text: str) -> list:
    """Every place the file and `resume` (on the same database) differ:
    under way and each stage, the queue in order with next, blocked_by and
    held_why, the decisions owed, the open leftovers by owner, the escapes.
    Empty when they agree."""
    r = resume_view(text)
    items = {it["id"]: it for it in data["items"]}
    out = []
    if data["under_way"] != list(r["active"]):
        out.append(("under_way", data["under_way"], list(r["active"])))
    for i, (words, detail) in r["active"].items():
        st = items.get(i, {}).get("stage") or {}
        if (st.get("words"), st.get("detail")) != (words, detail):
            out.append((f"{i} stage", (st.get("words"), st.get("detail")), (words, detail)))
    mine = [{"id": i, "next": items[i]["next"], "blocked_by": items[i]["blocked_by"],
             "held_why": items[i]["held_why"]} for i in data["queue"]]
    if mine != r["queue"]:
        out.append(("queue", mine, r["queue"]))
    if len(data["decisions_owed"]) != r["owed"]:
        out.append(("decisions owed", len(data["decisions_owed"]), r["owed"]))
    if data["open_counts"]["leftovers"] != r["leftovers"]:
        out.append(("leftovers", data["open_counts"]["leftovers"], r["leftovers"]))
    if data["escapes"]["count"] != r["escapes"]:
        out.append(("escapes", data["escapes"]["count"], r["escapes"]))
    return out


def test_resume_view_reads_resume(t, repo, fake_herdr):
    active_item(t, repo)
    t("add", "--title", "two", "--after", "M1")
    t("add", "--title", "three")
    t("hold", "M3", "--why", "waits for spring")
    v = resume_view(t.text("resume"))
    assert v["active"] == {"M1": ("planning", "criteria written")}
    assert v["queue"] == [{"id": "M2", "next": False, "blocked_by": "M2 follows M1, which is active, not promoted",
                           "held_why": None},
                          {"id": "M3", "next": False, "blocked_by": None, "held_why": "waits for spring"}]
    assert v["owed"] == 0 and v["leftovers"] == {"drew": 0, "coordinator": 0, "not_triaged": 0}
    assert v["escapes"] == 0


def test_criterion_3_an_item_walked_through_its_life(we, t, repo, fake_herdr):
    """After each step the file shows the stage `resume` gives, and the
    wait and hold fields as the rows have them."""
    def check(**want):
        data = we.collect(repo, t.db)
        assert data["database"]["state"] == "ok", data["database"]
        assert disagreements(data, t.text("resume")) == []
        items = {it["id"]: it for it in data["items"]}
        for i, w in want.items():
            for k, val in w.items():
                got = items[i]["stage"][k[6:]] if k.startswith("stage_") else items[i][k]
                assert got == val, (i, k, got, val)
        return data, items

    t("add", "--title", "the made-up one", "--tier", "3")
    t("add", "--title", "to be dropped")
    t("add", "--title", "to be held")
    t("add", "--title", "waits on Drew")
    t("add", "--title", "queued behind the made-up one", "--after", "M1")
    data, items = check(M1={"state": "queued", "position": 1, "next": True, "tier": 3},
                        M5={"blocked_by": "M5 follows M1, which is queued, not promoted", "after_item": "M1"})
    assert data["queue"] == ["M1", "M2", "M3", "M4", "M5"] and data["under_way"] == []

    commit(repo, {"docs/sprint/M1-criteria.md": CRIT.format(n=1)})
    t("start", "M1")
    t("step", "add", "M1", "criteria")
    t("note", "M1", "--next", "launch the build")
    data, items = check(M1={"state": "active", "position": None, "stage_stage": "planning",
                            "stage_detail": "criteria written", "next_step": "launch the build",
                            "handoff": "thing 1 is made and shown.",
                            "source": 'Drew, 2026-11-24: "make thing 1" and keep it small.'})
    assert data["under_way"] == ["M1"] and data["queue"] == ["M2", "M3", "M4", "M5"]

    fake_herdr.add_agent("m1-build", "working", repo)
    t("step", "start", "M1", "build", "--agent", "m1-build", "--model", "claude-opus-5-5")
    data, items = check(M1={"stage_stage": "implementing",
                            "stage_detail": "implementing, agent m1-build working"})
    build = [s for s in items["M1"]["steps"] if s["kind"] == "build"][0]
    assert (build["state"], build["agent"], build["by"], build["committed_at"]) == ("started", "m1-build", "agent", None)
    assert build["started_at"] and "model" not in build
    assert [a["name"] for a in items["M1"]["agents"]] == ["m1-build"]

    commit(repo, {"docs/sprint/M1-build.md": "# build\n"})
    fake_herdr.set_status("m1-build", "idle")
    t("step", "finish", "M1", "build")
    data, items = check(M1={"stage_stage": "waiting", "stage_detail": "build in, not tested",
                            "notes": ["m1-build: idle, workspace still open"]})

    fake_herdr.add_agent("m1-test", "working", repo)
    t("step", "start", "M1", "test", "--agent", "m1-test")
    check(M1={"stage_stage": "testing"})
    commit(repo, {"docs/sprint/M1-e2e.md": "# test\n\nVerdict: FAILS\n"})
    fake_herdr.set_status("m1-test", "done")
    t("step", "finish", "M1", "test")
    t("grade", "M1", "fail")
    data, items = check(M1={"stage_stage": "waiting", "stage_detail": "test 1: FAILS, graded fail"})
    e2e = [s for s in items["M1"]["steps"] if s["file"] == "M1-e2e.md"][0]
    assert (e2e["verdict"], e2e["graded"], e2e["verdicts"][0]["verdict"]) == ("FAILS", "fail", "FAILS")

    commit(repo, {"docs/sprint/M1-fix1.md": "# fix\n"})
    t("step", "add", "M1", "fix")
    check(M1={"stage_stage": "waiting", "stage_detail": "fix 1 in, not retested"})
    commit(repo, {"docs/sprint/M1-e2e2.md": "# test 2\n\nVerdict: PASSES\n"})
    t("step", "add", "M1", "test", "--round", "2")
    check(M1={"stage_stage": "to_promote", "stage_detail": "test 2: PASSES"})

    t("drop", "M2", "--why", "not wanted")
    t("hold", "M3", "--why", "after the holidays")
    t("note", "M4", "--waits", "drew", "--waits-note", "his go", "--next", "ask him")
    data, items = check(M2={"state": "dropped", "position": None, "next_step": None},
                        M3={"state": "held", "held_why": "after the holidays", "next": False},
                        M4={"state": "queued", "waits_on": "drew", "waits_note": "his go", "next_step": "ask him",
                            "next": True})
    assert data["queue"] == ["M3", "M4", "M5"] and "M2" not in data["queue"]

    git(repo, "tag", "item-M1")
    commit(repo, {"docs/sprint/M1-promote.md": "# checks\n"})
    t("promote", "M1", "--tag", "item-M1", "--deployed", "yes")
    data, items = check(M1={"state": "promoted", "stage_stage": "promoted", "next_step": None},
                        M5={"blocked_by": None, "next": False})
    assert data["under_way"] == []
    (row,) = data["done"]
    assert (row["id"], row["tag"], row["tag_in_git"], row["deployed"], row["tag_only"]) == \
        ("M1", "item-M1", True, "yes", False)
    assert row["verified"] == "docs/sprint/M1-promote.md" and row["line"] is None
    assert row["date"] == we.work_tool().local_day(row["at"]) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["date"])


def test_criterion_1_every_promotion_carries_its_tag(we, t, repo):
    for n, tag in ((1, "item-M1"), (2, None), (3, "item-M3")):
        active_item(t, repo, n)
        commit(repo, {f"docs/sprint/M{n}-promote.md": "x\n"})
        if tag:
            git(repo, "tag", tag)
            t("promote", f"M{n}", "--tag", tag, "--deployed", "no" if n == 3 else "yes")
        else:
            t("promote", f"M{n}", "--no-tag", "--deployed", "yes")
    data = we.collect(repo, t.db)
    rows = {r["id"]: r for r in data["done"]}
    want = {p["item_id"]: p["tag"] for p in t.sql("SELECT pi.item_id, p.tag FROM promotion p JOIN promotion_item pi "
                                                    "ON pi.promotion_id = p.id")}
    assert {i: r["tag"] for i, r in rows.items()} == want == {"M1": "item-M1", "M2": None, "M3": "item-M3"}
    assert rows["M3"]["tag_only"] is True and rows["M3"]["deployed"] == "no"
    assert [r["id"] for r in data["done"]] == ["M3", "M2", "M1"]
    assert disagreements(data, t.text("resume")) == []


def test_a_queued_item_with_no_criteria_path_falls_back(we, t, repo):
    t("add", "--title", "no file yet")
    t.sql("SELECT 1")
    c = sqlite3.connect(t.db)
    c.execute("UPDATE item SET criteria = NULL WHERE id = 'M1'")
    c.commit()
    c.close()
    t("note", "M1", "--next", "write the criteria")         # a write: the dump follows
    it = {i["id"]: i for i in we.collect(repo, t.db)["items"]}["M1"]
    assert (it["handoff"], it["source"], it["next_step"]) == (None, None, "write the criteria")
    commit(repo, {"docs/sprint/M1-criteria.md": CRIT.format(n=1)})
    it = {i["id"]: i for i in we.collect(repo, t.db)["items"]}["M1"]
    assert it["handoff"] == "thing 1 is made and shown."           # docs/sprint/<id>-criteria.md


def test_an_agent_no_active_item_owns_is_other(we, t, repo, fake_herdr):
    # Since M32 step 2 an agent of a queued or held item is on that item's
    # row (M34-e2e note 8), not under other_agents.
    active_item(t, repo)
    t("add", "--title", "queued")
    t("add", "--title", "held")
    t("hold", "M3", "--why", "spring")
    t("add", "--title", "promoted soon")
    t("drop", "M4", "--why", "no")
    for name in ("m1-build", "m2-test", "m3-review", "m4-fix1", "m9-fix1", "investigator"):
        fake_herdr.add_agent(name, "working", repo)
    data = we.collect(repo, t.db)
    items = {i["id"]: i for i in data["items"]}
    assert [a["name"] for a in items["M1"]["agents"]] == ["m1-build"]
    assert [(a["name"], a["role"]) for a in items["M2"]["agents"]] == [("m2-test", "test")]
    assert [a["name"] for a in items["M3"]["agents"]] == ["m3-review"]
    assert items["M4"]["agents"] == []
    assert {a["name"]: a["why"] for a in data["other_agents"]} == {
        "m4-fix1": "its item is dropped", "m9-fix1": "no item of that name",
        "investigator": "name is not <item>-<role>"}


# ═══ criterion 2: the schema-1 reader against this one ═══════════════════════

RUN_KEYS = {"schema", "ts", "made_at", "run_s"}
EMPTIED = {"heading": None, "heading_adds": False, "for_drew": 0, "for_drew_total": 0, "kind": None,
           "tag_written": None, "entry": None}


def _instant(s):
    from datetime import datetime
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def differences(old: dict, new: dict) -> list[tuple[str, str]]:
    """(key, class) for every difference between collect_legacy()'s file
    (schema 1) and collect()'s (schema 2) over the same commit and herdr
    answer. A difference no class covers is "UNCLASSIFIED"."""
    out: list = []

    def add(key, cls):
        out.append((key, cls))

    def keys(o, n, where, known):
        for k in sorted(set(o) | set(n)):
            if k not in n:
                add(f"{where}.{k}", "GONE")
            elif k not in o:
                add(f"{where}.{k}", "new key")
            elif k not in known and o[k] != n[k]:
                add(f"{where}.{k}", "UNCLASSIFIED")

    keys(old, new, "", {"items", "done", "under_way", "not_read", "backlog", *RUN_KEYS})
    for k in RUN_KEYS & set(old) & set(new):
        if old[k] != new[k]:
            add(k, "the run (schema, time)")
    if old["under_way"] != new["under_way"]:
        add("under_way", "fixed: under way is the active items")
    if old["backlog"] != new["backlog"]:
        add("backlog", "fixed: an entry follows its item under way")
    gone_nr = [x for x in old["not_read"] if x not in new["not_read"]]
    if gone_nr:
        add("not_read", "not_read: the step-file lines end")
    if [x for x in new["not_read"] if x not in old["not_read"]]:
        add("not_read", "UNCLASSIFIED")

    oi = {i["id"]: i for i in old["items"]}
    ni = {i["id"]: i for i in new["items"]}
    for i in sorted(set(oi) - set(ni)):
        add(f"items[{i}]", "GONE")
    for i in sorted(set(ni) - set(oi)):
        add(f"items[{i}]", "new: an item the rows hold and no file names")
    for i in sorted(set(oi) & set(ni)):
        o, n = oi[i], ni[i]
        w = f"items[{i}]"
        keys(o, n, w, {"title", "short_title", "steps", "uncommitted", "shipped", "roadmap", "stage"})
        if o["stage"] != n["stage"]:
            # The old reader leaves stage()'s "tag item-<id> exists" as written;
            # work.py's item_stage() fills the id in (its N7). Met once an item
            # has its tag and no PROMOTIONS.md entry (item-M34, PROMOTIONS.md
            # frozen since M34): found by M32 step 1's build.
            filled = {**o["stage"], "detail": (o["stage"].get("detail") or "").replace("item-<id>", f"item-{i}")}
            add(f"{w}.stage", "fixed: the tag detail names its item" if filled == n["stage"] else "UNCLASSIFIED")
        if (o["title"], o.get("short_title")) != (n["title"], n.get("short_title")):
            add(f"{w}.title", "a title (the row's, not the file's heading)")
        if [{k: v for k, v in s.items() if k != "adds"} for s in o["shipped"]] != \
                [{k: v for k, v in s.items() if k != "adds"} for s in n["shipped"]]:
            add(f"{w}.shipped", "UNCLASSIFIED")
        elif o["shipped"] != n["shipped"]:
            add(f"{w}.shipped.adds", "a title (the row's, not the file's heading)")
        if o["roadmap"] != n["roadmap"]:
            add(f"{w}.roadmap", "fixed: an entry follows its item under way")
        if o["uncommitted"]:
            add(f"{w}.uncommitted", "a key with no source left")
        of = {s["file"]: s for s in o["steps"] + o["uncommitted"]}
        nf = {s["file"]: s for s in n["steps"]}
        for f in sorted(set(of) - set(nf)):
            add(f"{w}.steps[{f}]", "a -promote.md step (a promotion's checks)" if f.endswith("-promote.md")
                else "GONE")
        for f in sorted(set(nf) - set(of), key=str):
            add(f"{w}.steps[{f}]", "new: a step the rows hold and no committed file names")
        if [s["file"] for s in o["steps"] if s["file"] in nf] != [s["file"] for s in n["steps"] if s["file"] in of]:
            add(f"{w}.steps", "step order (by the step's time, not the file's first commit)")
        for f in sorted(set(of) & set(nf)):
            so, sn = of[f], nf[f]
            ws = f"{w}.steps[{f}]"
            keys(so, sn, ws, {"committed_at", "title", "verdicts", "commit", "kind", "n"})
            if (so["kind"], so["n"]) != (sn["kind"], sn["n"]):
                add(f"{ws}.kind", "fixed: the row's kind and round, where the file name was no known step"
                    if so["kind"] == "other" and so["name"] == f"{sn['kind']}{sn['round']}" else "UNCLASSIFIED")
            if so["committed_at"] != sn["committed_at"]:
                add(f"{ws}.committed_at", "a time's format (UTC)" if _instant(so["committed_at"]) ==
                    _instant(sn["committed_at"]) else "a time's source (the step's end, not the file's first commit)")
            if so["title"] != sn["title"]:
                add(f"{ws}.title", "a key with no source left")
            if so["commit"] != sn["commit"]:
                add(f"{ws}.commit", "a commit (the report's last, not its first)")
            if so["verdicts"] != sn["verdicts"]:
                ov = [v for v in so["verdicts"] or [] if v["subject"] == "item"] or (so["verdicts"] or [])[:1]
                nv = sn["verdicts"] or []
                same = [v["verdict"] for v in ov[:1]] == [v["verdict"] for v in nv]
                add(f"{ws}.verdicts", "a key with no source left (the verdict's line and form; a second subject)"
                    if same else "UNCLASSIFIED")

    def rows(x):
        by: dict = {}
        for r in x["done"]:
            by.setdefault(r["id"], []).append(r)
        return by
    od, nd = rows(old), rows(new)
    if sorted(od) != sorted(nd) or any(len(od[i]) != len(nd[i]) for i in od):
        add("done", "UNCLASSIFIED")
    elif [r["id"] for r in old["done"]] != [r["id"] for r in new["done"]]:
        add("done", "row order within one promotion (by id, not as the label was written)")
    for i in sorted(set(od) & set(nd)):
        for k, (ro, rn) in enumerate(zip(od[i], nd[i])):
            w = f"done[{i}#{k}]"
            keys(ro, rn, w, {*EMPTIED, "date", "tag", "tag_in_git", "tag_only", "line", "notes"})
            for key, empty in EMPTIED.items():
                if ro[key] != rn[key]:
                    add(f"{w}.{key}", "a key with no source left" if rn[key] == empty else "UNCLASSIFIED")
            if ro["date"] != rn["date"]:
                add(f"{w}.date", "a time's format and source (the house's day of the row's time)")
            if (ro["tag"], ro["tag_in_git"]) != (rn["tag"], rn["tag_in_git"]):
                add(f"{w}.tag", "fixed: a tag the parser missed" if ro["tag"] is None and rn["tag"] else
                    "UNCLASSIFIED")
            if ro["tag_only"] != rn["tag_only"]:
                add(f"{w}.tag_only", "fixed: deployed as the row has it")
            if ro["line"] != rn["line"]:
                add(f"{w}.line", "a key with no source left (checks in a -promote.md file)" if rn["line"] is None
                    else "UNCLASSIFIED")
            if [n_.get("date") for n_ in ro["notes"]] != [n_.get("date") for n_ in rn["notes"]] and \
                    len(ro["notes"]) != len(rn["notes"]):
                add(f"{w}.notes", "UNCLASSIFIED")
            elif ro["notes"] != rn["notes"]:
                add(f"{w}.notes", "a key with no source left (a follow-up's heading)")
    return out


def classes(diffs) -> dict:
    by: dict = {}
    for key, cls in diffs:
        by.setdefault(cls, []).append(key)
    return by


def test_criterion_2_every_difference_from_the_old_reader_has_a_class(we, tmp_path, monkeypatch):
    """Over the same commit (HEAD of this repo), herdr off: the old reader
    of the files, and this exporter reading a database imported from the
    same files. Every difference falls in a named class."""
    if not (REPO / ".git").exists():
        pytest.skip("not a git working tree")
    monkeypatch.setenv("PATH", BASE_PATH)
    db = tmp_path / "work.db"
    subprocess.run([sys.executable, "-B", str(WORK_TOOL), "--db", str(db), "--repo", str(REPO), "import-legacy",
                    "--at", "HEAD"], check=True, capture_output=True, timeout=60)
    old, new = we.collect_legacy(REPO), we.collect(REPO, db)
    by = classes(differences(old, new))
    assert "UNCLASSIFIED" not in by and "GONE" not in by, {k: by[k][:5] for k in ("UNCLASSIFIED", "GONE") if k in by}
    assert old["schema"] == 1 and new["schema"] == 4          # 4 since M32 step 2
    assert set(new) >= set(old)


# ═══ criterion 5: no lock, nothing beside the dump, fast, never in the way ══

def test_the_exporter_takes_no_write_lock_and_opens_only_the_dump(we, t, repo):
    active_item(t, repo)
    for p in (Path(f"{t.db}-wal"), Path(f"{t.db}-shm")):
        p.unlink(missing_ok=True)
    before = {p.name: p.stat().st_mtime_ns for p in t.db.parent.iterdir()}
    c = sqlite3.connect(t.db, timeout=0)                    # a writer holding the lock throughout
    c.execute("BEGIN EXCLUSIVE")
    try:
        data = we.collect(repo, t.db)
    finally:
        c.rollback()
        c.close()
    assert data["database"]["state"] == "ok" and data["under_way"] == ["M1"]
    after = {p.name: p.stat().st_mtime_ns for p in t.db.parent.iterdir() if not p.name.endswith(("-wal", "-shm"))}
    assert after == {k: v for k, v in before.items() if not k.endswith(("-wal", "-shm"))}


def test_git_reads_are_three_lock_free_commands(we, t, repo, monkeypatch):
    active_item(t, repo)
    calls = []
    real = we.run

    def spy(cmd, cwd, timeout, env=None):
        calls.append((cmd, env))
        return real(cmd, cwd, timeout, env)
    monkeypatch.setattr(we, "run", spy)
    we.collect(repo, t.db)
    gits = [c for c in calls if c[0][0] == "git"]
    assert {c[0][2] for c in gits} == {"rev-parse", "for-each-ref", "rev-list"}
    for cmd, env in gits:
        assert cmd[1] == "--no-optional-locks" and env["GIT_OPTIONAL_LOCKS"] == "0"


def _export(repo, db, out, path=BASE_PATH, check=True):
    return subprocess.run([sys.executable, "-B", str(EXPORT), "--repo", str(repo), "--db", str(db), "--out", str(out)],
                          env=dict(os.environ, PATH=path), capture_output=True, text=True, timeout=30, check=check)


def test_twenty_exports_beside_fifty_writes_delay_no_write_by_50_ms(t, repo, tmp_path):
    active_item(t, repo)
    for n in range(2, 8):
        t("add", "--title", f"queued {n}")

    def write(n):
        t0 = time.monotonic()
        t("note", "M1", "--next", f"step {n}")
        return time.monotonic() - t0
    base = [write(n) for n in range(10)]
    procs, took = [], []
    for n in range(50):
        if n % 5 < 2 and len(procs) < 20:                   # twenty exports spread over the writes
            procs.append(subprocess.Popen([sys.executable, "-B", str(EXPORT), "--repo", str(repo), "--db",
                                           str(t.db), "--out", str(tmp_path / f"out{len(procs)}.json")],
                                          env=dict(os.environ, PATH=BASE_PATH), stdout=subprocess.DEVNULL,
                                          stderr=subprocess.PIPE))
        took.append(write(100 + n))
    for p in procs:
        assert p.wait(30) == 0, p.stderr.read()
    assert len(procs) == 20
    assert max(took) <= max(base) + 0.05, (sorted(took)[-3:], max(base))
    for n in range(20):
        assert json.loads((tmp_path / f"out{n}.json").read_text())["database"]["state"] == "ok"


def test_two_exports_at_once_leave_one_whole_file(t, repo, tmp_path):
    active_item(t, repo)
    out = tmp_path / "out" / "work.json"
    for _ in range(5):
        ps = [subprocess.Popen([sys.executable, "-B", str(EXPORT), "--repo", str(repo), "--db", str(t.db),
                                "--out", str(out)], env=dict(os.environ, PATH=BASE_PATH)) for _ in range(2)]
        assert [p.wait(30) for p in ps] == [0, 0]
        assert json.loads(out.read_text())["under_way"] == ["M1"]
        assert os.listdir(out.parent) == ["work.json"]


def test_a_run_on_the_real_tree_takes_under_half_a_second(we, tmp_path, monkeypatch):
    if not (REPO / ".git").exists():
        pytest.skip("not a git working tree")
    monkeypatch.setenv("PATH", BASE_PATH)
    db = tmp_path / "work.db"
    subprocess.run([sys.executable, "-B", str(WORK_TOOL), "--db", str(db), "--repo", str(REPO), "import-legacy",
                    "--at", "HEAD"], check=True, capture_output=True, timeout=60)
    best = min(we.collect(REPO, db)["run_s"] for _ in range(3))
    assert best < 0.5


# ═══ criterion 6: exactly the allowed columns reach the file ════════════════

# Free text: every TEXT column but the ids, the keys and the columns a CHECK
# holds to a list of words. A column added to the schema fails the first
# assertion until it is put in one of the two sets below.
NOT_FREE = {("item", "id"), ("item", "state"), ("item", "after_item"), ("item", "waits_on"),
            ("step", "item_id"), ("step", "kind"), ("step", "by"), ("step", "state"), ("step", "verdict"),
            ("step", "graded"), ("promotion", "kind"), ("promotion_item", "item_id"),
            ("open_thing", "kind"), ("open_thing", "item_id"), ("open_thing", "owner"), ("meta", "key")}
# Allowed into work.json (plumbing 1 to 4), and for which rows.
ALLOWED = {
    ("item", "title"): "any", ("item", "next_step"): "live", ("item", "waits_note"): "live",
    ("item", "held_why"): "held",
    ("step", "name"): "any", ("step", "agent"): "any", ("step", "report"): "any", ("step", "started_at"): "any",
    ("step", "ended_at"): "any",
    ("promotion", "tag"): "any", ("promotion", "at"): "any", ("promotion", "verified"): "any",
    ("promotion", "note"): "no item",
    ("open_thing", "opened_at"): "owed",
    # M32 step 2 (plumbing 6; Drew's #22, 2026-11-26: show all of it on /work):
    # an open leftover's or decision's text, on its item's row or as a
    # decision owed by Drew; a dropped item's end time. Its reason stays out.
    ("open_thing", "text"): "open matter", ("item", "ended_at"): "dropped",
}
NEVER = {("item", "criteria"), ("item", "ended_why"), ("item", "created_at"),
         ("step", "model"), ("step", "note"), ("step", "commit_sha"),
         ("promotion", "commit_sha"), ("promotion", "untested"),
         ("open_thing", "source"), ("open_thing", "closed_at"), ("open_thing", "closed_how"),
         ("meta", "value"), ("log", "at"), ("log", "who"), ("log", "cmd"), ("log", "changes")}


def test_criterion_6_a_marker_in_every_text_column(we, t, repo, fake_herdr):
    # Rows in every state, so every free column is set somewhere.
    active_item(t, repo, 1)
    fake_herdr.add_agent("m1-build", "working", repo)
    t("step", "start", "M1", "build", "--agent", "m1-build", "--model", "claude-opus-5-5")
    t("step", "start", "M1", "review", "--agent", "m1-review")
    t("step", "stop", "M1", "review", "--why", "stopped by hand")
    commit(repo, {"docs/sprint/M1-e2e.md": "Verdict: FAILS\n"})
    t("step", "add", "M1", "test")
    t("grade", "M1", "fail")
    commit(repo, {"docs/sprint/M1-audit.md": "# audit\n"})
    t("step", "add", "M1", "other", "--name", "audit")
    t("note", "M1", "--waits", "drew", "--waits-note", "his go", "--next", "fix it")
    active_item(t, repo, 2)
    git(repo, "tag", "item-M2")
    commit(repo, {"docs/sprint/M2-promote.md": "x\n"})
    t("note", "M2", "--next", "promote it")
    t("promote", "M2", "--tag", "item-M2", "--deployed", "yes", "--untested", "nothing")
    t("add", "--title", "queued", "--criteria", "docs/sprint/M3-criteria.md")
    t("note", "M3", "--next", "review", "--waits", "outside", "--waits-note", "a release")
    t("add", "--title", "held")
    t("hold", "M4", "--why", "spring")
    t("add", "--title", "dropped")
    t("drop", "M5", "--why", "no")
    t("matter", "add", "decision", "--owner", "drew", "--item", "M1", "--text", "ship?", "--source", "s", "--blocks")
    t("matter", "add", "decision", "--owner", "coordinator", "--text", "mine?", "--source", "s")
    gone = t.one("matter", "add", "decision", "--owner", "drew", "--text", "asked and answered")
    t("matter", "close", gone["matter"], "--how", "yes")
    t("matter", "add", "leftover", "--owner", "drew", "--item", "M2", "--text", "a weakness", "--source", "s")
    t("matter", "add", "escape", "--owner", "coordinator", "--item", "M2", "--text", "it escaped")
    c = sqlite3.connect(t.db)
    c.execute("INSERT INTO promotion (kind, tag, at, deployed, verified, note, untested) VALUES "
              "('promotion', 'chat-buttons', '2026-11-25T03:19:00Z', 1, 'docs/sprint/PROMOTIONS.md', 'buttons', 'u')")
    c.commit()
    # Plant a distinct marker in every free text column of every row that has a value.
    free = set()
    planted: dict = {}
    for (table,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})") if r[2].upper() == "TEXT"]
        for col in cols:
            if (table, col) in NOT_FREE:
                continue
            free.add((table, col))
            if table == "log":
                continue
            key = "rowid" if table not in ("meta",) else "key"
            for rid, val in c.execute(f"SELECT {key}, {col} FROM {table}").fetchall():
                if val is None or (table, col, rid) in (("meta", "value", "repo"), ("meta", "value", "schema_version")):
                    continue
                mark = f"QX{len(planted):03d}{table[:3]}{col[:4]}XQ"
                c.execute(f"UPDATE {table} SET {col} = ? WHERE {key} = ?", (mark, rid))
                planted[mark] = (table, col, rid)
    for col in ("at", "who", "cmd", "changes"):
        mark = f"QX{len(planted):03d}log{col[:4]}XQ"
        planted[mark] = ("log", col, None)
    marks = {v[1]: k for k, v in planted.items() if v[0] == "log"}
    c.execute("INSERT INTO log (at, who, cmd, changes) VALUES (?,?,?,?)",
              (marks["at"], marks["who"], marks["cmd"], marks["changes"]))
    c.commit()
    assert free == set(ALLOWED) | NEVER
    assert {(tb, col) for tb, col, _ in planted.values()} == free           # every free column planted
    state = {r[0]: r[1] for r in c.execute("SELECT id, state FROM item")}
    owed = {r[0] for r in c.execute("SELECT id FROM open_thing WHERE kind = 'decision' AND owner = 'drew' "
                                    "AND closed_at IS NULL")}
    no_item = {r[0] for r in c.execute("SELECT id FROM promotion WHERE id NOT IN (SELECT promotion_id FROM "
                                       "promotion_item)")}
    shown = {r[0] for r in c.execute("SELECT id FROM open_thing WHERE closed_at IS NULL AND kind IN "
                                     "('leftover', 'decision') AND (item_id IS NOT NULL OR "
                                     "(kind = 'decision' AND owner = 'drew'))")}
    rid_of = {}
    for tb in ("item",):
        rid_of[tb] = {r[0]: r[1] for r in c.execute("SELECT rowid, id FROM item")}
    c.close()
    t("dump")
    text = json.dumps(we.collect(repo, t.db), ensure_ascii=False)
    found = {m for m in planted if m in text}

    def allowed(table, col, rid):
        rule = ALLOWED.get((table, col))
        if rule is None:
            return False
        if rule == "live":
            return state[rid_of["item"][rid]] in ("active", "queued", "held")
        if rule == "held":
            return state[rid_of["item"][rid]] == "held"
        if rule == "no item":
            return rid in no_item
        if rule == "owed":
            return rid in owed
        if rule == "dropped":
            return state[rid_of["item"][rid]] == "dropped"
        if rule == "open matter":
            return rid in shown
        return True
    want = {m for m, (tb, col, rid) in planted.items() if allowed(tb, col, rid)}
    assert found == want, {"leaked": sorted(planted[m] for m in found - want),
                           "missing": sorted(planted[m] for m in want - found)}
    for word in ("ship?", "mine?", "a weakness", "it escaped", "asked and answered"):
        assert word not in text
    # The rows behind the rule, so a change of the test data cannot empty it:
    # Drew's decision on M1 and the leftover on M2 are shown; the
    # coordinator's decision with no item, the closed one and the escape not.
    assert len(shown) == 2 and "dropped" in state.values()


# ═══ criterion 8: a dump the exporter cannot use ═══════════════════════════

def _dump_text(db: Path) -> str:
    c = sqlite3.connect(db)
    try:
        return "\n".join(c.iterdump()) + "\n"
    finally:
        c.close()


@pytest.mark.parametrize("case,state", [("missing", "missing"), ("empty", "empty"), ("not sql", "corrupt"),
                                        ("cut", "corrupt"), ("no tables", "corrupt"), ("not text", "corrupt"),
                                        ("newer", "newer"), ("other repo", "other repo")])
def test_criterion_8_a_dump_that_cannot_be_used(t, repo, tmp_path, case, state):
    active_item(t, repo)
    commit(repo, {"ROADMAP.md": "# Roadmap\n\n## Now\n\n- **Still shown** *(work item M7)* text.\n"})
    dump = Path(f"{t.db}.dump.sql")
    text = dump.read_text()
    if case == "missing":
        dump.unlink()
    elif case == "empty":
        dump.write_text("\n")
    elif case == "not sql":
        dump.write_text("this is not SQL at all;\n")
    elif case == "cut":
        dump.write_text(text[: text.rindex("COMMIT")])
    elif case == "no tables":
        dump.write_text("BEGIN TRANSACTION;\nCREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n"
                        "INSERT INTO meta VALUES('schema_version','1');\nCOMMIT;\n")
    elif case == "not text":
        dump.write_bytes(b"\xff\xfe\x00 binary")
    elif case == "newer":
        c = sqlite3.connect(t.db)
        c.execute("UPDATE meta SET value = '9' WHERE key = 'schema_version'")
        c.commit()
        c.close()
        dump.write_text(_dump_text(t.db))
    elif case == "other repo":
        c = sqlite3.connect(t.db)
        c.execute("UPDATE meta SET value = ? WHERE key = 'repo'", (str(tmp_path / "otherplace-qz"),))
        c.commit()
        c.close()
        dump.write_text(_dump_text(t.db))
    out = tmp_path / "out.json"
    r = _export(repo, t.db, out)
    assert r.returncode == 0 and r.stderr == ""
    raw = out.read_text()
    data = json.loads(raw)
    d = data["database"]
    assert d["state"] == state and d["says"] and d["says"] in " ".join(data["errors"])
    assert data["items"] == data["under_way"] == data["queue"] == data["done"] == data["promotions_no_item"] == []
    assert data["decisions_owed"] == [] and data["escapes"] == {"window": [], "count": 0}
    assert [e["title"] for s in data["backlog"] for e in s["entries"]] == ["Still shown"]
    assert str(t.db.parent) not in raw and "otherplace-qz" not in raw and "sqlite" not in raw.lower()
    if case != "missing":
        assert d["dump_age_s"] is not None and d["dump_at"]


def test_an_ok_dump_says_so_with_its_age(we, t, repo):
    active_item(t, repo)
    old = time.time() - 120
    os.utime(f"{t.db}.dump.sql", (old, old))
    d = we.collect(repo, t.db)["database"]
    assert d["state"] == "ok" and d["schema"] == 1 and 119 <= d["dump_age_s"] <= 130
    assert set(d) == {"state", "says", "schema", "dump_at", "dump_age_s"}


# ═══ criterion 10: the suite reads neither the live database nor its dump ═══

def test_the_default_database_in_the_suite_is_the_scratch_one(we, repo):
    assert os.environ["KRONK_WORK_DB"] and not os.environ["KRONK_WORK_DB"].startswith(str(LIVE_DIR))
    data = we.collect(repo)
    assert data["database"]["state"] == "missing"


@pytest.mark.skipif(not shutil.which("strace"), reason="strace not installed")
def test_a_run_with_the_default_database_never_opens_the_live_directory(repo, tmp_path):
    trace = tmp_path / "trace.txt"
    r = subprocess.run(["strace", "-f", "-qq", "-o", str(trace), "-e", "trace=openat,open,stat,newfstatat,access",
                        sys.executable, "-B", str(EXPORT), "--repo", str(repo), "--print"],
                       env=dict(os.environ, PATH=BASE_PATH), capture_output=True, text=True, timeout=30)
    if r.returncode != 0 and "ptrace" in (r.stderr or ""):
        pytest.skip("ptrace not permitted here")
    assert r.returncode == 0, r.stderr
    assert str(LIVE_DIR) not in trace.read_text()



# ═══ plumbing 7: a promotion with no item ═══════════════════════════════════

def test_promote_with_no_item_takes_a_label_the_checks_and_a_time(we, t, repo, fake_herdr):
    active_item(t, repo)
    git(repo, "tag", "chat-buttons-2026-11-25")
    commit(repo, {"docs/sprint/PROMOTIONS.md": "# Promotions\n"})
    base = ["promote", "--tag", "chat-buttons-2026-11-25", "--deployed", "yes"]
    t(*base, "--verified", "docs/sprint/PROMOTIONS.md", rc=1)
    assert "give the items promoted, or --label" in t.last[2]
    t(*base, "--label", "Chat page buttons", rc=1)
    assert "needs --verified PATH" in t.last[2]
    t(*base, "--label", "Chat page buttons", "--verified", "docs/sprint/PROMOTIONS.md", "--followup", rc=1)
    t("promote", "M1", "--no-tag", "--deployed", "yes", "--label", "x", "--verified", "docs/sprint/PROMOTIONS.md",
      rc=1)
    assert "--label is for a promotion with no item" in t.last[2]
    t(*base, "--label", "two\nlines", "--verified", "docs/sprint/PROMOTIONS.md", rc=1)
    t("promote", "--tag", "no-such-tag", "--deployed", "yes", "--label", "x", "--verified",
      "docs/sprint/PROMOTIONS.md", rc=1)
    assert "git lists no tag no-such-tag" in t.last[2]
    r = t.one(*base, "--label", "Chat page buttons", "--verified", "docs/sprint/PROMOTIONS.md",
              "--at", "2026-11-25T22:19:00-05:00")
    assert (r["items"], r["label"], r["at"], r["deployed"]) == ([], "Chat page buttons", "2026-11-26T03:19:00Z", True)
    (p,) = t.sql("SELECT * FROM promotion")
    assert (p["note"], p["at"], p["verified"], p["tag"], p["kind"]) == (
        "Chat page buttons", "2026-11-26T03:19:00Z", "docs/sprint/PROMOTIONS.md", "chat-buttons-2026-11-25",
        "promotion")
    assert t.sql("SELECT * FROM promotion_item") == []
    assert {it["state"] for it in t.sql("SELECT state FROM item")} == {"active"}
    assert not [f for f in codes_all(t) if f["check"] in ("tag-not-promoted", "tag-gone", "no-shipped-line")]
    data = we.collect(repo, t.db)
    assert data["done"] == [] and data["under_way"] == ["M1"]
    (row,) = data["promotions_no_item"]
    assert (row["tag"], row["tag_in_git"], row["label"], row["at"], row["date"], row["deployed"], row["verified"]) == (
        "chat-buttons-2026-11-25", True, "Chat page buttons", "2026-11-26T03:19:00Z",
        we.work_tool().local_day("2026-11-26T03:19:00Z"), "yes", "docs/sprint/PROMOTIONS.md")
    assert disagreements(data, t.text("resume")) == []


def test_an_items_promotion_takes_at_too(t, repo):
    active_item(t, repo)
    commit(repo, {"docs/sprint/M1-promote.md": "x\n"})
    r = t.one("promote", "M1", "--no-tag", "--deployed", "no", "--at", "2026-11-20T12:00:00Z")
    assert r["at"] == "2026-11-20T12:00:00Z" and "label" not in r
    assert t.sql("SELECT ended_at FROM item WHERE id = 'M1'")[0]["ended_at"] == "2026-11-20T12:00:00Z"


# ═══ criterion 7 and plumbing 9: the route and the page ═════════════════════

PAGE_PATH = REPO / "orchestrator" / "static" / "work.html"
BUILD_BASE = "ef298a9"              # the last commit before the M34 build


def test_criterion_7_the_route_knows_schema_2_and_reads_no_database(tmp_path):
    import ast
    from unittest.mock import patch

    import work_api
    src = (REPO / "orchestrator" / "work_api.py").read_text()
    tree = ast.parse(src)
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in n.names} | {n.module.split(".")[0] for n in ast.walk(tree)
                                     if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"sqlite3", "subprocess", "httpx", "shutil"}
    body = tree.body[1:]                                         # the docstring aside
    consts = [n.value for b in body for n in ast.walk(b) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not [c for c in consts if "dump" in c or ".db" in c or "kronk-work/" in c or "work.db" in c]
    assert work_api.SCHEMA_KNOWN == 4                             # 4 since M32 step 2
    p = tmp_path / "work.json"
    now = 1_800_000_000.0
    for schema, note in ((4, None), (3, None), (2, None), (1, None),
                         (5, "work.json has schema 5, newer than this page's 4: keys added since are not shown")):
        p.write_text(json.dumps({"schema": schema, "ts": now - 5, "items": []}))
        with patch.object(work_api, "WORK_PATH", p), patch.object(work_api, "_cache", {}):
            d = work_api.work_source(now)
        # M32 step 1 added unchanged, page_version and server_s to the envelope.
        assert set(d) == {"source", "schema_known", "work", "unchanged", "page_version", "server_s"}
        assert d["schema_known"] == 4
        assert d["source"]["state"] == "fresh" and d["source"]["schema_note"] == note


def _literal(name):
    from tests.test_m27_work_page import _literal as lit
    return lit(PAGE_PATH.read_text(), name)


def test_the_page_has_a_word_for_every_database_state(we):
    assert set(_literal("DB_WORD")) == set(we.DB_STATES) and _literal("DB_WORD")["ok"] is None
    t_ = _literal("DB_TEXT")
    assert "{word}" in t_["banner"] and "{says}" in t_["banner"] and "{age}" in t_["age"] and "{n}" in t_["owed_many"]
    assert set(_literal("NO_ITEM_TEXT")["deployed"]) == set(we.DEPLOYED_WORD.values())


def test_the_page_reads_the_new_keys_and_sets_them_as_text():
    script = PAGE_PATH.read_text().split("<script>", 1)[1].split("// ── Heard (work item M30 step C)", 1)[0]
    for needle in ("w.active ?? w.under_way", "w.queue", "w.promotions_no_item", "w.decisions_owed", "w.database",
                   "it.blocked_by", "it.held_why", "it.waits_on", "it.next_step", "s.state", "s.graded", "e.label"):
        assert needle in script, needle
    assert not re.search(r"\.(innerHTML|outerHTML|insertAdjacentHTML)\b|document\.write", script)
    # A next step, a wait note, a hold and what holds an item go in as plain text, never through mdText().
    for needle in ("el('span', 'notes', fill(QUEUE_TEXT.next_step", "el('span', 'held-mark', fill(QUEUE_TEXT.held",
                   "el('span', null, it.blocked_by)"):
        assert needle in script, needle
    assert "PROMOTIONS.md</code>), newest entry first; dates as written there" not in PAGE_PATH.read_text()


def test_heard_is_untouched_since_before_the_build():
    old = subprocess.run(["git", "-C", str(REPO), "show", f"{BUILD_BASE}:orchestrator/static/work.html"],
                         capture_output=True, text=True)
    if old.returncode != 0:
        pytest.skip(f"{BUILD_BASE} is not here")

    def heard(s):
        css = s[s.index("  /* Heard (M30 step C)"):s.index("</style>")]
        start = s.index('  <details class="heard" id="heard">')
        html = s[start:s.index("  </details>", start)]
        return css, html, s[s.index("// ── Heard (work item M30 step C)"):]
    # M32 step 1 changed "Heard"'s script (the page's scheduler, the time
    # limit, the trim) and its route (no-store), by order: its style and
    # its markup are still M34's base's (tests/test_m32_live.py pins the rest).
    # M32 step 3 (contrast, plumbing 5) moved its state off --text3 onto
    # --text2 and changed nothing else (tests/test_m32_step3.py).
    def by_colour(parts):
        return tuple(x.replace("var(--text3)", "var(--text2)") for x in parts)
    assert by_colour(heard(PAGE_PATH.read_text())[:2]) == by_colour(heard(old.stdout)[:2])
