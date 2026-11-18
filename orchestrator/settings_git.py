"""git on the live config directory (sprint M21 step 2, criteria 15 and 16).

The live directory mounted at /config is its own small git repository
(seeded by scripts/deploy_tag.sh). The /settings page reads its log and
makes one local commit per save. No remote, no push.

Every call runs git from the image (Debian's package) as the service's
user (uid 1000, the host user), with no system or user config
(GIT_CONFIG_NOSYSTEM, GIT_CONFIG_GLOBAL=/dev/null), hooks off and the
identity on the command line, so nothing outside this module decides what
a commit is, and no hook in the repository can run. A repository that is
missing, is not the top of the directory, has no commits or fails
`git fsck` disables editing (health() says why); viewing and loading the
config never depend on git.
"""
import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path

import config_files
import config_loader

IDENTITY = ("-c", "user.name=Kronk settings page", "-c", "user.email=settings@kronk.invalid")
SAFE = ("-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "-c", "core.fsmonitor=false",
        "-c", "core.quotepath=false", "-c", "log.showSignature=false")
MAX_LOG = 100           # commits read per file for the history and per-key last change
HEALTH_TTL_S = 30


class GitError(Exception):
    pass


def _env() -> dict:
    return {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"), "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0"}


def run(base: Path, *args: str, input_bytes: bytes | None = None, timeout: float = 15) -> bytes:
    """git's stdout; GitError with git's own message on failure."""
    try:
        r = subprocess.run(["git", "-C", str(base), *SAFE, *IDENTITY, *args], input=input_bytes,
                           capture_output=True, timeout=timeout, env=_env())
    except FileNotFoundError:
        raise GitError("git is not installed in this image")
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} took longer than {timeout:.0f} s")
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).decode("utf-8", "replace").strip().splitlines()
        raise GitError(f"git {args[0]} failed: {config_files.safe(msg[-1] if msg else r.returncode, 200)}")
    return r.stdout


def blob_sha(data: bytes) -> str:
    """git's object id of a blob with these bytes (what `git hash-object` says)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


_health: dict = {}


def health(base: Path) -> str | None:
    """None when the live directory is a usable git repository; else why
    not, in a sentence for the page. Cached for HEALTH_TTL_S."""
    key = str(base)
    hit = _health.get(key)
    if hit and time.monotonic() - hit[0] < HEALTH_TTL_S:
        return hit[1]
    msg = _check(base)
    _health[key] = (time.monotonic(), msg)
    return msg


def forget_health() -> None:
    _health.clear()


def _check(base: Path) -> str | None:
    if shutil.which("git") is None:
        return "git is not installed in this image"
    if not (Path(base) / ".git").is_dir():
        return (f"{base} is not a git repository (no .git); seed the live directory with "
                "scripts/deploy_tag.sh seed, or restore its .git")
    try:
        top = run(base, "rev-parse", "--show-toplevel").decode().strip()
        if os.path.realpath(top) != os.path.realpath(base):
            return f"{base} is inside another git repository ({top}), not its own"
        run(base, "rev-parse", "--verify", "-q", "HEAD^{commit}")
        run(base, "fsck", "--no-progress", "--no-dangling", timeout=30)
    except GitError as e:
        return f"the git repository in {base} is not usable: {e}"
    return None


def head(base: Path) -> str:
    return run(base, "rev-parse", "HEAD").decode().strip()


def file_log(base: Path, rel: str, limit: int = MAX_LOG) -> list[dict]:
    """Newest first: commit, date (ISO), author, subject, who (the `By:`
    line of the message, else the author)."""
    out = run(base, "log", f"-n{limit}", "--format=%H%x1f%aI%x1f%an%x1f%s%x1f%b%x1e", "--", rel)
    entries = []
    for rec in out.decode("utf-8", "replace").split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        commit, date, author, subject, body = (rec.split("\x1f") + [""] * 5)[:5]
        who = next((line[3:].strip() for line in body.splitlines() if line.startswith("By:")), author)
        entries.append({"commit": commit, "short": commit[:7], "date": date, "author": author,
                        "subject": config_files.safe(subject, 300), "who": config_files.safe(who, 200),
                        "body": config_files.safe(body.strip(), 2000)})
    return entries


def show_file(base: Path, commit: str, rel: str) -> bytes | None:
    """rel's bytes at commit, None when rel did not exist there."""
    _check_commit(commit)
    try:
        return run(base, "show", f"{commit}:{rel}")
    except GitError:
        return None


def commit_diff(base: Path, commit: str, rel: str) -> str:
    _check_commit(commit)
    out = run(base, "show", "--format=%H %aI%n%B", "--no-color", "--no-ext-diff", commit, "--", rel)
    return out.decode("utf-8", "replace")


def parent(base: Path, commit: str) -> str | None:
    _check_commit(commit)
    try:
        return run(base, "rev-parse", "-q", "--verify", f"{commit}^1^{{commit}}").decode().strip()
    except GitError:
        return None


def is_dirty(base: Path, rel: str) -> bool:
    return bool(run(base, "status", "--porcelain", "--", rel).strip())


def _check_commit(commit: str) -> None:
    if not isinstance(commit, str) or not (4 <= len(commit) <= 40) or \
            any(c not in "0123456789abcdef" for c in commit):
        raise GitError(f"not a commit id: {config_files.quote(commit)}")


def commit_file(base: Path, rel: str, message: str) -> str:
    """Commit rel alone (the file the save wrote) with message; the new
    commit's id. Nothing else in the working tree is committed."""
    run(base, "add", "--", rel)
    run(base, "commit", "-q", "--no-verify", "-m", message, "--", rel)
    return head(base)


def merge3(current: bytes, base: bytes, other: bytes) -> tuple[bytes, int]:
    """git merge-file: apply the change base -> other to current. (merged
    bytes, number of conflicts). Temporary files outside the live directory."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="kronk-revert-") as d:
        paths = []
        for name, data in (("current", current), ("base", base), ("other", other)):
            p = Path(d) / name
            p.write_bytes(data)
            paths.append(str(p))
        try:
            r = subprocess.run(["git", "merge-file", "-p", "-L", "now", "-L", "the commit", "-L", "before it",
                                *paths], capture_output=True, timeout=15, env=_env())
        except FileNotFoundError:
            raise GitError("git is not installed in this image")
    if r.returncode < 0 or r.returncode > 127:
        raise GitError(f"git merge-file failed: {r.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return r.stdout, r.returncode


# ── per-key last change ───────────────────────────────────────────────────────

def _flat(data, prefix="") -> dict:
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                out.update(_flat(v, key))
            else:
                out[key] = v
    return out


_key_cache: dict = {}


def key_changes(base: Path, rel: str) -> dict[str, dict]:
    """For a YAML file: dotted key -> the newest commit (of the last
    MAX_LOG touching it) in which that key's value changed, was added or
    was removed. A version that does not parse is skipped."""
    h = head(base)
    ck = (str(base), rel, h)
    if ck in _key_cache:
        return _key_cache[ck]
    log = list(reversed(file_log(base, rel)))
    last: dict[str, dict] = {}
    prev: dict = {}
    for entry in log:
        data = show_file(base, entry["commit"], rel)
        if data is None:
            flat = {}
        else:
            try:
                parsed, _ = config_loader.parse_yaml(data.decode("utf-8"), rel)
            except Exception:
                continue
            flat = _flat(parsed)
        for k in set(prev) | set(flat):
            if k not in prev or k not in flat or prev[k] != flat[k]:
                last[k] = entry
        prev = flat
    if len(_key_cache) > 64:
        _key_cache.clear()
    _key_cache[ck] = last
    return last
