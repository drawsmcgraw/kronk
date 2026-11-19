#!/usr/bin/env python3
"""backups.py: Kronk's config backup, recordings archive and stale check
(sprint M16). Python standard library only.

    backups.py config  [--dry-run] [--no-notify] [--dest DIR] [--require-mount]
    backups.py archive [--dry-run] [--no-notify] [--dest DIR] [--require-mount] [--src DIR]
    backups.py check   [--no-notify] [--dest DIR] [--require-mount]
    backups.py status

config   One dated tar, <dest>/config/kronk-config-<YYYY-MM-DD>.tar.gz, mode
         0600: what is needed to rebuild Kronk (secrets included, in the
         clear), a MANIFEST, the repo state (and a git bundle of the commits
         no remote-tracking branch has), the image pins, the model list, the
         host facts, and the restore runbook. Built in memory, size-checked,
         written to a temporary name, read back, then renamed. A tar missing
         a required item is named kronk-config-<date>.incomplete.tar.gz, ends
         the job with exit 4, and never counts toward retention. Keeps the
         newest KRONK_BACKUP_KEEP (12) complete tars.
archive  Moves recordings older than KRONK_ARCHIVE_AGE_DAYS (7) from
         ~/kronk-voice-recordings to <dest>/recordings/<device>/<pipeline>/
         <YYYY-MM>/<run>/<file>: copied (no symlink followed at any level, in
         either tree), read back (size and SHA-256, page cache dropped), and
         then deleted locally in batches by a throwaway container whose
         script re-checks each file (size, mtime, inode, SHA-256) and its
         destination copy (size, inode, SHA-256) right before its rm. Never
         deletes or overwrites anything on the destination, its own leftover
         temporary files from an interrupted run included.
check    The stale check: warns when the newest config tar is older than 35
         days or the last finished archive run older than its interval plus a
         margin. One notice when the warnings change, repeated weekly.
status   Prints the status file.

Exit codes: 0 ok; 1 failed; 2 the destination, the recordings directory or
a setting is unusable (nothing done); 3 declined (lock); 4 config tar
written but INCOMPLETE. The check exits 1 when it warns.

The destination is a directory (KRONK_BACKUP_DIR, default /mnt/nas-backup,
an SMB share mounted by /etc/fstab). Any path at or under /mnt/nas-backup,
or any path with KRONK_BACKUP_REQUIRE_MOUNT=1 or --require-mount, needs the
share mounted (waiting up to KRONK_BACKUP_MOUNT_WAIT_S, 180, for it) and
writable, or the job writes nothing there and fails: a tar written into an
unmounted mount point would sit on the local disk and look like a backup.

State: KRONK_BACKUP_STATE (default data/backups): status.json (read by the
orchestrator's GET /api/backups for /services), lock, model_sums.json (the
model hash cache). One lock for both jobs; a job waits (up to
KRONK_BACKUP_LOCK_WAIT_MIN, 30) while the other runs or while the monthly
upgrade run holds data/upgrades/lock, then declines.

Notices go through scripts/lib/notify.sh (KRONK_BACKUP_NOTIFY_CMD replaces it
with a command that gets tag, title and body as arguments; --no-notify sends
none, for test runs). A notice that fails is logged; it does not fail the job.

Guide: docs/features/backups.md. Restore: docs/runbooks/restore.md.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import errno
import fcntl
import hashlib
import io
import json
import os
import re
import secrets
import shlex
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(os.environ.get("KRONK_REPO_DIR") or Path(__file__).resolve().parent.parent)
HOME = Path.home()
DEFAULT_DEST = "/mnt/nas-backup"
SCHEMA = 1
CONFIG_RE = re.compile(r"kronk-config-(\d{4}-\d{2}-\d{2})(?:\.(\d+))?\.tar\.gz")
INCOMPLETE_RE = re.compile(r"kronk-config-(\d{4}-\d{2}-\d{2})(?:\.(\d+))?\.incomplete\.tar\.gz")
CONFIG_TMP_RE = re.compile(r"\.kronk-config-\d{4}-\d{2}-\d{2}(?:\.\d+)?(?:\.incomplete)?\.tar\.gz\.tmp")
# The archive's temporary files: .kronk-archive.<pid>.<16 hex>.tmp, created
# O_EXCL. A source file whose name starts with the prefix is never archived;
# the job removes only the temporary file it created in this run (same inode).
ARCHIVE_TMP_PREFIX = ".kronk-archive"
DELETE_BATCH = 200                      # recordings per delete container
PROBE = ".kronk-write-probe"            # the writability probe, created and removed at once
TAG = "kronk-backup"
# Every helper container this script starts carries this label, so a run
# killed while one was being created can find and remove it (and nothing else).
HELPER_LABEL = "kronk.backup.helper=1"
# What tempfile.TemporaryDirectory(prefix="kronk-bundle-") names (8 random
# characters from its alphabet), and the only names unpushed_bundle leaves in it.
BUNDLE_DIR_RE = re.compile(r"kronk-bundle-[a-z0-9_]{8}")
BUNDLE_DIR_FILES = ("unpushed.bundle", "unpushed.bundle.lock")


def env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def human(n: int | float) -> str:
    """Bytes in SI units, one decimal: 5.9 MB."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"


def files(n: int) -> str:
    return f"{n} file" if n == 1 else f"{n} files"


def one_line(text: str, n: int = 160) -> str:
    """The first non-empty line of an error text, cut to n characters: a
    notice carries one line of a tool's error, never its usage text."""
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")[:n]


def show(name: str) -> str:
    """A path for a log line or notice: control characters escaped."""
    return json.dumps(name, ensure_ascii=False)[1:-1] if any(c < " " for c in name) else name


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def parse_iso(s) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(s)
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


# ═══ Host access (replaced by a fake in the tests) ═════════════════════════

@dataclass
class Result:
    rc: int
    out: bytes
    err: str


class Ops:
    """Everything that touches the host beyond the paths it is given:
    processes, docker, the clock, notices."""

    def __init__(self, log=print, no_notify: bool = False):
        self.log = log
        self.no_notify = no_notify

    def run(self, argv: list[str], timeout: int = 300, stdin: bytes | None = None) -> Result:
        try:
            p = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout)
            return Result(p.returncode, p.stdout, p.stderr.decode("utf-8", "replace").strip())
        except (OSError, subprocess.TimeoutExpired) as e:
            return Result(-1, b"", f"{e.__class__.__name__}: {e}")

    def now(self) -> dt.datetime:
        return dt.datetime.now().astimezone()

    def sleep(self, s: float) -> None:
        time.sleep(s)

    def ismount(self, p: Path) -> bool:
        return os.path.ismount(p)

    def notify(self, title: str, body: str) -> bool:
        if self.no_notify:
            self.log(f"notice not sent (--no-notify): {title}")
            return False
        cmd = os.environ.get("KRONK_BACKUP_NOTIFY_CMD")
        if cmd:
            argv = shlex.split(cmd) + [TAG, title, body]
        else:
            lib = REPO / "scripts" / "lib" / "notify.sh"
            argv = ["bash", "-c", 'source "$0"; load_ha_token && ha_notify "$1" "$2" "$3"',
                    str(lib), TAG, title, body]
        r = self.run(argv, timeout=60)
        if r.rc != 0:
            self.log(f"notice not sent ({r.rc}): {title}: {r.err[-300:]}")
        return r.rc == 0

    # Reads of files only root or a container can read (ruling 4).
    def container_ls(self, container: str, path: str) -> Result:
        return self.run(["docker", "exec", container, "ls", "-A1", "--", path], timeout=60)

    def container_tar(self, container: str, cwd: str, names: list[str]) -> Result:
        """A tar stream of <names> (literal, no globs) under <cwd> inside a running container."""
        script = 'cd "$1" && shift && exec tar -cf - -- "$@"'
        return self.run(["docker", "exec", container, "sh", "-c", script, "sh", cwd, *names], timeout=300)

    def container_cat(self, container: str, path: str) -> Result:
        return self.run(["docker", "exec", container, "cat", "--", path], timeout=120)

    def helper(self, mounts: list[tuple[str, str, bool]], argv: list[str], timeout: int = 300) -> Result:
        """A throwaway container of an image already on the host (no pull, no
        network), labelled HELPER_LABEL. Mounts are (source, target, read-only)
        and go in as --mount type=bind, which fails when the source is
        missing (-v would create it, root-owned, on the local disk)."""
        image = env("KRONK_BACKUP_HELPER_IMAGE", "alpine:3.20")
        args = []
        for source, target, ro in mounts:
            if any(c in str(source) for c in ',"\n'):
                return Result(-1, b"", f"refused: a path docker --mount cannot carry: {str(source)!r}")
            args += ["--mount", f"type=bind,source={source},target={target}" + (",readonly" if ro else "")]
        return self.run(["docker", "run", "--rm", "--pull=never", "--network", "none", "--label", HELPER_LABEL,
                         *args, image, *argv], timeout=timeout)

    def helper_tar(self, hostdir: Path) -> Result:
        return self.helper([(str(hostdir), "/src", True)], ["tar", "-cf", "-", "-C", "/src", "."])

    def remove_recordings(self, src: Path, dst: Path, items: list["Verified"]) -> tuple[dict[int, str], str]:
        """Delete verified recordings, DELETE_BATCH per call of DELETE_SCRIPT,
        which re-checks each one against what was copied and its copy on the
        destination right before its rm. The paths are arguments, never part
        of the script. Returns ({index into items: "ok" or why refused}, the
        last error text). KRONK_ARCHIVE_DELETE=direct runs the same script with
        the host's sh, for a tree the operator owns (tests, scratch copies)."""
        direct = env("KRONK_ARCHIVE_DELETE", "docker") == "direct"
        results: dict[int, str] = {}
        err = ""
        for start in range(0, len(items), DELETE_BATCH):
            batch = items[start:start + DELETE_BATCH]
            args = [str(len(batch))] + [a for v in batch for a in v.delete_args()]
            if direct:
                r = self.run(["sh", "-c", DELETE_SCRIPT, "sh", str(src), str(dst), *args], timeout=600)
            else:
                r = self.helper([(str(src), "/rec", False), (str(dst), "/dst", True)],
                                ["sh", "-c", DELETE_SCRIPT, "sh", "/rec", "/dst", *args], timeout=600)
            if r.rc != 0 or r.err:
                err = one_line(r.err, 200) or f"exit {r.rc}"
            for line in r.out.decode("utf-8", "replace").splitlines():
                word, _, rest = line.partition(" ")
                k, _, why = rest.partition(" ")
                if word in ("ok", "refused") and k.isdigit() and 1 <= int(k) <= len(batch):
                    results[start + int(k) - 1] = "ok" if word == "ok" else (why or "refused")
        return results, err


