"""Sprint M21 step 2, criterion 17: scripts/deploy_tag.sh and the live
config directories.

Each test builds a scratch git repository (user name and email set on the
repository, never the global config) holding a copy of the script, the
validator, both services' loaders and config/, tagged, plus scratch
release and live directories. docker, curl and sleep are stubs on PATH
that only record their arguments: nothing reaches a daemon or the network,
and the compose project is a scratch name.
"""
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("no_network")

REPO = Path(__file__).resolve().parent.parent
GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}

DOCKER_STUB = """#!/bin/sh
echo "docker $*" >> "$STUB_LOG"
case "$*" in
  *"config --services"*) printf 'nginx\\norchestrator\\ntool_service\\n' ;;
  ps*) echo "scratch-orchestrator-1|orchestrator|/nowhere|1 second ago" ;;
esac
exit 0
"""
CURL_STUB = """#!/bin/sh
echo "curl $*" >> "$STUB_LOG"
echo '{"message":{"content":"ok"}}'
exit 0
"""


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                           "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
                          check=True, capture_output=True, text=True, env={**os.environ, **GIT_ENV}).stdout


class Scratch:
    def __init__(self, root: Path):
        self.root = root
        self.repo = root / "kronk"
        self.releases = root / "releases"
        self.live = root / "live"
        self.bin = root / "bin"
        self.log = root / "stub.log"
        self.repo.mkdir()
        for sub in ("scripts",):
            (self.repo / sub).mkdir()
        shutil.copy(REPO / "scripts" / "deploy_tag.sh", self.repo / "scripts")
        shutil.copy(REPO / "scripts" / "config_check.py", self.repo / "scripts")
        for svc in ("orchestrator", "tool_service"):
            (self.repo / svc).mkdir()
            for f in (REPO / svc).glob("*.py"):
                shutil.copy(f, self.repo / svc)
        shutil.copytree(REPO / "config", self.repo / "config")
        (self.repo / "docker-compose.yml").write_text("services:\n  orchestrator:\n    image: scratch\n")
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "scratch")
        git(self.repo, "tag", "t1")
        self.bin.mkdir()
        for name, body in (("docker", DOCKER_STUB), ("curl", CURL_STUB), ("sleep", "#!/bin/sh\nexit 0\n")):
            p = self.bin / name
            p.write_text(body)
            p.chmod(p.stat().st_mode | stat.S_IXUSR)

    def run(self, *args, check=True):
        env = {**os.environ, **GIT_ENV, "PATH": f"{self.bin}:{os.environ['PATH']}",
               "KRONK_RELEASES": str(self.releases), "KRONK_LIVE_CONFIG": str(self.live),
               "KRONK_COMPOSE_PROJECT": "kronk-scratch-m21s2", "KRONK_CHECK_PYTHON": sys.executable,
               "STUB_LOG": str(self.log)}
        r = subprocess.run(["bash", str(self.repo / "scripts" / "deploy_tag.sh"), *args],
                           capture_output=True, text=True, env=env, timeout=120)
        if check:
            assert r.returncode == 0, r.stdout + r.stderr
        return r

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def commit_tag(self, tag, edit):
        edit(self.repo)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", f"for {tag}")
        git(self.repo, "tag", tag)


@pytest.fixture
def s(tmp_path):
    return Scratch(tmp_path)


def _tree(d: Path) -> dict:
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(d).parts}


def test_plan_changes_nothing_and_says_what_deploy_would_do(s):
    r = s.run("plan", "t1")
    assert not s.live.exists(), "plan must not create a live directory"
    out = r.stdout
    assert "prod: " in out and "(absent)" in out
    assert "deploy seeds prod from t1:config/" in out and "deploy seeds dev from t1:config/" in out
    assert "valid" in out
    # The release's config/ is untouched (no link) by plan.
    assert (s.releases / "t1" / "config").is_dir() and not (s.releases / "t1" / "config").is_symlink()
    assert all("up -d --build" not in c for c in s.calls())
    assert all("-p kronk-scratch-m21s2" in c for c in s.calls() if c.startswith("docker compose"))


