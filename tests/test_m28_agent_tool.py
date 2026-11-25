"""scripts/agent.py, the coordinator's tool for work-item agents (work item
M28, docs/sprint/M28-criteria.md).

These pin: the name rule against /work's own parser (criterion 2), every
launch refusal and the guard rails section (3), status and wait on a
temporary repo through each condition (4), that status takes no git lock
(5), that the brief's text is kept in one file only (6), and the launch,
prompt, peek, close and list rows of the plumbing table.

Repos are temporary git repositories (user set per repo). herdr is a fake
Python script on PATH that keeps its state in a JSON file beside it and
writes its errors to stderr, as the real one does. No socket is opened.
"""
import importlib.util
import io
import json
import re
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("no_network")

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "agent.py"
BASE_PATH = "/usr/bin:/bin"
MARKER = "QQ-BRIEF-MARKER-5512"
GUARD = """## Guard rails in every brief

Agents run with permissions skipped, so the brief is the only fence.

- Rail one.
- Rail two, `in full`.
"""
WORKFLOW = "# Workflow\n\nIntro.\n\n" + GUARD + "\n## Git\n\nAfter the rails.\n"
MUTATING = {("workspace", "create"), ("workspace", "close"), ("agent", "start"), ("agent", "prompt")}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def at():
    return _load("m28_agent_tool", SCRIPT)


@pytest.fixture
def we():
    return _load("m28_work_export", REPO / "scripts" / "work_export.py")


# ── fixtures: a temporary repo, a fake herdr ────────────────────────────────

def git(tree: Path, *args):
    e = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", HOME=str(tree.parent))
    return subprocess.run(["git", *args], cwd=tree, env=e, check=True, capture_output=True, text=True)


def commit(tree: Path, files: dict, msg="step"):
    for name, text in files.items():
        p = tree / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        git(tree, "add", name)
    git(tree, "commit", "-q", "-m", msg)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A temporary repo, and a scratch work database (M33) that describes it
    and holds item M9, active: agent.py records steps there."""
    tree = tmp_path / "tree"
    tree.mkdir()
    git(tree, "init", "-q", "-b", "main")
    git(tree, "config", "user.name", "Fixture")
    git(tree, "config", "user.email", "fixture@example.invalid")
    commit(tree, {"docs/sprint/WORKFLOW.md": WORKFLOW, "README.md": "x\n"}, "base")
    db = tmp_path / "work" / "work.db"
    monkeypatch.setenv("KRONK_WORK_DB", str(db))
    wk = _load("m28_work_tool", REPO / "scripts" / "work.py")
    for argv in (["init"], ["add", "--id", "M9", "--title", "fixture item"], ["start", "M9"]):
        assert wk.main(["--db", str(db), "--repo", str(tree), *argv], out=io.StringIO()) == 0, argv
    return tree


FAKE = r'''#!{python}
import json, os, sys
D = os.path.dirname(os.path.abspath(__file__))
SP = os.path.join(D, "state.json")
st = json.load(open(SP))
args = sys.argv[1:]
st["calls"].append(args)
def save():
    json.dump(st, open(SP, "w"))
def ok(res):
    save(); print(json.dumps({{"id": "cli:" + ":".join(args[:2]), "result": res}})); sys.exit(0)
def err(code):
    save()
    print(json.dumps({{"error": {{"code": code, "message": "target " + " ".join(args[2:3]) + " x"}},
                      "id": "cli:" + ":".join(args[:2])}}), file=sys.stderr)
    sys.exit(1)
def agent(name):
    for a in st["agents"]:
        if a.get("name") == name:
            return a
    return None
key = " ".join(args[:2])
if key in st["fail"]:
    err(st["fail"][key])
if key == "agent list":
    ok({{"agents": st["agents"], "type": "agent_list"}})
if key == "workspace list":
    ok({{"workspaces": st["workspaces"], "type": "workspace_list"}})
if key == "workspace create":
    st["n"] += 1
    ws = "w%d" % st["n"]
    label = args[args.index("--label") + 1]
    cwd = args[args.index("--cwd") + 1]
    st["workspaces"].append({{"workspace_id": ws, "label": label, "cwd": cwd}})
    ok({{"workspace": {{"workspace_id": ws, "label": label}}, "tab": {{"tab_id": ws + ":t1"}},
        "root_pane": {{"pane_id": ws + ":p1"}}, "type": "workspace_created"}})
if key == "workspace get":
    for w in st["workspaces"]:
        if w["workspace_id"] == args[2]:
            ok({{"workspace": w}})
    err("workspace_not_found")
if key == "workspace close":
    ws = args[2]
    if not any(w["workspace_id"] == ws for w in st["workspaces"]):
        err("workspace_not_found")
    if not st.get("close_noop"):
        st["workspaces"] = [w for w in st["workspaces"] if w["workspace_id"] != ws]
        st["agents"] = [a for a in st["agents"] if a.get("workspace_id") != ws]
    ok({{"type": "ok"}})
if key == "agent start":
    name, pane = args[2], args[args.index("--pane") + 1]
    ws = pane.split(":")[0]
    w = [w for w in st["workspaces"] if w["workspace_id"] == ws]
    if not w:
        err("pane_not_found")
    st["agents"].append({{"agent": "claude", "name": name, "agent_status": "idle", "pane_id": pane,
                         "workspace_id": ws, "cwd": w[0]["cwd"]}})
    ok({{"agent": agent(name)}})
if key == "agent get":
    a = agent(args[2])
    if a is None:
        err("agent_not_found")
    seq = st["get_seq"].get(args[2])
    if seq:
        a["agent_status"] = seq.pop(0) if len(seq) > 1 else seq[0]
        if a["agent_status"] == "gone":
            st["agents"].remove(a)
            err("agent_not_found")
    ok({{"agent": a, "type": "agent"}})
if key == "agent prompt":
    a = agent(args[2])
    if a is None:
        err("agent_not_found")
    r = st["prompt_results"].pop(0) if st["prompt_results"] else {{"status": "working"}}
    a["agent_status"] = r["status"]
    if r.get("error"):
        err(r["error"])
    ok({{"agent": a}})
if key == "agent read":
    if agent(args[2]) is None:
        err("agent_not_found")
    save()
    print("\n".join("pane line %d" % i for i in range(100)))
    sys.exit(0)
err("unknown_command")
'''


class Herdr:
    """The fake's state file: read and change it between tool calls."""

    def __init__(self, bindir: Path):
        self.path = bindir / "state.json"

    def load(self):
        return json.loads(self.path.read_text())

    def save(self, st):
        self.path.write_text(json.dumps(st))

    def update(self, **kw):
        st = self.load()
        st.update(kw)
        self.save(st)

    def set_status(self, name, status):
        st = self.load()
        for a in st["agents"]:
            if a.get("name") == name:
                a["agent_status"] = status
        self.save(st)

    def calls(self):
        return self.load()["calls"]

    def mutations(self):
        return [c for c in self.calls() if tuple(c[:2]) in MUTATING]

    def add_agent(self, name, status, cwd, ws="wx"):
        st = self.load()
        a = {"agent": "claude", "agent_status": status, "cwd": str(cwd), "pane_id": f"{ws}:p1", "workspace_id": ws}
        if name:
            a["name"] = name
        st["agents"].append(a)
        self.save(st)


@pytest.fixture
def herdr(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "herdr").write_text(FAKE.format(python=sys.executable))
    (bindir / "herdr").chmod(0o755)
    h = Herdr(bindir)
    h.save({"calls": [], "agents": [], "workspaces": [], "n": 0, "fail": {}, "get_seq": {},
            "prompt_results": []})
    monkeypatch.setenv("PATH", f"{bindir}:{BASE_PATH}")
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setenv("KRONK_AGENT_STATE", str(tmp_path / "state"))
    h.bindir = bindir
    h.state = tmp_path / "state"
    return h


@pytest.fixture
def brief(tmp_path):
    p = tmp_path / "brief.txt"
    p.write_text(f"Build the thing. {MARKER}\nSecond line.\n")
    return p


def run(at, capsys, *argv):
    rc = at.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return rc, out, err


def launch(at, capsys, repo, brief, item="M9", role="build", *extra):
    """launch, with --no-report added for a role that needs a report choice
    when the test names neither (the refusal has its own test)."""
    if role in ("build", "fix", "test", "review") and not {"--report", "--no-report"} & set(map(str, extra)):
        extra = (*extra, "--no-report")
    return run(at, capsys, "launch", item, role, "--brief", brief, "--repo", repo, *extra)


def one_json(out):
    lines = out.strip().splitlines()
    assert len(lines) == 1, out
    return json.loads(lines[0])


