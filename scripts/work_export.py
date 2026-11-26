#!/usr/bin/env python3
"""Export the state of the work items for /work (work items M27, M34). Runs
ON THE HOST (systemd user timer kronk-work-export.timer, every 60 s): the
production orchestrator runs from a release snapshot and can see neither
the working tree nor the work-state database, so this file is the bridge,
as gpu_mem.json and llama_props.json are for /resources.

What it reads, and nothing else (M34, docs/sprint/M34-criteria.md,
"Revised after the review"):

- the dump of the work-state database, <db>.dump.sql, which scripts/work.py
  rewrites after every committed write: loaded into an in-memory database.
  The database itself is never opened (a mode=ro reader of a WAL file makes
  and touches its -wal and -shm), and no other file beside it;
- scripts/work.py, beside this file, loaded inside collect() for its
  read-only overview() and queue_rows(): the stage, the queue and the
  counts are the ones `work.py resume` prints;
- ROADMAP.md, and the first 40 lines of each item's criteria file (its
  handoff line and Source: paragraph), from the tree;
- git, with GIT_OPTIONAL_LOCKS=0, by three lock-free commands: `git
  rev-parse` (branch and short HEAD), `git for-each-ref refs/tags`, `git
  rev-list HEAD` (a step committed off HEAD's history is not counted, as in
  resume). Never `status`, `diff`, `add` or `stash`: `git status` takes
  .git/index.lock, and an agent's commit in that instant fails;
- `herdr agent list`, once, with a 5 s limit.

What it writes: an outline. From the rows: ids, titles, state, tier, queue
place and what holds an item, next steps and wait notes (active, queued
and held items only), steps (kind, round, name, report file name, agent,
who, state, times, verdict, grading), promotions (tag, time, deployed,
the path of the checks; the label of one with no item), and counts of open
leftovers and decisions by owner; the decisions owed to Drew by id, item,
date and whether they block, never their text. Never the log, meta.repo,
a step's model or note, a matter's text, source or closing words, or an
SQLite error's text. From files: handoff lines and Source: paragraphs, the
open ROADMAP entries whole and, for Shipped, the title and leading *( ... )*.
From herdr: agents' names and statuses (never terminal_title,
agent_session or cwd; an agent outside this repo is counted, not named).

A dump that is missing, empty, does not load, is of a newer schema or
describes another repo still gives a file: it says which (`database`), holds
no item, and still holds the ROADMAP part; the exporter exits 0. Nothing is
invented from files.

The old reader of docs/sprint/PROMOTIONS.md and the step files
(collect_legacy() and the parsers it feeds) is import-only: work.py's
import-legacy and check, and the M27 tests, use it. Standard library only,
no network.

The keys are read by every deployed orchestrator: add keys, never rename
or repurpose one, and raise SCHEMA when keys are added. A schema-1 key
with no source left carries null, [] or false (docs/features/work-page.md).

    work_export.py [--repo DIR] [--db PATH] [--out FILE ...] [--print]

--db PATH reads PATH.dump.sql (default: KRONK_WORK_DB, else
~/.local/state/kronk-work/work.db, as work.py). Each --out file is written
atomically (temp file, rename). Default: the repo's data/work.json and
data/dev/work.json (production's and dev's orchestrators read
/data/work.json). See docs/features/work-page.md.
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCHEMA = 2
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HEAD_LINES = 40
HERDR_TIMEOUT_S = 5
GIT_TIMEOUT_S = 5
TITLE_MAX = 300
HANDOFF_MAX = 600
ENTRY_MAX = 4000
FOLD_MAX = 320          # a folded backlog entry: title, lead and first sentence, at most this
TEMP_MAX_AGE_S = 600    # a temp file of ours older than this was left by a kill: removed
GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}

ID = r"M\d+[a-z]?(?:-step\d+)?"
FILE_RE = re.compile(rf"^({ID})-(.+)\.md$")
KNOWN_SUFFIX = re.compile(r"^(criteria|review|build|e2e(\d+)?|fix(\d+))$")
NOT_ITEM_FILES = {"PROMOTIONS.md", "WORKFLOW.md"}

# ── Verdicts: anchored at the start of a line, in the first 40 lines ────────
VERDICT_WORD = r"(PASSES|FAILS|NOT DETERMINED)"
VERDICT_FORMS = [
    re.compile(rf"^Verdict:\s*{VERDICT_WORD}(?![\w])"),
    re.compile(rf"^\*\*Verdict: ITEM {VERDICT_WORD}\.\*\*"),
    re.compile(rf"^\*\*(RESTORE) {VERDICT_WORD}\.\*\*"),
    re.compile(rf"^\*\*([Mm]\d+[A-Za-z]?(?: [Ss][Tt][Ee][Pp] \d+)?) {VERDICT_WORD}\.\*\*"),
]

# ── Stage: the table of M27-criteria.md, as data ──────────────────────────
AGENT_RE = re.compile(r"^(m\d+[a-z]?(?:-step\d+)?)-(review|build|test|fix)\d*$")
ROLE_STAGE = {"review": "reviewing", "build": "implementing", "test": "testing", "fix": "fixing"}
STAGE_WORDS = {
    "reviewing": "reviewing", "implementing": "implementing", "testing": "testing", "fixing": "fixing",
    "planning": "planning", "waiting": "waiting for the coordinator", "to_promote": "to promote",
    "promoting": "promoting", "promoted": "promoted",
}
# Last committed step file, no agent working or blocked: (stage, detail).
FILE_STAGE = {
    "criteria": ("planning", "criteria written"),
    "review": ("planning", "review in; criteria being revised"),
    "build": ("waiting", "build in, not tested"),
    "fix": ("waiting", "fix {n} in, not retested"),
    "test": ("waiting", "test {n}: {verdict}"),
    "test_passes": ("to_promote", "test {n}: PASSES"),
    "other": ("waiting", "last file {file}"),
    "none": ("planning", "files written, not committed"),
}
DECIDING = ("working", "blocked")      # the only statuses that decide a stage
COORDINATOR_WORD = {"working": "working", "blocked": "blocked", "idle": "idle", "done": "idle",
                    "unknown": "unknown"}
# The state line's word for the coordinator ("shown"): the four the page uses.
COORDINATOR_SHOWN = ("working", "waiting on an agent", "idle", "not known")

# ── PROMOTIONS.md ───────────────────────────────────────────────────────────
LABEL_ID = re.compile(r"(?<![\w-])M\d+[a-z]?(?: step \d+|-step\d+)?(?![\w-])")
HEADING_ID = re.compile(r"^(#{2,3}) (M\d+[a-z]?(?: step \d+|-step\d+)?)(?![\w])")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
FOR_DREW = re.compile(r"\b[Ff]or\s+Drew\b(?!['’]s)")
TAG_LINE = re.compile(r"^- Tag `([^`]+)`(.*)")

# ── ROADMAP.md ──────────────────────────────────────────────────────────────
OPEN_SECTIONS = ("Now", "Next", "Later", "Chores")
LINK_RE = re.compile(r"\b(?:work item|sprint item|sprint)\s+(M\d+[a-z]?)((?:\s*(?:,|and)\s*M\d+[a-z]?)*)", re.I)


# ═════════════════════════════════ helpers ═════════════════════════════════

def norm_id(s: str) -> str:
    """'M21 step 1', 'm21-STEP1' -> 'M21-step1'; 'M4B' -> 'M4b'."""
    m = re.match(r"^[Mm](\d+)([A-Za-z]?)(?:[ -]?[Ss][Tt][Ee][Pp] ?(\d+))?$", s.strip())
    if not m:
        return s.strip()
    out = f"M{m.group(1)}{m.group(2).lower()}"
    return out + (f"-step{m.group(3)}" if m.group(3) else "")


def id_key(item_id: str):
    m = re.match(r"^M(\d+)([a-z]?)(?:-step(\d+))?$", item_id)
    if not m:
        return (10**9, item_id, 0)
    return (int(m.group(1)), m.group(2), int(m.group(3) or 0))


def read_head(path: Path, n: int = HEAD_LINES) -> tuple[list[str] | None, str | None]:
    """The first n lines of a file as text, or (None, why)."""
    try:
        with open(path, "rb") as f:
            raw = f.read(256 * 1024)
    except OSError as e:
        return None, f"cannot be read: {type(e).__name__}"
    if not raw.strip():
        return None, "empty"
    if b"\0" in raw:
        return None, "binary"
    text = raw.decode("utf-8", "replace")
    return text.splitlines()[:n], None


def read_text(path: Path) -> tuple[str | None, str | None]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, "missing"
    except OSError as e:
        return None, f"cannot be read: {type(e).__name__}"
    if b"\0" in raw:
        return None, "binary"
    return raw.decode("utf-8", "replace"), None


def clip(s: str | None, n: int) -> str | None:
    if s is None:
        return None
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def joined(lines: list[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(l.strip() for l in lines)).strip()


def run(cmd: list[str], cwd: Path, timeout: float, env: dict | None = None) -> tuple[int | None, str, str]:
    """Run a command in its own process group; on timeout kill the group.
    Returns (returncode or None on timeout/absent, stdout, why)."""
    full_env = dict(os.environ)
    full_env.update(env or {})
    try:
        p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=full_env, start_new_session=True)
    except FileNotFoundError:
        return None, "", "not found"
    except OSError as e:
        return None, "", f"could not start: {type(e).__name__}"
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            p.communicate(timeout=1)
        except Exception:
            pass
        return None, "", f"no answer within {timeout:g} s"
    return p.returncode, out.decode("utf-8", "replace"), ""


# ═══════════════════════════════════ git ═══════════════════════════════════

def git(repo: Path, *args: str) -> tuple[int | None, str, str]:
    return run(["git", "--no-optional-locks", *args], repo, GIT_TIMEOUT_S, GIT_ENV)


def git_first_commits(repo: Path) -> tuple[dict, str | None]:
    """{file name: (short sha, author date ISO, commit index)} for every
    file in docs/sprint: its first commit, in commit order."""
    rc, out, why = git(repo, "log", "--no-renames", "--diff-filter=A", "--name-only",
                       "--format=@%H%x09%aI", "--reverse", "--", "docs/sprint")
    if rc != 0:
        return {}, f"git log failed: {why or f'exit {rc}'}"
    first: dict = {}
    sha = date = None
    n = -1
    for line in out.splitlines():
        if line.startswith("@"):
            sha, _, date = line[1:].partition("\t")
            n += 1
        elif line.strip() and sha:
            name = line.strip()
            if name.startswith("docs/sprint/") and "/" not in name[len("docs/sprint/"):]:
                first.setdefault(name[len("docs/sprint/"):], (sha[:7], date, n))
    return first, None


def git_tags(repo: Path) -> tuple[set, str | None]:
    rc, out, why = git(repo, "for-each-ref", "refs/tags", "--format=%(refname:short)")
    if rc != 0:
        return set(), f"git for-each-ref failed: {why or f'exit {rc}'}"
    return {l.strip() for l in out.splitlines() if l.strip()}, None


def git_head(repo: Path) -> dict:
    head = {"branch": None, "head": None, "detached": None, "rebase": False, "error": None}
    rc, out, why = git(repo, "rev-parse", "--absolute-git-dir", "--symbolic-full-name", "HEAD")
    if rc != 0:
        head["error"] = f"git rev-parse failed: {why or f'exit {rc}'}"
        return head
    lines = out.splitlines()
    gitdir = Path(lines[0]) if lines else None
    ref = lines[1] if len(lines) > 1 else ""
    if ref.startswith("refs/heads/"):
        head["branch"], head["detached"] = ref[len("refs/heads/"):], False
    else:
        head["branch"], head["detached"] = None, True
    if gitdir:
        head["rebase"] = (gitdir / "rebase-merge").is_dir() or (gitdir / "rebase-apply").is_dir()
    rc, out, why = git(repo, "rev-parse", "--short", "HEAD")
    if rc == 0:
        head["head"] = out.strip() or None
    else:
        head["error"] = f"git rev-parse --short failed: {why or f'exit {rc}'}"
    return head


# ══════════════════════════════════ herdr ══════════════════════════════════

def herdr_agents(repo: Path) -> dict:
    """{"known", "seen", "reason", "agents": [{name, status}], "elsewhere",
    "skipped"}. Only name and agent_status leave this function, and an
    agent outside the repo loses its name. An agent described in a shape
    this cannot read (not an object, a status that is not a string, a cwd
    that is no path) is counted in "skipped" and left out."""
    out = {"known": False, "seen": None, "reason": None, "agents": [], "elsewhere": 0, "skipped": 0}
    exe = shutil.which("herdr")
    if not exe:
        out["reason"] = "herdr is not on PATH"
        return out
    rc, text, why = run([exe, "agent", "list"], repo, HERDR_TIMEOUT_S)
    if rc is None:
        out["reason"] = f"herdr agent list: {why}"
        return out
    try:
        data = json.loads(text)
    except ValueError:
        out["reason"] = f"herdr agent list answered no JSON (exit {rc})"
        return out
    if not isinstance(data, dict):
        out["reason"] = f"herdr agent list answered {type(data).__name__}, not an object (exit {rc})"
        return out
    err = data.get("error")
    if err is not None or rc != 0:
        code = err.get("code") if isinstance(err, dict) else None
        code = code if isinstance(code, str) and re.fullmatch(r"[\w.-]{1,64}", code) else "error"
        out["reason"] = f"herdr agent list: {code} (exit {rc})"
        return out
    agents = (data.get("result") or {}).get("agents") if isinstance(data.get("result"), dict) else None
    if not isinstance(agents, list):
        out["reason"] = "herdr agent list: no agents list in the answer"
        return out
    root = os.path.realpath(repo)
    out["known"], out["seen"] = True, len(agents)
    for a in agents:
        try:
            where, rec = herdr_agent(a, root)
        except Exception:                   # a shape not foreseen: counted, never fatal
            where, rec = "skipped", None
        if where == "repo":
            out["agents"].append(rec)
        else:
            out[where] += 1
    return out


def herdr_agent(a, root: str) -> tuple[str, dict | None]:
    """("repo", {name, status}), ("elsewhere", None) or ("skipped", None)
    for one entry of herdr's list."""
    if not isinstance(a, dict):
        return "skipped", None
    status = a.get("agent_status")
    if status is not None and not isinstance(status, str):
        return "skipped", None
    cwd = a.get("cwd")
    if isinstance(cwd, str) and "\0" in cwd:
        return "skipped", None
    if not isinstance(cwd, str):
        return "elsewhere", None
    real = os.path.realpath(cwd)
    if not (real == root or real.startswith(root + os.sep)):
        return "elsewhere", None
    name = a.get("name") if isinstance(a.get("name"), str) and a.get("name") else None
    return "repo", {"name": clip(name, 80), "status": status if status in COORDINATOR_WORD else "unknown"}


