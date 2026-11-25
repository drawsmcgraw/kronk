"""scripts/work.py, work-item state in one SQLite database (work item M33
step 1, docs/sprint/M33-criteria.md, "Revised after the review").

These pin: the schema and where the file lives (plumbing 1, 2), one
writer in BEGIN IMMEDIATE with a log row per command (3, criterion 4),
evidence copied from git and never typed (4), the queue commands (5),
every row of the review's Appendix C named by `check` and each repaired by
one command (6, criterion 6), what `resume` prints and in what order (7),
the dump and `restore-dump` (9, criterion 8), the import from a named
commit (10, criterion 2), a database that is missing, empty, corrupt,
locked or newer (criterion 5), and a full life of a made-up item through
the tools, agent.py with a fake herdr included (criterion 3).

Repos are temporary git repositories (user set per repo). herdr is the M28
tests' fake on PATH. No socket is opened. No test names the live database:
conftest points KRONK_WORK_DB at a temporary path for the whole run, and
fails the run if the live file changed.
"""
import contextlib
import io
import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.test_m28_agent_tool import FAKE, Herdr, _load, commit, git

pytestmark = pytest.mark.usefixtures("no_network")

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "work.py"
BASE_PATH = "/usr/bin:/bin"
GUARD = "## Guard rails in every brief\n\n- Rail one.\n"
WORKFLOW = "# Workflow\n\n" + GUARD + "\n## Git\n\nx\n"
ROADMAP = """# Roadmap

## Now

- **Something open** *(work item M1)* text.

## Shipped

- **Old thing** *(2026-11-01, work item M0)*
"""
CRIT = """# M{n} pass criteria: thing number {n}

Source: Drew, 2026-11-24: "make thing {n}"
and keep it small.

Handoff line: thing {n} is made and
shown.

## Criteria

1. It works.
"""


@pytest.fixture
def wk():
    return _load("m33_work_tool", SCRIPT)


@pytest.fixture(autouse=True)
def quiet_env(tmp_path, monkeypatch):
    """No real herdr (PATH has only /usr/bin:/bin), no real agent state."""
    monkeypatch.setenv("PATH", BASE_PATH)
    monkeypatch.setenv("KRONK_AGENT_STATE", str(tmp_path / "agents"))
    monkeypatch.setenv("KRONK_WORK_WHO", "test-session")


