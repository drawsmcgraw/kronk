"""Sprint M16: the config backup, the recordings archive, the stale check,
their units and GET /api/backups. Everything runs in tmp_path with a fake
Ops: no docker, no git, no socket, no notice leaves the test."""
import datetime as dt
import errno
import fcntl
import importlib.util
import io
import json
import os
import re
import stat
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("m16_backups", ROOT / "scripts" / "backups.py")
B = importlib.util.module_from_spec(spec)
sys.modules["m16_backups"] = B
spec.loader.exec_module(B)

NOW = dt.datetime(2026, 12, 10, 1, 30, tzinfo=dt.timezone(dt.timedelta(hours=-5)))
SECRET = "HA_TOKEN=not-a-real-token-but-treat-it-as-one-7f3c"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Criterion 16: these tests reach no network. A connect fails the test."""
    import socket

    def refuse(*a, **k):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setenv("KRONK_BACKUP_NOTIFY_CMD", "/bin/false")     # belt and braces: never notify.sh
    monkeypatch.setenv("KRONK_ARCHIVE_DELETE", "direct")           # the real delete script, the host's sh


def tar_of(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            if data is None:
                ti.type, ti.mode = tarfile.DIRTYPE, 0o755
                t.addfile(ti)
                continue
            ti.size = len(data)
            ti.mode = 0o644
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def wal_pair(main_rows=("t-main-1", "t-main-2"), wal_rows=("t-wal-1",)) -> tuple[bytes, bytes]:
    """A tiny auth.db as Music Assistant leaves it while it runs: WAL mode,
    some token rows checkpointed into the main file, the newest only in the
    write-ahead log. Returns (main file, WAL) bytes, read while it is open."""
    import sqlite3
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "auth.db"
        con = sqlite3.connect(db)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute("CREATE TABLE auth_tokens (token_id TEXT PRIMARY KEY, token_hash TEXT)")
        con.executemany("INSERT INTO auth_tokens VALUES (?, ?)", [(r, "h-" + r) for r in main_rows])
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.executemany("INSERT INTO auth_tokens VALUES (?, ?)", [(r, "h-" + r) for r in wal_rows])
        con.commit()
        main, wal = db.read_bytes(), (Path(td) / "auth.db-wal").read_bytes()
        con.close()
    return main, wal


def token_ids(blob: bytes, wal: bytes | None = None) -> list[str]:
    """The token ids a database file holds (opened on disk, as a restore
    would: a WAL-format image cannot be opened in memory), checked first."""
    import sqlite3
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "auth.db"
        db.write_bytes(blob)
        if wal is not None:
            (Path(td) / "auth.db-wal").write_bytes(wal)
        con = sqlite3.connect(db)
        try:
            assert con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            return sorted(r[0] for r in con.execute("SELECT token_id FROM auth_tokens"))
        finally:
            con.close()


MA_AUTH = wal_pair()


class FakeOps(B.Ops):
    def __init__(self, now=NOW):
        self.t = now
        self.notices: list[tuple[str, str]] = []
        self.logs: list[str] = []
        self.log = self.logs.append
        self.mounted: set[str] = set()
        self.notify_ok = True
        self.no_notify = False
        self.ha = tar_of({"./.storage/": None, "./.storage/core.entity_registry": b'{"data":{"entities":[]}}',
                          "./.storage/assist_pipeline.pipelines": b'{"data":{"items":[]}}',
                          "./configuration.yaml": b"homeassistant:\n"})
        self.ha_rc = 0
        self.ha_names = [".storage", "configuration.yaml", "home-assistant_v2.db", "tts"]
        self.keep_local: set[str] = set()     # recordings whose local delete "fails" (the container never ran)
        self.removed: list[list[str]] = []
        self.commands: list[list[str]] = []
        self.bundle = b"# v2 git bundle\n0123abc HEAD\n\nPACK"
        self.heads = b"0123abc HEAD\n0123abc refs/heads/0123abc\n0123abc refs/tags/sprint-M16\n"
        self.local_tags = {"sprint-M16"}
        self.unpushed = 3
        self.leftover_ids: list[str] = []        # helper containers "docker ps" reports in state created
        self.running_helpers: list[str] = []     # "<id> <name>" lines of running labelled helpers
        # Music Assistant's /data (M16b): what ls shows, what cat reads, the playlists tar.
        self.ma_names = ["auth.db", "auth.db-wal", "library.db", "playlists", "settings.json"]
        self.ma_files = {"/data/settings.json": b'{"providers": {}}',
                         "/data/auth.db": MA_AUTH[0], "/data/auth.db-wal": MA_AUTH[1]}
        self.ma_playlists = tar_of({"playlists": None, "playlists/Road Trip.m3u": b"#EXTM3U\n/media/nas/a.flac\n"})
        self.ma_reads: list[str] = []

    def run(self, argv, timeout=300, stdin=None):
        self.commands.append(argv)
        if argv[0] == "git":
            if "rev-list" in argv:
                assert argv[-1] in ("origin/0123abc..HEAD", "--remotes")   # no upstream in the fake: origin/<branch>
                return B.Result(0, f"{self.unpushed}\n".encode(), "")
            if "list-heads" in argv:
                return B.Result(0, self.heads, "")
            if "--verify" in argv and argv[-1].startswith("refs/tags/") and "^{commit}" not in argv[-1]:
                ok = argv[-1][len("refs/tags/"):] in self.local_tags
                return B.Result(0 if ok else 1, b"0123abc\n" if ok else b"", "")
            if "bundle" in argv:
                assert argv[-2:] == ["--not", "--remotes"]
                Path(argv[argv.index("--quiet") + 1]).write_bytes(self.bundle)
                return B.Result(0, b"", "")
            if "--symbolic-full-name" in argv:
                return B.Result(128, b"", "fatal: no upstream configured")
            return B.Result(0, b"0123abc\n" if "rev-parse" in argv else b"", "")
        if argv[:2] == ["docker", "ps"] and f"label={B.HELPER_LABEL}" in argv and "status=running" in argv:
            return B.Result(0, "".join(f"{i}\n" for i in self.running_helpers).encode(), "")
        if argv[:2] == ["docker", "ps"] and f"label={B.HELPER_LABEL}" in argv:
            return B.Result(0, "".join(f"{i}\n" for i in self.leftover_ids).encode(), "")
        if argv[:2] == ["docker", "rm"]:
            return B.Result(0, b"", "")
        if argv[:2] in (["docker", "ps"], ["docker", "inspect"]):
            return B.Result(0, b"kronk-orchestrator-1\t/home/x/kronk-releases/sprint-M16\n", "")
        if argv[:2] == ["sh", "-c"] and argv[2] == B.DELETE_SCRIPT:
            return super().run(argv, timeout, stdin)        # the real delete script on the test's own tree
        raise AssertionError(f"unexpected command in a test: {argv}")

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += dt.timedelta(seconds=s)

    def ismount(self, p):
        return str(p) in self.mounted

    def notify(self, title, body):
        self.notices.append((title, body))
        return self.notify_ok

    def container_ls(self, container, path):
        if container == "music-assistant-server":
            assert path == "/data"
            return B.Result(0, "\n".join(self.ma_names).encode() + b"\n", "")
        if self.ha_rc:
            return B.Result(self.ha_rc, b"", "No such container: homeassistant")
        return B.Result(0, "\n".join(self.ha_names).encode() + b"\n", "")

    def container_tar(self, container, cwd, names):
        if container == "music-assistant-server":
            assert (cwd, names) == ("/data", ["playlists"])
            return B.Result(0, self.ma_playlists, "")
        self.ha_tar_names = names
        return B.Result(self.ha_rc, self.ha if self.ha_rc == 0 else b"", "" if self.ha_rc == 0 else "No such container: homeassistant")

    def container_cat(self, container, path):
        assert container == "music-assistant-server"
        self.ma_reads.append(path)
        if path in self.ma_files:
            return B.Result(0, self.ma_files[path], "")
        return B.Result(1, b"", f"cat: {path}: No such file or directory")

    def helper_tar(self, hostdir):
        files = {f"./{p.relative_to(hostdir)}": p.read_bytes() for p in hostdir.rglob("*") if p.is_file()}
        return B.Result(0, tar_of(files), "")

    def remove_recordings(self, src, dst, items):
        self.removed.append([v.rec.path for v in items])
        if any(v.rec.path in self.keep_local for v in items):
            return {}, "No such image: alpine:3.20"        # the helper container did not run
        return super().remove_recordings(src, dst, items)


@pytest.fixture
def house(tmp_path, monkeypatch):
    """A repo, a home, /etc, a destination and a state directory, all in tmp_path."""
    home = tmp_path / "home"
    repo = home / "git-repos" / "kronk"
    for d in ("data", "searxng", "secrets/mm", "ops", "scripts/lib", "docs/runbooks", "docs/features"):
        (repo / d).mkdir(parents=True)
    (repo / "docs" / "runbooks" / "restore.md").write_text("# Runbook\n")
    (repo / "docs" / "features" / "backups.md").write_text("# Backups\n")
    (repo / ".env").write_text(SECRET + "\n")
    os.chmod(repo / ".env", 0o600)
    (repo / ".env.dev").write_text("MUSIC_DRY_RUN=1\n")
    (repo / ".env.telemetry").write_text("GF_SECURITY_ADMIN_PASSWORD=x\n")
    (repo / "searxng" / "settings.yml").write_text("server: {}\n")
    (repo / "secrets" / "empty").mkdir()
    (repo / "secrets" / "mm" / "kronk-mm-update").write_text("KEY\n")
    (repo / "data" / "music_taste.yaml").write_text("love: []\n")
    (repo / "data" / "kronk_state.md").write_text("# state\n")
    (repo / "data" / "mm_known_hosts").write_text("host key\n")
    (repo / "data" / "yt-music-cookie").write_text("cookie\n")
    import sqlite3
    for db in ("solar.db", "music_taste.db"):
        c = sqlite3.connect(repo / "data" / db)
        c.execute("create table t (x)")
        c.execute("insert into t values (42)")
        c.commit()
        c.close()
    (repo / "docker-compose.yml").write_text("services:\n  nginx:\n    image: nginx:1.29-alpine\n")
    live = home / "git-repos" / "kronk-config"
    for inst in ("prod", "dev"):
        (live / inst / ".git").mkdir(parents=True)
        (live / inst / "tuning.yaml").write_text(f"instance: {inst}\n")
        (live / inst / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    units = home / ".config" / "systemd" / "user"
    (units / "timers.target.wants").mkdir(parents=True)
    (units / "llama-x.service").write_text("[Service]\nExecStart=/bin/llama -m %h/models/x.gguf\n")
    (units / "timers.target.wants" / "x.timer").symlink_to(units / "x.timer")
    work = home / ".local" / "state" / "kronk-work"
    work.mkdir(parents=True)
    monkeypatch.delenv("KRONK_WORK_DB")            # conftest's scratch path: the house's HOME path here
    c = sqlite3.connect(work / "work.db")
    c.execute("create table item (id)")
    c.execute("insert into item values ('M33')")
    c.commit()
    c.close()
    (home / ".config" / "kronk").mkdir()
    (home / ".config" / "kronk" / "upgrade.env").write_text("KRONK_UPGRADE_BRANCH=refactor\n")
    (home / "models").mkdir()
    (home / "models" / "x.gguf").write_bytes(b"GGUF" * 1000)
    (home / "models" / "PROVENANCE.sha256").write_text("abc  x.gguf\n")
    etc = tmp_path / "etc"
    (etc / "default").mkdir(parents=True)
    (etc / "default" / "grub").write_text('GRUB_CMDLINE_LINUX_DEFAULT="quiet ttm.pages_limit=1"\n')
    (etc / "kronk").mkdir()
    (etc / "kronk" / "nas-backup.cred").write_text("username=u\npassword=p\n")
    (etc / "fstab").write_text("UUID=x / ext4 defaults 0 1\n# the NAS\n//atlas.local/music /mnt/nas-music cifs ro,credentials=/etc/kronk/nas-music.cred 0 0\n")
    (etc / "os-release").write_text('PRETTY_NAME="Test OS"\n')
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "cmdline").write_text("BOOT_IMAGE=/vmlinuz ttm.pages_limit=1\n")
    dest = tmp_path / "dest"
    dest.mkdir()
    (tmp_path / "tmp").mkdir()
    state = repo / "data" / "backups"
    monkeypatch.setattr(B, "HOME", home)
    s = B.Settings(dest=dest, require_mount=False, state=state, repo=repo, live_config=live,
                   upgrade_state=repo / "data" / "upgrades", model_dirs=(str(home / "models"),),
                   recordings=home / "kronk-voice-recordings", etc=etc, proc=proc, lock_wait_min=2,
                   tmp=tmp_path / "tmp")
    return s


def tar_names(p: Path) -> list[str]:
    with tarfile.open(p, "r:gz") as t:
        return t.getnames()


def read_member(p: Path, suffix: str) -> bytes:
    with tarfile.open(p, "r:gz") as t:
        for m in t:
            if m.name.endswith(suffix):
                return t.extractfile(m).read()
    raise KeyError(suffix)


def every_file(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


# ═══ Config backup ═════════════════════════════════════════════════════════

def test_one_dated_tar_mode_0600_with_every_item_and_a_manifest(house):
    ops = FakeOps()
    assert B.cmd_config(house, ops, dry_run=False) == 0
    files = list((house.dest / "config").iterdir())
    assert [f.name for f in files] == ["kronk-config-2026-12-10.tar.gz"]
    p = files[0]
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    names = tar_names(p)
    top = "kronk-config-2026-12-10/"
    for want in ("repo/.env", "repo/.env.dev", "repo/.env.telemetry", "repo/searxng/settings.yml",
                 "repo/secrets/mm/kronk-mm-update", "repo/data/music_taste.yaml", "repo/data/solar.db",
                 "repo/data/music_taste.db", "live-config/prod/tuning.yaml", "live-config/dev/.git/HEAD",
                 "systemd-user/llama-x.service", "systemd-user/timers.target.wants/x.timer",
                 "config-kronk/upgrade.env", "etc/default/grub", "etc/kronk/nas-backup.cred",
                 "ha/config/.storage/core.entity_registry", "ha/config/configuration.yaml",
                 "ma/settings.json", "repo-state.txt", "images.txt", "models.tsv", "host-facts.txt", "MANIFEST",
                 "repo-unpushed.bundle"):
        assert top + want in names, want
    assert names[0] == "kronk-config-2026-12-10" and names[-1] == top + "MANIFEST"
    # The live .env is byte-identical inside; the SQLite copies are real databases.
    assert read_member(p, "repo/.env") == (house.repo / ".env").read_bytes()
    assert read_member(p, "repo/data/solar.db").startswith(b"SQLite format 3")
    with tarfile.open(p, "r:gz") as t:
        assert t.getmember("kronk-config-2026-12-10").isdir() and t.getmember("kronk-config-2026-12-10").mode == 0o700
        link = t.getmember(top + "systemd-user/timers.target.wants/x.timer")
        assert link.issym()       # stored as a link, never followed
        assert t.getmember(top + "live-config/prod/.git").isdir()      # directories kept, empty ones too
        assert t.getmember(top + "ha/config/.storage").isdir()
    man = read_member(p, "MANIFEST").decode()
    env_sha = __import__("hashlib").sha256((house.repo / ".env").read_bytes()).hexdigest()
    assert f"{env_sha}\t{len(SECRET) + 1}\tfile\trepo/.env\t<- {house.repo / '.env'}" in man
    assert "sqlite backup\trepo/data/solar.db" in man
    assert "docker exec homeassistant tar\tha/config/.storage/core.entity_registry" in man
    assert "ABSENT\t" + str(house.repo / "ops" / "hosts.json") in man
    assert "directory\t0\tdirectory\trepo/secrets/empty\t" in man
    assert "ABSENT\t" + str(B.HOME / "kronk-voice-labels") in man
    state = read_member(p, "repo-state.txt").decode()
    assert "not pushed: 3 commits ahead of origin/0123abc" in state
    assert "commit: 0123abc" in state and "pushed:" in state and "production tag: sprint-M16 (1 containers)" in state
    assert "unpushed bundle: repo-unpushed.bundle: 3 commit(s) no remote-tracking branch has" in state
    assert ("production tag sprint-M16 on the remote: no: its commit 0123abc is on no remote-tracking branch"
            in state)
    assert read_member(p, "repo-unpushed.bundle") == ops.bundle
    assert "docker-compose.yml\tnginx:1.29-alpine" in read_member(p, "images.txt").decode()
    models = read_member(p, "models.tsv").decode()
    gguf = B.HOME / "models" / "x.gguf"
    assert __import__("hashlib").sha256(gguf.read_bytes()).hexdigest() + f"\t4000\t" in models
    assert "llama-x.service" in models          # named by the unit
    assert "models" + str(B.HOME / "models" / "PROVENANCE.sha256") in "\n".join(names)
    facts = read_member(p, "host-facts.txt").decode()
    assert "ttm.pages_limit=1" in facts and "//atlas.local/music /mnt/nas-music cifs" in facts and "# the NAS" in facts
    assert "UUID=x" not in facts


def test_m33_the_work_database_is_in_the_tar_as_a_live_sqlite_copy(house):
    ops = FakeOps()
    assert B.cmd_config(house, ops, dry_run=False) == 0
    p = next((house.dest / "config").iterdir())
    data = read_member(p, "work-state/work.db")
    assert data.startswith(b"SQLite format 3")
    man = read_member(p, "MANIFEST").decode()
    assert "sqlite backup\twork-state/work.db\t<- " + str(B.HOME / ".local" / "state" / "kronk-work" / "work.db") in man


def test_m33_the_work_database_member_follows_kronk_work_db(house, tmp_path, monkeypatch):
    import sqlite3
    other = tmp_path / "scratch-work.db"
    c = sqlite3.connect(other)
    c.execute("create table item (id)")
    c.execute("insert into item values ('SCRATCH')")
    c.commit()
    c.close()
    monkeypatch.setenv("KRONK_WORK_DB", str(other))
    assert B.cmd_config(house, FakeOps(), dry_run=False) == 0
    p = next((house.dest / "config").iterdir())
    got = tmp_path / "got.db"
    got.write_bytes(read_member(p, "work-state/work.db"))
    c = sqlite3.connect(got)
    assert c.execute("select id from item").fetchall() == [("SCRATCH",)]
    c.close()


def test_m33_a_missing_work_database_is_said_and_the_run_stays_ok(house):
    (B.HOME / ".local" / "state" / "kronk-work" / "work.db").unlink()
    ops = FakeOps()
    assert B.cmd_config(house, ops, dry_run=False) == 0
    p = next((house.dest / "config").iterdir())
    man = read_member(p, "MANIFEST").decode()
    assert "UNREADABLE\t" + str(B.HOME / ".local" / "state" / "kronk-work" / "work.db") in man
    assert ops.notices[-1][0] == "Kronk backup: config backup OK"


def test_m33_restored_from_the_tar_the_work_database_passes_and_resumes_the_same(house, tmp_path, monkeypatch):
    """Criterion 8: a real work.py database, written and then read by the
    monthly tar; restored with any -wal and -shm removed first, it passes
    integrity_check, and resume reads the same but for the log's tail."""
    import contextlib
    import sqlite3
    import subprocess
    spec = importlib.util.spec_from_file_location("m16_work_tool", Path(B.__file__).parent / "work.py")
    wk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wk)
    tree = tmp_path / "wtree"
    tree.mkdir()
    e = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", HOME=str(tmp_path))
    for argv in (["init", "-q", "-b", "main"], ["config", "user.name", "F"], ["config", "user.email", "f@example.invalid"],
                 ["commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", *argv], cwd=tree, env=e, check=True, capture_output=True)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("KRONK_AGENT_STATE", str(tmp_path / "agents"))
    live = B.HOME / ".local" / "state" / "kronk-work" / "work.db"
    live.unlink()

    def tool(db, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = wk.main(["--db", str(db), "--repo", str(tree), *argv], out=out)
        assert rc == 0, err.getvalue()
        return out.getvalue()
    tool(live, "init")
    tool(live, "add", "--title", "queued")
    tool(live, "add", "--title", "other")
    tool(live, "matter", "add", "decision", "--owner", "drew", "--text", "Daily copies?")
    held = sqlite3.connect(live)                      # a reader holds the WAL open while the tar is made
    held.execute("BEGIN")
    held.execute("SELECT * FROM item").fetchall()
    try:
        assert B.cmd_config(house, FakeOps(), dry_run=False) == 0
    finally:
        held.close()
    p = next((house.dest / "config").iterdir())
    restored = tmp_path / "restored" / "work.db"
    restored.parent.mkdir()
    restored.write_bytes(read_member(p, "work-state/work.db"))
    for sfx in ("-wal", "-shm"):
        Path(str(restored) + sfx).unlink(missing_ok=True)
    c = sqlite3.connect(restored)
    assert c.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    c.close()
    tool(restored, "dump")                              # the runbook's step: the dump is not in the tar
    tool(live, "note", "M1", "--next", "a write after the tar")
    # (fix lines name their --db since M33 fix round 1: the path is not compared)
    head = lambda db: tool(db, "resume").split("\n", 1)[1].split("== Last five writes")[0].replace(str(db), "<db>")
    a, b = head(live), head(restored)
    assert a.replace("next step: a write after the tar", "next step: -") == b
    assert "Daily copies?" in b


def test_no_secret_reaches_a_log_line_a_notice_or_the_status_file(house):
    ops = FakeOps()
    B.cmd_config(house, ops, dry_run=False)
    B.cmd_config(house, ops, dry_run=True)
    text = "\n".join(ops.logs) + "\n".join(t + b for t, b in ops.notices) + (house.state / "status.json").read_text()
    for secret in (SECRET, "password=p", "KEY", "GF_SECURITY_ADMIN_PASSWORD"):
        assert secret not in text


def test_what_cannot_be_read_is_listed_in_the_manifest_and_the_notice(house):
    """An optional item unreadable: still OK, and said."""
    ops = FakeOps()
    (house.repo / "secrets" / "mm" / "kronk-mm-update").chmod(0o000)
    try:
        assert B.cmd_config(house, ops, dry_run=False) == 0
    finally:
        (house.repo / "secrets" / "mm" / "kronk-mm-update").chmod(0o600)
    p = next((house.dest / "config").iterdir())
    man = read_member(p, "MANIFEST").decode()
    if os.geteuid() != 0:
        assert "UNREADABLE\t" + str(house.repo / "secrets" / "mm" / "kronk-mm-update") in man
        title, body = ops.notices[-1]
        assert title == "Kronk backup: config backup OK" and "NOT READ (1)" in body and "kronk-mm-update" in body
        # Finding 5: the unpushed sentence survives a gap (gaps += , not gaps =).
        assert "Repo: 3 commits not pushed (the tar holds them as repo-unpushed.bundle)." in body
        st = json.loads((house.state / "status.json").read_text())
        assert st["config"]["last"]["unreadable"] and st["config"]["last"]["result"] == "ok"


@pytest.mark.parametrize("break_it", ["ha", "ma", "env", "live-prod", "live-dev", "units", "env-missing",
                                      "solar-corrupt", "taste-db-missing", "ma-auth-missing", "ma-auth-corrupt"])
def test_a_missing_required_item_makes_the_tar_incomplete(house, monkeypatch, break_it):
    """Finding 5: HA, MA, .env, the live config directories, the units."""
    ops = FakeOps()
    if break_it == "ha":
        ops.ha_rc = 1
    elif break_it == "ma":
        monkeypatch.setattr(ops, "container_cat", lambda c, p: B.Result(1, b"", "No such container"))
    elif break_it == "ma-auth-missing":                  # M16b criterion 2: auth.db is required
        del ops.ma_files["/data/auth.db"], ops.ma_files["/data/auth.db-wal"]
    elif break_it == "ma-auth-corrupt":
        ops.ma_files["/data/auth.db"] = b"not a database" * 100
    elif break_it == "env":
        (house.repo / ".env").chmod(0o000)
    elif break_it == "env-missing":
        (house.repo / ".env").unlink()
    elif break_it == "live-prod":
        (house.live_config / "prod" / "tuning.yaml").chmod(0o000)
    elif break_it == "live-dev":
        import shutil
        shutil.rmtree(house.live_config / "dev")
    elif break_it == "units":
        (B.HOME / ".config" / "systemd" / "user" / "llama-x.service").chmod(0o000)
    elif break_it == "solar-corrupt":                    # second e2e, note 9 (coordinator ruling): required
        (house.repo / "data" / "solar.db").write_bytes(b"not a database" * 100)
    elif break_it == "taste-db-missing":
        (house.repo / "data" / "music_taste.db").unlink()
    if os.geteuid() == 0 and break_it in ("env", "live-prod", "units"):
        pytest.skip("root reads a 0000 file")
    cfg = house.dest / "config"
    cfg.mkdir()
    complete = [f"kronk-config-2025-{m:02d}-10.tar.gz" for m in range(1, 13)] + ["kronk-config-2026-01-10.tar.gz"]
    for n in complete:
        (cfg / n).write_bytes(b"a complete tar")
    try:
        assert B.cmd_config(house, ops, dry_run=False) == 4
    finally:
        for f in (house.repo / ".env", house.live_config / "prod" / "tuning.yaml",
                  B.HOME / ".config" / "systemd" / "user" / "llama-x.service"):
            if f.exists():
                f.chmod(0o600)
    names = sorted(f.name for f in cfg.iterdir())
    assert names == sorted(complete + ["kronk-config-2026-12-10.incomplete.tar.gz"])   # retention never ran
    title, body = ops.notices[-1]
    assert title == "Kronk backup: config backup INCOMPLETE"
    # MA down loses both of its required items: settings.json and auth.db (M16b).
    assert f"MISSING REQUIRED ({2 if break_it == 'ma' else 1})" in body and "does not count as a backup" in body
    if break_it.startswith("ma-auth"):
        assert "music-assistant-server:/data/auth.db" in body
    assert "Repo: 3 commits not pushed" in body
    st = json.loads((house.state / "status.json").read_text())
    assert st["config"]["last"]["result"] == "incomplete" and "last_ok_at" not in st["config"]
    p = cfg / "kronk-config-2026-12-10.incomplete.tar.gz"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    man = read_member(p, "MANIFEST").decode()
    assert "\nREQUIRED\t" in man
    if break_it == "env-missing":      # mutant n06: a missing required file is a gap, not "absent"
        assert "ABSENT\t" + str(house.repo / ".env") not in man and "missing: no such file" in man
    # The stale check does not count it as a backup.
    assert B.newest_config(house)[0] == "kronk-config-2026-01-10.tar.gz"
    assert any("the last config backup incomplete" in w for w in B.stale_warnings(house, FakeOps(), st))


def test_an_incomplete_tar_never_counts_toward_retention_and_a_later_good_run_keeps_twelve(house):
    cfg = house.dest / "config"
    cfg.mkdir()
    for m in range(1, 13):
        (cfg / f"kronk-config-2025-{m:02d}-10.tar.gz").write_bytes(b"complete")
    for m in range(1, 4):
        (cfg / f"kronk-config-2026-0{m}-11.incomplete.tar.gz").write_bytes(b"incomplete")
    assert B.cmd_config(house, FakeOps(), dry_run=False) == 0
    names = sorted(f.name for f in cfg.iterdir())
    assert "kronk-config-2025-01-10.tar.gz" not in names           # 12 complete kept: 11 old + today
    assert sum(1 for n in names if B.CONFIG_RE.fullmatch(n)) == 12
    assert sum(1 for n in names if B.INCOMPLETE_RE.fullmatch(n)) == 3


def test_start_and_end_notices_with_sizes_and_totals(house):
    ops = FakeOps()
    (house.dest / "recordings" / "d" / "p").mkdir(parents=True)
    (house.dest / "recordings" / "d" / "p" / "old.wav").write_bytes(b"x" * 2_500_000)
    B.cmd_config(house, ops, dry_run=False)
    assert ops.notices[0] == ("Kronk backup: config backup started",
                              f"Writing the 2026-12-10 config backup to {house.dest}/config.")
    title, body = ops.notices[1]
    assert title == "Kronk backup: config backup OK"
    assert re.match(r"kronk-config-2026-12-10\.tar\.gz, \d+\.\d KB, \d+ entries\. Repo: 3 commits not pushed "
                    r"\(the tar holds them as repo-unpushed\.bundle\)\.", body), body
    assert re.search(r"On the destination: config backups \d+\.\d KB \(1 file\), recordings archive 2\.5 MB "
                     r"\(1 file\), together 2\.5 MB\.$", body), body


def test_a_notice_that_fails_does_not_fail_the_job(house):
    ops = FakeOps()
    ops.notify_ok = False
    assert B.cmd_config(house, ops, dry_run=False) == 0
    st = json.loads((house.state / "status.json").read_text())
    assert st["config"]["last"]["result"] == "ok" and st["config"]["last"]["notice_sent"] is False


def test_the_real_notifier_runs_the_stub_command_with_tag_title_body(tmp_path, monkeypatch):
    out = tmp_path / "notices.txt"
    stub = tmp_path / "stub.sh"
    stub.write_text(f'#!/bin/sh\nprintf "%s|%s|%s\\n" "$1" "$2" "$3" >> {out}\n')
    stub.chmod(0o755)
    monkeypatch.setenv("KRONK_BACKUP_NOTIFY_CMD", str(stub))
    assert B.Ops(log=lambda m: None).notify("Kronk backup: config backup OK", "a b; c") is True
    assert out.read_text() == "kronk-backup|Kronk backup: config backup OK|a b; c\n"
    monkeypatch.setenv("KRONK_BACKUP_NOTIFY_CMD", str(tmp_path / "missing"))
    logs = []
    assert B.Ops(log=logs.append).notify("t", "b") is False and "notice not sent" in logs[0]


def test_written_to_a_temporary_name_then_verified_then_renamed(house, monkeypatch):
    ops = FakeOps()
    seen = {}
    real = B.verify_tar

    def spy(p, sha, n):
        seen["name"] = p.name
        seen["final_exists"] = any(f.name.startswith("kronk-config") for f in p.parent.iterdir())
        return real(p, sha, n)
    monkeypatch.setattr(B, "verify_tar", spy)
    B.cmd_config(house, ops, dry_run=False)
    assert seen == {"name": ".kronk-config-2026-12-10.tar.gz.tmp", "final_exists": False}


def test_a_failed_verify_leaves_no_file_and_says_so(house, monkeypatch):
    ops = FakeOps()
    monkeypatch.setattr(B, "verify_tar", lambda p, sha, n: "read back differs (test)")
    assert B.cmd_config(house, ops, dry_run=False) == 1
    assert list((house.dest / "config").iterdir()) == []
    assert ops.notices[-1][0] == "Kronk backup: config backup FAILED"
    assert "read back differs (test); the temporary file was removed" in ops.notices[-1][1]


def test_verify_reads_the_tar_to_the_end(tmp_path):
    p = tmp_path / "t.tar.gz"
    blob, sha = B.build_tar("top", [B.Member("a", "s", "file", b"x" * 100_000)])
    p.write_bytes(blob)
    assert B.verify_tar(p, sha, 2) is None          # the top directory and the member
    assert "members" in B.verify_tar(p, sha, 3)
    data = p.read_bytes()
    p.write_bytes(data[: len(data) // 2])
    assert "read back differs" in B.verify_tar(p, sha, 2)
    assert "cannot read it to the end" in B.verify_tar(p, B.sha256_file(p, False), 2)


def test_the_read_back_drops_the_page_cache_first(tmp_path, monkeypatch):
    """Finding 12: posix_fadvise DONTNEED before the first read of the read-back."""
    calls = []
    real_read = os.read
    monkeypatch.setattr(B.os, "posix_fadvise", lambda fd, a, b, adv: calls.append(("fadvise", adv)))
    monkeypatch.setattr(B.os, "read", lambda fd, n: (calls.append(("read",)), real_read(fd, n))[1])
    p = tmp_path / "f"
    p.write_bytes(b"abc")
    B.sha256_file(p, drop_cache=True)
    assert calls[0] == ("fadvise", os.POSIX_FADV_DONTNEED) and ("read",) in calls


def test_an_interrupted_run_leaves_only_a_temporary_name_which_the_next_run_removes(house, monkeypatch):
    ops = FakeOps()

    def killed(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(B, "verify_tar", killed)
    with pytest.raises(KeyboardInterrupt):
        B._config(house, ops, {"at": "x"}, "2026-12-10", True)
    # The kill cleaned its own file here; a SIGKILL would not: leave one by hand.
    cfg = house.dest / "config"
    (cfg / ".kronk-config-2026-11-10.tar.gz.tmp").write_bytes(b"half")
    (cfg / ".kronk-config-notes.tmp").write_bytes(b"not ours")
    (cfg / ".kronk-config-2026-11-09.tar.gz.tmp").mkdir()          # a directory: not ours
    assert not any(CONFIG_FINAL(f.name) for f in cfg.iterdir())
    monkeypatch.undo()
    monkeypatch.setattr(B, "HOME", house.repo.parent.parent)
    assert B.cmd_config(house, FakeOps(), dry_run=False) == 0
    assert sorted(f.name for f in cfg.iterdir()) == [".kronk-config-2026-11-09.tar.gz.tmp", ".kronk-config-notes.tmp",
                                                     "kronk-config-2026-12-10.tar.gz"]


def CONFIG_FINAL(name):
    return B.CONFIG_RE.fullmatch(name) is not None


def test_a_second_run_the_same_day_never_overwrites(house):
    B.cmd_config(house, FakeOps(), dry_run=False)
    B.cmd_config(house, FakeOps(), dry_run=False)
    assert sorted(f.name for f in (house.dest / "config").iterdir()) == [
        "kronk-config-2026-12-10.2.tar.gz", "kronk-config-2026-12-10.tar.gz"]


def test_retention_keeps_the_newest_n_of_its_own_files_only(house):
    cfg = house.dest / "config"
    cfg.mkdir()
    old = [f"kronk-config-2025-{m:02d}-10.tar.gz" for m in range(1, 13)] + ["kronk-config-2026-11-10.tar.gz"]
    for n in old:
        (cfg / n).write_bytes(b"old")
    foreign = ["notes.txt", "kronk-config-2020-01-01.tar.gz.keep", "kronk-config-latest.tar.gz"]
    for n in foreign:
        (cfg / n).write_bytes(b"mine")
    (cfg / "kronk-config-2019-01-01.tar.gz").mkdir()          # a directory with the name: never removed
    (house.dest / "kronk-config-2018-01-01.tar.gz").write_bytes(b"outside config/")
    ops = FakeOps()
    assert B.cmd_config(house, ops, dry_run=False) == 0
    left = sorted(f.name for f in cfg.iterdir())
    kept = sorted(n for n in left if CONFIG_FINAL(n) and (cfg / n).is_file())
    assert len(kept) == 12 and kept[-1] == "kronk-config-2026-12-10.tar.gz"
    assert "kronk-config-2025-01-10.tar.gz" not in left and "kronk-config-2025-02-10.tar.gz" not in left
    for n in foreign + ["kronk-config-2019-01-01.tar.gz"]:
        assert n in left
    assert (house.dest / "kronk-config-2018-01-01.tar.gz").exists()
    assert "Removed by retention: kronk-config-2025-01-10.tar.gz, kronk-config-2025-02-10.tar.gz." in ops.notices[-1][1]


def test_retention_never_runs_when_the_run_failed(house, monkeypatch):
    cfg = house.dest / "config"
    cfg.mkdir()
    for m in range(1, 15):
        (cfg / f"kronk-config-2025-{m % 12 + 1:02d}-{m:02d}.tar.gz").write_bytes(b"old")
    before = sorted(f.name for f in cfg.iterdir())
    monkeypatch.setattr(B, "verify_tar", lambda *a: "broken (test)")
    assert B.cmd_config(house, FakeOps(), dry_run=False) == 1
    assert sorted(f.name for f in cfg.iterdir()) == before


def test_over_the_ceiling_is_a_failure_naming_the_largest_members(house):
    (house.repo / "data" / "music_taste.yaml").write_bytes(os.urandom(300_000))
    house.max_bytes = 100_000
    ops = FakeOps()
    assert B.cmd_config(house, ops, dry_run=False) == 1
    assert list((house.dest / "config").iterdir()) == []
    body = ops.notices[-1][1]
    assert "over the 100.0 KB ceiling" in body and "largest members: repo/data/music_taste.yaml 300.0 KB" in body
    assert "nothing was written to the destination" in body


def test_the_size_guard_checks_before_anything_reaches_the_destination(house, monkeypatch):
    """Note 19: an oversize tar is never opened on the destination."""
    (house.repo / "data" / "music_taste.yaml").write_bytes(os.urandom(300_000))
    house.max_bytes = 100_000
    opened = []
    real = B.os.open
    monkeypatch.setattr(B.os, "open", lambda p, *a, **k: (opened.append(str(p)), real(p, *a, **k))[1])
    assert B.cmd_config(house, FakeOps(), dry_run=False) == 1
    assert not any(".tar.gz" in p for p in opened)


def test_the_size_guard_default_and_unit(monkeypatch):
    """Mutants g02, g03: 50 MB by default, KRONK_BACKUP_MAX_MB in decimal megabytes."""
    monkeypatch.delenv("KRONK_BACKUP_MAX_MB", raising=False)
    a = type("A", (), {"dest": "/tmp/x", "require_mount": False})()
    assert B.Settings.load(a).max_bytes == 50_000_000
    monkeypatch.setenv("KRONK_BACKUP_MAX_MB", "2")
    assert B.Settings.load(a).max_bytes == 2_000_000


def test_an_exception_mid_write_leaves_no_temporary_file(house, monkeypatch):
    """Mutant t15."""
    def broken(fd, b):
        os.write(fd, b[:10])
        raise OSError(errno.EIO, "Input/output error")
    monkeypatch.setattr(B, "_write_all", broken)
    assert B.cmd_config(house, FakeOps(), dry_run=False) == 1
    assert list((house.dest / "config").iterdir()) == []


def test_keep_zero_removes_nothing_and_retention_touches_only_config(house):
    """Mutants r06 (retention's directory) and r08 (KEEP=0)."""
    cfg = house.dest / "config"
    cfg.mkdir()
    for m in range(1, 13):
        (cfg / f"kronk-config-2025-{m:02d}-10.tar.gz").write_bytes(b"old")
    for d in (house.dest, house.dest / "recordings"):
        d.mkdir(exist_ok=True)
        (d / "kronk-config-2000-01-01.tar.gz").write_bytes(b"not in config/")
    assert B.retention(cfg, 0, lambda m: None) == []
    assert len(list(cfg.iterdir())) == 12
    assert B.retention(cfg, 10, lambda m: None) == ["kronk-config-2025-01-10.tar.gz", "kronk-config-2025-02-10.tar.gz"]
    for d in (house.dest, house.dest / "recordings"):
        assert (d / "kronk-config-2000-01-01.tar.gz").read_bytes() == b"not in config/"


def test_a_same_day_second_tar_unpacks_into_its_own_directory(house):
    """Note 23."""
    B.cmd_config(house, FakeOps(), dry_run=False)
    B.cmd_config(house, FakeOps(), dry_run=False)
    names = tar_names(house.dest / "config" / "kronk-config-2026-12-10.2.tar.gz")
    assert names[0] == "kronk-config-2026-12-10.2" and all(n.startswith("kronk-config-2026-12-10.2") for n in names)


def test_the_tar_carries_the_runbook_and_the_feature_doc(house):
    """Finding 9: the way back travels with the tar."""
    B.cmd_config(house, FakeOps(), dry_run=False)
    p = house.dest / "config" / "kronk-config-2026-12-10.tar.gz"
    assert read_member(p, "/RESTORE.md") == b"# Runbook\n" and read_member(p, "/BACKUPS.md") == b"# Backups\n"


def test_no_bundle_when_nothing_is_unpushed_and_none_over_the_cap(house):
    ops = FakeOps()
    ops.unpushed = 0
    B.cmd_config(house, ops, dry_run=False)
    p = house.dest / "config" / "kronk-config-2026-12-10.tar.gz"
    assert not any(n.endswith("repo-unpushed.bundle") for n in tar_names(p))
    assert "unpushed bundle: none needed" in read_member(p, "repo-state.txt").decode()
    assert not any("bundle" in argv for argv in ops.commands)
    ops = FakeOps()
    ops.bundle = b"x" * 2_000_000
    house.bundle_max_bytes = 1_000_000
    B.cmd_config(house, ops, dry_run=False)
    p = house.dest / "config" / "kronk-config-2026-12-10.2.tar.gz"
    assert not any(n.endswith("repo-unpushed.bundle") for n in tar_names(p))
    state = read_member(p, "repo-state.txt").decode()
    assert "unpushed bundle: OMITTED: 3 commit(s) unpushed, but the bundle is 2.0 MB, over the 1.0 MB cap" in state
    assert "they exist only on this machine: OMITTED" in ops.notices[-1][1]


def test_the_tag_on_the_remote_from_local_refs_only(house):
    ops = FakeOps()
    ops.run_orig = ops.run

    def run(argv, timeout=300, stdin=None):
        if argv[0] == "git" and "--contains" in argv and argv[-1] == "0123abc":
            return B.Result(0, b"  origin/refactor\n", "")
        if argv[0] == "git":
            assert not {"fetch", "ls-remote", "pull", "push"} & set(argv)
        return ops.run_orig(argv, timeout, stdin)
    ops.run = run
    assert B.tag_on_remote(house, ops, "sprint-M16").startswith(
        "unknown: the tag itself cannot be told without contacting the remote; its commit 0123abc is on origin/refactor")


def test_unknown_ha_files_are_reported_and_known_ones_copied_by_name(house):
    """Note 23: a new top-level name in HA's /config is not a silent gap."""
    ops = FakeOps()
    ops.ha_names = [".storage", "configuration.yaml", "custom_components", "home-assistant_v2.db", "tts",
                    "home-assistant.log.1", "automations.yaml.bak-20260904_162955", "zigbee.db"]
    B.cmd_config(house, ops, dry_run=False)
    assert ops.ha_tar_names == [".storage", "configuration.yaml", "custom_components"]
    p = house.dest / "config" / "kronk-config-2026-12-10.tar.gz"
    assert "UNKNOWN\thomeassistant:/config/zigbee.db" in read_member(p, "MANIFEST").decode()
    assert "HA files not known to the backup, not copied: zigbee.db." in ops.notices[-1][1]


def test_dry_run_lists_with_sizes_and_writes_and_sends_nothing(house):
    ops = FakeOps()
    assert B.cmd_config(house, ops, dry_run=True) == 0
    assert list(house.dest.iterdir()) == [] and not house.state.exists() and ops.notices == []
    out = "\n".join(ops.logs)
    assert re.search(r"\s+\d+  repo/\.env$", out, re.M)
    assert "nothing written, nothing sent" in out


def test_an_unmounted_destination_is_a_failure_and_gets_nothing(house):
    house.require_mount = True
    ops = FakeOps()
    assert B.cmd_config(house, ops, dry_run=False) == 2
    assert list(house.dest.iterdir()) == []
    title, body = ops.notices[-1]
    assert title == "Kronk backup: config backup FAILED" and "is not a mounted filesystem" in body
    assert json.loads((house.state / "status.json").read_text())["config"]["last"]["result"] == "failed"
    ops.mounted.add(str(house.dest))
    assert B.cmd_config(house, ops, dry_run=False) == 0


def test_the_default_destination_always_requires_a_mount(monkeypatch):
    monkeypatch.delenv("KRONK_BACKUP_DIR", raising=False)
    monkeypatch.delenv("KRONK_BACKUP_REQUIRE_MOUNT", raising=False)
    a = type("A", (), {"dest": None, "require_mount": False})()
    assert B.Settings.load(a).require_mount is True and str(B.Settings.load(a).dest) == "/mnt/nas-backup"
    a.dest = "/tmp/x"
    assert B.Settings.load(a).require_mount is False
    monkeypatch.setenv("KRONK_BACKUP_REQUIRE_MOUNT", "1")
    assert B.Settings.load(a).require_mount is True


@pytest.mark.parametrize("dest", ["/mnt/nas-backup", "/mnt/nas-backup/", "//mnt/nas-backup", "/mnt//nas-backup",
                                  "/mnt/./nas-backup", "/mnt/x/../nas-backup", "/mnt/nas-backup/kronk",
                                  "//mnt/nas-backup/kronk/./sub"])
def test_any_path_at_or_under_the_share_needs_the_mount(monkeypatch, dest):
    """Note 14."""
    monkeypatch.delenv("KRONK_BACKUP_REQUIRE_MOUNT", raising=False)
    a = type("A", (), {"dest": dest, "require_mount": False})()
    s = B.Settings.load(a)
    assert s.require_mount is True and s.mount_root == Path("/mnt/nas-backup")


def test_a_symlink_to_the_share_needs_the_mount(tmp_path, monkeypatch):
    monkeypatch.delenv("KRONK_BACKUP_REQUIRE_MOUNT", raising=False)
    (tmp_path / "usb").symlink_to("/mnt/nas-backup/kronk")
    a = type("A", (), {"dest": str(tmp_path / "usb"), "require_mount": False})()
    assert B.Settings.load(a).mount_root == Path("/mnt/nas-backup")
    for other in ("/mnt/nas-backupx", "/mnt/nas-music", str(tmp_path)):
        a.dest = other
        assert B.Settings.load(a).require_mount is False


def test_the_require_mount_flag_on_the_command_line(house, monkeypatch, tmp_path):
    """Mutant m06: --require-mount, not only the environment form."""
    monkeypatch.delenv("KRONK_BACKUP_REQUIRE_MOUNT", raising=False)
    monkeypatch.setenv("KRONK_BACKUP_STATE", str(house.state))
    monkeypatch.setenv("KRONK_BACKUP_MOUNT_WAIT_S", "0")
    ops = FakeOps()
    assert B.main(["check", "--dest", str(house.dest), "--require-mount", "--no-notify"], ops=ops) == 1
    st = json.loads((house.state / "status.json").read_text())
    assert any("is not a mounted filesystem" in w for w in st["check"]["warnings"])
    assert list(house.dest.iterdir()) == []


def test_a_destination_under_the_share_must_be_on_the_mounted_share(house):
    sub = house.dest / "kronk"
    sub.mkdir()
    house.dest, house.require_mount, house.mount_root = sub, True, sub.parent
    ops = FakeOps()
    assert "is not a mounted filesystem" in B.dest_problem(house, ops)
    ops.mounted.add(str(sub.parent))
    assert B.dest_problem(house, ops) is None


def test_the_job_waits_a_bounded_time_for_the_mount(house):
    """Note 16: a catch-up run at boot waits for the nofail mount."""
    house.require_mount, house.mount_wait_s = True, 180
    ops = FakeOps()

    def sleep(sec):
        ops.t += dt.timedelta(seconds=sec)
        if ops.t - NOW >= dt.timedelta(seconds=60):
            ops.mounted.add(str(house.dest))            # the share comes up after a minute
    ops.sleep = sleep
    assert B.dest_problem(house, ops, wait=True) is None and ops.t - NOW == dt.timedelta(seconds=60)
    ops = FakeOps()
    assert "(waited 180 s)" in B.dest_problem(house, ops, wait=True) and ops.t - NOW == dt.timedelta(seconds=180)
    ops = FakeOps()
    assert "waited" not in B.dest_problem(house, ops) and ops.t == NOW        # no wait unless asked


@pytest.mark.parametrize("cmd", ["config", "archive", "check"])
def test_a_bad_numeric_setting_is_a_clear_failure_with_a_notice(house, monkeypatch, cmd):
    """Note 20: a notice and a status line, no traceback, nothing done."""
    monkeypatch.setenv("KRONK_BACKUP_STATE", str(house.state))
    monkeypatch.setenv("KRONK_BACKUP_KEEP", "abc")
    monkeypatch.setenv("KRONK_ARCHIVE_AGE_DAYS", "-1")
    ops = FakeOps()
    assert B.main([cmd, "--dest", str(house.dest)], ops=ops) == 2
    title, body = ops.notices[-1]
    assert "FAILED" in title or title == "Kronk backup: needs a look"
    assert "KRONK_BACKUP_KEEP must be a whole number, not 'abc'" in body
    assert "KRONK_ARCHIVE_AGE_DAYS must be at least 1, not -1" in body
    st = json.loads((house.state / "status.json").read_text())
    entry = st["check"] if cmd == "check" else st[cmd]["last"]
    assert "bad setting" in json.dumps(entry)
    assert list(house.dest.iterdir()) == []


@pytest.mark.parametrize("cmd", ["config", "archive", "check"])
def test_no_notify_sends_nothing_even_without_a_stub(house, monkeypatch, cmd):
    """Finding 4: --no-notify on all three; notify.sh is never run."""
    monkeypatch.delenv("KRONK_BACKUP_NOTIFY_CMD", raising=False)
    monkeypatch.setenv("KRONK_BACKUP_STATE", str(house.state))
    monkeypatch.setenv("KRONK_BACKUP_DIR", str(house.dest))
    ran = []

    class Real(FakeOps):           # the real notify, the fake everything else
        notify = B.Ops.notify

        def run(self, argv, timeout=300, stdin=None):
            ran.append(argv)
            if "notify.sh" in " ".join(argv):
                raise AssertionError("notify.sh ran")
            return super().run(argv, timeout, stdin)
    monkeypatch.setattr(B.Settings, "load", classmethod(lambda cls, a: house))     # never the real repo's files
    ops = Real()
    ops.no_notify = False
    B.main([cmd, "--no-notify"], ops=ops)
    assert ops.no_notify is True
    assert any("notice not sent (--no-notify)" in m for m in ops.logs)
    assert not any("notify.sh" in " ".join(a) for a in ran)


def test_upgrade_lock_probe_never_creates_it_and_never_keeps_it(house):
    """Mutants l03 (creates the lock), l04 (keeps the shared lock)."""
    house.upgrade_state.mkdir(parents=True)
    assert B.upgrade_lock_held(house.upgrade_state) is False
    assert not (house.upgrade_state / "lock").exists()
    (house.upgrade_state / "lock").write_text("")
    assert B.upgrade_lock_held(house.upgrade_state) is False
    fd = os.open(house.upgrade_state / "lock", os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)       # nobody holds it after the probe
    finally:
        os.close(fd)


def test_the_etc_kronk_helper_mount_is_read_only(tmp_path):
    """Mutant d10."""
    seen = []

    class Capture(B.Ops):
        def run(self, argv, timeout=300, stdin=None):
            seen.append(argv)
            return B.Result(0, b"", "")
    Capture().helper_tar(tmp_path)
    assert seen[0][seen[0].index("--mount") + 1] == f"type=bind,source={tmp_path},target=/src,readonly"
    assert "-v" not in seen[0]


def test_waits_for_the_other_job_then_declines(house):
    house.state.mkdir(parents=True)
    fd = os.open(house.state / "lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        ops = FakeOps()
        assert B.cmd_config(house, ops, dry_run=False) == 3
    finally:
        os.close(fd)
    assert ops.t - NOW == dt.timedelta(minutes=2)
    assert ops.notices[-1][0] == "Kronk backup: config backup DECLINED"
    assert list(house.dest.iterdir()) == []


def test_declines_while_an_upgrade_run_holds_its_lock_and_never_creates_it(house):
    assert B.cmd_config(house, FakeOps(), dry_run=False) == 0
    assert not (house.upgrade_state / "lock").exists()
    house.upgrade_state.mkdir(parents=True)
    fd = os.open(house.upgrade_state / "lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        ops = FakeOps()
        assert B.cmd_archive(house, ops, dry_run=False) == 3
        assert "an upgrade run holds" in ops.notices[-1][1]
    finally:
        os.close(fd)


# ═══ Recordings archive ════════════════════════════════════════════════════

def rec(house, device, pipeline, run, name="01_stt-stt.faster_whisper.wav", age_days=10, data=None):
    d = house.recordings / device / pipeline / run
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(data if data is not None else os.urandom(3200))
    t = (NOW - dt.timedelta(days=age_days)).timestamp()
    os.utime(p, (t, t))
    return p


def test_first_real_run_a_share_that_stamps_the_file_when_the_write_handle_closes_still_keeps_the_time(
        house, monkeypatch):
    """First run on the Synology share, 2026-11-20: closing a handle that
    wrote resets the file's time to now, so a time set before that close is
    lost. The archive sets it again after the close."""
    old = rec(house, "dev1", "kronk", "1203688800703734", age_days=10)
    mtime, target = old.stat().st_mtime_ns, archived(house, old)
    real_write, real_close, real_utime, wrote = os.write, os.close, os.utime, set()

    def write(fd, b):
        wrote.add(fd)
        return real_write(fd, b)

    def close(fd):
        if fd in wrote:
            wrote.discard(fd)
            real_utime(fd, None)          # the server's stamp at close
        real_close(fd)

    monkeypatch.setattr(B.os, "write", write)
    monkeypatch.setattr(B.os, "close", close)
    assert B.cmd_archive(house, FakeOps(), dry_run=False) == 0
    assert target.stat().st_mtime_ns == mtime and not old.exists()


def test_first_real_run_a_share_that_cannot_keep_the_time_fails_the_copy_and_keeps_the_recording(
        house, monkeypatch):
    old = rec(house, "dev1", "kronk", "1203688800703734", age_days=10)
    target = archived(house, old)
    monkeypatch.setattr(B.os, "utime", lambda *a, **k: None)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) != 0
    assert old.exists() and not target.exists()
    assert "did not keep the recording's time" in ops.notices[-1][1]


def archived(house, p: Path) -> Path:
    rel = p.relative_to(house.recordings).parts
    month = dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m")
    return house.dest / "recordings" / rel[0] / rel[1] / month / rel[2] / rel[3]


def test_old_recordings_move_keeping_names_and_times_young_ones_stay(house):
    old = rec(house, "dev1", "kronk", "1203688800703734", age_days=10)
    young = rec(house, "dev1", "kronk", "1299062379733951", age_days=1)
    data, mtime, target = old.read_bytes(), old.stat().st_mtime_ns, archived(house, old)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0
    assert target == house.dest / "recordings/dev1/kronk/2026-11/1203688800703734/01_stt-stt.faster_whisper.wav"
    assert target.read_bytes() == data and target.stat().st_mtime_ns == mtime
    for d in (target.parent, target.parent.parent, target.parent.parent.parent, house.dest / "recordings" / "dev1",
              house.dest / "recordings"):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700, d       # mutant t11: every level the archive makes
    assert stat.S_IMODE(target.stat().st_mode) == 0o600          # mutant t10
    assert not old.exists() and not old.parent.exists()      # the emptied run directory goes too
    assert young.exists() and (house.recordings / "dev1" / "kronk").is_dir()
    assert ops.notices[0] == ("Kronk backup: recordings archive started",
                              f"Moving recordings older than 7 days from {house.recordings} to {house.dest}/recordings.")
    title, body = ops.notices[-1]
    assert title == "Kronk backup: recordings archive OK"
    assert body.startswith("Moved 1 recording(s) (3.2 KB); 1 remain local (3.2 KB). Written this run: 3.2 KB. "
                           "On the destination: config backups 0 B (0 files), recordings archive 3.2 KB (1 file), together 3.2 KB.")


def test_a_second_run_with_nothing_new_copies_and_deletes_nothing(house):
    rec(house, "dev1", "kronk", "1", age_days=10)
    rec(house, "dev1", "kronk", "2", age_days=1)
    B.cmd_archive(house, FakeOps(), dry_run=False)
    before = every_file(house.dest)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0
    assert every_file(house.dest) == before and ops.removed == []
    assert ops.notices[-1][1].startswith("Moved 0 recording(s) (0 B); 1 remain local (3.2 KB). Written this run: 0 B.")


def test_same_hash_on_the_destination_is_not_copied_again_and_is_deleted_locally(house):
    """Also the state a run killed between copy and delete leaves behind."""
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    t = archived(house, p)
    t.parent.mkdir(parents=True)
    t.write_bytes(p.read_bytes())
    os.utime(t, (1, 1))           # a different mtime: the hash decides, and the copy is not touched
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0
    assert not p.exists() and t.stat().st_mtime == 1
    assert "Moved 1 recording(s)" in ops.notices[-1][1] and "Written this run: 0 B" in ops.notices[-1][1]


def test_same_name_different_hash_is_never_overwritten_both_kept_and_reported(house):
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    t = archived(house, p)
    t.parent.mkdir(parents=True)
    t.write_bytes(b"something else")
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert t.read_bytes() == b"something else" and p.exists()
    assert ops.notices[-1][0] == "Kronk backup: recordings archive FAILED"
    assert "a different file of the same name (both kept)" in ops.notices[-1][1]


def test_fault_destination_not_mounted(house):
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    house.require_mount = True
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 2
    assert p.exists() and list(house.dest.iterdir()) == [] and ops.removed == []
    assert "is not a mounted filesystem" in ops.notices[-1][1]


def test_fault_destination_full_mid_copy(house, monkeypatch):
    a = rec(house, "dev1", "kronk", "1", age_days=10, data=os.urandom(3 << 20))
    b = rec(house, "dev1", "kronk", "2", age_days=10)
    calls = {"n": 0}
    real = B._write_all

    def full(fd, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(errno.ENOSPC, "No space left on device")
        real(fd, data)
    monkeypatch.setattr(B, "_write_all", full)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert a.exists() and not b.exists()                  # note 18: the smaller file after it still went
    assert not any(B.is_archive_tmp(f.name) for f in house.dest.rglob("*"))
    assert [f.name for f in house.dest.rglob("*") if f.is_file()] == [b.name]
    assert "destination full: No space left on device" in ops.notices[-1][1]


def test_a_file_that_does_not_fit_is_skipped_and_smaller_later_files_still_go(house, monkeypatch):
    """Note 18: one oversized file early in the order no longer blocks the rest."""
    big = rec(house, "dev1", "kronk", "1", age_days=10, data=b"B" * 50_000)
    big2 = rec(house, "dev1", "kronk", "2", age_days=10, data=b"C" * 60_000)
    small = rec(house, "dev1", "kronk", "3", age_days=10, data=b"s" * 100)
    real = B._write_all

    def tiny_disk(fd, data):
        if len(data) > 10_000:
            raise OSError(errno.ENOSPC, "No space left on device")
        real(fd, data)
    monkeypatch.setattr(B, "_write_all", tiny_disk)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert big.exists() and big2.exists() and not small.exists()
    assert [f.name for f in house.dest.rglob("*") if f.is_file()] == [small.name]
    body = ops.notices[-1][1]
    assert "Moved 1 recording(s)" in body and "skipped, does not fit" in str(json.loads(
        (house.state / "status.json").read_text())["archive"]["last"]["problems"])


def test_fault_the_copy_reads_back_different(house, monkeypatch):
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    real = B.sha256_fd
    monkeypatch.setattr(B, "sha256_fd", lambda fd, drop_cache: "0" * 64 if drop_cache else real(fd, drop_cache))
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert p.exists() and [f for f in house.dest.rglob("*") if f.is_file()] == [] and ops.removed == []
    assert "the copy read back different; kept locally" in ops.notices[-1][1]


def test_fault_the_local_delete_fails_then_the_next_run_finishes_it(house):
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    ops = FakeOps()
    ops.keep_local.add("dev1/kronk/1/01_stt-stt.faster_whisper.wav")
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert p.exists() and archived(house, p).exists()
    assert "local delete failed" in ops.notices[-1][1]
    ops2 = FakeOps()
    assert B.cmd_archive(house, ops2, dry_run=False) == 0
    assert not p.exists() and "Written this run: 0 B" in ops2.notices[-1][1]


def test_fault_killed_mid_copy_the_leftover_is_kept_and_reported_never_removed(house):
    """Finding 1: the job removes only the temporary file it made in this run."""
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    t = archived(house, p)
    t.parent.mkdir(parents=True)
    leftover = t.parent / ".kronk-archive.4242.0123456789abcdef.tmp"
    leftover.write_bytes(b"half a copy")
    old_name = t.parent / ".kronk-archive.tmp"
    old_name.write_bytes(b"a finished recording that once got this name")
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0
    assert sorted(f.name for f in t.parent.iterdir()) == sorted([t.name, leftover.name, old_name.name])
    assert leftover.read_bytes() == b"half a copy" and old_name.read_bytes().startswith(b"a finished")
    assert not p.exists() and t.read_bytes()
    assert "2 leftover temporary file(s) from interrupted archive runs" in ops.notices[-1][1]


def test_fault_a_recording_being_written_is_left_for_the_next_run(house, monkeypatch):
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    real = B._write_all
    once = []

    def growing(fd, data):
        real(fd, data)
        if not once:                      # HA appends once while the archive reads
            once.append(1)
            with open(p, "ab") as f:
                f.write(b"more audio")
    monkeypatch.setattr(B, "_write_all", growing)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0
    assert p.exists() and [f for f in house.dest.rglob("*") if f.is_file()] == [] and ops.removed == []
    assert "Left for the next run: 1 (dev1/kronk/1/01_stt-stt.faster_whisper.wav: changed while it was read" in ops.notices[-1][1]


def test_fault_symlinks_are_not_followed(house, tmp_path):
    outside = tmp_path / "outside"
    (outside / "kronk" / "9").mkdir(parents=True)
    victim = outside / "kronk" / "9" / "x.wav"
    victim.write_bytes(b"not a recording")
    os.utime(victim, (1, 1))
    house.recordings.mkdir(parents=True)
    (house.recordings / "linkdev").symlink_to(outside)                  # a symlinked device directory
    rec(house, "dev1", "kronk", "1", age_days=10)
    (house.recordings / "dev1" / "kronk" / "1" / "02_link.wav").symlink_to(victim)   # a symlinked file
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1        # reported as a problem: a person looks
    assert victim.read_bytes() == b"not a recording"
    assert (house.recordings / "dev1" / "kronk" / "1" / "02_link.wav").is_symlink()
    files = [f.name for f in house.dest.rglob("*") if f.is_file()]
    assert files == ["01_stt-stt.faster_whisper.wav"]
    assert "linkdev: symlink, not followed" in ops.notices[-1][1]


def test_fault_names_with_spaces_and_newlines(house):
    p = rec(house, "dev 1", "kronk dev", "run\nwith newline", name="a b\nc.wav", age_days=10)
    data, target = p.read_bytes(), archived(house, p)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0
    assert target.read_bytes() == data and not p.exists()
    assert ops.removed == [["dev 1/kronk dev/run\nwith newline/a b\nc.wav"]]


def vrec(rel, size=10, mtime_ns=5_000_000_000, dev=1, ino=2, month="2026-11", sha="0" * 64, ddev=3, dino=4):
    return B.Verified(B.Rec(tuple(rel), size, mtime_ns, dev, ino), month, sha, ddev, dino)


def test_the_delete_runs_by_literal_path_in_a_container_that_sees_only_the_two_trees(tmp_path, monkeypatch):
    monkeypatch.delenv("KRONK_ARCHIVE_DELETE", raising=False)
    monkeypatch.delenv("KRONK_BACKUP_HELPER_IMAGE", raising=False)
    seen = []

    class Capture(B.Ops):
        def run(self, argv, timeout=300, stdin=None):
            seen.append(argv)
            return B.Result(0, b"ok 1\n", "")
    src, dst = tmp_path / "rec", tmp_path / "dest" / "recordings"
    res, err = Capture().remove_recordings(src, dst, [vrec(["d", "p", "r 1", "a\nb.wav"])])
    argv = seen[0]
    # Second e2e, finding 3: --mount (a missing source fails, -v would create it); note 7: the label;
    # third e2e, note 8: a name that carries the job's pid.
    name = argv.pop(9)
    assert argv.pop(8) == "--name" and B.HELPER_NAME_RE.fullmatch(name).group(1) == str(os.getpid())
    assert argv[:13] == ["docker", "run", "--rm", "--pull=never", "--network", "none",
                         "--label", "kronk.backup.helper=1",
                         "--mount", f"type=bind,source={src},target=/rec",
                         "--mount", f"type=bind,source={dst},target=/dst,readonly", "alpine:3.20"]
    assert "-v" not in argv
    assert argv[13:16] == ["sh", "-c", B.DELETE_SCRIPT] and argv[16:19] == ["sh", "/rec", "/dst"]
    assert argv[19:] == ["1", "d", "p", "r 1", "a\nb.wav", "2026-11", "10", "5", "1:2", "0" * 64, "3:4"]
    assert res == {0: "ok"} and err == ""
    for bad in ("a,b", 'a"b'):
        assert Capture().remove_recordings(tmp_path / bad, dst, [vrec(["d", "p", "r", "x"])]) == ({}, \
            f"refused: a path docker --mount cannot carry: {str(tmp_path / bad)!r}")
    assert len(seen) == 1


def test_the_delete_script_text_is_pinned():
    """Mutants d02, d03: rm and rmdir only, no -r, no -f, after every check."""
    assert B.DELETE_SCRIPT == DELETE_SCRIPT_PINNED
    lines = B.DELETE_SCRIPT.splitlines()
    assert [ln for ln in lines if re.search(r"\brm(dir)? --", ln)] == [
        '  if [ -z "$why" ] && rm -- "$f"; then echo "ok $i"; rmdir -- "$R/$dv/$pl/$rn" 2>/dev/null']


DELETE_SCRIPT_PINNED = r"""R=$1; D=$2; n=$3; shift 3; i=0
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
      g=$(sha256sum < "$t"); g=${g%% *}
      d=$(stat -c '%s %d:%i' -- "$t")
      h=$(sha256sum < "$f"); h=${h%% *}
      s=$(stat -c '%s %Y %d:%i' -- "$f")
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


def pair(tmp_path, data=b"audio"):
    """A local recording and its verified copy, for the delete script."""
    src, dst = tmp_path / "rec", tmp_path / "dst"
    f = src / "d" / "p" / "r" / "a.wav"
    t = dst / "d" / "p" / "2026-11" / "r" / "a.wav"
    for x in (f, t):
        x.parent.mkdir(parents=True)
        x.write_bytes(data)
    a, b = f.stat(), t.stat()
    v = B.Verified(B.Rec(("d", "p", "r", "a.wav"), a.st_size, a.st_mtime_ns, a.st_dev, a.st_ino), "2026-11",
                   __import__("hashlib").sha256(data).hexdigest(), b.st_dev, b.st_ino)
    return src, dst, f, t, v


def test_the_delete_script_removes_only_the_verified_file(tmp_path):
    src, dst, f, t, v = pair(tmp_path)
    res, err = B.Ops().remove_recordings(src, dst, [v])
    assert res == {0: "ok"} and not f.exists() and not f.parent.exists() and t.read_bytes() == b"audio"


@pytest.mark.parametrize("change,why", [
    ("content", "sha256 differs"), ("swap", "size, mtime or inode differ"), ("mtime", "size, mtime or inode differ"),
    ("dir", "not a regular file"), ("symlink", "not a regular file"), ("dest-gone", "the destination copy is gone"),
    ("dest-swapped", "the destination copy is not the verified one"), ("dest-symlink", "the destination copy is gone"),
    ("dest-content", "the destination copy's sha256 differs"),
    ("run-symlink", "a directory on the way is a symlink"), ("dest-run-symlink", "a directory on the way is a symlink")])
def test_the_delete_script_refuses_anything_but_the_verified_file(tmp_path, change, why):
    """Finding 2: what is about to be removed is compared with what was copied."""
    src, dst, f, t, v = pair(tmp_path)
    if change == "content":
        f.write_bytes(b"AUDIO")                               # same size, same inode, new bytes
        os.utime(f, ns=(v.rec.mtime_ns, v.rec.mtime_ns))
    elif change == "swap":
        n = f.with_name("new")
        n.write_bytes(b"audio")
        os.utime(n, ns=(v.rec.mtime_ns, v.rec.mtime_ns))
        os.replace(n, f)                                      # same bytes, same mtime, another inode
    elif change == "mtime":
        os.utime(f, (1, 1))
    elif change == "dir":
        f.unlink()
        f.mkdir()                                             # a directory named like the file
        (f / "inside").write_bytes(b"keep")
    elif change == "symlink":
        keep = tmp_path / "keep.wav"
        keep.write_bytes(b"audio")
        f.unlink()
        f.symlink_to(keep)
    elif change == "dest-gone":
        t.unlink()
    elif change == "dest-swapped":
        n = t.with_name("new")
        n.write_bytes(b"audio")
        os.replace(n, t)                                      # same bytes, another inode
    elif change == "dest-symlink":
        t.unlink()
        t.symlink_to(f)
    elif change == "dest-content":                            # second e2e, finding 1: same inode, same size
        with open(t, "r+b") as w:
            w.write(b"AUDIO")
    elif change == "run-symlink":
        moved = tmp_path / "elsewhere"
        f.parent.rename(moved)
        f.parent.symlink_to(moved)
    elif change == "dest-run-symlink":
        t.parent.rename(tmp_path / "d2")
        t.parent.symlink_to(f.parent)                         # finding 3's shape
    before = every_file(tmp_path)
    res, err = B.Ops().remove_recordings(src, dst, [v])
    assert res[0].startswith(why), res
    assert every_file(tmp_path) == before


def test_thirty_thousand_deletes_go_in_batches(tmp_path, monkeypatch):
    """Finding 11: no argument list too long, every name exactly once."""
    monkeypatch.delenv("KRONK_ARCHIVE_DELETE", raising=False)
    seen = []

    class Capture(B.Ops):
        def run(self, argv, timeout=300, stdin=None):
            seen.append(argv)
            n = int(argv[argv.index("/dst") + 1])
            return B.Result(0, "".join(f"ok {i}\n" for i in range(1, n + 1)).encode(), "")
    items = [vrec(["e0949788663255952ce7778975527882", "kronk", f"{1203688800703734 + i}",
                   "01_stt-stt.faster_whisper.wav"], ino=i) for i in range(30_000)]
    res, err = Capture().remove_recordings(tmp_path / "rec", tmp_path / "dst", items)
    assert len(seen) == 30_000 // B.DELETE_BATCH and res == {i: "ok" for i in range(30_000)}
    assert max(sum(len(a) + 1 for a in argv) for argv in seen) < 128 * 1024
    at = [argv.index("/dst") + 1 for argv in seen]
    names = [argv[i + 1 + 10 * k + 2] for argv, i in zip(seen, at) for k in range(int(argv[i]))]
    assert len(set(names)) == 30_000
    assert len({argv[argv.index("--name") + 1] for argv in seen}) == len(seen)      # one name per container


def test_nothing_outside_the_recordings_and_nothing_on_the_destination_is_deleted(house, tmp_path, monkeypatch):
    """Criterion 10: a tree around both, through every fault above."""
    around = tmp_path / "around"
    (around / "kronk-voice-recordings-old").mkdir(parents=True)
    (around / "kronk-voice-recordings-old" / "keep.wav").write_bytes(b"k")
    (house.dest / "config").mkdir()
    (house.dest / "config" / "kronk-config-2026-01-01.tar.gz").write_bytes(b"c")
    (house.dest / "other").mkdir()
    (house.dest / "other" / "photo.jpg").write_bytes(b"p")
    (house.dest / "recordings" / "dev1" / "kronk" / "2026-01" / "5").mkdir(parents=True)
    (house.dest / "recordings" / "dev1" / "kronk" / "2026-01" / "5" / "01_stt.wav").write_bytes(b"archived earlier")
    (house.dest / "recordings" / "dev1" / "kronk" / "2026-01" / "5" / ".kronk-archive.tmp.keep").write_bytes(b"x")
    rec(house, "dev1", "kronk", "1", age_days=10)
    rec(house, "dev1", "kronk", "2", age_days=10)
    house.recordings.joinpath("stray.txt").write_bytes(b"not at the recording depth")
    outside_before = every_file(around)
    dest_before = every_file(house.dest)
    ops = FakeOps()
    ops.keep_local.add("dev1/kronk/2/01_stt-stt.faster_whisper.wav")
    B.cmd_archive(house, ops, dry_run=False)
    B.cmd_archive(house, FakeOps(), dry_run=False)
    assert every_file(around) == outside_before
    after = every_file(house.dest)
    assert all(after.get(k) == v for k, v in dest_before.items())          # nothing removed or changed
    assert house.recordings.joinpath("stray.txt").exists()


# ═══ Blockers of the end-to-end test (docs/sprint/M16-e2e.md) ═══════════

def test_finding1_a_source_named_like_the_temporary_file_is_reported_and_never_copied(house):
    tmpname = rec(house, "devA", "kronk", "run1", name=".kronk-archive.tmp", data=b"t" * 5000)
    other = rec(house, "devA", "kronk", "run1", name="01_b.wav", data=b"b" * 6000)
    lookalike = rec(house, "devA", "kronk", "run1", name=".kronk-archive.1.0123456789abcdef.tmp", data=b"l" * 10)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1           # reported: a person looks
    assert tmpname.read_bytes() == b"t" * 5000 and lookalike.read_bytes() == b"l" * 10
    assert [f.name for f in house.dest.rglob("*") if f.is_file()] == ["01_b.wav"]
    assert not other.exists()
    assert "named like the archive's temporary files; never archived" in ops.notices[-1][1]
    # A second run: still there, still not copied, the archived file untouched.
    before = every_file(house.dest)
    B.cmd_archive(house, FakeOps(), dry_run=False)
    assert tmpname.exists() and every_file(house.dest) == before


def test_finding1_the_copy_never_unlinks_a_destination_file_it_did_not_create(house, monkeypatch):
    """Even a file that takes this run's own temporary name first is left alone."""
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    t = archived(house, p)
    t.parent.mkdir(parents=True)
    names = iter(["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"])
    monkeypatch.setattr(B.secrets, "token_hex", lambda n: next(names))
    squatter = t.parent / f".kronk-archive.{os.getpid()}.aaaaaaaaaaaaaaaa.tmp"
    squatter.write_bytes(b"someone else's")
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0, ops.notices[-1][1][:300]
    assert squatter.read_bytes() == b"someone else's" and t.exists() and not p.exists()
    assert not (t.parent / f".kronk-archive.{os.getpid()}.bbbbbbbbbbbbbbbb.tmp").exists()


def test_finding1_the_temporary_file_is_removed_only_while_it_is_still_ours(tmp_path):
    d = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        (tmp_path / "t").write_bytes(b"ours")
        st = os.stat(tmp_path / "t")
        own = (st.st_dev, st.st_ino)
        n = tmp_path / "n"
        n.write_bytes(b"someone else's")
        os.replace(n, tmp_path / "t")                      # the name now holds another file
        B._unlink_own("t", d, own)
        assert (tmp_path / "t").read_bytes() == b"someone else's"
        st = os.stat(tmp_path / "t")
        B._unlink_own("t", d, (st.st_dev, st.st_ino))
        assert not (tmp_path / "t").exists()
    finally:
        os.close(d)


def test_a_same_size_rewrite_during_the_copy_is_left_for_the_next_run(house, monkeypatch):
    p = rec(house, "dev1", "kronk", "1", data=b"a" * 3 * (1 << 20))
    real = B._write_all
    once = []

    def rewrite(fd, data):
        real(fd, data)
        if not once:
            once.append(1)
            with open(p, "r+b") as f:
                f.write(b"b" * 10)                      # same size, new bytes, new mtime
            os.utime(p, ns=(p.stat().st_mtime_ns + 1_000_000_000,) * 2)
    monkeypatch.setattr(B, "_write_all", rewrite)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0
    assert p.exists() and [f for f in house.dest.rglob("*") if f.is_file()] == [] and ops.removed == []
    assert "Left for the next run: 1" in ops.notices[-1][1]


def test_local_exists_counts_what_it_cannot_see_as_still_there(tmp_path):
    (tmp_path / "d" / "p").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "d" / "p" / "r").symlink_to(tmp_path / "elsewhere")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert B.local_exists(fd, B.Rec(("d", "p", "r", "f.wav"), 1, 1)) is True
        assert B.local_exists(fd, B.Rec(("d", "p", "gone", "f.wav"), 1, 1)) is False
    finally:
        os.close(fd)


def test_a_destination_under_the_share_on_another_filesystem_is_refused(house, monkeypatch):
    sub = house.dest / "kronk"
    sub.mkdir()
    house.dest, house.require_mount, house.mount_root = sub, True, sub.parent
    ops = FakeOps()
    ops.mounted.add(str(sub.parent))
    real = os.stat

    def other(st):
        t = list(st)
        t[2] += 1                                         # st_dev: another filesystem
        return os.stat_result(t)
    monkeypatch.setattr(B.os, "stat", lambda p, *a, **k: other(real(p)) if str(p) == str(sub) else real(p, *a, **k))
    assert "is not on the share mounted at" in B.dest_problem(house, ops)


def test_a_file_that_appears_under_the_final_name_during_the_copy_is_never_replaced(house, monkeypatch):
    p = rec(house, "dev1", "kronk", "1", data=b"a" * 100)
    t = archived(house, p)
    real = B._write_all

    def and_someone_writes(fd, data):
        real(fd, data)
        t.write_bytes(b"appeared")
    monkeypatch.setattr(B, "_write_all", and_someone_writes)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert t.read_bytes() == b"appeared" and p.read_bytes() == b"a" * 100
    assert not any(B.is_archive_tmp(f.name) for f in t.parent.iterdir())
    assert "appeared on the destination during the copy (both kept)" in ops.notices[-1][1]


def test_finding2_a_file_swapped_after_its_verify_is_kept_and_the_run_fails(house, monkeypatch):
    a = rec(house, "dev1", "kronk", "run1", name="01_a.wav", data=b"a" * 4000)
    real = B.copy_verified

    def then_swap(src_fd, dst_fd, r, month):
        out = real(src_fd, dst_fd, r, month)
        if r.rel[3] == "01_a.wav":
            n = a.with_name("new")
            n.write_bytes(b"A" * 4000)                       # the replacement, by rename
            os.utime(n, ns=(r.mtime_ns, r.mtime_ns))
            os.replace(n, a)
        return out
    monkeypatch.setattr(B, "copy_verified", then_swap)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert a.read_bytes() == b"A" * 4000                       # the replacement exists, locally
    assert archived(house, a).read_bytes() == b"a" * 4000      # the first content, on the destination
    assert "local delete refused (size, mtime or inode differ from the copied file)" in ops.notices[-1][1]
    assert "Moved 0 recording(s)" in ops.notices[-1][1]


@pytest.mark.parametrize("how", ["vanished", "replaced", "symlinked"])
def test_finding2_a_destination_copy_that_changes_after_its_verify_stops_the_delete(house, monkeypatch, how):
    a = rec(house, "dev1", "kronk", "run1", data=b"a" * 4000)
    real = B.copy_verified

    def then_break(src_fd, dst_fd, r, month):
        out = real(src_fd, dst_fd, r, month)
        t = house.dest / "recordings" / "dev1" / "kronk" / month / "run1" / r.rel[3]
        if how == "vanished":
            t.unlink()
        elif how == "replaced":
            n = t.with_name("x")
            n.write_bytes(b"a" * 4000)
            os.replace(n, t)
        else:
            t.unlink()
            t.symlink_to(a)
        return out
    monkeypatch.setattr(B, "copy_verified", then_break)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert a.read_bytes() == b"a" * 4000
    # The check is DELETE_SCRIPT's, right before the rm (the host's weaker re-check is gone).
    assert "local delete refused (the destination copy" in ops.notices[-1][1]


def test_finding3_a_symlinked_destination_run_directory_never_verifies_a_file_against_itself(house):
    a = rec(house, "devA", "kronk", "run1", data=b"x" * 5000)
    month = dt.datetime.fromtimestamp(a.stat().st_mtime).strftime("%Y-%m")
    d = house.dest / "recordings" / "devA" / "kronk" / month
    d.mkdir(parents=True)
    (d / "run1").symlink_to(a.parent)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert a.read_bytes() == b"x" * 5000 and ops.removed == []
    assert "is a symlink or not a directory" in ops.notices[-1][1]


def test_finding3_a_symlinked_device_directory_gets_nothing_written_through_it(house, tmp_path):
    a = rec(house, "devA", "kronk", "run1", data=b"x" * 5000)
    outside = tmp_path / "outside"
    outside.mkdir()
    (house.dest / "recordings").mkdir()
    (house.dest / "recordings" / "devA").symlink_to(outside)
    assert B.cmd_archive(house, FakeOps(), dry_run=False) == 1
    assert a.exists() and list(outside.iterdir()) == []


def test_finding3_samefile_is_a_conflict_never_already_archived(house):
    a = rec(house, "devA", "kronk", "run1", data=b"x" * 5000)
    t = archived(house, a)
    t.parent.mkdir(parents=True)
    os.link(a, t)                                             # the same file in both trees
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert a.read_bytes() == b"x" * 5000 and ops.removed == []
    assert "is the source file itself" in ops.notices[-1][1]


def test_finding3_a_symlinked_recordings_root_is_refused(house, tmp_path):
    real = tmp_path / "real-recordings"
    (real / "dev1" / "kronk" / "1").mkdir(parents=True)
    f = real / "dev1" / "kronk" / "1" / "01.wav"
    f.write_bytes(b"r")
    os.utime(f, (1, 1))
    house.recordings.symlink_to(real)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 2
    assert f.exists() and not (house.dest / "recordings").exists()
    assert "is a symlink; refused, not followed" in ops.notices[-1][1]


@pytest.mark.parametrize("where", ["dest-in-src", "src-in-dest"])
def test_overlapping_trees_are_refused(house, where):
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    if where == "dest-in-src":
        house.dest = house.recordings / "dev1"
    else:
        house.recordings = house.dest / "recordings" / "inner"
        house.recordings.mkdir(parents=True)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 2
    assert p.exists() and "overlap; refused" in ops.notices[-1][1]


def test_a_directory_swapped_for_a_symlink_mid_run_is_not_followed(house, tmp_path, monkeypatch):
    a = rec(house, "dev1", "kronk", "1", data=b"a" * 100)
    b = rec(house, "dev1", "kronk", "2", data=b"b" * 100)
    outside = tmp_path / "outside" / "2"
    outside.mkdir(parents=True)
    (outside / b.name).write_bytes(b"not a recording")
    os.utime(outside / b.name, ns=(b.stat().st_mtime_ns,) * 2)
    real = B.scan_recordings
    swapped = []

    def scan_then_swap(src):
        out = real(src)
        if not swapped:
            swapped.append(1)
            b.parent.rename(tmp_path / "moved-away")
            b.parent.symlink_to(outside)                   # between the scan and the copy
        return out
    monkeypatch.setattr(B, "scan_recordings", scan_then_swap)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert not a.exists() and (outside / b.name).read_bytes() == b"not a recording"
    assert (tmp_path / "moved-away" / b.name).read_bytes() == b"b" * 100
    assert "source: " in ops.notices[-1][1] and "is a symlink or not a directory" in ops.notices[-1][1]


def test_a_fifo_swapped_in_mid_run_never_hangs_the_run(house, monkeypatch):
    a = rec(house, "dev1", "kronk", "1", data=b"a" * 100)
    real = B.scan_recordings

    def scan_then_fifo(src):
        out = real(src)
        a.unlink()
        os.mkfifo(a)
        return out
    monkeypatch.setattr(B, "scan_recordings", scan_then_fifo)
    assert B.cmd_archive(house, FakeOps(), dry_run=False) == 0      # left for the next run
    assert stat.S_ISFIFO(os.lstat(a).st_mode)


def test_a_kill_between_verify_and_delete_is_finished_by_the_next_run(house, monkeypatch):
    a = rec(house, "dev1", "kronk", "1", data=b"a" * 100)

    def killed(*args, **kw):
        raise KeyboardInterrupt
    ops = FakeOps()
    monkeypatch.setattr(ops, "remove_recordings", killed)
    with pytest.raises(KeyboardInterrupt):
        B.cmd_archive(house, ops, dry_run=False)
    assert a.exists() and archived(house, a).read_bytes() == b"a" * 100
    monkeypatch.undo()
    monkeypatch.setenv("KRONK_ARCHIVE_DELETE", "direct")
    monkeypatch.setenv("KRONK_BACKUP_NOTIFY_CMD", "/bin/false")
    monkeypatch.setattr(B, "HOME", house.repo.parent.parent.parent)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0
    assert not a.exists() and "Written this run: 0 B" in ops.notices[-1][1]


def test_same_size_different_content_on_the_destination_is_a_conflict(house):
    """Mutant v05: the hash decides, not the size."""
    p = rec(house, "dev1", "kronk", "1", data=b"a" * 100)
    t = archived(house, p)
    t.parent.mkdir(parents=True)
    t.write_bytes(b"b" * 100)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert p.read_bytes() == b"a" * 100 and t.read_bytes() == b"b" * 100 and ops.removed == []


def test_a_non_file_of_the_same_name_on_the_destination_is_a_conflict(house):
    """Mutant v15."""
    p = rec(house, "dev1", "kronk", "1", data=b"a" * 100)
    t = archived(house, p)
    t.mkdir(parents=True)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert p.exists() and t.is_dir() and "something else of the same name" in ops.notices[-1][1]


def test_files_at_the_wrong_depth_are_never_taken_as_recordings(house):
    """Mutant s06."""
    rec(house, "dev1", "kronk", "1")
    shallow = house.recordings / "dev1" / "kronk" / "x.wav"
    deep = house.recordings / "dev1" / "kronk" / "1" / "sub" / "y.wav"
    deep.parent.mkdir()
    for f in (shallow, deep):
        f.write_bytes(b"z")
        os.utime(f, (1, 1))
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert shallow.exists() and deep.exists()
    assert [f.name for f in house.dest.rglob("*") if f.is_file()] == ["01_stt-stt.faster_whisper.wav"]


def test_archive_dry_run_writes_deletes_and_sends_nothing(house):
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=True) == 0
    assert p.exists() and list(house.dest.iterdir()) == [] and ops.notices == [] and ops.removed == []
    assert not house.state.exists()
    assert "would move" in "\n".join(ops.logs) and "recordings/dev1/kronk/2026-11/1/" in "\n".join(ops.logs)


# ═══ The stale check ═══════════════════════════════════════════════════════

def test_stale_check_with_a_clock(house):
    rec(house, "dev1", "kronk", "1", age_days=10)
    B.cmd_config(house, FakeOps(), dry_run=False)
    B.cmd_archive(house, FakeOps(), dry_run=False)
    # The tar's mtime is the real clock; pin it to the fake run time.
    tarp = next((house.dest / "config").iterdir())
    os.utime(tarp, (NOW.timestamp(), NOW.timestamp()))
    ops = FakeOps(NOW + dt.timedelta(days=1))
    assert B.cmd_check(house, ops) == 0 and ops.notices == []
    ops = FakeOps(NOW + dt.timedelta(days=10))
    assert B.cmd_check(house, ops) == 1
    assert ops.notices == [("Kronk backup: needs a look", "the recordings archive last finished 10 days ago (limit 9).")]
    ops = FakeOps(NOW + dt.timedelta(days=11))          # same warnings the next day: no repeat
    B.cmd_check(house, ops)
    assert ops.notices == []
    ops = FakeOps(NOW + dt.timedelta(days=36))          # changed (config now stale too): a notice
    B.cmd_check(house, ops)
    assert ops.notices and "the newest config backup, kronk-config-2026-12-10.tar.gz, is 36 days old (limit 35)" in ops.notices[0][1]
    ops = FakeOps(NOW + dt.timedelta(days=43))          # a week on, unchanged: repeated
    B.cmd_check(house, ops)
    assert len(ops.notices) == 1
    st = json.loads((house.state / "status.json").read_text())
    assert len(st["check"]["warnings"]) == 2 and st["limits"] == {"config_days": 35, "archive_days": 9}


def test_stale_check_never_ran_and_not_mounted(house):
    ops = FakeOps()
    assert B.cmd_check(house, ops) == 1
    body = ops.notices[0][1]
    assert f"no config backup on {house.dest}/config" in body and "never finished a run" in body
    house.require_mount = True
    ops = FakeOps()
    B.cmd_check(house, ops)
    assert "is not a mounted filesystem" in ops.notices[0][1]
    assert list(house.dest.iterdir()) == []


# ═══ Units and wrappers (criterion 12) ═════════════════════════════════════

UNITS = ROOT / "systemd"


@pytest.mark.parametrize("name,calendar", [("kronk-backup.timer", "*-*-10 01:30"),
                                           ("kronk-recordings-archive.timer", "Mon *-*-* 01:00"),
                                           ("kronk-backup-check.timer", "*-*-* 09:30")])
def test_timers(name, calendar):
    text = (UNITS / name).read_text()
    assert f"OnCalendar={calendar}\n" in text and "Persistent=true" in text and "WantedBy=timers.target" in text


def test_the_backup_misses_the_upgrade_window():
    """Not 02:00-05:00 on days 1-7 (the upgrade run), not the 8th's 04:30 report."""
    backup = (UNITS / "kronk-backup.timer").read_text()
    m = re.search(r"OnCalendar=\*-\*-(\d+) (\d\d):(\d\d)", backup)
    day, hour = int(m.group(1)), int(m.group(2))
    assert day > 8 and (hour < 2 or hour >= 5)
    archive = re.search(r"OnCalendar=Mon \*-\*-\* (\d\d):(\d\d)", (UNITS / "kronk-recordings-archive.timer").read_text())
    assert int(archive.group(1)) < 2       # Mondays include days 1-7: before the window


@pytest.mark.parametrize("unit,script", [("kronk-backup.service", "backup.sh"),
                                         ("kronk-recordings-archive.service", "archive_recordings.sh"),
                                         ("kronk-backup-check.service", "backup_check.sh")])
def test_services_run_the_repo_scripts_and_wrappers_call_the_one_script(unit, script):
    text = (UNITS / unit).read_text()
    assert f"ExecStart=%h/git-repos/drawsmcgraw/kronk/scripts/{script}\n" in text and "Type=oneshot" in text
    w = ROOT / "scripts" / script
    assert os.access(w, os.X_OK)
    assert re.search(r'exec /usr/bin/python3 "\$\(dirname "\$0"\)/backups.py" (config|archive|check) "\$@"', w.read_text())


def test_the_operations_block_has_install_run_and_remove():
    text = (UNITS / "kronk-backup.service").read_text()
    for word in ("# Operations", "install:", "enable --now", "dry run:", "run now:", "pause:", "log:", "remove:"):
        assert word in text


# ═══ GET /api/backups (criterion 13) ═══════════════════════════════════════

sys.path.insert(0, str(ROOT / "orchestrator"))
import backups_api  # noqa: E402


def api(tmp_path, raw=None, text=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    p = tmp_path / "status.json"
    if raw is not None:
        p.write_text(json.dumps(raw))
    if text is not None:
        p.write_text(text)
    backups_api.STATUS_FILE = str(p)
    app = FastAPI()
    app.include_router(backups_api.router)
    return TestClient(app).get("/api/backups").json()


def test_api_without_a_status_file_warns(tmp_path):
    """Note 17: no status file at all is not a green card."""
    r = api(tmp_path)
    assert r["present"] is False and r["warnings"] == [
        "no backup job has written a status file yet (the timers are not installed, or no job has ever run)"]


def test_api_a_job_that_never_succeeded_is_a_warning_and_a_string_is_one_warning(tmp_path):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    raw = {"config": {"last": {"at": now, "result": "incomplete", "detail": "HA missing"}},
           "check": {"at": now, "warnings": "destination /mnt/nas-backup is not a mounted filesystem"}}
    w = api(tmp_path, raw)["warnings"]
    assert "the config backup has never finished a good run" in w
    assert "the recordings archive has never finished a good run" in w
    assert any(x.startswith("the last config backup incomplete") for x in w)
    assert "destination /mnt/nas-backup is not a mounted filesystem" in w and "d" not in w
    raw["check"]["warnings"] = 5
    assert any("warnings are unreadable (int)" in x for x in api(tmp_path, raw)["warnings"])


def test_api_unreadable_is_a_warning(tmp_path):
    assert "backup status file unreadable" in api(tmp_path, text="{nope")["warnings"][0]
    assert "not a JSON object" in api(tmp_path, text="[1]")["warnings"][0]


def test_api_warns_on_a_failed_run_a_stale_success_and_the_checks_findings(tmp_path):
    now = dt.datetime.now(dt.timezone.utc)
    raw = {"limits": {"config_days": 35, "archive_days": 9},
           "config": {"last": {"at": (now - dt.timedelta(days=40)).isoformat(), "result": "ok", "size": 6_000_000},
                      "last_ok_at": (now - dt.timedelta(days=40)).isoformat()},
           "archive": {"last": {"at": now.isoformat(), "result": "failed", "detail": "destination full"},
                       "last_ok_at": now.isoformat()},
           "check": {"at": now.isoformat(), "warnings": ["destination /mnt/nas-backup is not a mounted filesystem"]}}
    w = api(tmp_path, raw)["warnings"]
    assert any("the last good config backup was 40 days ago (limit 35)" in x for x in w)
    assert any("the last recordings archive failed" in x and "destination full" in x for x in w)
    assert "destination /mnt/nas-backup is not a mounted filesystem" in w


def test_api_quiet_when_all_is_well(tmp_path):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    raw = {"config": {"last": {"at": now, "result": "ok", "unreadable": []}, "last_ok_at": now},
           "archive": {"last": {"at": now, "result": "ok", "moved": 3, "remaining": 4}, "last_ok_at": now},
           "check": {"at": now, "warnings": []}}
    r = api(tmp_path, raw)
    assert r["warnings"] == [] and r["jobs"]["archive"]["last"]["moved"] == 3


def test_api_only_reads():
    assert [(r.path, sorted(r.methods)) for r in backups_api.router.routes] == [("/api/backups", ["GET"])]
    src = (ROOT / "orchestrator" / "backups_api.py").read_text()
    assert "write_" not in src and "open(" not in src
    assert "backups_api.router" in (ROOT / "orchestrator" / "main.py").read_text()
    assert "/api/backups" in (ROOT / "orchestrator" / "static" / "services.html").read_text()


# ═══ Second end-to-end test (docs/sprint/M16-e2e2.md) ═══════════════════

def no_link(err=errno.EPERM):
    def link(*a, **k):
        raise OSError(err, os.strerror(err))
    return link


def force_placement(monkeypatch, path, err=errno.EPERM):
    """link: the normal path; renameat2: link(2) refused as on a share without
    hard links; rename: that, and no renameat2 either."""
    if path == "link":
        return
    monkeypatch.setattr(B.os, "link", no_link(err))
    if path == "rename":
        monkeypatch.setattr(B, "RENAMEAT2", None)
    else:
        assert B.RENAMEAT2 is not None, "this libc has no renameat2"


PLACED_BY = {"link": "link(2)", "renameat2": "renameat2(RENAME_NOREPLACE)", "rename": "rename after a check"}


def placement_line(ops, path, n):
    want = {k: (n if k == PLACED_BY[path] else 0) for k in B.PLACED}
    return "placement this run: " + ", ".join(f"{k} {v}" for k, v in want.items())


@pytest.mark.parametrize("path", ["link", "renameat2", "rename"])
@pytest.mark.parametrize("point", ["after-verify", "before-container"])
def test_fix2_finding1_a_destination_copy_rewritten_in_place_after_its_verify_is_kept(house, monkeypatch, point, path):
    """Blocker: same inode, same size, other bytes. The old code deleted the local file."""
    a = rec(house, "dev1", "kronk", "run1", name="01_a.wav", data=b"a" * 4000)
    b = rec(house, "dev1", "kronk", "run2", name="01_b.wav", data=b"b" * 4000)
    ta, tb = archived(house, a), archived(house, b)
    force_placement(monkeypatch, path)
    done = []

    def rewrite():
        st = ta.stat()
        with open(ta, "r+b") as w:
            w.write(b"X" * 4000)
        after = ta.stat()
        assert (after.st_ino, after.st_size) == (st.st_ino, st.st_size)
        done.append(1)
    ops = FakeOps()
    if point == "after-verify":
        real = B.copy_verified

        def then_rewrite(src_fd, dst_fd, r, month):
            out = real(src_fd, dst_fd, r, month)
            if r.rel[3] == "01_a.wav":
                rewrite()
            return out
        monkeypatch.setattr(B, "copy_verified", then_rewrite)
    else:
        real_run = ops.run

        def run(argv, timeout=300, stdin=None):
            if argv[:2] == ["sh", "-c"] and argv[2] == B.DELETE_SCRIPT:
                rewrite()                                   # the moment before the container starts
            return real_run(argv, timeout, stdin)
        ops.run = run
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert done == [1]
    assert a.read_bytes() == b"a" * 4000                       # the recording survives, locally
    assert ta.read_bytes() == b"X" * 4000
    assert not b.exists() and tb.read_bytes() == b"b" * 4000   # the other one moved
    body = ops.notices[-1][1]
    assert "local delete refused (the destination copy's sha256 differs from the copied file)" in body
    assert body.startswith("1 problem(s): ") and "Moved 1 recording(s)" in body
    assert placement_line(ops, path, 2) in ops.logs


@pytest.mark.parametrize("err", [errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS])
@pytest.mark.parametrize("path", ["renameat2", "rename"])
def test_fix2_finding2_without_hard_links_the_copy_is_still_placed(house, monkeypatch, err, path):
    """Mutants v15 (EPERM no longer selects the fallback) and x11 (the rename removed)."""
    p = rec(house, "dev1", "kronk", "1", data=b"a" * 3000)
    data, mtime, t = p.read_bytes(), p.stat().st_mtime_ns, archived(house, p)
    force_placement(monkeypatch, path, err)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0, ops.notices[-1][1]
    assert t.read_bytes() == data and t.stat().st_mtime_ns == mtime and stat.S_IMODE(t.stat().st_mode) == 0o600
    assert not p.exists() and [f.name for f in t.parent.iterdir()] == [t.name]      # no temporary file left
    assert placement_line(ops, path, 1) in ops.logs


@pytest.mark.parametrize("err", [errno.EACCES, errno.EIO])
def test_fix2_finding2_other_link_errors_keep_the_file(house, monkeypatch, err):
    p = rec(house, "dev1", "kronk", "1", data=b"a" * 3000)
    monkeypatch.setattr(B.os, "link", no_link(err))
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert p.exists() and [f for f in house.dest.rglob("*") if f.is_file()] == []
    assert "cannot put the copy in place" in ops.notices[-1][1]


@pytest.mark.parametrize("path", ["renameat2", "rename"])
def test_fix2_finding2_without_hard_links_a_final_name_that_exists_is_never_replaced(house, monkeypatch, path):
    """Mutant v14 (the fresh check before the rename removed)."""
    p = rec(house, "dev1", "kronk", "1", data=b"a" * 100)
    t = archived(house, p)
    force_placement(monkeypatch, path)
    real = B._write_all

    def and_someone_writes(fd, data):
        real(fd, data)
        t.write_bytes(b"appeared")
    monkeypatch.setattr(B, "_write_all", and_someone_writes)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert t.read_bytes() == b"appeared" and p.read_bytes() == b"a" * 100
    assert [f.name for f in t.parent.iterdir()] == [t.name]
    assert "appeared on the destination during the copy (both kept)" in ops.notices[-1][1]


def test_fix2_note8_renameat2_refuses_a_name_that_appears_after_every_check(house, monkeypatch):
    """The fallback's window, closed: the name appears right before the rename itself."""
    p = rec(house, "dev1", "kronk", "1", data=b"a" * 100)
    t = archived(house, p)
    force_placement(monkeypatch, "renameat2")
    real = B.rename_noreplace

    def late(old, new, fd):
        t.write_bytes(b"late")
        return real(old, new, fd)
    monkeypatch.setattr(B, "rename_noreplace", late)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert t.read_bytes() == b"late" and p.read_bytes() == b"a" * 100
    assert [f.name for f in t.parent.iterdir()] == [t.name]
    assert "appeared on the destination during the copy (both kept)" in ops.notices[-1][1]


@pytest.mark.parametrize("e", [errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP])
def test_fix2_note8_a_filesystem_without_renameat2_falls_back_to_the_checked_rename(house, monkeypatch, e):
    p = rec(house, "dev1", "kronk", "1", data=b"a" * 100)
    t = archived(house, p)
    monkeypatch.setattr(B.os, "link", no_link())
    import ctypes

    def refuses(*a):
        ctypes.set_errno(e)
        return -1
    monkeypatch.setattr(B, "RENAMEAT2", refuses)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0, ops.notices[-1][1]
    assert t.read_bytes() == b"a" * 100 and not p.exists()
    assert placement_line(ops, "rename", 1) in ops.logs


def test_fix2_rename_noreplace_itself(tmp_path):
    d = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        (tmp_path / "a").write_bytes(b"a")
        (tmp_path / "b").write_bytes(b"b")
        with pytest.raises(FileExistsError):
            B.rename_noreplace("a", "b", d)
        assert (tmp_path / "a").read_bytes() == b"a" and (tmp_path / "b").read_bytes() == b"b"
        assert B.rename_noreplace("a", "c", d) is True
        assert not (tmp_path / "a").exists() and (tmp_path / "c").read_bytes() == b"a"
    finally:
        os.close(d)


@pytest.mark.parametrize("path", ["link", "renameat2", "rename"])
def test_fix2_finding2_the_placed_file_must_be_the_one_written(house, monkeypatch, path):
    """Mutant v12: whatever ends up under the final name is checked to be ours."""
    p = rec(house, "dev1", "kronk", "1", data=b"a" * 100)
    t = archived(house, p)
    force_placement(monkeypatch, path)

    def swap():
        n = t.with_name("other")
        n.write_bytes(b"a" * 100)                           # same bytes, another file
        os.replace(n, t)
    if path == "link":
        real_link = os.link

        def link(*a, **k):
            real_link(*a, **k)
            swap()
        monkeypatch.setattr(B.os, "link", link)
    elif path == "renameat2":
        real = B.rename_noreplace
        monkeypatch.setattr(B, "rename_noreplace", lambda o, n, fd: (real(o, n, fd), swap())[0])
    else:
        real_rename = os.rename

        def rename(a, b, **kw):
            real_rename(a, b, **kw)
            swap()
        monkeypatch.setattr(B.os, "rename", rename)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert p.read_bytes() == b"a" * 100 and ops.removed == []
    assert "the archived copy is not the file that was written; kept locally" in ops.notices[-1][1]


def test_fix2_finding3_every_helper_mount_is_a_bind_mount_that_needs_its_source(tmp_path, monkeypatch):
    monkeypatch.delenv("KRONK_ARCHIVE_DELETE", raising=False)
    seen = []

    class Capture(B.Ops):
        def run(self, argv, timeout=300, stdin=None):
            seen.append(argv)
            return B.Result(0, b"", "")
    Capture().helper_tar(tmp_path)
    Capture().remove_recordings(tmp_path / "rec", tmp_path / "dst", [vrec(["d", "p", "r", "x"])])
    for argv in seen:
        assert "-v" not in argv and "--volume" not in argv
        mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
        assert mounts and all(m.startswith("type=bind,source=") for m in mounts)
    assert [argv[argv.index("--mount") + 1] for argv in seen][0].endswith(",target=/src,readonly")


def test_fix2_finding4_a_production_tag_that_is_not_a_local_ref_is_left_out_and_named(house):
    ops = FakeOps()
    ops.local_tags = set()                                  # docker ps names sprint-M16; no such local tag
    ops.heads = b"0123abc HEAD\n0123abc refs/heads/0123abc\n"
    assert B.cmd_config(house, ops, dry_run=False) == 0
    create = next(a for a in ops.commands if a[0] == "git" and "create" in a)
    assert "sprint-M16" not in " ".join(create) and "refs/tags/sprint-M16" not in create
    p = house.dest / "config" / "kronk-config-2026-12-10.tar.gz"
    assert read_member(p, "repo-unpushed.bundle") == ops.bundle
    state = read_member(p, "repo-state.txt").decode()
    assert ("unpushed bundle: repo-unpushed.bundle: 3 commit(s) no remote-tracking branch has, "
            f"{len(ops.bundle)} bytes; refs in it: HEAD, refs/heads/0123abc; left out, not a local tag: sprint-M16") in state


def test_fix2_finding4_a_bundle_error_in_the_notice_is_one_line(house):
    ops = FakeOps()
    real = ops.run

    def run(argv, timeout=300, stdin=None):
        if argv[0] == "git" and "create" in argv:
            return B.Result(128, b"", "fatal: ambiguous argument 'x': unknown revision\nUse '--' to separate paths\n"
                                      "usage: git bundle create [<options>] <file> <git-rev-list args>")
        return real(argv, timeout, stdin)
    ops.run = run
    assert B.cmd_config(house, ops, dry_run=False) == 0
    body = ops.notices[-1][1]
    assert "NOT WRITTEN: git bundle failed (128): fatal: ambiguous argument 'x': unknown revision)." in body
    assert "\n" not in body and "usage" not in body


def test_fix2_finding4_one_line():
    assert B.one_line("\n\n  first  \nsecond") == "first" and B.one_line("") == "" and B.one_line("x" * 300, 5) == "xxxxx"


def test_fix2_finding4_and_note13_a_real_bundle_holds_the_refs_it_names(tmp_path, monkeypatch):
    """Real git, in a temporary repository with its own user; no remote, no network."""
    import subprocess
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path / "repo"
    repo.mkdir()

    def g(*a):
        return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True).stdout.decode()
    g("init", "-q", "-b", "main")
    g("config", "user.name", "M16 test")
    g("config", "user.email", "m16-test@example.invalid")
    (repo / "f").write_text("x\n")
    g("add", "f")
    g("commit", "-q", "-m", "one")
    g("tag", "sprint-M17")
    s = B.Settings(dest=tmp_path, require_mount=False, state=tmp_path / "state", repo=repo, tmp=tmp_path)
    data, note, n = B.unpushed_bundle(s, B.Ops(log=lambda m: None), "main", ["sprint-M17", "sprint-M18"])
    assert data is not None and n == 1
    assert note == (f"repo-unpushed.bundle: 1 commit(s) no remote-tracking branch has, {len(data)} bytes; refs in it: "
                    "HEAD, refs/heads/main, refs/tags/sprint-M17; left out, not a local tag: sprint-M18")
    (tmp_path / "b.bundle").write_bytes(data)
    heads = g("bundle", "list-heads", str(tmp_path / "b.bundle")).split()
    assert heads[1::2] == ["HEAD", "refs/heads/main", "refs/tags/sprint-M17"]
    assert not any(p.name.startswith("kronk-bundle-") for p in tmp_path.iterdir())     # its directory is gone


def test_fix2_note14_one_commit_not_pushed(house):
    ops = FakeOps()
    ops.unpushed = 1
    B.cmd_config(house, ops, dry_run=False)
    assert "Repo: 1 commit not pushed (the tar holds them as repo-unpushed.bundle)." in ops.notices[-1][1]


def test_fix2_finding5_the_defaults(monkeypatch):
    """Mutants a05 (archive age), r07 (tars kept), n14 (stale days)."""
    for n in ("KRONK_ARCHIVE_AGE_DAYS", "KRONK_BACKUP_KEEP", "KRONK_BACKUP_STALE_DAYS"):
        monkeypatch.delenv(n, raising=False)
    a = type("A", (), {"dest": "/tmp/x", "require_mount": False})()
    s = B.Settings.load(a)
    assert (s.age_days, s.keep, s.stale_days) == (7, 12, 35)
    s = B.Settings(dest=Path("/tmp/x"), require_mount=False, state=Path("/tmp/x"))
    assert (s.age_days, s.keep, s.stale_days) == (7, 12, 35)


def test_fix2_finding5_a_symlinked_directory_in_a_tree_is_stored_not_followed(house, tmp_path):
    """Mutants t14 (dropped) and t25 (followed)."""
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (outside / "not-ours.txt").write_text("outside the tree\n")
    (house.live_config / "prod" / "shared").symlink_to(outside)
    B.cmd_config(house, FakeOps(), dry_run=False)
    p = house.dest / "config" / "kronk-config-2026-12-10.tar.gz"
    top = "kronk-config-2026-12-10/live-config/prod/shared"
    with tarfile.open(p, "r:gz") as t:
        m = t.getmember(top)
        assert m.issym() and m.linkname == str(outside)
        assert not any(n.startswith(top + "/") for n in t.getnames())


def test_fix2_finding5_a_symlink_in_has_tar_stream_is_kept(house):
    """Mutant t26."""
    ops = FakeOps()
    buf = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(ops.ha)) as src, tarfile.open(fileobj=buf, mode="w") as out:
        for m in src:
            out.addfile(m, src.extractfile(m) if m.isfile() else None)
        ti = tarfile.TarInfo("./themes")
        ti.type, ti.linkname = tarfile.SYMTYPE, "/config/shared-themes"
        out.addfile(ti)
    ops.ha = buf.getvalue()
    B.cmd_config(house, ops, dry_run=False)
    with tarfile.open(house.dest / "config" / "kronk-config-2026-12-10.tar.gz", "r:gz") as t:
        m = t.getmember("kronk-config-2026-12-10/ha/config/themes")
        assert m.issym() and m.linkname == "/config/shared-themes"


def test_fix2_finding5_sqlite_is_read_through_the_online_backup(house, tmp_path):
    """Mutant t22: the rows are still in the WAL; a raw read of the file has none of them."""
    import sqlite3
    db = house.repo / "data" / "solar.db"
    db.unlink()
    w = sqlite3.connect(db)
    try:
        w.execute("pragma journal_mode=wal")
        w.execute("pragma wal_autocheckpoint=0")
        w.execute("create table readings (x)")
        w.executemany("insert into readings values (?)", [(i,) for i in range(100)])
        w.commit()
        raw = tmp_path / "raw.db"
        raw.write_bytes(db.read_bytes())                 # what a raw read gets: the main file, no WAL
        c = sqlite3.connect(raw)
        assert c.execute("select count(*) from sqlite_master").fetchone()[0] == 0
        c.close()
        assert B.cmd_config(house, FakeOps(), dry_run=False) == 0
    finally:
        w.close()
    got = tmp_path / "got.db"
    got.write_bytes(read_member(house.dest / "config" / "kronk-config-2026-12-10.tar.gz", "repo/data/solar.db"))
    c = sqlite3.connect(got)
    try:
        assert c.execute("select count(*) from readings").fetchone()[0] == 100
    finally:
        c.close()


def test_fix2_finding6_the_runbook():
    text = (ROOT / "docs" / "runbooks" / "restore.md").read_text()
    step4 = text[text.index("### 4."):text.index("### 5.")]
    after = step4[step4.index("sudo reboot"):]
    assert re.search(r"^T=kronk-config-", after, re.M) and re.search(r"^R=~/restore/\$T", after, re.M) \
        and re.search(r"^K=", after, re.M)
    step16 = text[text.index("### 16."):text.index("## Restore one thing only")]
    assert "~/restore/ha-storage-before-restore.tgz" in step16


@pytest.mark.parametrize("job", ["config", "archive"])
def test_fix2_note7_a_job_removes_its_own_leftovers_by_exact_signature_only(house, job):
    ops = FakeOps()
    ops.leftover_ids = ["0123456789ab", "--force", "abc;rm", "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"]
    tmp = house.tmp
    day_ago = (dt.datetime.now() - dt.timedelta(days=2)).timestamp()

    def bundle_dir(name, files=("unpushed.bundle",), old=True):
        d = tmp / name
        d.mkdir()
        for f in files:
            (d / f).write_bytes(b"bundle")
        if old:
            os.utime(d, (day_ago, day_ago))
        return d
    own = bundle_dir("kronk-bundle-ab_cd123", ("unpushed.bundle", "unpushed.bundle.lock"))
    empty = bundle_dir("kronk-bundle-empty123", ())
    young = bundle_dir("kronk-bundle-young123", old=False)
    other = bundle_dir("kronk-bundle-other123", ("unpushed.bundle", "notes.txt"))
    short = bundle_dir("kronk-bundle-abc")
    upper = bundle_dir("kronk-bundle-ABCDEFGH")
    target = bundle_dir("not-a-bundle-dir")
    link = tmp / "kronk-bundle-link1234"
    link.symlink_to(target)
    os.utime(link, (day_ago, day_ago), follow_symlinks=False)
    plain = tmp / "kronk-bundle-file1234"
    plain.write_bytes(b"x")
    os.utime(plain, (day_ago, day_ago))
    if job == "config":
        B.cmd_config(house, ops, dry_run=False)
    else:
        B.cmd_archive(house, ops, dry_run=False)
    ps = ["docker", "ps", "-a", "--filter", "label=kronk.backup.helper=1", "--filter", "status=created",
          "--format", "{{.ID}}"]
    assert ops.commands[0] == ps                             # first thing after the lock
    rms = [a for a in ops.commands if a[:2] == ["docker", "rm"]]
    assert rms == [["docker", "rm", "0123456789ab", "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"]]
    assert not own.exists() and not empty.exists()
    for keep in (young, other, short, upper, target, link, plain):
        assert os.path.lexists(keep), keep
    assert (other / "notes.txt").exists() and (other / "unpushed.bundle").exists()
    assert any("left alone: " in m and "other123" in m for m in ops.logs)


def test_fix2_note7_nothing_to_remove_runs_no_rm_and_a_dry_run_looks_at_nothing(house):
    ops = FakeOps()
    B.cmd_config(house, ops, dry_run=False)
    assert not any(a[:2] == ["docker", "rm"] for a in ops.commands)
    ops = FakeOps()
    ops.leftover_ids = ["0123456789ab"]
    B.cmd_config(house, ops, dry_run=True)
    B.cmd_archive(house, ops, dry_run=True)
    assert not any(B.HELPER_LABEL in " ".join(a) for a in ops.commands)


def test_fix2_note7_a_failing_leftover_check_never_fails_the_job(house, monkeypatch):
    def boom(s, ops):
        raise RuntimeError("boom")
    monkeypatch.setattr(B, "_clean_own_leftovers", boom)
    ops = FakeOps()
    assert B.cmd_config(house, ops, dry_run=False) == 0
    assert "leftover check failed: RuntimeError: boom" in ops.logs


# ═══ Third end-to-end report (docs/sprint/M16-e2e3.md), fix round 3 ═══════

def _git_repo(tmp_path, monkeypatch):
    """A temporary repository with its own user; remote-tracking refs made
    with update-ref (no remote, no network)."""
    import subprocess
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path / "repo"
    repo.mkdir()

    def g(*a):
        return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True).stdout.decode()
    g("init", "-q", "-b", "main")
    g("config", "user.name", "M16 test")
    g("config", "user.email", "m16-test@example.invalid")
    return repo, g


def test_fix3_finding1_head_pushed_and_the_production_tag_unpushed_real_git(tmp_path, monkeypatch):
    """The tester's case: c1, c2 on the remote; c3 (tag sprint-T1) and c4 local only; HEAD on c2."""
    repo, g = _git_repo(tmp_path, monkeypatch)
    for i in (1, 2, 3, 4):
        (repo / "f").write_text(f"{i}\n")
        g("add", "f")
        g("commit", "-q", "-m", f"c{i}")
        if i == 2:
            g("update-ref", "refs/remotes/origin/main", "HEAD")
        if i == 3:
            g("tag", "sprint-T1")
    g("checkout", "-q", "-b", "side", "origin/main")
    s = B.Settings(dest=tmp_path, require_mount=False, state=tmp_path / "state", repo=repo, tmp=tmp_path)
    ops = B.Ops(log=lambda m: None)
    data, note, n = B.unpushed_bundle(s, ops, "side", ["sprint-T1"])
    assert data is not None and n == 1
    assert note.startswith("repo-unpushed.bundle: 1 commit(s) no remote-tracking branch has") and \
        note.endswith("refs in it: refs/tags/sprint-T1")
    (tmp_path / "b.bundle").write_bytes(data)
    assert g("bundle", "list-heads", str(tmp_path / "b.bundle")).split()[1::2] == ["refs/tags/sprint-T1"]
    assert B.tag_on_remote(s, ops, "sprint-T1").startswith("no: its commit")
    # Nothing unpushed anywhere: no bundle, and the note says the tags were counted.
    g("update-ref", "refs/remotes/origin/main", "main")
    assert B.unpushed_bundle(s, ops, "side", ["sprint-T1"]) == (
        None, "none needed: every commit of HEAD and of the production tags is on a remote-tracking branch", 0)


def _count_by_refs(ops, head=0, tag=2, upstream_fails=False, remotes_fails=False):
    """git rev-list answers: HEAD alone `head`, HEAD with the production tag `tag`."""
    real = ops.run

    def run(argv, timeout=300, stdin=None):
        if argv[0] == "git" and "rev-list" in argv:
            if argv[-1] == "--remotes":
                if remotes_fails:
                    return B.Result(128, b"", "fatal: bad object refs/remotes/x\nmore text")
                return B.Result(0, f"{tag if 'refs/tags/sprint-M16' in argv else head}\n".encode(), "")
            if upstream_fails:
                return B.Result(128, b"", f"fatal: bad revision '{argv[-1]}'")
        return real(argv, timeout, stdin)
    ops.run = run
    return ops


def test_fix3_finding1_the_notice_names_an_unpushed_production_tag_bundle_or_not(house):
    ops = _count_by_refs(FakeOps())
    ops.unpushed = 0                                   # HEAD is pushed
    assert B.cmd_config(house, ops, dry_run=False) == 0
    p = house.dest / "config" / "kronk-config-2026-12-10.tar.gz"
    assert read_member(p, "repo-unpushed.bundle") == ops.bundle
    create = next(a for a in ops.commands if a[0] == "git" and "create" in a)
    assert "refs/tags/sprint-M16" in create
    body = ops.notices[-1][1]
    assert ("Repo: 2 commits not pushed (the tar holds them as repo-unpushed.bundle). Production tag sprint-M16: "
            "its commit is on no remote-tracking branch (in the tar's repo-unpushed.bundle).") in body
    state = read_member(p, "repo-state.txt").decode()
    assert "on no remote-tracking branch (HEAD and the production tags): 2 commit(s)" in state
    # Over the cap: no bundle, and still one sentence about the tag.
    ops = _count_by_refs(FakeOps())
    ops.bundle = b"x" * 2_000_000
    house.bundle_max_bytes = 1_000_000
    assert B.cmd_config(house, ops, dry_run=False) == 0
    body = ops.notices[-1][1]
    assert "Production tag sprint-M16: its commit is on no remote-tracking branch (NOT in the tar: push it)." in body
    assert "they exist only on this machine: OMITTED: 2 commit(s) unpushed" in body and "\n" not in body


def test_fix3_finding2_a_local_only_branch_and_every_bundle_note_reach_the_notice(house):
    ops = _count_by_refs(FakeOps(), head=2, tag=2, upstream_fails=True)
    B.cmd_config(house, ops, dry_run=False)
    assert "Repo: 2 commits not pushed (the tar holds them as repo-unpushed.bundle)." in ops.notices[-1][1]
    ops = _count_by_refs(FakeOps(), head=3, tag=3, upstream_fails=True)
    ops.bundle = b"x" * 2_000_000
    house.bundle_max_bytes = 1_000_000
    B.cmd_config(house, ops, dry_run=False)
    assert ("Repo: 3 commits not pushed (they exist only on this machine: OMITTED: 3 commit(s) unpushed, but the "
            "bundle is 2.0 MB, over the 1.0 MB cap") in ops.notices[-1][1]
    ops = _count_by_refs(FakeOps(), upstream_fails=True, remotes_fails=True)
    B.cmd_config(house, ops, dry_run=False)
    body = ops.notices[-1][1]
    assert ("Repo: unpushed commits unknown ((git rev-list --count HEAD refs/tags/sprint-M16 --not --remotes failed: "
            "fatal: bad object refs/remotes/x)).") in body
    assert "\n" not in body and "more text" not in body


def test_fix3_note12_the_notice_counts_uncommitted_and_untracked_paths(house):
    ops = FakeOps()
    real = ops.run

    def run(argv, timeout=300, stdin=None):
        if argv[0] == "git" and "status" in argv:
            return B.Result(0, b" M scripts/backups.py\n?? notes.txt\n", "")
        return real(argv, timeout, stdin)
    ops.run = run
    B.cmd_config(house, ops, dry_run=False)
    body = ops.notices[-1][1]
    assert "Working tree: 2 uncommitted or untracked paths, not in the tar." in body
    assert "notes.txt" not in body and "backups.py" not in body           # a count only
    ops = FakeOps()
    B.cmd_config(house, ops, dry_run=False)
    assert "Working tree" not in ops.notices[-1][1]


def test_fix3_finding3_a_check_waiting_for_the_mount_never_writes_back_a_stale_archive_entry(house):
    """The tester's interleaving: the check waits for the mount; an archive run ends meanwhile."""
    house.require_mount, house.mount_wait_s = True, 20
    ops = FakeOps()
    tick = ops.sleep

    def sleep(sec):
        if ops.t == NOW:
            B.save_run(house, FakeOps(), "archive", {"at": B.iso(NOW), "result": "ok", "finished": B.iso(NOW)})
        tick(sec)
    ops.sleep = sleep
    assert B.cmd_check(house, ops) == 1
    st = json.loads((house.state / "status.json").read_text())
    assert st["archive"]["last"]["result"] == "ok" and st["archive"]["last_ok_at"] == B.iso(NOW)
    assert any("is not a mounted filesystem" in w for w in st["check"]["warnings"])
    assert not any("never finished a run" in w for w in st["check"]["warnings"])      # read after the wait


@pytest.mark.parametrize("who", ["save_run", "check", "bad-setting"])
def test_fix3_finding3_every_status_write_is_read_modify_write_under_the_status_lock(house, who):
    import threading
    house.state.mkdir(parents=True)
    fd = os.open(house.state / "status.lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    failed = []

    def job():
        try:
            if who == "save_run":
                B.save_run(house, FakeOps(), "config", {"at": B.iso(NOW), "result": "declined", "detail": "x"})
            elif who == "check":
                B.cmd_check(house, FakeOps())
            else:
                house.errors = ["KRONK_BACKUP_KEEP must be a whole number, not 'x'"]
                B.bad_settings(house, FakeOps(), "check")
        except BaseException as e:      # noqa: BLE001
            failed.append(e)
    t = threading.Thread(target=job)
    t.start()
    try:
        t.join(0.5)
        assert t.is_alive() and not (house.state / "status.json").exists()
        # Another job's entry lands while this one waits: it must survive.
        B.write_json(house.state / "status.json", {"schema": 1, "archive": {"last_ok_at": B.iso(NOW)}})
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    t.join(10)
    assert not t.is_alive() and not failed
    st = json.loads((house.state / "status.json").read_text())
    assert st["archive"] == {"last_ok_at": B.iso(NOW)}
    assert ("config" if who == "save_run" else "check") in st


def test_fix3_finding4_the_backup_lock_is_exclusive(house):
    house.lock_wait_min = 0
    fd, why = B.take_lock(house, FakeOps(), "config")
    try:
        assert fd is not None and why == ""
        fd2, why2 = B.take_lock(house, FakeOps(), "archive")
        assert fd2 is None and "another backup job holds the lock (config pid=" in why2
    finally:
        os.close(fd)
    fd3, _ = B.take_lock(house, FakeOps(), "archive")
    assert fd3 is not None
    os.close(fd3)


def test_fix3_finding4_the_cleanup_never_follows_a_symlink_or_unlinks_a_non_file(house):
    day_ago = (dt.datetime.now() - dt.timedelta(days=2)).timestamp()
    outside = house.repo.parent / "outside-dir"
    outside.mkdir()
    (outside / "unpushed.bundle").write_bytes(b"keep me")
    os.utime(outside, (day_ago, day_ago))
    link = house.tmp / "kronk-bundle-link5678"          # named like one, a symlink to an old bundle-only directory
    link.symlink_to(outside)
    os.utime(link, (day_ago, day_ago), follow_symlinks=False)
    inner = house.tmp / "kronk-bundle-inner567"         # a real one whose "bundle" is a symlink
    inner.mkdir()
    (inner / "unpushed.bundle").symlink_to(outside / "unpushed.bundle")
    os.utime(inner, (day_ago, day_ago))
    ops = FakeOps()
    B.clean_own_leftovers(house, ops)
    assert (outside / "unpushed.bundle").read_bytes() == b"keep me"
    assert link.is_symlink() and (inner / "unpushed.bundle").is_symlink()
    assert any("left alone: " in m and "inner567" in m for m in ops.logs)


def test_fix3_finding4_and_note14b_no_probe_in_a_dry_run_or_the_check_and_a_run_removes_only_its_own(house):
    rec(house, "dev1", "kronk", "1")
    opened = []
    real = os.open
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(B.os, "open", lambda p, *a, **k: (opened.append(str(p)), real(p, *a, **k))[1])
        B.cmd_config(house, FakeOps(), dry_run=True)
        B.cmd_archive(house, FakeOps(), dry_run=True)
        B.cmd_check(house, FakeOps())
        assert not any(B.PROBE in p for p in opened)
        B.cmd_config(house, FakeOps(), dry_run=False)
        assert any(B.PROBE in p for p in opened)              # a real run does probe
    theirs = house.dest / ".kronk-write-probe"
    theirs.write_bytes(b"not the job's")
    assert B.cmd_config(house, FakeOps(), dry_run=False) == 0
    assert B.cmd_archive(house, FakeOps(), dry_run=False) == 0
    assert theirs.read_bytes() == b"not the job's"
    assert [f.name for f in house.dest.iterdir() if f.name.startswith(B.PROBE)] == [".kronk-write-probe"]


def test_fix3_finding4_the_temporary_tar_is_0600_and_config_0700(house, monkeypatch):
    seen = {}
    real = B.verify_tar

    def spy(p, sha, n):
        seen["mode"] = stat.S_IMODE(os.lstat(p).st_mode)
        return real(p, sha, n)
    monkeypatch.setattr(B, "verify_tar", spy)
    old = os.umask(0o022)
    try:
        assert B.cmd_config(house, FakeOps(), dry_run=False) == 0
    finally:
        os.umask(old)
    assert seen["mode"] == 0o600 and stat.S_IMODE((house.dest / "config").stat().st_mode) == 0o700


def test_fix3_finding4_a_final_tar_name_that_appears_during_the_run_is_never_overwritten(house, monkeypatch):
    real = B.verify_tar

    def spy(p, sha, n):
        (p.parent / p.name[1:-len(".tmp")]).write_bytes(b"someone else's tar")
        return real(p, sha, n)
    monkeypatch.setattr(B, "verify_tar", spy)
    ops = FakeOps()
    assert B.cmd_config(house, ops, dry_run=False) == 1
    cfg = house.dest / "config"
    assert sorted(f.name for f in cfg.iterdir()) == ["kronk-config-2026-12-10.tar.gz"]       # the temporary one removed
    assert (cfg / "kronk-config-2026-12-10.tar.gz").read_bytes() == b"someone else's tar"
    assert ops.notices[-1][0] == "Kronk backup: config backup FAILED"
    assert "appeared during the run; not overwritten" in ops.notices[-1][1]


def test_fix3_finding4_does_not_fit_keeps_the_smallest_size_that_failed(house, monkeypatch):
    rec(house, "dev1", "kronk", "1", data=b"a" * 300)
    rec(house, "dev1", "kronk", "2", data=b"b" * 100)
    rec(house, "dev1", "kronk", "3", data=b"c" * 200)
    tried = []

    def full(src_fd, dst_fd, r, month):
        tried.append(r.size)
        return "full", "destination full: No space left on device", None
    monkeypatch.setattr(B, "copy_verified", full)
    assert B.cmd_archive(house, FakeOps(), dry_run=False) == 1
    assert tried == [300, 100]
    problems = json.loads((house.state / "status.json").read_text())["archive"]["last"]["problems"]
    assert any(x.startswith("dev1/kronk/3/") and "skipped, does not fit (a 100 B file did not)" in x for x in problems)


def test_fix3_finding4_a_check_notice_that_fails_is_recorded_as_not_sent(house):
    ops = FakeOps()
    ops.notify_ok = False
    assert B.cmd_check(house, ops) == 1 and len(ops.notices) == 1
    assert json.loads((house.state / "status.json").read_text())["check"]["notified"] == {}
    ops = FakeOps(NOW + dt.timedelta(hours=1))              # the same warnings: sent again, since none got out
    B.cmd_check(house, ops)
    assert len(ops.notices) == 1
    assert json.loads((house.state / "status.json").read_text())["check"]["notified"]["at"] == B.iso(ops.t)


def test_fix3_finding4_a_temporary_file_replaced_before_the_read_back_is_noticed(house, monkeypatch):
    p = rec(house, "dev1", "kronk", "1")
    data = p.read_bytes()
    target = archived(house, p)
    real = os.open
    swapped = []

    def spy(path, flags, *a, **k):
        if (not swapped and flags == B.O_FILE and isinstance(path, str) and path.startswith(B.ARCHIVE_TMP_PREFIX)
                and "dir_fd" in k):
            d = k["dir_fd"]
            fd = real("other", os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=d)
            os.write(fd, data)                              # the same bytes: only the inode tells
            os.close(fd)
            os.rename("other", path, src_dir_fd=d, dst_dir_fd=d)
            swapped.append(path)
        return real(path, flags, *a, **k)
    monkeypatch.setattr(B.os, "open", spy)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    monkeypatch.undo()
    assert swapped and p.read_bytes() == data and not target.exists()
    assert (target.parent / swapped[0]).read_bytes() == data         # someone else's file: left alone
    assert "the temporary file was replaced during the copy; left alone, kept locally" in ops.notices[-1][1]


def test_fix3_finding4_only_a_file_at_the_fourth_level_is_a_recording(house):
    rec(house, "dev1", "kronk", "1")
    for rel in ("x.wav", "dev1/y.wav", "dev1/kronk/z.wav", "dev1/kronk/1/sub/w.wav"):
        f = house.recordings / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"z")
    recs, skipped = B.scan_recordings(house.recordings)
    assert [r.path for r in recs] == ["dev1/kronk/1/01_stt-stt.faster_whisper.wav"]
    where = "not at <device>/<pipeline>/<run>/<file>"
    assert dict(skipped) == {"x.wav": where, "dev1/y.wav": where, "dev1/kronk/z.wav": where,
                             "dev1/kronk/1/sub": "unexpected directory"}


@pytest.mark.parametrize("kind", ["fifo", "directory"])
def test_fix3_finding5_a_sqlite_source_that_is_not_a_regular_file_is_a_gap_not_a_hang(house, kind):
    import signal
    p = house.repo / "data" / "music_taste.db"
    p.unlink()
    os.mkfifo(p) if kind == "fifo" else p.mkdir()

    def hung(signum, frame):
        raise TimeoutError("the config job hung on the SQLite source")
    before = signal.signal(signal.SIGALRM, hung)
    signal.alarm(20)
    try:
        ops = FakeOps()
        rc = B.cmd_config(house, ops, dry_run=False)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, before)
    assert rc == 4
    title, body = ops.notices[-1]
    assert title == "Kronk backup: config backup INCOMPLETE"
    assert f"MISSING REQUIRED (1): {p}: not a regular file." in body


def test_fix3_finding5_a_locked_sqlite_source_is_a_gap_after_the_deadline(house, monkeypatch):
    import sqlite3
    import time
    monkeypatch.setattr(B, "SQLITE_READ_S", 0.5)
    w = sqlite3.connect(house.repo / "data" / "solar.db", isolation_level=None)
    w.execute("begin exclusive")
    w.execute("insert into t values (7)")
    try:
        t0 = time.monotonic()
        ops = FakeOps()
        rc = B.cmd_config(house, ops, dry_run=False)
        assert time.monotonic() - t0 < 15
    finally:
        w.execute("rollback")
        w.close()
    assert rc == 4 and "solar.db: sqlite: still locked after 0.5 s" in ops.notices[-1][1]


def test_fix3_note6_an_mtime_python_cannot_date_is_reported_and_skipped(house):
    bad = rec(house, "dev1", "kronk", "1")
    os.utime(bad, (-70_000_000_000, -70_000_000_000))       # year -249
    good = rec(house, "dev1", "kronk", "2")
    target = archived(house, good)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=True) == 0
    assert "skipped dev1/kronk/1/01_stt-stt.faster_whisper.wav: its mtime cannot be dated" in "\n".join(ops.logs)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert bad.exists() and not good.exists() and target.exists()
    title, body = ops.notices[-1]
    assert title == "Kronk backup: recordings archive FAILED"
    assert "dev1/kronk/1/01_stt-stt.faster_whisper.wav: its mtime cannot be dated (out of range); skipped" in body
    assert "Moved 1 recording(s)" in body


