#!/usr/bin/env python3
"""backups.py: Kronk's config backup, recordings archive and stale check
(sprint M16). Python standard library only.

    backups.py config  [--dry-run] [--dest DIR] [--require-mount]
    backups.py archive [--dry-run] [--dest DIR] [--require-mount] [--src DIR]
    backups.py check   [--dest DIR] [--require-mount]
    backups.py status

config   One dated tar, <dest>/config/kronk-config-<YYYY-MM-DD>.tar.gz, mode
         0600: what is needed to rebuild Kronk (secrets included, in the
         clear), a MANIFEST, the repo state, the image pins, the model list
         and the host facts. Written to a temporary name, read back, then
         renamed. Keeps the newest KRONK_BACKUP_KEEP (12).
archive  Moves recordings older than KRONK_ARCHIVE_AGE_DAYS (7) from
         ~/kronk-voice-recordings to <dest>/recordings/<device>/<pipeline>/
         <YYYY-MM>/<run>/<file>: copied, read back (size and SHA-256), and
         only then deleted locally, by literal path, through a throwaway
         container (the files are root-owned). Never deletes a finished file
         on the destination.
check    The stale check: warns when the newest config tar is older than 35
         days or the last finished archive run older than its interval plus a
         margin. One notice when the warnings change, repeated weekly.
status   Prints the status file.

The destination is a directory (KRONK_BACKUP_DIR, default /mnt/nas-backup,
an SMB share mounted by /etc/fstab). The default, or any path with
KRONK_BACKUP_REQUIRE_MOUNT=1 or --require-mount, must be a mount point and
writable, or the job writes nothing there and fails: a tar written into an
unmounted mount point would sit on the local disk and look like a backup.

State: KRONK_BACKUP_STATE (default data/backups): status.json (read by the
orchestrator's GET /api/backups for /services), lock, model_sums.json (the
model hash cache). One lock for both jobs; a job waits (up to
KRONK_BACKUP_LOCK_WAIT_MIN, 30) while the other runs or while the monthly
upgrade run holds data/upgrades/lock, then declines.

Notices go through scripts/lib/notify.sh (KRONK_BACKUP_NOTIFY_CMD replaces it
with a command that gets tag, title and body as arguments). A notice that
fails is logged; it does not fail the job.

Guide: docs/features/backups.md. Restore: docs/runbooks/restore.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import errno
import fcntl
import hashlib
import io
import json
import os
import re
import shlex
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(os.environ.get("KRONK_REPO_DIR") or Path(__file__).resolve().parent.parent)
HOME = Path.home()
DEFAULT_DEST = "/mnt/nas-backup"
SCHEMA = 1
CONFIG_RE = re.compile(r"kronk-config-(\d{4}-\d{2}-\d{2})(?:\.(\d+))?\.tar\.gz")
CONFIG_TMP_RE = re.compile(r"\.kronk-config-\d{4}-\d{2}-\d{2}\.tar\.gz\.tmp")
ARCHIVE_TMP = ".kronk-archive.tmp"      # the archive's one temporary name, per run directory
PROBE = ".kronk-write-probe"            # the writability probe, created and removed at once
TAG = "kronk-backup"


def env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except ValueError:
        raise SystemExit(f"{name} must be a whole number, not {os.environ.get(name)!r}")


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

    def __init__(self, log=print):
        self.log = log

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
    def container_tar(self, container: str, cwd: str, names: list[str]) -> Result:
        """A tar stream of <names> (globs allowed) under <cwd> inside a running container."""
        script = 'cd "$1" && shift && exec tar -cf - -- $*'
        return self.run(["docker", "exec", container, "sh", "-c", script, "sh", cwd, *names], timeout=300)

    def container_cat(self, container: str, path: str) -> Result:
        return self.run(["docker", "exec", container, "cat", "--", path], timeout=120)

    def helper(self, mounts: list[str], argv: list[str], timeout: int = 300) -> Result:
        """A throwaway container of an image already on the host (no pull, no network)."""
        image = env("KRONK_BACKUP_HELPER_IMAGE", "alpine:3.20")
        for m in mounts:
            if m.count(":") > 2 or "," in m or "\n" in m:
                return Result(-1, b"", f"refused: a path docker -v cannot carry: {m!r}")
        vols = [x for m in mounts for x in ("-v", m)]
        return self.run(["docker", "run", "--rm", "--pull=never", "--network", "none", *vols, image, *argv],
                        timeout=timeout)

    def helper_tar(self, hostdir: Path) -> Result:
        return self.helper([f"{hostdir}:/src:ro"], ["tar", "-cf", "-", "-C", "/src", "."])

    def remove_recordings(self, src: Path, rels: list[str], dirs: list[str]) -> Result:
        """rm each file, then rmdir each run directory that is now empty, by
        literal path, inside a container that sees only <src>. The paths are
        arguments, never part of the script."""
        mode = env("KRONK_ARCHIVE_DELETE", "docker")
        if mode == "direct":       # a tree the operator owns (tests, scratch copies)
            for r in rels:
                try:
                    os.unlink(src / r)
                except OSError:
                    pass
            for d in dirs:
                try:
                    os.rmdir(src / d)
                except OSError:
                    pass
            return Result(0, b"", "")
        script = ('n=$1; shift; i=0; for f do i=$((i+1)); '
                  'if [ $i -le $n ]; then rm -- "/rec/$f"; else rmdir -- "/rec/$f" 2>/dev/null; fi; done; exit 0')
        return self.helper([f"{src}:/rec"], ["sh", "-c", script, "sh", str(len(rels)), *rels, *dirs])


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

    @classmethod
    def load(cls, args) -> "Settings":
        dest = getattr(args, "dest", None) or env("KRONK_BACKUP_DIR", DEFAULT_DEST)
        flagged = getattr(args, "require_mount", False) or env("KRONK_BACKUP_REQUIRE_MOUNT", "0") == "1"
        return cls(
            dest=Path(dest),
            require_mount=flagged or os.path.normpath(dest) == DEFAULT_DEST,
            state=Path(env("KRONK_BACKUP_STATE", str(REPO / "data" / "backups"))),
            keep=env_int("KRONK_BACKUP_KEEP", 12),
            max_bytes=env_int("KRONK_BACKUP_MAX_MB", 50) * 1000 * 1000,
            recordings=Path(getattr(args, "src", None) or env("KRONK_RECORDINGS_DIR", str(HOME / "kronk-voice-recordings"))),
            age_days=env_int("KRONK_ARCHIVE_AGE_DAYS", 7),
            stale_days=env_int("KRONK_BACKUP_STALE_DAYS", 35),
            archive_interval_days=env_int("KRONK_ARCHIVE_INTERVAL_DAYS", 7),
            archive_margin_days=env_int("KRONK_ARCHIVE_MARGIN_DAYS", 2),
            lock_wait_min=env_int("KRONK_BACKUP_LOCK_WAIT_MIN", 30),
            upgrade_state=Path(env("KRONK_UPGRADE_STATE", str(REPO / "data" / "upgrades"))),
            live_config=Path(env("KRONK_LIVE_CONFIG", str(REPO.parent / "kronk-config"))),
            model_dirs=tuple(p for p in env("KRONK_MODEL_DIRS", f"/opt/models:{HOME}/model-staging").split(":") if p),
        )


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


def dest_problem(s: Settings, ops: Ops, probe: bool = True) -> str | None:
    """Why the destination cannot be written to, or None (ruling 2)."""
    d = s.dest
    if not d.is_dir():
        return f"destination {d} does not exist or is not a directory"
    if s.require_mount and not ops.ismount(d):
        return (f"destination {d} is not a mounted filesystem (the NAS share is not mounted: "
                f"`mount {d}` or check /etc/fstab); nothing was written there")
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


def tree_size(root: Path) -> tuple[int, int]:
    """Bytes and count of regular files under root, symlinks not followed."""
    total = n = 0
    for dirpath, _dirs, files in os.walk(root, followlinks=False):
        for f in files:
            try:
                st = os.lstat(os.path.join(dirpath, f))
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
                n += 1
    return total, n


def dest_totals(s: Settings) -> dict:
    c, cn = tree_size(s.dest / "config")
    r, rn = tree_size(s.dest / "recordings")
    return {"config": c, "config_files": cn, "recordings": r, "recordings_files": rn, "all": c + r}


def totals_line(t: dict) -> str:
    return (f"On the destination: config backups {human(t['config'])} ({files(t['config_files'])}), "
            f"recordings archive {human(t['recordings'])} ({files(t['recordings_files'])}), "
            f"together {human(t['all'])}.")


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
    ]


HA_NAMES = [".storage", "blueprints", ".HA_VERSION", "*.yaml"]


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
    r = ops.container_tar("homeassistant", "/config", HA_NAMES)
    if r.rc == 0 and r.out:
        try:
            add_tar_stream(c, "ha/config", "homeassistant:/config", "docker exec homeassistant tar", r.out, True)
        except tarfile.TarError as e:
            c.unreadable.append(("homeassistant:/config", f"tar stream unreadable: {e}"))
    else:
        c.unreadable.append(("homeassistant:/config (.storage, YAML, blueprints)",
                             f"docker exec failed ({r.rc}): {r.err[:200]}"))

    # Music Assistant: settings.json only (library.db is over 10 MB).
    r = ops.container_cat("music-assistant-server", "/data/settings.json")
    if r.rc == 0:
        c.add(Member("ma/settings.json", "music-assistant-server:/data/settings.json",
                     "docker exec music-assistant-server cat", r.out, 0o600, ops.now().timestamp(), secret=True))
    else:
        c.unreadable.append(("music-assistant-server:/data/settings.json", f"docker exec failed ({r.rc}): {r.err[:200]}"))

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
            c.unreadable.append((str(etc), f"helper container failed ({r.rc}): {r.err[:200]}"))
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
    return r.out.decode().strip() if r.rc == 0 else f"(git {' '.join(a)} failed: {r.err[:120]})"


def repo_state(s: Settings, ops: Ops) -> str:
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
             f"working tree: {'clean' if not dirty else str(len(dirty.splitlines())) + ' changed paths'}",
             f"last commit: {git(s, ops, 'log', '-1', '--format=%ci %s')}", "",
             "production (compose project kronk, the directory each container was created from):"]
    r = ops.run(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=kronk", "--format",
                 '{{.Names}}\t{{.Label "com.docker.compose.project.working_dir"}}'], timeout=60)
    rows = r.out.decode().strip().splitlines() if r.rc == 0 else [f"(docker ps failed: {r.err[:120]})"]
    lines += [f"  {x}" for x in rows]
    tags: dict[str, int] = {}
    for x in rows:
        if "\t" in x and "kronk-releases" in x:
            tags[Path(x.split("\t")[1]).name] = tags.get(Path(x.split("\t")[1]).name, 0) + 1
    lines.insert(6, "production tag: " + (", ".join(f"{t} ({n} containers)" for t, n in sorted(tags.items()))
                                          if tags else "none (not run from a release directory)"))
    return "\n".join(lines) + "\n"


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
    """SHA-256 of a large file without filling the page cache with it."""
    h = hashlib.sha256()
    with open(p, "rb", buffering=0) as f:
        fd = f.fileno()
        while True:
            b = f.read(8 << 20)
            if not b:
                break
            h.update(b)
            if drop_cache and hasattr(os, "posix_fadvise"):
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
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


class HashingWriter(io.RawIOBase):
    def __init__(self, fd: int):
        self.fd, self.h, self.n = fd, hashlib.sha256(), 0

    def writable(self):
        return True

    def write(self, b):
        b = bytes(b)
        view = memoryview(b)
        while view:
            k = os.write(self.fd, view)
            view = view[k:]
        self.h.update(b)
        self.n += len(b)
        return len(b)


def manifest(top: str, c: Collected, generated: list[Member]) -> bytes:
    out = [f"# {top}: one line per member: sha256, size, how it was read, member path, source",
           "# names with control characters are JSON-escaped", ""]
    for m in c.members + generated:
        what = (f"symlink -> {show(m.link)}" if m.link is not None else "directory" if m.isdir
                else hashlib.sha256(m.data).hexdigest())
        out.append(f"{what}\t{m.size}\t{m.how}\t{show(m.arc)}\t<- {show(m.source)}")
    out += ["", "# could not be read (a gap in this backup):"]
    out += [f"UNREADABLE\t{show(src)}\t{why}" for src, why in c.unreadable] or ["(none)"]
    out += ["", "# absent on this host (nothing to back up):"]
    out += [f"ABSENT\t{show(src)}" for src, _ in c.absent] or ["(none)"]
    out += ["", "# MANIFEST itself is the last member and is not listed above.", ""]
    return "\n".join(out).encode()


def write_tar(path_fd: int, top: str, members: list[Member]) -> tuple[str, int]:
    w = HashingWriter(path_fd)
    buf = io.BufferedWriter(w, buffer_size=1 << 20)
    with tarfile.open(fileobj=buf, mode="w|gz", format=tarfile.PAX_FORMAT) as t:
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
    buf.flush()
    return w.h.hexdigest(), w.n


def verify_tar(p: Path, want_sha: str, want_n: int) -> str | None:
    """Read the file back: SHA-256 equal to the one computed while writing,
    and tar reads it to the end with the expected number of members."""
    got = sha256_file(p, drop_cache=False)
    if got != want_sha:
        return f"read back differs (sha256 {got[:12]} written {want_sha[:12]})"
    try:
        with tarfile.open(p, "r:gz") as t:
            n = sum(1 for _ in t)
    except (tarfile.TarError, OSError, EOFError) as e:
        return f"tar cannot read it to the end: {e}"
    return None if n == want_n else f"tar lists {n} members, {want_n} were written"


def next_name(cfg: Path, day: str) -> str:
    name, k = f"kronk-config-{day}.tar.gz", 1
    while os.path.lexists(cfg / name):
        k += 1
        name = f"kronk-config-{day}.{k}.tar.gz"
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
    top = f"kronk-config-{day}"
    if dry_run:
        prob = dest_problem(s, ops, probe=False)
        ops.log(f"dry run: destination {s.dest}: {prob or 'usable'} (mount required: {s.require_mount})")
        c = collect(s, ops)
        for m in sorted((m for m in c.members if not m.isdir), key=lambda m: m.arc):
            ops.log(f"  {m.size:>12}  {show(m.arc)}" + (" -> " + show(m.link) if m.link is not None else ""))
        ops.log(f"  generated: MANIFEST, repo-state.txt, images.txt, models.tsv, host-facts.txt")
        for src, why in c.unreadable:
            ops.log(f"  UNREADABLE {show(src)}: {why}")
        for src, _ in c.absent:
            ops.log(f"  absent     {show(src)}")
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
        ops.notify("Kronk backup: config backup started", f"Writing the {day} config backup to {s.dest}/config.")
        rc, run = _config(s, ops, run, day, top, hash_models)
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
    if run["result"] == "ok":
        gaps = ""
        if run.get("unpushed"):
            gaps += f" Repo: {run['unpushed']} commits not pushed (they exist only on this machine; the tar does not hold the code)."
        if run.get("unreadable"):
            gaps = f" NOT READ ({len(run['unreadable'])}): " + "; ".join(run["unreadable"][:5]) + "."
        title, body = "Kronk backup: config backup OK", (
            f"{run['file']}, {human(run['size'])}, {run['members']} entries.{gaps}"
            f"{' Removed by retention: ' + ', '.join(run['removed']) + '.' if run.get('removed') else ''}")
    else:
        title, body = "Kronk backup: config backup FAILED", f"{run['detail']}."
    if totals:
        body += " " + totals_line(totals)
    ops.log(f"{title}: {body}")
    run["notice_sent"] = ops.notify(title, body)
    save_run(s, ops, "config", run)
    os.close(fd)
    return rc


def _config(s: Settings, ops: Ops, run: dict, day: str, top: str, hash_models: bool) -> tuple[int, dict]:
    prob = dest_problem(s, ops)
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
    t = ops.now().timestamp()
    gen = [Member("repo-state.txt", "git, docker ps", "generated", repo_state(s, ops).encode(), 0o600, t),
           Member("images.txt", "compose files, docker inspect", "generated", image_pins(s, ops).encode(), 0o600, t),
           Member("models.tsv", ",".join(s.model_dirs) + ", user units", "generated",
                  model_list(s, ops, hash_models).encode(), 0o600, t),
           Member("host-facts.txt", "/proc/cmdline, /etc/fstab, uname", "generated",
                  host_facts(s, ops).encode(), 0o600, t)]
    man = Member("MANIFEST", "this run", "generated", manifest(top, c, gen), 0o600, t)
    members = c.members + gen + [man]
    tmp = cfg / f".{top}.tar.gz.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        try:
            sha, n = write_tar(fd, top, members)
            os.fsync(fd)
        finally:
            os.close(fd)
        size = tmp.stat().st_size
        bad = verify_tar(tmp, sha, len(members))
        if not bad and size > s.max_bytes:
            big = sorted(members, key=lambda m: -m.size)[:5]
            bad = (f"the tar is {human(size)}, over the {human(s.max_bytes)} ceiling (KRONK_BACKUP_MAX_MB); "
                   "largest members: " + ", ".join(f"{show(m.arc)} {human(m.size)}" for m in big))
        if bad:
            os.unlink(tmp)
            run["detail"] = bad + "; the temporary file was removed, no backup was written"
            return 1, run
        name = next_name(cfg, day)
        os.rename(tmp, cfg / name)
        os.chmod(cfg / name, 0o600)
    except BaseException:
        if tmp.exists():
            os.unlink(tmp)
        raise
    removed = retention(cfg, s.keep, ops.log)
    run.update(result="ok", file=name, path=str(cfg / name), size=size, sha256=sha, members=len(members),
               unreadable=[f"{show(a)}: {b}" for a, b in c.unreadable], absent=[show(a) for a, _ in c.absent],
               removed=removed, detail="written and verified")
    m = re.search(r"^not pushed: (\d+) commits", gen[0].data.decode(), re.M)
    if m and int(m.group(1)):
        run["unpushed"] = int(m.group(1))
    return 0, run


# ═══ The recordings archive ════════════════════════════════════════════════

@dataclass
class Rec:
    rel: tuple[str, str, str, str]     # device, pipeline, run, file
    size: int
    mtime_ns: int

    @property
    def path(self) -> str:
        return "/".join(self.rel)


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
                if len(rel) == 4:
                    st = e.stat(follow_symlinks=False)
                    recs.append(Rec(rel, st.st_size, st.st_mtime_ns))
                else:
                    skipped.append(("/".join(rel), "not at <device>/<pipeline>/<run>/<file>"))
            else:
                skipped.append(("/".join(rel), "not a regular file"))
    walk(src, ())
    return recs, skipped


def copy_verified(src: Path, rec: Rec, ddir: Path) -> tuple[str, str]:
    """Copy one recording to ddir/<file> by way of ARCHIVE_TMP, read it back,
    rename it. Returns (outcome, detail): copied, same, conflict, changed,
    mismatch, error, full."""
    final = ddir / rec.rel[3]
    sp = src / rec.path
    if os.path.lexists(final):
        if not final.is_file() or final.is_symlink():
            return "conflict", "the destination has something else of the same name"
        if final.stat().st_size == rec.size and sha256_file(final, False) == sha256_file(sp, False):
            return "same", "already on the destination with the same SHA-256"
        return "conflict", "the destination has a different file of the same name (both kept)"
    mkdir_private(ddir)
    tmp = ddir / ARCHIVE_TMP
    if os.path.lexists(tmp):
        os.unlink(tmp)                 # this job's own leftover from an interrupted run
    h = hashlib.sha256()
    n = 0
    try:
        sfd = os.open(sp, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as e:
        return "error", f"cannot open: {e.strerror}"
    try:
        st0 = os.fstat(sfd)
        dfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            while True:
                b = os.read(sfd, 1 << 20)
                if not b:
                    break
                _write_all(dfd, b)
                h.update(b)
                n += len(b)
            os.fsync(dfd)
        finally:
            os.close(dfd)
        st1 = os.fstat(sfd)
        cur = os.lstat(sp)
    except OSError as e:
        _unlink_quiet(tmp)
        if e.errno in (errno.ENOSPC, errno.EDQUOT):
            return "full", f"destination full: {e.strerror}"
        return "error", f"copy failed: {e.strerror}"
    finally:
        os.close(sfd)
    if (n != st0.st_size or (st1.st_size, st1.st_mtime_ns) != (st0.st_size, st0.st_mtime_ns)
            or (cur.st_size, cur.st_mtime_ns) != (st0.st_size, st0.st_mtime_ns) or cur.st_ino != st0.st_ino):
        _unlink_quiet(tmp)
        return "changed", "changed while it was read; left for the next run"
    want = h.hexdigest()
    try:
        back_size = os.stat(tmp).st_size
        back = sha256_file(tmp, False)
    except OSError as e:
        _unlink_quiet(tmp)
        return "error", f"read back failed: {e.strerror}"
    if back_size != n or back != want:
        _unlink_quiet(tmp)
        return "mismatch", "the copy read back different; kept locally"
    os.utime(tmp, ns=(st0.st_atime_ns, st0.st_mtime_ns))
    os.rename(tmp, final)
    return "copied", ""


def mkdir_private(d: Path) -> None:
    """mkdir -p with every level it creates at 0700 (makedirs sets the mode of the last one only)."""
    if d.is_dir():
        return
    mkdir_private(d.parent)
    try:
        os.mkdir(d, 0o700)
    except FileExistsError:
        pass


def _write_all(fd: int, b: bytes) -> None:
    view = memoryview(b)
    while view:
        view = view[os.write(fd, view):]


def _unlink_quiet(p: Path) -> None:
    try:
        os.unlink(p)
    except OSError:
        pass


def cmd_archive(s: Settings, ops: Ops, dry_run: bool) -> int:
    now = ops.now()
    cutoff_ns = int((now - dt.timedelta(days=s.age_days)).timestamp() * 1e9)
    src = s.recordings
    if dry_run:
        prob = dest_problem(s, ops, probe=False)
        ops.log(f"dry run: destination {s.dest}: {prob or 'usable'} (mount required: {s.require_mount})")
        recs, skipped = scan_recordings(src) if src.is_dir() else ([], [(str(src), "not a directory")])
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
        ops.notify("Kronk backup: recordings archive started",
                   f"Moving recordings older than {s.age_days} days from {src} to {s.dest}/recordings.")
        rc = _archive(s, ops, run, cutoff_ns)
    except Exception as e:  # noqa: BLE001
        run.update(result="failed", detail=f"{e.__class__.__name__}: {e}")
        rc = 1
    run["finished"] = iso(ops.now())
    if src.is_dir():
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
    os.close(fd)
    return rc


def _archive(s: Settings, ops: Ops, run: dict, cutoff_ns: int) -> int:
    prob = dest_problem(s, ops)
    if prob:
        run["detail"] = prob
        return 2
    src = s.recordings
    if not src.is_dir():
        run["detail"] = f"recordings directory {src} does not exist"
        return 2
    root = s.dest / "recordings"
    root.mkdir(mode=0o700, exist_ok=True)
    recs, skipped = scan_recordings(src)
    problems: list[str] = [f"{show(p)}: {why}" for p, why in skipped]
    left: list[str] = []
    to_delete: list[Rec] = []
    moved_bytes = written = 0
    for rec in recs:
        if rec.mtime_ns >= cutoff_ns:
            continue
        month = dt.datetime.fromtimestamp(rec.mtime_ns / 1e9).strftime("%Y-%m")
        ddir = root / rec.rel[0] / rec.rel[1] / month / rec.rel[2]
        try:
            outcome, detail = copy_verified(src, rec, ddir)
        except OSError as e:
            outcome, detail = "error", f"{e.strerror or e}"
        if outcome == "copied":
            written += rec.size
            to_delete.append(rec)
        elif outcome == "same":
            to_delete.append(rec)
        elif outcome == "changed":
            left.append(f"{show(rec.path)}: {detail}")
        else:
            problems.append(f"{show(rec.path)}: {detail}")
            if outcome == "full":
                break
    # Local deletes, by literal path, only for files verified on the destination.
    deleted = []
    if to_delete:
        runs = sorted({"/".join(r.rel[:3]) for r in to_delete})
        r = ops.remove_recordings(src, [x.path for x in to_delete], runs)
        for rec in to_delete:
            if os.path.lexists(src / rec.path):
                problems.append(f"{show(rec.path)}: local delete failed ({r.err[:120] or 'still there'}); "
                                "the copy on the destination is verified, the next run deletes it")
            else:
                deleted.append(rec)
                moved_bytes += rec.size
    run.update(moved=len(deleted), moved_bytes=moved_bytes, written_bytes=written,
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
    prob = dest_problem(s, ops, probe=False)
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
        if last.get("result") in ("failed", "declined"):
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


def main(argv=None, ops: Ops | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backups.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("config", "archive", "check"):
        p = sub.add_parser(name)
        p.add_argument("--dest", help="destination directory (default KRONK_BACKUP_DIR or /mnt/nas-backup)")
        p.add_argument("--require-mount", action="store_true", help="the destination must be a mount point")
        if name != "check":
            p.add_argument("--dry-run", action="store_true", help="list what would happen; write and send nothing")
        if name == "archive":
            p.add_argument("--src", help="recordings directory (default KRONK_RECORDINGS_DIR or ~/kronk-voice-recordings)")
    sub.add_parser("status", help="print the status file (reads only)")
    a = ap.parse_args(argv)
    ops = ops or Ops(log=lambda m: print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {m}", flush=True))
    s = Settings.load(a)
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


if __name__ == "__main__":
    sys.exit(main())