# ── criterion 2: the name rule, against work_export.py's own parser ─────────

@pytest.mark.parametrize("item,role,rnd,name", [
    ("M28", "build", 1, "m28-build"), ("m28", "test", 2, "m28-test2"), ("M28", "fix", 3, "m28-fix3"),
    ("M28", "review", 1, "m28-review"), ("M4b", "fix", 1, "m4b-fix"), ("M4B", "test", 1, "m4b-test"),
    ("M21-step2", "build", 1, "m21-step2-build"), ("m21-STEP2", "test", 4, "m21-step2-test4"),
])
def test_names_parse_back_to_item_and_role_by_work_exports_parser(at, we, item, role, rnd, name):
    canon, got = at.agent_name(item, role, rnd, False)
    assert got == name
    assert we.agent_for(got) == (canon, role)
    assert we.norm_id(item) == canon


def test_investigate_and_probe_names_are_not_attributed_to_the_item(at, we):
    for role in at.ROLES:
        _, probe = at.agent_name("M28", role, 1, True)
        assert probe == f"m28-probe-{role}" and we.agent_for(probe) == (None, None)
    _, inv = at.agent_name("M28", "investigate", 1, False)
    assert inv == "m28-investigate" and we.agent_for(inv) == (None, None)


def test_every_stage_role_is_a_tool_role(at, we):
    assert set(we.ROLE_STAGE) <= set(at.ROLES)


def test_launched_agents_show_on_their_item_with_the_stage_word(at, we, capsys, repo, brief, herdr):
    """Launch one agent per role /work knows through the fake herdr, then
    read them back the way the exporter does (its herdr reader, its name
    parser and its stage table) and check the stage word for each."""
    for role in we.ROLE_STAGE:
        rc, out, err = launch(at, capsys, repo, brief, "M9", role, "--parallel")
        assert rc == 0, err
    h = we.herdr_agents(repo)
    assert h["known"] and len(h["agents"]) == 4
    for a in h["agents"]:
        item, role = we.agent_for(a["name"])
        assert item == "M9" and a["status"] == "working"
        st = we.stage([], False, False, [dict(a, role=role)])
        assert st["words"] == we.STAGE_WORDS[we.ROLE_STAGE[role]]
        assert st["detail"] == f"{st['words']}, agent {a['name']} working"


# ── launch: what it starts and sends ───────────────────────────────────────

@pytest.mark.parametrize("role,model,effort", [
    ("build", "claude-opus-5-5", "high"), ("fix", "claude-opus-5-5", "high"),
    ("test", "claude-fable-5-2", "medium"), ("review", "claude-fable-5-2", "medium"),
    ("investigate", "claude-fable-5-2", "medium"),
])
def test_launch_starts_the_role_model_and_sends_brief_plus_guard_rails(at, capsys, repo, brief, herdr,
                                                                        role, model, effort):
    rc, out, err = launch(at, capsys, repo, brief, "M9", role)
    assert rc == 0 and err == ""
    res = one_json(out)
    name = f"m9-{role}"
    assert (res["name"], res["model"], res["effort"], res["sent"], res["agent_status"]) == \
        (name, model, effort, "once", "working")
    calls = herdr.calls()
    (create,) = [c for c in calls if c[:2] == ["workspace", "create"]]
    assert create[2:] == ["--label", name, "--cwd", str(repo.resolve()), "--no-focus"]
    (start,) = [c for c in calls if c[:2] == ["agent", "start"]]
    assert start == ["agent", "start", name, "--kind", "claude", "--pane", "w1:p1", "--timeout", "60000",
                     "--", "--model", model, "--effort", effort, "--dangerously-skip-permissions"]
    (prompt,) = [c for c in calls if c[:2] == ["agent", "prompt"]]
    want = brief.read_text().strip() + "\n\n" + GUARD.strip() + "\n"
    assert prompt[2:4] == [name, want]
    assert prompt[4:] == ["--wait", "--until", "working", "--until", "blocked", "--timeout", "25000"]
    pf = Path(res["prompt_file"])
    assert pf.read_text() == want and stat.S_IMODE(pf.stat().st_mode) == 0o600
    st = json.loads((herdr.state / f"{name}.json").read_text())
    assert {k: st[k] for k in ("name", "item", "role", "workspace_id", "pane_id", "model", "effort")} == \
        {"name": name, "item": "M9", "role": role, "workspace_id": "w1", "pane_id": "w1:p1",
         "model": model, "effort": effort}
    assert st["head"] == git(repo, "rev-parse", "HEAD").stdout.strip()
    assert git(repo, "status", "--porcelain").stdout == ""          # nothing written in the repo


def test_launch_overrides_model_effort_and_records_report(at, capsys, repo, brief, herdr):
    rc, out, err = launch(at, capsys, repo, brief, "M9", "test", "--round", "2", "--model", "claude-haiku-5-5",
                          "--effort", "low", "--report", "docs/sprint/M9-e2e2.md")
    assert rc == 0, err
    res = one_json(out)
    assert (res["name"], res["model"], res["effort"], res["report"]) == \
        ("m9-test2", "claude-haiku-5-5", "low", "docs/sprint/M9-e2e2.md")
    (start,) = [c for c in herdr.calls() if c[:2] == ["agent", "start"]]
    assert start[-5:] == ["--model", "claude-haiku-5-5", "--effort", "low", "--dangerously-skip-permissions"]


def test_guard_rails_section_is_taken_whole_up_to_the_next_heading(at, repo):
    rails = at.guard_rails(repo)
    assert rails == GUARD.strip() and "After the rails" not in rails


def test_guard_rails_section_of_the_real_workflow_file(at):
    rails = at.guard_rails(REPO)
    assert rails.startswith("## Guard rails in every brief\n")
    assert "Never `docker compose down -v`" in rails and "No new special-case routing rule" in rails
    assert "\n## " not in rails


def test_a_brief_starting_with_a_dash_is_not_read_as_an_option(at, capsys, repo, tmp_path, herdr):
    b = tmp_path / "dash.txt"
    b.write_text("--model x is not an option\n")
    rc, out, err = launch(at, capsys, repo, b)
    assert rc == 0, err
    (prompt,) = [c for c in herdr.calls() if c[:2] == ["agent", "prompt"]]
    assert not prompt[3].startswith("-") and "--model x is not an option" in prompt[3]


# ── criterion 3: every refusal changes nothing ─────────────────────────────

def refused(herdr, rc, out, err, words):
    assert rc == 1 and out == ""
    assert len(err.strip().splitlines()) == 1 and words in err, err
    assert "Traceback" not in err
    assert herdr.mutations() == []
    assert not herdr.state.exists() or not list(herdr.state.iterdir())


def test_refused_outside_herdr(at, capsys, repo, brief, herdr, monkeypatch):
    monkeypatch.setenv("HERDR_ENV", "0")
    refused(herdr, *launch(at, capsys, repo, brief), "not inside herdr")
    monkeypatch.delenv("HERDR_ENV")
    refused(herdr, *launch(at, capsys, repo, brief), "not inside herdr")


def test_refused_when_an_agent_of_that_name_exists(at, capsys, repo, brief, herdr):
    herdr.add_agent("m9-build", "idle", repo)
    refused(herdr, *launch(at, capsys, repo, brief), "an agent named m9-build exists")


def test_refused_when_a_workspace_of_that_name_exists(at, capsys, repo, brief, herdr):
    herdr.update(workspaces=[{"workspace_id": "w7", "label": "m9-build"}])
    refused(herdr, *launch(at, capsys, repo, brief), "a workspace labelled m9-build exists")


def test_refused_when_the_tool_already_knows_that_name(at, capsys, repo, brief, herdr):
    assert launch(at, capsys, repo, brief)[0] == 0
    herdr.set_status("m9-build", "idle")
    n = len(herdr.mutations())
    rc, out, err = launch(at, capsys, repo, brief)
    assert rc == 1 and "already in" in err and "close m9-build" in err
    assert len(herdr.mutations()) == n


def test_refused_while_another_tool_agent_works_unless_parallel(at, capsys, repo, brief, herdr):
    assert launch(at, capsys, repo, brief, "M9", "build")[0] == 0        # working after its prompt
    n = len(herdr.mutations())
    rc, out, err = launch(at, capsys, repo, brief, "M10", "investigate")
    assert rc == 1 and "another agent the tool launched is working (m9-build)" in err and "--parallel" in err
    assert len(herdr.mutations()) == n and not (herdr.state / "m10-investigate.json").exists()
    rc, out, err = launch(at, capsys, repo, brief, "M10", "investigate", "--parallel")
    assert rc == 0, err