def coordinator_shown(coord: dict, agents: list[dict]) -> str:
    """The state line's word, one of COORDINATOR_SHOWN: "working" when the
    coordinator's pane works; "waiting on an agent" when it does not and a
    named agent in this repo does (an item's agent, or an investigator);
    "idle" only when nothing here is working; "not known" when herdr is
    not reachable or the coordinator's pane cannot be told (or herdr says
    its status is unknown)."""
    s = coord.get("status")
    if s is None or s == "unknown":
        return "not known"
    if s == "working":
        return "working"
    if any(a["name"] is not None and a["status"] == "working" for a in agents):
        return "waiting on an agent"
    return "idle"


def coordinator(h: dict) -> dict:
    """The coordinator's pane carries no agent name (agents are named
    <item>-<role> by the coordinator; its own pane is not). herdr's agent
    list does not carry the workspace label, so the coordinator is the one
    unnamed agent in this repo."""
    if not h["known"]:
        return {"status": None, "word": "not known", "reason": h["reason"]}
    unnamed = [a for a in h["agents"] if a["name"] is None]
    if len(unnamed) == 1:
        s = unnamed[0]["status"]
        return {"status": s, "word": COORDINATOR_WORD.get(s, "unknown"), "reason": None}
    if not unnamed:
        return {"status": None, "word": "not seen",
                "reason": "no unnamed agent in this repo (the coordinator's pane is the unnamed one)"}
    return {"status": None, "word": "not known",
            "reason": f"{len(unnamed)} unnamed agents in this repo; the coordinator cannot be told apart"}