@pytest.fixture
def repo(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    git(tree, "init", "-q", "-b", "main")
    git(tree, "config", "user.name", "Fixture")
    git(tree, "config", "user.email", "fixture@example.invalid")
    commit(tree, {"docs/sprint/WORKFLOW.md": WORKFLOW, "ROADMAP.md": ROADMAP, "README.md": "x\n"}, "base")
    return tree


class Tool:
    def __init__(self, wk, db, repo):
        self.wk, self.db, self.repo = wk, Path(db), Path(repo)

    def __call__(self, *argv, rc=0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            got = self.wk.main(["--db", str(self.db), "--repo", str(self.repo), *map(str, argv)], out=out)
        if rc is not None:
            assert got == rc, (argv, got, err.getvalue(), out.getvalue())
        self.last = (got, out.getvalue(), err.getvalue())
        return self.last

    def j(self, *argv, rc=0):
        _, out, _ = self(*argv, rc=rc)
        return [json.loads(x) for x in out.splitlines()]

    def one(self, *argv, rc=0):
        (x,) = self.j(*argv, rc=rc)
        return x

    def text(self, *argv, rc=0):
        return self(*argv, rc=rc)[1]

    def sql(self, q, *args):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in c.execute(q, args)]
        finally:
            c.close()


@pytest.fixture
def db(tmp_path):
    return tmp_path / "state" / "kronk-work" / "work.db"


@pytest.fixture
def t(wk, db, repo):
    tool = Tool(wk, db, repo)
    tool("init")
    return tool


def log_count(t):
    return t.sql("SELECT COUNT(*) AS n FROM log")[0]["n"]


# ── plumbing 1, 2: where, the schema, the file's mode ──────────────────────

def test_default_path_is_under_home_state_not_the_repo(wk, monkeypatch, tmp_path):
    monkeypatch.delenv("KRONK_WORK_DB")
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    assert wk.default_db() == tmp_path / "h" / ".local" / "state" / "kronk-work" / "work.db"
    monkeypatch.setenv("KRONK_WORK_DB", "/x/y.db")
    assert wk.default_db() == Path("/x/y.db")


def test_the_suite_never_points_at_the_live_database():
    live = Path.home() / ".local" / "state" / "kronk-work" / "work.db"
    assert os.environ["KRONK_WORK_DB"] != str(live)
    assert not Path(os.environ["KRONK_WORK_DB"]).exists() or "kronk-work-" in os.environ["KRONK_WORK_DB"]


def test_init_makes_a_private_wal_database_with_the_seven_tables(t, db, repo):
    assert stat.S_IMODE(db.stat().st_mode) == 0o600
    assert stat.S_IMODE(db.parent.stat().st_mode) == 0o700
    names = {r["name"] for r in t.sql("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == {"meta", "item", "step", "promotion", "promotion_item", "open_thing", "log"}
    assert t.sql("PRAGMA journal_mode")[0]["journal_mode"] == "wal"
    meta = {r["key"]: r["value"] for r in t.sql("SELECT * FROM meta")}
    assert meta["schema_version"] == "1" and meta["repo"] == os.path.realpath(repo)
    t("init", rc=1)
    assert "exists; not replaced" in t.last[2]


def test_stage_is_not_a_column(t):
    cols = {r["name"] for r in t.sql("PRAGMA table_info(item)")}
    assert "stage" not in cols and {"state", "position", "after_item", "waits_on", "tier"} <= cols


def test_the_log_is_append_only(t):
    t("add", "--title", "x")
    c = sqlite3.connect(t.db)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        c.execute("UPDATE log SET who = 'me'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        c.execute("DELETE FROM log")
    c.close()


def test_nothing_makes_the_database_but_init_import_and_restore(wk, db, repo):
    tool = Tool(wk, db, repo)
    for argv in (["add", "--title", "x"], ["resume"], ["check"], ["queue"], ["log"]):
        rc, out, err = tool(*argv, rc=2)
        assert err.startswith(f"work.py {argv[0]}: missing: no database at {db}") and out == ""
    assert not db.exists() and not db.parent.exists()


# ── plumbing 3: one log row per writing command, who and what ──────────────

def test_one_log_row_per_writing_command_with_who_and_the_changed_rows(t):
    n = log_count(t)
    t("add", "--title", "first")
    t("queue")
    t("show", "M1")
    rows = t.sql("SELECT * FROM log ORDER BY id")
    assert len(rows) == n + 1
    last = rows[-1]
    assert last["who"] == "test-session" and "add --title first" in last["cmd"]
    ch = json.loads(last["changes"])
    assert ch["items"] == ["M1"] and ch["rows"][0]["t"] == "item" and ch["rows"][0]["new"]["title"] == "first"


def test_who_falls_back_to_the_session_then_the_process(wk, monkeypatch):
    monkeypatch.delenv("KRONK_WORK_WHO")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc-123")
    assert wk.who() == "session abc-123"
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    assert wk.who().startswith(f"pid {os.getpid()} started ")


def test_a_command_that_changes_nothing_writes_no_log_row(t, repo):
    commit(repo, {"docs/sprint/M1-criteria.md": CRIT.format(n=1)})
    t("add", "--title", "x")
    t("start", "M1")
    t("note", "M1", "--next", "build")
    n = log_count(t)
    t("note", "M1", "--next", "build")
    assert log_count(t) == n


def test_log_filters_by_item_and_day(t):
    t("add", "--title", "a")
    t("add", "--title", "b")
    t("note", "M2", "--next", "x")
    assert [x["cmd"].split(" ", 5)[-1] for x in t.j("log", "--item", "M2")] == ["add --title b", "note M2 --next x"]
    day = t.sql("SELECT at FROM log ORDER BY id DESC LIMIT 1")[0]["at"][:10]
    assert len(t.j("log", "--day", day)) == log_count(t)
    assert t.j("log", "--day", "1999-01-01") == []
    assert len(t.j("log", "--limit", "2")) == 2


# ── plumbing 4, 5: add, the queue, hold, drop, after, note ─────────────────

def test_add_allocates_numbers_and_never_reuses_a_dropped_one(t):
    assert t.one("add", "--title", "a")["id"] == "M1"
    assert t.one("add", "--title", "b")["id"] == "M2"
    t("drop", "M2", "--why", "not needed")
    assert t.one("add", "--title", "c")["id"] == "M3"
    t("add", "--id", "M2", "--title", "again", rc=1)
    assert "is used" in t.last[2]
    t("add", "--id", "M1", "--title", "again", rc=1)
    t("add", "--id", "M3-step2", "--title", "split")
    assert t.one("add", "--id", "M9", "--title", "jump")["id"] == "M9"
    assert t.one("add", "--title", "next")["id"] == "M10"


def test_the_queue_is_numbered_and_move_renumbers_1_to_n(t):
    for x in "abcd":
        t("add", "--title", x)
    t("move", "M4", "1")
    assert [(q["position"], q["id"]) for q in t.j("queue")] == [(1, "M4"), (2, "M1"), (3, "M2"), (4, "M3")]
    t("move", "M4", "3")
    assert [q["id"] for q in t.j("queue")] == ["M1", "M2", "M4", "M3"]
    t("move", "M1", "9", rc=1)
    assert "the queue has 1 to 4" in t.last[2]


def test_hold_keeps_its_place_and_next_skips_it(t):
    for x in "abc":
        t("add", "--title", x)
    t("hold", "M1", "--why", "Drew: tell me more")
    q = t.j("queue")
    assert [(x["id"], x["state"], x["next"]) for x in q] == [("M1", "held", False), ("M2", "queued", True),
                                                              ("M3", "queued", False)]
    assert q[0]["held_why"] == "Drew: tell me more"
    t("start", "M1", rc=1)
    assert "is held" in t.last[2]
    t("unhold", "M1")
    assert t.j("queue")[0]["next"] is True


def test_start_moves_queued_to_active_and_closes_the_gap(t):
    for x in "ab":
        t("add", "--title", x)
    t("start", "M1")
    assert t.sql("SELECT state, position FROM item WHERE id='M1'") == [{"state": "active", "position": None}]
    assert [(q["position"], q["id"]) for q in t.j("queue")] == [(1, "M2")]
    t("start", "M1", rc=1)


def test_drop_with_why_clears_the_place(t):
    for x in "abc":
        t("add", "--title", x)
    t("after", "M3", "M2")
    res = t.one("drop", "M2", "--why", "Drew: drop m2")
    assert res["followers_to_review"] == ["M3"]
    row = t.sql("SELECT * FROM item WHERE id='M2'")[0]
    assert row["state"] == "dropped" and row["ended_why"] == "Drew: drop m2" and row["position"] is None
    assert [(q["position"], q["id"]) for q in t.j("queue")] == [(1, "M1"), (2, "M3")]
    t("drop", "M2", "--why", "again", rc=1)


def test_after_blocks_move_and_start_until_the_other_is_promoted(t, repo):
    t("add", "--title", "first")
    t("add", "--title", "second", "--after", "M1")
    t("move", "M2", "1", rc=1)
    assert "M2 follows M1, which is queued, not promoted; not moved" in t.last[2]
    t("start", "M2", rc=1)
    assert "M2 follows M1" in t.last[2]
    q = t.j("queue")
    assert q[1]["blocked_by"] and q[0]["next"]
    t("after", "M1", "M2", rc=1)
    assert "loop" in t.last[2]
    t("after", "M2", "--clear")
    t("move", "M2", "1")


def test_note_waits_on_is_one_of_three_kinds_with_a_note(t):
    t("add", "--title", "x")
    t("note", "M1", "--waits", "drew", rc=1)
    assert "needs --waits-note" in t.last[2]
    t("note", "M1", "--waits", "nobody", "--waits-note", "x", rc=1)
    r = t.one("note", "M1", "--waits", "outside", "--waits-note", "yt-dlp/ejs#80", "--next", "wait", "--tier", "2")
    assert (r["waits_on"], r["waits_note"], r["next_step"], r["tier"]) == ("outside", "yt-dlp/ejs#80", "wait", 2)
    r = t.one("note", "M1", "--waits", "none")
    assert (r["waits_on"], r["waits_note"]) == (None, None)
    t("note", "M1", rc=1)


def test_show_reads_handoff_and_source_from_the_criteria_file_not_a_row(t, repo):
    commit(repo, {"docs/sprint/M1-criteria.md": CRIT.format(n=1)})
    t("add", "--title", "thing")
    s = t.one("show", "M1")
    assert s["handoff"] == "thing 1 is made and shown."
    assert s["source"] == 'Drew, 2026-11-24: "make thing 1" and keep it small.'
    cols = {r["name"] for r in t.sql("PRAGMA table_info(item)")}
    assert not {"what", "why", "drews_words", "handoff", "source"} & cols


# ── plumbing 4: evidence copied, never typed ───────────────────────────────

def active_item(t, repo, n=1):
    commit(repo, {f"docs/sprint/M{n}-criteria.md": CRIT.format(n=n)})
    t("add", "--title", f"thing {n}")
    t("start", f"M{n}")
    t("step", "add", f"M{n}", "criteria")


def test_step_add_copies_the_commit_and_refuses_an_uncommitted_file(t, repo):
    t("add", "--title", "x")
    t("step", "add", "M1", "criteria", rc=1)
    assert "is not committed in HEAD" in t.last[2]
    commit(repo, {"docs/sprint/M1-criteria.md": CRIT.format(n=1)})
    r = t.one("step", "add", "M1", "criteria")
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    row = t.sql("SELECT * FROM step")[0]
    assert row["commit_sha"] == head and r["commit"] == head[:7] and row["by"] == "coordinator"
    assert row["state"] == "finished" and row["started_at"] is None and row["report"] == "docs/sprint/M1-criteria.md"


def test_a_tests_verdict_is_read_from_the_report_and_there_is_no_verdict_option(t, repo, wk):
    active_item(t, repo)
    commit(repo, {"docs/sprint/M1-e2e.md": "# test\n\nVerdict: FAILS\n\n**RESTORE PASSES.**\n"})
    t("step", "add", "M1", "test")
    row = t.sql("SELECT verdict, note FROM step WHERE kind='test'")[0]
    assert row == {"verdict": "FAILS", "note": "RESTORE PASSES"}
    t("step", "finish", "M1", "test", "--verdict", "PASSES", rc=1)


def test_finish_is_idempotent_and_follows_the_report_when_it_changes(t, repo):
    active_item(t, repo)
    t("step", "start", "M1", "test", "--agent", "m1-test", "--model", "claude-fable-5-2")
    t("step", "finish", "M1", "test", rc=1)
    commit(repo, {"docs/sprint/M1-e2e.md": "Verdict: FAILS\n"})
    a = t.one("step", "finish", "M1", "test")
    n = log_count(t)
    b = t.one("step", "finish", "M1", "test")
    assert a["changed"] and not b["changed"] and log_count(t) == n
    commit(repo, {"docs/sprint/M1-e2e.md": "Verdict: PASSES\n"})
    c = t.one("step", "finish", "M1", "test")
    assert c["changed"] and c["verdict"] == "PASSES" and c["commit"] != a["commit"]
    assert len(t.sql("SELECT * FROM step WHERE kind='test'")) == 1


def test_promote_refuses_a_tag_git_does_not_list_and_needs_the_promote_file(t, repo):
    active_item(t, repo)
    t("promote", "M1", "--tag", "item-M1", "--deployed", "yes", rc=1)
    assert "git lists no tag item-M1" in t.last[2]
    git(repo, "tag", "item-M1")
    t("promote", "M1", "--tag", "item-M1", "--deployed", "yes", rc=1)
    assert "M1-promote.md is not committed" in t.last[2]
    commit(repo, {"docs/sprint/M1-promote.md": "# checks\n"})
    t("promote", "M1", "--tag", "item-M1", "--no-tag", "--deployed", "yes", rc=1)
    r = t.one("promote", "M1", "--tag", "item-M1", "--deployed", "no", "--untested", "the fix")
    tagged = git(repo, "rev-list", "-n1", "item-M1").stdout.strip()
    p = t.sql("SELECT * FROM promotion")[0]
    assert (p["tag"], p["commit_sha"], p["deployed"], p["verified"], p["untested"]) == \
        ("item-M1", tagged, 0, "docs/sprint/M1-promote.md", "the fix")
    assert t.sql("SELECT state FROM item WHERE id='M1'")[0]["state"] == "promoted" and r["deployed"] is False
    t("promote", "M1", "--no-tag", "--deployed", "no", rc=1)
    t("promote", "M1", "--no-tag", "--deployed", "no", "--followup")
    assert t.sql("SELECT kind, tag FROM promotion ORDER BY id")[1] == {"kind": "followup", "tag": None}


def test_one_promotion_for_several_items(t, repo):
    active_item(t, repo, 1)
    active_item(t, repo, 2)
    commit(repo, {"docs/sprint/M1-promote.md": "x\n"})
    t("promote", "M1", "M2", "--no-tag", "--deployed", "yes")
    assert t.sql("SELECT item_id FROM promotion_item ORDER BY item_id") == [{"item_id": "M1"}, {"item_id": "M2"}]


def test_grade_is_the_coordinators_on_a_finished_test(t, repo):
    active_item(t, repo)
    t("grade", "M1", "pass", rc=1)
    commit(repo, {"docs/sprint/M1-e2e.md": "Verdict: FAILS\n"})
    t("step", "add", "M1", "test")
    r = t.one("grade", "M1", "pass")
    assert (r["verdict"], r["graded"]) == ("FAILS", "pass")


def test_matters_are_one_list_with_a_kind_and_an_owner(t):
    t("add", "--title", "x")
    a = t.one("matter", "add", "decision", "--owner", "drew", "--item", "M1", "--text", "cap it?", "--blocks")
    b = t.one("matter", "add", "leftover", "--owner", "coordinator", "--text", "no item")
    t("matter", "add", "escape", "--owner", "coordinator", "--text", "x", rc=1)
    t("matter", "add", "leftover", "--owner", "someone", "--text", "x", rc=1)
    t("matter", "close", a["matter"], "--how", "Drew: cap at 400 tokens")
    t("matter", "close", a["matter"], "--how", "again", rc=1)
    assert [m["id"] for m in t.j("matter", "list", "--open")] == [b["matter"]]
    m = t.sql("SELECT * FROM open_thing WHERE id=?", a["matter"])[0]
    assert m["closed_how"] == "Drew: cap at 400 tokens" and m["blocks"] == 1 and m["kind"] == "decision"


# ── plumbing 6, criterion 6: check names each row of Appendix C ───────────

@pytest.fixture
def fake_herdr(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "herdr").write_text(FAKE.format(python=sys.executable))
    (bindir / "herdr").chmod(0o755)
    h = Herdr(bindir)
    h.save({"calls": [], "agents": [], "workspaces": [], "n": 0, "fail": {}, "get_seq": {},
            "prompt_results": []})
    monkeypatch.setenv("PATH", f"{bindir}:{BASE_PATH}")
    monkeypatch.setenv("HERDR_ENV", "1")
    return h


def codes(t):
    rc, out, _ = t("check", rc=None)
    found = [json.loads(x) for x in out.splitlines()]
    assert rc == (3 if found else 0)
    return {f["check"]: f for f in found}


def run_fix(t, fix):
    """Run the fix line check printed, as work.py arguments."""
    import shlex
    argv = shlex.split(fix.split("  (")[0])
    assert argv[0] == "work.py"
    return t(*argv[1:])


def test_check_is_clean_on_a_fresh_item_and_read_only(t, repo, fake_herdr):
    active_item(t, repo)
    before = t.db.read_bytes(), log_count(t)
    assert codes(t) == {}
    assert (t.db.read_bytes(), log_count(t)) == before


def test_c1_a_started_step_with_no_live_agent_is_named_and_stopped_by_one_command(t, repo, fake_herdr):
    active_item(t, repo)
    t("step", "start", "M1", "build", "--agent", "m1-build")
    f = codes(t)["started-not-working"]
    assert "m1-build is not in herdr" in f["says"] and f["item"] == "M1"
    run_fix(t, f["fix"].replace("'...'", "dead-agent"))
    assert "started-not-working" not in codes(t)
    assert t.sql("SELECT state, note FROM step WHERE kind='build'")[0] == {"state": "stopped", "note": "dead-agent"}


def test_c2_an_agent_working_with_no_step_is_named_and_started_by_one_command(t, repo, fake_herdr, tmp_path):
    active_item(t, repo)
    fake_herdr.add_agent("m1-build", "working", repo)
    st = {"name": "m1-build", "item": "M1", "role": "build", "round": 1, "probe": False, "repo": str(repo),
          "launched_iso": "2026-11-24T10:00:00-0500", "model": "claude-opus-5-5",
          "report": "docs/sprint/M1-build.md"}
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "m1-build.json").write_text(json.dumps(st))
    f = codes(t)["agent-no-step"]
    assert "--at 2026-11-24T10:00:00-0500" in f["fix"] and "--report docs/sprint/M1-build.md" in f["fix"]
    run_fix(t, f["fix"])
    assert codes(t) == {}
    row = t.sql("SELECT state, started_at, agent, model FROM step WHERE kind='build'")[0]
    assert row == {"state": "started", "started_at": "2026-11-24T15:00:00Z", "agent": "m1-build",
                   "model": "claude-opus-5-5"}


def test_c2_a_state_file_with_no_step_is_named(t, repo, fake_herdr, tmp_path):
    active_item(t, repo)
    st = {"name": "m1-test", "item": "M1", "role": "test", "round": 1, "probe": False, "repo": str(repo),
          "launched_iso": "2026-11-24T10:00:00-0500", "report": "docs/sprint/M1-e2e.md"}
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "m1-test.json").write_text(json.dumps(st))
    assert "state-file-no-step" in codes(t)


def test_c3_a_started_step_whose_report_is_in_is_named_with_the_command(t, repo, fake_herdr):
    active_item(t, repo)
    t("step", "start", "M1", "build", "--agent", "m1-build")
    fake_herdr.add_agent("m1-build", "idle", repo)
    commit(repo, {"docs/sprint/M1-build.md": "# build\n"})
    f = codes(t)["started-report-in"]
    assert "agent.py status m1-build" in f["fix"]
    t("step", "finish", "M1", "build")
    assert codes(t) == {}


def test_c4_a_verdict_changed_in_the_report_is_named_and_refreshed(t, repo, fake_herdr):
    active_item(t, repo)
    commit(repo, {"docs/sprint/M1-e2e.md": "Verdict: FAILS\n"})
    t("step", "add", "M1", "test")
    commit(repo, {"docs/sprint/M1-e2e.md": "Verdict: PASSES\n"})
    f = codes(t)["verdict-changed"]
    assert "row says FAILS" in f["says"] and "now reads PASSES" in f["says"]
    run_fix(t, f["fix"])
    assert codes(t) == {} and t.sql("SELECT verdict FROM step WHERE kind='test'")[0]["verdict"] == "PASSES"


def test_c5_a_step_commit_not_in_the_branch_is_named(t, repo, fake_herdr):
    active_item(t, repo)
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, {"docs/sprint/M1-build.md": "# build\n"})
    t("step", "add", "M1", "build")
    git(repo, "checkout", "-q", "main")
    assert "commit-not-in-branch" in codes(t)


def test_c6_a_tag_with_no_promotion_row_is_named(t, repo, fake_herdr):
    active_item(t, repo)
    git(repo, "tag", "item-M1")
    assert "promote M1 --tag item-M1" in codes(t)["tag-not-promoted"]["fix"]


def test_c7_a_promotions_tag_that_git_lost_is_named(t, repo, fake_herdr):
    active_item(t, repo)
    git(repo, "tag", "item-M1")
    commit(repo, {"docs/sprint/M1-promote.md": "x\n"})
    t("promote", "M1", "--tag", "item-M1", "--deployed", "yes")
    git(repo, "tag", "-d", "item-M1")
    assert "tag-gone" in codes(t)


def test_c8_a_criteria_file_with_no_item_is_named(t, repo, fake_herdr):
    commit(repo, {"docs/sprint/M7-criteria.md": CRIT.format(n=7)})
    f = codes(t)["file-no-item"]
    assert f["item"] == "M7" and "add --id M7" in f["fix"]


def test_a_committed_step_file_with_no_row_is_named(t, repo, fake_herdr):
    active_item(t, repo)
    commit(repo, {"docs/sprint/M1-e2e2.md": "Verdict: PASSES\n"})
    f = codes(t)["file-no-step"]
    assert f["fix"] == "work.py step add M1 test --round 2"
    run_fix(t, f["fix"])
    assert codes(t) == {}


def test_a_queue_not_numbered_1_to_n_is_named(t, repo, fake_herdr):
    t("add", "--title", "a")
    t("add", "--title", "b")
    c = sqlite3.connect(t.db)
    c.execute("UPDATE item SET position = 5 WHERE id = 'M2'")
    c.commit()
    c.close()
    assert "[1, 5]" in codes(t)["queue-numbering"]["says"]


def test_a_promotion_with_no_roadmap_shipped_line_is_named(t, repo, fake_herdr):
    active_item(t, repo)
    commit(repo, {"docs/sprint/M1-promote.md": "x\n"})
    t("promote", "M1", "--no-tag", "--deployed", "yes")
    assert "no-shipped-line" in codes(t)
    commit(repo, {"ROADMAP.md": ROADMAP + "- **Thing** *(2026-11-25, work item M1)*\n"})
    assert "no-shipped-line" not in codes(t)


def test_herdr_not_reachable_is_said_not_guessed(t, repo):
    active_item(t, repo)
    t("step", "start", "M1", "build", "--agent", "m1-build")
    f = codes(t)
    assert "herdr" in f and "started-not-working" not in f


def test_a_stale_or_missing_dump_is_named(t, repo, fake_herdr, wk):
    active_item(t, repo)
    wk.dump_path(t.db).unlink()
    assert "dump-missing" in codes(t)
    t("dump")
    assert codes(t) == {}


def test_check_names_a_database_of_another_repo(wk, db, repo, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    Tool(wk, db, other)("init")
    rc, out, _ = Tool(wk, db, repo)("check", rc=3)
    assert '"check": "repo"' in out


# ── plumbing 7: resume ─────────────────────────────────────────────────────

SECTIONS = ["== Check:", "== Under way:", "== Queue:", "== Owed by Drew:", "== Open leftovers:",
            "== Escapes within the last 10 promoted items", "== Tree:", "== Agents scripts/agent.py knows:",
            "== Last five writes", "== Not covered here"]


def test_resume_prints_the_sections_in_order(t, repo, fake_herdr):
    active_item(t, repo)
    t("add", "--title", "queued one")
    t("note", "M1", "--next", "launch the build", "--waits", "drew", "--waits-note", "the go")
    t("matter", "add", "decision", "--owner", "drew", "--item", "M1", "--text", "Ship it?", "--blocks")
    t("matter", "add", "leftover", "--owner", "coordinator", "--text", "a loose end")
    text = t.text("resume")
    at = [text.index(s) for s in SECTIONS]
    assert at == sorted(at)
    assert "M1  tier -  thing 1" in text and "next step: launch the build" in text and "waits on: drew: the go" in text
    assert "1. M2  tier -  queued one  <- next" in text
    assert "M1 BLOCKS the item" in text and "Ship it?" in text
    assert "coordinator: 1" in text and "a loose end" in text
    assert "voice runs marked as corrected in Grafana" in text and "ROADMAP.md entry that waits on something outside" in text
    assert "== Tree: branch main" in text
    assert "test-session: work.py" in text


def test_resume_shows_the_stage_computed_from_rows_and_the_grading(t, repo, fake_herdr):
    active_item(t, repo)
    assert "stage: planning (criteria written)" in t.text("resume")
    fake_herdr.add_agent("m1-build", "working", repo)
    t("step", "start", "M1", "build", "--agent", "m1-build")
    assert "stage: implementing (implementing, agent m1-build working)" in t.text("resume")
    commit(repo, {"docs/sprint/M1-build.md": "# b\n"})
    fake_herdr.set_status("m1-build", "idle")
    t("step", "finish", "M1", "build")
    assert "stage: waiting for the coordinator (build in, not tested)" in t.text("resume")
    commit(repo, {"docs/sprint/M1-e2e.md": "Verdict: FAILS\n"})
    t("step", "add", "M1", "test")
    t("grade", "M1", "pass")
    text = t.text("resume")
    assert "stage: waiting for the coordinator (test 1: FAILS)" in text
    assert "last step: test round 1, FAILS, graded pass" in text


def test_resume_counts_escapes_within_the_last_ten_promoted(t, repo, fake_herdr):
    for n in range(1, 13):
        active_item(t, repo, n)
        commit(repo, {f"docs/sprint/M{n}-promote.md": "x\n"})
        t("promote", f"M{n}", "--no-tag", "--deployed", "yes")
    t("matter", "add", "escape", "--owner", "coordinator", "--item", "M1", "--text", "old")
    t("matter", "add", "escape", "--owner", "coordinator", "--item", "M12", "--text", "new one")
    text = t.text("resume")
    assert "(M12, M11, M10, M9, M8, M7, M6, M5, M4, M3): 1" in text and "new one" in text


@pytest.mark.parametrize("case", ["missing", "empty", "corrupt", "newer"])
def test_resume_on_a_broken_database_exits_2_and_never_says_nothing_under_way(wk, db, repo, case):
    tool = Tool(wk, db, repo)
    if case != "missing":
        if case == "newer":
            tool("init")
            c = sqlite3.connect(db)
            c.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
            c.commit()
            c.close()
        else:
            db.parent.mkdir(parents=True)
            db.write_bytes(b"" if case == "empty" else b"this is not a database at all " * 100)
    t0 = time.monotonic()
    rc, out, err = tool("resume", rc=2)
    assert time.monotonic() - t0 < 12
    assert out == "" and err.count("\n") == 1 and err.startswith(f"work.py resume: {case}: ")


def test_an_empty_schemaless_sqlite_file_is_empty(wk, db, repo):
    db.parent.mkdir(parents=True)
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE other (x)")
    c.commit()
    c.close()
    assert Tool(wk, db, repo)("resume", rc=2)[2].startswith("work.py resume: empty: ")


def hold_lock(db: Path, readers_too: bool):
    code = ("import sqlite3, sys, time\n"
            f"c = sqlite3.connect({str(db)!r}, isolation_level=None)\n"
            + ("c.execute('PRAGMA locking_mode=EXCLUSIVE')\nc.execute('BEGIN EXCLUSIVE')\n"
               "c.execute(\"INSERT INTO meta VALUES ('lock', 'x')\")\n" if readers_too else
               "c.execute('BEGIN IMMEDIATE')\n")
            + "print('held', flush=True)\ntime.sleep(30)\n")
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    return p


def test_a_database_locked_for_30_s_is_said_within_12_s(t, repo):
    holder = hold_lock(t.db, readers_too=True)
    try:
        env = dict(os.environ, KRONK_WORK_WHO="x")
        cmds = [["note", "M1", "--next", "x"], ["resume"]]
        t0 = time.monotonic()
        procs = [subprocess.Popen([sys.executable, str(SCRIPT), "--db", str(t.db), "--repo", str(repo), *c],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env) for c in cmds]
        res = [p.communicate(timeout=30) for p in procs]
        took = time.monotonic() - t0
    finally:
        holder.kill()
        holder.wait()
    assert took < 12
    for p, (out, err), c in zip(procs, res, cmds):
        assert p.returncode == 2 and out == "" and err.count("\n") == 1
        assert err.startswith(f"work.py {c[0]}: locked: still locked after 10 s"), err


# ── plumbing 9, criterion 8: the dump ──────────────────────────────────────

def test_every_write_rewrites_the_dump_private_and_restore_dump_rebuilds_it(t, repo, wk, tmp_path):
    active_item(t, repo)
    t("add", "--title", "two")
    t("matter", "add", "leftover", "--owner", "drew", "--text", "x")
    dp = wk.dump_path(t.db)
    assert stat.S_IMODE(dp.stat().st_mode) == 0o600
    assert dp.read_text().count("INSERT INTO \"log\"") == log_count(t)
    before = t.text("resume").split("== Last five writes")[0]
    target = tmp_path / "restored" / "work.db"
    r = Tool(wk, target, repo)
    r("restore-dump", "--from", dp)
    after = r.text("resume").split("== Last five writes")[0]
    norm = lambda s: s.split("\n", 1)[1]
    assert norm(after) == norm(before)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    r("restore-dump", "--from", dp, rc=1)
    assert "exists; move it" in r.last[2]


def test_restore_dump_refuses_a_dump_that_does_not_load(wk, tmp_path, repo):
    bad = tmp_path / "bad.sql"
    bad.write_text("CREATE TABLE x (\n")
    Tool(wk, tmp_path / "n" / "work.db", repo)("restore-dump", "--from", bad, rc=1)
    assert not (tmp_path / "n" / "work.db").exists()


# ── criterion 4: two writers, as processes ─────────────────────────────────

WRITER = """
import io, sys
sys.dont_write_bytecode = True
import importlib.util
spec = importlib.util.spec_from_file_location("w", sys.argv[1])
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
db, repo, kind, n = sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
bad = 0
for i in range(n):
    if kind == "add":
        argv = ["add", "--title", f"a{i}"]
    else:
        argv = ["note", "M1", "--next", f"b{i}"]
    rc = w.main(["--db", db, "--repo", repo, *argv], out=io.StringIO())
    bad += rc != 0
print(bad)
"""


def test_two_writers_200_read_then_write_commands_each_lose_nothing(t, repo, tmp_path):
    t("add", "--title", "the one being noted")
    script = tmp_path / "writer.py"
    script.write_text(WRITER)
    base = log_count(t)
    env = dict(os.environ, KRONK_WORK_WHO="writer")
    procs = [subprocess.Popen([sys.executable, str(script), str(SCRIPT), str(t.db), str(repo), k, "200"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
             for k in ("add", "note")]
    res = [p.communicate(timeout=300) for p in procs]
    for p, (out, err) in zip(procs, res):
        assert p.returncode == 0 and out.strip() == "0", err
        assert "locked" not in err
    assert log_count(t) == base + 400
    ids = [r["id"] for r in t.sql("SELECT id FROM item")]
    assert len(ids) == 201 and len(set(ids)) == 201
    assert t.sql("SELECT next_step FROM item WHERE id='M1'")[0]["next_step"] == "b199"
    assert [q["position"] for q in t.j("queue")] == list(range(1, 202))


# ── plumbing 10, criterion 2: the import ───────────────────────────────────

PROMOTIONS = """# Sprint promotions log

| Item | Tag | Promoted | What changed in production | Held or left over |
| --- | --- | --- | --- | --- |
| M1 | `sprint-M1` | 2026-11-15 | Redeployed. | A note left over. |
| M2, M3 | `sprint-M2-M3` (`abc`) | 2026-11-16 | Both. | |
| M4 | none | 2026-11-17 | Audit only. | Four changes wait. |

### M1 again (2026-11-18)

- Not done: one more thing.

### M5 (2026-11-20): the fifth

- Tag `item-M5` on the commit. **No deploy**.
- Held or left over:
  - triaged in the seed
"""


@pytest.fixture
def legacy(repo):
    files = {"docs/sprint/PROMOTIONS.md": PROMOTIONS}
    for n in (1, 2, 3, 5, 6):
        files[f"docs/sprint/M{n}-criteria.md"] = CRIT.format(n=n)
    files["docs/sprint/M1-e2e.md"] = "# t\n\nVerdict: FAILS\n"
    files["docs/sprint/M1-e2e2.md"] = "# t\n\n**Verdict: ITEM PASSES.**\n\n**RESTORE FAILS.**\n"
    files["docs/sprint/M4-audit.md"] = "# audit\n"
    files["docs/sprint/M5-build.md"] = "# build\n"
    files["docs/sprint/M5-e2e.md"] = "Verdict: FAILS\n"
    commit(repo, files, "legacy")
    for tag in ("sprint-M1", "sprint-M2-M3", "item-M5"):
        git(repo, "tag", tag)
    commit(repo, {"docs/sprint/M7-criteria.md": CRIT.format(n=7)}, "dropped item's criteria")
    crit7 = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "rm", "-q", "docs/sprint/M7-criteria.md")
    git(repo, "commit", "-q", "-m", "drop M7")
    drop7 = git(repo, "rev-parse", "HEAD").stdout.strip()
    at = drop7
    commit(repo, {"docs/sprint/M6-build.md": "# after the import commit\n"}, "later")
    git(repo, "tag", "item-M6")
    seed = {"items": [
        {"id": "M5", "tier": 1, "tag": "item-M5", "deployed": False, "deploy_note": "host only",
         "graded": "test FAILS; graded should-fix by the coordinator"},
        {"id": "M6", "state": "queued", "tier": 2, "position": 1, "next_step": "review", "note": "between"},
        {"id": "M7", "state": "dropped", "why": "Drew: drop m7", "criteria_commit": crit7[:7],
         "dropped_commit": drop7[:7], "title": "the seventh"},
        {"id": "M8", "state": "queued", "position": 2, "after": "M6", "title": "the eighth"},
    ], "matters": [
        {"kind": "decision", "item": "M5", "owner": "drew", "state": "open", "how": None, "text": "Cap it?"},
        {"kind": "leftover", "item": "M5", "owner": "coordinator", "state": "closed", "how": "fixed in M6",
         "text": "A loose end"},
        {"kind": "leftover", "item": None, "owner": "drew", "state": "open", "how": None, "text": "Push"},
    ]}
    p = repo.parent / "seed.json"
    p.write_text(json.dumps(seed))
    return {"at": at, "seed": p, "crit7": crit7}


def test_import_from_a_named_commit(wk, db, repo, legacy):
    t = Tool(wk, db, repo)
    (s,) = t.j("import-legacy", "--at", legacy["at"][:7], "--seed", legacy["seed"])
    assert s["import"] == legacy["at"][:7]
    items = {r["id"]: r for r in t.sql("SELECT * FROM item")}
    assert sorted(items) == ["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8"]
    assert {i: r["state"] for i, r in items.items()} == {
        "M1": "promoted", "M2": "promoted", "M3": "promoted", "M4": "promoted", "M5": "promoted",
        "M6": "queued", "M7": "dropped", "M8": "queued"}
    assert items["M1"]["title"] == "thing number 1" and items["M8"]["after_item"] == "M6"
    assert (items["M6"]["position"], items["M8"]["position"], items["M6"]["next_step"]) == (1, 2, "review; between")
    assert (items["M7"]["ended_why"], items["M7"]["criteria"]) == ("Drew: drop m7", "docs/sprint/M7-criteria.md")
    m7 = t.sql("SELECT * FROM step WHERE item_id='M7'")
    assert len(m7) == 1 and m7[0]["commit_sha"] == legacy["crit7"] and m7[0]["kind"] == "criteria"
    # Steps from the files at the commit, verdicts by work_export's rule, a second subject as a note.
    tests = t.sql("SELECT round, verdict, note, graded, by FROM step WHERE item_id='M1' AND kind='test' ORDER BY round")
    assert tests == [{"round": 1, "verdict": "FAILS", "note": None, "graded": None, "by": "agent"},
                     {"round": 2, "verdict": "PASSES", "note": "RESTORE FAILS", "graded": None, "by": "agent"}]
    assert t.sql("SELECT kind, name FROM step WHERE item_id='M4'") == [{"kind": "other", "name": "audit"}]
    assert not t.sql("SELECT * FROM step WHERE item_id='M6' AND kind='build'")      # after --at
    # Promotions: rows, combined, the follow-up, the seed's tag, deployed and note.
    proms = t.sql("SELECT p.kind, p.tag, p.deployed, p.verified, p.note, group_concat(pi.item_id) AS ids "
                  "FROM promotion p JOIN promotion_item pi ON pi.promotion_id = p.id GROUP BY p.id ORDER BY p.id")
    assert [(p["kind"], p["tag"], p["ids"]) for p in proms] == [
        ("promotion", "sprint-M1", "M1"), ("promotion", "sprint-M2-M3", "M2,M3"), ("promotion", None, "M4"),
        ("followup", None, "M1"), ("promotion", "item-M5", "M5")]
    assert proms[4]["deployed"] == 0 and "host only" in proms[4]["note"] and proms[2]["note"] == "tag written as 'none'"
    assert proms[0]["verified"] == "PROMOTIONS.md:5"
    m5 = t.sql("SELECT graded, note FROM step WHERE item_id='M5' AND kind='test'")[0]
    assert m5 == {"graded": "pass", "note": "test FAILS; graded should-fix by the coordinator"}
    assert items["M5"]["tier"] == 1
    # Matters: the seed's triage, then each older cell untriaged, naming its line.
    ms = t.sql("SELECT kind, item_id, owner, text, closed_how FROM open_thing ORDER BY id")
    assert ms[:3] == [
        {"kind": "decision", "item_id": "M5", "owner": "drew", "text": "Cap it?", "closed_how": None},
        {"kind": "leftover", "item_id": "M5", "owner": "coordinator", "text": "A loose end", "closed_how": "fixed in M6"},
        {"kind": "leftover", "item_id": None, "owner": "drew", "text": "Push", "closed_how": None}]
    assert ms[3:] == [
        {"kind": "leftover", "item_id": "M1", "owner": None, "text": "see PROMOTIONS.md:5", "closed_how": None},
        {"kind": "leftover", "item_id": "M4", "owner": None, "text": "see PROMOTIONS.md:7", "closed_how": None},
        {"kind": "leftover", "item_id": "M1", "owner": None, "text": "see PROMOTIONS.md:11", "closed_how": None}]
    # The counts: every tag on a row or listed; every file a report or listed.
    assert s["tags"] == {"in_git": 4, "on_a_promotion": 3, "not_on_a_promotion": ["item-M6"]}
    assert s["files"]["not_a_report"] == ["PROMOTIONS.md", "WORKFLOW.md"]
    assert s["matters"] == {"triaged": 3, "untriaged_older_cells": 3}


def test_import_is_reproducible_and_only_into_an_empty_database(wk, tmp_path, repo, legacy):
    dumps = []
    for n in (1, 2):
        t = Tool(wk, tmp_path / f"i{n}" / "work.db", repo)
        t("import-legacy", "--at", legacy["at"], "--seed", legacy["seed"])
        rows = {tb: t.sql(f"SELECT * FROM {tb}") for tb in ("item", "step", "promotion", "promotion_item")}
        rows["open_thing"] = [{k: v for k, v in r.items() if k != "id"} for r in t.sql("SELECT * FROM open_thing")]
        dumps.append(rows)
    assert dumps[0] == dumps[1]
    t("import-legacy", "--at", legacy["at"], "--seed", legacy["seed"], rc=1)
    assert "fills an empty database only" in t.last[2]


def test_import_refuses_a_seed_it_cannot_read(wk, db, repo, legacy, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"items": [{"id": "M1", "colour": "red"}]}))
    Tool(wk, db, repo)("import-legacy", "--at", legacy["at"], "--seed", bad, rc=1)
    bad.write_text(json.dumps({"matters": [{"kind": "leftover", "owner": "drew", "state": "closed", "text": "x"}]}))
    Tool(wk, db, repo)("import-legacy", "--at", legacy["at"], "--seed", bad, rc=1)


def test_import_compares_with_a_work_json_item_for_item(wk, db, repo, legacy, tmp_path):
    we = _load("m33_we", REPO / "scripts" / "work_export.py")
    data = we.collect(repo)
    p = tmp_path / "work.json"
    p.write_text(json.dumps(data))
    lines = Tool(wk, db, repo).j("import-legacy", "--at", legacy["at"], "--seed", legacy["seed"], "--compare", p)
    diffs = [x for x in lines if "compare" in x]
    # The fixture's '- Tag `item-M5`' line is read by the parser, so only the file committed after --at
    # differs (M6's stage is "promoting" either way: its tag decides).
    assert {(d["compare"], d["item"]) for d in diffs} == {("step", "M6")}


SEED = REPO / "docs" / "sprint" / "M33-seed.json"
IMPORT_AT = "4eee8fd"


def test_the_coordinators_seed_loads(wk):
    seed = wk.load_seed(str(SEED))
    assert len(seed["items"]) == 10 and len(seed["matters"]) == 43


@pytest.mark.skipif(subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", f"{IMPORT_AT}^{{commit}}"],
                                   capture_output=True).returncode != 0, reason="the import commit is not here")