# The delete, run as root in the helper container (the recordings are
# root-owned) with the recordings at $1 and the destination read-only at $2.
# For each file: every directory on both paths must be a plain directory
# (not a symlink); the local file must be a regular file whose SHA-256, size,
# mtime (seconds) and device:inode are those of the file that was copied; the
# destination copy must be a regular file with the copied size, the
# device:inode recorded at its verify and the copied SHA-256 (read again
# here, then its device:inode checked once more, so a copy rewritten in place
# or swapped during the read is caught). Only then `rm --` (no -r, no -f: it
# cannot remove a directory), then `rmdir --` of the run directory, which
# succeeds only when it is empty. One line per file on stdout: "ok <n>" or
# "refused <n> <why>". tests/test_m16_backups.py pins this text.
DELETE_SCRIPT = r"""R=$1; D=$2; n=$3; shift 3; i=0
while [ "$i" -lt "$n" ]; do
  dv=$1; pl=$2; rn=$3; nm=$4; mo=$5; sz=$6; mt=$7; si=$8; hs=$9; shift 9; di=$1; shift
  i=$((i+1)); f="$R/$dv/$pl/$rn/$nm"; t="$D/$dv/$pl/$mo/$rn/$nm"; why=""
  for p in "$R/$dv" "$R/$dv/$pl" "$R/$dv/$pl/$rn" "$D/$dv" "$D/$dv/$pl" "$D/$dv/$pl/$mo" "$D/$dv/$pl/$mo/$rn"; do
    if [ -L "$p" ] || [ ! -d "$p" ]; then why="a directory on the way is a symlink or missing"; break; fi
  done
  if [ -z "$why" ]; then
    if [ -L "$f" ] || [ ! -f "$f" ]; then why="not a regular file"
    elif [ -L "$t" ] || [ ! -f "$t" ]; then why="the destination copy is gone"
    else
      h=$(sha256sum < "$f"); h=${h%% *}
      s=$(stat -c '%s %Y %d:%i' -- "$f")
      g=$(sha256sum < "$t"); g=${g%% *}
      d=$(stat -c '%s %d:%i' -- "$t")
      if [ "$s" != "$sz $mt $si" ]; then why="size, mtime or inode differ from the copied file"
      elif [ "$h" != "$hs" ]; then why="sha256 differs from the copied file"
      elif [ "$d" != "$sz $di" ]; then why="the destination copy is not the verified one"
      elif [ "$g" != "$hs" ]; then why="the destination copy's sha256 differs from the copied file"
      fi
    fi
  fi
  if [ -z "$why" ] && rm -- "$f"; then echo "ok $i"; rmdir -- "$R/$dv/$pl/$rn" 2>/dev/null
  else echo "refused $i ${why:-rm failed}"; fi
done
exit 0
"""


# ═══ Settings, state, lock ═════════════════════════════════════════════════

@dataclass
class Settings:
    dest: Path
    require_mount: bool
    state: Path
    keep: int = 12
    max_bytes: int = 50 * 1000 * 1000
    recordings: Path = HOME / "kronk-voice-recordings"
    age_days: int = 7
    stale_days: int = 35
    archive_interval_days: int = 7
    archive_margin_days: int = 2
    lock_wait_min: int = 30
    upgrade_state: Path = REPO / "data" / "upgrades"
    live_config: Path = REPO.parent / "kronk-config"
    model_dirs: tuple = ("/opt/models", str(HOME / "model-staging"))
    repo: Path = REPO
    etc: Path = Path("/etc")          # host files read for the facts and /etc/kronk (tests point these elsewhere)
    proc: Path = Path("/proc")
    tmp: Path = Path(tempfile.gettempdir())   # where the bundle is made, and its leftovers looked for
    mount_root: Path | None = None    # the mount point the destination must be on (None: the destination itself)
    mount_wait_s: int = 180
    bundle_max_bytes: int = 20 * 1000 * 1000
    errors: list = field(default_factory=list)   # bad settings: the job fails with a notice, does nothing

    @classmethod
    def load(cls, args) -> "Settings":
        errors: list[str] = []

        def num(name: str, default: int, lo: int) -> int:
            raw = env(name, str(default))
            try:
                v = int(raw)
            except ValueError:
                errors.append(f"{name} must be a whole number, not {raw!r}")
                return default
            if v < lo:
                errors.append(f"{name} must be at least {lo}, not {v}")
                return default
            return v
        dest = getattr(args, "dest", None) or env("KRONK_BACKUP_DIR", DEFAULT_DEST)
        flagged = getattr(args, "require_mount", False) or env("KRONK_BACKUP_REQUIRE_MOUNT", "0") == "1"
        under = mount_root_for(dest)
        return cls(
            dest=Path(dest),
            require_mount=flagged or under is not None,
            mount_root=under,
            state=Path(env("KRONK_BACKUP_STATE", str(REPO / "data" / "backups"))),
            keep=num("KRONK_BACKUP_KEEP", 12, 0),
            max_bytes=num("KRONK_BACKUP_MAX_MB", 50, 1) * 1000 * 1000,
            recordings=Path(getattr(args, "src", None) or env("KRONK_RECORDINGS_DIR", str(HOME / "kronk-voice-recordings"))),
            age_days=num("KRONK_ARCHIVE_AGE_DAYS", 7, 1),
            stale_days=num("KRONK_BACKUP_STALE_DAYS", 35, 1),
            archive_interval_days=num("KRONK_ARCHIVE_INTERVAL_DAYS", 7, 1),
            archive_margin_days=num("KRONK_ARCHIVE_MARGIN_DAYS", 2, 0),
            lock_wait_min=num("KRONK_BACKUP_LOCK_WAIT_MIN", 30, 0),
            mount_wait_s=num("KRONK_BACKUP_MOUNT_WAIT_S", 180, 0),
            bundle_max_bytes=num("KRONK_BACKUP_BUNDLE_MAX_MB", 20, 1) * 1000 * 1000,
            upgrade_state=Path(env("KRONK_UPGRADE_STATE", str(REPO / "data" / "upgrades"))),
            live_config=Path(env("KRONK_LIVE_CONFIG", str(REPO.parent / "kronk-config"))),
            model_dirs=tuple(p for p in env("KRONK_MODEL_DIRS", f"/opt/models:{HOME}/model-staging").split(":") if p),
            errors=errors,
        )


def mount_root_for(dest: str) -> Path | None:
    """/mnt/nas-backup when <dest> is at or under it, else None. Both the
    lexical form (., .., a doubled slash) and the form with symlinks resolved
    are tested, so neither /mnt//nas-backup/x nor a symlink to the share
    escapes the mount check. Only path strings are compared: the check that
    the share is mounted comes later (dest_problem)."""
    lexical = os.path.normpath(os.path.join(os.getcwd(), dest))
    if lexical.startswith("//"):
        lexical = "/" + lexical.lstrip("/")
    roots = {DEFAULT_DEST, os.path.realpath(DEFAULT_DEST)}
    for p in (lexical, os.path.realpath(dest)):
        if any(p == r or p.startswith(r + "/") for r in roots):
            return Path(DEFAULT_DEST)
    return None


def read_status(sd: Path) -> dict:
    p = sd / "status.json"
    try:
        raw = json.loads(p.read_text())
        if isinstance(raw, dict):
            return raw
    except FileNotFoundError:
        return {"schema": SCHEMA}
    except (OSError, ValueError):
        pass
    # Unreadable: kept aside for a person, not silently overwritten.
    try:
        os.replace(p, sd / f"status.json.unreadable-{int(time.time())}")
    except OSError:
        pass
    return {"schema": SCHEMA, "note": "the previous status file could not be read and was set aside"}


def write_json(p: Path, data: dict) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, p)


def save_run(s: Settings, ops: Ops, job: str, run: dict) -> None:
    st = read_status(s.state)
    st["schema"] = SCHEMA
    entry = st.get(job) if isinstance(st.get(job), dict) else {}
    entry["last"] = run
    if run.get("result") == "ok":
        entry["last_ok_at"] = run["at"]
    st[job] = entry
    st["limits"] = {"config_days": s.stale_days,
                    "archive_days": s.archive_interval_days + s.archive_margin_days}
    st["updated"] = iso(ops.now())
    write_json(s.state / "status.json", st)