# ═══════════════════════════════ item files ════════════════════════════════

def step_kind(suffix: str) -> tuple[str, int | None]:
    """('criteria'|'review'|'build'|'test'|'fix'|'other', number)."""
    m = KNOWN_SUFFIX.match(suffix)
    if not m:
        return "other", None
    if suffix.startswith("e2e"):
        return "test", int(m.group(2) or 1)
    if suffix.startswith("fix"):
        return "fix", int(m.group(3))
    return suffix, None


def step_rank(kind: str, n: int | None) -> int:
    """Order of steps added in one commit: criteria, review, build, then
    test 1, fix 1, test 2, fix 2, ..."""
    if kind == "criteria":
        return 0
    if kind == "review":
        return 1
    if kind == "build":
        return 2
    if kind == "test":
        return 3 + 2 * (n - 1)
    if kind == "fix":
        return 4 + 2 * (n - 1)
    return 1000


def verdicts(lines: list[str], item_id: str) -> list[dict]:
    """Every anchored verdict in the lines, the first per subject. Subject
    "item" when the line names the file's own item (or none)."""
    found: list[dict] = []
    seen: set = set()
    for i, line in enumerate(lines[:HEAD_LINES], start=1):
        for form in VERDICT_FORMS:
            m = form.match(line)
            if not m:
                continue
            if m.lastindex == 1:
                written, word = None, m.group(1)
            else:
                written, word = m.group(1), m.group(2)
            if written is None or norm_id(written).lower() == item_id.lower():
                subject = "item"
            else:
                subject = written
            if subject not in seen:
                seen.add(subject)
                found.append({"subject": subject, "written": written, "verdict": word, "line": i})
            break
    return found


def title_of(lines: list[str]) -> str | None:
    if lines and lines[0].startswith("# "):
        return clip(lines[0][2:], TITLE_MAX)
    return None


def handoff_of(lines: list[str]) -> str | None:
    for i, line in enumerate(lines):
        if line.startswith("Handoff line:"):
            para = [line[len("Handoff line:"):]]
            for nxt in lines[i + 1:]:
                if not nxt.strip() or nxt.startswith("#"):
                    break
                para.append(nxt)
            return clip(joined(para), HANDOFF_MAX)
    return None


def scan_items(repo: Path, first: dict, not_read: list) -> dict:
    sprint = repo / "docs" / "sprint"
    items: dict = {}
    try:
        names = sorted(p.name for p in sprint.iterdir() if p.is_file() and p.suffix == ".md")
    except OSError as e:
        not_read.append({"file": "docs/sprint/", "why": f"cannot be listed: {type(e).__name__}"})
        names = []
    for name in names:
        if name in NOT_ITEM_FILES:
            continue
        m = FILE_RE.match(name)
        if not m:
            not_read.append({"file": name, "why": "not an item file name"})
            continue
        item_id, suffix = m.group(1), m.group(2)
        kind, n = step_kind(suffix)
        item = items.setdefault(item_id, {"id": item_id, "steps": [], "uncommitted": []})
        lines, why = read_head(sprint / name)
        step = {"file": name, "kind": kind, "n": n, "name": suffix, "title": None,
                "verdicts": [] if kind == "test" else None}
        if lines is None:
            not_read.append({"file": name, "why": why})
        else:
            step["title"] = title_of(lines)
            if kind == "criteria":
                handoff = handoff_of(lines)
                if handoff:
                    item["handoff"] = handoff
                if step["title"] is None:
                    not_read.append({"file": name, "why": "no title line"})
            if kind == "test":
                step["verdicts"] = verdicts(lines, item_id)
                if not step["verdicts"]:
                    not_read.append({"file": name, "why": f"no verdict line in the first {HEAD_LINES} lines"})
        if kind == "other":
            not_read.append({"file": name, "why": f"step '{suffix}' is not a known step (listed under its name)"})
        if name in first:
            sha, date, n_commit = first[name]
            step.update({"commit": sha, "committed_at": date, "_order": n_commit})
            item["steps"].append(step)
        else:
            step.update({"commit": None, "committed_at": None})
            item["uncommitted"].append(step)
    for item in items.values():
        item["steps"].sort(key=lambda s: (s["_order"], step_rank(s["kind"], s["n"]), s["file"]))
        for s in item["steps"]:
            s.pop("_order", None)
        crit = next((s for s in item["steps"] + item["uncommitted"] if s["kind"] == "criteria"), None)
        titled = [s for s in item["steps"] + item["uncommitted"] if s["title"]]
        item["title"] = (crit or {}).get("title") or (titled[0]["title"] if titled else None)
        item.setdefault("handoff", None)
    return items