def test_fix3_note7_ha_tar_exit_1_is_read_once_more_after_a_few_seconds(house):
    for second, want in ((0, 0), (1, 4)):
        ops = FakeOps()
        calls = []

        def tar(container, cwd, names, ops=ops, calls=calls, second=second):
            if container == "music-assistant-server":
                return FakeOps.container_tar(ops, container, cwd, names)
            calls.append(ops.t)
            rc = 1 if len(calls) == 1 or second else 0
            return B.Result(rc, ops.ha, "tar: ./.storage/core.entity_registry: file changed as we read it" if rc else "")
        ops.container_tar = tar
        assert B.cmd_config(house, ops, dry_run=False) == want
        assert len(calls) == 2 and calls[1] - calls[0] == dt.timedelta(seconds=B.HA_TAR_RETRY_S)
        assert any("HA's tar exited 1 (tar: ./.storage/core.entity_registry: file changed" in m for m in ops.logs)
    assert ops.notices[-1][0] == "Kronk backup: config backup INCOMPLETE"


def test_fix3_note8_the_delete_container_is_named_and_current_while_it_runs(tmp_path, monkeypatch):
    monkeypatch.delenv("KRONK_ARCHIVE_DELETE", raising=False)
    seen = []

    class Capture(B.Ops):
        def run(self, argv, timeout=300, stdin=None):
            seen.append((argv[argv.index("--name") + 1], self.current_helper))
            return B.Result(0, b"ok 1\n", "")
    ops = Capture(log=lambda m: None)
    ops.remove_recordings(tmp_path / "rec", tmp_path / "dst", [vrec(["d", "p", "r", "x"])])
    name, current = seen[0]
    assert name == current and B.HELPER_NAME_RE.fullmatch(name).group(1) == str(os.getpid())
    assert ops.current_helper is None