def test_the_real_import_at_the_build_start(wk, db):
    """The trial import of the build report, pinned: the coordinator's live
    import at promotion should give these counts at this commit."""
    t = Tool(wk, db, REPO)
    (s,) = t.j("import-legacy", "--at", IMPORT_AT, "--seed", SEED)
    assert s["counts"] == {"item": 36, "step": 156, "promotion": 32, "promotion_item": 35, "open_thing": 67}
    assert s["matters"] == {"triaged": 43, "untriaged_older_cells": 24}
    assert s["tags"]["in_git"] >= 28 and not [x for x in s["tags"]["not_on_a_promotion"] if not x.startswith("item-M3")]
    assert s["files"]["not_a_report"] == ["PROMOTIONS.md", "WORKFLOW.md"]
    tags = {r["id"]: r["tag"] for r in t.sql("SELECT pi.item_id AS id, p.tag FROM promotion p JOIN promotion_item "
                                             "pi ON pi.promotion_id = p.id WHERE p.kind = 'promotion'")}
    assert (tags["M28"], tags["M31"], tags["M6"]) == ("item-M28", "item-M31", None)
    st = {r["id"]: r["state"] for r in t.sql("SELECT id, state FROM item")}
    assert (st["M29"], st["M30"], st["M32"], st["M33"]) == ("dropped", "queued", "queued", "active")
    dep = {r["id"]: r["deployed"] for r in t.sql("SELECT pi.item_id AS id, p.deployed FROM promotion p JOIN "
                                                 "promotion_item pi ON pi.promotion_id = p.id WHERE p.kind='promotion'")}
    assert [dep[f"M{n}"] for n in (24, 25, 26, 27, 28, 31)] == [1, 1, 0, 1, 0, 1]