def test_an_idle_or_blocked_tool_agent_does_not_block_a_launch(at, capsys, repo, brief, herdr):
    assert launch(at, capsys, repo, brief, "M9", "build")[0] == 0
    for s in ("idle", "done", "blocked"):
        herdr.set_status("m9-build", s)
        rc, out, err = launch(at, capsys, repo, brief, "M9", "test")
        assert rc == 0, (s, err)
        assert run(at, capsys, "close", "m9-test", "--force")[0] == 0


def test_agents_the_tool_did_not_launch_do_not_count(at, capsys, repo, brief, herdr):
    """The coordinator's own pane (unnamed) and a named agent started by hand
    are working, but neither is in the tool's state directory."""
    herdr.add_agent(None, "working", repo, ws="w90")
    herdr.add_agent("m9-other", "working", repo, ws="w91")
    rc, out, err = launch(at, capsys, repo, brief)
    assert rc == 0, err


def test_refused_when_the_brief_is_missing_or_empty(at, capsys, repo, tmp_path, herdr):
    refused(herdr, *launch(at, capsys, repo, tmp_path / "nope.txt"), "does not exist")
    empty = tmp_path / "empty.txt"
    empty.write_text(" \n\t\n")
    refused(herdr, *launch(at, capsys, repo, empty), "is empty")


@pytest.mark.parametrize("workflow,words", [
    ("# Workflow\n\n## Git\n\nNo rails here.\n", "no '## Guard rails in every brief' section"),
    ("# Workflow\n\n## Guard rails in every brief\n\n## Git\n", "section of"),
    ("# Workflow\n\n### Guard rails in every brief\n\n- a rail\n", "no '## Guard rails in every brief' section"),
])
def test_refused_without_the_guard_rails_section(at, capsys, repo, brief, herdr, workflow, words):
    commit(repo, {"docs/sprint/WORKFLOW.md": workflow})
    refused(herdr, *launch(at, capsys, repo, brief), words)


def test_refused_when_workflow_file_is_absent(at, capsys, repo, brief, herdr):
    git(repo, "rm", "-q", "docs/sprint/WORKFLOW.md")
    git(repo, "commit", "-q", "-m", "rm")
    refused(herdr, *launch(at, capsys, repo, brief), "cannot be read")


def test_there_is_no_way_to_leave_the_guard_rails_out(at, capsys, repo, brief, herdr):
    rc, out, err = launch(at, capsys, repo, brief, "M9", "build", "--no-guard-rails")
    assert rc == 1 and "unrecognized arguments: --no-guard-rails" in err
    assert herdr.mutations() == []


@pytest.mark.parametrize("args,words", [
    (["M9", "builder"], "role 'builder' is not one of: build, fix, test, review, investigate"),
    (["X9", "build"], "is not an item id"),
    (["M9", "build", "--round", "0"], "rounds start at 1"),
    (["M9", "build", "--effort", "huge"], "--effort 'huge'"),
    (["M9", "build", "--report", "../outside.md"], "inside the repo"),
    (["M" + "9" * 40, "build"], "breaks herdr's rule"),
])
def test_other_refusals_change_nothing(at, capsys, repo, brief, herdr, args, words):
    rc, out, err = run(at, capsys, "launch", *args, "--brief", brief, "--repo", repo)
    refused(herdr, rc, out, err, words)


def test_a_failed_agent_start_closes_the_workspace_it_made(at, capsys, repo, brief, herdr):
    herdr.update(fail={"agent start": "agent_not_ready"})
    rc, out, err = launch(at, capsys, repo, brief)
    assert rc == 1 and "herdr agent start: agent_not_ready" in err and "workspace w1 closed" in err
    assert herdr.load()["workspaces"] == [] and list(herdr.state.iterdir()) == []


# ── launch and prompt: confirming the prompt arrived ───────────────────────

def test_launch_resends_once_when_the_agent_did_not_start_working(at, capsys, repo, brief, herdr):
    herdr.update(prompt_results=[{"status": "idle"}, {"status": "working"}])
    rc, out, err = launch(at, capsys, repo, brief)
    assert rc == 0 and one_json(out)["sent"] == "twice"
    assert len([c for c in herdr.calls() if c[:2] == ["agent", "prompt"]]) == 2


def test_launch_reports_an_agent_that_never_started_and_leaves_it_open(at, capsys, repo, brief, herdr):
    herdr.update(prompt_results=[{"status": "idle"}, {"status": "idle"}])
    rc, out, err = launch(at, capsys, repo, brief)
    assert rc == 1 and one_json(out)["agent_status"] == "idle" and "sent twice" in err
    assert len([c for c in herdr.calls() if c[:2] == ["agent", "prompt"]]) == 2
    assert (herdr.state / "m9-build.json").exists()          # left for peek, prompt or close


@pytest.mark.parametrize("code", ["timeout", "agent_prompt_stalled"])
def test_herdrs_timeout_is_swallowed_and_the_status_decides(at, capsys, repo, brief, herdr, code):
    herdr.update(prompt_results=[{"status": "working", "error": code}])
    rc, out, err = launch(at, capsys, repo, brief)
    assert rc == 0 and err == "" and one_json(out)["sent"] == "once"


def test_any_other_prompt_error_is_reported(at, capsys, repo, brief, herdr):
    herdr.update(prompt_results=[{"status": "blocked", "error": "agent_blocked"}])
    rc, out, err = launch(at, capsys, repo, brief)
    assert rc == 1 and "herdr agent prompt: agent_blocked" in err and "left open" in err
    assert MARKER not in err


def test_prompt_sends_a_file_and_confirms_working(at, capsys, repo, brief, herdr, tmp_path):
    assert launch(at, capsys, repo, brief)[0] == 0
    herdr.set_status("m9-build", "idle")
    f = tmp_path / "follow.txt"
    f.write_text("Follow up.\n")
    herdr.update(prompt_results=[{"status": "idle"}, {"status": "working"}])
    rc, out, err = run(at, capsys, "prompt", "m9-build", "--file", f)
    assert rc == 0 and one_json(out) == {"name": "m9-build", "before": "idle", "sent": "twice",
                                         "agent_status": "working"}
    prompts = [c for c in herdr.calls() if c[:2] == ["agent", "prompt"]]
    assert prompts[-1][3] == "Follow up.\n"                    # no guard rails on a follow-up


def test_prompt_refusals(at, capsys, repo, brief, herdr, tmp_path):
    assert launch(at, capsys, repo, brief)[0] == 0
    f = tmp_path / "follow.txt"
    f.write_text("Follow up.\n")
    n = len(herdr.mutations())
    herdr.set_status("m9-build", "blocked")
    rc, out, err = run(at, capsys, "prompt", "m9-build", "--file", f)
    assert rc == 1 and "blocked" in err
    rc, out, err = run(at, capsys, "prompt", "m9-build", "--file", tmp_path / "none.txt")
    assert rc == 1 and "does not exist" in err
    rc, out, err = run(at, capsys, "prompt", "m9-nobody", "--file", f)
    assert rc == 1 and "no agent 'm9-nobody'" in err
    herdr.update(get_seq={"m9-build": ["gone"]})
    rc, out, err = run(at, capsys, "prompt", "m9-build", "--file", f)
    assert rc == 1 and "gone" in err
    assert len(herdr.mutations()) == n


# ── criterion 4: status and wait ───────────────────────────────────────────

REPORT = "docs/sprint/M9-build.md"


@pytest.fixture
def launched(at, capsys, repo, brief, herdr):
    rc, out, err = launch(at, capsys, repo, brief, "M9", "build", "--report", REPORT)
    assert rc == 0, err
    return "m9-build"


def status(at, capsys, name="m9-build", *extra):
    rc, out, err = run(at, capsys, "status", name, *extra)
    assert rc == 0, err
    return one_json(out)


def test_status_done_when_settled_clean_and_report_committed(at, capsys, repo, herdr, launched):
    commit(repo, {REPORT: "# M9 build\n\nVerdict: PASSES\n"}, "M9: build")
    for s in ("idle", "done"):
        herdr.set_status(launched, s)
        got = status(at, capsys)
        assert got["done"] is True and got["agent_status"] == s
    assert set(got) == {"name", "agent_status", "minutes", "commits_since", "agent_commits", "last_commit",
                        "tree_clean", "report", "done", "verdict"}
    assert got["commits_since"] == 1 and got["last_commit"].endswith(" M9: build")
    assert got["tree_clean"] is True
    assert got["report"] == {"path": REPORT, "committed": True, "changed_since_prompt": None}
    assert got["verdict"] == "PASSES" and isinstance(got["minutes"], float)


