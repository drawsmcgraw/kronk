#!/usr/bin/env python3
"""M18 rehearsal: scripts/upgrades.py minor against a scratch compose project.

    python3 scripts/upgrade_rehearsal.py <empty scratch dir> A-upgrade-kept
    python3 scripts/upgrade_rehearsal.py <empty scratch dir> B-forced-failure-rollback
    python3 scripts/upgrade_rehearsal.py <empty scratch dir> C-rollback-fails
    python3 scripts/upgrade_rehearsal.py <scratch dir> cleanup

The target is compose project `m18-rehearsal` (one service `box`, image
busybox, `network_mode: none`, no volumes): it shares no network, volume or
name with any real stack. Scenario A upgrades busybox 1.36.0 -> 1.36.1 and
keeps it; B forces a real health-check failure on 1.36.1 and rolls back; C
also removes the 1.36.0 image after the upgrade (with `pull_policy: never`),
so the rollback really fails, then shows the next run declining and `ack`.
`cleanup` removes the project's container and the two busybox tags.

Real: the registry lookups (hub.docker.com), git in a scratch repository, the
lock and status file in a scratch state directory, docker pull / compose up /
inspect / config --hash on m18-rehearsal, the health check run inside the
container. Stubbed: HA's states, the voice_tracer probe and nginx's log (the
house is reported quiet), the offline corpus and the live replay (reported
passing), the notifier (printed, never sent). Each scenario needs its own
empty directory under the scratch dir. Sprint M18, docs/sprint/M18-build.md.
"""
import json, subprocess, sys, importlib.util, textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("upgrades", REPO / "scripts" / "upgrades.py")
U = importlib.util.module_from_spec(spec); sys.modules["upgrades"] = U; spec.loader.exec_module(U)

ROOT = Path(sys.argv[1]); SCEN = sys.argv[2]
PROJECT, CONTAINER = "m18-rehearsal", "m18-rehearsal-box-1"

def sh(*a, check=True):
    r = subprocess.run(a, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"{a}: {r.stderr}")
    return r.stdout

RULES = f'''
[settings]
expected_branch = "main"
min_age_days = 14
max_attempts = 7
[replay]
flappers = []
[[component]]
# The rehearsal's one component: busybox in project {PROJECT}.
name = "busybox-scratch"
kind = "image"
order = 1
file = "docker-compose.scratch.yml"
match = '^\\s*image:\\s*busybox:(\\S+)'
image = "busybox"
pattern = '^(\\d+)\\.(\\d+)\\.(\\d+)$'
same = 2
minor = "auto"
releases = "dockerhub:library/busybox"
registry = "dockerhub:library/busybox"
deploy = "compose"
project = "{PROJECT}"
service = "box"
container = "{CONTAINER}"
health = [{{ type = "running", container = "{CONTAINER}" }}, {{ type = "exec", container = "{CONTAINER}", cmd = HEALTH }}]
'''

def compose_file(pull_policy):
    return textwrap.dedent(f"""\
        name: {PROJECT}
        services:
          box:
            image: busybox:1.36.0
            command: ["sleep", "3600"]
            network_mode: none
            pull_policy: {pull_policy}
        """)

class RehearsalOps(U.Ops):
    def __init__(self, repo, hook=None):
        super().__init__(repo)
        self.notices, self.hook = [], hook
    # stubbed: the house is quiet
    def ha_get(self, path):
        if path == "/api/states":
            return [{"entity_id": "assist_satellite.stub", "state": "idle", "last_changed": "2026-01-01T00:00:00Z"}]
        raise U.StepFailed("stub: no HA in the rehearsal")
    def http_get(self, url, headers=None, timeout=10):
        if "probe/voice_tracer" in url:
            return 200, json.dumps({"held": 0})
        return super().http_get(url, headers, timeout)
    def docker(self, *args, timeout=600):
        if args and args[0] == "logs":
            return U.Result(0, "")              # stub: nginx's log, empty
        return super().docker(*args, timeout=timeout)
    def compose(self, project, file, *args, env_file=None, timeout=900):
        r = super().compose(project, file, *args, env_file=env_file, timeout=timeout)
        if self.hook:
            self.hook(args, r)
        return r
    # stubbed: corpus, replay, notifier
    def run_tests(self):
        return U.Result(0, "stub: offline corpus not run in the rehearsal")
    def replay(self, label, out, check_only=False):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps({"rows": [{"id": "stub-row", "status": "pass"}]}))
        return U.Result(0)
    def notify(self, tag, title, body):
        self.notices.append({"tag": tag, "title": title, "body": body})
        print(f"[stub notifier] {title}\n" + "\n".join("    " + l for l in body.splitlines()))
        return True