def test_deploy_seeds_both_live_dirs_links_and_never_overwrites(s):
    s.run("deploy", "t1")
    prod, dev = s.live / "prod", s.live / "dev"
    for d in (prod, dev):
        assert _tree(d) == _tree(s.repo / "config")
        log = git(d, "log", "--format=%s")
        assert log.startswith("Seed from t1 ("), log
        assert git(d, "status", "--porcelain") == ""
    rel = s.releases / "t1"
    assert (rel / "config").is_symlink() and os.readlink(rel / "config") == str(prod)
    assert _tree(rel / "config.shipped") == _tree(s.repo / "config")
    assert any("up -d --build" in c for c in s.calls())
    # An edit in the live directory survives the next deploy of a new tag.
    (prod / "tuning.yaml").write_text((prod / "tuning.yaml").read_text().replace("research: 8", "research: 6"))
    git(prod, "commit", "-qam", "edit")
    s.commit_tag("t2", lambda r: (r / "config" / "voice.yaml").write_text(
        (r / "config" / "voice.yaml").read_text().replace("deadline_s: 25", "deadline_s: 30")))
    r = s.run("deploy", "t2")
    assert "research: 6" in (prod / "tuning.yaml").read_text()
    assert "deadline_s: 25" in (prod / "voice.yaml").read_text(), "a live file must never be overwritten"
    # The shipped default that differs is shown for information.
    assert "deadline_s: 30" in r.stdout and "research: 8" in r.stdout


def test_deploy_refuses_a_live_dir_the_tag_does_not_validate(s):
    s.run("deploy", "t1")
    prod = s.live / "prod"
    (prod / "tuning.yaml").write_text((prod / "tuning.yaml").read_text().replace("research: 8", "research: 80"))
    git(prod, "commit", "-qam", "too many rounds")
    n_before = len(s.calls())
    r = s.run("deploy", "t1", check=False)
    assert r.returncode == 1
    assert "config/tuning.yaml:21: agent_loop.max_rounds.research" in r.stdout
    assert "refused: the production live config does not validate against t1's schema" in r.stderr
    assert "git -C" in r.stderr and "revert" in r.stderr
    assert not any("up -d" in c for c in s.calls()[n_before:])
    plan = s.run("plan", "t1")
    assert "would be REFUSED" in plan.stdout


def test_rollback_to_an_older_tag_that_lacks_a_key_is_refused_with_what_to_do(s):
    """t2's loader knows a key t1 does not; a live file using it validates
    against t2 and is refused for a rollback to t1, naming file and line."""
    def add_key(r):
        p = r / "orchestrator" / "config_loader.py"
        text = p.read_text()
        old = "    max_overtakes: int = Field(3, ge=0, le=100)\n"
        assert old in text
        p.write_text(text.replace(old, old + "    max_wait_s: int = Field(30, ge=1, le=600)\n"))
    s.commit_tag("t2", add_key)
    s.run("deploy", "t2")
    prod = s.live / "prod"
    (prod / "tuning.yaml").write_text((prod / "tuning.yaml").read_text() + "  max_wait_s: 40\n")
    git(prod, "commit", "-qam", "use the new key")
    assert s.run("plan", "t2").returncode == 0
    r = s.run("deploy", "t1", check=False)
    assert r.returncode == 1
    assert "model_lock.max_wait_s: unknown key" in r.stdout
    assert "refused" in r.stderr
    # After reverting the edit, the rollback goes through.
    git(prod, "revert", "--no-edit", "HEAD")
    s.run("deploy", "t1")
    assert os.readlink(s.releases / "t1" / "config") == str(prod)


def test_seed_refuses_an_existing_dir_and_unknown_revs(s):
    s.run("seed", "dev", "t1")
    r = s.run("seed", "dev", "t1", check=False)
    assert r.returncode == 1 and "never overwritten" in r.stderr
    r = s.run("seed", "staging", "t1", check=False)
    assert r.returncode == 1 and "usage" in r.stderr
    r = s.run("seed", "prod", "no-such-tag", check=False)
    assert r.returncode == 1 and "no such tag" in r.stderr
    assert not (s.live / "prod").exists()


def test_seed_records_the_tag_and_is_byte_for_byte(s):
    s.run("seed", "prod", "t1")
    prod = s.live / "prod"
    assert _tree(prod) == _tree(s.repo / "config")
    commit = git(s.repo, "rev-parse", "t1^{commit}").strip()
    assert git(prod, "log", "-1", "--format=%s") == f"Seed from t1 ({commit})\n"
    assert git(prod, "log", "-1", "--format=%an <%ae>") == "kronk deploy_tag <deploy_tag@kronk.invalid>\n"


def test_the_working_tree_config_is_the_shipped_defaults_and_validates():
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "config_check.py"), "--code", str(REPO),
                        "--dir", str(REPO / "config")], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip().endswith("valid")


def test_compose_mounts_the_live_dirs_without_creating_them():
    text = (REPO / "docker-compose.yml").read_text()
    assert "./config:/config" not in text, "short syntax would create a missing directory as root"
    assert text.count("create_host_path: false }") == 4
    assert text.count("${KRONK_DEV_CONFIG_DIR:-../kronk-config/dev}") == 2
