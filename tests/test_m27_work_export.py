"""scripts/work_export.py, the host exporter behind /work (work item M27).

Since M34 the live path reads the work-state database's dump; the reader
of PROMOTIONS.md and the step files is collect_legacy(), import-only
(work.py import-legacy and check use it). The in-process tests here pin
that reader; the ones that run the script give it a scratch database made
by `work.py import-legacy` from the fixture tree itself, so the same
fixtures hold for both paths.

It runs from the working tree on a timer every minute while agents commit
in the same tree, and production reads what it writes. So these tests pin:
the verdict rule and the file grammar (criteria 1, 2), the stage table
(criterion 3), the backlog, done and link rules (5, 6, 7), that nothing
but the outline leaves the repo (8), and that a run takes no git lock,
writes nothing else, survives files it cannot parse and a herdr that is
absent, failing or hung (9).

Fixture trees are temporary git repositories (user set per repo); herdr
is a fake script on PATH. No socket is opened by the tests; the exporter
talks to herdr through the fake, which opens none either.
"""
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("no_network")

REPO = Path(__file__).resolve().parent.parent
# M27_WORK_EXPORT points the tests at a scratch copy: the exporter is
# built and fixed outside scripts/ and moved in when these pass, because
# the installed timer runs the working tree's copy every minute.
SCRIPT = Path(os.environ.get("M27_WORK_EXPORT", REPO / "scripts" / "work_export.py"))
BASE_PATH = "/usr/bin:/bin"
MARKER = "QQ-SECRET-MARKER-7731"


WORK_TOOL = SCRIPT.parent / "work.py"        # the work.py beside the exporter under test


def import_db(tree: Path, root: Path) -> Path:
    """A scratch work-state database of the tree as it stands (work.py
    import-legacy at HEAD, no seed), with its dump. Never the live one."""
    db = root / "state" / "work.db"
    subprocess.run([sys.executable, "-B", str(WORK_TOOL), "--db", str(db), "--repo", str(tree), "import-legacy",
                    "--at", "HEAD"], env=dict(os.environ, PATH=BASE_PATH), check=True, capture_output=True,
                   timeout=60)
    return db