def test_fix3_note8_sigterm_removes_the_running_delete_container_by_its_name(monkeypatch):
    import signal
    seen, kills, sigs = [], [], []

    class Capture(B.Ops):
        def run(self, argv, timeout=300, stdin=None):
            seen.append(argv)
            return B.Result(0, b"", "")
    ops = Capture(log=lambda m: None)
    monkeypatch.setattr(B.os, "kill", lambda pid, sig: kills.append((pid, sig)))
    monkeypatch.setattr(B.signal, "signal", lambda sig, h: sigs.append((sig, h)))
    h = B.on_sigterm(ops)
    h(signal.SIGTERM, None)                               # no container running: nothing to remove
    assert seen == [] and kills == [(os.getpid(), signal.SIGTERM)] and sigs == [(signal.SIGTERM, signal.SIG_DFL)]
    ops.current_helper = "kronk-backup-delete-77-0123abcd"
    h(signal.SIGTERM, None)
    assert seen == [["docker", "rm", "-f", "kronk-backup-delete-77-0123abcd"]]
    assert kills[-1] == (os.getpid(), signal.SIGTERM)       # and then it dies of the signal, as before


def test_fix3_note8_main_holds_the_handler_only_around_a_real_run(house, monkeypatch):
    import signal
    monkeypatch.setenv("KRONK_BACKUP_STATE", str(house.state))
    before = signal.getsignal(signal.SIGTERM)
    inside = []
    for name in ("cmd_config", "cmd_archive"):
        monkeypatch.setattr(B, name, lambda s, ops, dry: inside.append((dry, signal.getsignal(signal.SIGTERM))) or 0)
    for cmd in ("config", "archive"):
        B.main([cmd, "--dest", str(house.dest), "--no-notify"], ops=FakeOps())
        B.main([cmd, "--dest", str(house.dest), "--no-notify", "--dry-run"], ops=FakeOps())
    assert [d for d, _ in inside] == [False, True, False, True]
    assert all(getattr(h, "__qualname__", "").startswith("on_sigterm") for d, h in inside if not d)
    assert all(h is before for d, h in inside if d) and signal.getsignal(signal.SIGTERM) is before


