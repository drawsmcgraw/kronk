"""Shared fakes for the M18 upgrade tests: a fake internet (FakeWeb), a fake
house (FakeOps: docker, compose, deploy_tag, HA, probes, notifier, clock),
and temporary git repositories with their own user. Nothing here opens a
socket or runs docker."""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_upgrades():
    name = "upgrades_m18"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "upgrades.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


U = load_upgrades()
NOW = dt.datetime(2026, 12, 1, 8, 30, tzinfo=dt.timezone.utc)


def iso(days_ago: float, now: dt.datetime = NOW) -> str:
    return (now - dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeWeb(U.Web):
    """Answers from a table of URL prefixes; anything else is a 404. Keeps
    the real Web's host allow-list and request accounting."""

    def __init__(self, table: dict | None = None):
        super().__init__(opener=self._fake_open)
        self.table = dict(table or {})
        self.seen: list[tuple[str, dict]] = []

    def _fake_open(self, req, timeout=None):
        url = req.full_url
        self.seen.append((url, dict(req.header_items())))
        for prefix in sorted(self.table, key=len, reverse=True):
            if url.startswith(prefix):
                ans = self.table[prefix]
                if callable(ans):
                    ans = ans(url)
                if isinstance(ans, Exception):
                    raise ans
                body, hdrs = ans if isinstance(ans, tuple) else (ans, {})
                return _Resp(body if isinstance(body, bytes) else json.dumps(body).encode(), hdrs)
        import urllib.error
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)


class _Resp:
    def __init__(self, body: bytes, headers: dict):
        self._b, self.headers = body, _H(headers)

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _H(dict):
    def items(self):
        return super().items()


def gh_release(tag, days_ago, pre=False, body="", now=NOW):
    return {"tag_name": tag, "published_at": iso(days_ago, now), "prerelease": pre, "draft": False,
            "html_url": f"https://github.com/x/y/releases/tag/{tag}", "body": body}


def hub_tag(name, days_ago, now=NOW):
    return {"name": name, "last_updated": iso(days_ago, now), "tag_last_pushed": iso(days_ago, now),
            "digest": fake_digest(name)}


def fake_digest(tag: str) -> str:
    """The digest a fake registry gives a tag, and a fake pull records."""
    return "sha256:" + hashlib.sha256(tag.encode()).hexdigest()


def _not_found(url):
    import urllib.error
    return urllib.error.HTTPError(url, 404, "not found", {}, None)


def hub_tag_lookup(pushed_days: dict, now=NOW):
    """GET .../tags/<tag>: the tag's push date and digest, or a 404."""
    def answer(url):
        tag = url.rsplit("/", 1)[-1]
        return hub_tag(tag, pushed_days[tag], now) if tag in pushed_days else _not_found(url)
    return answer


def ghcr_registry(table: dict, repo: str, created_days: dict, now=NOW):
    """ghcr's manifest (an index with a linux/amd64 image), the image's
    manifest and its config blob (with `created`), as ghcr.io answers."""
    def manifests(url):
        ref = url.rsplit("/", 1)[-1]
        if ref.startswith("amd64-"):
            return {"schemaVersion": 2, "config": {"digest": f"cfg-{ref[6:]}"}}
        if ref not in created_days:
            return _not_found(url)
        return ({"schemaVersion": 2, "manifests": [
            {"digest": f"arm64-{ref}", "platform": {"os": "linux", "architecture": "arm64"}},
            {"digest": f"amd64-{ref}", "platform": {"os": "linux", "architecture": "amd64"}}]},
            {"docker-content-digest": fake_digest(ref)})

    def blobs(url):
        tag = url.rsplit("/", 1)[-1].removeprefix("cfg-")
        return {"created": iso(created_days[tag], now)} if tag in created_days else _not_found(url)
    table[f"https://ghcr.io/v2/{repo}/manifests/"] = manifests
    table[f"https://ghcr.io/v2/{repo}/blobs/"] = blobs


# ── temporary git repositories ───────────────────────────────────────────

def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def make_repo(tmp: Path, files: dict[str, str], branch: str = "main") -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", branch)
    git(repo, "config", "user.name", "M18 Test")
    git(repo, "config", "user.email", "m18-test@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "config", "core.hooksPath", "/dev/null")
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "initial")
    return repo