def _load():
    spec = importlib.util.spec_from_file_location("m27_work_export", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def we():
    return _load()


# ── fixture trees ──────────────────────────────────────────────────────────

ROADMAP = """# Roadmap

Conventions:
- **Admission:** a convention bullet, not an entry.
- An entry is at most a few lines.

---

## Now — in flight

- **One-line entry** *(work item M9, ordered 2026-11-01)* — on one line.

- **Wrapped lead** *(sprint
  M7, bench run)* — the lead wraps a line.
## Next — agreed, not started

- **`/work`: a title with backticks** *(work item
  M8; after M7)* — Drew: "words". A body mention of M2 is not a link.
- **Nested list** *(admitted 2026-11-24)* — in this order:
  1. **First.** one.
  2. **Second.** two.
- **Old numbering** *(item 11, 2026-09-03)* — names item 3 and sprint M2 in the body.

## Later — wanted

- **Later entry** — a dash, no lead.

## Chores

- **A chore** — tidy.

## Shipped

Newest first.

- **Backups** *(2026-11-20, sprint M2 and M2b)* — shipped body text.
- **Split item** *(2026-11-18, sprint
  M5)* — both steps.
- **Old shipped thing** *(item 1, 2026-07-05)* — an old body.
- **Plain paren** (2026-05) — no lead.
"""

PROMOTIONS = """# Promotions

| Item | Tag | Promoted | What changed | Held or left over |
| --- | --- | --- | --- | --- |
| M1 | `sprint-M1` | 2026-11-15 | changed. | For Drew: one. |
| M2, M1 restore | `sprint-M2-M1` (`abc1234`) | 2026-11-16 00:02 UTC | x | for Drew's go, not addressed |
| M3 (gate only) | none | 2026-11-17 | audit | nothing |
| M5 step 1 | `sprint-M5-step1` | 2026-11-17 07:22 UTC | x | **Known regression, for Drew:** y. For Drew: z. |
| M6 | `sprint-M6` (tag only; **production not redeployed**) | 2026-11-18 | x | x |

## After the sprint (decisions)

- not an entry. For Drew: not counted.

### M2b (2026-11-20): a heading entry, no tag

- body line.

## M4 (a heading entry), 2026-11-21

- Tag `item-M4` (`1733285`). deployed.
- **For Drew:** one.
  - **For Drew, the second** line.

### M4, a later note (2026-11-22)

- note body. For Drew to look at.
"""


def git(tree: Path, *args, env=None, date=None):
    e = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", HOME=str(tree.parent))
    if date:
        e.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    e.update(env or {})
    return subprocess.run(["git", *args], cwd=tree, env=e, check=True, capture_output=True, text=True)


def make_tree(root: Path, commits, roadmap=ROADMAP, promotions=PROMOTIONS, untracked=None) -> Path:
    """commits: [(date, {file name: text}), ...] in order, each one commit
    under docs/sprint/; untracked: {file name: text} left uncommitted."""
    tree = root / "tree"
    (tree / "docs" / "sprint").mkdir(parents=True)
    git(tree, "init", "-q", "-b", "main")
    git(tree, "config", "user.name", "Fixture")
    git(tree, "config", "user.email", "fixture@example.invalid")
    (tree / "ROADMAP.md").write_text(roadmap)
    (tree / "docs" / "sprint" / "PROMOTIONS.md").write_text(promotions)
    git(tree, "add", "ROADMAP.md", "docs/sprint/PROMOTIONS.md")
    git(tree, "commit", "-q", "-m", "base", date="2026-11-01T09:00:00-05:00")
    for date, files in commits:
        for name, text in files.items():
            p = tree / "docs" / "sprint" / name
            if isinstance(text, bytes):
                p.write_bytes(text)
            else:
                p.write_text(text)
            git(tree, "add", f"docs/sprint/{name}")
        git(tree, "commit", "-q", "-m", "step", date=date)
    for name, text in (untracked or {}).items():
        (tree / "docs" / "sprint" / name).write_text(text)
    return tree


def report(verdict_line: str, item: str = "M9", n_blank: int = 1) -> str:
    return f"# {item} test\n" + "\n" * n_blank + verdict_line + "\nBody text of the report.\n"


def crit(item: str, title: str = None, handoff: str = None) -> str:
    t = f"# {title or item + ' pass criteria'}\n\nSource: words.\n"
    if handoff:
        t += f"\nHandoff line: {handoff}\n\n## Scope\n"
    return t


def herdr_bin(root: Path, agents=None, *, rc=0, body=None, sleep=None) -> Path:
    """A fake herdr on PATH: prints the agent list (or body) and exits rc."""
    bindir = root / "bin"
    bindir.mkdir(exist_ok=True)
    if body is None:
        body = json.dumps({"id": "cli:agent:list", "result": {"agents": agents or [], "type": "agent_list"}})
    (bindir / "answer.json").write_text(body)
    script = "#!/bin/sh\n" + (f"sleep {sleep}\n" if sleep else "") + f'cat "{bindir}/answer.json"\nexit {rc}\n'
    (bindir / "herdr").write_text(script)
    (bindir / "herdr").chmod(0o755)
    return bindir


def agent(name, status, cwd, **extra):
    a = {"agent": "claude", "agent_status": status, "cwd": str(cwd), "pane_id": "w1:p1", "workspace_id": "w1",
         "terminal_title": "◐ a title", "agent_session": {"value": "00000000-session"}}
    if name is not None:
        a["name"] = name
    a.update(extra)
    return a


@pytest.fixture
def path_with(monkeypatch):
    def set_(bindir=None):
        monkeypatch.setenv("PATH", (f"{bindir}:" if bindir else "") + BASE_PATH)
    set_()
    return set_


def by_id(out):
    return {it["id"]: it for it in out["items"]}


# ── criterion 2: verdicts ────────────────────────────────────────────────────

@pytest.mark.parametrize("line,expected", [
    ("Verdict: PASSES", [("item", None, "PASSES")]),
    ("Verdict: FAILS", [("item", None, "FAILS")]),
    ("**Verdict: ITEM PASSES.** Criteria 1 to 20 pass.", [("item", None, "PASSES")]),
    ("**M9 FAILS.**", [("item", "M9", "FAILS")]),
    ("**m9 NOT DETERMINED.**", [("item", "m9", "NOT DETERMINED")]),
    ("**RESTORE PASSES.** All eleven criteria.", [("RESTORE", "RESTORE", "PASSES")]),
    ("First test (FAILS): `docs/sprint/M23-e2e.md`.", []),
    ("The verdict: PASSES, said in a sentence.", []),
    ("  Verdict: PASSES", []),
    ("See Verdict: PASSES in the next file.", []),
    ("Earlier: **M9 FAILS.** was the first test.", []),
    ("**M9 PASSED.**", []),
])
def test_verdict_forms_anchored_at_line_start(we, line, expected):
    got = [(v["subject"], v["written"], v["verdict"]) for v in we.verdicts(["# t", "", line], "M9")]
    assert got == expected


def test_step_id_in_capitals_is_the_item(we):
    (v,) = we.verdicts(["# t", "", "**M21 STEP 1 FAILS.**"], "M21-step1")
    assert (v["subject"], v["written"], v["verdict"], v["line"]) == ("item", "M21 STEP 1", "FAILS", 3)
    (v,) = we.verdicts(["**M4B PASSES.** text"], "M4b")
    assert v["subject"] == "item" and v["written"] == "M4B"


def test_two_subjects_yield_both_first_per_subject(we):
    lines = ["# M4b retest", "", "**RESTORE PASSES.** All.", "", "**M4B FAILS.** one hole", "**M4B PASSES.** later"]
    got = [(v["subject"], v["written"], v["verdict"], v["line"]) for v in we.verdicts(lines, "M4b")]
    assert got == [("RESTORE", "RESTORE", "PASSES", 3), ("item", "M4B", "FAILS", 5)]


def test_verdict_on_line_41_is_not_read(we, tmp_path, path_with):
    tree = make_tree(tmp_path, [
        ("2026-11-02T10:00:00-05:00", {"M9-criteria.md": crit("M9")}),
        ("2026-11-02T11:00:00-05:00", {"M9-e2e.md": report("Verdict: FAILS", n_blank=39),
                                       "M9-e2e2.md": report("Verdict: PASSES", n_blank=38)}),
    ])
    out = we.collect_legacy(tree)
    steps = {s["file"]: s for s in by_id(out)["M9"]["steps"]}
    assert steps["M9-e2e.md"]["verdicts"] == []            # line 41
    assert steps["M9-e2e2.md"]["verdicts"][0]["line"] == 40
    assert {"file": "M9-e2e.md", "why": "no verdict line in the first 40 lines"} in out["not_read"]


def test_report_with_no_verdict_and_a_word_in_a_sentence(we, tmp_path, path_with):
    tree = make_tree(tmp_path, [
        ("2026-11-02T10:00:00-05:00", {"M9-criteria.md": crit("M9"),
                                       "M9-e2e.md": "# M9 test\n\nFirst test (FAILS): see below.\nNo verdict.\n"}),
    ])
    out = we.collect_legacy(tree)
    (t,) = [s for s in by_id(out)["M9"]["steps"] if s["kind"] == "test"]
    assert t["verdicts"] == []
    assert any(n["file"] == "M9-e2e.md" for n in out["not_read"])
    assert by_id(out)["M9"]["stage"]["detail"] == "test 1: verdict not read"


# ── criterion 1/4: the file grammar ───────────────────────────────────────────

@pytest.mark.parametrize("name,item,kind,n", [
    ("M9-criteria.md", "M9", "criteria", None), ("M9-review.md", "M9", "review", None),
    ("M9-build.md", "M9", "build", None), ("M9-e2e.md", "M9", "test", 1), ("M9-e2e3.md", "M9", "test", 3),
    ("M9-fix2.md", "M9", "fix", 2), ("M21-step1-e2e2.md", "M21-step1", "test", 2),
    ("M16b-fix1.md", "M16b", "fix", 1), ("M6-audit.md", "M6", "other", None),
    ("M5-change1.md", "M5", "other", None), ("M3-e2e2-rows.md", "M3", "other", None),
    ("M1b-restore-criteria.md", "M1b", "other", None), ("M1b-restore-build.md", "M1b", "other", None),
])
def test_file_grammar(we, name, item, kind, n):
    m = we.FILE_RE.match(name)
    assert m and m.group(1) == item
    assert we.step_kind(m.group(2)) == (kind, n)


def test_other_suffixes_are_steps_and_named_never_tests(we, tmp_path, path_with):
    tree = make_tree(tmp_path, [
        ("2026-11-02T10:00:00-05:00", {"M3-criteria.md": crit("M3")}),
        ("2026-11-02T11:00:00-05:00", {"M3-e2e2.md": report("**M3 NOT DETERMINED.**", "M3"),
                                       "M3-e2e2-rows.md": "# rows\n\nVerdict: PASSES\n"}),
        ("2026-11-02T12:00:00-05:00", {"M6-audit.md": "# M6 audit: what listens\n\nbody\n"}),
    ])
    out = we.collect_legacy(tree)
    m3 = by_id(out)["M3"]
    assert [s["file"] for s in m3["steps"]] == ["M3-criteria.md", "M3-e2e2.md", "M3-e2e2-rows.md"]
    rows = m3["steps"][2]
    assert rows["kind"] == "other" and rows["verdicts"] is None and rows["name"] == "e2e2-rows"
    assert m3["steps"][1]["verdicts"][0]["verdict"] == "NOT DETERMINED"
    named = {n["file"] for n in out["not_read"]}
    assert {"M3-e2e2-rows.md", "M6-audit.md"} <= named
    assert by_id(out)["M6"]["title"] == "M6 audit: what listens"


# ── criterion 3: the stage table ──────────────────────────────────────────────

def S(kind, n=None, verdict=None):
    s = {"file": f"M9-{kind}{n or ''}.md", "kind": kind, "n": n, "name": kind}
    if kind == "test":
        s["verdicts"] = [{"subject": "item", "written": None, "verdict": verdict, "line": 3}] if verdict else []
    return s


def A(name, status, role):
    return {"name": name, "status": status, "role": role}


@pytest.mark.parametrize("role,word", [("review", "reviewing"), ("build", "implementing"),
                                       ("test", "testing"), ("fix", "fixing")])
def test_stage_one_agent_working(we, role, word):
    st = we.stage([S("criteria")], False, False, [A(f"m9-{role}", "working", role)])
    assert (st["stage"], st["detail"], st["blocked"], st["by"]) == (word, f"{word}, agent m9-{role} working", False, "agents")


def test_stage_one_agent_blocked_is_prominent(we):
    st = we.stage([S("build")], False, False, [A("m9-test", "blocked", "test")])
    assert (st["stage"], st["detail"], st["blocked"]) == ("testing", "testing, agent m9-test BLOCKED", True)


def test_stage_two_agents_working_shows_both(we):
    st = we.stage([S("build")], False, False, [A("m9-test", "working", "test"), A("m9-fix1", "working", "fix")])
    assert st["detail"] == "fixing, agent m9-fix1 working; testing, agent m9-test working"
    assert st["words"] == "fixing; testing"


@pytest.mark.parametrize("status", ["idle", "done", "unknown"])
def test_stage_idle_done_unknown_do_not_decide(we, status):
    st = we.stage([S("criteria"), S("build")], False, False, [A("m9-build", status, "build")])
    assert (st["stage"], st["detail"], st["by"]) == ("waiting", "build in, not tested", "files")


@pytest.mark.parametrize("steps,stage,detail", [
    ([S("criteria")], "planning", "criteria written"),
    ([S("criteria"), S("review")], "planning", "review in; criteria being revised"),
    ([S("criteria"), S("build")], "waiting", "build in, not tested"),
    ([S("criteria"), S("build"), S("test", 1, "FAILS"), S("fix", 1)], "waiting", "fix 1 in, not retested"),
    ([S("criteria"), S("build"), S("test", 1, "FAILS")], "waiting", "test 1: FAILS"),
    ([S("criteria"), S("build"), S("test", 2, "NOT DETERMINED")], "waiting", "test 2: NOT DETERMINED"),
    ([S("criteria"), S("build"), S("test", 3, "PASSES")], "to_promote", "test 3: PASSES"),
    ([S("criteria"), S("build"), S("test", 1)], "waiting", "test 1: verdict not read"),
    ([], "planning", "files written, not committed"),
])
def test_stage_no_agent_by_last_step(we, steps, stage, detail):
    for agents in (None, []):          # herdr not reachable, or no agent
        st = we.stage(steps, False, False, agents)
        assert (st["stage"], st["detail"]) == (stage, detail)
    assert we.STAGE_WORDS["waiting"] == "waiting for the coordinator"


def test_stage_tag_without_promotion_is_promoting_whatever_the_agents(we):
    st = we.stage([S("test", 1, "PASSES")], True, False, [A("m9-test", "working", "test")])
    assert st["stage"] == "promoting" and st["words"] == "promoting"


def test_stage_promotion_entry_is_promoted(we):
    st = we.stage([S("build")], True, True, [A("m9-fix1", "working", "fix")])
    assert st["stage"] == "promoted"


def test_agent_name_pattern(we):
    assert we.agent_for("m27-build") == ("M27", "build")
    assert we.agent_for("m25-test2") == ("M25", "test")
    assert we.agent_for("m21-step2-fix1") == ("M21-step2", "fix")
    assert we.agent_for("m16b-review") == ("M16b", "review")
    for bad in ("kronk-claude", "m27", "m27-investigate", "M27-build", "m27-build-x", None, ""):
        assert we.agent_for(bad) == (None, None)


UNDER_WAY_TREE = [
    ("2026-11-02T10:00:00-05:00", {"M9-criteria.md": crit("M9", handoff="M9 does the thing,\nwrapped.")}),
    ("2026-11-02T11:00:00-05:00", {"M9-build.md": "# M9 build\n"}),
    ("2026-11-02T12:00:00-05:00", {"M1-criteria.md": crit("M1")}),
]


def test_live_agents_through_a_fake_herdr(we, tmp_path, path_with):
    tree = make_tree(tmp_path, UNDER_WAY_TREE)
    other = tmp_path / "other-repo"
    other.mkdir()
    path_with(herdr_bin(tmp_path, [
        agent(None, "working", tree),                      # the coordinator's pane
        agent("m9-test", "working", tree),
        agent("m9-build", "done", tree),                   # finished, workspace open
        agent("m1-fix1", "working", tree),                 # its item is promoted
        agent("m77-build", "working", tree),               # no such item
        agent("investigator", "idle", tree),               # name not <item>-<role>
        agent("m9-review", "working", other),              # another repo: counted only
    ]))
    out = we.collect_legacy(tree)
    m9 = by_id(out)["M9"]
    assert out["under_way"] == ["M9"]
    assert m9["stage"]["detail"] == "testing, agent m9-test working"
    assert m9["notes"] == ["m9-build: done, workspace still open"]
    assert m9["handoff"] == "M9 does the thing, wrapped."
    assert out["herdr"]["known"] and out["herdr"]["seen"] == 7 and out["herdr"]["elsewhere"] == 1
    assert out["herdr"]["coordinator"]["word"] == "working"
    others = {a["name"]: (a["status"], a["why"]) for a in out["other_agents"]}
    assert others == {"m1-fix1": ("working", "its item is promoted"),
                      "m77-build": ("working", "no item of that name"),
                      "investigator": ("idle", "name is not <item>-<role>")}
    assert "m9-review" not in json.dumps(out)


@pytest.mark.parametrize("status,word", [("working", "working"), ("done", "idle"), ("idle", "idle"),
                                         ("blocked", "blocked"), ("unknown", "unknown")])
def test_coordinator_word(we, tmp_path, path_with, status, word):
    tree = make_tree(tmp_path, UNDER_WAY_TREE)
    path_with(herdr_bin(tmp_path, [agent(None, status, tree)]))
    assert we.collect_legacy(tree)["herdr"]["coordinator"]["word"] == word


def test_zero_agents_is_not_the_same_as_not_known(we, tmp_path, path_with):
    tree = make_tree(tmp_path, UNDER_WAY_TREE)
    path_with(herdr_bin(tmp_path, []))
    out = we.collect_legacy(tree)
    assert out["herdr"]["known"] is True and out["herdr"]["seen"] == 0
    assert out["herdr"]["coordinator"]["word"] == "not seen"
    assert by_id(out)["M9"]["stage"]["detail"] == "build in, not tested"


def test_two_unnamed_panes_make_the_coordinator_not_known(we, tmp_path, path_with):
    tree = make_tree(tmp_path, UNDER_WAY_TREE)
    path_with(herdr_bin(tmp_path, [agent(None, "working", tree), agent(None, "done", tree)]))
    c = we.collect_legacy(tree)["herdr"]["coordinator"]
    assert c["word"] == "not known" and "2 unnamed agents" in c["reason"]


@pytest.mark.parametrize("how", ["absent", "json_error", "not_json", "no_list"])
def test_herdr_unreachable_means_agent_state_not_known(we, tmp_path, path_with, how):
    tree = make_tree(tmp_path, UNDER_WAY_TREE)
    if how == "json_error":
        path_with(herdr_bin(tmp_path, rc=1, body='{"error":{"code":"server_not_running","message":"/x/y.sock"}}'))
    elif how == "not_json":
        path_with(herdr_bin(tmp_path, rc=0, body="herdr: something went wrong"))
    elif how == "no_list":
        path_with(herdr_bin(tmp_path, rc=0, body='{"result":{}}'))
    out = we.collect_legacy(tree)
    h = out["herdr"]
    assert h["known"] is False and h["seen"] is None and h["coordinator"]["word"] == "not known"
    assert by_id(out)["M9"]["stage"]["by"] == "files"
    if how == "json_error":
        assert h["reason"] == "herdr agent list: server_not_running (exit 1)"
        assert "/x/y.sock" not in json.dumps(out)
    if how == "absent":
        assert h["reason"] == "herdr is not on PATH"


def test_hung_herdr_is_cut_at_5_s_and_the_file_is_written_within_10(tmp_path):
    tree = make_tree(tmp_path, UNDER_WAY_TREE)
    db = import_db(tree, tmp_path)
    bindir = herdr_bin(tmp_path, [], sleep=30)
    out = tmp_path / "out" / "work.json"
    env = dict(os.environ, PATH=f"{bindir}:{BASE_PATH}")
    t0 = time.monotonic()
    subprocess.run([sys.executable, "-B", str(SCRIPT), "--repo", str(tree), "--db", str(db), "--out", str(out)],
                   env=env, check=True, timeout=20)
    took = time.monotonic() - t0
    data = json.loads(out.read_text())
    assert 4.5 <= took < 10
    assert data["herdr"]["known"] is False
    assert data["herdr"]["reason"] == "herdr agent list: no answer within 5 s"
    assert by_id(data)["M9"]["stage"]["detail"] == "build in, not tested"


def test_only_name_and_status_leave_herdr(we, tmp_path, path_with):
    tree = make_tree(tmp_path, UNDER_WAY_TREE)
    path_with(herdr_bin(tmp_path, [
        agent("m9-test", "working", tree, terminal_title=f"◐ {MARKER} title",
              agent_session={"value": f"{MARKER}-sess"}, foreground_cwd=f"/home/{MARKER}"),
        agent("m9-fix1", "idle", f"/home/{MARKER}/elsewhere"),
    ]))
    out = we.collect_legacy(tree)
    assert MARKER not in json.dumps(out)
    assert by_id(out)["M9"]["agents"] == [{"name": "m9-test", "status": "working", "role": "test"}]


# ── criterion 5: the backlog ──────────────────────────────────────────────────

def test_backlog_sections_and_entries(we):
    nr = []
    secs = we.parse_roadmap(ROADMAP, nr)
    assert [s["name"] for s in secs] == ["Now — in flight", "Next — agreed, not started", "Later — wanted",
                                         "Chores", "Shipped"]
    titles = {s["name"]: [e["title"] for e in s["entries"]] for s in secs}
    assert titles["Now — in flight"] == ["One-line entry", "Wrapped lead"]
    assert titles["Next — agreed, not started"] == ["`/work`: a title with backticks", "Nested list", "Old numbering"]
    assert titles["Later — wanted"] == ["Later entry"] and titles["Chores"] == ["A chore"]
    assert "Admission:" not in json.dumps(secs)                    # the Conventions bullets
    now = secs[0]["entries"]
    assert now[0]["text"] == "**One-line entry** *(work item M9, ordered 2026-11-01)* — on one line."
    assert now[1]["lead"] == "sprint M7, bench run"
    nested = secs[1]["entries"][1]
    assert nested["text"] == ("**Nested list** *(admitted 2026-11-24)* — in this order: "
                              "1. **First.** one. 2. **Second.** two.")
    assert nr == []


def test_paragraph_at_column_0_after_a_blank_line_ends_an_entry(we):
    text = "## Now — in flight\n\n- **A** — one.\n\nA loose paragraph.\n- **B** — two.\n"
    (s,) = we.parse_roadmap(text, [])
    assert [e["text"] for e in s["entries"]] == ["**A** — one.", "**B** — two."]


def test_unknown_section_is_not_exported_and_is_named(we):
    nr = []
    secs = we.parse_roadmap("## Parked\n\n- **X** — secret plans.\n", nr)
    assert secs == [] and nr == [{"file": "ROADMAP.md", "why": "section 'Parked' is not a known section"}]


def test_shipped_entries_carry_title_and_lead_only(we):
    secs = we.parse_roadmap(ROADMAP, [])
    shipped = secs[-1]["entries"]
    assert all("text" not in e for e in shipped)
    assert [(e["title"], e["lead"]) for e in shipped] == [
        ("Backups", "2026-11-20, sprint M2 and M2b"), ("Split item", "2026-11-18, sprint M5"),
        ("Old shipped thing", "item 1, 2026-07-05"), ("Plain paren", None)]


# ── criterion 7: links ───────────────────────────────────────────────────────

def test_links_only_inside_the_leading_lead(we):
    secs = we.parse_roadmap(ROADMAP, [])
    links = {e["title"]: e["links"] for s in secs for e in s["entries"]}
    assert links["One-line entry"] == ["M9"]
    assert links["Wrapped lead"] == ["M7"]                       # "sprint\n  M7"
    assert links["`/work`: a title with backticks"] == ["M8"]    # "work item\n  M8; after M7"
    assert links["Old numbering"] == []                          # body mentions, "item 11"
    assert links["Nested list"] == [] and links["Later entry"] == []
    assert links["Backups"] == ["M2", "M2b"]                     # "sprint M2 and M2b"
    assert links["Old shipped thing"] == []


def test_a_bare_step_id_links_both_steps(we):
    assert we.resolve_links(["M5"], ["M5-step1", "M5-step2", "M50"]) == ["M5-step1", "M5-step2"]
    assert we.resolve_links(["M2", "M2b"], ["M2", "M2b", "M20"]) == ["M2", "M2b"]


DONE_TREE = [
    ("2026-11-02T10:00:00-05:00", {"M1-criteria.md": crit("M1", "M1 pass criteria: first")}),
    ("2026-11-02T11:00:00-05:00", {"M2-criteria.md": crit("M2"), "M2b-criteria.md": crit("M2b")}),
    ("2026-11-02T12:00:00-05:00", {"M3-criteria.md": crit("M3"), "M4-criteria.md": crit("M4"),
                                   "M5-step1-criteria.md": crit("M5-step1"), "M6-criteria.md": crit("M6")}),
    ("2026-11-02T13:00:00-05:00", {"M4-e2e.md": report("Verdict: PASSES", "M4")}),
    ("2026-11-03T09:00:00-05:00", {"M5-step2-criteria.md": crit("M5-step2"),
                                   "M7-criteria.md": crit("M7"), "M8-criteria.md": crit("M8")}),
]


def test_open_entry_linked_to_an_item_under_way_moves_to_it(we, tmp_path, path_with):
    tree = make_tree(tmp_path, DONE_TREE)
    out = we.collect_legacy(tree)
    assert out["under_way"] == ["M5-step2", "M7", "M8"]
    items = by_id(out)
    assert [e["title"] for e in items["M7"]["roadmap"]] == ["Wrapped lead"]
    assert [e["title"] for e in items["M8"]["roadmap"]] == ["`/work`: a title with backticks"]
    backlog = [e["title"] for s in out["backlog"] for e in s["entries"]]
    # "One-line entry" links M9, which is not under way: it stays.
    assert backlog == ["One-line entry", "Nested list", "Old numbering", "Later entry", "A chore"]


# ── criterion 6: done ────────────────────────────────────────────────────────

def test_done_is_promotions_in_reverse_file_order(we, tmp_path, path_with):
    tree = make_tree(tmp_path, DONE_TREE)
    out = we.collect_legacy(tree)
    got = [(r["id"], r["tag"], r["date"], r["for_drew"], r["for_drew_total"], [n["heading"] for n in r["notes"]])
           for r in out["done"]]
    assert got == [
        ("M4", "item-M4", "2026-11-21", 2, 3, ["M4, a later note (2026-11-22)"]),
        ("M2b", None, "2026-11-20", 0, 0, []),
        ("M6", "sprint-M6", "2026-11-18", 0, 0, []),
        ("M5-step1", "sprint-M5-step1", "2026-11-17 07:22 UTC", 2, 2, []),
        ("M3", None, "2026-11-17", 0, 0, []),
        ("M2", "sprint-M2-M1", "2026-11-16 00:02 UTC", 0, 0, []),
        ("M1", "sprint-M2-M1", "2026-11-16 00:02 UTC", 0, 0, []),     # M1's second promotion
        ("M1", "sprint-M1", "2026-11-15", 1, 1, []),
    ]
    rows = {(r["id"], r["line"]): r for r in out["done"]}
    m3 = next(r for r in out["done"] if r["id"] == "M3")
    assert m3["tag_written"] == "none" and m3["heading"] == "M3 (gate only)"
    m6 = next(r for r in out["done"] if r["id"] == "M6")
    assert m6["tag_only"] is True
    assert rows[("M2b", 15)]["kind"] == "heading" and rows[("M2b", 15)]["heading"].startswith("M2b (2026-11-20)")
    # A heading that starts with no id is no entry.
    assert all("After the sprint" not in (r["heading"] or "") for r in out["done"])
    assert {it["id"] for it in out["items"] if it["stage"]["stage"] == "promoted"} == \
        {"M1", "M2", "M2b", "M3", "M4", "M5-step1", "M6"}


def test_linked_shipped_entries_go_on_the_items_row_not_below(we, tmp_path, path_with):
    tree = make_tree(tmp_path, DONE_TREE)
    out = we.collect_legacy(tree)
    items = by_id(out)
    assert [s["title"] for s in items["M2"]["shipped"]] == ["Backups"]
    assert [s["title"] for s in items["M2b"]["shipped"]] == ["Backups"]
    assert [s["title"] for s in items["M5-step1"]["shipped"]] == ["Split item"]
    assert [s["title"] for s in items["M5-step2"]["shipped"]] == ["Split item"]
    assert [e["title"] for e in out["shipped_unlinked"]] == ["Old shipped thing", "Plain paren"]


def test_for_drew_counts(we):
    entries = we.parse_promotions(PROMOTIONS)
    counts = {(tuple(e["ids"]), e["line"]): e["for_drew"] for e in entries}
    assert counts == {(("M1",), 5): 1, (("M2", "M1"), 6): 0, (("M3",), 7): 0, (("M5-step1",), 8): 2,
                      (("M6",), 9): 0, (("M2b",), 15): 0, (("M4",), 19): 2, (("M4",), 25): 1}
    note = [e for e in entries if e["line"] == 25][0]
    assert note["note"] is True and note["note_on"] == ["M4"]


# ── criterion 8: nothing but the outline leaves the repo ──────────────────────

def test_marker_in_every_body_is_nowhere_in_the_export(we, tmp_path, path_with):
    M = MARKER
    roadmap = ROADMAP.replace("shipped body text.", f"shipped body {M}.").replace("an old body.", f"old {M}.")
    promotions = (PROMOTIONS.replace("| changed. | For Drew: one. |", f"| {M} cell four | {M} cell five |")
                  .replace("- body line.", f"- {M} heading body.")
                  .replace("- note body.", f"- {M} note body.")
                  .replace("- Tag `item-M4` (`1733285`). deployed.", f"- Tag `item-M4` (`1733285`). {M}."))
    tree = make_tree(tmp_path, [
        ("2026-11-02T10:00:00-05:00", {"M9-criteria.md": f"# M9 pass criteria\n\n{M} criteria body\n\n"
                                                         f"Handoff line: the handoff.\n\n{M} more\n"}),
        ("2026-11-02T11:00:00-05:00", {"M9-build.md": f"# M9 build\n\n{M} build body\n",
                                       "M9-e2e.md": f"# M9 test\n\nVerdict: FAILS {M}\n{M} report body\n",
                                       "M9-review.md": f"# M9 review\n\n{M}\n"}),
        ("2026-11-02T12:00:00-05:00", {"M6-audit.md": f"# M6 audit\n\n{M}\n", "M5-change1.md": f"# change\n{M}\n",
                                       "M9-e2e2-rows.md": f"# rows\n{M}\n", "M9-fix1.md": f"# fix\n{M}\n"}),
    ], roadmap=roadmap, promotions=promotions)
    path_with(herdr_bin(tmp_path, [agent("m9-fix1", "working", tree, terminal_title=M,
                                         agent_session={"value": M})]))
    text = json.dumps(we.collect_legacy(tree), ensure_ascii=False)
    assert M not in text
    assert "the handoff." in text and "M9 pass criteria" in text


def _strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)