def test_fix3_note8_the_next_job_removes_a_running_delete_container_whose_job_is_gone(house):
    import subprocess
    child = subprocess.Popen(["true"])
    child.wait()
    dead = child.pid
    assert B.pid_alive(os.getpid()) and not B.pid_alive(dead)
    ops = FakeOps()
    ops.running_helpers = [f"0123456789ab kronk-backup-delete-{dead}-0123abcd",
                           f"abcdefabcdef kronk-backup-delete-{os.getpid()}-0123abcd",     # its job is alive
                           "fedcba987654 kronk-backup-helper-x",                           # not a delete name
                           f"--force kronk-backup-delete-{dead}-0123abcd",                 # not an id
                           f"111111111111 kronk-backup-delete-{dead}-0123abcdX"]           # not the exact name
    B.clean_own_leftovers(house, ops)
    assert ["docker", "ps", "--filter", "label=kronk.backup.helper=1", "--filter", "status=running",
            "--format", "{{.ID}} {{.Names}}"] in ops.commands
    assert [a for a in ops.commands if a[:2] == ["docker", "rm"]] == [["docker", "rm", "-f", "0123456789ab"]]
    assert any(f"still running after their job ended: kronk-backup-delete-{dead}-0123abcd" in m for m in ops.logs)


def test_fix3_note9_the_stale_check_goes_by_the_status_file_and_the_tar_name_not_the_mtime(house):
    assert B.cmd_config(house, FakeOps(), dry_run=False) == 0
    cfg = house.dest / "config"
    cur = cfg / "kronk-config-2026-12-10.tar.gz"
    st = json.loads((house.state / "status.json").read_text())
    assert st["config"]["last_ok_path"] == str(cur) and st["config"]["last_ok_at"] == B.iso(NOW)
    long_ago = (NOW - dt.timedelta(days=400)).timestamp()
    os.utime(cur, (long_ago, long_ago))                     # copied back with cp -p: an old mtime
    later = FakeOps(NOW + dt.timedelta(days=3))
    assert not any("config backup" in x for x in B.stale_warnings(house, later, st))
    # The two disagree (a run recorded 40 days ago wrote today's name: a wrong clock): the status
    # file wins while its tar is there.
    st40 = {"config": {"last_ok_at": B.iso(NOW - dt.timedelta(days=40)), "last_ok_path": str(cur)}}
    assert any(x.startswith("the newest config backup, kronk-config-2026-12-10.tar.gz, is 40 days old")
               for x in B.stale_warnings(house, FakeOps(), st40))
    newer = cfg / "kronk-config-2026-12-11.tar.gz"         # a tar the status file does not know: logged
    newer.write_bytes(b"t")
    ops = FakeOps()
    B.stale_warnings(house, ops, st)
    assert any("kronk-config-2026-12-11.tar.gz on the share is not the status file's last good run" in m
               for m in ops.logs)
    newer.unlink()
    old = cfg / "kronk-config-2025-06-10.tar.gz"           # a 2025 tar touched now
    old.write_bytes(b"t")
    os.utime(old, (NOW.timestamp(), NOW.timestamp()))
    cur.unlink()                                           # the recorded tar is gone: by the newest name
    ops = FakeOps()
    w = B.stale_warnings(house, ops, st)
    assert any(x.startswith("the newest config backup, kronk-config-2025-06-10.tar.gz, is ") for x in w)
    assert any("is not a tar in" in m and "going by the newest tar's name, kronk-config-2025-06-10.tar.gz" in m
               for m in ops.logs)
    assert any("kronk-config-2025-06-10.tar.gz, is " in x for x in B.stale_warnings(house, FakeOps(), {}))