def setup(health_fails_on, pull_policy="missing"):
    repo, state = ROOT / SCEN / "repo", ROOT / SCEN / "state"
    repo.mkdir(parents=True)
    sh("git", "-C", str(repo), "init", "-q", "-b", "main")
    sh("git", "-C", str(repo), "config", "user.name", "M18 Rehearsal")
    sh("git", "-C", str(repo), "config", "user.email", "m18-rehearsal@example.invalid")
    sh("git", "-C", str(repo), "config", "core.hooksPath", "/dev/null")
    (repo / "docker-compose.scratch.yml").write_text(compose_file(pull_policy))
    sh("git", "-C", str(repo), "add", "-A"); sh("git", "-C", str(repo), "commit", "-q", "-m", "scratch compose")
    health = json.dumps(["sh", "-c", f"busybox | head -1 | grep -qv 'v{health_fails_on} '"]) if health_fails_on else json.dumps(["true"])
    rules_path = ROOT / SCEN / "rules.toml"
    rules_path.write_text(RULES.replace("HEALTH", health))
    # Bring the scratch project up at 1.36.0 (the "production" of the rehearsal).
    sh("docker", "compose", "-p", PROJECT, "--project-directory", str(repo), "-f", str(repo / "docker-compose.scratch.yml"), "up", "-d")
    return repo, state, U.load_rules(rules_path, env={})

def show(repo, state):
    print("--- git log:"); print(sh("git", "-C", str(repo), "log", "--format=%h %an: %s"))
    print("--- running:", sh("docker", "inspect", CONTAINER, "-f", "{{.Config.Image}} {{.State.Running}}").strip())
    st = json.loads((state / "status.json").read_text())
    print("--- status:", json.dumps({k: st.get(k) for k in ("last_run", "rollback_failed", "attention", "months")}, indent=1)[:1800])

if SCEN == "A-upgrade-kept":
    repo, state, rules = setup(None)
    ops = RehearsalOps(repo)
    print("=== dry run first"); print("exit", U.minor_run(ops, rules, state, dry_run=True)); print("state dir exists after dry run:", state.exists())
    print("=== real run"); print("exit", U.minor_run(ops, rules, state)); show(repo, state)
elif SCEN == "B-forced-failure-rollback":
    repo, state, rules = setup("1.36.1")
    ops = RehearsalOps(repo)
    print("exit", U.minor_run(ops, rules, state)); show(repo, state)
elif SCEN == "C-rollback-fails":
    repo, state, rules = setup("1.36.1", pull_policy="never")
    sh("docker", "pull", "-q", "busybox:1.36.1")       # pull_policy never: the new tag must be local
    def hook(args, r):
        # After the upgrade's `up` succeeds, remove the previous image so the
        # rollback's `up` (pull_policy: never) really fails.
        if args[:1] == ("up",) and r.ok and "1.36.1" in sh("docker", "inspect", CONTAINER, "-f", "{{.Config.Image}}"):
            print("[rehearsal hook] removing busybox:1.36.0 to force the rollback to fail:", sh("docker", "rmi", "busybox:1.36.0", check=False).strip()[:80])
    ops = RehearsalOps(repo, hook)
    print("exit", U.minor_run(ops, rules, state)); show(repo, state)
    print("=== next run (by hand, --ignore-month): must decline")
    print("exit", U.minor_run(ops, rules, state, ignore_month=True))
    print("last_run:", json.loads((state / "status.json").read_text())["last_run"]["summary"][:300])
    import os; os.environ["KRONK_UPGRADE_STATE"] = str(state)
    print("=== ack"); print("exit", U.main(["ack"], ops=ops))
    print("rollback_failed after ack:", json.loads((state / "status.json").read_text())["rollback_failed"])
elif SCEN == "cleanup":
    sh("docker", "rm", "-f", CONTAINER, check=False)
    for t in ("busybox:1.36.0", "busybox:1.36.1"):
        print(t, sh("docker", "rmi", t, check=False).strip()[:60] or "absent")
    print("left:", sh("docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={PROJECT}", "--format", "{{.Names}}").strip() or "nothing")
else:
    raise SystemExit(__doc__)