def upgrade_lock_held(sd: Path) -> bool:
    """The monthly upgrade run's lock (scripts/upgrades.py), tested and
    released at once; never taken and never created."""
    p = sd / "lock"
    try:
        fd = os.open(p, os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def take_lock(s: Settings, ops: Ops, job: str) -> tuple[int | None, str]:
    """The backup lock (data/backups/lock), waiting while the other job or an
    upgrade run is in progress. Returns (fd, '') or (None, why)."""
    s.state.mkdir(parents=True, exist_ok=True)
    waited = 0
    while True:
        fd = os.open(s.state / "lock", os.O_RDWR | os.O_CREAT, 0o644)
        why = ""
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if upgrade_lock_held(s.upgrade_state):
                why = f"an upgrade run holds {s.upgrade_state / 'lock'}"
            else:
                os.ftruncate(fd, 0)
                os.write(fd, f"{job} pid={os.getpid()} since {iso(ops.now())}\n".encode())
                return fd, ""
        except BlockingIOError:
            try:
                why = "another backup job holds the lock (" + (s.state / "lock").read_text().strip() + ")"
            except OSError:
                why = "another backup job holds the lock"
        os.close(fd)
        if waited >= s.lock_wait_min:
            return None, f"{why}; waited {waited} minutes"
        if waited == 0:
            ops.log(f"waiting: {why} (up to {s.lock_wait_min} minutes)")
        ops.sleep(60)
        waited += 1


def dest_problem(s: Settings, ops: Ops, probe: bool = True, wait: bool = False) -> str | None:
    """Why the destination cannot be written to, or None (ruling 2). With
    wait, a share that is not mounted yet is waited for (mount_wait_s) before
    it is a failure: a timer's catch-up run at boot can start before fstab's
    nofail mount is up."""
    d = s.dest
    if s.require_mount:
        root = s.mount_root or d
        waited = 0
        while wait and not ops.ismount(root) and waited < s.mount_wait_s:
            if waited == 0:
                ops.log(f"waiting up to {s.mount_wait_s} s for {root} to be mounted")
            ops.sleep(10)
            waited += 10
        if not ops.ismount(root):
            return (f"destination {root} is not a mounted filesystem (the NAS share is not mounted: "
                    f"`mount {root}` or check /etc/fstab); nothing was written there"
                    + (f" (waited {waited} s)" if waited else ""))
    if not d.is_dir():
        return f"destination {d} does not exist or is not a directory"
    if s.require_mount and s.mount_root is not None and os.path.normpath(d) != str(s.mount_root):
        try:
            same = os.stat(d).st_dev == os.stat(s.mount_root).st_dev
        except OSError:
            same = False
        if not same:
            return f"destination {d} is not on the share mounted at {s.mount_root}; nothing was written there"
    if not probe:
        return None if os.access(d, os.W_OK) else f"destination {d} is not writable"
    try:
        fd = os.open(d / PROBE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        os.unlink(d / PROBE)
    except FileExistsError:
        try:
            os.unlink(d / PROBE)
        except OSError as e:
            return f"destination {d} is not writable: {e.strerror}"
    except OSError as e:
        return f"destination {d} is not writable: {e.strerror}"
    return None


def is_archive_tmp(name: str) -> bool:
    return name.startswith(ARCHIVE_TMP_PREFIX)


def tree_size(root: Path) -> tuple[int, int, int]:
    """Bytes and count of regular files under root, symlinks not followed,
    and how many of them are the archive's leftover temporary files."""
    total = n = tmp = 0
    for dirpath, _dirs, files in os.walk(root, followlinks=False):
        for f in files:
            try:
                st = os.lstat(os.path.join(dirpath, f))
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
                n += 1
                tmp += is_archive_tmp(f)
    return total, n, tmp


def dest_totals(s: Settings) -> dict:
    c, cn, _ = tree_size(s.dest / "config")
    r, rn, rt = tree_size(s.dest / "recordings")
    return {"config": c, "config_files": cn, "recordings": r, "recordings_files": rn, "all": c + r,
            "leftover_tmp": rt}


def totals_line(t: dict) -> str:
    line = (f"On the destination: config backups {human(t['config'])} ({files(t['config_files'])}), "
            f"recordings archive {human(t['recordings'])} ({files(t['recordings_files'])}), "
            f"together {human(t['all'])}.")
    if t.get("leftover_tmp"):
        line += (f" {t['leftover_tmp']} leftover temporary file(s) from interrupted archive runs are under "
                 f"recordings/ (.kronk-archive.*.tmp; the job never removes them: delete them by hand).")
    return line


# ═══ The config backup ═════════════════════════════════════════════════════

@dataclass
class Member:
    arc: str            # path inside the tar, below the dated top directory
    source: str
    how: str
    data: bytes = b""
    mode: int = 0o600
    mtime: float = 0.0
    link: str | None = None   # a symlink's target (stored, never followed)
    secret: bool = False
    isdir: bool = False       # a directory entry (kept so empty directories come back)

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass
class Collected:
    members: list[Member] = field(default_factory=list)
    absent: list[tuple[str, str]] = field(default_factory=list)       # (source, note)
    unreadable: list[tuple[str, str]] = field(default_factory=list)   # (source, why)
    unknown: list[str] = field(default_factory=list)                  # HA /config names this backup does not know

    def add(self, m: Member) -> None:
        self.members.append(m)


# What goes in. (arc, source, kind, secret). Kinds: file, tree, sqlite,
# optional (a file whose absence is normal). Container reads follow below.
def file_sources(s: Settings) -> list[tuple[str, Path, str, bool]]:
    r = s.repo
    return [
        ("repo/.env", r / ".env", "file", True),
        ("repo/.env.dev", r / ".env.dev", "file", True),
        ("repo/.env.telemetry", r / ".env.telemetry", "file", True),
        ("repo/searxng/settings.yml", r / "searxng" / "settings.yml", "file", True),
        ("repo/secrets", r / "secrets", "tree", True),
        ("repo/ops/hosts.json", r / "ops" / "hosts.json", "optional", True),
        ("repo/data/music_taste.yaml", r / "data" / "music_taste.yaml", "file", False),
        ("repo/data/music_taste.db", r / "data" / "music_taste.db", "sqlite", False),
        ("repo/data/solar.db", r / "data" / "solar.db", "sqlite", False),
        ("repo/data/kronk_state.md", r / "data" / "kronk_state.md", "file", False),
        ("repo/data/mm_known_hosts", r / "data" / "mm_known_hosts", "file", False),
        ("repo/data/yt-music-cookie", r / "data" / "yt-music-cookie", "file", True),
        ("repo/data/upgrades/status.json", s.upgrade_state / "status.json", "optional", False),
        ("live-config/prod", s.live_config / "prod", "tree", False),
        ("live-config/dev", s.live_config / "dev", "tree", False),
        ("systemd-user", HOME / ".config" / "systemd" / "user", "tree", False),
        ("config-kronk", HOME / ".config" / "kronk", "tree", False),
        ("voice-labels", HOME / "kronk-voice-labels", "optional-tree", False),
        ("etc/default/grub", s.etc / "default" / "grub", "file", False),
        ("etc/docker/daemon.json", s.etc / "docker" / "daemon.json", "optional", False),
        # The way back travels with the tar: readable before any clone.
        ("RESTORE.md", r / "docs" / "runbooks" / "restore.md", "file", False),
        ("BACKUPS.md", r / "docs" / "features" / "backups.md", "file", False),
    ]


def required_sources(s: Settings) -> list[str]:
    """What a tar cannot be called a backup without (docs/features/backups.md,
    "Required and optional"). A gap at or under any of these makes the run
    INCOMPLETE; every other gap is reported and the run is still OK."""
    return [str(s.repo / ".env"), str(s.live_config / "prod"), str(s.live_config / "dev"),
            str(HOME / ".config" / "systemd" / "user"), "homeassistant:", "music-assistant-server:",
            # The only copies of household data in the tar (coordinator ruling, note 9 of the second e2e).
            str(s.repo / "data" / "solar.db"), str(s.repo / "data" / "music_taste.db")]


def required_gaps(s: Settings, c: Collected) -> list[str]:
    roots = required_sources(s)

    def under(src: str, r: str) -> bool:
        return src.startswith(r) if r.endswith(":") else (src == r or src.startswith(r + "/"))
    return [f"{show(src)}: {why}" for src, why in c.unreadable if any(under(src, r) for r in roots)]


# Home Assistant's /config: what goes in, and what is left out on purpose.
# A top-level name in neither list is reported (MANIFEST and notice), so a
# new directory HA grows (custom_components/, www/) is not a silent gap.
HA_WANTED = [".storage", "blueprints", ".HA_VERSION", "custom_components", "www", "themes", "packages", "*.yaml"]
HA_LEFT_OUT = ["home-assistant_v2.db*", "home-assistant.log*", "tts", ".cache", "deps", ".ha_run.lock", ".cloud",
               "*.bak-*", "*.bak"]


def read_file(c: Collected, arc: str, p: Path, how: str, secret: bool) -> None:
    try:
        st = os.lstat(p)
        if stat.S_ISLNK(st.st_mode):
            c.add(Member(arc, str(p), "symlink", mode=0o777, mtime=st.st_mtime, link=os.readlink(p)))
            return
        if not stat.S_ISREG(st.st_mode):
            c.unreadable.append((str(p), "not a regular file"))
            return
        with open(p, "rb") as f:
            data = f.read()
        c.add(Member(arc, str(p), how, data, stat.S_IMODE(st.st_mode), st.st_mtime, secret=secret))
    except FileNotFoundError:
        raise
    except OSError as e:
        c.unreadable.append((str(p), e.strerror or str(e)))


def read_tree(c: Collected, arc: str, root: Path, secret: bool) -> None:
    def unreadable(e: OSError) -> None:     # a directory os.walk could not open is a gap, said so
        c.unreadable.append((str(e.filename), e.strerror or str(e)))
    for dirpath, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
        rel = os.path.relpath(dirpath, root)
        try:
            st = os.lstat(dirpath)
            c.add(Member(arc if rel == "." else f"{arc}/{rel}", dirpath, "directory",
                         mode=stat.S_IMODE(st.st_mode), mtime=st.st_mtime, isdir=True))
        except OSError as e:
            c.unreadable.append((dirpath, e.strerror or str(e)))
        # A symlinked directory shows up in dirs: stored as a link, not descended.
        links = [d for d in dirs if os.path.islink(os.path.join(dirpath, d))]
        for name in links + files:
            a = f"{arc}/{name}" if rel == "." else f"{arc}/{rel}/{name}"
            try:
                read_file(c, a, Path(dirpath) / name, "file", secret)
            except FileNotFoundError:
                pass        # gone between the listing and the read


def read_sqlite(c: Collected, arc: str, p: Path) -> None:
    """A consistent copy of a live SQLite database (the online backup API),
    without writing a file."""
    if not p.exists():
        raise FileNotFoundError(p)
    try:
        st = p.stat()
        src = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30)
        mem = sqlite3.connect(":memory:")
        try:
            src.backup(mem)
            data = mem.serialize()
        finally:
            src.close()
            mem.close()
        c.add(Member(arc, str(p), "sqlite backup", bytes(data), stat.S_IMODE(st.st_mode), st.st_mtime))
    except (OSError, sqlite3.Error) as e:
        c.unreadable.append((str(p), f"sqlite: {e}"))


def add_tar_stream(c: Collected, arc: str, source: str, how: str, blob: bytes, secret: bool) -> int:
    """Re-add the members of a tar stream (from a container) under arc."""
    n = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as t:
        for ti in t:
            name = ti.name[2:] if ti.name.startswith("./") else ti.name
            if not name or name == ".":
                continue
            a = f"{arc}/{name}"
            if ti.issym():
                c.add(Member(a, f"{source}/{name}", how, mode=0o777, mtime=ti.mtime, link=ti.linkname))
            elif ti.isdir():
                c.add(Member(a, f"{source}/{name}", how, mode=ti.mode & 0o7777, mtime=ti.mtime, isdir=True))
            elif ti.isfile():
                f = t.extractfile(ti)
                c.add(Member(a, f"{source}/{name}", how, f.read() if f else b"", ti.mode & 0o7777,
                             ti.mtime, secret=secret))
            else:
                continue
            n += 1
    return n


def collect(s: Settings, ops: Ops) -> Collected:
    c = Collected()
    for arc, p, kind, secret in file_sources(s):
        try:
            if kind in ("file", "optional"):
                read_file(c, arc, p, "file", secret)
            elif kind == "sqlite":
                read_sqlite(c, arc, p)
            elif kind in ("tree", "optional-tree"):
                if not p.is_dir():
                    raise FileNotFoundError(p)
                read_tree(c, arc, p, secret)
        except FileNotFoundError:
            (c.absent if kind.startswith("optional") else c.unreadable).append(
                (str(p), "absent" if kind.startswith("optional") else "missing: no such file"))

    # Home Assistant (ruling 4, 5): copied live from the running container.
    import fnmatch
    r = ops.container_ls("homeassistant", "/config")
    if r.rc == 0:
        present = [n for n in r.out.decode("utf-8", "replace").splitlines() if n not in ("", ".", "..")]
        names = [n for n in present if any(fnmatch.fnmatchcase(n, w) for w in HA_WANTED)]
        c.unknown = [n for n in present if n not in names and not any(fnmatch.fnmatchcase(n, x) for x in HA_LEFT_OUT)]
        r = ops.container_tar("homeassistant", "/config", names) if names else Result(1, b"", "nothing to copy in /config")
    if r.rc == 0 and r.out:
        try:
            add_tar_stream(c, "ha/config", "homeassistant:/config", "docker exec homeassistant tar", r.out, True)
        except tarfile.TarError as e:
            c.unreadable.append(("homeassistant:/config", f"tar stream unreadable: {e}"))
    else:
        c.unreadable.append(("homeassistant:/config (.storage, YAML, blueprints)",
                             f"docker exec failed ({r.rc}): {one_line(r.err, 200)}"))

    # Music Assistant: settings.json only (library.db is over 10 MB).
    r = ops.container_cat("music-assistant-server", "/data/settings.json")
    if r.rc == 0:
        c.add(Member("ma/settings.json", "music-assistant-server:/data/settings.json",
                     "docker exec music-assistant-server cat", r.out, 0o600, ops.now().timestamp(), secret=True))
    else:
        c.unreadable.append(("music-assistant-server:/data/settings.json", f"docker exec failed ({r.rc}): {one_line(r.err, 200)}"))

    # /etc/kronk: root-only on the host; a throwaway container reads it.
    etc = s.etc / "kronk"
    if etc.exists():
        r = ops.helper_tar(etc)
        if r.rc == 0 and r.out:
            try:
                add_tar_stream(c, "etc/kronk", str(etc), "docker run (read-only mount)", r.out, True)
            except tarfile.TarError as e:
                c.unreadable.append((str(etc), f"tar stream unreadable: {e}"))
        else:
            c.unreadable.append((str(etc), f"helper container failed ({r.rc}): {one_line(r.err, 200)}"))
    else:
        c.absent.append((str(etc), "absent"))

    # Recorded sources next to the models (PROVENANCE files, checksum lists).
    for d in s.model_dirs:
        for dirpath, _dirs, files in os.walk(d, followlinks=False):
            for f in files:
                if f.startswith("PROVENANCE") or f.endswith(".sha256"):
                    p = Path(dirpath) / f
                    try:
                        if os.lstat(p).st_size <= 1_000_000:
                            read_file(c, "models" + str(p), p, "file", False)
                    except OSError as e:
                        c.unreadable.append((str(p), e.strerror or str(e)))
    return c


def git(s: Settings, ops: Ops, *a: str) -> str:
    r = ops.run(["git", "-C", str(s.repo), *a], timeout=60)
    return r.out.decode().strip() if r.rc == 0 else f"(git {' '.join(a)} failed: {one_line(r.err, 120)})"


def production_tags(s: Settings, ops: Ops) -> tuple[list[str], dict[str, int]]:
    """docker ps rows of compose project kronk, and the release tag each
    container runs from (its working directory under kronk-releases/)."""
    r = ops.run(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=kronk", "--format",
                 '{{.Names}}\t{{.Label "com.docker.compose.project.working_dir"}}'], timeout=60)
    rows = r.out.decode().strip().splitlines() if r.rc == 0 else [f"(docker ps failed: {r.err[:120]})"]
    tags: dict[str, int] = {}
    for x in rows:
        if "\t" in x and "kronk-releases" in x:
            t = Path(x.split("\t")[1]).name
            tags[t] = tags.get(t, 0) + 1
    return rows, tags