def leaked_lines(repo: Path, export: dict) -> list[str]:
    """Lines of 20 characters or more from an item file's body,
    PROMOTIONS.md's body or a Shipped entry's body that appear in the
    export, outside the fields the outline carries on purpose."""
    # What is exported by design is left out of the blob: titles, handoff
    # lines (a promotions entry opens with its item's handoff line, so the
    # same words are in PROMOTIONS.md's body), leads, and the open entries,
    # which are exported whole ("text", and "fold", the same text in two
    # parts). The rest is checked.
    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items()
                    if k not in ("title", "short_title", "handoff", "source", "lead", "text", "fold")}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x
    blob = "\n".join(_strings(strip(export)))
    sprint = repo / "docs" / "sprint"
    candidates: list[str] = []
    for p in sorted(sprint.glob("M*.md")):
        lines = p.read_text(errors="replace").splitlines()
        in_handoff = False
        for i, line in enumerate(lines):
            if line.startswith("Handoff line:"):
                in_handoff = True
            elif in_handoff and not line.strip():
                in_handoff = False
            if i == 0 or in_handoff:
                continue
            candidates.append(line)
    for line in (sprint / "PROMOTIONS.md").read_text().splitlines():
        if line.startswith("#"):
            continue
        if line.startswith("|"):
            candidates.extend(line.split("|")[4:])
        candidates.append(line)
    shipped = (repo / "ROADMAP.md").read_text().split("\n## Shipped", 1)[1]
    candidates.extend(shipped.splitlines()[1:])
    return sorted({c.strip() for c in candidates if len(c.strip()) >= 20 and c.strip() in blob})