# ── the stage function is work_export's, fed from rows ─────────────────────

def test_stage_from_rows_equals_work_exports_stage_from_files(wk):
    we = _load("m33_we2", REPO / "scripts" / "work_export.py")
    rows = [
        {"id": 1, "kind": "criteria", "round": 1, "name": "", "state": "finished", "report": "docs/sprint/M1-criteria.md",
         "verdict": None, "ended_at": "2026-11-24T01:00:00Z"},
        {"id": 2, "kind": "build", "round": 1, "name": "", "state": "finished", "report": "docs/sprint/M1-build.md",
         "verdict": None, "ended_at": "2026-11-24T02:00:00Z"},
        {"id": 3, "kind": "test", "round": 2, "name": "", "state": "finished", "report": "docs/sprint/M1-e2e2.md",
         "verdict": "PASSES", "ended_at": "2026-11-24T03:00:00Z"},
        {"id": 4, "kind": "fix", "round": 2, "name": "", "state": "started", "report": None, "verdict": None,
         "ended_at": None},
    ]
    files = [{"kind": "criteria", "n": None, "file": "M1-criteria.md", "verdicts": None},
             {"kind": "build", "n": None, "file": "M1-build.md", "verdicts": None},
             {"kind": "test", "n": 2, "file": "M1-e2e2.md", "verdicts": [{"subject": "item", "verdict": "PASSES"}]}]
    for tag, promoted in ((False, False), (True, False), (False, True)):
        assert wk.item_stage(rows, promoted, tag, None) == we.stage(files, tag, promoted, None)
    agents = [{"name": "m1-fix2", "status": "working", "role": "fix"}]
    assert wk.item_stage(rows, False, False, agents) == we.stage(files, False, False, agents)