COMPOSE_TELEMETRY = """services:
  tempo:
    image: grafana/tempo:3.1.1
  grafana:
    image: grafana/grafana:13.2.4
"""
COMPOSE_KRONK = """services:
  nginx:
    image: nginx:1.29-alpine
  searxng:
    image: searxng/searxng:2026.11.9-57ff753ad
  litellm:
    image: ghcr.io/berriai/litellm:v1.88.1
  wyoming-piper:
    image: rhasspy/wyoming-piper:2.2.2
"""
COMPOSE_HA = """name: kronk-ha
services:
  homeassistant:
    image: ghcr.io/home-assistant/home-assistant:2026.5.4
"""


def base_files() -> dict[str, str]:
    return {"docker-compose.telemetry.yml": COMPOSE_TELEMETRY, "docker-compose.yml": COMPOSE_KRONK,
            "docker-compose.ha.yml": COMPOSE_HA, "docs/readme.md": "docs\n"}


# ── rules for a test ─────────────────────────────────────────────────────

def rules(only: list[str] | None = None, **settings):
    r = U.load_rules(env={})
    r.settings.update({"expected_branch": "main", **settings})
    if only is not None:
        r.components = [c for c in r.components if c["name"] in only]
    return r


# ── the fake house ───────────────────────────────────────────────────────

def _quiet_states(now=NOW):
    return [
        {"entity_id": "assist_satellite.kitchen", "state": "idle", "last_changed": iso(1, now)},
        {"entity_id": "assist_satellite.office", "state": "idle", "last_changed": iso(1, now)},
        {"entity_id": "media_player.kitchen", "state": "idle", "last_changed": iso(1, now)},
        {"entity_id": "select.kitchen_assistant_2", "state": "kronk-dev", "last_changed": iso(2, now)},
        {"entity_id": "select.kitchen_wake_word_2", "state": "Hey Jarvis", "last_changed": iso(2, now)},
    ]