def unpushed_bundle(s: Settings, ops: Ops, branch: str, tags: list[str]) -> tuple[bytes | None, str]:
    """A git bundle of HEAD, the branch and the production tags, less every
    commit some remote-tracking branch has (as of the last fetch; this job
    never fetches). Nothing when there is nothing unpushed; left out, and
    said so, when it is over the cap. A production tag that is not a local
    ref is left out of the bundle (not the bundle out of the tar) and named;
    the note names the refs the bundle really holds (git leaves out a ref
    whose commit a remote already has)."""
    n = git(s, ops, "rev-list", "--count", "HEAD", "--not", "--remotes")
    if not n.isdigit():
        return None, f"unknown ({n})"
    if int(n) == 0:
        return None, "none needed: every commit of HEAD is on a remote-tracking branch"
    refs = ["HEAD"] + ([branch] if branch and branch != "HEAD" and not branch.startswith("(") else [])
    missing = []
    for t in tags:
        r = ops.run(["git", "-C", str(s.repo), "rev-parse", "--verify", "--quiet", f"refs/tags/{t}"], timeout=60)
        if r.rc == 0 and r.out.strip():
            refs.append(f"refs/tags/{t}")
        else:
            missing.append(t)
    gone = f"; left out, not a local tag: {', '.join(missing)}" if missing else ""
    with tempfile.TemporaryDirectory(prefix="kronk-bundle-", dir=s.tmp) as td:      # 0700, local
        p = Path(td) / "unpushed.bundle"
        r = ops.run(["git", "-C", str(s.repo), "bundle", "create", "--quiet", str(p), *refs, "--not", "--remotes"],
                    timeout=300)
        if r.rc != 0 or not p.is_file():
            return None, f"NOT WRITTEN: git bundle failed ({r.rc}): {one_line(r.err)}{gone}"
        size = p.stat().st_size
        if size > s.bundle_max_bytes:
            return None, (f"OMITTED: {n} commit(s) unpushed, but the bundle is {human(size)}, over the "
                          f"{human(s.bundle_max_bytes)} cap (KRONK_BACKUP_BUNDLE_MAX_MB); push them")
        r = ops.run(["git", "-C", str(s.repo), "bundle", "list-heads", str(p)], timeout=60)
        heads = [ln.split(" ", 1)[1] for ln in r.out.decode("utf-8", "replace").splitlines() if " " in ln]
        inside = ", ".join(heads) if r.rc == 0 else f"unknown (git bundle list-heads failed: {one_line(r.err, 120)})"
        data = p.read_bytes()
    return data, f"repo-unpushed.bundle: {n} commit(s) no remote-tracking branch has, {size} bytes; refs in it: {inside}{gone}"


def clean_own_leftovers(s: Settings, ops: Ops) -> None:
    try:
        _clean_own_leftovers(s, ops)
    except Exception as e:  # noqa: BLE001 - housekeeping never fails the job
        ops.log(f"leftover check failed: {e.__class__.__name__}: {e}")


def _clean_own_leftovers(s: Settings, ops: Ops) -> None:
    """What a run killed at the wrong moment leaves outside the destination,
    removed at the start of the next job by exact signature only: helper
    containers (label HELPER_LABEL) in state created, never started so never
    removed by --rm; and bundle directories in s.tmp named like
    unpushed_bundle's, a plain directory owned by this user, older than a
    day, holding nothing but the bundle's own files. Anything else is left
    alone. Never fails the job."""
    r = ops.run(["docker", "ps", "-a", "--filter", f"label={HELPER_LABEL}", "--filter", "status=created",
                 "--format", "{{.ID}}"], timeout=60)
    ids = [x for x in r.out.decode("utf-8", "replace").split() if re.fullmatch(r"[0-9a-f]{12,64}", x)] if r.rc == 0 else []
    if ids:
        rm = ops.run(["docker", "rm", *ids], timeout=60)
        ops.log(f"removed {len(ids)} helper container(s) a killed run left in state created: {' '.join(ids)}"
                if rm.rc == 0 else f"could not remove leftover helper container(s) {' '.join(ids)}: {one_line(rm.err)}")
    cutoff = time.time() - 86400
    try:
        entries = list(os.scandir(s.tmp))
    except OSError:
        return
    for e in entries:
        if not BUNDLE_DIR_RE.fullmatch(e.name):
            continue
        try:
            st = os.lstat(e.path)
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mtime > cutoff:
                continue
            inner = os.listdir(e.path)
            if any(n not in BUNDLE_DIR_FILES or not stat.S_ISREG(os.lstat(os.path.join(e.path, n)).st_mode)
                   for n in inner):
                ops.log(f"left alone: {e.path} holds something other than a bundle")
                continue
            for n in inner:
                os.unlink(os.path.join(e.path, n))
            os.rmdir(e.path)
            ops.log(f"removed a bundle directory a killed run left: {e.path}")
        except OSError as x:
            ops.log(f"could not remove {e.path}: {x.strerror}")


def repo_state(s: Settings, ops: Ops, rows: list[str], tags: dict[str, int], bundle_note: str) -> str:
    head = git(s, ops, "rev-parse", "HEAD")
    branch = git(s, ops, "rev-parse", "--abbrev-ref", "HEAD")
    remotes = git(s, ops, "branch", "-r", "--contains", "HEAD")
    dirty = git(s, ops, "status", "--porcelain")
    # The branch's upstream, else origin/<branch> (refactor has no upstream configured).
    up = git(s, ops, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if up.startswith("(") or not up:
        up = f"origin/{branch}"
    ahead = git(s, ops, "rev-list", "--count", f"{up}..HEAD")
    lines = [f"commit: {head}", f"branch: {branch}",
             "pushed: " + (f"yes, HEAD is on {', '.join(remotes.split())}" if remotes and not remotes.startswith("(")
                           else "no: no remote-tracking branch contains HEAD") +
             " (as of the last fetch; this run does not contact the remote)",
             f"not pushed: {ahead + ' commits ahead of ' + up if ahead.isdigit() else 'unknown, ' + ahead}",
             f"unpushed bundle: {bundle_note}",
             f"working tree: {'clean' if not dirty else str(len(dirty.splitlines())) + ' changed paths'}",
             f"last commit: {git(s, ops, 'log', '-1', '--format=%ci %s')}",
             "production tag: " + (", ".join(f"{t} ({n} containers)" for t, n in sorted(tags.items()))
                                   if tags else "none (not run from a release directory)")]
    for t in sorted(tags):
        lines.append(f"production tag {t} on the remote: {tag_on_remote(s, ops, t)}")
    lines += ["", "production (compose project kronk, the directory each container was created from):"]
    lines += [f"  {x}" for x in rows]
    return "\n".join(lines) + "\n"


def tag_on_remote(s: Settings, ops: Ops, tag: str) -> str:
    """What local refs can tell about a tag on the remote. Git keeps no
    remote-tracking refs for tags, so the tag itself is "unknown" unless its
    commit is on no remote-tracking branch (then it cannot be there)."""
    r = ops.run(["git", "-C", str(s.repo), "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}"], timeout=60)
    commit = r.out.decode().strip() if r.rc == 0 else ""
    if not commit:
        return "unknown (no local tag of that name)"
    on = git(s, ops, "branch", "-r", "--contains", commit)
    if on.startswith("("):
        return f"unknown ({on}); commit {commit}"
    if not on:
        return (f"no: its commit {commit} is on no remote-tracking branch (the tag is in "
                f"repo-unpushed.bundle when that member exists)")
    return (f"unknown: the tag itself cannot be told without contacting the remote; its commit {commit} is on "
            f"{', '.join(on.split())}. After a clone without it: git tag {tag} {commit}")


def image_pins(s: Settings, ops: Ops) -> str:
    out = ["# image: lines of the compose files at backup time"]
    for f in ("docker-compose.yml", "docker-compose.ha.yml", "docker-compose.ma.yml", "docker-compose.telemetry.yml"):
        try:
            for ln in (s.repo / f).read_text().splitlines():
                m = re.match(r"\s*image:\s*(\S+)", ln)
                if m:
                    out.append(f"{f}\t{m.group(1)}")
        except OSError as e:
            out.append(f"{f}\t(unreadable: {e.strerror})")
    out += ["", "# running containers: name, image, image id"]
    r = ops.run(["docker", "ps", "--format", "{{.Names}}"], timeout=60)
    names = r.out.decode().split() if r.rc == 0 else []
    if names:
        r = ops.run(["docker", "inspect", "-f", "{{.Name}}\t{{.Config.Image}}\t{{.Image}}", *names], timeout=60)
        out += [ln.lstrip("/") for ln in r.out.decode().splitlines()] if r.rc == 0 else [f"(docker inspect failed: {r.err[:120]})"]
    return "\n".join(out) + "\n"


def host_facts(s: Settings, ops: Ops) -> str:
    def rd(p):
        try:
            return Path(p).read_text()
        except OSError as e:
            return f"(unreadable: {e.strerror})\n"
    fstab = rd(s.etc / "fstab").splitlines()
    keep, block = [], []
    for ln in fstab:      # each NAS line (cifs, nfs) with the comment block above it
        if ln.strip().startswith("#"):
            block.append(ln)
            continue
        f = ln.split()
        if len(f) >= 3 and f[2] in ("cifs", "smb3", "nfs", "nfs4"):
            keep += block + [ln]
        block = []
    osr = re.search(r'PRETTY_NAME="([^"]*)"', rd(s.etc / "os-release"))
    return "\n".join([
        f"hostname: {socket.gethostname()}",
        f"taken: {iso(ops.now())}",
        f"os: {osr.group(1) if osr else 'unknown'}",
        f"kernel: {os.uname().release}",
        f"/proc/cmdline: {rd(s.proc / 'cmdline').strip()}",
        "", "/etc/fstab, network filesystem lines:", *keep, "",
    ])


def sha256_file(p: Path, drop_cache: bool = True) -> str:
    """SHA-256 of a file. With drop_cache its pages are dropped before the
    first read and after each chunk: a large file does not fill the page
    cache, and a file just written and fsynced is read back from the device
    (on CIFS: from the server) rather than from this machine's memory."""
    with open(p, "rb", buffering=0) as f:
        return sha256_fd(f.fileno(), drop_cache)


def drop_pages(fd: int) -> None:
    if hasattr(os, "posix_fadvise"):
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)


