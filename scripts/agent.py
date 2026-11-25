#!/usr/bin/env python3
"""Launch, check on, wait for, prompt and close a work-item agent in herdr
(work item M28, docs/features/agent-tool.md). Run on the host by the
coordinator, inside herdr. Each subcommand is one step; none of them runs
the whole life of an agent. Every answer is one line of JSON on stdout
(peek prints the pane as text); a refusal or a herdr error is one line on
stderr and exit 1.

    agent.py launch <item> <role> --brief FILE [--round N] [--report PATH]
                    [--model M] [--effort E] [--parallel] [--probe] [--repo DIR]
    agent.py status <name> [--report PATH]
    agent.py wait   <name> [--report PATH] [--interval S] [--timeout S] [--idle-limit S]
    agent.py peek   <name> [--lines N]
    agent.py prompt <name> --file FILE
    agent.py close  <name> [--force]
    agent.py list

Roles: build and fix run claude-opus-5-5 at effort high; test, review and
investigate run claude-fable-5-2 at effort medium; all with
--dangerously-skip-permissions. The agent is named <item>-<role>, with the
round number after the role from round 2 on (m28-test2), the name
scripts/work_export.py reads a stage from for /work. --probe names it
<item>-probe-<role> instead, which /work does not attribute to the item
(for trying the tool itself).

The prompt sent at launch is the brief followed by the "Guard rails in every
brief" section of docs/sprint/WORKFLOW.md, read whole at launch. There is no
way to leave the guard rails out.

State: one JSON file per agent under ~/.local/state/kronk-agents/
(KRONK_AGENT_STATE overrides), outside the repo, mode 600, and beside it
the assembled prompt (<name>.prompt.txt, mode 600), the one place the
brief's text is kept. close removes both.

Git is read with GIT_OPTIONAL_LOCKS=0 and --no-optional-locks, by read-only
commands only (rev-parse, rev-list, log, status, diff, cat-file): the tool
never writes in the repo and never takes .git/index.lock, so an agent's
commit is never refused because of it.

Exit codes: 0 success; 1 a refusal or a herdr error; 2 wait reached its time
limit; 3 wait found the agent blocked, gone, or idle for --idle-limit with
the work not done. Standard library only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
WORKFLOW = Path("docs/sprint/WORKFLOW.md")
GUARD_HEADING = "## Guard rails in every brief"

ROLES = {
    "build": ("claude-opus-5-5", "high"),
    "fix": ("claude-opus-5-5", "high"),
    "test": ("claude-fable-5-2", "medium"),
    "review": ("claude-fable-5-2", "medium"),
    "investigate": ("claude-fable-5-2", "medium"),
}
SKIP_PERMISSIONS = "--dangerously-skip-permissions"
ITEM_RE = re.compile(r"^[Mm](\d+)([A-Za-z]?)(?:-[Ss][Tt][Ee][Pp](\d+))?$")
HERDR_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")     # herdr's own rule for agent names
EFFORTS = ("low", "medium", "high", "xhigh", "max")
SETTLED = ("idle", "done")
PROMPT_MAX = 120_000          # bytes; one argv string may not exceed 128 KiB on Linux
GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
T_QUERY, T_CHANGE, T_START, T_PROMPT = 15, 20, 75, 30   # seconds for each kind of herdr call
START_TIMEOUT_MS = 60000
PROMPT_TIMEOUT_MS = 25000
SUBJECT_MAX = 120


class Refusal(Exception):
    """A refusal or a herdr error: one line on stderr, exit 1."""


class HerdrError(Refusal):
    def __init__(self, what: str, code: str, message: str = ""):
        self.what, self.code = what, code
        super().__init__(f"herdr {what}: {code}" + (f" ({message})" if message else ""))


# ═══════════════════════════════ processes ═════════════════════════════════

def run(cmd: list[str], cwd: Path | None, timeout: float, env: dict | None = None) -> tuple[int | None, str, str]:
    """(returncode, stdout, stderr or why). returncode None when the command
    could not start or did not answer within timeout (its process group is
    killed); the third value then says why."""
    full_env = dict(os.environ)
    full_env.update(env or {})
    try:
        p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=full_env, start_new_session=True)
    except FileNotFoundError:
        return None, "", "not found"
    except OSError as e:
        return None, "", f"could not start ({type(e).__name__})"
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            p.communicate(timeout=2)
        except Exception:
            pass
        return None, "", f"no answer within {timeout:g} s"
    return p.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def herdr(what: str, args: list[str], timeout: float, *, text: bool = False):
    """Run `herdr <args>`. Returns the "result" object (or the text, for
    agent read). Raises HerdrError with herdr's error code. herdr writes
    its answer to stdout and an error object to stderr."""
    rc, out, err = run(["herdr", *args], None, timeout)
    if rc is None:
        raise HerdrError(what, "unreachable", err)
    if text and rc == 0:
        return out
    data = None
    for stream in (out, err):
        try:
            data = json.loads(stream)
            break
        except ValueError:
            continue
    if data is None:
        raise HerdrError(what, "no JSON in the answer", f"exit {rc}")
    if not isinstance(data, dict):
        raise HerdrError(what, "not an object in the answer", f"exit {rc}")
    err = data.get("error")
    if err is not None or rc != 0:
        code = err.get("code") if isinstance(err, dict) else None
        code = code if isinstance(code, str) and re.fullmatch(r"[\w.-]{1,64}", code) else "error"
        raise HerdrError(what, code, f"exit {rc}")
    res = data.get("result")
    if not isinstance(res, dict):
        raise HerdrError(what, "no result in the answer", f"exit {rc}")
    return res


def herdr_agents() -> list[dict]:
    res = herdr("agent list", ["agent", "list"], T_QUERY)
    agents = res.get("agents")
    if not isinstance(agents, list):
        raise HerdrError("agent list", "no agents list in the answer")
    return [a for a in agents if isinstance(a, dict)]


def herdr_status(name: str) -> str:
    """herdr's agent_status for the name, or "gone" when herdr knows no live
    agent of that name."""
    try:
        res = herdr("agent get", ["agent", "get", name], T_QUERY)
    except HerdrError as e:
        if e.code == "agent_not_found":
            return "gone"
        raise
    a = res.get("agent")
    s = a.get("agent_status") if isinstance(a, dict) else None
    return s if isinstance(s, str) and s else "unknown"


# ══════════════════════════════════ git ════════════════════════════════════

def git(repo: Path, *args: str) -> tuple[int | None, str]:
    rc, out, _ = run(["git", "--no-optional-locks", "-C", str(repo), *args], None, T_CHANGE, GIT_ENV)
    rc = rc if rc is not None else -1
    return rc, out


def git_head(repo: Path) -> str | None:
    rc, out = git(repo, "rev-parse", "--verify", "-q", "HEAD^{commit}")
    return out.strip() if rc == 0 and out.strip() else None


def work_state(repo: Path, launch_head: str | None, report: str | None, item: str) -> dict:
    """What git says about the agent's work, read only and lock-free."""
    out = {"commits_since": None, "last_commit": None, "tree_clean": None,
           "report": None, "verdict": None}
    if launch_head:
        rc, txt = git(repo, "rev-list", "--count", f"{launch_head}..HEAD")
        if rc == 0 and txt.strip().isdigit():
            out["commits_since"] = int(txt.strip())
    rc, txt = git(repo, "log", "-1", "--format=%h %s", "HEAD")
    if rc == 0 and txt.strip():
        line = txt.strip().splitlines()[0]
        out["last_commit"] = line if len(line) <= SUBJECT_MAX else line[:SUBJECT_MAX - 1] + "…"
    rc, txt = git(repo, "status", "--porcelain=v1", "--untracked-files=normal")
    if rc == 0:
        out["tree_clean"] = txt.strip() == ""
    if report:
        committed = False
        rc_e, _ = git(repo, "cat-file", "-e", f"HEAD:{report}")
        if rc_e == 0 and launch_head:
            rc_d, _ = git(repo, "diff", "--quiet", launch_head, "HEAD", "--", report)
            committed = rc_d == 1            # 1: it differs from launch (added or changed)
        out["report"] = {"path": report, "committed": committed}
        if rc_e == 0:
            out["verdict"] = report_verdict(repo, report, item)
    return out