def test_fix3_note10_retention_keeps_the_newest_n_dates_not_files(house):
    cfg = house.dest / "config"
    cfg.mkdir()
    for n in ("kronk-config-2025-10-10.tar.gz", "kronk-config-2025-11-10.tar.gz",
              "kronk-config-2025-12-10.tar.gz", "kronk-config-2025-12-10.10.tar.gz"):
        (cfg / n).write_bytes(b"old")
    house.keep = 3
    ops = FakeOps()
    B.cmd_config(house, ops, dry_run=False)
    B.cmd_config(house, FakeOps(), dry_run=False)             # a rerun by hand the same day
    assert sorted(f.name for f in cfg.iterdir()) == [
        "kronk-config-2025-11-10.tar.gz", "kronk-config-2025-12-10.10.tar.gz", "kronk-config-2025-12-10.tar.gz",
        "kronk-config-2026-12-10.2.tar.gz", "kronk-config-2026-12-10.tar.gz"]
    assert "Removed by retention: kronk-config-2025-10-10.tar.gz." in ops.notices[-1][1]


def test_fix3_notes14_15_and_finding2_the_doc_and_runbook_wording():
    doc = (ROOT / "docs" / "features" / "backups.md").read_text()
    rb = (ROOT / "docs" / "runbooks" / "restore.md").read_text()
    assert "The jobs never delete anything on the share except retention's old config" not in doc     # 14b
    assert ".kronk-config-<date>[.n].tar.gz.tmp" in doc and "probe file each run creates" in doc
    assert "a second hard link of a finished, verified copy" in doc                                  # 14a
    assert "local-only one included" in doc and "on any branch" in rb                                 # finding 2
    assert "$R/etc/kronk/* /etc/kronk/" not in rb and '= nas-backup.cred ] ||' in rb                 # 15a
    assert "(this overwrites the one typed in step 2)" in rb
    assert "sudo install -d -o drew -g drew /opt/models" in rb                                       # 15b
    assert "`*.wants` links to snap units" in rb                                                     # 15c