# ── criterion 3: a full life through the tools, agent.py included ─────────

def test_a_full_life_of_a_made_up_item(wk, db, repo, fake_herdr, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KRONK_WORK_DB", str(db))
    at = _load("m33_agent_tool", REPO / "scripts" / "agent.py")
    brief = tmp_path / "brief.txt"
    brief.write_text("Do it.\n")
    t = Tool(wk, db, repo)

    def resume():
        return t.text("resume").split("== Last five writes")[0]

    def agent(*argv):
        rc = at.main([str(a) for a in argv])
        out, err = capsys.readouterr()
        assert "not recorded" not in err, err
        return rc, out

    t("init")
    t("add", "--title", "the other one")
    t("add", "--title", "the made-up one")
    assert "1. M1" in resume() and "2. M2" in resume()
    t("move", "M2", "1")
    assert "1. M2  tier -  the made-up one  <- next" in resume()
    commit(repo, {"docs/sprint/M2-criteria.md": CRIT.format(n=2)})
    t("start", "M2")
    t("step", "add", "M2", "criteria")
    t("note", "M2", "--tier", "3", "--next", "build")
    assert "M2  tier 3  the made-up one\n    stage: planning (criteria written)" in resume()
    # build, launched by agent.py through the fake herdr
    assert agent("launch", "M2", "build", "--brief", brief, "--repo", repo,
                 "--report", "docs/sprint/M2-build.md")[0] == 0
    assert "stage: implementing (implementing, agent m2-build working)" in resume()
    assert "started: build round 1 by m2-build" in resume()
    commit(repo, {"docs/sprint/M2-build.md": "# build\n"})
    fake_herdr.set_status("m2-build", "idle")
    rc, out = agent("wait", "m2-build", "--interval", "0.05", "--timeout", "10")
    assert rc == 0 and json.loads(out)["done"]
    assert "stage: waiting for the coordinator (build in, not tested)" in resume()
    assert agent("close", "m2-build")[0] == 0
    # test 1 fails
    assert agent("launch", "M2", "test", "--brief", brief, "--repo", repo, "--report", "docs/sprint/M2-e2e.md")[0] == 0
    commit(repo, {"docs/sprint/M2-e2e.md": "Verdict: FAILS\n"})
    fake_herdr.set_status("m2-test", "idle")
    assert agent("status", "m2-test")[0] == 0
    assert agent("close", "m2-test")[0] == 0
    assert "stage: waiting for the coordinator (test 1: FAILS)" in resume()
    t("note", "M2", "--next", "fix round 1")
    # fix, then test 2 passes
    assert agent("launch", "M2", "fix", "--brief", brief, "--repo", repo, "--report", "docs/sprint/M2-fix1.md")[0] == 0
    commit(repo, {"docs/sprint/M2-fix1.md": "# fix\n"})
    fake_herdr.set_status("m2-fix", "idle")
    assert agent("close", "m2-fix")[0] == 0              # closed without a status: close finishes it
    assert "stage: waiting for the coordinator (fix 1 in, not retested)" in resume()
    assert agent("launch", "M2", "test", "--round", "2", "--brief", brief, "--repo", repo,
                 "--report", "docs/sprint/M2-e2e2.md")[0] == 0
    commit(repo, {"docs/sprint/M2-e2e2.md": "Verdict: PASSES\n"})
    fake_herdr.set_status("m2-test2", "idle")
    assert agent("wait", "m2-test2", "--interval", "0.05", "--timeout", "10")[0] == 0
    assert agent("close", "m2-test2")[0] == 0
    assert "stage: to promote (test 2: PASSES)" in resume()
    # a leftover opened and closed, a decision asked and answered
    lo = t.one("matter", "add", "leftover", "--owner", "coordinator", "--item", "M2", "--text", "tidy the docs")
    dc = t.one("matter", "add", "decision", "--owner", "drew", "--item", "M2", "--text", "Deploy tonight?",
               "--blocks")
    assert "Deploy tonight?" in resume() and "tidy the docs" in resume()
    t("matter", "close", dc["matter"], "--how", "Drew: yes")
    t("matter", "close", lo["matter"], "--how", "done in the promote file")
    assert "Deploy tonight?" not in resume() and "tidy the docs" not in resume()
    # promoted
    git(repo, "tag", "item-M2")
    commit(repo, {"docs/sprint/M2-promote.md": "# checks\n"})
    t("promote", "M2", "--tag", "item-M2", "--deployed", "yes")
    text = resume()
    assert "== Under way: 0" in text and "1. M1  tier -  the other one  <- next" in text
    steps = t.sql("SELECT kind, round, state, verdict, agent, model, started_at IS NOT NULL AS s FROM step "
                  "WHERE item_id='M2' ORDER BY id")
    assert [(x["kind"], x["round"], x["state"], x["verdict"]) for x in steps] == [
        ("criteria", 1, "finished", None), ("build", 1, "finished", None), ("test", 1, "finished", "FAILS"),
        ("fix", 1, "finished", None), ("test", 2, "finished", "PASSES")]
    assert all(x["s"] and x["agent"] for x in steps[1:])
    assert steps[1]["model"] == "claude-opus-5-5" and steps[2]["model"] == "claude-fable-5-2"
    # the log holds every change
    log = t.sql("SELECT cmd FROM log ORDER BY id")
    cmds = [x["cmd"].split(" ", 5)[-1] if x["cmd"].startswith("work.py") else x["cmd"] for x in log]
    for want in ("add --title 'the made-up one'", "move M2 1", "start M2", "step add M2 criteria",
                 "agent.py launch m2-build", "agent.py wait m2-build", "agent.py launch m2-test",
                 "agent.py status m2-test", "agent.py close m2-fix", "agent.py wait m2-test2",
                 "promote M2 --tag item-M2 --deployed yes"):
        assert any(c == want or c.startswith(want) for c in cmds), want
    assert not [x for x in cmds if x.startswith("agent.py close m2-build")]    # already finished: no write
    assert set(codes(t)) == {"no-shipped-line"}
    commit(repo, {"ROADMAP.md": ROADMAP + "- **The made-up one** *(2026-11-25, work item M2)*\n"})
    assert codes(t) == {}