def sha256_fd(fd: int, drop_cache: bool) -> str:
    h = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    if drop_cache:
        drop_pages(fd)
    while True:
        b = os.read(fd, 8 << 20)
        if not b:
            break
        h.update(b)
        if drop_cache:
            drop_pages(fd)
    return h.hexdigest()


def model_list(s: Settings, ops: Ops, hash_models: bool) -> str:
    """Every GGUF under the model directories and every GGUF a user unit
    names: path, size, SHA-256 (cached by path, size and mtime)."""
    paths: dict[str, set] = {}
    for d in s.model_dirs:
        for dirpath, _dirs, files in os.walk(d, followlinks=False):
            for f in files:
                if f.endswith(".gguf"):
                    paths.setdefault(os.path.join(dirpath, f), set())
    units = HOME / ".config" / "systemd" / "user"
    for u in sorted(units.glob("*.service")) if units.is_dir() else []:
        try:
            for m in re.finditer(r"(?:%h|/)\S*?\.gguf", u.read_text()):
                p = m.group(0).replace("%h", str(HOME))
                paths.setdefault(p, set()).add(u.name)
        except OSError:
            pass
    cache_p = s.state / "model_sums.json"
    try:
        cache = json.loads(cache_p.read_text())
    except (OSError, ValueError):
        cache = {}
    rows = ["# sha256\tsize\tmodified\tpath\tnamed by", ]
    for p in sorted(paths):
        try:
            st = os.stat(p)
        except OSError as e:
            rows.append(f"-\t-\t-\t{p}\t{','.join(sorted(paths[p])) or '-'} (missing: {e.strerror})")
            continue
        key = f"{st.st_size}:{st.st_mtime_ns}"
        digest = cache.get(p, {}).get(key) if isinstance(cache.get(p), dict) else None
        if not digest and hash_models:
            t0 = time.monotonic()
            digest = sha256_file(Path(p))
            ops.log(f"hashed {p} ({human(st.st_size)}) in {time.monotonic() - t0:.0f} s")
            cache[p] = {key: digest}
        mod = dt.datetime.fromtimestamp(st.st_mtime).date().isoformat()
        rows.append(f"{digest or '(not hashed)'}\t{st.st_size}\t{mod}\t{p}\t{','.join(sorted(paths[p])) or '-'}")
    if hash_models:
        s.state.mkdir(parents=True, exist_ok=True)
        write_json(cache_p, cache)
    return "\n".join(rows) + "\n"


def manifest(top: str, c: Collected, generated: list[Member], gaps: list[str]) -> bytes:
    out = [f"# {top}: one line per member: sha256, size, how it was read, member path, source",
           "# names with control characters are JSON-escaped", ""]
    for m in c.members + generated:
        what = (f"symlink -> {show(m.link)}" if m.link is not None else "directory" if m.isdir
                else hashlib.sha256(m.data).hexdigest())
        out.append(f"{what}\t{m.size}\t{m.how}\t{show(m.arc)}\t<- {show(m.source)}")
    out += ["", "# REQUIRED items that could not be read (this tar is INCOMPLETE when any is listed):"]
    out += [f"REQUIRED\t{g}" for g in gaps] or ["(none)"]
    out += ["", "# could not be read (a gap in this backup):"]
    out += [f"UNREADABLE\t{show(src)}\t{why}" for src, why in c.unreadable] or ["(none)"]
    out += ["", "# absent on this host (nothing to back up):"]
    out += [f"ABSENT\t{show(src)}" for src, _ in c.absent] or ["(none)"]
    out += ["", "# in Home Assistant's /config, not known to this backup, NOT copied (look):"]
    out += [f"UNKNOWN\thomeassistant:/config/{show(n)}" for n in c.unknown] or ["(none)"]
    out += ["", "# MANIFEST itself is the last member and is not listed above.", ""]
    return "\n".join(out).encode()