# ═══════════════════════════════ Done rows ═════════════════════════════════

ID_WRITTEN = re.compile(r"(?<![\w-])[Mm]\d+[a-z]?(?:[ -]?[Ss]tep ?\d+)?(?![\w-])")
DATE_WRITTEN = re.compile(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?(?: ?UTC)?")
SMALL_WORDS = {"the", "and", "for", "its", "with", "from", "into", "pass", "criteria"}


def short_title(title: str | None, item_id: str) -> str | None:
    """An item's title without its own id and "pass criteria:" in front
    ("M25 pass criteria: /resources shows real usage" -> "/resources shows
    real usage"), so a Done row names the item once. Any other title is
    returned as it is."""
    if not title:
        return title
    m = re.match(r"^([Mm]\d+[A-Za-z]?(?:[ -]?[Ss][Tt][Ee][Pp] ?\d+)?)(?![\w-])\s*", title)
    if not m or norm_id(m.group(1)) != item_id:
        return title
    rest = re.sub(r"^pass criteria:\s*", "", title[m.end():], flags=re.I)
    return rest or title


def _words(s: str) -> list[str]:
    s = DATE_WRITTEN.sub(" ", ID_WRITTEN.sub(" ", s.lower().replace("’", "'")))
    return [w for w in re.findall(r"[a-z0-9']+", s) if len(w) >= 3 and w not in SMALL_WORDS]


def adds_to(text: str | None, title: str | None) -> bool:
    """Whether a promotions heading or a Shipped title says something the
    item's title does not: a word of three letters or more, other than ids,
    dates and a few small words, that the title lacks."""
    if not text:
        return False
    if not title:
        return True
    have = set(_words(title))
    return any(w not in have for w in _words(text))


# ═══════════════════════════════ PROMOTIONS.md ═════════════════════════════

def tag_from_cell(cell: str) -> tuple[str | None, bool]:
    m = re.search(r"`([^`]+)`", cell)
    return (m.group(1) if m else None), ("tag only" in cell.lower())


def bare_word(cell: str) -> str | None:
    """A tag cell with no tag in backticks: its text only when it is one
    word ("none"); anything longer is cell text and stays in the file."""
    w = cell.strip()
    return w if re.fullmatch(r"[\w-]{1,20}", w) else None


def parse_promotions(text: str) -> list[dict]:
    """Entries in file order: a table row, or a ## / ### heading starting
    with an item id. A later entry for an id already promoted is a note on
    it when it is a heading; a later row is a second promotion (M1b). A
    heading entry's body runs to the next heading of level 1 to 3, tables
    included: a table inside an entry is its body, not promotions."""
    lines = text.splitlines()
    entries: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("|"):
            cells = line.split("|")
            if len(cells) >= 4:
                label = cells[1].strip()
                head_part = label.split("(", 1)[0]
                ids = [norm_id(x) for x in LABEL_ID.findall(head_part)]
                if ids and not set(label) <= set("-: "):
                    tag, tag_only = tag_from_cell(cells[2])
                    entries.append({"kind": "row", "ids": ids, "heading": clip(label, TITLE_MAX),
                                    "tag": tag, "tag_written": bare_word(cells[2]) if tag is None else None,
                                    "tag_only": tag_only, "date": clip(cells[3].strip(), 120),
                                    "for_drew": len(FOR_DREW.findall("|".join(cells[4:]))), "line": i + 1})
            i += 1
            continue
        m = HEADING_ID.match(line)
        if m:
            body: list[str] = []
            j = i + 1
            while j < len(lines) and not re.match(r"^#{1,3} ", lines[j]):
                body.append(lines[j])
                j += 1
            tag = None
            tag_only = False
            for b in body:
                t = TAG_LINE.match(b)
                if t:
                    tag, tag_only = t.group(1), "tag only" in t.group(2).lower()
                    break
            d = DATE_RE.search(line)
            entries.append({"kind": "heading", "ids": [norm_id(m.group(2))],
                            "heading": clip(line.lstrip("#").strip(), TITLE_MAX), "tag": tag,
                            "tag_written": None, "tag_only": tag_only, "date": d.group(0) if d else None,
                            "for_drew": len(FOR_DREW.findall("\n".join(body))), "line": i + 1})
            i = j
            continue
        i += 1
    promoted: set = set()
    for e in entries:
        e["note_on"] = []
        for item_id in e["ids"]:
            if item_id in promoted and e["kind"] == "heading":
                e["note_on"].append(item_id)
            promoted.add(item_id)
        e["note"] = bool(e["note_on"]) and len(e["note_on"]) == len(e["ids"])
    return entries


# ═════════════════════════════════ ROADMAP.md ══════════════════════════════

def parse_roadmap(text: str, not_read: list) -> list[dict]:
    """Sections under '## ' headings, each with its entries: a '- ' bullet
    at column 0 and its continuation (indented lines, or lines straight
    after it). Text before the first '## ' (the Conventions) is not read."""
    sections: list[dict] = []
    cur = None
    entry: list[str] | None = None
    entry_line = 0

    def close():
        nonlocal entry
        if cur is not None and entry is not None:
            while entry and not entry[-1].strip():
                entry.pop()
            cur["entries"].append((entry_line, entry))
        entry = None

    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## "):
            close()
            cur = {"name": line[3:].strip(), "entries": []}
            sections.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("- "):
            close()
            entry, entry_line = [line[2:]], i + 1
            continue
        if entry is None:
            continue
        if not line.strip():
            entry.append(line)
            continue
        if line[0].isspace() or (entry and entry[-1].strip()):
            entry.append(line)
            continue
        close()          # a paragraph at column 0 after a blank line ends it
    close()
    out = []
    for s in sections:
        first = s["name"].split()[0] if s["name"].split() else ""
        if first in OPEN_SECTIONS:
            s["open"] = True
        elif first == "Shipped":
            s["open"] = False
        else:
            not_read.append({"file": "ROADMAP.md", "why": f"section '{clip(s['name'], 60)}' is not a known section"})
            continue
        s["entries"] = [roadmap_entry(line, body, s["open"]) for line, body in s["entries"]]
        out.append(s)
    return out


def roadmap_entry(line: int, body: list[str], open_: bool) -> dict:
    text = joined(body)
    m = re.match(r"^\*\*(.+?)\*\*", text)
    title = m.group(1) if m else None
    rest = text[m.end():] if m else text
    lm = re.match(r"^\s*\*\((.*?)\)\*", rest)
    lead = lm.group(1) if lm else None
    links: list[str] = []
    for lk in LINK_RE.finditer(lead or ""):
        ids = [lk.group(1)] + re.findall(r"M\d+[a-z]?", lk.group(2) or "")
        for x in ids:
            x = norm_id(x)
            if x not in links:
                links.append(x)
    e = {"line": line, "title": clip(title, TITLE_MAX), "lead": clip(lead, 400), "links": links}
    if open_:
        e["text"] = clip(text, ENTRY_MAX)
        e["fold"] = fold(e["text"])
    return e


SENTENCE_END = re.compile(r"[.?!][)*\"'’”]*(?=\s)")


def _balanced(s: str) -> bool:
    """No `code`, **bold**, *emphasis* or [link] left open in s."""
    if s.count("`") % 2:
        return False
    bare = re.sub(r"`[^`]*`", "", s)
    stars = bare.count("**")
    return stars % 2 == 0 and bare.replace("**", "").count("*") % 2 == 0 and bare.count("[") == bare.count("]")


def fold(text: str) -> dict:
    """An open entry folded for the backlog: {"head": its title, lead and
    first sentence, "rest": the remainder or None}; text is head + " " +
    rest. The first sentence ends at . ? or ! (and closing quotes, brackets
    or stars) before a space, after the title and lead, where no markup is
    left open and the word before is not a bare number ("1." of a list).
    Longer than FOLD_MAX: cut at the last space before it that leaves no
    markup open."""
    m = re.match(r"^\*\*.+?\*\*(\s*\*\(.*?\)\*)?", text)
    start = m.end() if m else 0
    end = None
    for se in SENTENCE_END.finditer(text, start):
        if re.search(r"(?:^|\s)\d+$", text[:se.start()]):
            continue
        if _balanced(text[:se.end()]):
            end = se.end()
            break
    if end is not None and end <= FOLD_MAX:
        head, rest = text[:end], text[end:].strip()
        return {"head": head, "rest": rest or None}
    if len(text) <= FOLD_MAX:
        return {"head": text, "rest": None}
    for i in range(FOLD_MAX, start, -1):
        if text[i] == " " and _balanced(text[:i]):
            return {"head": text[:i], "rest": text[i + 1:]}
    return {"head": text, "rest": None}


def resolve_links(links: list[str], item_ids) -> list[str]:
    """'M21' links both of its steps."""
    out = []
    for x in links:
        hits = [i for i in item_ids if i == x or i.startswith(x + "-step")]
        for h in sorted(hits, key=id_key):
            if h not in out:
                out.append(h)
    return out


# ══════════════════════════════════ stage ══════════════════════════════════

def agent_for(name: str | None) -> tuple[str | None, str | None]:
    """(item id, role) parsed from an agent's name, or (None, None)."""
    m = AGENT_RE.match(name or "")
    if not m:
        return None, None
    return norm_id(m.group(1)), m.group(2)


def stage(steps: list[dict], tag: bool, promoted: bool, agents: list[dict] | None) -> dict:
    """The stage of one item: a pure function of its committed step files,
    whether the tag item-<id> exists, whether it has a promotions entry,
    and the agents named for it with their status (None: herdr not
    reachable, so the agent rows are skipped). Returns {"stage", "words",
    "detail", "blocked", "by"}; stage is a key of STAGE_WORDS."""
    if promoted:
        return {"stage": "promoted", "words": STAGE_WORDS["promoted"], "detail": None, "blocked": False,
                "by": "promotions"}
    if tag:
        return {"stage": "promoting", "words": STAGE_WORDS["promoting"], "detail": "tag item-<id> exists",
                "blocked": False, "by": "tag"}
    active = [a for a in (agents or []) if a["status"] in DECIDING and a.get("role") in ROLE_STAGE]
    if active:
        parts = []
        for a in sorted(active, key=lambda a: (a["status"] != "blocked", a["name"])):
            st = ROLE_STAGE[a["role"]]
            how = "BLOCKED" if a["status"] == "blocked" else "working"
            parts.append({"stage": st, "text": f"{STAGE_WORDS[st]}, agent {a['name']} {how}"})
        return {"stage": parts[0]["stage"], "words": "; ".join(STAGE_WORDS[p["stage"]] for p in parts),
                "detail": "; ".join(p["text"] for p in parts),
                "blocked": any(a["status"] == "blocked" for a in active), "by": "agents"}
    if not steps:
        st, detail = FILE_STAGE["none"]
    else:
        last = steps[-1]
        k = last["kind"]
        if k in ("criteria", "review", "build"):
            st, detail = FILE_STAGE[k]
        elif k == "fix":
            st, detail = FILE_STAGE["fix"][0], FILE_STAGE["fix"][1].format(n=last["n"])
        elif k == "test":
            vs = last.get("verdicts") or []
            v = next((x for x in vs if x["subject"] == "item"), vs[0] if vs else None)
            word = v["verdict"] if v else "verdict not read"
            key = "test_passes" if word == "PASSES" else "test"
            st, detail = FILE_STAGE[key][0], FILE_STAGE[key][1].format(n=last["n"], verdict=word)
        else:
            st, detail = FILE_STAGE["other"][0], FILE_STAGE["other"][1].format(file=last["file"])
    return {"stage": st, "words": STAGE_WORDS[st], "detail": detail, "blocked": False, "by": "files"}


# ═══════════════════════ collect_legacy (import-only) ═══════════════════════
# The schema-1 reader of docs/sprint/PROMOTIONS.md and the step files. Not
# on the live path since M34: work.py import-legacy and check, and
# tests/test_m27_work_export.py, use it and the parsers above it
# (git_first_commits, scan_items, parse_promotions, tag_from_cell,
# bare_word). Its output is schema 1.

def collect_legacy(repo: Path) -> dict:
    t0 = time.monotonic()
    not_read: list = []
    errors: list = []
    head = git_head(repo)
    first, err = git_first_commits(repo)
    if err:
        errors.append(err)
    tags, err = git_tags(repo)
    if err:
        errors.append(err)
    items = scan_items(repo, first, not_read)

    ptext, why = read_text(repo / "docs" / "sprint" / "PROMOTIONS.md")
    if ptext is None:
        not_read.append({"file": "PROMOTIONS.md", "why": why})
        ptext = ""
    promotions = parse_promotions(ptext)
    for e in promotions:
        e["tag_in_git"] = e["tag"] in tags if e["tag"] else None
        for item_id in e["ids"]:
            items.setdefault(item_id, {"id": item_id, "steps": [], "uncommitted": [], "title": None,
                                       "handoff": None})

    rtext, why = read_text(repo / "ROADMAP.md")
    if rtext is None:
        not_read.append({"file": "ROADMAP.md", "why": why})
        rtext = ""
    sections = parse_roadmap(rtext, not_read)

    try:
        h = herdr_agents(repo)
    except Exception as e:                  # never fatal: the file is still written
        h = {"known": False, "seen": None, "agents": [], "elsewhere": 0, "skipped": 0,
             "reason": f"herdr agent list: the answer could not be read ({type(e).__name__})"}
    coord = coordinator(h)
    coord["shown"] = coordinator_shown(coord, h["agents"])

    promoted_ids = {i for e in promotions for i in e["ids"]}
    under_way = sorted((i for i, it in items.items() if i not in promoted_ids and (it["steps"] or it["uncommitted"])),
                       key=id_key)

    # Agents: each named agent in the repo goes to its item when that item
    # is under way; every other one is "other".
    other_agents: list = []
    per_item: dict = {i: [] for i in items}
    for a in h["agents"]:
        if a["name"] is None:
            continue
        item_id, role = agent_for(a["name"])
        if item_id in per_item and item_id in under_way:
            per_item[item_id].append({**a, "role": role})
        else:
            why_other = ("no item of that name" if item_id and item_id not in items else
                         "its item is promoted" if item_id else "name is not <item>-<role>")
            other_agents.append({**a, "why": why_other})

    item_ids = list(items)
    for e in (en for s in sections for en in s["entries"]):
        e["links"] = resolve_links(e["links"], item_ids)

    out_items = []
    for item_id in sorted(items, key=id_key):
        it = items[item_id]
        agents = per_item[item_id] if h["known"] else None
        it["tag_ref"] = f"item-{item_id}" in tags
        it["promoted"] = item_id in promoted_ids
        it["stage"] = stage(it["steps"], it["tag_ref"], it["promoted"], agents)
        it["agents"] = per_item[item_id]
        it["notes"] = [f"{a['name']}: {a['status']}, workspace still open" for a in per_item[item_id]
                       if a["status"] not in DECIDING]
        out_items.append(it)

    backlog = []
    shipped_unlinked = []
    for s in sections:
        if s["open"]:
            entries = []
            for e in s["entries"]:
                if any(l in under_way for l in e["links"]):
                    for l in e["links"]:
                        if l in under_way:
                            items[l].setdefault("roadmap", []).append(e)
                else:
                    entries.append(e)
            backlog.append({"section": s["name"], "entries": entries})
        else:
            for e in s["entries"]:
                if e["links"]:
                    for l in e["links"]:
                        items[l].setdefault("shipped", []).append({"title": e["title"], "lead": e["lead"],
                                                                   "line": e["line"],
                                                                   "adds": adds_to(e["title"], items[l]["title"])})
                else:
                    shipped_unlinked.append({"title": e["title"], "lead": e["lead"], "line": e["line"]})
    for it in out_items:
        it.setdefault("roadmap", [])
        it.setdefault("shipped", [])
        it["short_title"] = short_title(it["title"], it["id"])

    # Done: PROMOTIONS.md in reverse file order; a note is shown on the
    # item's row of the promotion before it.
    done = []
    last_row: dict = {}
    rows = []
    for idx, e in enumerate(promotions):
        for item_id in e["ids"]:
            if item_id in e["note_on"] and item_id in last_row:
                last_row[item_id]["notes"].append({"heading": e["heading"], "date": e["date"],
                                                   "for_drew": e["for_drew"], "line": e["line"]})
            else:
                row = {"id": item_id, "entry": idx, "notes": []}
                rows.append(row)
                last_row[item_id] = row
    for row in sorted(rows, key=lambda r: -r["entry"]):      # stable: label order kept
        e = promotions[row["entry"]]
        row.update({"heading": e["heading"], "tag": e["tag"], "tag_written": e["tag_written"],
                    "tag_only": e["tag_only"], "tag_in_git": e["tag_in_git"], "date": e["date"],
                    "for_drew": e["for_drew"], "line": e["line"], "kind": e["kind"],
                    "heading_adds": adds_to(e["heading"], items[row["id"]]["title"])})
        row["for_drew_total"] = e["for_drew"] + sum(n["for_drew"] for n in row["notes"])
        done.append(row)

    return {
        "schema": 1,
        "ts": time.time(),
        "made_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "repo": head,
        "herdr": {"known": h["known"], "seen": h["seen"], "reason": h["reason"],
                  "elsewhere": h["elsewhere"], "skipped": h["skipped"], "coordinator": coord},
        "items": out_items,
        "under_way": under_way,
        "other_agents": other_agents,
        "backlog": backlog,
        "done": done,
        "shipped_unlinked": shipped_unlinked,
        "not_read": not_read,
        "errors": errors,
        "run_s": round(time.monotonic() - t0, 3),
    }


# ═══════════════════ collect: from the work-state database ═════════════════
# Work item M34. The rows come from the dump work.py writes beside the
# database after every write, loaded into memory; the stage, the queue and
# the counts from work.py's own overview(), which `resume` prints from.

DB_STATES = ("ok", "missing", "empty", "corrupt", "newer", "other repo")
DEPLOYED_WORD = {1: "yes", 0: "no", None: "not recorded"}
FOLLOWUP_NOTE = "follow-up, {date}"
PROMOTIONS_LINE = re.compile(r"^PROMOTIONS\.md:(\d+)$")
HEX = re.compile(r"^[0-9a-f]{7,40}$")

_WP = None


class _This:
    """This module, as work.py's work_export() would load it: handed to the
    loaded work.py so it calls this copy's stage(), git() and run() rather
    than parsing the file a second time under another name (and a test's
    monkeypatch of this module reaches work.py's calls)."""

    def __getattr__(self, name):
        try:
            return globals()[name]
        except KeyError:
            raise AttributeError(name) from None


def work_tool():
    """scripts/work.py, beside this file, loaded on first use with no
    bytecode written. Never at import: work.py and agent.py load this file
    on every call, and a fault in work.py must not become a fault there."""
    global _WP
    if _WP is None:
        path = HERE / "work.py"
        before = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec = importlib.util.spec_from_file_location("kronk_work_tool_for_export", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            sys.dont_write_bytecode = before
        if mod._WE is None:
            mod._WE = _This()
        _WP = mod
    return _WP


class Rows:
    """The loaded dump, as work.py's queue_rows() and after_blocks() read a
    database: .one() and .all() give dicts."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def one(self, sql, *args):
        r = self.conn.execute(sql, args).fetchone()
        return None if r is None else {k: r[k] for k in r.keys()}

    def all(self, sql, *args):
        return [{k: r[k] for k in r.keys()} for r in self.conn.execute(sql, args)]


def dump_of(db: Path) -> Path:
    return db.with_name(db.name + ".dump.sql")


def load_dump(wp, db: Path, repo: Path, now: float) -> tuple[Rows | None, dict]:
    """(rows, the "database" key). The dump is read once, read-only, and
    loaded into an in-memory database; nothing else in its directory is
    opened. A dump that cannot be used gives no rows and a state word with
    a sentence written here (never SQLite's text, which names paths)."""
    dp = dump_of(db)
    info = {"state": None, "says": None, "schema": None, "dump_at": None, "dump_age_s": None}

    def bad(state, says):
        info.update(state=state, says=says)
        return None, info

    try:
        with open(dp, "rb") as f:
            st = os.fstat(f.fileno())
            raw = f.read()
    except FileNotFoundError:
        return bad("missing", "No dump of the work-state database was found (work.py writes it after every "
                              "write; `python3 scripts/work.py dump` writes it again).")
    except OSError as e:
        return bad("corrupt", f"The dump of the work-state database cannot be read ({type(e).__name__}).")
    info["dump_at"] = dt.datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds")
    info["dump_age_s"] = round(max(0.0, now - st.st_mtime), 1)
    if not raw.strip():
        return bad("empty", "The dump of the work-state database is empty: it holds no schema.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return bad("corrupt", "The dump of the work-state database is not text.")
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(text)
        cut = conn.in_transaction
    except sqlite3.Error:
        conn.close()
        return bad("corrupt", "The dump of the work-state database does not load as SQL.")
    if cut:
        conn.close()
        return bad("corrupt", "The dump of the work-state database ends inside its transaction: it is cut short.")
    try:
        meta = wp.validate(conn, dp)
    except wp.WorkError as e:
        conn.close()
        if e.kind == "newer":
            return bad("newer", f"The work-state database is of a newer schema than this exporter knows "
                                f"({wp.SCHEMA}): the newer scripts/work.py and work_export.py are needed.")
        if e.kind == "empty":
            return bad("empty", "The dump of the work-state database holds no schema.")
        return bad("corrupt", "The dump of the work-state database lacks its tables or its schema version.")
    except sqlite3.Error:
        conn.close()
        return bad("corrupt", "The dump of the work-state database does not load as SQL.")
    info["schema"] = int(meta["schema_version"])
    if meta.get("repo") != os.path.realpath(repo):
        conn.close()
        return bad("other repo", "The work-state database describes another repo, not the one exported here.")
    info.update(state="ok", says="Read from the dump of the work-state database, which work.py writes after "
                                  "every write.")
    return Rows(conn), info


def criteria_lines(repo: Path, item: dict, steps: list[dict], not_read: list) -> tuple[str | None, str | None]:
    """(handoff, source) from the head of the item's criteria file: the
    row's path, else its finished criteria step's report, else
    docs/sprint/<id>-criteria.md, else none. A file that exists and cannot
    be read is named in not_read."""
    wp = work_tool()
    crit = [s["report"] for s in steps if s["kind"] == "criteria" and s["state"] == "finished" and s["report"]]
    for path in (item.get("criteria"), *crit, f"docs/sprint/{item['id']}-criteria.md"):
        if not path or not (repo / path).is_file():
            continue
        head = wp.criteria_head(repo, path)
        if head["criteria_read"]:
            not_read.append({"file": path, "why": head["criteria_read"]})
        return head["handoff"], head["source"]
    return None, None


def step_out(s: dict, item: str) -> dict:
    """A step row in the schema-1 shape and the new keys. `name` keeps its
    meaning, the report's suffix (M9-e2e2.md -> e2e2), else the row's name."""
    report = s["report"]
    file = Path(report).name if report else None
    m = FILE_RE.match(file or "")
    name = m.group(2) if m and m.group(1) == item else (s["name"] or s["kind"])
    v = [{"subject": "item", "written": None, "verdict": s["verdict"], "line": None}] if s["verdict"] else []
    return {"file": file, "kind": s["kind"], "n": s["round"] if s["kind"] in ("test", "fix") else None,
            "name": name, "title": None, "verdicts": v if s["kind"] == "test" else None,
            "commit": s["commit_sha"][:7] if HEX.match(s["commit_sha"] or "") else None,
            "committed_at": s["ended_at"] if s["state"] == "finished" else None,
            "round": s["round"], "state": s["state"], "by": s["by"], "agent": s["agent"],
            "started_at": s["started_at"], "ended_at": s["ended_at"], "verdict": s["verdict"],
            "graded": s["graded"]}


def empty_counts() -> dict:
    return {"leftovers": {"drew": 0, "coordinator": 0, "not_triaged": 0},
            "decisions": {"drew": 0, "coordinator": 0, "not_triaged": 0}}


def collect(repo: Path, db: Path | None = None) -> dict:
    t0 = time.monotonic()
    now = time.time()
    wp = work_tool()
    db = Path(db) if db is not None else wp.default_db()
    not_read: list = []
    errors: list = []
    head = git_head(repo)
    try:
        tags = wp.tag_commits(repo)
    except wp.WorkError:
        tags = {}
        errors.append("git for-each-ref failed: tags not read")
    commits = wp.head_commits(repo)
    if commits is None:
        errors.append("git rev-list HEAD failed: every finished step is counted for the stage")

    rtext, why = read_text(repo / "ROADMAP.md")
    if rtext is None:
        not_read.append({"file": "ROADMAP.md", "why": why})
        rtext = ""
    sections = parse_roadmap(rtext, not_read)

    try:
        h = herdr_agents(repo)
    except Exception as e:                  # never fatal: the file is still written
        h = {"known": False, "seen": None, "agents": [], "elsewhere": 0, "skipped": 0,
             "reason": f"herdr agent list: the answer could not be read ({type(e).__name__})"}
    coord = coordinator(h)
    coord["shown"] = coordinator_shown(coord, h["agents"])

    rows, database = load_dump(wp, db, repo, now)
    if rows is None:
        errors.append(f"work-state database: {database['state']}. {database['says']}")
        g = {"items": [], "steps": [], "promotions": [], "pitems": [], "matters": [], "queue": []}
    else:
        try:
            g = {"items": rows.all("SELECT * FROM item"),
                 "steps": rows.all("SELECT * FROM step ORDER BY id"),
                 "promotions": rows.all("SELECT * FROM promotion ORDER BY id"),
                 "pitems": rows.all("SELECT * FROM promotion_item"),
                 "matters": rows.all("SELECT * FROM open_thing ORDER BY id"),
                 "queue": wp.queue_rows(rows)}
        finally:
            rows.conn.close()
    g.update(tags=tags, branch_commits=commits,
             herdr={**h, "by_name": {a["name"]: a["status"] for a in h["agents"] if a["name"]}})
    v = wp.overview(g)

    items = {i["id"]: i for i in g["items"]}
    steps_of: dict = {}
    for s in g["steps"]:
        steps_of.setdefault(s["item_id"], []).append(s)
    under_way = [x["item"]["id"] for x in v["active"]]
    queue = [q["id"] for q in v["queue"]]
    qrow = {q["id"]: q for q in v["queue"]}
    proms = {p["id"]: p for p in g["promotions"]}
    items_of: dict = {}
    for pi in g["pitems"]:
        items_of.setdefault(pi["promotion_id"], []).append(pi["item_id"])

    # Agents: a named agent in the repo goes to its item when that item is
    # active; every other one is "other".
    other_agents: list = []
    per_item: dict = {}
    for a in h["agents"]:
        if a["name"] is None:
            continue
        item_id, role = agent_for(a["name"])
        if item_id in under_way:
            per_item.setdefault(item_id, []).append({**a, "role": role})
        else:
            why_other = ("no item of that name" if item_id and item_id not in items else
                         f"its item is {items[item_id]['state']}" if item_id else "name is not <item>-<role>")
            other_agents.append({**a, "why": why_other})

    for e in (en for s in sections for en in s["entries"]):
        e["links"] = resolve_links(e["links"], list(items))

    out_items = []
    for item_id in sorted(items, key=id_key):
        it = items[item_id]
        mine = sorted(steps_of.get(item_id, []),
                      key=lambda s: (s["ended_at"] or s["started_at"] or "", step_rank(s["kind"], s["round"]), s["id"]))
        handoff, source = criteria_lines(repo, it, mine, not_read)
        live = it["state"] in ("active", "queued", "held")
        q = qrow.get(item_id, {})
        agents = per_item.get(item_id, [])
        out_items.append({
            "id": item_id, "title": it["title"], "short_title": short_title(it["title"], item_id),
            "stage": v["stages"][item_id], "handoff": handoff,
            "notes": [f"{a['name']}: {a['status']}, workspace still open" for a in agents
                      if a["status"] not in DECIDING],
            "steps": [step_out(s, item_id) for s in mine], "uncommitted": [],
            "tag_ref": f"item-{item_id}" in tags, "promoted": it["state"] == "promoted", "agents": agents,
            "state": it["state"], "tier": it["tier"], "position": it["position"],
            "after_item": it["after_item"] if live else None, "blocked_by": q.get("blocked_by"),
            "next": bool(q.get("next")), "next_step": it["next_step"] if live else None,
            "waits_on": it["waits_on"] if live else None, "waits_note": it["waits_note"] if live else None,
            "held_why": it["held_why"] if it["state"] == "held" else None, "source": source,
            "open": v["counts"].get(item_id) or empty_counts(),
        })
    by_id = {it["id"]: it for it in out_items}

    backlog = []
    shipped_unlinked = []
    for s in sections:
        if s["open"]:
            entries = []
            for e in s["entries"]:
                if any(l in under_way for l in e["links"]):
                    for l in e["links"]:
                        if l in under_way:
                            by_id[l].setdefault("roadmap", []).append(e)
                else:
                    entries.append(e)
            backlog.append({"section": s["name"], "entries": entries})
        else:
            for e in s["entries"]:
                if e["links"]:
                    for l in e["links"]:
                        by_id[l].setdefault("shipped", []).append({"title": e["title"], "lead": e["lead"],
                                                                   "line": e["line"],
                                                                   "adds": adds_to(e["title"], by_id[l]["title"])})
                else:
                    shipped_unlinked.append({"title": e["title"], "lead": e["lead"], "line": e["line"]})
    for it in out_items:
        it.setdefault("roadmap", [])
        it.setdefault("shipped", [])

    # Done: one row per item of each promotion, newest promotion first; a
    # follow-up is a note on the item's row of the promotion before it. A
    # promotion with no item has its own key, never a row here.
    def prom_out(p):
        line = PROMOTIONS_LINE.match(p["verified"] or "")
        return {"tag": p["tag"], "tag_in_git": (p["tag"] in tags) if p["tag"] else None,
                "date": wp.local_day(p["at"]), "at": p["at"], "deployed": DEPLOYED_WORD.get(p["deployed"]),
                "verified": p["verified"], "line": int(line.group(1)) if line else None, "promotion": p["id"],
                "kind": p["kind"]}

    rows_out: list = []
    last_row: dict = {}
    for p in sorted(proms.values(), key=lambda p: p["id"]):
        for item_id in sorted(items_of.get(p["id"], []), key=id_key):
            if p["kind"] == "followup" and item_id in last_row:
                po = prom_out(p)
                last_row[item_id]["notes"].append({"heading": FOLLOWUP_NOTE.format(date=po["date"]),
                                                   "date": po["date"], "for_drew": 0, "line": po["line"],
                                                   "at": po["at"], "verified": po["verified"]})
                continue
            po = prom_out(p)
            row = {"id": item_id, "entry": None, "notes": [], "heading": None, "tag": po["tag"],
                   "tag_written": None, "tag_only": bool(p["tag"]) and p["deployed"] == 0,
                   "tag_in_git": po["tag_in_git"], "date": po["date"], "for_drew": 0, "line": po["line"],
                   "kind": None, "heading_adds": False, "for_drew_total": 0,
                   "at": po["at"], "deployed": po["deployed"], "verified": po["verified"],
                   "promotion": p["id"], "followup": p["kind"] == "followup"}
            rows_out.append(row)
            last_row[item_id] = row
    done = sorted(rows_out, key=lambda r: -r["promotion"])          # stable: item order kept
    no_item = [{**prom_out(p), "label": p["note"]}
               for p in sorted(proms.values(), key=lambda p: (p["at"], p["id"]), reverse=True)
               if p["id"] not in items_of]

    owed = [{"id": m["id"], "item": m["item_id"], "date": wp.local_day(m["opened_at"]), "opened_at": m["opened_at"],
             "blocks": bool(m["blocks"])} for m in v["decisions"]]
    totals = empty_counts()
    for per in v["counts"].values():
        for kind in totals:
            for owner in totals[kind]:
                totals[kind][owner] += per[kind][owner]

    return {
        "schema": SCHEMA,
        "ts": now,
        "made_at": dt.datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
        "repo": head,
        "herdr": {"known": h["known"], "seen": h["seen"], "reason": h["reason"],
                  "elsewhere": h["elsewhere"], "skipped": h["skipped"], "coordinator": coord},
        "items": out_items,
        "under_way": under_way,
        "other_agents": other_agents,
        "backlog": backlog,
        "done": done,
        "shipped_unlinked": shipped_unlinked,
        "not_read": not_read,
        "errors": errors,
        "database": database,
        "active": under_way,
        "queue": queue,
        "decisions_owed": owed,
        "open_counts": totals,
        "escapes": {"window": v["escape_window"], "count": len(v["escapes"])},
        "promotions_no_item": no_item,
        "run_s": round(time.monotonic() - t0, 3),
    }


def remove_old_temps(path: Path, now: float | None = None) -> None:
    """Remove this file's own temp files (".<name>.XXXXXXXX", as mkstemp
    names them in write()) older than TEMP_MAX_AGE_S: a run killed in
    mid-write leaves one. Regular files only; any error is ignored."""
    now = time.time() if now is None else now
    pat = re.compile(rf"^\.{re.escape(path.name)}\.[a-z0-9_]{{8}}$")
    try:
        names = os.listdir(path.parent)
    except OSError:
        return
    for name in names:
        if not pat.match(name):
            continue
        p = path.parent / name
        try:
            st = os.lstat(p)
            if stat.S_ISREG(st.st_mode) and now - st.st_mtime > TEMP_MAX_AGE_S:
                os.unlink(p)
        except OSError:
            pass


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    remove_old_temps(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
            f.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=REPO, help="the working tree to read (default: this repo)")
    ap.add_argument("--db", type=Path, help="the work-state database whose dump (PATH.dump.sql) is read "
                                            "(default: KRONK_WORK_DB, else ~/.local/state/kronk-work/work.db)")
    ap.add_argument("--out", type=Path, action="append", help="file to write (repeatable)")
    ap.add_argument("--print", action="store_true", help="print the JSON instead of writing")
    a = ap.parse_args(argv)
    data = collect(a.repo, a.db)
    if a.print:
        json.dump(data, sys.stdout, indent=1, ensure_ascii=False)
        print()
        return 0
    for p in a.out or [a.repo / "data" / "work.json", a.repo / "data" / "dev" / "work.json"]:
        write(p, data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