# ── criterion 9: safe to run every minute ─────────────────────────────────────

def test_git_commands_are_the_three_and_lockless(we, tmp_path, path_with, monkeypatch):
    tree = make_tree(tmp_path, UNDER_WAY_TREE)
    calls = []
    real = we.run

    def spy(cmd, cwd, timeout, env=None):
        calls.append((cmd, env))
        return real(cmd, cwd, timeout, env)
    monkeypatch.setattr(we, "run", spy)
    we.collect_legacy(tree)
    gits = [c for c in calls if c[0][0] == "git"]
    assert {c[0][2] for c in gits} == {"rev-parse", "log", "for-each-ref"}
    for cmd, env in gits:
        assert cmd[1] == "--no-optional-locks" and env["GIT_OPTIONAL_LOCKS"] == "0"
        assert not {"status", "diff", "add", "stash", "commit", "gc"} & set(cmd)
    (log,) = [c[0] for c in gits if c[0][2] == "log"]
    assert log[2:] == ["log", "--no-renames", "--diff-filter=A", "--name-only", "--format=@%H%x09%aI",
                       "--reverse", "--", "docs/sprint"]


@pytest.mark.skipif(not shutil.which("strace"), reason="strace not installed")
def test_under_strace_no_lock_no_other_write_no_socket(tmp_path):
    tree = make_tree(tmp_path, UNDER_WAY_TREE)
    db = import_db(tree, tmp_path)
    bindir = herdr_bin(tmp_path, [agent("m9-test", "working", tree)])
    outdir = tmp_path / "out"
    (outdir / "dev").mkdir(parents=True)
    trace = tmp_path / "trace.txt"
    env = dict(os.environ, PATH=f"{bindir}:{BASE_PATH}")
    r = subprocess.run(["strace", "-f", "-qq", "-o", str(trace), "-e",
                        "trace=openat,open,creat,socket,connect,rename,renameat,renameat2,unlink,unlinkat,mkdir,mkdirat",
                        sys.executable, "-B", str(SCRIPT), "--repo", str(tree), "--db", str(db),
                        "--out", str(outdir / "work.json"), "--out", str(outdir / "dev" / "work.json")],
                       env=env, capture_output=True, text=True, timeout=30)
    if r.returncode != 0 and "ptrace" in (r.stderr or ""):
        pytest.skip("ptrace not permitted here")
    assert r.returncode == 0, r.stderr
    lines = trace.read_text().splitlines()
    assert not [l for l in lines if '.lock"' in l]
    writes = [l for l in lines if ("O_WRONLY" in l or "O_RDWR" in l or "O_CREAT" in l) and "= -1" not in l]
    allowed = (f'"{outdir}/.work.json.', f'"{outdir}/dev/.work.json.', '"/dev/null"')
    assert all(any(a in l for a in allowed) for l in writes), [l for l in writes if not any(a in l for a in allowed)]
    renames = [l for l in lines if "rename" in l and "= 0" in l]
    assert len(renames) == 2
    assert not [l for l in lines if l.split(" ", 1)[-1].startswith(("socket(", "connect("))]
    assert not [l for l in lines if "unlink" in l and "= 0" in l]
    # M34 criterion 5: in the database's directory only the dump is opened, read-only.
    beside = [l for l in lines if f'"{db.parent}/' in l and "= -1" not in l]
    assert beside and all(f'"{db}.dump.sql"' in l and "O_RDONLY" in l for l in beside), beside
    assert json.loads((outdir / "work.json").read_text())["under_way"] == ["M9"]