# ── After the fourth end-to-end test (coordinator fixes) ─────────────────

def test_e2e4_the_delete_script_reads_the_local_file_last():
    """Note 5: the last thing read before the rm is the local file (its hash,
    then its stat), so a file swapped in while the destination copy is being
    read is caught by the inode or the hash."""
    lines = B.DELETE_SCRIPT.splitlines()
    idx = {k: next(i for i, ln in enumerate(lines) if ln.strip().startswith(k))
           for k in ('g=$(sha256sum < "$t")', "d=$(stat", 'h=$(sha256sum < "$f")', "s=$(stat")}
    assert idx['g=$(sha256sum < "$t")'] < idx["d=$(stat"] < idx['h=$(sha256sum < "$f")'] < idx["s=$(stat"]


def test_e2e4_retention_never_removes_the_tar_the_run_just_wrote(tmp_path):
    """Finding 4: twelve newer dates already there (a clock set back)."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    newer = [f"kronk-config-2027-{m:02d}-10.tar.gz" for m in range(1, 13)]
    for n in newer + ["kronk-config-2026-11-19.tar.gz", "kronk-config-2026-10-10.tar.gz"]:
        (cfg / n).write_bytes(b"x")
    said = []
    removed = B.retention(cfg, 12, said.append, protect="kronk-config-2026-11-19.tar.gz")
    assert removed == ["kronk-config-2026-10-10.tar.gz"]
    assert (cfg / "kronk-config-2026-11-19.tar.gz").exists()
    assert all((cfg / n).exists() for n in newer)
    assert any("just written; kept" in x for x in said)
    # without the protection the old behaviour is what the finding showed
    assert B.retention(cfg, 12, said.append) == ["kronk-config-2026-11-19.tar.gz"]


def test_e2e4_the_notice_counts_unpushed_commits_on_other_local_branches(house):
    """Finding 2: HEAD and the production tag pushed, 3 commits on another local branch."""
    ops = _count_by_refs(FakeOps(), head=0, tag=0)
    ops.unpushed = 0
    inner = ops.run

    def run(argv, timeout=300, stdin=None):
        if argv[0] == "git" and "rev-list" in argv and "--branches" in argv:
            return B.Result(0, b"3\n", "")
        return inner(argv, timeout, stdin)
    ops.run = run
    assert B.cmd_config(house, ops, dry_run=False) == 0
    body = ops.notices[-1][1]
    assert ("Other local branches: 3 commits on no remote and NOT in the tar (the bundle covers the checked-out "
            "branch and the production tags): push them.") in body
    # nothing elsewhere: no sentence
    ops = _count_by_refs(FakeOps(), head=0, tag=0)
    ops.unpushed = 0
    assert B.cmd_config(house, ops, dry_run=False) == 0
    assert "Other local branches" not in ops.notices[-1][1]


def test_e2e4_the_notice_names_a_production_tag_that_is_not_a_local_tag(house, monkeypatch):
    """Finding 1: git cannot answer for the tag; the notice says so instead of nothing."""
    monkeypatch.setattr(B, "tag_on_remote", lambda s, ops, t: "unknown (no local tag of that name)")
    ops = _count_by_refs(FakeOps(), head=0, tag=0)
    ops.unpushed = 0
    assert B.cmd_config(house, ops, dry_run=False) == 0
    body = ops.notices[-1][1]
    assert ("Production tag sprint-M16: not a local tag, so the tar cannot say which commit production runs "
            "or whether a remote has it.") in body


def test_first_real_run_a_local_tag_whose_commit_is_on_a_remote_branch_gets_no_warning(house, monkeypatch):
    """First real backup, 2026-11-20: wyoming-piper still ran from sprint-M17,
    a local tag whose commit is on origin/refactor, and the notice called it
    "not a local tag"."""
    monkeypatch.setattr(B, "tag_on_remote", lambda s, ops, t: (
        "unknown: the tag itself cannot be told without contacting the remote; its commit 55e4bfb is on "
        "origin/refactor. After a clone without it: git tag sprint-M17 55e4bfb"))
    ops = _count_by_refs(FakeOps(), head=0, tag=0)
    ops.unpushed = 0
    assert B.cmd_config(house, ops, dry_run=False) == 0
    assert "not a local tag" not in ops.notices[-1][1]


# ═══ M16b: Music Assistant's playlists/ and auth.db in the config tar ═══════

TAR = "kronk-config-2026-12-10.tar.gz"


def test_m16b_c1_the_tar_holds_playlists_and_a_consistent_auth_db(house):
    """Criterion 1: auth.db's newest rows are only in its WAL; the copy in the
    tar has them, opens and passes integrity_check. A plain copy of the main
    file (the trap) has not."""
    ops = FakeOps()
    assert B.cmd_config(house, ops, dry_run=False) == 0
    p = house.dest / "config" / TAR
    assert token_ids(MA_AUTH[0]) == ["t-main-1", "t-main-2"]                  # the main file alone
    assert token_ids(read_member(p, "ma/auth.db")) == ["t-main-1", "t-main-2", "t-wal-1"]
    assert read_member(p, "ma/playlists/Road Trip.m3u") == b"#EXTM3U\n/media/nas/a.flac\n"
    with tarfile.open(p, "r:gz") as t:
        assert t.getmember("kronk-config-2026-12-10/ma/playlists").isdir()
        assert t.getmember("kronk-config-2026-12-10/ma/auth.db").mode == 0o600
    # Read by cat only (nothing opened or written in MA's /data), main file and WAL twice each.
    assert ops.ma_reads == ["/data/settings.json", "/data/auth.db", "/data/auth.db-wal",
                            "/data/auth.db", "/data/auth.db-wal"]
    assert not any(n.startswith("kronk-sqlite-") for n in os.listdir(house.tmp))   # the private copy is gone


def test_m16b_c1_a_playlist_symlink_is_stored_as_a_link_never_followed(house):
    ops = FakeOps()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        d = tarfile.TarInfo("playlists")
        d.type, d.mode = tarfile.DIRTYPE, 0o755
        t.addfile(d)
        ln = tarfile.TarInfo("playlists/escape.m3u")
        ln.type, ln.linkname = tarfile.SYMTYPE, "/data/settings.json"
        t.addfile(ln)
    ops.ma_playlists = buf.getvalue()
    assert B.cmd_config(house, ops, dry_run=False) == 0
    with tarfile.open(house.dest / "config" / TAR, "r:gz") as t:
        m = t.getmember("kronk-config-2026-12-10/ma/playlists/escape.m3u")
        assert m.issym() and m.linkname == "/data/settings.json" and m.size == 0


def test_m16b_c2_the_manifest_says_how_each_was_read(house):
    assert B.cmd_config(house, FakeOps(), dry_run=False) == 0
    man = read_member(house.dest / "config" / TAR, "MANIFEST").decode()
    assert ("\tdocker exec cat (main file and WAL, each read twice, identical), WAL replayed by SQLite in a "
            "private copy, backup API, integrity_check ok\tma/auth.db\t<- music-assistant-server:/data/auth.db") in man
    assert "\tdocker exec music-assistant-server tar\tma/playlists/Road Trip.m3u\t" \
           "<- music-assistant-server:/data/playlists/Road Trip.m3u" in man
    assert "directory\t0\tdocker exec music-assistant-server tar\tma/playlists\t" in man


@pytest.mark.parametrize("case", ["absent", "empty", "unreadable"])
def test_m16b_c2_playlists_absent_empty_or_unreadable_is_not_a_failure(house, case):
    ops = FakeOps()
    if case == "absent":
        ops.ma_names.remove("playlists")
    elif case == "empty":
        ops.ma_playlists = tar_of({"playlists": None})
    else:
        ops.container_tar = lambda c, cwd, n, ops=ops: (B.Result(2, b"", "tar: playlists: Cannot open: Permission denied")
                                                        if c == "music-assistant-server" else FakeOps.container_tar(ops, c, cwd, n))
    assert B.cmd_config(house, ops, dry_run=False) == 0
    man = read_member(house.dest / "config" / TAR, "MANIFEST").decode()
    assert "REQUIRED items that could not be read (this tar is INCOMPLETE when any is listed):\n(none)" in man
    if case == "absent":
        assert "ABSENT\tmusic-assistant-server:/data/playlists" in man
    elif case == "empty":
        assert "directory\t0\tdocker exec music-assistant-server tar\tma/playlists\t" in man
        assert "ma/playlists/" not in man
    else:
        assert "UNREADABLE\tmusic-assistant-server:/data/playlists\tdocker exec tar failed (2)" in man
        assert "NOT READ (1)" in ops.notices[-1][1]
    assert ops.notices[-1][0] == "Kronk backup: config backup OK"


def test_m16b_c1_a_pair_that_changed_while_read_is_read_again(house):
    """A checkpoint between the two main-file reads, then a WAL restart
    between the two WAL reads: each pair is refused, the third is taken."""
    ops = FakeOps()
    seen = []
    other_main = MA_AUTH[0][:100] + b"\xff" + MA_AUTH[0][101:]
    other_wal = MA_AUTH[1][:16] + b"\x00\x00\x00\x09" + MA_AUTH[1][20:]       # salt-1 moved: a restart

    def cat(container, path):
        seen.append((ops.t, path))
        n = len(seen)
        if n == 4:          # second read of the main file, first try
            return B.Result(0, other_main, "")
        if n == 9:          # second read of the WAL, second try
            return B.Result(0, other_wal, "")
        return FakeOps.container_cat(ops, container, path)
    ops.container_cat = cat
    assert B.cmd_config(house, ops, dry_run=False) == 0
    assert len(seen) == 1 + 3 * 4
    assert seen[5][0] - seen[4][0] == dt.timedelta(seconds=B.WAL_PAIR_RETRY_S)
    assert token_ids(read_member(house.dest / "config" / TAR, "ma/auth.db")) == ["t-main-1", "t-main-2", "t-wal-1"]


@pytest.mark.parametrize("fault", ["checkpoints", "restarts", "garbage"])
def test_m16b_c2_a_pair_never_consistent_makes_the_tar_incomplete(house, fault):
    ops = FakeOps()
    n = [0]

    def cat(container, path):
        if path == "/data/auth.db":
            n[0] += 1
            if fault == "checkpoints":
                return B.Result(0, MA_AUTH[0] + bytes([n[0]]), "")
        if path == "/data/auth.db-wal" and fault == "restarts":
            n[0] += 1
            return B.Result(0, MA_AUTH[1][:16] + bytes([n[0]] * 4) + MA_AUTH[1][20:], "")
        if path == "/data/auth.db" and fault == "garbage":
            return B.Result(0, b"X" * 16 + MA_AUTH[0][16:], "")      # stable, but no database
        return FakeOps.container_cat(ops, container, path)
    ops.container_cat = cat
    assert B.cmd_config(house, ops, dry_run=False) == 4
    title, body = ops.notices[-1]
    assert title == "Kronk backup: config backup INCOMPLETE" and "MISSING REQUIRED (1)" in body
    why = {"checkpoints": "the main file changed while it was read (a checkpoint)",
           "restarts": "the write-ahead log was restarted while it was read",
           "garbage": "sqlite: "}[fault]
    st = json.loads((house.state / "status.json").read_text())["config"]["last"]
    assert any(g.startswith("music-assistant-server:/data/auth.db: " + why) and g.endswith("(3 reads)")
               for g in st["required_gaps"])
    assert not any(e.name.startswith("kronk-sqlite-") for e in os.scandir(house.tmp))


def test_m16b_c1_no_wal_file_means_the_main_file_alone(house):
    """MA stopped cleanly removes the WAL: the main file is the database."""
    ops = FakeOps()
    full = B.Collected()
    B.read_wal_pair(full, house, FakeOps(), "music-assistant-server", "/data/auth.db", "x")
    ops.ma_files = {"/data/settings.json": b"{}", "/data/auth.db": full.members[0].data}
    assert B.cmd_config(house, ops, dry_run=False) == 0
    assert token_ids(read_member(house.dest / "config" / TAR, "ma/auth.db")) == ["t-main-1", "t-main-2", "t-wal-1"]


def test_m16b_c3_auth_db_reaches_no_log_notice_or_status_beyond_its_name_and_size(house):
    ops = FakeOps()
    assert B.cmd_config(house, ops, dry_run=False) == 0
    assert B.cmd_config(house, ops, dry_run=True) == 0
    text = ("\n".join(ops.logs) + "\n".join(t + b for t, b in ops.notices)
            + (house.state / "status.json").read_text())
    for marker in ("h-t-main-1", "h-t-wal-1", "t-wal-1", "auth_tokens"):
        assert marker not in text
    size = len(read_member(house.dest / "config" / TAR, "ma/auth.db"))
    assert any(re.fullmatch(rf"\s+{size}\s+ma/auth\.db", m) for m in ops.logs)     # the dry run: name and size


def test_m16b_c3_a_failing_read_reports_no_content(house):
    ops = FakeOps()
    ops.ma_files["/data/auth.db"] = b"h-t-secret-row" * 300
    assert B.cmd_config(house, ops, dry_run=False) == 4
    text = "\n".join(ops.logs) + "\n".join(t + b for t, b in ops.notices) + (house.state / "status.json").read_text()
    assert "h-t-secret-row" not in text and "music-assistant-server:/data/auth.db" in text


def test_m16b_a_killed_runs_database_copy_is_removed_by_exact_signature_only(house):
    ops = FakeOps()
    old = (dt.datetime.now() - dt.timedelta(days=2)).timestamp()

    def mk(name, files):
        d = house.tmp / name
        d.mkdir()
        for f in files:
            (d / f).write_bytes(b"x")
        os.utime(d, (old, old))
        return d
    own = mk("kronk-sqlite-ab_cd123", ("copy.db", "copy.db-wal", "copy.db-shm"))
    other = mk("kronk-sqlite-other123", ("copy.db", "notes.txt"))
    assert B.cmd_config(house, ops, dry_run=False) == 0
    assert not own.exists() and other.exists()
    assert any(m == f"removed a database copy directory a killed run left: {own}" for m in ops.logs)
    assert any(m == f"left alone: {other} holds something other than a database copy" for m in ops.logs)


def test_m16b_c3_c4_the_docs_say_auth_db_is_a_secret_and_restored():
    feat = (ROOT / "docs" / "features" / "backups.md").read_text()
    run = (ROOT / "docs" / "runbooks" / "restore.md").read_text()
    claude = (ROOT / "CLAUDE.md").read_text()
    assert "auth.db" in feat[feat.index("### What is in the tar"):feat.index("### Required and optional")]
    assert "MA's library, history, auth and" not in feat and "is not backed up" not in run
    assert "auth.db" in claude[claude.index("- **Secrets**"):claude.index("- **New compose files**")]
    ma = run[run.index("### 13. Music Assistant"):run.index("### 14.")]
    assert "auth.db" in ma and "playlists" in ma and "auth.db-wal" in ma
    table = run[run.index("## What needs a human"):run.index("## ", run.index("## What needs a human") + 3)]
    assert "the old token is dead" not in table


# ═══ M16b, after the end-to-end test (coordinator) ═════════════════════════

def test_m16b_e2e1_a_wal_read_that_comes_back_short_with_rc_0_is_not_taken(house):
    """Finding 1: the first WAL read cut short (header intact, rc 0), the
    second whole. Both headers agree, so the old code replayed the short one."""
    ops = FakeOps()
    n = [0]

    def cat(container, path):
        r = FakeOps.container_cat(ops, container, path)
        if path == "/data/auth.db-wal":
            n[0] += 1
            if n[0] == 1:
                return B.Result(0, r.out[:132], "")
        return r
    ops.container_cat = cat
    assert B.cmd_config(house, ops, dry_run=False) == 0
    assert n[0] == 4                    # the first pair refused, the second taken
    assert token_ids(read_member(house.dest / "config" / TAR, "ma/auth.db")) == ["t-main-1", "t-main-2", "t-wal-1"]


def test_m16b_e2e1_a_wal_cut_inside_a_frame_the_same_way_every_time_is_a_gap(house):
    ops = FakeOps()
    ops.ma_files["/data/auth.db-wal"] = MA_AUTH[1][:-7]
    assert B.cmd_config(house, ops, dry_run=False) == 4
    assert "does not end on a frame" in ops.notices[-1][1]
    assert B.wal_is_whole_frames(b"") and B.wal_is_whole_frames(MA_AUTH[1])
    assert not B.wal_is_whole_frames(MA_AUTH[1][:20]) and not B.wal_is_whole_frames(MA_AUTH[1] + b"x")


def test_m16b_e2e2_a_hard_linked_member_of_a_container_tar_is_reported_not_dropped():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        a = tarfile.TarInfo("playlists/a.m3u")
        a.size = 3
        t.addfile(a, io.BytesIO(b"abc"))
        h = tarfile.TarInfo("playlists/sub-hard.m3u")
        h.type, h.linkname = tarfile.LNKTYPE, "playlists/a.m3u"
        t.addfile(h)
        f = tarfile.TarInfo("playlists/pipe")
        f.type = tarfile.FIFOTYPE
        t.addfile(f)
    c = B.Collected()
    assert B.add_tar_stream(c, "ma", "music-assistant-server:/data", "tar", buf.getvalue(), False) == 1
    assert c.unreadable == [
        ("music-assistant-server:/data/playlists/sub-hard.m3u", "left out: a hard link to playlists/a.m3u"),
        ("music-assistant-server:/data/playlists/pipe", "left out: not a file, directory or symlink")]


def test_m16b_e2e3_a_pair_that_opens_but_fails_the_integrity_check_is_refused(house):
    """Mutant: the check's result ignored. An index that disagrees with its
    table opens and copies; only integrity_check says so."""
    import sqlite3
    d = house.tmp / "mk"
    d.mkdir()
    con = sqlite3.connect(d / "x.db")
    con.execute("CREATE TABLE t (k TEXT)")
    con.execute("CREATE INDEX i ON t (k)")
    con.executemany("INSERT INTO t VALUES (?)", [(f"key-value-{n:04d}",) for n in range(20)])
    con.commit()
    root = con.execute("SELECT rootpage FROM sqlite_master WHERE name = 'i'").fetchone()[0]
    page = con.execute("PRAGMA page_size").fetchone()[0]
    con.close()
    blob = bytearray((d / "x.db").read_bytes())
    at = blob.index(b"key-value-0007", (root - 1) * page, root * page)
    blob[at:at + 14] = b"kex-value-0007"
    with pytest.raises(sqlite3.DatabaseError, match=r"integrity_check failed \(\d+ line"):
        B.sqlite_from_pair(house, bytes(blob), None)
    assert not list(house.tmp.glob("kronk-sqlite-*")) and not B.PRIVATE_DIRS


def test_m16b_e2e3_the_cleanup_leaves_a_young_copy_and_a_longer_name(house):
    """Mutants: the age test dropped (a running job's copy would go); the
    name pattern loosened."""
    old = (dt.datetime.now() - dt.timedelta(days=2)).timestamp()
    young = house.tmp / "kronk-sqlite-abcd1234"
    longer = house.tmp / "kronk-sqlite-abcd12345"
    for d in (young, longer):
        d.mkdir()
        (d / "copy.db").write_bytes(b"x")
    os.utime(longer, (old, old))
    assert B.cmd_config(house, FakeOps(), dry_run=False) == 0
    assert young.exists() and longer.exists()


def test_m16b_e2e4_a_stopped_job_takes_its_database_copy_with_it(house, monkeypatch):
    d = house.tmp / "kronk-sqlite-zzzz9999"
    d.mkdir()
    (d / "copy.db").write_bytes(b"secret")
    monkeypatch.setattr(B, "PRIVATE_DIRS", {str(d)})
    monkeypatch.setattr(B.signal, "signal", lambda *a: None)
    killed = []
    monkeypatch.setattr(B.os, "kill", lambda pid, sig: killed.append(sig))
    B.on_sigterm(FakeOps())(15, None)
    assert not d.exists() and killed == [15]