def test_not_done_while_working(at, capsys, repo, herdr, launched):
    commit(repo, {REPORT: "# r\n"})
    herdr.set_status(launched, "working")
    assert status(at, capsys)["done"] is False


def test_not_done_with_a_dirty_tree(at, capsys, repo, herdr, launched):
    commit(repo, {REPORT: "# r\n"})
    herdr.set_status(launched, "idle")
    (repo / "stray.txt").write_text("untracked\n")
    got = status(at, capsys)
    assert (got["done"], got["tree_clean"], got["report"]["committed"]) == (False, False, True)
    (repo / "stray.txt").unlink()
    (repo / "README.md").write_text("changed, not committed\n")
    assert status(at, capsys)["done"] is False


def test_not_done_without_the_report_committed(at, capsys, repo, herdr, launched):
    herdr.set_status(launched, "idle")
    got = status(at, capsys)
    assert (got["done"], got["tree_clean"], got["report"]) == \
        (False, True, {"path": REPORT, "committed": False, "changed_since_prompt": None})
    assert got["commits_since"] == 0 and got["verdict"] is None


def test_done_without_a_report_needs_only_settled_and_clean(at, capsys, repo, brief, herdr):
    assert launch(at, capsys, repo, brief, "M9", "test")[0] == 0
    herdr.set_status("m9-test", "idle")
    got = status(at, capsys, "m9-test")
    assert got["done"] is True and got["report"] is None


def test_a_report_from_before_the_launch_is_not_committed_until_changed(at, capsys, repo, brief, herdr):
    commit(repo, {REPORT: "# M9 build\n\nVerdict: FAILS\n"}, "old report")
    assert launch(at, capsys, repo, brief, "M9", "build", "--report", REPORT)[0] == 0
    herdr.set_status("m9-build", "idle")
    commit(repo, {"other.txt": "x\n"}, "unrelated commit")
    got = status(at, capsys)
    assert got["report"]["committed"] is False and got["done"] is False
    commit(repo, {REPORT: "# M9 build\n\nVerdict: PASSES\n"}, "new report")
    got = status(at, capsys)
    assert got["report"]["committed"] is True and got["done"] is True and got["verdict"] == "PASSES"


def test_report_can_be_named_at_status_and_is_kept(at, capsys, repo, brief, herdr):
    assert launch(at, capsys, repo, brief, "M9", "test")[0] == 0
    herdr.set_status("m9-test", "idle")
    assert status(at, capsys, "m9-test", "--report", "docs/sprint/M9-e2e.md")["done"] is False
    assert status(at, capsys, "m9-test")["report"] == {"path": "docs/sprint/M9-e2e.md", "committed": False,
                                                       "changed_since_prompt": None}
    commit(repo, {"docs/sprint/M9-e2e.md": "# M9 test\n\nVerdict: NOT DETERMINED\n"})
    got = status(at, capsys, "m9-test")
    assert got["done"] is True and got["verdict"] == "NOT DETERMINED"


def test_status_of_a_gone_agent(at, capsys, repo, herdr, launched):
    herdr.update(get_seq={launched: ["gone"]})
    got = status(at, capsys)
    assert got["agent_status"] == "gone" and got["done"] is False


def wait(at, capsys, *extra, name="m9-build"):
    return run(at, capsys, "wait", name, "--interval", "0.05", *extra)


def test_wait_exits_0_when_done(at, capsys, repo, herdr, launched):
    commit(repo, {REPORT: "# r\n"})
    herdr.update(get_seq={launched: ["working", "working", "idle"]})
    rc, out, err = wait(at, capsys, "--timeout", "30")
    assert rc == 0 and err == "" and one_json(out)["done"] is True


@pytest.mark.parametrize("seq,words", [(["working", "blocked"], "is blocked"), (["working", "gone"], "is gone")])
def test_wait_exits_3_when_blocked_or_gone(at, capsys, repo, herdr, launched, seq, words):
    herdr.update(get_seq={launched: seq})
    rc, out, err = wait(at, capsys, "--timeout", "30")
    assert rc == 3 and words in err
    assert one_json(out)["agent_status"] == seq[-1]


def test_wait_exits_3_when_idle_with_the_work_not_done(at, capsys, repo, herdr, launched):
    herdr.set_status(launched, "idle")                       # no report committed
    rc, out, err = wait(at, capsys, "--timeout", "30", "--idle-limit", "0.3")
    assert rc == 3 and "with the work not done" in err
    got = one_json(out)
    assert got["agent_status"] == "idle" and got["done"] is False


def test_wait_idle_clock_restarts_when_the_agent_works_again(at, capsys, repo, herdr, launched):
    herdr.update(get_seq={launched: ["idle", "idle", "working", "idle", "idle", "idle"]})
    rc, out, err = wait(at, capsys, "--timeout", "30", "--idle-limit", "0.12")
    assert rc == 3
    gets = [c for c in herdr.calls() if c[:2] == ["agent", "get"]]
    assert len(gets) >= 6


def test_wait_exits_2_at_the_time_limit(at, capsys, repo, herdr, launched):
    herdr.set_status(launched, "working")
    rc, out, err = wait(at, capsys, "--timeout", "0.3")
    assert rc == 2 and "time limit" in err
    assert one_json(out)["agent_status"] == "working"


def test_wait_records_a_report_named_at_wait(at, capsys, repo, brief, herdr):
    assert launch(at, capsys, repo, brief, "M9", "fix")[0] == 0
    commit(repo, {"docs/sprint/M9-fix1.md": "# fix\n"})
    herdr.set_status("m9-fix", "idle")
    rc, out, err = wait(at, capsys, "--report", "docs/sprint/M9-fix1.md", name="m9-fix")
    assert rc == 0 and one_json(out)["report"]["committed"] is True


def test_wait_gives_up_after_three_herdr_errors_in_a_row(at, capsys, repo, herdr, launched):
    before = len(herdr.calls())
    herdr.update(fail={"agent get": "server_not_running"})
    rc, out, err = wait(at, capsys, "--timeout", "30")
    assert rc == 1 and "herdr agent get: server_not_running" in err and out == ""
    assert len([c for c in herdr.calls()[before:] if c[:2] == ["agent", "get"]]) == 3


# ── criterion 5: no git lock, nothing written in the repo ──────────────────

def test_git_is_called_read_only_and_lockless(at, capsys, repo, herdr, launched, monkeypatch):
    seen = []
    real = at.run

    def spy(cmd, cwd, timeout, env=None):
        if cmd[0] == "git":
            seen.append((cmd, env))
        return real(cmd, cwd, timeout, env)
    monkeypatch.setattr(at, "run", spy)
    commit(repo, {REPORT: "Verdict: PASSES\n"})
    herdr.set_status(launched, "idle")
    assert status(at, capsys)["done"] is True
    assert seen
    for cmd, env in seen:
        assert cmd[1] == "--no-optional-locks" and env["GIT_OPTIONAL_LOCKS"] == "0"
        assert cmd[4] in {"rev-parse", "rev-list", "log", "status", "diff", "cat-file"}, cmd
        assert not {"add", "commit", "stash", "gc", "reset", "checkout", "update-index"} & set(cmd)


@pytest.mark.skipif(not shutil.which("strace"), reason="strace not installed")
def test_under_strace_status_opens_no_lock_and_writes_nothing_in_the_repo(at, capsys, repo, herdr, launched,
                                                                        tmp_path):
    commit(repo, {REPORT: "Verdict: PASSES\n"})
    herdr.set_status(launched, "idle")
    trace = tmp_path / "trace.txt"
    env = dict(os.environ)
    r = subprocess.run(["strace", "-f", "-qq", "-o", str(trace), "-e",
                        "trace=openat,open,creat,rename,renameat,renameat2,unlink,unlinkat,mkdir,mkdirat",
                        sys.executable, str(SCRIPT), "status", "m9-build"],
                       env=env, capture_output=True, text=True, timeout=60)
    if r.returncode != 0 and "ptrace" in (r.stderr or ""):
        pytest.skip("ptrace not permitted here")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["done"] is True
    lines = trace.read_text().splitlines()
    assert [l for l in lines if "/.git/" in l], "the trace saw no git file: git was not traced"
    assert not [l for l in lines if ".lock" in l]
    root = str(repo.resolve())
    writes = [l for l in lines if root in l and ("O_WRONLY" in l or "O_RDWR" in l or "O_CREAT" in l
                                                 or "rename" in l or "unlink" in l or "mkdir" in l)]
    assert writes == []