KILL_CHILD = r"""
import importlib.util, json, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("m", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
target, marker = Path(sys.argv[2]), Path(sys.argv[3])
def slow(data, f, **kw):
    f.write('{"schema": 1, "ts": 2, "half')
    f.flush()
    marker.write_text("mid-write")
    time.sleep(60)
m.json.dump = slow
m.write(target, {"ts": 2})
"""


def test_kill_in_the_middle_of_a_write_leaves_the_old_file_whole(tmp_path):
    target = tmp_path / "work.json"
    old = '{"schema": 1, "ts": 1, "items": []}'
    target.write_text(old)
    marker = tmp_path / "marker"
    child = subprocess.Popen([sys.executable, "-I", "-c", KILL_CHILD, str(SCRIPT), str(target), str(marker)])
    try:
        deadline = time.time() + 20
        while not marker.exists():
            assert child.poll() is None, "child exited before the write"
            assert time.time() < deadline, "child never reached the write"
            time.sleep(0.02)
        os.kill(child.pid, signal.SIGKILL)
        child.wait(10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(10)
    assert child.returncode == -signal.SIGKILL
    assert target.read_text() == old


def test_does_not_fail_on_bad_files_and_lists_untracked(we, tmp_path, path_with):
    tree = make_tree(tmp_path, [
        ("2026-11-02T10:00:00-05:00", {"M9-criteria.md": "no title line here\n\nbody\n"}),
        ("2026-11-02T11:00:00-05:00", {"M9-build.md": "", "M9-e2e.md": b"\x00\x01\xff binary\x00",
                                       "notes.md": "# not an item\n"}),
    ], untracked={"M9-fix1.md": "# M9 fix 1\n", "M10-criteria.md": "# M10 pass criteria\n"})
    out = we.collect_legacy(tree)
    m9 = by_id(out)["M9"]
    assert [s["file"] for s in m9["steps"]] == ["M9-criteria.md", "M9-build.md", "M9-e2e.md"]
    assert [(s["file"], s["committed_at"]) for s in m9["uncommitted"]] == [("M9-fix1.md", None)]
    assert m9["title"] == "M9 fix 1"                      # the criteria file has no title line
    why = {n["file"]: n["why"] for n in out["not_read"]}
    assert why["M9-criteria.md"] == "no title line"
    assert why["M9-build.md"] == "empty"
    assert why["M9-e2e.md"] == "binary"
    assert why["notes.md"] == "not an item file name"
    assert "M10" in out["under_way"]
    m10 = by_id(out)["M10"]
    assert m10["steps"] == [] and m10["stage"]["detail"] == "files written, not committed"


def test_missing_roadmap_and_promotions_are_named_not_fatal(we, tmp_path, path_with):
    tree = make_tree(tmp_path, UNDER_WAY_TREE)
    (tree / "ROADMAP.md").unlink()
    (tree / "docs" / "sprint" / "PROMOTIONS.md").write_bytes(b"\x00binary")
    out = we.collect_legacy(tree)
    why = {n["file"]: n["why"] for n in out["not_read"]}
    assert why["ROADMAP.md"] == "missing" and why["PROMOTIONS.md"] == "binary"
    assert out["backlog"] == [] and out["done"] == []
    assert out["under_way"] == ["M1", "M9"]


def test_detached_head_and_rebase_in_progress(we, tmp_path, path_with):
    tree = make_tree(tmp_path, UNDER_WAY_TREE)
    git(tree, "checkout", "-q", "--detach", "HEAD~1")      # before M1-criteria.md
    (tree / ".git" / "rebase-merge").mkdir()
    out = we.collect_legacy(tree)
    assert out["repo"]["detached"] is True and out["repo"]["branch"] is None
    assert out["repo"]["rebase"] is True and len(out["repo"]["head"]) >= 7
    m1 = by_id(out)["M1"]                                  # named by PROMOTIONS.md only
    assert m1["steps"] == [] and m1["uncommitted"] == []
    assert [s["file"] for s in by_id(out)["M9"]["steps"]] == ["M9-criteria.md", "M9-build.md"]


def test_not_a_git_repo_still_writes(we, tmp_path, path_with):
    tree = tmp_path / "plain"
    (tree / "docs" / "sprint").mkdir(parents=True)
    (tree / "docs" / "sprint" / "M9-criteria.md").write_text("# M9\n")
    out = we.collect_legacy(tree)
    assert out["repo"]["error"] and out["errors"]
    assert by_id(out)["M9"]["uncommitted"][0]["file"] == "M9-criteria.md"


def test_default_outputs_are_data_and_data_dev(we, tmp_path, path_with, monkeypatch):
    tree = make_tree(tmp_path, UNDER_WAY_TREE)
    written = []
    monkeypatch.setattr(we, "write", lambda p, d: written.append(p))
    assert we.main(["--repo", str(tree)]) == 0
    assert written == [tree / "data" / "work.json", tree / "data" / "dev" / "work.json"]
    assert we.SCHEMA == 3 and we.HERDR_TIMEOUT_S == 5          # 3 since M32 step 1 (house_zone)


# ── the real tree (read only; herdr left off PATH) ────────────────────────────
# Since M34 the exporter reads a database: the real tree's files are
# imported at HEAD into a scratch one (work.py import-legacy, no seed), and
# the exporter reads its dump. The live database and its dump are never read.

@pytest.fixture(scope="module")
def real(tmp_path_factory):
    if not (REPO / ".git").exists():
        pytest.skip("not a git working tree")
    db = import_db(REPO, tmp_path_factory.mktemp("m27-real"))
    env = dict(os.environ, PATH=BASE_PATH)
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, "-B", str(SCRIPT), "--repo", str(REPO), "--db", str(db), "--print"],
                       env=env, capture_output=True, text=True, timeout=30, check=True)
    return json.loads(r.stdout), time.monotonic() - t0