class FakeOps(U.Ops):
    """A house with containers, a Kronk stack deployed from tags, HA, the
    probes and a notifier, all in memory. `fail` makes chosen steps fail."""

    def __init__(self, repo: Path, web: FakeWeb | None = None, now: dt.datetime = NOW):
        super().__init__(repo, web or FakeWeb())
        self.clock = now
        self.local_offset = dt.timedelta(hours=-5)
        self.calls: list[tuple] = []
        self.notices: list[tuple[str, str, str]] = []
        self.notify_ok = True
        self.states = _quiet_states(now)
        self.services = [{"domain": "rest_command", "services": {"kronk_music_voice": {}}}]
        self.held = 0                     # held runs voice_tracer's state file does not list
        self.held_ids: list[str] = []     # held runs it lists (data/voice_tracer_state.json)
        self.dev_runs: set[str] = set()   # HA's recent run ids on the kronk-dev pipeline
        self.dev_list_ok = True
        self.nginx_log = ""               # household lines a test puts in nginx's log
        self.nginx_own: list[str] = []    # lines the run's own work leaves there (replays, deploy checks)
        self._run_n = 0
        self.deploying: list[str] = []
        self.ha_up = True
        # what runs: container -> image reference; image ids per reference
        self.containers = {
            "kronk-telemetry-tempo-1": "grafana/tempo:3.1.1",
            "kronk-telemetry-grafana-1": "grafana/grafana:13.2.4",
            "kronk-nginx-1": "nginx:1.29-alpine",
            "kronk-searxng-1": "searxng/searxng:2026.11.9-57ff753ad",
            "kronk-litellm-1": "ghcr.io/berriai/litellm:v1.88.1",
            "wyoming-piper": "rhasspy/wyoming-piper:2.2.2",
            "homeassistant": "ghcr.io/home-assistant/home-assistant:2026.5.4",
            "music-assistant-server": "kronk/music-assistant:2.11.0b5-kronk.9",
        }
        self.images = {ref: f"sha256:{abs(hash(ref)) :064x}"[:71] for ref in self.containers.values()}
        self.ma_started = self.ma_started_default = "2026-11-05T10:00:00Z"
        self.prod_tag = "sprint-X"
        git(repo, "tag", self.prod_tag)
        self.hashes_match = True
        # Compose's config hash per service: what the file at HEAD (and its
        # env file) gives the service, and the label a container carries,
        # set by the `up` that created it (sprint M18 fix 2, G1).
        self.config_extra: dict[str, str] = {}     # a change outside the pin (an env file, another key)
        self.labels: dict[str, str] = {}
        self.stopped: set[str] = set()     # containers stopped and not started again
        # Home Assistant's compose file renders these bind mounts; the paths that exist on the host
        self.ha_binds = [("/etc/localtime", "/etc/localtime"), ("/home/drew/kronk-voice-recordings", "/voice_recordings")]
        self.existing_paths = {"/etc/localtime", "/home/drew/kronk-voice-recordings"}
        self.ha_mounted = list(self.ha_binds)  # what the running container mounts
        # Bind mounts of the other containers, their start times, and the
        # modification times of files in the working tree (F10).
        self.mounts = {"kronk-telemetry-tempo-1": [(str(repo / "telemetry/tempo.yaml"), "/etc/tempo.yaml")],
                       "kronk-telemetry-grafana-1": [(str(repo / "telemetry/grafana/provisioning"), "/etc/grafana/provisioning"),
                                                     (str(repo / "telemetry/grafana/dashboards"), "/var/lib/grafana/dashboards")]}
        self.started = {"music-assistant-server": self.ma_started_default,
                        "kronk-telemetry-tempo-1": "2026-11-08T22:30:23.087882723Z",
                        "kronk-telemetry-grafana-1": "2026-11-08T22:28:30.647456834Z",
                        "homeassistant": "2026-11-16T02:00:00.000000000Z"}
        self.mtimes: dict[str, dt.datetime] = {}
        self.repo_digests: dict[str, list[str]] = {}   # image ref -> RepoDigests, when not the registry's (F12)
        self.ha_log = "2026-12-01 08:31:00 INFO (MainThread) [homeassistant.bootstrap] Home Assistant initialized\n"
        # failure switches
        self.bad_versions: set[str] = set()        # health fails while one of these runs
        self.deploy_fail_tags: set[str] = set()
        self.tests_fail = False
        self.tests_fail_versions: set[str] = set()   # the corpus fails while one of these runs
        self.replay_regress: dict[str, list[str]] = {}   # running version -> row ids that fail
        self.replay_rc = 0
        self.cannot_return: set[str] = set()          # versions that cannot be brought back (rollback fails)
        self.pull_fail: set[str] = set()

    # helpers
    def _config_hash(self, file: str, svc: str) -> str:
        """What `compose config --hash <svc>` gives the file on disk: its
        image line for the service and anything else configured for it."""
        image = _IMAGE_OF.get(svc, "")
        return _hash(svc, f"{image}:{self._pins_at(None, file).get(image, '')}", self.config_extra.get(svc, ""))

    def _image_of(self, comp_service_container: str) -> str:
        return self.containers.get(comp_service_container, "")

    def _version_running(self, container: str) -> str:
        return self.containers.get(container, ":").rsplit(":", 1)[-1]

    def _pins_at(self, rev: str | None, file: str) -> dict[str, str]:
        text = git(self.repo, "show", f"{rev}:{file}") if rev else (self.repo / file).read_text()
        return {m.group(1): m.group(2) for m in re.finditer(r"^\s*image:\s*((?:[^\s:]+/)*[^\s:]+):(\S+)", text, re.M)}

    # Ops interface
    def run(self, args, timeout=600, env=None):
        self.calls.append(("run", tuple(args)))
        if args[0] == "git":                       # real git, in the temporary repository
            return U.Ops.run(self, args, timeout, env)
        if args[:2] == ["systemctl", "--user"]:
            return U.Result(0, "active\n")
        return U.Result(0, "")

    def docker(self, *args, timeout=600):
        self.calls.append(("docker",) + args)
        a = list(args)
        if a[0] == "inspect":
            c, fmt = a[1], a[3]
            if c not in self.containers:
                return U.Result(1, "", f"no such container {c}")
            ref = self.containers[c]
            if fmt == "{{.Config.Image}}":
                return U.Result(0, ref + "\n")
            if fmt == "{{.Config.Image}} {{.State.Running}}":
                return U.Result(0, f"{ref} {'false' if c in self.stopped else 'true'}\n")
            if fmt == "{{.Image}}":
                return U.Result(0, self.images[ref] + "\n")
            if fmt == "{{.State.StartedAt}}":
                return U.Result(0, (self.ma_started if c == "music-assistant-server" else self.started.get(c, self.ma_started)) + "\n")
            if fmt == "{{json .Mounts}}":
                pairs = self.ha_mounted if c == "homeassistant" else self.mounts.get(c, [])
                return U.Result(0, json.dumps([{"Type": "bind", "Source": s, "Destination": t} for s, t in pairs]))
            if fmt == "{{.Config.StopTimeout}}":
                return U.Result(0, "120\n")
            if fmt == "{{.State.Health.Status}}":
                return U.Result(0, ("unhealthy" if self._version_running(c) in self.bad_versions else "healthy") + "\n")
            if fmt == "{{.State.Running}}":
                return U.Result(0, ("false" if c in self.stopped else "true") + "\n")
            if "config-hash" in fmt:
                svc = _SVC_OF.get(c, c)
                return U.Result(0, self.labels.get(c, _hash(svc, ref, "")) + "\n")
        if a[0] == "image" and a[1] == "inspect":
            ref = a[2]
            if ref not in self.images:
                return U.Result(1, "", "no such image")
            if "RepoDigests" in a[-1]:
                image, _, tag = ref.rpartition(":")
                digs = self.repo_digests.get(ref, [f"{image}@{fake_digest(tag)}"] if image else [])
                return U.Result(0, f"{self.images[ref]} {json.dumps(digs)}\n")
            return U.Result(0, self.images[ref] + "\n")
        if a[0] == "tag":
            src, dst = a[1], a[2]
            self.images[dst] = src if src.startswith("sha256:") else self.images[src]
            return U.Result(0)
        if a[0] == "pull":
            if a[1] in self.pull_fail:
                return U.Result(1, "", "pull failed")
            self.images.setdefault(a[1], f"sha256:{abs(hash(a[1])):064x}"[:71])
            return U.Result(0)
        if a[0] == "stop":
            if a[-1] not in self.containers:      # as Docker: a stop of a missing container fails
                return U.Result(1, "", f"Error response from daemon: No such container: {a[-1]}")
            self.stopped.add(a[-1])
            return U.Result(0)
        if a[0] == "start":
            if a[-1] not in self.containers:
                return U.Result(1, "", f"Error response from daemon: No such container: {a[-1]}")
            self.stopped.discard(a[-1])
            return U.Result(0)
        if a[0] == "logs" and a[-1] == "homeassistant":
            return U.Result(0, self.ha_log)
        if a[0] == "logs":
            # `docker logs --since <N>m`: only lines from the last N minutes.
            since = self.clock - dt.timedelta(minutes=int(a[a.index("--since") + 1].rstrip("m")))
            lines = (self.nginx_log + "".join(self.nginx_own)).splitlines(keepends=True)
            return U.Result(0, "".join(ln for ln in lines if _logged_at(ln) is None or _logged_at(ln) >= since))
        if a[0] == "events":
            return U.Result(0, "15\n")
        if a[0] == "exec":
            return U.Result(0 if self._version_running(a[1]) not in self.bad_versions else 1)
        return U.Result(0)

    def compose(self, project, file, *args, env_file=None, timeout=900):
        self.calls.append(("compose", project, file) + args)
        a = list(args)
        if a[0] == "config" and a[1] == "--hash":
            return U.Result(0, f"{a[2]} {self._config_hash(file, a[2]) if self.hashes_match else 'OTHER'}\n")
        if a[:3] == ["config", "--format", "json"]:
            vols = [{"type": "volume", "source": "ha-config", "target": "/config"}] + [
                {"type": "bind", "source": s, "target": t, "bind": {}} for s, t in self.ha_binds]
            return U.Result(0, json.dumps({"services": {"homeassistant": {"volumes": vols}}}))
        if a[0] == "pull":
            pins = self._pins_at(None, file)
            for image, tag in pins.items():
                if f"{image}:{tag}" in self.pull_fail:
                    return U.Result(1, "", "pull failed")
            for image, tag in pins.items():
                self.images.setdefault(f"{image}:{tag}", f"sha256:{abs(hash(image + tag)):064x}"[:71])
            return U.Result(0)
        if a[0] == "up":
            svc = a[-1]
            container = {"tempo": "kronk-telemetry-tempo-1", "grafana": "kronk-telemetry-grafana-1",
                         "homeassistant": "homeassistant"}.get(svc, svc)
            pins = self._pins_at(None, file)
            image = self.containers[container].rsplit(":", 1)[0] if container in self.containers else _IMAGE_OF[svc]
            want = f"{image}:{pins[image]}"
            if pins[image] in self.cannot_return and want != self.containers.get(container):
                return U.Result(1, "", "the previous image is gone")
            self.containers[container] = want
            self.stopped.discard(container)
            self.labels[container] = self._config_hash(file, svc)
            self.started[container] = self.clock.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
            self.images.setdefault(want, f"sha256:{abs(hash(want)):064x}"[:71])
            return U.Result(0)
        return U.Result(0)

    def deploy_tag(self, *args):
        self.calls.append(("deploy_tag",) + args)
        if args[0] == "status":
            rows = ["CONTAINER SERVICE RUNNING_FROM CREATED",
                    f"kronk-orchestrator-1 orchestrator tag {self.prod_tag} 1 hour ago",
                    f"kronk-litellm-1 litellm tag {self.prod_tag} 1 hour ago",
                    "kronk-orchestrator-dev-1 orchestrator-dev working tree 1 hour ago"]
            return U.Result(0, "\n".join(rows) + "\n")
        if args[0] == "deploy":
            tag = args[1]
            if tag in self.deploy_fail_tags:
                return U.Result(1, "", "verification failed")
            pins = self._pins_at(tag, "docker-compose.yml")
            for image, version in pins.items():
                cur = next((r for r in self.containers.values() if r.rsplit(":", 1)[0] == image), None)
                if version in self.cannot_return and cur != f"{image}:{version}":
                    return U.Result(1, "", f"deploy of {tag} failed: {image}:{version} cannot come back")
            for image, version in pins.items():
                self.images.setdefault(f"{image}:{version}", f"sha256:{abs(hash(image + version)):064x}"[:71])
                for c, ref in list(self.containers.items()):
                    if ref.rsplit(":", 1)[0] == image:
                        self.containers[c] = f"{image}:{version}"
            self.prod_tag = tag
            # deploy_tag.sh's verify: /api/status and the /api/chat shim, with
            # the User-Agent it is handed in KRONK_CLIENT_UA (else curl's).
            ua = self.client_env().get("KRONK_CLIENT_UA", "curl/8.14.1")
            self.nginx_own += [_nginx("GET", "/api/status", ua, self.clock), _nginx("POST", "/api/chat", ua, self.clock)]
            return U.Result(0, f"deployed {tag}\n")
        return U.Result(0)

    def run_tests(self):
        self.calls.append(("run_tests",))
        running = {v.rsplit(":", 1)[-1] for v in self.containers.values()}
        if self.tests_fail or running & self.tests_fail_versions:
            return U.Result(1, "", "1 failed")
        return U.Result(0, "4989 passed")

    def replay(self, label, out, check_only=False):
        self.calls.append(("replay", label, check_only))
        if self.replay_rc in (2, 3):
            return U.Result(self.replay_rc, "", "refused")
        if check_only:
            return U.Result(0)
        running = set(v.rsplit(":", 1)[-1] for v in self.containers.values())
        failing = {rid for v, ids in self.replay_regress.items() if v in running for rid in ids}
        rows = [{"id": rid, "status": "fail" if rid in failing else "pass"} for rid in ("row-a", "row-b", "row-c")]
        rows.append({"id": "inc-0930-search-deep-house-ytm", "status": "fail" if "flap" in failing else "pass"})
        # What a real replay leaves behind: each row is a run on HA's kronk-dev
        # pipeline, whose Kronk is behind /dev/voice (HA's client, not ours);
        # voice_tracer holds each run for its correction window (here: until
        # the end of the test).
        for row in rows:
            self._run_n += 1
            rid = f"01JDEVRUN{self._run_n:04d}"
            row["pipeline_run_id"] = rid
            self.held_ids.append(rid)
            self.dev_runs.add(rid)
            self.nginx_own.append(_nginx("POST", "/dev/voice/api/chat", "HomeAssistant/2026.5.4 aiohttp/3.12.13", self.clock))
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps({"rows": rows}))
        return U.Result(1 if failing else 0)

    def pgrep(self, pattern):
        return list(self.deploying)

    def http_get(self, url, headers=None, timeout=10):
        self.calls.append(("http", url))
        if "probe/voice_tracer" in url:
            return 200, json.dumps({"held": self.held + len(self.held_ids), "connected": True, "too_old": 0})
        bad = any(self._version_running(c) in self.bad_versions for c in self.containers)
        if ":3200/" in url:
            return (500 if self._version_running("kronk-telemetry-tempo-1") in self.bad_versions else 200), "ready"
        if ":3000/" in url:
            return (500 if self._version_running("kronk-telemetry-grafana-1") in self.bad_versions else 200), "{}"
        if ":8002/" in url:
            return (500 if self._version_running("kronk-litellm-1") in self.bad_versions else 200), "{}"
        return (500 if bad else 200), "{}"

    def ha_get(self, path):
        self.calls.append(("ha_get", path))
        if not self.ha_up:
            raise U.StepFailed("HA GET: connection refused")
        if path == "/api/states":
            return json.loads(json.dumps(self.states))
        if path == "/api/services":
            return self.services
        if path == "/api/":
            if self._version_running("homeassistant") in self.bad_versions:
                raise U.StepFailed("HA GET /api/: 502")
            return {"message": "API running."}
        raise U.StepFailed(f"unexpected {path}")

    def voice_tracer_held_ids(self, rel):
        self.calls.append(("held_ids", rel))
        return list(self.held_ids)

    def dev_pipeline_run_ids(self, names):
        self.calls.append(("dev_runs", tuple(names)))
        if not self.dev_list_ok:
            return None
        return set(self.dev_runs) if "kronk-dev" in names else set()

    def path_exists(self, path):
        return path in self.existing_paths

    def files_changed_since(self, sources, since):
        return [(f, t) for f, t in self.mtimes.items() if any(f == s or f.startswith(s + "/") for s in sources) and t > since]

    def wyoming(self, host, port):
        return self._version_running("wyoming-piper") not in self.bad_versions

    def notify(self, tag, title, body):
        self.notices.append((tag, title, body))
        return self.notify_ok

    def sleep(self, s):
        self.calls.append(("sleep", s))

    def now(self):
        return self.clock

    def local_now(self):
        # America/New_York in December: UTC-5 (08:30Z is 03:30 local, inside the night window).
        return (self.clock + self.local_offset).replace(tzinfo=None)