# ── criterion 6: the brief's text lives in one file only ───────────────────

def test_the_brief_is_kept_only_in_the_prompt_file(at, capsys, repo, brief, herdr, tmp_path):
    printed = []

    def go(*argv):
        rc, out, err = run(at, capsys, *argv)
        printed.append(out + err)
        return rc
    f = tmp_path / "follow.txt"
    f.write_text("Follow up.\n")
    assert go("launch", "M9", "build", "--brief", brief, "--repo", repo, "--report", REPORT) == 0
    herdr.set_status("m9-build", "idle")
    go("status", "m9-build")
    go("wait", "m9-build", "--interval", "0.05", "--idle-limit", "0.1")
    go("list")
    go("prompt", "m9-build", "--file", f)
    herdr.update(prompt_results=[{"status": "blocked", "error": "agent_blocked"}])
    herdr.set_status("m9-build", "idle")
    go("prompt", "m9-build", "--file", f)
    assert not [p for p in printed if MARKER in p]
    holders = []
    for p in herdr.state.rglob("*"):
        if p.is_file():
            assert stat.S_IMODE(p.stat().st_mode) == 0o600, p
            if MARKER in p.read_text():
                holders.append(p.name)
    assert holders == ["m9-build.prompt.txt"]
    assert stat.S_IMODE(herdr.state.stat().st_mode) == 0o700
    herdr.set_status("m9-build", "idle")
    assert go("close", "m9-build") == 0
    assert not [p for p in printed if MARKER in p]
    assert list(herdr.state.iterdir()) == []


# ── peek, close, list ──────────────────────────────────────────────────────

def test_peek_prints_the_last_lines_as_text(at, capsys, repo, herdr, launched):
    rc, out, err = run(at, capsys, "peek", launched, "--lines", "3")
    assert rc == 0 and out == "pane line 97\npane line 98\npane line 99\n"
    (read,) = [c for c in herdr.calls() if c[:2] == ["agent", "read"]]
    assert read == ["agent", "read", launched, "--source", "recent-unwrapped", "--lines", "3", "--format", "text"]
    rc, out, err = run(at, capsys, "peek", launched)
    assert rc == 0 and len(out.splitlines()) == 40


def test_peek_a_gone_agent(at, capsys, repo, herdr, launched):
    herdr.update(get_seq={launched: ["gone"]})
    run(at, capsys, "status", launched)
    rc, out, err = run(at, capsys, "peek", launched)
    assert rc == 1 and "is gone" in err


def test_close_refuses_a_working_agent_unless_forced(at, capsys, repo, herdr, launched):
    rc, out, err = run(at, capsys, "close", launched)
    assert rc == 1 and "is working" in err and "--force" in err
    assert len(herdr.load()["workspaces"]) == 1 and (herdr.state / "m9-build.json").exists()
    rc, out, err = run(at, capsys, "close", launched, "--force")
    assert rc == 0 and one_json(out) == {"name": launched, "workspace_id": "w1", "workspace": "closed",
                                         "agent_status_before": "working"}
    assert herdr.load()["workspaces"] == [] and list(herdr.state.iterdir()) == []


def test_close_closes_only_the_recorded_workspace(at, capsys, repo, brief, herdr):
    herdr.update(workspaces=[{"workspace_id": "w1", "label": "kronk-claude"}], n=1)
    herdr.add_agent(None, "working", repo, ws="w1")             # the coordinator's own
    assert launch(at, capsys, repo, brief)[0] == 0
    herdr.set_status("m9-build", "idle")
    assert run(at, capsys, "close", "m9-build")[0] == 0
    closes = [c for c in herdr.calls() if c[:2] == ["workspace", "close"]]
    assert closes == [["workspace", "close", "w2"]]
    assert [w["label"] for w in herdr.load()["workspaces"]] == ["kronk-claude"]
    rc, out, err = run(at, capsys, "close", "kronk-claude")
    assert rc == 1 and "no agent 'kronk-claude'" in err
    assert len([c for c in herdr.calls() if c[:2] == ["workspace", "close"]]) == 1


def test_close_when_the_workspace_is_already_gone(at, capsys, repo, herdr, launched):
    herdr.update(workspaces=[], agents=[])
    rc, out, err = run(at, capsys, "close", launched)
    assert rc == 0 and one_json(out)["workspace"] == "already gone" and list(herdr.state.iterdir()) == []


def test_close_keeps_state_when_the_workspace_survives(at, capsys, repo, herdr, launched):
    herdr.update(close_noop=True)
    herdr.set_status(launched, "idle")
    rc, out, err = run(at, capsys, "close", launched)
    assert rc == 1 and "still there" in err and (herdr.state / "m9-build.json").exists()


def test_list_prints_one_line_per_known_agent(at, capsys, repo, brief, herdr):
    rc, out, err = run(at, capsys, "list")
    assert (rc, out, err) == (0, "", "")
    assert launch(at, capsys, repo, brief, "M9", "build", "--report", REPORT)[0] == 0
    assert launch(at, capsys, repo, brief, "M9", "investigate", "--parallel")[0] == 0
    herdr.update(get_seq={"m9-investigate": ["gone"]})
    run(at, capsys, "status", "m9-investigate")
    rc, out, err = run(at, capsys, "list")
    rows = [json.loads(l) for l in out.splitlines()]
    assert rc == 0 and [(r["name"], r["agent_status"], r["role"], r["report"]) for r in rows] == \
        [("m9-build", "working", "build", REPORT), ("m9-investigate", "gone", "investigate", None)]


# ── exit codes and errors: one line, never a traceback ─────────────────────

def cli(*argv, env=None):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, argv)], capture_output=True,
                          text=True, env=env, timeout=60)


def test_herdr_absent_is_one_line_exit_1(tmp_path, repo, brief):
    env = dict(os.environ, PATH=BASE_PATH, HERDR_ENV="1", KRONK_AGENT_STATE=str(tmp_path / "s"))
    for argv in (["launch", "M9", "build", "--brief", brief, "--repo", repo, "--no-report"], ["list"]):
        r = cli(*argv, env=env)
        assert r.returncode in (0, 1) and "Traceback" not in r.stderr
    r = cli("launch", "M9", "build", "--brief", brief, "--repo", repo, "--no-report", env=env)
    assert r.returncode == 1 and r.stderr.count("\n") == 1 and "herdr agent list: unreachable" in r.stderr


def test_errors_from_herdr_on_stderr_are_read_by_code(at, capsys, repo, herdr, launched):
    herdr.update(fail={"agent get": "server_not_running"})
    rc, out, err = run(at, capsys, "status", launched)
    assert rc == 1 and err == "agent.py status: herdr agent get: server_not_running (exit 1)\n"


def test_a_corrupt_state_file_is_one_line(at, capsys, herdr):
    herdr.state.mkdir()
    (herdr.state / "m9-build.json").write_text("{not json")
    for sub in ("status", "wait", "peek", "close"):
        rc, out, err = run(at, capsys, sub, "m9-build")
        assert rc == 1 and "cannot be read" in err and err.count("\n") == 1
    rc, out, err = run(at, capsys, "list")
    assert (rc, out) == (0, "")


def test_bad_names_and_usage_are_one_line(at, capsys, herdr):
    for argv in (["status", "../etc/passwd"], ["status", "Bad Name"], ["bogus"], [], ["wait"]):
        rc, out, err = run(at, capsys, *argv)
        assert rc == 1 and err.count("\n") == 1 and "Traceback" not in err, argv


# ── fix round 1 (M28-criteria.md, Rulings) ─────────────────────────────────

def no_herdr_change(herdr):
    assert herdr.mutations() == [] and herdr.load()["workspaces"] == []


# B1: nothing is created in herdr before the state directory is proven
# writable; a state write failing after workspace create closes it.

@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through mode bits")
@pytest.mark.parametrize("how", ["read-only", "a file", "parent read-only"])
def test_b1_launch_refused_before_herdr_when_the_state_directory_cannot_be_written(
        at, capsys, repo, brief, herdr, monkeypatch, tmp_path, how):
    if how == "read-only":
        herdr.state.mkdir(mode=0o500)
        fix = herdr.state
    elif how == "a file":
        herdr.state.write_text("not a directory\n")
        fix = None
    else:
        parent = tmp_path / "ro"
        parent.mkdir(mode=0o500)
        monkeypatch.setenv("KRONK_AGENT_STATE", str(parent / "state"))
        fix = parent
    try:
        rc, out, err = launch(at, capsys, repo, brief)
        assert rc == 1 and out == "" and err.count("\n") == 1
        assert "cannot be written" in err and "not launched" in err, err
        no_herdr_change(herdr)
    finally:
        if fix is not None:
            fix.chmod(0o700)