def test_real_tree_runs_under_1_s(real):
    data, took = real
    assert took < 1.0 and data["run_s"] < 0.5
    assert data["errors"] == [] and data["database"]["state"] == "ok"


def test_real_tree_every_test_report_has_a_verdict(real):
    data, _ = real
    tests = [s for it in data["items"] for s in it["steps"] if s["kind"] == "test"]
    assert len(tests) >= 49
    assert [s["file"] for s in tests if not s["verdicts"]] == []
    # One verdict per step since M34: the item's own (the second subject is prose in the row's note).
    m4b = {s["file"]: s for s in by_id(data)["M4b"]["steps"]}["M4b-e2e.md"]
    assert [(v["subject"], v["verdict"]) for v in m4b["verdicts"]] == [("item", "FAILS")]
    rows = {s["file"]: s for s in by_id(data)["M3"]["steps"]}["M3-e2e2-rows.md"]
    assert (rows["kind"], rows["name"]) == ("other", "e2e2-rows")


def test_real_tree_outline_only(real):
    data, _ = real
    assert leaked_lines(REPO, data) == []


# ── fix round 1 (docs/sprint/M27-fix1.md) ─────────────────────────────────────

# S1: an agent herdr describes in a shape the exporter cannot read is
# counted and skipped, and the file is still written.

@pytest.mark.parametrize("bad", [{"agent_status": ["working"]}, {"agent_status": {"s": "working"}},
                                 {"cwd": "/home/x\u0000y"}, {"cwd": ["/x"]}])