def report_verdict(repo: Path, report: str, item: str) -> str | None:
    """The committed report's verdict word, read by work_export.py's own
    rule (the first 40 lines, anchored forms), so /work and this agree."""
    rc, txt = git(repo, "cat-file", "-p", f"HEAD:{report}")
    if rc != 0:
        return None
    try:
        we = work_export()
    except Refusal:
        return None
    found = we.verdicts(txt[:65536].splitlines(), item)
    own = [v for v in found if v["subject"] == "item"] or found
    return own[0]["verdict"] if own else None


# ══════════════════════════════ names, files ═══════════════════════════════

_WE = None


def work_export():
    """scripts/work_export.py, the /work exporter, loaded from beside this
    file: its name parser decides whether /work shows an agent on its item."""
    global _WE
    if _WE is None:
        path = HERE / "work_export.py"
        try:
            spec = importlib.util.spec_from_file_location("kronk_work_export_for_agent_tool", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            raise Refusal(f"cannot load {path} to check the name against /work's parser ({type(e).__name__})")
        _WE = mod
    return _WE


def item_id(item: str) -> tuple[str, str]:
    """('M28', 'm28') from 'M28' or 'm28'; ('M21-step2', 'm21-step2')."""
    m = ITEM_RE.match(item.strip())
    if not m:
        raise Refusal(f"item {item!r} is not an item id (M28, M4b, M21-step2)")
    canon = f"M{m.group(1)}{m.group(2).lower()}" + (f"-step{m.group(3)}" if m.group(3) else "")
    return canon, canon.lower()


def agent_name(item: str, role: str, rnd: int, probe: bool) -> tuple[str, str]:
    """(canonical item id, agent name), refused when the role is unknown, the
    round is below 1, the name breaks herdr's rule, or /work's own parser
    would not read it back as (item, role)."""
    if role not in ROLES:
        raise Refusal(f"role {role!r} is not one of: {', '.join(ROLES)}")
    if rnd < 1:
        raise Refusal(f"--round {rnd}: rounds start at 1 (the number is added from 2 on)")
    canon, low = item_id(item)
    name = f"{low}-{'probe-' if probe else ''}{role}{rnd if rnd >= 2 else ''}"
    if not HERDR_NAME_RE.match(name):
        raise Refusal(f"name {name!r} breaks herdr's rule [a-z][a-z0-9_-]{{0,31}}")
    we = work_export()
    if not probe and role in we.ROLE_STAGE and we.agent_for(name) != (canon, role):
        raise Refusal(f"name {name!r} would not show on /work as {canon} {role}")
    return canon, name


def state_dir() -> Path:
    d = os.environ.get("KRONK_AGENT_STATE")
    return Path(d) if d else Path.home() / ".local" / "state" / "kronk-agents"


def state_path(name: str) -> Path:
    if not HERDR_NAME_RE.match(name):
        raise Refusal(f"{name!r} is not an agent name")
    return state_dir() / f"{name}.json"


def prompt_path(name: str) -> Path:
    return state_dir() / f"{name}.prompt.txt"


def write_private(path: Path, text: str) -> None:
    """Write text to path, mode 600, by a temp file and a rename."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_state(name: str) -> dict:
    p = state_path(name)
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Refusal(f"no agent {name!r} in {state_dir()} (`agent.py list` shows the known ones)") from None
    except (OSError, ValueError) as e:
        raise Refusal(f"state file {p} cannot be read ({type(e).__name__})") from None
    if not isinstance(st, dict) or st.get("name") != name:
        raise Refusal(f"state file {p} is not this tool's")
    return st


def save_state(st: dict) -> None:
    write_private(state_path(st["name"]), json.dumps(st, indent=1, sort_keys=True) + "\n")


def all_states() -> list[dict]:
    d = state_dir()
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(st, dict) and isinstance(st.get("name"), str) and p.name == f"{st['name']}.json":
            out.append(st)
    return out


def repo_relative(path: str | None, repo: Path) -> str | None:
    if path is None:
        return None
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(repo.resolve())
        except ValueError:
            raise Refusal(f"--report {path}: not inside the repo {repo}") from None
    if not p.parts or ".." in p.parts:
        raise Refusal(f"--report {path}: give a path inside the repo, relative to it")
    return p.as_posix()


def guard_rails(repo: Path) -> str:
    """The "Guard rails in every brief" section of WORKFLOW.md, whole: its
    heading line through the line before the next ## heading."""
    path = repo / WORKFLOW
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        raise Refusal(f"guard rails: {path} cannot be read ({type(e).__name__}); not launched") from None
    try:
        start = next(i for i, l in enumerate(lines) if l.rstrip() == GUARD_HEADING)
    except StopIteration:
        raise Refusal(f"guard rails: no '{GUARD_HEADING}' section in {path}; not launched") from None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    section = "\n".join(lines[start:end]).strip()
    if not section[len(GUARD_HEADING):].strip():
        raise Refusal(f"guard rails: the '{GUARD_HEADING}' section of {path} is empty; not launched")
    return section


def assemble(brief: str, rails: str) -> str:
    text = brief.strip() + "\n\n" + rails + "\n"
    if text.startswith("-"):
        text = "Brief:\n\n" + text      # herdr would read a leading dash as an option
    return text


def minutes_since(t: float | None) -> float | None:
    return None if t is None else round(max(0.0, time.time() - t) / 60, 1)


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, separators=(", ", ": ")), flush=True)