_SVC_OF = {"kronk-telemetry-tempo-1": "tempo", "kronk-telemetry-grafana-1": "grafana", "homeassistant": "homeassistant"}
_IMAGE_OF = {"tempo": "grafana/tempo", "grafana": "grafana/grafana", "homeassistant": "ghcr.io/home-assistant/home-assistant"}


def _hash(svc: str, ref: str, extra: str) -> str:
    return hashlib.sha256(f"{svc}|{ref}|{extra}".encode()).hexdigest()[:16]


def _logged_at(line):
    m = re.search(r"\[(\d\d/\w{3}/\d{4}:\d\d:\d\d:\d\d) \+0000\]", line)
    return dt.datetime.strptime(m.group(1), "%d/%b/%Y:%H:%M:%S").replace(tzinfo=dt.timezone.utc) if m else None


def _nginx(method, path, ua, when):
    """One access-log line in nginx's combined format, as kronk-nginx-1 writes it."""
    return (f'172.18.0.1 - - [{when.strftime("%d/%b/%Y:%H:%M:%S +0000")}] "{method} {path} HTTP/1.1" '
            f'200 512 "-" "{ua}"\n')


def household_chat(ops, ua="HomeAssistant/2026.5.4 aiohttp/3.12.13", path="/api/chat"):
    """A real household request in the quiet window."""
    ops.nginx_log += _nginx("POST", path, ua, ops.clock)