def test_an_agent_in_a_shape_not_read_is_counted_and_skipped(tmp_path, bad):
    tree = make_tree(tmp_path, UNDER_WAY_TREE)
    odd = agent("m9-fix1", "working", tree)
    odd.update(bad)
    bindir = herdr_bin(tmp_path, [agent(None, "done", tree), agent("m9-test", "working", tree), odd])
    db = import_db(tree, tmp_path)
    out = tmp_path / "out" / "work.json"
    env = dict(os.environ, PATH=f"{bindir}:{BASE_PATH}")
    r = subprocess.run([sys.executable, "-B", str(SCRIPT), "--repo", str(tree), "--db", str(db), "--out", str(out)],
                       env=env, capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    data = json.loads(out.read_text())
    h = data["herdr"]
    assert h["known"] is True and h["seen"] == 3
    if "cwd" in bad and isinstance(bad["cwd"], list):
        assert (h["skipped"], h["elsewhere"]) == (0, 1)        # a cwd that is not a path: elsewhere, as before
    else:
        assert (h["skipped"], h["elsewhere"]) == (1, 0)
    m9 = by_id(data)["M9"]
    assert [a["name"] for a in m9["agents"]] == ["m9-test"]
    assert m9["stage"]["detail"] == "testing, agent m9-test working"
    assert "\u0000" not in out.read_text() and "\\u0000" not in out.read_text()


@pytest.mark.parametrize("a,where", [
    ({"agent_status": ["working"], "cwd": "/r"}, "skipped"), ({"agent_status": {"s": 1}, "cwd": "/r"}, "skipped"),
    ({"agent_status": "working", "cwd": "/r/x\u0000y"}, "skipped"), (["not", "an", "object"], "skipped"),
    ({"agent_status": None, "cwd": "/r"}, "repo"), ({"agent_status": "working", "cwd": 5}, "elsewhere"),
    ({"agent_status": "working", "cwd": "/elsewhere"}, "elsewhere"),
])
def test_herdr_agent_reads_one_entry_without_raising(we, a, where):
    got, rec = we.herdr_agent(a, "/r")
    assert got == where
    assert (rec is not None) == (where == "repo")


def test_an_agent_entry_that_raises_is_skipped(we, tmp_path, path_with, monkeypatch):
    tree = make_tree(tmp_path, UNDER_WAY_TREE)
    path_with(herdr_bin(tmp_path, [agent("m9-test", "working", tree), agent("m9-fix1", "working", tree)]))
    real = we.herdr_agent

    def flaky(a, root):
        if a.get("name") == "m9-fix1":
            raise RuntimeError("a shape not foreseen")
        return real(a, root)
    monkeypatch.setattr(we, "herdr_agent", flaky)
    h = we.collect_legacy(tree)["herdr"]
    assert (h["known"], h["seen"], h["skipped"]) == (True, 2, 1)


def test_a_herdr_reader_that_throws_still_writes_the_file(we, tmp_path, path_with, monkeypatch):
    tree = make_tree(tmp_path, UNDER_WAY_TREE)

    def boom(repo):
        raise TypeError("unhashable type: 'list'")
    monkeypatch.setattr(we, "herdr_agents", boom)
    out = we.collect_legacy(tree)
    assert out["herdr"]["known"] is False and out["herdr"]["coordinator"]["shown"] == "not known"
    assert out["herdr"]["reason"] == "herdr agent list: the answer could not be read (TypeError)"
    assert by_id(out)["M9"]["stage"]["by"] == "files"


# S4: "For Drew" with a line break inside it is counted.

def test_for_drew_broken_across_a_line_is_counted(we):
    text = ("# P\n\n## M9 (a heading entry), 2026-11-21\n\n- **For\n  Drew:** one.\n"
            "- left for\nDrew to decide.\n- waits for\n  Drew's go (not addressed to him).\n")
    (e,) = we.parse_promotions(text)
    assert e["for_drew"] == 2


# S5: inside a heading entry a table is body, not promotions; ids match on
# word boundaries; a row label M30-step1 is that item.

TABLE_IN_ENTRY = """# Promotions

| Item | Tag | Promoted | What changed | Held or left over |
| --- | --- | --- | --- | --- |
| M1 | `sprint-M1` | 2026-11-15 | x | y |
| M33 (see M9) | `sprint-M33` | 2026-11-16 | x | y |

## M2 (a heading entry), 2026-11-21

- Tag `item-M2`.

| Step | What |
| --- | --- |
| M39 (next item) | waits on this one |
| ARM64 build | x |

- **For Drew:** after the table.

## After the sprint (decisions)

| Item | Tag | Promoted | What changed | Held or left over |
| --- | --- | --- | --- | --- |
| M16 (backups) | `sprint-M16` | 2026-11-19 | x | y |
| M30-step1 | `item-M30-step1` | 2026-11-25 | x | y |
| ARM64 build, M31 | `item-M31` | 2026-11-26 | x | y |
"""


def test_a_table_inside_a_heading_entry_is_its_body(we):
    entries = we.parse_promotions(TABLE_IN_ENTRY)
    assert [(e["kind"], e["ids"], e["tag"]) for e in entries] == [
        ("row", ["M1"], "sprint-M1"), ("row", ["M33"], "sprint-M33"), ("heading", ["M2"], "item-M2"),
        ("row", ["M16"], "sprint-M16"), ("row", ["M30-step1"], "item-M30-step1"), ("row", ["M31"], "item-M31")]
    assert entries[2]["for_drew"] == 1                        # the line after the table


def test_an_item_named_in_a_table_inside_an_entry_stays_under_way(we, tmp_path, path_with):
    tree = make_tree(tmp_path, [
        ("2026-11-02T10:00:00-05:00", {"M2-criteria.md": crit("M2"), "M39-criteria.md": crit("M39")}),
    ], promotions=TABLE_IN_ENTRY)
    out = we.collect_legacy(tree)
    assert out["under_way"] == ["M39"]
    ids = set(by_id(out))
    assert "M64" not in ids and "M9" not in ids and {"M30-step1", "M31"} <= ids


# N8: the exporter removes its own temp files older than ten minutes.

def test_its_own_temp_files_older_than_ten_minutes_are_removed(we, tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    old, fresh = d / ".work.json.ab_d1234", d / ".work.json.wxyz5678"
    other = d / ".other.json.abcd1234"            # another file's temp name
    keep = d / ".work.json.keep-me"               # not a name mkstemp makes
    olddir = d / ".work.json.dir12345"            # a directory
    target = tmp_path / "target.txt"
    target.write_text("not ours")
    link = d / ".work.json.lnk12345"              # a symlink: left alone
    for p in (old, fresh, other, keep):
        p.write_text('{"half')
    olddir.mkdir()
    link.symlink_to(target)
    t = time.time() - 11 * 60
    for p in (old, other, keep, olddir, target):
        os.utime(p, (t, t))
    os.utime(link, (t, t), follow_symlinks=False)
    os.utime(fresh, (time.time() - 9 * 60,) * 2)
    we.write(d / "work.json", {"ts": 1})
    assert sorted(p.name for p in d.iterdir()) == sorted(
        [".work.json.wxyz5678", ".other.json.abcd1234", ".work.json.keep-me", ".work.json.dir12345",
         ".work.json.lnk12345", "work.json"])
    assert target.read_text() == "not ours"
    assert json.loads((d / "work.json").read_text()) == {"ts": 1}


# Change 5: the coordinator's word on the state line.

@pytest.mark.parametrize("coord,named,shown", [
    ("working", [], "working"),
    ("working", [("m9-test", "working")], "working"),
    ("done", [("m9-test", "working")], "waiting on an agent"),
    ("idle", [("m9-fix1", "working")], "waiting on an agent"),
    ("done", [("investigator", "working")], "waiting on an agent"),   # any named agent here working
    ("done", [("m9-test", "done")], "idle"),
    ("done", [("m9-test", "blocked")], "idle"),
    ("done", [], "idle"),
    ("idle", [], "idle"),
    ("blocked", [], "idle"),
    ("unknown", [("m9-test", "working")], "not known"),
    (None, [("m9-test", "working")], "not known"),                 # no unnamed pane: not seen
    ("two", [("m9-test", "working")], "not known"),                # two unnamed panes
])
def test_coordinator_shown_word(we, tmp_path, path_with, coord, named, shown):
    tree = make_tree(tmp_path, UNDER_WAY_TREE)
    other = tmp_path / "other-repo"
    other.mkdir()
    agents = [agent(n, s, tree) for n, s in named] + [agent("m9-build", "working", other)]
    if coord == "two":
        agents += [agent(None, "done", tree), agent(None, "done", tree)]
    elif coord is not None:
        agents.append(agent(None, coord, tree))
    path_with(herdr_bin(tmp_path, agents))
    assert we.collect_legacy(tree)["herdr"]["coordinator"]["shown"] == shown


def test_coordinator_shown_word_with_herdr_not_reachable(we, tmp_path, path_with):
    tree = make_tree(tmp_path, UNDER_WAY_TREE)
    assert we.collect_legacy(tree)["herdr"]["coordinator"]["shown"] == "not known"


# Change 3: a Done row names its item once.

@pytest.mark.parametrize("title,item,short", [
    ("M25 pass criteria: /resources shows real usage", "M25", "/resources shows real usage"),
    ("M21 step 2 pass criteria: the `/settings` page", "M21-step2", "the `/settings` page"),
    ("M4b pass criteria: the mirror update", "M4b", "the mirror update"),
    ("M6 audit: what is listening on the Kronk host", "M6", "audit: what is listening on the Kronk host"),
    ("M60 pass criteria: another item", "M6", "M60 pass criteria: another item"),
    ("Something else", "M9", "Something else"),
    (None, "M9", None),
])
def test_short_title(we, title, item, short):
    assert we.short_title(title, item) == short


@pytest.mark.parametrize("text,title,adds", [
    ("M25 (/resources shows real usage), 2026-11-24", "M25 pass criteria: /resources shows real usage", False),
    ("M23 (the queue incident: fixes 1 to 3), 2026-11-21", "M23 pass criteria: the queue incident (fixes 1, 2 and 3)", False),
    ("M21 step 2", "M21 step 2 pass criteria: the `/settings` page", False),
    ("M14 (gate only)", "M14 pass criteria: the messy-transcript gate (gate only)", False),
    ("M3, M4, M4b, M1b restore", "M1b pass criteria: remove the hot tub feature", True),
    ("M8 (line on, streaming off)", "M8 pass criteria: sentence-level streaming on the voice mount", True),
    ("Backups", "M16 pass criteria: backups (config, monthly) and the recordings archive", False),
    ("Backups", "M16b pass criteria: Music Assistant's playlists", True),
    ("The queue incident", "M23 pass criteria: the queue incident (fixes 1, 2 and 3)", False),
    ("M18 (the upgrade cadence)", "M18 pass criteria: an upgrade cadence", False),
    (None, "M9 pass criteria", False),
    ("Anything", None, True),
])
def test_adds_to_title(we, text, title, adds):
    assert we.adds_to(text, title) is adds


def test_done_rows_say_what_adds_something(we, tmp_path, path_with):
    tree = make_tree(tmp_path, DONE_TREE)
    out = we.collect_legacy(tree)
    items = by_id(out)
    rows = {(r["id"], r["line"]): r for r in out["done"]}
    assert rows[("M3", 7)]["heading_adds"] is True             # "M3 (gate only)"
    assert rows[("M1", 5)]["heading_adds"] is False            # "M1"
    assert items["M1"]["short_title"] == "first"
    assert [s["adds"] for s in items["M2"]["shipped"]] == [True]   # "Backups" beside "M2 pass criteria"


# S6 and change 1: a backlog entry folds to its title, lead and first sentence.

@pytest.mark.parametrize("text,head,rest", [
    ("**One-line entry** *(work item M9, ordered 2026-11-01)* — on one line.",
     "**One-line entry** *(work item M9, ordered 2026-11-01)* — on one line.", None),
    ("**A** — 2026.5.4 to 2026.11.2, the first. Waits for `docs/x.md`. More.",
     "**A** — 2026.5.4 to 2026.11.2, the first.", "Waits for `docs/x.md`. More."),
    ("**A** — see `a. b` here. Then.", "**A** — see `a. b` here.", "Then."),
    ("**A** — in this order: 1. **First.** one. 2. two.", "**A** — in this order: 1. **First.**", "one. 2. two."),
    ("**A** — known: \"stop\" → \"What's up?\", then more. Rest.", "**A** — known: \"stop\" → \"What's up?\", then more.", "Rest."),
    ("**A** — *Why: one. two.* Three.", "**A** — *Why: one. two.*", "Three."),
    ("**A** *(lead. with a stop)* — body. Rest.", "**A** *(lead. with a stop)* — body.", "Rest."),
    ("**A** *(ordered 2026-11-24. after M26.)* — body. Rest.", "**A** *(ordered 2026-11-24. after M26.)* — body.", "Rest."),
    ("**A** — [a.b](https://x.example/a.b) done. Rest.", "**A** — [a.b](https://x.example/a.b) done.", "Rest."),
])
def test_fold_of_an_open_entry(we, text, head, rest):
    f = we.fold(text)
    assert (f["head"], f["rest"]) == (head, rest)
    assert text == (f["head"] if f["rest"] is None else f"{f['head']} {f['rest']}")


def test_fold_of_a_long_sentence_is_cut_at_a_space(we):
    text = "**A** — " + " ".join(["word"] * 200) + "."
    f = we.fold(text)
    assert len(f["head"]) <= we.FOLD_MAX and f["head"].endswith("word") and f["rest"]
    assert text == f"{f['head']} {f['rest']}"


def test_open_entries_carry_their_fold_and_shipped_do_not(we):
    secs = we.parse_roadmap(ROADMAP, [])
    nested = secs[1]["entries"][1]
    assert nested["fold"] == {"head": "**Nested list** *(admitted 2026-11-24)* — in this order: 1. **First.**",
                              "rest": "one. 2. **Second.** two."}
    assert all("fold" not in e for e in secs[-1]["entries"])


# S3: an open entry linked to a promoted item stays in the backlog.

def test_open_entry_linked_to_a_promoted_item_stays_in_the_backlog(we, tmp_path, path_with):
    roadmap = ROADMAP.replace("*(work item M9, ordered 2026-11-01)*", "*(work item M1, ordered 2026-11-01)*")
    tree = make_tree(tmp_path, DONE_TREE, roadmap=roadmap)
    out = we.collect_legacy(tree)
    assert by_id(out)["M1"]["stage"]["stage"] == "promoted"
    (now,) = [s for s in out["backlog"] if s["section"] == "Now — in flight"]
    assert [(e["title"], e["links"]) for e in now["entries"]] == [("One-line entry", ["M1"])]
    assert by_id(out)["M1"]["roadmap"] == []


def test_real_tree_tts_trial_stays_in_the_backlog(real):
    data, _ = real
    entries = [e for s in data["backlog"] for e in s["entries"]]
    (tts,) = [e for e in entries if e["title"] == "Kronk's voice: TTS trial"]
    assert tts["links"] == ["M20"] and by_id(data)["M20"]["stage"]["stage"] == "promoted"


def test_real_tree_done_rows_name_the_item_once(real):
    data, _ = real
    items = by_id(data)
    m25 = next(r for r in data["done"] if r["id"] == "M25")
    assert m25["heading_adds"] is False and [s["adds"] for s in items["M25"]["shipped"]] == [False]
    assert items["M25"]["short_title"] == "/resources shows real usage"
    # No promotions heading since M34: the row's title is the item's name.
    assert {(r["heading"], r["heading_adds"]) for r in data["done"]} == {(None, False)}
    assert len([r for r in data["done"] if r["id"] == "M1b"]) == 2