def build_tar(top: str, members: list[Member]) -> tuple[bytes, str]:
    """The whole .tar.gz in memory (the members already are): its size is
    known, and checked against the ceiling, before anything reaches the
    destination. The top directory is its own member, mode 0700."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as t:
        ti = tarfile.TarInfo(top)
        ti.type, ti.mode, ti.mtime, ti.uname, ti.gname = tarfile.DIRTYPE, 0o700, int(time.time()), "", ""
        t.addfile(ti)
        for m in members:
            ti = tarfile.TarInfo(f"{top}/{m.arc}")
            ti.mtime = int(m.mtime)
            ti.mode = m.mode
            ti.uname = ti.gname = ""
            if m.link is not None:
                ti.type, ti.linkname = tarfile.SYMTYPE, m.link
                t.addfile(ti)
            elif m.isdir:
                ti.type = tarfile.DIRTYPE
                t.addfile(ti)
            else:
                ti.size = m.size
                t.addfile(ti, io.BytesIO(m.data))
    blob = buf.getvalue()
    return blob, hashlib.sha256(blob).hexdigest()


def verify_tar(p: Path, want_sha: str, want_n: int) -> str | None:
    """Read the file back (page cache dropped first): SHA-256 equal to the
    one computed while writing, and tar reads it to the end with the expected
    number of entries."""
    got = sha256_file(p, drop_cache=True)
    if got != want_sha:
        return f"read back differs (sha256 {got[:12]} written {want_sha[:12]})"
    try:
        with tarfile.open(p, "r:gz") as t:
            n = sum(1 for _ in t)
    except (tarfile.TarError, OSError, EOFError) as e:
        return f"tar cannot read it to the end: {e}"
    return None if n == want_n else f"tar lists {n} members, {want_n} were written"


def next_name(cfg: Path, day: str, incomplete: bool = False) -> str:
    kind = ".incomplete" if incomplete else ""
    name, k = f"kronk-config-{day}{kind}.tar.gz", 1
    while os.path.lexists(cfg / name) or os.path.lexists(cfg / f".{name}.tmp"):
        k += 1
        name = f"kronk-config-{day}.{k}{kind}.tar.gz"
    return name


def retention(cfg: Path, keep: int, log) -> list[str]:
    """Remove all but the newest <keep> files named like this job's tars,
    in <dest>/config only. Returns the names removed."""
    found = []
    for e in os.scandir(cfg):
        m = CONFIG_RE.fullmatch(e.name)
        if m and e.is_file(follow_symlinks=False):
            found.append(((m.group(1), int(m.group(2) or 1)), e.name))
    found.sort()
    removed = []
    for _key, name in found[:-keep] if keep > 0 else []:
        os.unlink(cfg / name)
        log(f"retention: removed {name}")
        removed.append(name)
    return removed


def cmd_config(s: Settings, ops: Ops, dry_run: bool, hash_models: bool = True) -> int:
    now = ops.now()
    day = now.date().isoformat()
    if dry_run:
        prob = dest_problem(s, ops, probe=False)
        ops.log(f"dry run: destination {s.dest}: {prob or 'usable'} (mount required: {s.require_mount}"
                + (f", on the share at {s.mount_root}" if s.mount_root else "") + ")")
        c = collect(s, ops)
        for m in sorted((m for m in c.members if not m.isdir), key=lambda m: m.arc):
            ops.log(f"  {m.size:>12}  {show(m.arc)}" + (" -> " + show(m.link) if m.link is not None else ""))
        ops.log(f"  generated: MANIFEST, repo-state.txt, images.txt, models.tsv, host-facts.txt"
                f" (and repo-unpushed.bundle when commits are unpushed)")
        for g in required_gaps(s, c):
            ops.log(f"  REQUIRED, NOT READ {g}")
        for src, why in c.unreadable:
            ops.log(f"  UNREADABLE {show(src)}: {why}")
        for src, _ in c.absent:
            ops.log(f"  absent     {show(src)}")
        for n in c.unknown:
            ops.log(f"  not known, not copied: homeassistant:/config/{show(n)}")
        ops.log(f"dry run: {sum(not m.isdir for m in c.members)} files and links, {human(sum(m.size for m in c.members))} before compression; "
                f"nothing written, nothing sent")
        return 0

    fd, why = take_lock(s, ops, "config")
    run = {"at": iso(now), "result": "failed"}
    if fd is None:
        run.update(result="declined", detail=why, finished=iso(ops.now()))
        ops.log(f"declined: {why}")
        run["notice_sent"] = ops.notify("Kronk backup: config backup DECLINED", f"Declined: {why}. Run scripts/backup.sh by hand later.")
        save_run(s, ops, "config", run)
        return 3
    try:
        clean_own_leftovers(s, ops)
        return _config_run(s, ops, run, day, hash_models)
    finally:
        os.close(fd)          # also on a KeyboardInterrupt: the lock is never kept past the run


def _config_run(s: Settings, ops: Ops, run: dict, day: str, hash_models: bool) -> int:
    try:
        ops.notify("Kronk backup: config backup started", f"Writing the {day} config backup to {s.dest}/config.")
        rc, run = _config(s, ops, run, day, hash_models)
    except Exception as e:  # noqa: BLE001 - a failure is reported, never silent
        run.update(result="failed", detail=f"{e.__class__.__name__}: {e}")
        rc = 1
    run["finished"] = iso(ops.now())
    totals = None
    if dest_problem(s, ops, probe=False) is None:
        try:
            totals = dest_totals(s)
            run["totals"] = totals
        except OSError:
            pass
    if run["result"] in ("ok", "incomplete"):
        gaps = ""
        if run["result"] == "incomplete":
            gaps += (f" MISSING REQUIRED ({len(run['required_gaps'])}): " + "; ".join(run["required_gaps"][:5])
                     + ". This tar does not count as a backup: it is kept, retention ignores it, "
                       "and no complete tar was removed.")
        if run.get("unpushed"):
            gaps += (f" Repo: {run['unpushed']} commit{'' if run['unpushed'] == 1 else 's'} not pushed ("
                     + ("the tar holds them as repo-unpushed.bundle)." if run.get("bundle")
                        else f"they exist only on this machine: {run.get('bundle_note')}).")
                     )
        other = [u for u in run.get("unreadable", []) if u not in run.get("required_gaps", [])]
        if other:
            gaps += f" NOT READ ({len(other)}): " + "; ".join(other[:5]) + "."
        if run.get("unknown"):
            gaps += f" HA files not known to the backup, not copied: {', '.join(run['unknown'][:5])}."
        word = "OK" if run["result"] == "ok" else "INCOMPLETE"
        title, body = f"Kronk backup: config backup {word}", (
            f"{run['file']}, {human(run['size'])}, {run['members']} entries.{gaps}"
            f"{' Removed by retention: ' + ', '.join(run['removed']) + '.' if run.get('removed') else ''}")
    else:
        title, body = "Kronk backup: config backup FAILED", f"{run['detail']}."
    if totals:
        body += " " + totals_line(totals)
    ops.log(f"{title}: {body}")
    run["notice_sent"] = ops.notify(title, body)
    save_run(s, ops, "config", run)
    return rc


def _config(s: Settings, ops: Ops, run: dict, day: str, hash_models: bool) -> tuple[int, dict]:
    prob = dest_problem(s, ops, wait=True)
    if prob:
        run["detail"] = prob
        return 2, run
    cfg = s.dest / "config"
    cfg.mkdir(mode=0o700, exist_ok=True)
    for e in os.scandir(cfg):          # an earlier run's temporary file (we hold the lock)
        if CONFIG_TMP_RE.fullmatch(e.name) and e.is_file(follow_symlinks=False):
            os.unlink(cfg / e.name)
            ops.log(f"removed a stale temporary file: {e.name}")
    c = collect(s, ops)
    gaps = required_gaps(s, c)
    name = next_name(cfg, day, incomplete=bool(gaps))
    top = name[: -len(".tar.gz")]       # a same-day .2 tar unpacks into its own directory
    t = ops.now().timestamp()
    rows, tags = production_tags(s, ops)
    branch = git(s, ops, "rev-parse", "--abbrev-ref", "HEAD")
    bundle, bundle_note = unpushed_bundle(s, ops, branch, sorted(tags))
    gen = [Member("repo-state.txt", "git, docker ps", "generated",
                  repo_state(s, ops, rows, tags, bundle_note).encode(), 0o600, t),
           Member("images.txt", "compose files, docker inspect", "generated", image_pins(s, ops).encode(), 0o600, t),
           Member("models.tsv", ",".join(s.model_dirs) + ", user units", "generated",
                  model_list(s, ops, hash_models).encode(), 0o600, t),
           Member("host-facts.txt", "/proc/cmdline, /etc/fstab, uname", "generated",
                  host_facts(s, ops).encode(), 0o600, t)]
    if bundle is not None:
        gen.append(Member("repo-unpushed.bundle", "git bundle create HEAD <branch> <tags> --not --remotes",
                          "generated", bundle, 0o600, t))
    man = Member("MANIFEST", "this run", "generated", manifest(top, c, gen, gaps), 0o600, t)
    members = c.members + gen + [man]
    blob, sha = build_tar(top, members)
    size = len(blob)
    if size > s.max_bytes:            # checked before anything reaches the destination
        big = sorted(members, key=lambda m: -m.size)[:5]
        run["detail"] = (f"the tar is {human(size)}, over the {human(s.max_bytes)} ceiling (KRONK_BACKUP_MAX_MB); "
                         "largest members: " + ", ".join(f"{show(m.arc)} {human(m.size)}" for m in big)
                         + "; nothing was written to the destination")
        return 1, run
    tmp = cfg / f".{name}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        try:
            _write_all(fd, blob)
            os.fsync(fd)
            drop_pages(fd)
        finally:
            os.close(fd)
        del blob
        bad = verify_tar(tmp, sha, len(members) + 1)       # + the top directory
        if bad:
            os.unlink(tmp)
            run["detail"] = bad + "; the temporary file was removed, no backup was written"
            return 1, run
        if os.path.lexists(cfg / name):
            raise FileExistsError(f"{cfg / name} appeared during the run; not overwritten")
        os.rename(tmp, cfg / name)
        os.chmod(cfg / name, 0o600)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise
    removed = retention(cfg, s.keep, ops.log) if not gaps else []   # an incomplete run never removes a tar
    run.update(result="incomplete" if gaps else "ok", file=name, path=str(cfg / name), size=size, sha256=sha,
               members=len(members), unreadable=[f"{show(a)}: {b}" for a, b in c.unreadable],
               required_gaps=gaps, absent=[show(a) for a, _ in c.absent], unknown=[show(n) for n in c.unknown],
               removed=removed, bundle=bundle is not None, bundle_note=bundle_note,
               detail="written and verified" + ("; INCOMPLETE: a required item is missing" if gaps else ""))
    m = re.search(r"^not pushed: (\d+) commits", gen[0].data.decode(), re.M)
    if m and int(m.group(1)):
        run["unpushed"] = int(m.group(1))
    return (4 if gaps else 0), run


# ═══ The recordings archive ════════════════════════════════════════════════

@dataclass
class Rec:
    rel: tuple[str, str, str, str]     # device, pipeline, run, file
    size: int
    mtime_ns: int
    dev: int = 0
    ino: int = 0

    @property
    def path(self) -> str:
        return "/".join(self.rel)


@dataclass
class Verified:
    """A recording whose copy on the destination was read back equal, and
    what the delete must find again before it removes the local file."""
    rec: Rec
    month: str
    sha256: str
    dst_dev: int
    dst_ino: int
    placed: str = ""          # how a new copy was put in place (PLACED); "" when it was already there

    def delete_args(self) -> list[str]:
        r = self.rec
        return [*r.rel[:3], r.rel[3], self.month, str(r.size), str(r.mtime_ns // 1_000_000_000),
                f"{r.dev}:{r.ino}", self.sha256, f"{self.dst_dev}:{self.dst_ino}"]


class Refused(Exception):
    """A symlink, or something that is not a directory, where a directory must be."""


# How a new copy is put under its final name, in the order tried.
PLACED = ("link(2)", "renameat2(RENAME_NOREPLACE)", "rename after a check")
RENAME_NOREPLACE = 1


def _libc_renameat2():
    try:
        f = ctypes.CDLL(None, use_errno=True).renameat2
    except (OSError, AttributeError):
        return None                         # a libc without it: the plain rename after a check
    f.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    f.restype = ctypes.c_int
    return f


RENAMEAT2 = _libc_renameat2()


def rename_noreplace(old: str, new: str, dir_fd: int) -> bool:
    """renameat2(RENAME_NOREPLACE) inside one directory: True when done;
    False when libc, the kernel or the filesystem does not offer it (EINVAL,
    ENOSYS, EOPNOTSUPP). FileExistsError when <new> exists (nothing
    replaced); OSError for anything else."""
    f = RENAMEAT2
    if f is None:
        return False
    if f(dir_fd, os.fsencode(old), dir_fd, os.fsencode(new), RENAME_NOREPLACE) == 0:
        return True
    e = ctypes.get_errno()
    if e in (errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP, errno.ENOTSUP):
        return False
    raise OSError(e, os.strerror(e))        # EEXIST comes out as FileExistsError


O_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
O_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC     # NONBLOCK: a FIFO never hangs the run


def open_chain(root_fd: int, parts, create: bool = False) -> int:
    """A directory fd for <root>/<parts...>, opened one level at a time with
    O_NOFOLLOW, so no symlink on the way is ever followed; missing levels are
    made 0700 when create is set. The caller closes the fd."""
    fd = os.dup(root_fd)
    try:
        for i, name in enumerate(parts):
            try:
                nfd = os.open(name, O_DIR, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(name, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                try:
                    nfd = os.open(name, O_DIR, dir_fd=fd)
                except OSError as e:
                    if e.errno in (errno.ELOOP, errno.ENOTDIR):
                        raise Refused(f"{show('/'.join(parts[:i + 1]))} is a symlink or not a directory") from None
                    raise
            except OSError as e:
                if e.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise Refused(f"{show('/'.join(parts[:i + 1]))} is a symlink or not a directory") from None
                raise
            os.close(fd)
            fd = nfd
    except BaseException:
        os.close(fd)
        raise
    return fd


def scan_recordings(src: Path) -> tuple[list[Rec], list[tuple[str, str]]]:
    """Every regular file at <device>/<pipeline>/<run>/<file>. Symlinks are
    never followed; anything else is reported and left alone."""
    recs, skipped = [], []

    def walk(d: Path, parts: tuple):
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError as e:
            skipped.append(("/".join(parts) or ".", f"cannot list: {e.strerror}"))
            return
        for e in entries:
            rel = parts + (e.name,)
            if e.is_symlink():
                skipped.append(("/".join(rel), "symlink, not followed"))
            elif e.is_dir(follow_symlinks=False):
                if len(rel) < 4:
                    walk(Path(e.path), rel)
                else:
                    skipped.append(("/".join(rel), "unexpected directory"))
            elif e.is_file(follow_symlinks=False):
                if len(rel) != 4:
                    skipped.append(("/".join(rel), "not at <device>/<pipeline>/<run>/<file>"))
                elif is_archive_tmp(e.name):
                    skipped.append(("/".join(rel), "named like the archive's temporary files; never archived "
                                                   "(rename it to archive it)"))
                else:
                    st = e.stat(follow_symlinks=False)
                    recs.append(Rec(rel, st.st_size, st.st_mtime_ns, st.st_dev, st.st_ino))
            else:
                skipped.append(("/".join(rel), "not a regular file"))
    walk(src, ())
    return recs, skipped


def _unlink_own(name: str, dir_fd: int, own: tuple[int, int]) -> None:
    """Remove <name> only if it is still the file this run created (same
    device and inode): never anything else of that name."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if stat.S_ISREG(st.st_mode) and (st.st_dev, st.st_ino) == own:
            os.unlink(name, dir_fd=dir_fd)
    except OSError:
        pass