@pytest.mark.parametrize("fails", [".json", ".prompt.txt"])
def test_b1_a_state_write_failing_after_create_closes_the_workspace(at, capsys, repo, brief, herdr,
                                                                    monkeypatch, fails):
    real = at.write_private

    def flaky(path, text):
        if str(path).endswith(fails):
            raise PermissionError(13, "Permission denied")
        return real(path, text)
    monkeypatch.setattr(at, "write_private", flaky)
    rc, out, err = launch(at, capsys, repo, brief)
    assert rc == 1 and out == "" and err.count("\n") == 1
    assert "could not be written (PermissionError)" in err and "workspace w1 closed; nothing left" in err, err
    assert herdr.load()["workspaces"] == [] and not [c for c in herdr.calls() if c[:2] == ["agent", "start"]]
    assert list(herdr.state.iterdir()) == []


def test_b1_says_so_when_the_workspace_cannot_be_closed_either(at, capsys, repo, brief, herdr, monkeypatch):
    def broken(path, text):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(at, "write_private", broken)
    herdr.update(fail={"workspace close": "server_error"})
    rc, out, err = launch(at, capsys, repo, brief)
    assert rc == 1 and "workspace w1 left open with no record: `herdr workspace close w1`" in err, err


# B2: no bytecode cache is written beside work_export.py.