def standard_web(now=NOW) -> FakeWeb:
    """Registries as of 2026-12-01 for the components the tests upgrade."""
    gh = "https://api.github.com/repos/"
    w = FakeWeb({
        gh + "grafana/tempo/releases?": [gh_release("v3.1.2", 20), gh_release("v3.1.1", 41), gh_release("v3.2.0", 15)],
        gh + "grafana/grafana/releases?": [gh_release("v13.3.1", 21), gh_release("v13.2.6", 21),
                                            gh_release("v13.2.7", 3), gh_release("v13.2.5", 35)],
        gh + "rhasspy/wyoming-piper/releases?": [gh_release("v2.5.4", 30), gh_release("v2.2.2", 300)],
        gh + "BerriAI/litellm/releases?": [gh_release("v1.110.0", 13), gh_release("v1.88.6-stable", 100)],
        gh + "BerriAI/litellm/releases/tags/v1.88.6": gh_release("v1.88.6", 150),
        gh + "home-assistant/core/releases?": [gh_release("2026.11.2", 15), gh_release("2026.5.4", 190)],
        "https://hub.docker.com/v2/repositories/grafana/tempo/tags?": {"results": [hub_tag("3.1.2", 20), hub_tag("3.1.1", 41)]},
        "https://hub.docker.com/v2/repositories/grafana/grafana/tags?": {"results": [hub_tag("13.2.6", 21), hub_tag("13.2.7", 3)]},
        "https://hub.docker.com/v2/repositories/grafana/tempo/tags/": hub_tag_lookup({"3.1.2": 20, "3.1.1": 41, "3.2.0": 15}),
        "https://hub.docker.com/v2/repositories/grafana/grafana/tags/": hub_tag_lookup(
            {"13.2.5": 35, "13.2.6": 21, "13.2.7": 3, "13.3.1": 21}),
        "https://hub.docker.com/v2/repositories/rhasspy/wyoming-piper/tags": {"results": []},
        "https://hub.docker.com/v2/repositories/searxng/searxng/tags?": {"results": [
            hub_tag("2026.11.17-b99cf7496", 14.5), hub_tag("2026.11.28-aaaaaaa11", 3), hub_tag("latest", 0)]},
        "https://hub.docker.com/v2/repositories/searxng/searxng/tags/": hub_tag_lookup(
            {"2026.11.17-b99cf7496": 14.5, "2026.11.28-aaaaaaa11": 3}),
        "https://ghcr.io/token": {"token": "anon"},
        "https://ghcr.io/v2/berriai/litellm/tags/list": {"tags": ["v1.88.1", "v1.88.6", "v1.88.6-stable", "main-latest", "v1.110.0"]},
        "https://ghcr.io/v2/home-assistant/home-assistant/tags/list": {"tags": ["2026.5.3", "2026.5.4", "2026.6.0b0", "stable"]},
    })
    ghcr_registry(w.table, "berriai/litellm", {"v1.88.1": 200, "v1.88.6": 150, "v1.110.0": 13})
    ghcr_registry(w.table, "home-assistant/home-assistant", {"2026.5.4": 190, "2026.5.5": 20, "2026.6.0": 30, "2026.11.2": 15})
    return w


def cleanup(path: Path):
    shutil.rmtree(path, ignore_errors=True)


# ── killing a run at a chosen call (a BaseException no handler catches) ──

class Killed(BaseException):
    pass


def kill_on(ops, kind, pred):
    """Kill the run right after the call `kind` (git|docker|compose|deploy_tag|...) whose args match pred."""
    orig = getattr(ops, kind)

    def wrapped(*args, **kw):
        r = orig(*args, **kw)
        if pred(args):
            raise Killed(f"killed after {kind} {args[:3]}")
        return r
    setattr(ops, kind, wrapped)
    return lambda: setattr(ops, kind, orig)


def kill_before(ops, kind, pred):
    """Kill the run right before the call `kind` whose args match pred."""
    orig = getattr(ops, kind)

    def wrapped(*args, **kw):
        if pred(args):
            raise Killed(f"killed before {kind} {args[:3]}")
        return orig(*args, **kw)
    setattr(ops, kind, wrapped)
    return lambda: setattr(ops, kind, orig)