def _same_source(sfd: int, sdir: int, rec: Rec) -> bool:
    """The source is still the file the scan found: by its fd and by its name."""
    try:
        a = os.fstat(sfd)
        b = os.stat(rec.rel[3], dir_fd=sdir, follow_symlinks=False)
    except OSError:
        return False
    want = (rec.size, rec.mtime_ns, rec.dev, rec.ino)
    return all((x.st_size, x.st_mtime_ns, x.st_dev, x.st_ino) == want and stat.S_ISREG(x.st_mode) for x in (a, b))


def copy_verified(src_fd: int, dst_fd: int, rec: Rec, month: str) -> tuple[str, str, Verified | None]:
    """Copy one recording to <dst>/<device>/<pipeline>/<month>/<run>/<file>
    by way of a temporary file of this run's own, read it back (page cache
    dropped), and link it into place without replacing anything. Returns
    (outcome, detail, Verified or None); outcomes: copied, same, conflict,
    changed, mismatch, error, full."""
    sdir = ddir = sfd = None
    try:
        try:
            sdir = open_chain(src_fd, rec.rel[:3])
            sfd = os.open(rec.rel[3], O_FILE, dir_fd=sdir)
        except Refused as e:
            return "conflict", f"source: {e}; not followed", None
        except FileNotFoundError:
            return "changed", "gone since the scan; left for the next run", None
        except OSError as e:
            return "error", f"cannot open: {e.strerror}", None
        if not _same_source(sfd, sdir, rec):
            return "changed", "changed since the scan; left for the next run", None
        try:
            ddir = open_chain(dst_fd, (rec.rel[0], rec.rel[1], month, rec.rel[2]), create=True)
        except Refused as e:
            return "conflict", f"destination: {e}; nothing written there, kept locally", None
        try:
            fst = os.stat(rec.rel[3], dir_fd=ddir, follow_symlinks=False)
        except FileNotFoundError:
            fst = None
        if fst is not None:
            return _already_there(sfd, sdir, ddir, rec, month, fst)
        return _copy_new(sfd, sdir, ddir, rec, month)
    finally:
        for fd in (sfd, sdir, ddir):
            if fd is not None:
                os.close(fd)


def _already_there(sfd, sdir, ddir, rec, month, fst) -> tuple[str, str, Verified | None]:
    if not stat.S_ISREG(fst.st_mode):
        return "conflict", "the destination has something else of the same name (kept locally)", None
    if (fst.st_dev, fst.st_ino) == (rec.dev, rec.ino):
        return "conflict", "the destination file is the source file itself (the trees are linked); kept", None
    if fst.st_size != rec.size:
        return "conflict", "the destination has a different file of the same name (both kept)", None
    try:
        ffd = os.open(rec.rel[3], O_FILE, dir_fd=ddir)
    except OSError as e:
        return "error", f"cannot read the destination copy: {e.strerror}", None
    try:
        f2 = os.fstat(ffd)
        if (f2.st_dev, f2.st_ino) != (fst.st_dev, fst.st_ino):
            return "conflict", "the destination file changed while it was checked (kept locally)", None
        there = sha256_fd(ffd, drop_cache=True)
    finally:
        os.close(ffd)
    here = sha256_fd(sfd, drop_cache=False)
    if there != here:
        return "conflict", "the destination has a different file of the same name (both kept)", None
    if not _same_source(sfd, sdir, rec):
        return "changed", "changed while it was read; left for the next run", None
    return "same", "already on the destination with the same SHA-256", Verified(rec, month, here, fst.st_dev, fst.st_ino)


def _copy_new(sfd, sdir, ddir, rec, month) -> tuple[str, str, Verified | None]:
    name = rec.rel[3]
    while True:
        tmpname = f"{ARCHIVE_TMP_PREFIX}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        try:
            dfd = os.open(tmpname, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                          dir_fd=ddir)
            break
        except FileExistsError:
            continue                    # someone else's file: never touched; another name
        except OSError as e:
            if e.errno in (errno.ENOSPC, errno.EDQUOT):
                return "full", f"destination full: {e.strerror}", None
            return "error", f"cannot create the temporary file: {e.strerror}", None
    t0 = os.fstat(dfd)
    own = (t0.st_dev, t0.st_ino)
    h, n = hashlib.sha256(), 0
    try:
        try:
            os.lseek(sfd, 0, os.SEEK_SET)
            while True:
                b = os.read(sfd, 1 << 20)
                if not b:
                    break
                _write_all(dfd, b)
                h.update(b)
                n += len(b)
            os.fchmod(dfd, 0o600)
            os.utime(dfd, ns=(rec.mtime_ns, rec.mtime_ns))
            os.fsync(dfd)
            drop_pages(dfd)
        finally:
            os.close(dfd)
    except OSError as e:
        _unlink_own(tmpname, ddir, own)
        if e.errno in (errno.ENOSPC, errno.EDQUOT):
            return "full", f"destination full: {e.strerror}", None
        return "error", f"copy failed: {e.strerror}", None
    if n != rec.size or not _same_source(sfd, sdir, rec):
        _unlink_own(tmpname, ddir, own)
        return "changed", "changed while it was read; left for the next run", None
    want = h.hexdigest()
    try:
        rfd = os.open(tmpname, O_FILE, dir_fd=ddir)
    except OSError as e:
        _unlink_own(tmpname, ddir, own)
        return "error", f"read back failed: {e.strerror}", None
    try:
        r0 = os.fstat(rfd)
        if (r0.st_dev, r0.st_ino) != own:      # not ours any more: left alone, not removed
            return "error", "the temporary file was replaced during the copy; left alone, kept locally", None
        back = sha256_fd(rfd, drop_cache=True)
        back_size = os.fstat(rfd).st_size
    except OSError as e:
        _unlink_own(tmpname, ddir, own)
        return "error", f"read back failed: {e.strerror}", None
    finally:
        os.close(rfd)
    if back_size != n or back != want:
        _unlink_own(tmpname, ddir, own)
        return "mismatch", "the copy read back different; kept locally", None
    # Into place without replacing anything: link(2) fails on an existing
    # name. A share without hard links gets renameat2(RENAME_NOREPLACE),
    # which fails on one too; where that is not offered either, a rename
    # after a fresh check (residual window: between the check and the rename).
    appeared = "a file of the same name appeared on the destination during the copy (both kept)"
    placed = PLACED[0]
    try:
        os.link(tmpname, name, src_dir_fd=ddir, dst_dir_fd=ddir, follow_symlinks=False)
        _unlink_own(tmpname, ddir, own)
    except FileExistsError:
        _unlink_own(tmpname, ddir, own)
        return "conflict", appeared, None
    except OSError as e:
        if e.errno not in (errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP, errno.ENOSYS, errno.EMLINK):
            _unlink_own(tmpname, ddir, own)
            return "error", f"cannot put the copy in place: {e.strerror}", None
        try:
            done = rename_noreplace(tmpname, name, ddir)
        except FileExistsError:
            _unlink_own(tmpname, ddir, own)
            return "conflict", appeared, None
        except OSError as e2:
            _unlink_own(tmpname, ddir, own)
            return "error", f"cannot put the copy in place: {e2.strerror}", None
        placed = PLACED[1] if done else PLACED[2]
        if not done:
            try:
                os.stat(name, dir_fd=ddir, follow_symlinks=False)
                _unlink_own(tmpname, ddir, own)
                return "conflict", appeared, None
            except FileNotFoundError:
                os.rename(tmpname, name, src_dir_fd=ddir, dst_dir_fd=ddir)
    try:
        f = os.stat(name, dir_fd=ddir, follow_symlinks=False)
    except OSError as e:
        return "error", f"the archived copy cannot be found after it was placed: {e.strerror}", None
    if (f.st_dev, f.st_ino) != own or f.st_size != n:
        return "error", "the archived copy is not the file that was written; kept locally", None
    return "copied", "", Verified(rec, month, want, f.st_dev, f.st_ino, placed)


def _write_all(fd: int, b: bytes) -> None:
    view = memoryview(b)
    while view:
        view = view[os.write(fd, view):]