def test_b2_the_tool_writes_no_bytecode_in_its_scripts_directory(tmp_path, repo, brief, herdr):
    copy = tmp_path / "copy" / "scripts"
    copy.mkdir(parents=True)
    for f in ("agent.py", "work_export.py", "work.py"):
        shutil.copy(REPO / "scripts" / f, copy / f)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
    r = subprocess.run([sys.executable, str(copy / "agent.py"), "launch", "M9", "build", "--brief", brief,
                        "--repo", repo, "--report", REPORT], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    commit(repo, {REPORT: "Verdict: PASSES\n"})             # status now reads the verdict through work_export
    r = subprocess.run([sys.executable, str(copy / "agent.py"), "status", "m9-build"], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and json.loads(r.stdout)["verdict"] == "PASSES", r.stderr
    assert not (copy / "__pycache__").exists()


def test_b2_the_flag_is_put_back_after_the_load(at):
    before = sys.dont_write_bytecode
    at._WE = None
    at.work_export()
    assert sys.dont_write_bytecode == before


# S1: a report path that is a directory is refused at launch, status and wait.

def test_s1_a_directory_report_is_refused_at_launch(at, capsys, repo, brief, herdr):
    for path in ("docs/sprint", "docs", "docs/sprint/"):          # trees in HEAD
        rc, out, err = launch(at, capsys, repo, brief, "M9", "build", "--report", path)
        assert rc == 1 and "is a directory" in err, (path, err)
    (repo / "empty-dir").mkdir()                                   # on disk only
    rc, out, err = launch(at, capsys, repo, brief, "M9", "build", "--report", "empty-dir")
    assert rc == 1 and "is a directory" in err
    no_herdr_change(herdr)


def test_s1_a_directory_report_is_refused_at_status_and_wait(at, capsys, repo, brief, herdr):
    assert launch(at, capsys, repo, brief, "M9", "build", "--report", REPORT)[0] == 0
    herdr.set_status("m9-build", "idle")
    commit(repo, {"docs/sprint/other.md": "x\n"})
    for sub in ("status", "wait"):
        rc, out, err = run(at, capsys, sub, "m9-build", "--report", "docs/sprint")
        assert rc == 1 and out == "" and "is a directory" in err, (sub, err)
    assert json.loads((herdr.state / "m9-build.json").read_text())["report"] == REPORT
    (repo / REPORT).mkdir(parents=True)                            # the recorded path became a directory
    for sub in ("status", "wait"):
        rc, out, err = run(at, capsys, sub, "m9-build")
        assert rc == 1 and "is a directory" in err, (sub, err)


# S2: prompt records HEAD; status says whether the report changed since and
# how many commits are the agent's; wait --expect-commit needs a new one.

def coord_commit(tree: Path, files: dict, msg="coordinator step"):
    for name, text in files.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_text(text)
        git(tree, "add", name)
    git(tree, "commit", "-q", "-m", msg, "-m", "Claude-Session: https://claude.ai/code/session_x")


def follow_up(at, capsys, tmp_path, name="m9-build"):
    f = tmp_path / "follow.txt"
    f.write_text("Follow up.\n")
    rc, out, err = run(at, capsys, "prompt", name, "--file", f)
    assert rc == 0, err


def test_s2_prompt_records_head_and_status_reports_changed_since_prompt(at, capsys, repo, herdr, launched,
                                                                       tmp_path):
    commit(repo, {REPORT: "# M9 build\n\nVerdict: PASSES\n"}, "round one")
    herdr.set_status(launched, "idle")
    assert status(at, capsys)["report"]["changed_since_prompt"] is None
    follow_up(at, capsys, tmp_path)
    st = json.loads((herdr.state / "m9-build.json").read_text())
    assert st["prompt_head"] == git(repo, "rev-parse", "HEAD").stdout.strip() and st["prompts"] == 1
    herdr.set_status(launched, "idle")
    got = status(at, capsys)
    assert got["report"] == {"path": REPORT, "committed": True, "changed_since_prompt": False}
    assert got["done"] is True                                     # point 7 still decides done
    rc, out, err = wait(at, capsys, "--timeout", "30", "--idle-limit", "0.1", "--expect-commit")
    assert rc == 3 and one_json(out)["done"] is False and "work not done" in err
    commit(repo, {REPORT: "# M9 build\n\nVerdict: PASSES, round two\n"}, "round two")
    got = status(at, capsys)
    assert got["report"]["changed_since_prompt"] is True
    rc, out, err = wait(at, capsys, "--timeout", "30", "--expect-commit")
    assert rc == 0 and one_json(out)["done"] is True


def test_s2_agent_commits_leave_out_the_coordinators_stamped_commits(at, capsys, repo, brief, herdr, tmp_path):
    coord_commit(repo, {"criteria.md": "c\n"}, "criteria")        # history shows the trailer in use
    assert launch(at, capsys, repo, brief, "M9", "build", "--report", REPORT)[0] == 0
    herdr.set_status("m9-build", "idle")
    assert status(at, capsys)["agent_commits"] == 0
    commit(repo, {REPORT: "# r\n"}, "agent's report")
    coord_commit(repo, {"rulings.md": "r\n"}, "coordinator's ruling")
    got = status(at, capsys)
    assert (got["commits_since"], got["agent_commits"]) == (2, 1)
    follow_up(at, capsys, tmp_path)
    herdr.set_status("m9-build", "idle")
    coord_commit(repo, {"rulings.md": "r2\n"}, "another ruling")
    rc, out, err = wait(at, capsys, "--timeout", "30", "--idle-limit", "0.1", "--expect-commit")
    assert rc == 3, err                                            # the coordinator's commit is not the agent's
    commit(repo, {"fix.txt": "x\n"}, "agent's fix")
    rc, out, err = wait(at, capsys, "--timeout", "30", "--expect-commit")
    assert rc == 0 and one_json(out)["agent_commits"] == 2


def test_s2_agent_commits_is_unknown_where_no_commit_carries_the_trailer(at, capsys, repo, herdr, launched):
    commit(repo, {REPORT: "# r\n"})
    herdr.set_status(launched, "idle")
    got = status(at, capsys)
    assert got["commits_since"] == 1 and got["agent_commits"] is None
    rc, out, err = wait(at, capsys, "--timeout", "30", "--expect-commit")
    assert rc == 0                                                 # any commit since the launch prompt counts


def test_s2_expect_commit_without_a_follow_up_counts_from_the_launch(at, capsys, repo, herdr, launched):
    herdr.set_status(launched, "idle")
    rc, out, err = run(at, capsys, "status", launched, "--report", "README.md", "--expect-commit")
    assert rc == 0 and one_json(out)["done"] is False
    commit(repo, {"README.md": "changed\n"})
    rc, out, err = run(at, capsys, "status", launched, "--expect-commit")
    assert rc == 0 and one_json(out)["done"] is True


# S3: a NUL byte in a brief or prompt file is refused before anything is made.

def test_s3_a_brief_with_a_nul_byte_is_refused(at, capsys, repo, tmp_path, herdr):
    b = tmp_path / "nul.txt"
    b.write_bytes(b"Build it.\x00\n")
    refused(herdr, *launch(at, capsys, repo, b), "holds a NUL byte")


def test_s3_a_prompt_file_with_a_nul_byte_is_refused(at, capsys, repo, herdr, launched, tmp_path):
    herdr.set_status(launched, "idle")
    f = tmp_path / "nul.txt"
    f.write_bytes(b"Follow up.\x00\n")
    n = len(herdr.mutations())
    rc, out, err = run(at, capsys, "prompt", launched, "--file", f)
    assert rc == 1 and "holds a NUL byte; nothing sent" in err and len(herdr.mutations()) == n
    assert "prompt_head" not in json.loads((herdr.state / "m9-build.json").read_text())


# S4: committed, then another commit; a failing git diff is not "committed".

def test_s4_a_report_committed_then_followed_by_another_commit_stays_committed(at, capsys, repo, herdr, launched):
    commit(repo, {REPORT: "# M9 build\n\nVerdict: PASSES\n"}, "report")
    commit(repo, {"later.txt": "x\n"}, "a later commit")
    herdr.set_status(launched, "idle")
    got = status(at, capsys)
    assert got["report"]["committed"] is True and got["done"] is True


def test_s4_a_failing_git_diff_does_not_count_as_committed(at, capsys, repo, herdr, launched):
    commit(repo, {REPORT: "# r\n"})
    herdr.set_status(launched, "idle")
    p = herdr.state / "m9-build.json"
    st = json.loads(p.read_text())
    st["head"] = "0" * 40                                           # a launch HEAD git does not know
    p.write_text(json.dumps(st))
    got = status(at, capsys)
    assert got["report"]["committed"] is False and got["done"] is False


# N1: build, fix, test and review need --report or --no-report.

@pytest.mark.parametrize("role", ["build", "fix", "test", "review"])
def test_n1_a_report_role_is_refused_without_report_or_no_report(at, capsys, repo, brief, herdr, role):
    rc, out, err = run(at, capsys, "launch", "M9", role, "--brief", brief, "--repo", repo)
    refused(herdr, rc, out, err, f"role {role} needs --report PATH")


def test_n1_no_report_and_investigate_launch_without_a_report(at, capsys, repo, brief, herdr):
    rc, out, err = run(at, capsys, "launch", "M9", "investigate", "--brief", brief, "--repo", repo)
    assert rc == 0, err
    rc, out, err = run(at, capsys, "launch", "M9", "build", "--brief", brief, "--repo", repo, "--no-report",
                       "--parallel")
    assert rc == 0 and one_json(out)["report"] is None, err


def test_n1_report_and_no_report_together_are_refused(at, capsys, repo, brief, herdr):
    rc, out, err = run(at, capsys, "launch", "M9", "build", "--brief", brief, "--repo", repo, "--report", REPORT,
                       "--no-report")
    refused(herdr, rc, out, err, "--report and --no-report together")


# N2: an agent of the name in another pane is not the tool's: gone.

def test_n2_an_agent_of_the_name_in_another_pane_is_gone(at, capsys, repo, herdr, launched):
    herdr.update(workspaces=[{"workspace_id": "w9", "label": "m9-build", "cwd": str(repo)}],
                 agents=[{"agent": "claude", "name": "m9-build", "agent_status": "working", "pane_id": "w9:p1",
                          "workspace_id": "w9", "cwd": str(repo)}])
    assert status(at, capsys)["agent_status"] == "gone"
    rc, out, err = wait(at, capsys, "--timeout", "30")
    assert rc == 3 and "is gone" in err
    rc, out, err = run(at, capsys, "list")
    assert rc == 0 and one_json(out)["agent_status"] == "gone"
    rc, out, err = run(at, capsys, "close", launched)              # not refused as working
    assert rc == 0 and one_json(out) == {"name": launched, "workspace_id": "w1", "workspace": "already gone",
                                         "agent_status_before": "gone"}
    assert [w["workspace_id"] for w in herdr.load()["workspaces"]] == ["w9"]
    assert [c for c in herdr.calls() if c[:2] == ["workspace", "close"]] == [["workspace", "close", "w1"]]


def test_n2_the_same_pane_is_still_the_agent(at, capsys, repo, herdr, launched):
    herdr.set_status(launched, "working")
    assert status(at, capsys)["agent_status"] == "working"
    rc, out, err = run(at, capsys, "list")
    assert one_json(out)["agent_status"] == "working"


# ── M33: agent.py records the step in scripts/work.py's database ──────────
# The repo fixture made a scratch database for the temporary repo, with M9
# active (KRONK_WORK_DB). agent.py holds no SQL; it calls work.py.

def steps(**where):
    import sqlite3
    c = sqlite3.connect(os.environ["KRONK_WORK_DB"])
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute("SELECT * FROM step ORDER BY id")]
    finally:
        c.close()


def log_rows():
    import sqlite3
    c = sqlite3.connect(os.environ["KRONK_WORK_DB"])
    try:
        return [r[0] for r in c.execute("SELECT cmd FROM log ORDER BY id")]
    finally:
        c.close()


def test_m33_agent_py_holds_no_sql():
    src = SCRIPT.read_text()
    assert "import sqlite3" not in src and not re.search(r"\b(SELECT|INSERT|UPDATE|DELETE) ", src)


def test_m33_launch_starts_a_step_with_the_agent_model_and_launch_time(at, capsys, repo, brief, herdr, launched):
    (s,) = steps()
    st = json.loads((herdr.state / "m9-build.json").read_text())
    assert (s["item_id"], s["kind"], s["round"], s["state"], s["by"], s["agent"], s["model"], s["report"]) == \
        ("M9", "build", 1, "started", "agent", "m9-build", "claude-opus-5-5", REPORT)
    import datetime as _dt
    launched = _dt.datetime.strptime(st["launched_iso"], "%Y-%m-%dT%H:%M:%S%z").astimezone(_dt.timezone.utc)
    assert s["started_at"] == launched.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert log_rows()[-1] == "agent.py launch m9-build"


def test_m33_wait_finishes_it_from_the_report_once(at, capsys, repo, herdr, launched):
    commit(repo, {REPORT: "Verdict: PASSES\n"})
    herdr.set_status(launched, "idle")
    rc, out, err = wait(at, capsys, "--timeout", "30")
    assert rc == 0 and err == ""
    (s,) = steps()
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert (s["state"], s["commit_sha"], s["verdict"]) == ("finished", head, None)    # a build has no verdict
    n = len(log_rows())
    for _ in range(3):
        assert status(at, capsys)["done"] is True
    assert len(log_rows()) == n                                  # idempotent: no write, no log row


def test_m33_a_test_steps_verdict_is_copied_from_the_report(at, capsys, repo, brief, herdr):
    assert launch(at, capsys, repo, brief, "M9", "test", "--report", "docs/sprint/M9-e2e.md")[0] == 0
    commit(repo, {"docs/sprint/M9-e2e.md": "# t\n\nVerdict: FAILS\n"})
    herdr.set_status("m9-test", "idle")
    assert status(at, capsys, "m9-test")["verdict"] == "FAILS"
    assert [(s["kind"], s["state"], s["verdict"]) for s in steps()] == [("test", "finished", "FAILS")]


@pytest.mark.parametrize("seq,code", [(["working"], 2), (["working", "blocked"], 3), (["working", "gone"], 3)])
def test_m33_wait_exiting_2_or_3_leaves_it_started(at, capsys, repo, herdr, launched, seq, code):
    herdr.update(get_seq={launched: seq})
    rc, out, err = wait(at, capsys, "--timeout", "0.3", "--idle-limit", "0.1")
    assert rc == code
    assert [s["state"] for s in steps()] == ["started"]


def test_m33_close_with_the_work_not_done_marks_it_stopped_with_why(at, capsys, repo, herdr, launched):
    herdr.set_status(launched, "idle")
    rc, out, err = run(at, capsys, "close", launched)
    assert rc == 0 and err == ""
    (s,) = steps()
    assert s["state"] == "stopped" and "report not committed" in s["note"] and "agent was idle" in s["note"]
    assert s["ended_at"]


def test_m33_close_with_the_report_committed_finishes_it(at, capsys, repo, herdr, launched):
    commit(repo, {REPORT: "# r\n"})
    herdr.set_status(launched, "idle")
    assert run(at, capsys, "close", launched)[0] == 0
    assert [s["state"] for s in steps()] == ["finished"]


def test_m33_close_after_finish_changes_nothing(at, capsys, repo, herdr, launched):
    commit(repo, {REPORT: "# r\n"})
    herdr.set_status(launched, "idle")
    assert status(at, capsys)["done"]
    n = len(log_rows())
    assert run(at, capsys, "close", launched)[0] == 0
    assert len(log_rows()) == n and [s["state"] for s in steps()] == ["finished"]


def test_m33_a_follow_up_reopens_nothing_and_the_next_commit_moves_the_row(at, capsys, repo, herdr, launched,
                                                                         tmp_path):
    commit(repo, {REPORT: "# r\n"})
    herdr.set_status(launched, "idle")
    assert status(at, capsys)["done"]
    first = steps()[0]["commit_sha"]
    f = tmp_path / "follow.txt"
    f.write_text("Fix the report.\n")
    assert run(at, capsys, "prompt", launched, "--file", f)[0] == 0
    assert [s["state"] for s in steps()] == ["finished"] and steps()[0]["commit_sha"] == first
    commit(repo, {REPORT: "# r, fixed\n"})
    herdr.set_status(launched, "idle")
    rc, out, err = wait(at, capsys, "--expect-commit", "--timeout", "30")
    assert rc == 0
    (s,) = steps()
    assert s["state"] == "finished" and s["commit_sha"] == git(repo, "rev-parse", "HEAD").stdout.strip() != first


@pytest.mark.parametrize("extra", [("--probe", "--report", REPORT), ("--no-report",)])
def test_m33_no_step_for_probe_or_no_report(at, capsys, repo, brief, herdr, extra):
    rc, out, err = launch(at, capsys, repo, brief, "M9", "build", *extra)
    assert rc == 0 and err == ""
    name = one_json(out)["name"]
    herdr.set_status(name, "idle")
    commit(repo, {REPORT: "# r\n"})
    assert run(at, capsys, "status", name)[0] == 0
    assert run(at, capsys, "close", name)[0] == 0
    assert steps() == []


def test_m33_no_step_for_investigate(at, capsys, repo, brief, herdr):
    rc, out, err = launch(at, capsys, repo, brief, "M9", "investigate", "--report", REPORT)
    assert rc == 0 and err == "" and steps() == []


def break_db(case, repo, tmp_path, monkeypatch):
    """Point KRONK_WORK_DB at a database in the named state."""
    import sqlite3
    db = Path(os.environ["KRONK_WORK_DB"])
    if case == "missing":
        monkeypatch.setenv("KRONK_WORK_DB", str(tmp_path / "nowhere" / "work.db"))
    elif case == "empty":
        p = tmp_path / "empty.db"
        p.write_bytes(b"")
        monkeypatch.setenv("KRONK_WORK_DB", str(p))
    elif case == "corrupt":
        p = tmp_path / "corrupt.db"
        p.write_bytes(b"not a database " * 300)
        monkeypatch.setenv("KRONK_WORK_DB", str(p))
    elif case == "newer":
        c = sqlite3.connect(db)
        c.execute("UPDATE meta SET value = '9' WHERE key = 'schema_version'")
        c.commit()
        c.close()
    elif case == "another repo":
        c = sqlite3.connect(db)
        c.execute("UPDATE meta SET value = '/some/other/repo' WHERE key = 'repo'")
        c.commit()
        c.close()
    elif case == "no item":
        c = sqlite3.connect(db)
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("UPDATE item SET id = 'M8' WHERE id = 'M9'")
        c.commit()
        c.close()
    return Path(os.environ["KRONK_WORK_DB"])


@pytest.mark.parametrize("case,words", [
    ("missing", "missing: no database at"), ("empty", "empty:"), ("corrupt", "corrupt:"),
    ("newer", "newer: "), ("another repo", "describes /some/other/repo"), ("no item", "no item M9"),
])
def test_m33_a_database_it_cannot_write_never_stops_the_agent_command(at, capsys, repo, brief, herdr, tmp_path,
                                                                      monkeypatch, case, words):
    db = break_db(case, repo, tmp_path, monkeypatch)
    before = db.read_bytes() if db.exists() else None
    rc, out, err = launch(at, capsys, repo, brief, "M9", "build", "--report", REPORT)
    assert rc == 0 and one_json(out)["agent_status"] == "working"
    assert err.count("\n") == 1 and err.startswith("agent.py launch: work state not recorded (M9 build round 1): ")
    assert words in err
    commit(repo, {REPORT: "# r\n"})
    herdr.set_status("m9-build", "idle")
    rc, out, err = wait(at, capsys, "--timeout", "30")
    assert rc == 0 and one_json(out)["done"] is True and "agent.py wait: work state not recorded" in err
    rc, out, err = run(at, capsys, "close", "m9-build")
    assert rc == 0 and one_json(out)["workspace"] == "closed" and "agent.py close: work state not recorded" in err
    assert (db.read_bytes() if db.exists() else None) == before
    if case == "missing":
        assert not db.parent.exists()


def test_m33_a_locked_database_costs_launch_wait_and_close_under_3_s(at, capsys, repo, brief, herdr, tmp_path):
    import time as _t
    t0 = _t.monotonic()
    assert launch(at, capsys, repo, brief, "M7", "build", "--no-report")[0] == 0     # the usual time, no write
    usual = _t.monotonic() - t0
    db = os.environ["KRONK_WORK_DB"]
    code = ("import sqlite3, time\n"
            f"c = sqlite3.connect({db!r}, isolation_level=None)\n"
            "c.execute('PRAGMA locking_mode=EXCLUSIVE'); c.execute('BEGIN EXCLUSIVE')\n"
            "c.execute(\"INSERT INTO meta VALUES ('lock', 'x')\")\n"
            "print('held', flush=True); time.sleep(30)\n")
    holder = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        t0 = _t.monotonic()
        rc, out, err = launch(at, capsys, repo, brief, "M9", "build", "--report", REPORT, "--parallel")
        took = _t.monotonic() - t0
        assert rc == 0 and "locked: still locked after 2 s" in err
        assert took < usual + 3
        commit(repo, {REPORT: "# r\n"})
        herdr.set_status("m9-build", "idle")
        t0 = _t.monotonic()
        assert wait(at, capsys, "--timeout", "30")[0] == 0
        assert _t.monotonic() - t0 < 3 + 1
        t0 = _t.monotonic()
        assert run(at, capsys, "close", "m9-build")[0] == 0
        assert _t.monotonic() - t0 < 3 + 1
    finally:
        holder.kill()
        holder.wait()


def test_m33_an_agent_launched_before_the_database_is_recorded_when_it_finishes(at, capsys, repo, brief, herdr,
                                                                              tmp_path, monkeypatch):
    """An agent launched by the old tool, or with no database yet: its state
    file has no step. status, once done, inserts the finished step with the
    launch time from the state file."""
    db = os.environ["KRONK_WORK_DB"]
    monkeypatch.setenv("KRONK_WORK_DB", str(tmp_path / "not-yet.db"))
    assert launch(at, capsys, repo, brief, "M9", "build", "--report", REPORT)[0] == 0
    monkeypatch.setenv("KRONK_WORK_DB", db)
    assert steps() == []
    commit(repo, {REPORT: "# r\n"})
    herdr.set_status("m9-build", "idle")
    rc, out, err = run(at, capsys, "status", "m9-build")
    assert rc == 0 and err == ""
    (s,) = steps()
    st = json.loads((herdr.state / "m9-build.json").read_text())
    assert s["state"] == "finished" and s["agent"] == "m9-build" and s["started_at"] is not None
    assert "report" in st and "work_step" not in st                       # the state file's shape is M28's


def test_m33_a_launch_in_a_temp_repo_leaves_the_live_path_alone(at, capsys, repo, brief, herdr, tmp_path,
                                                                 monkeypatch):
    """With KRONK_WORK_DB unset the database is HOME's; one that describes
    another repo (the real one) is not written by a launch in a temp repo."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("KRONK_WORK_DB")
    wk = _load("m28_work_tool_live", REPO / "scripts" / "work.py")
    live = home / ".local" / "state" / "kronk-work" / "work.db"
    assert wk.default_db() == live
    for argv in (["init"], ["add", "--id", "M9", "--title", "live"], ["start", "M9"]):
        assert wk.main(["--db", str(live), "--repo", str(REPO), *argv], out=io.StringIO()) == 0
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in live.parent.iterdir()}
    rc, out, err = launch(at, capsys, repo, brief, "M9", "build", "--report", REPORT)
    assert rc == 0 and f"this database describes {REPO}" in err
    commit(repo, {REPORT: "# r\n"})
    herdr.set_status("m9-build", "idle")
    assert run(at, capsys, "status", "m9-build")[0] == 0
    assert run(at, capsys, "close", "m9-build")[0] == 0
    after = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in live.parent.iterdir()}
    assert after == before