# ═══════════════════════════════ prompting ═════════════════════════════════

def send(name: str, text: str) -> tuple[str, str]:
    """Send text; confirm the agent moved to working (or blocked, which is a
    turn under way that stopped at a question); send once more if it did
    not. Returns (how, agent_status): how is "once" or "twice". herdr's
    timeout and stalled answers are swallowed (the status decides); any
    other herdr error is raised."""
    for attempt in ("once", "twice"):
        try:
            herdr("agent prompt", ["agent", "prompt", name, text, "--wait", "--until", "working",
                                   "--until", "blocked", "--timeout", str(PROMPT_TIMEOUT_MS)], T_PROMPT)
        except HerdrError as e:
            if e.code not in ("timeout", "agent_prompt_stalled"):
                raise HerdrError("agent prompt", e.code) from None     # no message: it could echo text
        status = herdr_status(name)
        if status in ("working", "blocked"):
            return attempt, status
        if status == "gone":
            raise Refusal(f"agent {name} is gone after the prompt")
    return "twice", status


# ═══════════════════════════════ subcommands ═══════════════════════════════

def cmd_launch(a) -> int:
    canon, name = agent_name(a.item, a.role, a.round, a.probe)
    model, effort = ROLES[a.role]
    model, effort = a.model or model, a.effort or effort
    if effort not in EFFORTS:
        raise Refusal(f"--effort {effort!r} is not one of: {', '.join(EFFORTS)}")
    if os.environ.get("HERDR_ENV") != "1":
        raise Refusal("not inside herdr (HERDR_ENV is not 1); not launched")
    brief_path = Path(a.brief)
    try:
        brief = brief_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise Refusal(f"brief {brief_path} does not exist; not launched") from None
    except (OSError, UnicodeDecodeError) as e:
        raise Refusal(f"brief {brief_path} cannot be read ({type(e).__name__}); not launched") from None
    if not brief.strip():
        raise Refusal(f"brief {brief_path} is empty; not launched")
    repo = Path(a.repo).resolve()
    rails = guard_rails(repo)
    text = assemble(brief, rails)
    if len(text.encode("utf-8")) > PROMPT_MAX:
        raise Refusal(f"brief and guard rails come to more than {PROMPT_MAX} bytes; not launched")
    report = repo_relative(a.report, repo)
    head = git_head(repo)
    if head is None:
        raise Refusal(f"{repo} is not a git work tree with a commit; not launched")

    if state_path(name).exists():
        raise Refusal(f"{name} is already in {state_dir()}: `agent.py close {name}` first; not launched")
    agents = herdr_agents()
    if any(x.get("name") == name for x in agents):
        raise Refusal(f"an agent named {name} exists in herdr; not launched")
    spaces = herdr("workspace list", ["workspace", "list"], T_QUERY).get("workspaces")
    if any(isinstance(w, dict) and w.get("label") == name for w in spaces or []):
        raise Refusal(f"a workspace labelled {name} exists in herdr; not launched")
    if not a.parallel:
        live = {x.get("name"): x.get("agent_status") for x in agents}
        busy = sorted(s["name"] for s in all_states() if live.get(s["name"]) == "working")
        if busy:
            raise Refusal(f"another agent the tool launched is working ({', '.join(busy)}); "
                          f"--parallel to run beside it; not launched")

    res = herdr("workspace create", ["workspace", "create", "--label", name, "--cwd", str(repo),
                                     "--no-focus"], T_CHANGE)
    try:
        ws = res["workspace"]["workspace_id"]
        pane = res["root_pane"]["pane_id"]
        assert isinstance(ws, str) and isinstance(pane, str)
    except (KeyError, TypeError, AssertionError):
        raise Refusal("herdr workspace create: no workspace or root pane id in the answer; "
                      "check `herdr workspace list` for a workspace labelled " + name) from None
    st = {"name": name, "item": canon, "role": a.role, "round": a.round, "probe": a.probe,
          "workspace_id": ws, "pane_id": pane, "model": model, "effort": effort,
          "repo": str(repo), "head": head, "launched": time.time(),
          "launched_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "report": report}
    save_state(st)
    try:
        herdr("agent start", ["agent", "start", name, "--kind", "claude", "--pane", pane,
                              "--timeout", str(START_TIMEOUT_MS), "--", "--model", model,
                              "--effort", effort, SKIP_PERMISSIONS], T_START)
    except Refusal as e:
        try:
            herdr("workspace close", ["workspace", "close", ws], T_CHANGE)
            state_path(name).unlink(missing_ok=True)
            undone = f"workspace {ws} closed"
        except Refusal:
            undone = f"workspace {ws} left open, state kept: `agent.py close {name} --force`"
        raise Refusal(f"{e}; {undone}") from None
    write_private(prompt_path(name), text)
    try:
        how, status = send(name, text)
    except Refusal as e:
        raise Refusal(f"{e}; the agent is left open, state kept (peek, prompt or close it)") from None
    out = {"name": name, "item": canon, "role": a.role, "workspace_id": ws, "pane_id": pane,
           "model": model, "effort": effort, "head": head[:7], "report": report,
           "prompt_file": str(prompt_path(name)), "sent": how, "agent_status": status}
    emit(out)
    if status not in ("working", "blocked"):
        raise Refusal(f"{name} is {status} after the prompt was sent twice; the agent is left open "
                      f"(peek, prompt or close it)")
    return 0


def status_of(st: dict) -> dict:
    s = herdr_status(st["name"])
    w = work_state(Path(st["repo"]), st.get("head"), st.get("report"), st.get("item") or "")
    rep = w["report"]
    done = (s in SETTLED and w["tree_clean"] is True and (rep is None or rep["committed"]))
    return {"name": st["name"], "agent_status": s, "minutes": minutes_since(st.get("launched")),
            "commits_since": w["commits_since"], "last_commit": w["last_commit"],
            "tree_clean": w["tree_clean"], "report": rep, "done": done, "verdict": w["verdict"]}


def with_report(st: dict, report: str | None) -> dict:
    if report is not None:
        rel = repo_relative(report, Path(st["repo"]))
        if rel != st.get("report"):
            st["report"] = rel
            save_state(st)
    return st


def cmd_status(a) -> int:
    st = with_report(load_state(a.name), a.report)
    emit(status_of(st))
    return 0


def cmd_wait(a) -> int:
    st = with_report(load_state(a.name), a.report)
    if a.interval <= 0 or a.timeout < 0 or a.idle_limit < 0:
        raise Refusal("--interval must be above 0; --timeout and --idle-limit 0 or more")
    start = time.monotonic()
    idle_since = None
    errors = 0
    last = None
    while True:
        try:
            last = status_of(st)
            errors = 0
        except HerdrError:
            errors += 1
            if errors >= 3:                    # three polls in a row: herdr is not answering
                raise
            last = None
        now = time.monotonic()
        if last is not None:
            s = last["agent_status"]
            if last["done"]:
                emit(last)
                return 0
            if s in ("blocked", "gone"):
                emit(last)
                print(f"agent.py wait: {a.name} is {s}", file=sys.stderr)
                return 3
            if s in SETTLED:
                idle_since = idle_since if idle_since is not None else now
                if now - idle_since >= a.idle_limit:
                    emit(last)
                    print(f"agent.py wait: {a.name} has been {s} for {now - idle_since:.0f} s "
                          f"with the work not done", file=sys.stderr)
                    return 3
            else:
                idle_since = None
        if now - start >= a.timeout:
            emit(last if last is not None else status_of(st))
            print(f"agent.py wait: {a.name}: time limit of {a.timeout:g} s reached", file=sys.stderr)
            return 2
        time.sleep(max(0.0, min(a.interval, a.timeout - (now - start))))


def cmd_peek(a) -> int:
    load_state(a.name)
    if a.lines < 1:
        raise Refusal("--lines must be 1 or more")
    try:
        text = herdr("agent read", ["agent", "read", a.name, "--source", "recent-unwrapped",
                                    "--lines", str(a.lines), "--format", "text"], T_QUERY, text=True)
    except HerdrError as e:
        if e.code == "agent_not_found":
            raise Refusal(f"agent {a.name} is gone") from None
        raise
    lines = text.splitlines()[-a.lines:]
    sys.stdout.write("\n".join(lines) + ("\n" if lines else ""))
    return 0


def cmd_prompt(a) -> int:
    load_state(a.name)
    p = Path(a.file)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise Refusal(f"file {p} does not exist; nothing sent") from None
    except (OSError, UnicodeDecodeError) as e:
        raise Refusal(f"file {p} cannot be read ({type(e).__name__}); nothing sent") from None
    if not text.strip():
        raise Refusal(f"file {p} is empty; nothing sent")
    text = text.strip() + "\n"
    if text.startswith("-"):
        text = "Prompt:\n\n" + text
    if len(text.encode("utf-8")) > PROMPT_MAX:
        raise Refusal(f"file {p} is more than {PROMPT_MAX} bytes; nothing sent")
    before = herdr_status(a.name)
    if before == "gone":
        raise Refusal(f"agent {a.name} is gone; nothing sent")
    if before == "blocked":
        raise Refusal(f"agent {a.name} is blocked at a question; peek it; nothing sent")
    how, status = send(a.name, text)
    emit({"name": a.name, "before": before, "sent": how, "agent_status": status})
    if status not in ("working", "blocked"):
        raise Refusal(f"{a.name} is {status} after the prompt was sent twice")
    return 0


def cmd_close(a) -> int:
    st = load_state(a.name)
    status = herdr_status(a.name)
    if status == "working" and not a.force:
        raise Refusal(f"{a.name} is working; --force to close it anyway; not closed")
    ws = st["workspace_id"]
    try:
        herdr("workspace close", ["workspace", "close", ws], T_CHANGE)
        how = "closed"
    except HerdrError as e:
        if e.code != "workspace_not_found":
            raise
        how = "already gone"
    try:
        herdr("workspace get", ["workspace", "get", ws], T_QUERY)
        raise Refusal(f"workspace {ws} is still there after close; state kept")
    except HerdrError as e:
        if e.code != "workspace_not_found":
            raise
    state_path(a.name).unlink(missing_ok=True)
    prompt_path(a.name).unlink(missing_ok=True)
    emit({"name": a.name, "workspace_id": ws, "workspace": how, "agent_status_before": status})
    return 0


def cmd_list(a) -> int:
    states = all_states()
    err = None
    live: dict = {}
    if states:
        try:
            live = {x.get("name"): x.get("agent_status") for x in herdr_agents()}
        except HerdrError as e:
            err = e
    for st in states:
        emit({"name": st["name"], "item": st.get("item"), "role": st.get("role"),
              "workspace_id": st.get("workspace_id"),
              "agent_status": None if err else (live.get(st["name"]) or "gone"),
              "minutes": minutes_since(st.get("launched")), "report": st.get("report")})
    if err:
        raise err
    return 0


# ══════════════════════════════════ main ═══════════════════════════════════

class Parser(argparse.ArgumentParser):
    def error(self, message):                 # one line, exit 1, as every other refusal
        raise Refusal(message)


def parser() -> argparse.ArgumentParser:
    ap = Parser(prog="agent.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True, parser_class=Parser)

    p = sub.add_parser("launch", help="workspace, agent, prompt; confirms the agent is working")
    p.add_argument("item")
    p.add_argument("role")
    p.add_argument("--brief", required=True)
    p.add_argument("--round", type=int, default=1)
    p.add_argument("--report")
    p.add_argument("--model")
    p.add_argument("--effort")
    p.add_argument("--parallel", action="store_true", help="launch beside another working agent")
    p.add_argument("--probe", action="store_true", help="name it <item>-probe-<role> (not shown on the item)")
    p.add_argument("--repo", default=str(REPO), help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_launch)

    p = sub.add_parser("status", help="one JSON line: where the agent and its work stand")
    p.add_argument("name")
    p.add_argument("--report")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("wait", help="block until done (0), time limit (2), blocked/gone/idle (3)")
    p.add_argument("name")
    p.add_argument("--report")
    p.add_argument("--interval", type=float, default=20.0, help="seconds between polls (20)")
    p.add_argument("--timeout", type=float, default=3 * 3600.0, help="time limit in seconds (10800)")
    p.add_argument("--idle-limit", type=float, default=300.0,
                   help="seconds idle with the work not done before exit 3 (300)")
    p.set_defaults(fn=cmd_wait)

    p = sub.add_parser("peek", help="the last lines of the agent's pane, as text")
    p.add_argument("name")
    p.add_argument("--lines", type=int, default=40)
    p.set_defaults(fn=cmd_peek)

    p = sub.add_parser("prompt", help="a follow-up prompt from a file; confirms it arrived")
    p.add_argument("name")
    p.add_argument("--file", required=True)
    p.set_defaults(fn=cmd_prompt)

    p = sub.add_parser("close", help="close the workspace the tool created")
    p.add_argument("name")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_close)

    p = sub.add_parser("list", help="every agent the tool launched and still knows")
    p.set_defaults(fn=cmd_list)
    return ap


def main(argv=None) -> int:
    cmd = "agent.py"
    try:
        a = parser().parse_args(argv)
        cmd = f"agent.py {a.cmd}"
        return a.fn(a)
    except Refusal as e:
        print(f"{cmd}: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"{cmd}: interrupted", file=sys.stderr)
        return 1
    except Exception as e:                     # never a traceback to the coordinator
        print(f"{cmd}: internal error ({type(e).__name__}: {str(e)[:200]})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