def local_exists(src_fd: int, rec: Rec) -> bool:
    try:
        d = open_chain(src_fd, rec.rel[:3])
    except FileNotFoundError:
        return False
    except (Refused, OSError):
        return True               # cannot tell: counted as still there
    try:
        os.stat(rec.rel[3], dir_fd=d, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True
    finally:
        os.close(d)


def cmd_archive(s: Settings, ops: Ops, dry_run: bool) -> int:
    now = ops.now()
    cutoff_ns = int((now - dt.timedelta(days=s.age_days)).timestamp() * 1e9)
    src = s.recordings
    if dry_run:
        prob = dest_problem(s, ops, probe=False) or trees_problem(s)
        ops.log(f"dry run: destination {s.dest}: {prob or 'usable'} (mount required: {s.require_mount})")
        recs, skipped = scan_recordings(src) if plain_dir(src) else ([], [(str(src), "not a directory, or a symlink")])
        old = [r for r in recs if r.mtime_ns < cutoff_ns]
        for r in old:
            month = dt.datetime.fromtimestamp(r.mtime_ns / 1e9).strftime("%Y-%m")
            ops.log(f"  would move {r.size:>9}  {show(r.path)} -> recordings/{show(r.rel[0])}/{show(r.rel[1])}/{month}/{show(r.rel[2])}/")
        for p, why in skipped:
            ops.log(f"  skipped {show(p)}: {why}")
        ops.log(f"dry run: {len(old)} of {len(recs)} recordings are older than {s.age_days} days "
                f"({human(sum(r.size for r in old))}); nothing written, nothing deleted, nothing sent")
        return 0

    fd, why = take_lock(s, ops, "archive")
    run = {"at": iso(now), "result": "failed"}
    if fd is None:
        run.update(result="declined", detail=why, finished=iso(ops.now()))
        run["notice_sent"] = ops.notify("Kronk backup: recordings archive DECLINED", f"Declined: {why}.")
        save_run(s, ops, "archive", run)
        return 3
    try:
        clean_own_leftovers(s, ops)
        return _archive_run(s, ops, run, cutoff_ns, src)
    finally:
        os.close(fd)          # also on a KeyboardInterrupt: the lock is never kept past the run


def _archive_run(s: Settings, ops: Ops, run: dict, cutoff_ns: int, src: Path) -> int:
    try:
        ops.notify("Kronk backup: recordings archive started",
                   f"Moving recordings older than {s.age_days} days from {src} to {s.dest}/recordings.")
        rc = _archive(s, ops, run, cutoff_ns)
    except Exception as e:  # noqa: BLE001
        run.update(result="failed", detail=f"{e.__class__.__name__}: {e}")
        rc = 1
    run["finished"] = iso(ops.now())
    if plain_dir(src):
        left, _ = scan_recordings(src)
        run["remaining"], run["remaining_bytes"] = len(left), sum(r.size for r in left)
    totals = None
    if dest_problem(s, ops, probe=False) is None:
        totals = run["totals"] = dest_totals(s)
    moved = (f"Moved {run.get('moved', 0)} recording(s) ({human(run.get('moved_bytes', 0))}); "
             f"{run.get('remaining', '?')} remain local ({human(run.get('remaining_bytes', 0))}). "
             f"Written this run: {human(run.get('written_bytes', 0))}.")
    if run["result"] == "ok":
        title, body = "Kronk backup: recordings archive OK", moved
    else:
        title, body = "Kronk backup: recordings archive FAILED", f"{run['detail']}. {moved}"
    if run.get("left"):
        body += f" Left for the next run: {len(run['left'])} ({run['left'][0]})."
    if totals:
        body += " " + totals_line(totals)
    ops.log(f"{title}: {body}")
    run["notice_sent"] = ops.notify(title, body)
    save_run(s, ops, "archive", run)
    return rc


def plain_dir(p: Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(p).st_mode)
    except OSError:
        return False


def trees_problem(s: Settings) -> str | None:
    """The recordings directory and <dest>/recordings: real directories (not
    symlinks), neither inside the other."""
    src, root = s.recordings, s.dest / "recordings"
    try:
        st = os.lstat(src)
    except OSError:
        return f"recordings directory {src} does not exist"
    if stat.S_ISLNK(st.st_mode):
        return f"recordings directory {src} is a symlink; refused, not followed"
    if not stat.S_ISDIR(st.st_mode):
        return f"recordings directory {src} is not a directory"
    try:
        rt = os.lstat(root)
        if stat.S_ISLNK(rt.st_mode) or not stat.S_ISDIR(rt.st_mode):
            return f"destination {root} is a symlink or not a directory; refused"
    except FileNotFoundError:
        pass
    a, b = os.path.realpath(src), os.path.realpath(root)
    if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
        return f"the recordings directory {src} and the destination {root} overlap; refused"
    return None


def _archive(s: Settings, ops: Ops, run: dict, cutoff_ns: int) -> int:
    prob = dest_problem(s, ops, wait=True) or trees_problem(s)
    if prob:
        run["detail"] = prob
        return 2
    src = s.recordings
    root = s.dest / "recordings"
    try:
        os.mkdir(root, 0o700)
    except FileExistsError:
        pass
    src_fd = os.open(src, O_DIR)
    try:
        dst_fd = os.open(root, O_DIR)
    except BaseException:
        os.close(src_fd)
        raise
    try:
        return _archive_files(s, ops, run, cutoff_ns, src_fd, dst_fd)
    finally:
        os.close(src_fd)
        os.close(dst_fd)


def _archive_files(s: Settings, ops: Ops, run: dict, cutoff_ns: int, src_fd: int, dst_fd: int) -> int:
    src, root = s.recordings, s.dest / "recordings"
    recs, skipped = scan_recordings(src)
    problems: list[str] = [f"{show(p)}: {why}" for p, why in skipped]
    left: list[str] = []
    pending: list[Verified] = []
    moved = [0, 0]                      # count, bytes
    written = 0
    full_at = None                      # the smallest size that did not fit this run
    placed = dict.fromkeys(PLACED, 0)

    def flush() -> None:
        # Every check of the destination copy (path, type, size, inode,
        # SHA-256) is made by DELETE_SCRIPT right before each rm.
        ready = list(pending)
        pending.clear()
        if not ready:
            return
        res, err = ops.remove_recordings(src, root, ready)
        for i, v in enumerate(ready):
            got = res.get(i)
            gone = not local_exists(src_fd, v.rec)
            if got not in (None, "ok"):
                problems.append(f"{show(v.rec.path)}: local delete refused ({got}); kept locally, "
                                "the copy on the destination stays")
            elif gone:
                moved[0] += 1
                moved[1] += v.rec.size
            else:
                problems.append(f"{show(v.rec.path)}: local delete failed ({err[:120] or 'still there'}); "
                                "the copy on the destination is verified, the next run deletes it")

    for rec in recs:
        if rec.mtime_ns >= cutoff_ns:
            continue
        if full_at is not None and rec.size >= full_at:
            problems.append(f"{show(rec.path)}: skipped, does not fit (a {human(full_at)} file did not)")
            continue
        month = dt.datetime.fromtimestamp(rec.mtime_ns / 1e9).strftime("%Y-%m")
        try:
            outcome, detail, v = copy_verified(src_fd, dst_fd, rec, month)
        except OSError as e:
            outcome, detail, v = "error", f"{e.strerror or e}", None
        if outcome in ("copied", "same") and v is not None:
            written += rec.size if outcome == "copied" else 0
            if v.placed:
                placed[v.placed] += 1
            pending.append(v)
            if len(pending) >= DELETE_BATCH:
                flush()
        elif outcome == "changed":
            left.append(f"{show(rec.path)}: {detail}")
        else:
            problems.append(f"{show(rec.path)}: {detail}")
            if outcome == "full":
                full_at = rec.size if full_at is None else min(full_at, rec.size)
    flush()
    ops.log("placement this run: " + ", ".join(f"{k} {n}" for k, n in placed.items()))
    run.update(moved=moved[0], moved_bytes=moved[1], written_bytes=written,
               left=left[:50], problems=problems[:50], problem_count=len(problems))
    if problems:
        run["detail"] = f"{len(problems)} problem(s): " + "; ".join(problems[:3])
        return 1
    run.update(result="ok", detail="done")
    return 0


# ═══ The stale check ═══════════════════════════════════════════════════════

def newest_config(s: Settings) -> tuple[str, dt.datetime] | None:
    best = None
    for e in os.scandir(s.dest / "config") if (s.dest / "config").is_dir() else []:
        m = CONFIG_RE.fullmatch(e.name)
        if m and e.is_file(follow_symlinks=False):
            t = dt.datetime.fromtimestamp(e.stat(follow_symlinks=False).st_mtime).astimezone()
            if best is None or t > best[1]:
                best = (e.name, t)
    return best


def stale_warnings(s: Settings, ops: Ops, st: dict) -> list[str]:
    now = ops.now()
    w = []
    prob = dest_problem(s, ops, probe=False, wait=True)
    if prob:
        w.append(prob)
    else:
        nc = newest_config(s)
        if nc is None:
            w.append(f"no config backup on {s.dest}/config")
        elif now - nc[1] > dt.timedelta(days=s.stale_days):
            w.append(f"the newest config backup, {nc[0]}, is {(now - nc[1]).days} days old (limit {s.stale_days})")
    limit = s.archive_interval_days + s.archive_margin_days
    ok = parse_iso((st.get("archive") or {}).get("last_ok_at"))
    if ok is None:
        w.append("the recordings archive has never finished a run")
    elif now - ok > dt.timedelta(days=limit):
        w.append(f"the recordings archive last finished {(now - ok).days} days ago (limit {limit})")
    for job, word in (("config", "config backup"), ("archive", "recordings archive")):
        last = (st.get(job) or {}).get("last") or {}
        if last.get("result") in ("failed", "declined", "incomplete"):
            w.append(f"the last {word} {last['result']} ({last.get('at')}): {str(last.get('detail'))[:160]}")
    return w


def cmd_check(s: Settings, ops: Ops) -> int:
    s.state.mkdir(parents=True, exist_ok=True)
    st = read_status(s.state)
    now = ops.now()
    w = stale_warnings(s, ops, st)
    prev = st.get("check") if isinstance(st.get("check"), dict) else {}
    notified = prev.get("notified") or {}
    # Same warnings with a day count one higher are not news: compare without the numbers.
    text = re.sub(r"\d+", "#", " | ".join(w))
    last = parse_iso(notified.get("at"))
    if w and (notified.get("text") != text or last is None or now - last >= dt.timedelta(days=7)):
        if ops.notify("Kronk backup: needs a look", "; ".join(w) + "."):
            notified = {"text": text, "at": iso(now)}
    elif not w:
        notified = {}
    st["check"] = {"at": iso(now), "warnings": w, "notified": notified}
    st["schema"] = SCHEMA
    st["limits"] = {"config_days": s.stale_days, "archive_days": s.archive_interval_days + s.archive_margin_days}
    st["updated"] = iso(now)
    write_json(s.state / "status.json", st)
    for x in w:
        ops.log(f"WARNING: {x}")
    if not w:
        ops.log("ok: config backup and recordings archive are current")
    return 1 if w else 0


JOB_WORDS = {"config": "config backup", "archive": "recordings archive", "check": "backup check"}


def main(argv=None, ops: Ops | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backups.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("config", "archive", "check"):
        p = sub.add_parser(name)
        p.add_argument("--dest", help="destination directory (default KRONK_BACKUP_DIR or /mnt/nas-backup)")
        p.add_argument("--require-mount", action="store_true", help="the destination must be a mount point")
        p.add_argument("--no-notify", action="store_true", help="send no notice (test and by-hand runs)")
        if name != "check":
            p.add_argument("--dry-run", action="store_true", help="list what would happen; write and send nothing")
        if name == "archive":
            p.add_argument("--src", help="recordings directory (default KRONK_RECORDINGS_DIR or ~/kronk-voice-recordings)")
    sub.add_parser("status", help="print the status file (reads only)")
    a = ap.parse_args(argv)
    ops = ops or Ops(log=lambda m: print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {m}", flush=True))
    ops.no_notify = ops.no_notify or getattr(a, "no_notify", False)
    s = Settings.load(a)
    if s.errors and a.cmd != "status" and not getattr(a, "dry_run", False):
        return bad_settings(s, ops, a.cmd)
    if s.errors:
        for e in s.errors:
            ops.log(f"bad setting: {e}")
        return 2
    if a.cmd == "config":
        return cmd_config(s, ops, a.dry_run)
    if a.cmd == "archive":
        return cmd_archive(s, ops, a.dry_run)
    if a.cmd == "check":
        return cmd_check(s, ops)
    try:
        print((s.state / "status.json").read_text(), end="")
    except FileNotFoundError:
        print(f"no status file yet ({s.state / 'status.json'})")
    return 0


def bad_settings(s: Settings, ops: Ops, job: str) -> int:
    """A setting that is not a usable number: nothing is done, and it says
    so in a notice and the status file (never a traceback)."""
    detail = "bad setting: " + "; ".join(s.errors) + " (~/.config/kronk/backup.env or the environment); nothing was done"
    ops.log(detail)
    now = iso(ops.now())
    title = f"Kronk backup: {JOB_WORDS[job]} FAILED"
    sent = ops.notify(title if job != "check" else "Kronk backup: needs a look", detail + ".")
    try:
        s.state.mkdir(parents=True, exist_ok=True)
        if job == "check":
            st = read_status(s.state)
            st["check"] = {"at": now, "warnings": [detail], "notified": {}}
            st["updated"] = now
            write_json(s.state / "status.json", st)
        else:
            save_run(s, ops, job, {"at": now, "finished": now, "result": "failed", "detail": detail,
                                   "notice_sent": sent})
    except OSError as e:
        ops.log(f"status not written: {e}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
