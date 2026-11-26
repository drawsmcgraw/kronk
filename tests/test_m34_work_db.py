"""/work drawn from the work-state database (work item M34,
docs/sprint/M34-criteria.md, "Revised after the review").

Step one: scripts/work.py gains overview(), one read-only function that
`resume` prints from and the exporter serialises (plumbing 5). These pin
what it returns on rows made by the tool itself.

Repos and databases are temporary (the M33 tests' fixtures); herdr is the
M28 tests' fake. No test names the live database or its dump.
"""
import pytest

from tests.test_m28_agent_tool import commit
from tests.test_m33_work_state import (  # noqa: F401  (fixtures)
    Tool, active_item, db, fake_herdr, quiet_env, repo, t, wk)

pytestmark = pytest.mark.usefixtures("no_network")


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
