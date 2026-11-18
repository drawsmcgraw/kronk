#!/usr/bin/env python3
"""Upgrade cadence (sprint M18): the monthly minor run, the quarterly major
check, the inventory, and the notices.

    scripts/upgrade_minor.sh [--dry-run] [--ignore-month]   monthly patch upgrades
    scripts/upgrade_major_check.sh [--dry-run]              quarterly major-release report + notice
    python3 scripts/upgrades.py inventory                   pins, what runs, what is newest
    python3 scripts/upgrades.py notices                     retry notices HA did not take
    python3 scripts/upgrades.py ack                         clear the /services warning
    python3 scripts/upgrades.py status                      print the status file
    python3 scripts/upgrades.py test-notice                 one notice now, marked TEST (install check)

The rules (what is minor, what is major, per component) are data:
scripts/upgrade_rules.toml. This file decides nothing the rules do not say:
a version it cannot parse, a registry it cannot read, a check it cannot run
are each reported as a skip or a decline, never guessed around.

Monthly run, in order (docs/features/upgrade-cadence.md has the long form;
sprint M18 fix 1 added the night window, the intent record and recovery,
the household-only busy check and the outcomes not applied / out of step):
  0. An earlier run's unfinished intent record is dealt with first; outside
     settings.night_window the run declines without counting an attempt.
  1. Lock (data/upgrades/lock); this month's attempt count (at most
     max_attempts; the timer fires on days 1-7).
  2. Retry any notice HA did not take last time.
  3. Plan: for each component the newest release the rules call minor,
     at least min_age_days old, whose image exists in the registry.
     Nothing to do: stop, send nothing.
  4. Decline when the household is busy or the tree is not ours to commit on
     (the run's own replays and deploy checks do not count):
     a voice or chat request in the last quiet_minutes, a satellite not
     idle, a media player playing, voice_tracer holding a run, a deploy or
     another run in progress, the last rollback failed, the tree not clean,
     not on the expected branch. Each reason is logged; the timer tries
     again tomorrow, up to max_attempts a month.
  5. Baseline: the offline corpus (scripts/run_tests.sh) and the live
     dry-run replay on the dev path (scripts/voice_replay.py) before any
     change. If either cannot run, decline.
  6. One component at a time, in `order`: edit the pin, commit
     ("upgrade: <name> <from> -> <to>"), bring it up (Kronk stack: tag and
     scripts/deploy_tag.sh deploy; Home Assistant: M12's restart runbook in
     code; telemetry: compose pull and up of that one service), verify
     (image, health, the corpus, the replay against the baseline, the
     stack's own checks), keep it or roll it back (pin restored by a
     commit, previous tag deployed or previous image by id, then verified).
     A failure before anything changed what runs is "not applied" (the pin
     commit reverted). A failed rollback, or git left out of step with what
     runs, stops the run and blocks later runs until `ack`.
  7. One notice (scripts/lib/notify.sh) when anything was changed or tried;
     if it cannot be sent, the status file keeps it, /services shows a
     warning, and `notices` (hourly timer) retries it.
It pushes nothing to any git remote.

--dry-run runs every read (files, registries, git, docker inspect, HA's
states, the probes) and prints what it would do. It writes nothing, takes
no lock, commits nothing, pulls nothing and sends nothing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import fnmatch
import http.client
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(os.getenv("KRONK_REPO_DIR", str(Path(__file__).resolve().parent.parent)))
RULES_FILE = Path(__file__).resolve().parent / "upgrade_rules.toml"
NOTIFY_TAG = "kronk-upgrades"
COMMIT_NAME = "Kronk upgrade_minor"
COMMIT_EMAIL = "upgrade_minor@kronk.invalid"
HA_ROLLBACK_REPO = "kronk/ha-rollback"

# Hosts the lookups may read. Anonymous GETs only, a generic client name,
# nothing about this system in the request. Anything else is refused.
LOOKUP_HOSTS = {"api.github.com", "hub.docker.com", "ghcr.io", "pypi.org",
                "raw.githubusercontent.com", "www.home-assistant.io"}
# Where ghcr.io redirects a blob GET (the image config, for its build date).
# Redirects are followed only to these hosts and the ones above.
REDIRECT_HOSTS = {"pkg-containers.githubusercontent.com"}
USER_AGENT = "upgrade-check/1.0"
# Local HTTP (HA, the probes) only ever goes to this host's loopback.
LOCAL_HOSTS = {"localhost", "127.0.0.1"}


# ═══ Rules ═══════════════════════════════════════════════════════════════

class RulesError(ValueError):
    pass


@dataclass
class Rules:
    settings: dict
    replay: dict
    report: dict
    components: list[dict]

    def component(self, name: str) -> dict:
        for c in self.components:
            if c["name"] == name:
                return c
        raise KeyError(name)


_REQUIRED = ("name", "kind", "order", "pattern", "same", "minor")
_MINOR = ("auto", "report", "none", "excluded")


def load_rules(path: Path = RULES_FILE, env: dict | None = None) -> Rules:
    env = os.environ if env is None else env
    data = tomllib.loads(Path(path).read_text())
    settings = dict(data.get("settings", {}))
    if env.get("KRONK_UPGRADE_BRANCH"):
        settings["expected_branch"] = env["KRONK_UPGRADE_BRANCH"]
    for key, default in (("expected_branch", "main"), ("min_age_days", 14), ("quiet_minutes", 5),
                         ("select_quiet_minutes", 15), ("max_attempts", 7), ("inert_paths", []),
                         ("dev_services", []), ("dev_pipelines", ["kronk-dev"]),
                         ("voice_tracer_state", "data/voice_tracer_state.json"), ("flapping_players", []),
                         ("major_lock_wait_minutes", 20),
                         ("night_window", ["02:00", "05:00"])):
        settings.setdefault(key, default)
    try:
        win = [dt.time.fromisoformat(t) for t in settings["night_window"]]
        assert len(win) == 2 and win[0] != win[1]
    except (ValueError, TypeError, AssertionError) as e:
        raise RulesError(f"settings.night_window = {settings['night_window']!r}: need [\"HH:MM\", \"HH:MM\"] ({e})") from e
    settings["night_window_t"] = win
    comps = data.get("component", [])
    seen = set()
    for c in comps:
        for k in _REQUIRED:
            if k not in c:
                raise RulesError(f"component {c.get('name', '?')}: missing {k}")
        if c["minor"] not in _MINOR:
            raise RulesError(f"component {c['name']}: minor = {c['minor']!r}, not one of {_MINOR}")
        if c["name"] in seen:
            raise RulesError(f"component {c['name']}: listed twice")
        seen.add(c["name"])
        try:
            groups = re.compile(c["pattern"]).groups
        except re.error as e:
            raise RulesError(f"component {c['name']}: bad pattern: {e}") from e
        if not 0 <= c["same"] <= c.get("compare", groups):
            raise RulesError(f"component {c['name']}: same = {c['same']} outside 0..{c.get('compare', groups)}")
        if c["minor"] == "auto" and c.get("deploy") not in ("kronk", "ha", "compose"):
            raise RulesError(f"component {c['name']}: minor = auto needs deploy = kronk | ha | compose")
        if c.get("deploy") in ("kronk", "ha", "compose") and c.get("kind") in ("lockfile", "llama-build", "host-venv"):
            raise RulesError(f"component {c['name']}: a {c['kind']} is never deployed by this script")
        if c["name"] in ("music-assistant", "llama.cpp") and c["minor"] not in ("excluded", "none"):
            raise RulesError(f"component {c['name']}: may only be excluded or none")
        # The Kronk stack is deployed only from a tag by deploy_tag.sh, never
        # by a compose command (M12, M17); the Music Assistant stack, by
        # stack and not by name, is never touched (Drew 2026-11-14) (F10).
        if c.get("deploy") in ("compose", "ha") and c.get("project") in (None, "", "kronk"):
            raise RulesError(f"component {c['name']}: deploy = {c['deploy']} with project {c.get('project')!r}: "
                             f"the Kronk stack is deployed only through deploy_tag.sh (deploy = \"kronk\")")
        in_ma = c.get("file") == "docker-compose.ma.yml" or c.get("project") == "kronk-ma"
        if in_ma and (c["minor"] not in ("excluded", "none") or c.get("deploy")):
            raise RulesError(f"component {c['name']}: part of the Music Assistant stack; may only be excluded or none, "
                             f"with no deploy")
    comps.sort(key=lambda c: c["order"])
    return Rules(settings, data.get("replay", {}), data.get("report", {}), comps)


# ═══ Versions ════════════════════════════════════════════════════════════

def vkey(comp: dict, version: str) -> tuple | None:
    """The comparable key of an exact version, or None when the rule does
    not understand the string."""
    m = re.fullmatch(comp["pattern"], version or "")
    if not m:
        return None
    groups = m.groups()[: comp.get("compare", len(m.groups()))]
    try:
        return tuple(int(g) if g is not None else 0 for g in groups)
    except ValueError:
        return None


def classify(comp: dict, pin: str, cand: str) -> str | None:
    """'minor' | 'major' | 'not-newer' | None (candidate not understood).
    Raises RulesError when the pin itself is not understood."""
    pk = vkey(comp, pin)
    if pk is None:
        raise RulesError(f"{comp['name']}: pin {pin!r} is not an exact version under {comp['pattern']}")
    ck = vkey(comp, cand)
    if ck is None:
        return None
    if ck <= pk:
        return "not-newer"
    return "minor" if ck[: comp["same"]] == pk[: comp["same"]] else "major"


@dataclass
class Release:
    version: str
    date: dt.datetime | None = None          # the project's release date (its release list)
    url: str | None = None
    notes: str | None = None
    prerelease: bool = False
    listed: bool = True                      # in the project's release list (False: seen in the registry only)
    reg_date: dt.datetime | None = None      # when the registry's tag was pushed (ghcr: the image's build time)
    digest: str | None = None                # the registry's digest for the tag at planning


def newest(comp: dict, pin: str, releases: list[Release], kind: str, now: dt.datetime | None = None,
           min_age_days: int | None = None) -> tuple[Release | None, list[str]]:
    """The newest release of `kind` ('minor' or 'major'), honouring the age
    rule when min_age_days is given. Returns (release or None, notes) where
    notes explain what was passed over."""
    notes: list[str] = []
    pool = [r for r in releases if not r.prerelease and classify(comp, pin, r.version) == kind]
    pool.sort(key=lambda r: vkey(comp, r.version), reverse=True)
    if min_age_days is None:
        return (pool[0] if pool else None), notes
    now = now or dt.datetime.now(dt.timezone.utc)
    for r in pool:
        if r.date is None:
            notes.append(f"{r.version}: release date unknown, not taken")
            continue
        age = (now - r.date).days
        if age < min_age_days:
            notes.append(f"{r.version}: {age} days old, under {min_age_days}")
            continue
        return r, notes
    return None, notes


# ═══ Pins ════════════════════════════════════════════════════════════════

@dataclass
class Pin:
    value: str
    file: str
    line: int            # 1-based
    text: str


def read_pin(repo: Path, comp: dict) -> Pin:
    """The pin as written. Exactly one line must match `match`."""
    path = repo / comp["file"]
    lines = path.read_text().splitlines()
    hits = [(i, ln) for i, ln in enumerate(lines) if re.search(comp["match"], ln)]
    if len(hits) != 1:
        raise RulesError(f"{comp['name']}: {len(hits)} lines of {comp['file']} match {comp['match']!r} (need exactly 1)")
    i, ln = hits[0]
    return Pin(re.search(comp["match"], ln).group(1), comp["file"], i + 1, ln)


def write_pin(repo: Path, comp: dict, old: str, new: str) -> None:
    """Replace the pin on its one line; refuse when the file is not as read."""
    pin = read_pin(repo, comp)
    if pin.value != old:
        raise RulesError(f"{comp['name']}: pin is {pin.value!r}, expected {old!r}")
    m = re.search(comp["match"], pin.text)
    new_line = pin.text[: m.start(1)] + new + pin.text[m.end(1):]
    path = repo / comp["file"]
    raw = path.read_text()
    lines = raw.split("\n")
    assert lines[pin.line - 1] == pin.text
    lines[pin.line - 1] = new_line
    path.write_text("\n".join(lines))
    if read_pin(repo, comp).value != new:
        raise RulesError(f"{comp['name']}: pin write did not take")


def pkg_name(name: str) -> str:
    """PEP 503 normalised distribution name (ruamel.yaml == ruamel-yaml)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def read_lock(repo: Path, comp: dict) -> dict[str, str]:
    """name==version pairs of a uv/pip-compile lockfile, names normalised."""
    out = {}
    for ln in (repo / comp["file"]).read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+?)\s*(\\)?$", ln)
        if m:
            out[pkg_name(m.group(1))] = m.group(2)
    return out


# ═══ Lookups (internet, read-only) ═══════════════════════════════════════

class LookupFailed(RuntimeError):
    pass


class Web:
    """Anonymous GETs to the allowed hosts, cached for the run. Every host
    contacted is recorded in `hosts`."""

    def __init__(self, timeout: float = 20, opener=None):
        self.timeout = timeout
        self.cache: dict[str, tuple[bytes, dict]] = {}
        self.hosts: set[str] = set()
        self.requests = 0
        self._open = opener or urllib.request.build_opener(_GuardedRedirects()).open

    def get(self, url: str, headers: dict | None = None) -> tuple[bytes, dict]:
        u = urllib.parse.urlsplit(url)
        if u.scheme != "https" or u.hostname not in LOOKUP_HOSTS:
            raise LookupFailed(f"refused: {url} is not an allowed lookup host")
        key = url + json.dumps(headers or {}, sort_keys=True)
        if key in self.cache:
            return self.cache[key]
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        self.hosts.add(u.hostname)
        self.requests += 1
        try:
            with self._open(req, timeout=self.timeout) as r:
                body, hdrs = r.read(), {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            raise LookupFailed(f"GET {url}: HTTP {e.code}") from e
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException, ValueError) as e:
            # HTTPException covers a body cut short (IncompleteRead) and a
            # malformed status line.
            raise LookupFailed(f"GET {url}: {e.__class__.__name__}: {e}") from e
        self.cache[key] = (body, hdrs)
        return body, hdrs

    def json(self, url: str, headers: dict | None = None):
        body, _ = self.get(url, headers)
        try:
            return json.loads(body)
        except ValueError as e:
            raise LookupFailed(f"GET {url}: not JSON") from e


class _GuardedRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to an allowed lookup host, over https."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urllib.parse.urlsplit(newurl)
        if u.scheme != "https" or u.hostname not in LOOKUP_HOSTS | REDIRECT_HOSTS:
            raise urllib.error.URLError(f"redirect to {u.hostname} refused (not an allowed lookup host)")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _shape(value, kind, what: str):
    """An answer of the expected JSON shape, or LookupFailed: a registry that
    answers nonsense skips that component, never the run (F6)."""
    if not isinstance(value, kind):
        raise LookupFailed(f"unexpected answer: {what} is {type(value).__name__} {str(value)[:80]!r}")
    return value


def _date(s: str | None) -> dt.datetime | None:
    if s is not None and not isinstance(s, str):
        raise LookupFailed(f"unexpected answer: a date is {type(s).__name__} {s!r}")
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def github_releases(web: Web, repo: str, prefix: str = "", pages: int = 1) -> list[Release]:
    out = []
    for page in range(1, pages + 1):
        data = web.json(f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}",
                        {"Accept": "application/vnd.github+json"})
        if not isinstance(data, list):
            raise LookupFailed(f"github {repo}: unexpected answer {str(data)[:120]}")
        for r in data:
            _shape(r, dict, f"a github {repo} release")
            if r.get("draft"):
                continue
            tag = _shape(r.get("tag_name") or "", str, f"github {repo} tag_name")
            _shape(r.get("body") or "", str, f"github {repo} release body")
            if prefix and tag.startswith(prefix):
                tag = tag[len(prefix):]
            out.append(Release(tag, _date(r.get("published_at")), r.get("html_url"), r.get("body"),
                               bool(r.get("prerelease"))))
        if len(data) < 100:
            break
    return out


def github_release(web: Web, repo: str, tag: str, prefix: str = "") -> Release | None:
    try:
        r = web.json(f"https://api.github.com/repos/{repo}/releases/tags/{prefix}{tag}",
                     {"Accept": "application/vnd.github+json"})
    except LookupFailed as e:
        if "HTTP 404" in str(e):
            return None
        raise
    _shape(r, dict, f"github {repo} release {tag}")
    if r.get("draft"):
        return None
    _shape(r.get("body") or "", str, f"github {repo} release body")
    return Release(tag, _date(r.get("published_at")), r.get("html_url"), r.get("body"), bool(r.get("prerelease")))


def dockerhub_tags(web: Web, repo: str, name: str | None = None, pages: int = 2) -> list[Release]:
    out = []
    q = {"page_size": "100", "ordering": "last_updated"}
    if name:
        q["name"] = name
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags?{urllib.parse.urlencode(q)}"
    for _ in range(pages):
        data = _shape(web.json(url), dict, f"docker hub {repo} tag list")
        for t in _shape(data.get("results", []), list, f"docker hub {repo} results"):
            _shape(t, dict, f"a docker hub {repo} tag")
            name = _shape(t.get("name", ""), str, f"docker hub {repo} tag name")
            out.append(Release(name, _date(t.get("tag_last_pushed") or t.get("last_updated")),
                               f"https://hub.docker.com/r/{repo.removeprefix('library/')}/tags?name={name}"))
        url = data.get("next")
        if not url:
            break
        _shape(url, str, f"docker hub {repo} next page")
    return out


def ghcr_token(web: Web, repo: str) -> str:
    tok = _shape(web.json(f"https://ghcr.io/token?scope=repository:{repo}:pull"), dict, f"ghcr {repo} token answer").get("token")
    if not tok or not isinstance(tok, str):
        raise LookupFailed(f"ghcr {repo}: no anonymous token")
    return tok


def ghcr_tags(web: Web, repo: str, max_pages: int = 20) -> list[str]:
    tok = ghcr_token(web, repo)
    url, tags = f"https://ghcr.io/v2/{repo}/tags/list?n=1000", []
    for _ in range(max_pages):
        body, hdrs = web.get(url, {"Authorization": f"Bearer {tok}"})
        try:
            data = json.loads(body)
        except ValueError as e:
            raise LookupFailed(f"ghcr {repo}: tag list is not JSON ({body[:60]!r})") from e
        page = _shape(_shape(data, dict, f"ghcr {repo} tag list").get("tags") or [], list, f"ghcr {repo} tags")
        tags += [_shape(t, str, f"a ghcr {repo} tag") for t in page]
        m = re.search(r"<([^>]+)>", hdrs.get("link", ""))
        if not m:
            return tags
        url = "https://ghcr.io" + m.group(1)
    raise LookupFailed(f"ghcr {repo}: more than {max_pages} pages of tags")


def pypi_releases(web: Web, package: str) -> list[Release]:
    data = _shape(web.json(f"https://pypi.org/pypi/{package}/json"), dict, f"pypi {package}")
    out = []
    for v, files in _shape(data.get("releases") or {}, dict, f"pypi {package} releases").items():
        files = [f for f in _shape(files, list, f"pypi {package} {v} files") if _shape(f, dict, "a pypi file") and not f.get("yanked")]
        if not files:
            continue
        when = min((_date(f.get("upload_time_iso_8601")) for f in files if f.get("upload_time_iso_8601")),
                   default=None)
        out.append(Release(v, when, f"https://pypi.org/project/{package}/{v}/"))
    return out


def _kind_src(spec: str) -> tuple[str, str]:
    kind, _, rest = (spec or "").partition(":")
    return kind, rest


def image_releases(web: Web, comp: dict, pin: str, deep: bool = False) -> list[Release]:
    """Every version of an image component this run can see, with dates
    where a source gives them. The registry decides which tags exist; the
    release source (GitHub) gives dates and notes."""
    rkind, rrepo = _kind_src(comp.get("releases", ""))
    gkind, grepo = _kind_src(comp.get("registry", ""))
    prefix = comp.get("tag_prefix", "")
    rel: dict[str, Release] = {}
    if rkind == "github":
        for r in github_releases(web, rrepo, prefix, pages=2 if deep else 1):
            rel.setdefault(r.version, r)
    elif rkind == "dockerhub":
        same_prefix = _same_prefix(comp, pin)
        for r in dockerhub_tags(web, rrepo, same_prefix or None):
            rel.setdefault(r.version, r)
        if same_prefix:
            for r in dockerhub_tags(web, rrepo, None, pages=1):
                rel.setdefault(r.version, r)
    # Tags the registry has and the release list does not are kept, marked
    # listed=False: the inventory shows them, the monthly run never takes
    # them (F12). The registry's date never stands in for a release date.
    if gkind == "ghcr":
        for t in ghcr_tags(web, grepo):
            if t not in rel and vkey(comp, t) is not None:
                rel[t] = Release(t, listed=False)
    elif gkind == "dockerhub" and rkind == "github":
        same_prefix = _same_prefix(comp, pin)
        for r in dockerhub_tags(web, grepo, same_prefix or None, pages=1):
            if r.version in rel:
                rel[r.version].reg_date = r.date
            elif vkey(comp, r.version) is not None:
                rel[r.version] = Release(r.version, None, r.url, listed=False, reg_date=r.date)
    return list(rel.values())


def _same_prefix(comp: dict, pin: str) -> str:
    """The literal text of the pin up to the end of its `same` groups, for a
    registry name filter ('3.1.' for 3.1.1 with same 2). '' when same is 0."""
    m = re.fullmatch(comp["pattern"], pin)
    if not m or comp["same"] == 0:
        return ""
    end = m.end(comp["same"])
    return pin[:end] + (pin[end] if end < len(pin) and not pin[end].isalnum() else "")


def fill_date(web: Web, comp: dict, r: Release) -> Release:
    """A candidate beyond the release list's first page, or seen only in the
    registry: look its release up by tag (a draft is not a release)."""
    rkind, rrepo = _kind_src(comp.get("releases", ""))
    if (r.date is None or not r.listed) and rkind == "github":
        g = github_release(web, rrepo, r.version, comp.get("tag_prefix", ""))
        if g:
            r.date, r.url, r.notes, r.prerelease, r.listed = g.date, g.url, g.notes, g.prerelease, True
    return r


def separate_sources(comp: dict) -> bool:
    """True when the release list and the registry are different sources
    (GitHub and a registry); False when the registry is the release list
    (SearXNG, nginx, the Python bases on Docker Hub)."""
    return _kind_src(comp.get("releases", "")) != _kind_src(comp.get("registry", ""))


def registry_info(web: Web, comp: dict, tag: str, seen_tags: set[str] | None = None) -> tuple[bool, dt.datetime | None, str | None]:
    """(the registry has the tag, when it was pushed, its digest). Docker
    Hub gives the push date and digest per tag. ghcr gives no push date: the
    image's build time (its config's `created`) stands in, and the digest is
    the manifest's (Docker-Content-Digest)."""
    gkind, grepo = _kind_src(comp.get("registry", ""))
    if gkind == "dockerhub":
        try:
            t = _shape(web.json(f"https://hub.docker.com/v2/repositories/{grepo}/tags/{tag}"), dict,
                       f"docker hub {grepo} tag {tag}")
        except LookupFailed as e:
            if "HTTP 404" in str(e):
                return False, None, None
            raise
        d = t.get("digest")
        return True, _date(t.get("tag_last_pushed") or t.get("last_updated")), d if isinstance(d, str) else None
    if gkind == "ghcr":
        tags = seen_tags if seen_tags is not None else set(ghcr_tags(web, grepo))
        if tag not in tags:
            return False, None, None
        created, digest = ghcr_created(web, grepo, tag)
        return True, created, digest
    raise LookupFailed(f"{comp['name']}: no registry to confirm {tag}")


_MANIFEST_TYPES = ", ".join(("application/vnd.oci.image.index.v1+json",
                             "application/vnd.docker.distribution.manifest.list.v2+json",
                             "application/vnd.oci.image.manifest.v1+json",
                             "application/vnd.docker.distribution.manifest.v2+json"))


def ghcr_created(web: Web, repo: str, tag: str) -> tuple[dt.datetime | None, str | None]:
    """The linux/amd64 image's build time and the tag's manifest digest."""
    auth = {"Authorization": f"Bearer {ghcr_token(web, repo)}"}
    body, hdrs = web.get(f"https://ghcr.io/v2/{repo}/manifests/{tag}", {**auth, "Accept": _MANIFEST_TYPES})
    digest = hdrs.get("docker-content-digest")
    try:
        man = _shape(json.loads(body), dict, f"ghcr {repo} manifest {tag}")
        if "manifests" in man:
            plat = [m for m in _shape(man["manifests"], list, f"ghcr {repo} {tag} manifests")
                    if isinstance(m, dict) and (m.get("platform") or {}).get("os") == "linux"
                    and (m.get("platform") or {}).get("architecture") == "amd64"]
            if not plat:
                raise LookupFailed(f"ghcr {repo} {tag}: no linux/amd64 image")
            body, _ = web.get(f"https://ghcr.io/v2/{repo}/manifests/{plat[0]['digest']}", {**auth, "Accept": _MANIFEST_TYPES})
            man = _shape(json.loads(body), dict, f"ghcr {repo} amd64 manifest {tag}")
        cfg = _shape(man.get("config"), dict, f"ghcr {repo} {tag} config")["digest"]
        body, _ = web.get(f"https://ghcr.io/v2/{repo}/blobs/{cfg}", auth)
        created = _shape(json.loads(body), dict, f"ghcr {repo} {tag} image config").get("created")
    except (ValueError, KeyError) as e:
        raise LookupFailed(f"ghcr {repo} {tag}: unexpected manifest ({e.__class__.__name__}: {e})") from e
    return _date(created), digest if isinstance(digest, str) else None


# ═══ External steps (stubbed by the tests) ═══════════════════════════════

@dataclass
class Result:
    rc: int
    out: str = ""
    err: str = ""

    @property
    def ok(self) -> bool:
        return self.rc == 0

    def tail(self, n: int = 400) -> str:
        return (self.out + self.err).strip()[-n:]


class Ops:
    """Every step that touches something outside this process. The tests
    replace it; the rehearsal replaces parts of it."""

    def __init__(self, repo: Path = REPO, web: Web | None = None):
        self.repo = repo
        self.web = web or Web()
        self._ha_token: str | None = None
        # The User-Agent this run's own requests to production carry
        # (deploy_tag.sh's /api/chat check reads it from KRONK_CLIENT_UA), so
        # the house check can tell them from the household's. Set per run.
        self.client_ua: str | None = None

        # The token the run writes into its lock file: deploy_tag.sh refuses
        # to deploy while the lock is held, except for the holder (F9).
        self.upgrade_token: str | None = None

    def client_env(self) -> dict:
        env = {"KRONK_CLIENT_UA": self.client_ua} if self.client_ua else {}
        if self.upgrade_token:
            env["KRONK_UPGRADE_TOKEN"] = self.upgrade_token
        return env

    # processes
    def run(self, args: list[str], timeout: float = 600, env: dict | None = None) -> Result:
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                               env={**os.environ, **(env or {})}, cwd=self.repo)
            return Result(p.returncode, p.stdout, p.stderr)
        except subprocess.TimeoutExpired:
            return Result(124, "", f"timed out after {timeout}s: {' '.join(args[:4])}")
        except OSError as e:
            return Result(127, "", str(e))

    def git(self, *args: str, check: bool = True) -> str:
        r = self.run(["git", "-C", str(self.repo), "-c", f"user.name={COMMIT_NAME}",
                      "-c", f"user.email={COMMIT_EMAIL}", "-c", "commit.gpgsign=false", *args], timeout=60)
        if check and not r.ok:
            raise StepFailed(f"git {' '.join(args)}: {r.tail(300)}")
        return r.out

    def docker(self, *args: str, timeout: float = 600) -> Result:
        return self.run(["docker", *args], timeout=timeout)

    def compose(self, project: str, file: str, *args: str, env_file: str | None = None,
                timeout: float = 900) -> Result:
        cmd = ["docker", "compose", "-p", project, "--project-directory", str(self.repo),
               "-f", str(self.repo / file)]
        if env_file:
            cmd += ["--env-file", str(self.repo / env_file)]
        return self.run(cmd + list(args), timeout=timeout)

    def deploy_tag(self, *args: str) -> Result:
        return self.run([str(self.repo / "scripts" / "deploy_tag.sh"), *args], timeout=3600, env=self.client_env())

    def run_tests(self) -> Result:
        return self.run([str(self.repo / "scripts" / "run_tests.sh"), "-q", "-p", "no:cacheprovider"],
                        timeout=3600)

    def replay(self, label: str, out: Path, check_only: bool = False, ids: list[str] | None = None) -> Result:
        cmd = ["python3", str(self.repo / "scripts" / "voice_replay.py"), "--label", label, "--out", str(out)]
        for i in ids or []:
            cmd += ["--id", i]
        return self.run(cmd + (["--check-only"] if check_only else []), timeout=3600)

    def corpus_ids(self) -> list[str]:
        """The row ids of the voice corpus, in order (what voice_replay selects by default)."""
        text = (self.repo / "tests" / "voice_corpus.jsonl").read_text()
        return [json.loads(ln)["id"] for ln in text.splitlines() if ln.strip()]

    def pgrep(self, pattern: str) -> list[str]:
        r = self.run(["pgrep", "-af", pattern], timeout=10)
        return [ln for ln in r.out.splitlines() if ln.strip() and str(os.getpid()) != ln.split()[0]]

    # local HTTP: loopback only
    def http_get(self, url: str, headers: dict | None = None, timeout: float = 10) -> tuple[int, str]:
        u = urllib.parse.urlsplit(url)
        if u.hostname not in LOCAL_HOSTS:
            raise StepFailed(f"refused: {url} is not this host's loopback")
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, ""
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return 0, str(e)

    def ha_token(self) -> str:
        if self._ha_token is None:
            tok = os.getenv("HA_TOKEN") or env_value(self.repo / ".env", "HA_TOKEN")
            if not tok:
                raise StepFailed("HA_TOKEN not set (no env var, not in .env)")
            self._ha_token = tok
        return self._ha_token

    def ha_get(self, path: str):
        status, body = self.http_get(f"http://localhost:8123{path}",
                                     {"Authorization": f"Bearer {self.ha_token()}"}, timeout=15)
        if status != 200:
            raise StepFailed(f"HA GET {path}: {status or body[:120]}")
        return json.loads(body)

    def voice_tracer_held_ids(self, rel: str) -> list[str] | None:
        """The run ids voice_tracer lists as held (its state file, which it
        rewrites on every hold and every export), or None when unreadable."""
        try:
            held = json.loads((self.repo / rel).read_text()).get("held")
        except (OSError, ValueError, AttributeError):
            return None
        return [str(x) for x in held] if isinstance(held, list) else None

    def dev_pipeline_run_ids(self, names: list[str]) -> set[str] | None:
        """HA's recent run ids on the named pipelines (read-only websocket
        calls on this host's loopback: assist_pipeline/pipeline/list and
        pipeline_debug/list, what voice_tracer and voice_replay read), or None
        when HA cannot be asked."""
        try:
            from websockets.sync.client import connect
        except ImportError:
            return None
        try:
            with connect("ws://localhost:8123/api/websocket", open_timeout=10, close_timeout=5, max_size=None) as ws:
                ws.recv(timeout=10)
                ws.send(json.dumps({"type": "auth", "access_token": self.ha_token()}))
                if json.loads(ws.recv(timeout=10)).get("type") != "auth_ok":
                    return None
                n = 0

                def call(type_: str, **kw):
                    nonlocal n
                    n += 1
                    ws.send(json.dumps({"id": n, "type": type_, **kw}))
                    while True:
                        m = json.loads(ws.recv(timeout=15))
                        if m.get("id") == n and m.get("type") == "result":
                            if not m.get("success"):
                                raise StepFailed(f"{type_}: {m.get('error')}")
                            return m.get("result") or {}

                out: set[str] = set()
                for p in call("assist_pipeline/pipeline/list").get("pipelines", []):
                    if p.get("name") in names:
                        runs = call("assist_pipeline/pipeline_debug/list", pipeline_id=p["id"]).get("pipeline_runs", [])
                        out |= {str(r["pipeline_run_id"]) for r in runs if isinstance(r, dict) and r.get("pipeline_run_id")}
                return out
        except Exception:  # noqa: BLE001 - not knowing is reported by the caller as not quiet
            return None

    def path_exists(self, path: str) -> bool:
        return os.path.exists(path)

    def files_changed_since(self, sources: list[str], since: dt.datetime) -> list[tuple[str, dt.datetime]]:
        """Files under each source path (a file or a directory) modified after `since`."""
        out = []
        for src in sources:
            paths = [Path(src)] if Path(src).is_file() else [q for q in Path(src).rglob("*") if q.is_file()]
            for q in paths:
                try:
                    t = dt.datetime.fromtimestamp(q.stat().st_mtime, dt.timezone.utc)
                except OSError:
                    continue
                if t > since:
                    out.append((str(q), t))
        return out

    def wyoming(self, host: str, port: int) -> bool:
        if host not in LOCAL_HOSTS:
            raise StepFailed(f"refused: {host} is not this host's loopback")
        try:
            with socket.create_connection((host, port), timeout=5) as s:
                s.sendall(b'{"type": "describe"}\n')
                s.settimeout(5)
                data = s.recv(4096)
            return b'"info"' in data
        except OSError:
            return False

    def notify(self, tag: str, title: str, body: str) -> bool:
        lib = self.repo / "scripts" / "lib" / "notify.sh"
        r = self.run(["bash", "-c", 'source "$0"; load_ha_token && ha_notify "$1" "$2" "$3"',
                      str(lib), tag, title, body], timeout=60)
        return r.ok

    def sleep(self, s: float) -> None:
        time.sleep(s)

    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)

    def local_now(self) -> dt.datetime:
        """This host's local time (the night window is local: the house sleeps by the clock on the wall)."""
        return dt.datetime.now().astimezone()


class StepFailed(RuntimeError):
    pass


def env_value(path: Path, key: str) -> str | None:
    try:
        for ln in path.read_text().splitlines():
            k, sep, v = ln.partition("=")
            if sep and k.strip() == key:
                return v.strip()
    except OSError:
        return None
    return None


# ═══ Status file, log, notices ═══════════════════════════════════════════

def state_dir(repo: Path = REPO) -> Path:
    return Path(os.getenv("KRONK_UPGRADE_STATE", str(repo / "data" / "upgrades")))


class StatusCorrupt(RuntimeError):
    """The status file exists and cannot be read: never read as empty, it
    may hold a failed-rollback block or an interrupted run's record (F9)."""


def load_status(sd: Path) -> dict:
    path = sd / "status.json"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise StatusCorrupt(f"{path} cannot be read ({e})") from e
    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as e:
        raise StatusCorrupt(f"{path} is not UTF-8 ({e})") from e
    except ValueError as e:
        raise StatusCorrupt(f"{path} is not valid JSON ({e})") from e
    if not isinstance(data, dict):
        raise StatusCorrupt(f"{path} holds a {type(data).__name__}, not an object")
    bad = status_shape(data)
    if bad:
        raise StatusCorrupt(f"{path} is not shaped as this script writes it: {'; '.join(bad[:4])}")
    return data


def status_shape(d: dict) -> list[str]:
    """What in a parsed status file is not of the type this script writes
    (checked once, at load: the rest of the code may then trust it, K11).
    Keys it does not know are left alone."""
    bad: list[str] = []

    def want(path: str, v, kind: type, opt: bool = False) -> bool:
        if (v is None and opt) or (isinstance(v, kind) and (kind is bool or not isinstance(v, bool))):
            return v is not None
        bad.append(f"{path} is {type(v).__name__}, not {kind.__name__}")
        return False
    if "updated" in d:
        want("updated", d["updated"], str)
    if want("months", d.get("months", {}), dict):
        for k, m in d.get("months", {}).items():
            if want(f"months.{k}", m, dict):
                want(f"months.{k}.attempts", m.get("attempts"), int)
                if "done" in m:
                    want(f"months.{k}.done", m["done"], bool)
                for key in ("held_back", "components"):
                    if key in m:
                        want(f"months.{k}.{key}", m[key], dict)
    for key in ("intent", "rollback_failed", "out_of_step", "last_run", "major", "acknowledged", "unreported"):
        want(key, d.get(key), dict, opt=True)
    if isinstance(d.get("intent"), dict):
        for k in ("component", "from", "to", "step"):
            want(f"intent.{k}", d["intent"].get(k), str)
    for key, fields in (("attention", ("id", "text")), ("pending_notices", ("id", "tag", "title", "body"))):
        if want(key, d.get(key, []), list):
            for i, a in enumerate(d.get(key, [])):
                if want(f"{key}[{i}]", a, dict):
                    for f in fields:
                        want(f"{key}[{i}].{f}", a.get(f), str)
    u = d.get("unreported")
    if isinstance(u, dict) and want("unreported.outcomes", u.get("outcomes", []), list):
        names = set(Outcome.__dataclass_fields__)
        for i, o in enumerate(u.get("outcomes", [])):
            if want(f"unreported.outcomes[{i}]", o, dict):
                if not set(o) <= names:
                    bad.append(f"unreported.outcomes[{i}] has keys {sorted(set(o) - names)}")
                for f in ("name", "frm", "to", "result"):
                    want(f"unreported.outcomes[{i}].{f}", o.get(f), str)
    return bad


def status_corrupt(ops: Ops, e: StatusCorrupt, log, notify: bool) -> int:
    log(f"STOPPED: {e}. It is not read as empty (it may hold a failed-rollback block or an interrupted run's "
        f"record). Nothing was done. Look at it, then fix it or move it aside.")
    if notify:
        try:
            ops.notify(NOTIFY_TAG, "Kronk upgrade: STOPPED, the status file is unreadable",
                       f"{e}. Nothing was done; later runs stop the same way until it is fixed or moved aside. "
                       f"/services shows the same warning.")
        except Exception as ex:  # noqa: BLE001
            log(f"notice: notifier raised {ex!r}")
    return 2


def save_status(sd: Path, status: dict, now: dt.datetime) -> None:
    sd.mkdir(parents=True, exist_ok=True)
    status["updated"] = now.isoformat(timespec="seconds")
    fd, tmp = tempfile.mkstemp(dir=sd, prefix=".status.")
    with os.fdopen(fd, "w") as f:
        json.dump(status, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, sd / "status.json")


def block(status: dict, o, now: dt.datetime) -> None:
    """A failed rollback, or git and what runs out of step: later runs
    decline until `ack`, and /services shows it."""
    key = o.result                       # rollback_failed | out_of_step
    status[key] = {"component": o.name, "at": now.isoformat(timespec="seconds"), "state": o.state,
                   "detail": o.detail}
    word = "rollback FAILED" if key == "rollback_failed" else "OUT OF STEP"
    add_attention(status, key, f"Upgrade {word} for {o.name}: {o.state}. {_cut(o.detail, 300)}. "
                  f"Later runs decline until `python3 scripts/upgrades.py ack`.", now)


def add_attention(status: dict, key: str, text: str, now: dt.datetime) -> None:
    items = [a for a in status.get("attention", []) if a.get("id") != key]
    items.append({"id": key, "at": now.isoformat(timespec="seconds"), "text": text})
    status["attention"] = items


class RunLog:
    """One log per run: stdout always, the file unless dry-run."""

    def __init__(self, path: Path | None):
        self.path = path
        self.lines: list[str] = []
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, msg: str = "") -> None:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%SZ")
        line = f"{stamp} {msg}" if msg else ""
        self.lines.append(line)
        print(line, flush=True)
        if self.path:
            with self.path.open("a") as f:
                f.write(line + "\n")


def deliver(ops: Ops, status: dict, notice: dict, now: dt.datetime, log: RunLog) -> bool:
    """Send one notice; on failure keep it pending (retried by `notices`)
    and raise the /services warning."""
    ok = False
    try:
        ok = ops.notify(notice["tag"], notice["title"], notice["body"])
    except Exception as e:  # noqa: BLE001 - a notifier that raises is a notifier that failed
        log(f"notice: notifier raised {e!r}")
    if ok:
        log(f"notice sent: {notice['title']}")
        return True
    notice = {**notice, "attempts": notice.get("attempts", 0) + 1, "last_try": now.isoformat(timespec="seconds")}
    pend = [n for n in status.get("pending_notices", []) if n.get("id") != notice["id"]]
    pend.append(notice)
    status["pending_notices"] = pend
    add_attention(status, f"notice:{notice['id']}",
                  f"Notice not delivered (HA did not take it): {notice['title']}. Retried hourly; "
                  f"text in data/upgrades/status.json.", now)
    log(f"notice NOT delivered, kept for retry: {notice['title']}")
    return False


def retry_pending(ops: Ops, status: dict, now: dt.datetime, log: RunLog) -> int:
    """Retry every pending notice; returns how many are still pending."""
    pend = list(status.get("pending_notices", []))
    if not pend:
        return 0
    still = []
    for n in pend:
        ok = False
        try:
            ok = ops.notify(n["tag"], n["title"], n["body"])
        except Exception as e:  # noqa: BLE001
            log(f"notice retry: notifier raised {e!r}")
        if ok:
            log(f"pending notice delivered: {n['title']}")
            status["attention"] = [a for a in status.get("attention", []) if a.get("id") != f"notice:{n['id']}"]
        else:
            still.append({**n, "attempts": n.get("attempts", 0) + 1, "last_try": now.isoformat(timespec="seconds")})
    status["pending_notices"] = still
    return len(still)


# ═══ Planning ════════════════════════════════════════════════════════════

@dataclass
class Plan:
    comp: dict
    action: str                    # upgrade | current | skip | report | excluded | none
    pin: str | None = None
    to: Release | None = None
    detail: str = ""
    notes: list[str] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)   # lockfile patch lines


def plan_component(rules: Rules, comp: dict, repo: Path, web: Web, now: dt.datetime) -> Plan:
    name = comp["name"]
    if comp["minor"] == "excluded":
        return Plan(comp, "excluded", detail=comp.get("why", "excluded"))
    if comp["kind"] == "lockfile":
        return plan_lockfile(rules, comp, repo, web, now)
    if comp["minor"] == "none":
        return Plan(comp, "none", detail=comp.get("why", "nothing is minor for this component"))
    try:
        pin = read_pin(repo, comp)
    except (RulesError, OSError) as e:
        return Plan(comp, "skip", detail=str(e))
    if vkey(comp, pin.value) is None:
        return Plan(comp, "skip", pin.value,
                    detail=f"pin {pin.value!r} ({pin.file}:{pin.line}) is not an exact version under the rule; never changed")
    if comp["minor"] == "report":
        return Plan(comp, "report", pin.value, detail="report only")
    try:
        releases = image_releases(web, comp, pin.value)
        notes: list[str] = []
        # Newest first; walk down until one is old enough and exists.
        pool = sorted((r for r in releases if not r.prerelease and classify(comp, pin.value, r.version) == "minor"),
                      key=lambda r: vkey(comp, r.version), reverse=True)
        if not pool:
            return Plan(comp, "current", pin.value, detail="no newer release inside the minor line")
        gkind, grepo = _kind_src(comp.get("registry", ""))
        ghcr_seen = set(ghcr_tags(web, grepo)) if gkind == "ghcr" else None
        min_age = rules.settings["min_age_days"]
        grace = dt.timedelta(hours=rules.settings.get("moved_tag_grace_hours", 48))
        two = separate_sources(comp)
        for r in pool:
            # Taken only when the release list and the registry both have
            # it, both dates at least min_age_days old, and the registry's
            # tag was not pushed later than the release (a moved tag) (F12).
            fill_date(web, comp, r)
            if not r.listed:
                notes.append(f"{r.version}: in the registry but not in the release list ({comp.get('releases')}), not taken")
                continue
            if r.prerelease:
                notes.append(f"{r.version}: marked pre-release, not taken")
                continue
            if r.date is None:
                notes.append(f"{r.version}: release date unknown, not taken")
                continue
            age = (now - r.date).days
            if age < min_age:
                notes.append(f"{r.version}: {age} days old, under {min_age}")
                continue
            has, reg_date, digest = registry_info(web, comp, r.version, ghcr_seen)
            if not has:
                notes.append(f"{r.version}: not in the registry {comp.get('registry')}")
                continue
            if two:
                if reg_date is None:
                    notes.append(f"{r.version}: the registry's date for the tag is unknown, not taken")
                    continue
                if reg_date > r.date + grace:
                    notes.append(f"{r.version}: MOVED TAG: the registry's tag is from {reg_date.isoformat(timespec='minutes')}, "
                                 f"after the release ({r.date.isoformat(timespec='minutes')}); not taken")
                    continue
                if (now - reg_date).days < min_age:
                    notes.append(f"{r.version}: the registry's tag is {(now - reg_date).days} days old, under {min_age}")
                    continue
            r.reg_date, r.digest = reg_date, digest
            return Plan(comp, "upgrade", pin.value, r,
                        f"{pin.value} -> {r.version} (released {r.date.date()}, {age} days"
                        + (f"; registry {reg_date.date()}" if two and reg_date else "") + ")", notes)
        return Plan(comp, "current", pin.value, detail="newer minor releases exist, none taken yet", notes=notes)
    except (LookupFailed, RulesError) as e:
        return Plan(comp, "skip", pin.value, detail=f"lookup failed: {e}")
    except Exception as e:  # noqa: BLE001 - an answer of a shape no check foresaw: skip the component, not the run
        return Plan(comp, "skip", pin.value, detail=f"lookup failed: unexpected answer ({e.__class__.__name__}: {e})")


def safe_plan(rules: Rules, comp: dict, repo: Path, web: Web, now: dt.datetime) -> Plan:
    """plan_component, and whatever it raises is that component's skip."""
    try:
        return plan_component(rules, comp, repo, web, now)
    except Exception as e:  # noqa: BLE001
        return Plan(comp, "skip", detail=f"planning failed: {e.__class__.__name__}: {e}")


def plan_lockfile(rules: Rules, comp: dict, repo: Path, web: Web, now: dt.datetime) -> Plan:
    try:
        pins = read_lock(repo, comp)
    except OSError as e:
        return Plan(comp, "skip", detail=str(e))
    lines, notes = [], []
    for pkg, ver in sorted(pins.items()):
        if vkey(comp, ver) is None:
            notes.append(f"{pkg}=={ver}: not an exact X.Y[.Z] version; skipped")
            continue
        try:
            rels = pypi_releases(web, pkg)
            r, _ = newest(comp, ver, rels, "minor", now, rules.settings["min_age_days"])
        except Exception as e:  # noqa: BLE001 - LookupFailed, or an answer of a shape no check foresaw
            notes.append(f"{pkg}: lookup failed ({e})")
            continue
        if r:
            lines.append(f"{pkg} {ver} -> {r.version}")
    return Plan(comp, "report", f"{len(pins)} packages", detail=f"{len(lines)} patch release(s) available; report only",
                notes=notes, reports=lines)


# ═══ Declines ════════════════════════════════════════════════════════════

# A chat or voice request in nginx's access log (combined format). Group 1 is
# the path: requests under /dev/ are the dev path (the replay's, a test's)
# and not the household's.
CHAT_ROUTE = re.compile(r'"(?:POST|GET) (/(?:dev/)?(?:api/chat|message|v1/chat/completions|voice)\b\S*)')
UA_FIELD = re.compile(r'"([^"]*)"\s*$')       # the last quoted field: the User-Agent


@dataclass
class Own:
    """What this run itself sends, so the house check does not read the
    run's own work as the household being busy (sprint M18 fix 1, F1)."""
    ua: str | None = None                       # User-Agent of its own requests to production
    runs: set[str] = field(default_factory=set)  # HA pipeline run ids of its own replays


def house_busy(ops: Ops, rules: Rules, now: dt.datetime, own: Own | None = None,
               info: list[str] | None = None) -> list[str]:
    """Reasons the household is busy now (empty = quiet). A check that
    cannot be read is a reason: not knowing is not quiet. Not the
    household's, and so not counted: requests under /dev/, requests that
    carry this run's own User-Agent, and held runs on a dev pipeline or from
    this run's replays. What was set aside is appended to `info`."""
    why, quiet = [], rules.settings["quiet_minutes"]
    own = own or Own()
    try:
        states = ops.ha_get("/api/states")
        for s in states:
            eid, st = s.get("entity_id", ""), s.get("state")
            if eid.startswith("assist_satellite."):
                if st != "idle":
                    why.append(f"{eid} is {st}")
                ch = _date(s.get("last_changed"))
                if ch and (now - ch).total_seconds() < quiet * 60:
                    why.append(f"{eid} changed state {int((now - ch).total_seconds())}s ago")
            elif eid.startswith("media_player.") and st in ("playing", "buffering"):
                why.append(f"{eid} is {st}")
    except (StepFailed, ValueError, AttributeError, TypeError) as e:
        why.append(f"cannot read HA's states ({e}): cannot tell whether a satellite or a player is busy")
    status, body = ops.http_get("http://localhost/probe/voice_tracer")
    try:
        held = json.loads(body).get("held") if status == 200 else None
    except (ValueError, AttributeError):
        held = None
    if not isinstance(held, int):
        why.append(f"cannot read voice_tracer's probe (HTTP {status})")
    elif held:
        why += _held_not_ours(ops, rules, held, own, info)
    logs = ops.docker("logs", "--since", f"{quiet}m", "kronk-nginx-1", timeout=30)
    if not logs.ok:
        why.append(f"cannot read nginx's log ({logs.tail(120)})")
    else:
        hits, dev_n, own_n = [], 0, 0
        for ln in (logs.out + logs.err).splitlines():
            m = CHAT_ROUTE.search(ln)
            if not m:
                continue
            if m.group(1).startswith("/dev/"):
                dev_n += 1
                continue
            ua = UA_FIELD.search(ln)
            if own.ua and ua and ua.group(1) == own.ua:
                own_n += 1
                continue
            hits.append(ln)
        if hits:
            why.append(f"{len(hits)} chat/voice request(s) in the last {quiet} minutes")
        if info is not None and (dev_n or own_n):
            info.append(f"nginx: set aside {dev_n} request(s) on the dev path (/dev/) and {own_n} of this run's own "
                        f"(User-Agent {own.ua})" if own.ua else
                        f"nginx: set aside {dev_n} request(s) on the dev path (/dev/)")
    return why


def _held_not_ours(ops: Ops, rules: Rules, held: int, own: Own, info: list[str] | None) -> list[str]:
    """voice_tracer holds `held` runs: the ones that are not on a dev
    pipeline and not from this run's replays are the household's."""
    ids = ops.voice_tracer_held_ids(rules.settings["voice_tracer_state"])
    if ids is None:
        return [f"voice_tracer holds {held} run(s) in the correction window and its state file "
                f"({rules.settings['voice_tracer_state']}) cannot be read to tell whose"]
    names = rules.settings["dev_pipelines"]
    dev = ops.dev_pipeline_run_ids(names)
    theirs = [i for i in ids if i not in own.runs and i not in (dev or set())]
    why = []
    if theirs:
        why.append(f"voice_tracer holds {len(theirs)} run(s) in the correction window that are not on "
                   f"{', '.join(names)} or from this run's replay"
                   + ("" if dev is not None else " (HA's list of dev pipeline runs could not be read)"))
    if held > len(ids):
        why.append(f"voice_tracer holds {held - len(ids)} run(s) its state file does not list")
    aside = len(ids) - len(theirs)
    if aside and info is not None:
        info.append(f"voice_tracer: set aside {aside} held run(s) on {', '.join(names)} or from this run's replay")
    return why


def outside_window(ops: Ops, rules: Rules) -> str | None:
    """None inside the night window; else why not (F8). The window may cross midnight."""
    start, end = rules.settings["night_window_t"]
    t = ops.local_now().time()
    inside = start <= t < end if start < end else (t >= start or t < end)
    if inside:
        return None
    return (f"outside the night window {start.strftime('%H:%M')}-{end.strftime('%H:%M')} local "
            f"(now {t.strftime('%H:%M')}): the timer tries the next night")


def tree_state(ops: Ops, rules: Rules) -> list[str]:
    why = []
    try:
        branch = ops.git("rev-parse", "--abbrev-ref", "HEAD").strip()
    except StepFailed as e:
        return [f"cannot read the git branch ({e})"]
    want = rules.settings["expected_branch"]
    if branch != want:
        why.append(f"on branch {branch!r}, expected {want!r} (KRONK_UPGRADE_BRANCH / expected_branch)")
    dirty = [ln for ln in ops.git("status", "--porcelain", "--untracked-files=normal").splitlines() if ln.strip()]
    if dirty:
        why.append(f"working tree not clean ({len(dirty)} path(s): {', '.join(l[3:] for l in dirty[:5])})")
    return why


def run_declines(ops: Ops, rules: Rules, status: dict, now: dt.datetime, own: Own | None = None,
                 info: list[str] | None = None) -> list[str]:
    why = run_declines_blocked(status)
    procs = ops.pgrep(r"scripts/deploy_tag\.sh (deploy|plan|seed)")
    if procs:
        why.append(f"a deploy is in progress ({procs[0][:120]})")
    why += tree_state(ops, rules)
    why += house_busy(ops, rules, now, own, info)
    return why


def run_declines_blocked(status: dict) -> list[str]:
    """Blocks an earlier run left: only `ack` clears them."""
    why = []
    if status.get("rollback_failed"):
        rf = status["rollback_failed"]
        why.append(f"the last rollback failed ({rf.get('component')}: {'; '.join(x for x in (rf.get('state'), _cut(rf.get('detail', ''), 160)) if x)}); "
                   f"fix it, then `python3 scripts/upgrades.py ack`")
    if status.get("out_of_step"):
        oo = status["out_of_step"]
        why.append(f"an earlier run left {oo.get('component')} out of step ({oo.get('state', '')}); "
                   f"fix it, then `python3 scripts/upgrades.py ack`")
    return why


DEPLOY_ROW = re.compile(r"^(\S+)\s+(\S+)\s+(tag (\S+)|working tree|other: \S+)")


def production_tag(ops: Ops, rules: Rules) -> tuple[str | None, list[str]]:
    """The tag production's orchestrator runs, and reasons the Kronk stack
    is not in a state an unattended deploy may touch."""
    r = ops.deploy_tag("status")
    if not r.ok:
        return None, [f"deploy_tag.sh status failed: {r.tail(200)}"]
    why, tag = [], None
    for ln in r.out.splitlines()[1:]:
        m = DEPLOY_ROW.match(ln)
        if not m:
            continue
        name, svc, frm, t = m.groups()
        if svc in rules.settings["dev_services"]:
            continue
        if t is None:
            why.append(f"{name} runs from the {frm}, not a tag")
        if svc == "orchestrator":
            tag = t
    if tag is None and not why:
        why.append("no production orchestrator container found")
    return tag, why


def undeployed(ops: Ops, rules: Rules, tag: str) -> list[str]:
    """Paths that differ between `tag` and HEAD and would reach the Kronk
    containers if HEAD were deployed."""
    names = [n for n in ops.git("diff", "--name-only", "-z", tag, "HEAD").split("\0") if n]
    pats = rules.settings["inert_paths"]
    return [n for n in names if not any(fnmatch.fnmatch(n, p) for p in pats)]


def bind_files_unreleased(ops: Ops, comp: dict) -> str | None:
    """None when no file the container bind-mounts from the working tree
    changed after the container started; else why not (F10). The config
    hash does not cover the contents of bind-mounted files, and a recreate
    loads whatever the working tree holds: a file modified after the start
    is one the running service may never have loaded. Compared by
    modification time with the container's start (a touched but unchanged
    file declines too: errs toward not starting). Paths the service reloads
    by itself while it runs (the component's `live_binds`) are set aside:
    the running service already has them."""
    c = comp["container"]
    st = ops.docker("inspect", c, "-f", "{{.State.StartedAt}}", timeout=30)
    mi = ops.docker("inspect", c, "-f", "{{json .Mounts}}", timeout=30)
    try:
        started = _date(st.out.strip()) if st.ok else None
        mounts = json.loads(mi.out) if mi.ok else None
        repo = ops.repo.resolve()
        live = [(repo / q).resolve() for q in comp.get("live_binds", [])]
        srcs = [m["Source"] for m in mounts if m.get("Type") == "bind" and Path(m["Source"]).resolve().is_relative_to(repo)
                and not any(Path(m["Source"]).resolve().is_relative_to(q) for q in live)]
    except (ValueError, TypeError, KeyError, AttributeError, LookupFailed) as e:
        return f"cannot read {c}'s start time and mounts ({e})"
    if started is None or mounts is None:
        return f"cannot read {c}'s start time and mounts"
    changed = ops.files_changed_since(srcs, started)
    if not changed:
        return None
    names = [shown_path(Path(f), ops.repo) for f, _ in sorted(changed, key=lambda x: x[1])]
    return (f"{', '.join(names[:4])}{' …' if len(names) > 4 else ''} changed after {c} started "
            f"({started.isoformat(timespec='seconds')}; last change {max(t for _, t in changed).isoformat(timespec='seconds')}): "
            f"a recreate would load {'it' if len(names) == 1 else 'them'} unreleased")


def config_hash(ops: Ops, comp: dict) -> tuple[str | None, str | None]:
    """(the config hash compose gives the service from the file on disk and
    its env file, None) or (None, why it could not be read)."""
    r = ops.compose(comp["project"], comp["file"], "config", "--hash", comp["service"], env_file=comp.get("env_file"),
                    timeout=60)
    if not r.ok:
        return None, f"compose config --hash failed: {r.tail(160)}"
    return (r.out.split()[-1] if r.out.split() else ""), None


def label_hash(ops: Ops, comp: dict) -> tuple[str | None, str | None]:
    """(the config hash the running container was created with, None) or (None, why not)."""
    i = ops.docker("inspect", comp["container"], "-f", '{{index .Config.Labels "com.docker.compose.config-hash"}}',
                   timeout=30)
    if not i.ok:
        return None, f"cannot inspect {comp['container']}: {i.tail(160)}"
    return i.out.strip(), None


def compose_hash_ok(ops: Ops, comp: dict) -> str | None:
    """None when the file at HEAD describes what runs; else why not."""
    want, err = config_hash(ops, comp)
    have, err2 = label_hash(ops, comp) if not err else (None, None)
    if err or err2:
        return err or err2
    if have != want:
        return (f"{comp['file']} at HEAD does not describe the running {comp['container']} "
                f"(config hash differs): it has changes that were never brought up")
    return None


# ═══ Bringing a version up, verifying it, rolling it back ═══════════════

@dataclass
class Ctx:
    ops: Ops
    rules: Rules
    log: RunLog
    sd: Path
    stamp: str
    now: dt.datetime
    prod_tag: str | None = None
    baseline: list[dict] | None = None
    ha_before: dict | None = None
    ha_old_id: str | None = None
    ha_restart_at: str | None = None      # ISO time just before the stop, for `docker events --since`
    own: Own = field(default_factory=Own)  # this run's own requests and replay runs (F1)
    touched: bool = False                  # the component's bring-up changed what runs (F2, F4)
    redeploys: int = 0                     # production (Kronk stack) deploys made by this run (F11)
    status: dict | None = None             # the status file's content; carries the intent record (F3)
    plan: "Plan | None" = None             # the component being applied (its planned digest, F12)
    ha_binds: list | None = None           # (source, target) of HA's bind mounts, from the compose file (F5)
    outside_window: str | None = None      # set when the run is outside the night window (F8)
    ignore_window: bool = False            # --ignore-window: a run by hand at any hour
    player_notes: list = field(default_factory=list)   # player differences a rollback's check reports (K4)
    ha_back: str | None = None             # a recovery brought a stopped or missing HA back (G2)


# The intent record (F3): before each step that changes git or what runs,
# the run writes what it is about to do into the status file; it clears it
# when the component is finished, whichever way. A run that dies (reboot,
# OOM, systemctl stop, the unit's timeout) leaves it behind, and the next run
# reads it before anything else (recover()).

def set_intent(ctx: Ctx, comp: dict, frm: str, to: str, step: str, **extra) -> None:
    if ctx.status is None:
        return
    it = ctx.status.get("intent") or {}
    it.update({"component": comp["name"], "from": frm, "to": to, "deploy": comp.get("deploy"), "step": step,
               "prev_tag": ctx.prod_tag, "ha_old_id": ctx.ha_old_id, "run": ctx.stamp,
               "at": ctx.ops.now().isoformat(timespec="seconds"), **extra})
    ctx.status["intent"] = it
    save_status(ctx.sd, ctx.status, ctx.ops.now())


def step(ctx: Ctx, name: str, **extra) -> None:
    it = (ctx.status or {}).get("intent")
    if it is None:
        return
    it.update({"step": name, "ha_old_id": ctx.ha_old_id, "at": ctx.ops.now().isoformat(timespec="seconds"), **extra})
    save_status(ctx.sd, ctx.status, ctx.ops.now())


def clear_intent(ctx: Ctx) -> None:
    if ctx.status is not None and ctx.status.pop("intent", None) is not None:
        save_status(ctx.sd, ctx.status, ctx.ops.now())


def image_is(ctx: Ctx, comp: dict, version: str) -> str | None:
    want = f"{comp['image']}:{version}"
    r = ctx.ops.docker("inspect", comp["container"], "-f", "{{.Config.Image}}", timeout=30)
    if not r.ok:
        return f"cannot inspect {comp['container']}: {r.tail(160)}"
    if r.out.strip() != want:
        return f"{comp['container']} runs {r.out.strip()}, not {want}"
    return None


def health(ctx: Ctx, comp: dict, wait_s: float = 300, step: float = 10) -> list[str]:
    """Every health check of the component, each retried until wait_s."""
    problems = []
    tries = max(1, int(wait_s // step)) + 1
    for chk in comp.get("health", []):
        for i in range(tries):
            last = _one_health(ctx, chk)
            if last is None or i == tries - 1:
                break
            ctx.ops.sleep(step)
        if last:
            problems.append(last)
    return problems


def _one_health(ctx: Ctx, chk: dict) -> str | None:
    ops, t = ctx.ops, chk["type"]
    if t == "http":
        status, _ = ops.http_get(chk["url"])
        return None if status == 200 else f"{chk['url']}: HTTP {status}"
    if t == "docker-healthy":
        r = ops.docker("inspect", chk["container"], "-f", "{{.State.Health.Status}}", timeout=30)
        return None if r.ok and r.out.strip() == "healthy" else f"{chk['container']}: {r.out.strip() or r.tail(100)}"
    if t == "wyoming":
        return None if ops.wyoming(chk["host"], chk["port"]) else f"wyoming {chk['host']}:{chk['port']}: no info answer"
    if t == "exec":
        r = ops.docker("exec", chk["container"], *chk["cmd"], timeout=60)
        what = " ".join(chk["cmd"])
        what = what if len(what) <= 60 else what[:57] + "..."
        return None if r.ok else f"check `{what}` in {chk['container']} failed (exit {r.rc}){': ' + r.tail(160) if r.tail(160) else ''}"
    if t == "ha-api":
        try:
            ops.ha_get("/api/")
            return None
        except (StepFailed, ValueError) as e:
            return f"HA API: {e}"
    if t == "running":
        r = ops.docker("inspect", chk["container"], "-f", "{{.State.Running}}", timeout=30)
        return None if r.ok and r.out.strip() == "true" else f"{chk['container']} not running"
    return f"unknown health check type {t!r}"


def replay_regressions(baseline: list[dict], rows: list[dict], flappers: list[str]) -> list[str]:
    """Rows that passed in the baseline and fail now, flappers excepted."""
    base = {r["id"]: r.get("status") for r in baseline}
    return [r["id"] for r in rows
            if r.get("status") == "fail" and base.get(r["id"]) in ("pass", "xpass") and r["id"] not in flappers]


REPLAY_RETRIES, REPLAY_RETRY_WAIT_S = 3, 300      # a replay stopped by a player: 3 retries over 15 minutes (K5)


def _replay_rows(out: Path) -> list[dict]:
    rows = json.loads(out.read_text())["rows"]
    if not isinstance(rows, list):
        raise ValueError("rows is not a list")
    return rows


def run_replay(ctx: Ctx, label: str, retry: bool = False) -> tuple[list[dict] | None, str]:
    """The live dry-run replay. retry=True (verify): a replay stopped by a
    player (exit 3) runs again for the rows it did not reach, after 5
    minutes, up to 3 times; only then is it a failure (K5)."""
    out = ctx.sd / "runs" / f"{ctx.stamp}-replay-{label}.json"
    r = ctx.ops.replay(f"upgrade-{label}", out)
    rows: list[dict] = []
    tries = 0

    def failed(why: str):
        _own_runs(ctx, rows)
        return None, why
    try:
        while True:
            if r.rc == 2:
                return failed(f"replay could not complete (exit 2: a safety precondition refused it): {r.tail(200)}")
            if r.rc == 3 and not retry:
                return failed(f"replay could not complete (exit 3: stopped by the player or dry-run check): {r.tail(200)}")
            rows += [x for x in _replay_rows(out) if isinstance(x, dict)]
            if r.rc != 3:
                break
            done = {x.get("id") for x in rows}
            left = [i for i in ctx.ops.corpus_ids() if i not in done]
            if not left:                    # stopped after its last row: the replay is complete
                break
            if tries == REPLAY_RETRIES:
                return failed(f"replay stopped by a player {tries + 1} times over "
                              f"{tries * REPLAY_RETRY_WAIT_S // 60} minutes: {r.tail(200)}")
            tries += 1
            ctx.log(f"  replay stopped by a player after {len(rows)} row(s); retry {tries} of {REPLAY_RETRIES} in "
                    f"{REPLAY_RETRY_WAIT_S // 60} minutes for the other {len(left)}")
            ctx.ops.sleep(REPLAY_RETRY_WAIT_S)
            out = ctx.sd / "runs" / f"{ctx.stamp}-replay-{label}-retry{tries}.json"
            r = ctx.ops.replay(f"upgrade-{label}-retry{tries}", out, ids=left)
    except (OSError, ValueError, KeyError, TypeError) as e:
        return failed(f"replay wrote no readable result ({e}; exit {r.rc}: {r.tail(160)})")
    _own_runs(ctx, rows)
    return rows, ""


def _own_runs(ctx: Ctx, rows: list[dict]) -> None:
    # The runs this replay made: voice_tracer holds them for their correction
    # window, and they are this run's, not the household's.
    ctx.own.runs |= {str(x["pipeline_run_id"]) for x in rows if isinstance(x, dict) and x.get("pipeline_run_id")}


def verify(ctx: Ctx, comp: dict, version: str, full: bool = True) -> list[str]:
    """Everything that must hold for `version` to stay. full=False is the
    rollback's check: image, health, the stack's own checks."""
    problems = []
    if comp.get("image") and comp.get("container"):
        p = image_is(ctx, comp, version)
        if p:
            problems.append(p)
    problems += health(ctx, comp)
    if comp["deploy"] == "ha":
        problems += ha_checks(ctx, comp, players_fail=full)
    if problems or not full:
        return problems
    t = ctx.ops.run_tests()
    if not t.ok:
        problems.append(f"offline corpus failed: {t.tail(300)}")
        return problems
    rows, err = run_replay(ctx, comp["name"], retry=True)
    if rows is None:
        problems.append(err)
    else:
        reg = replay_regressions(ctx.baseline or [], rows, ctx.rules.replay.get("flappers", []))
        if reg:
            problems.append(f"live replay: {len(reg)} row(s) passed before and fail now: {', '.join(reg[:8])}")
    return problems


# Kronk stack: a tag and deploy_tag.sh.

def image_digests(ops: Ops, ref: str) -> tuple[str | None, list[str]]:
    """(local image id, its RepoDigests), or (None, []) when not present."""
    r = ops.docker("image", "inspect", ref, "-f", "{{.Id}} {{json .RepoDigests}}", timeout=30)
    if not r.ok:
        return None, []
    iid, _, rd = r.out.strip().partition(" ")
    try:
        digs = [d for d in (json.loads(rd) or []) if isinstance(d, str)]
    except ValueError:
        digs = []
    return iid or None, digs


def log_kept(ctx: Ctx, comp: dict, frm: str) -> None:
    """The run's log records the image kept for rollback (F12)."""
    iid, digs = image_digests(ctx.ops, f"{comp['image']}:{frm}")
    ctx.log(f"  kept for rollback: {comp['image']}:{frm} = {iid or 'NOT PRESENT LOCALLY'}"
            + (f" ({', '.join(digs)})" if digs else ""))


def check_pulled(ctx: Ctx, comp: dict, to: str) -> str | None:
    """Log the digest pulled; a digest other than the registry's at planning
    means the tag moved in between (F12)."""
    iid, digs = image_digests(ctx.ops, f"{comp['image']}:{to}")
    want = ctx.plan.to.digest if ctx.plan and ctx.plan.to else None
    ctx.log(f"  image pulled: {comp['image']}:{to} = {iid or 'NOT PRESENT LOCALLY'}"
            + (f" ({', '.join(digs)})" if digs else "") + f"; the registry gave {want or 'no digest'} at planning")
    if iid is None:
        return f"{comp['image']}:{to} is not present locally after the pull"
    if want and digs and not any(d.endswith("@" + want) for d in digs):
        return f"the tag {comp['image']}:{to} moved between planning ({want}) and the pull ({', '.join(digs)})"
    return None


def kronk_up(ctx: Ctx, comp: dict, tag: str) -> str | None:
    if comp.get("image") and ctx.plan:
        # The image first, by a plain pull, and its digest against the
        # registry's at planning: a moved tag is "not applied" before any
        # tag or deploy (K7). deploy_tag.sh then finds it present.
        ref = f"{comp['image']}:{ctx.plan.to.version}"
        step(ctx, "pull")
        r = ctx.ops.docker("pull", ref, timeout=900)
        ctx.log(f"  docker pull {ref}: exit {r.rc}")
        if not r.ok:
            return f"docker pull {ref} failed: {r.tail(200)}"
        moved = check_pulled(ctx, comp, ctx.plan.to.version)
        if moved:
            return moved
    step(ctx, "tag", tag=tag)
    head = ctx.ops.git("rev-parse", "HEAD").strip()
    have = ctx.ops.git("rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}", check=False).strip()
    if have and have != head:
        return f"tag {tag} exists at {have[:10]}, not at the pin commit {head[:10]}"
    if not have:
        ctx.ops.git("tag", tag)
    if comp.get("image") and ctx.plan:
        log_kept(ctx, comp, ctx.plan.pin)
    ctx.log(f"  deploy_tag.sh deploy {tag}")
    step(ctx, "deploy", tag=tag)
    ctx.touched = True
    ctx.redeploys += 1
    r = ctx.ops.deploy_tag("deploy", tag)
    for ln in r.tail(1500).splitlines()[-12:]:
        ctx.log(f"    | {ln}")
    if not r.ok:
        return f"deploy_tag.sh deploy {tag} failed (exit {r.rc})"
    return None


# Telemetry (and the rehearsal): compose pull + up of the one service.

def compose_up(ctx: Ctx, comp: dict) -> str | None:
    if ctx.plan:
        log_kept(ctx, comp, ctx.plan.pin)
    for args in (("pull", comp["service"]), ("up", "-d", "--no-deps", comp["service"])):
        step(ctx, args[0])
        if args[0] == "up":
            moved = check_pulled(ctx, comp, ctx.plan.to.version) if ctx.plan else None
            if moved:
                return moved
            ctx.touched = True
        r = ctx.ops.compose(comp["project"], comp["file"], *args, env_file=comp.get("env_file"))
        ctx.log(f"  docker compose -p {comp['project']} {' '.join(args)}: exit {r.rc}")
        if not r.ok:
            return f"compose {' '.join(args)} failed: {r.tail(200)}"
    return None


# Home Assistant: docs/sprint/M12-fix1.md H6-H8 (with M12-fix2's corrections) in code.

def ha_snapshot(ctx: Ctx) -> dict:
    ops = ctx.ops
    states = ops.ha_get("/api/states")
    pick = {}
    for s in states:
        eid = s.get("entity_id", "")
        if eid.startswith(("assist_satellite.", "media_player.")) or (
                eid.startswith("select.") and ("_assistant" in eid or "_wake_word" in eid)):
            pick[eid] = {"state": s.get("state"), "last_changed": s.get("last_changed")}
    services = ops.ha_get("/api/services")
    rest = sorted(next((list(d.get("services", {}).keys()) for d in services if d.get("domain") == "rest_command"), []))
    ma = ops.docker("inspect", "music-assistant-server", "-f", "{{.State.StartedAt}}", timeout=30)
    return {"entities": pick, "rest_command": rest, "ma_started": ma.out.strip() if ma.ok else None}


def ha_precheck(ctx: Ctx, snap: dict) -> list[str]:
    """H6: satellites idle, nothing playing, selects untouched for 15 min."""
    why, mins = [], ctx.rules.settings["select_quiet_minutes"]
    for eid, s in snap["entities"].items():
        if eid.startswith("assist_satellite.") and s["state"] != "idle":
            why.append(f"{eid} is {s['state']}")
        if eid.startswith("media_player.") and s["state"] in ("playing", "buffering"):
            why.append(f"{eid} is {s['state']}")
        if eid.startswith("select."):
            ch = _date(s["last_changed"])
            if ch and (ctx.ops.now() - ch).total_seconds() < mins * 60:
                why.append(f"{eid} changed {int((ctx.ops.now() - ch).total_seconds() // 60)} min ago (under {mins})")
    return why


def ha_files_ok(ctx: Ctx, comp: dict) -> list[str]:
    """M12 H2/H7b: the compose file renders, and every path it bind-mounts
    exists (`create_host_path: false` makes Docker refuse to create HA
    without the recordings directory, after HA was stopped)."""
    r = ctx.ops.compose(comp["project"], comp["file"], "config", "--format", "json", timeout=60)
    if not r.ok:
        return [f"{comp['file']} does not render (compose config failed): {r.tail(200)}"]
    try:
        vols = json.loads(r.out)["services"][comp["service"]].get("volumes") or []
        ctx.ha_binds = [(v["source"], v["target"]) for v in vols if isinstance(v, dict) and v.get("type") == "bind"]
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return [f"{comp['file']} renders, but not as expected ({e.__class__.__name__}: {e})"]
    return [f"{src} (bind-mounted by {comp['file']} at {tgt}) does not exist: Docker would refuse to create HA "
            f"after the stop" for src, tgt in ctx.ha_binds if not ctx.ops.path_exists(src)]


def ha_up(ctx: Ctx, comp: dict, frm: str, to: str) -> str | None:
    ops = ctx.ops
    i = ops.docker("inspect", comp["container"], "-f", "{{.Image}}", timeout=30)
    if not i.ok:
        return f"cannot inspect {comp['container']}: {i.tail(160)}"
    ctx.ha_old_id = i.out.strip()
    _, digs = image_digests(ops, ctx.ha_old_id)
    ctx.log(f"  kept for rollback: {comp['image']}:{frm} = {ctx.ha_old_id}" + (f" ({', '.join(digs)})" if digs else "")
            + f", tagged {HA_ROLLBACK_REPO}:{frm}")
    err = _ha_steps(ctx, comp, [("docker", ("tag", ctx.ha_old_id, f"{HA_ROLLBACK_REPO}:{frm}")),
                                ("docker", ("pull", f"{comp['image']}:{to}"))])
    if err:
        return err
    err = check_pulled(ctx, comp, to)
    if err:
        return err
    # H7b, right before the stop: anything missing is "not started", never a stop.
    why = ha_files_ok(ctx, comp)
    n = ops.docker("image", "inspect", f"{comp['image']}:{to}", "-f", "{{.Id}}", timeout=30)
    if not n.ok:
        why.append(f"{comp['image']}:{to} is not present locally after the pull")
    k = ops.docker("image", "inspect", f"{HA_ROLLBACK_REPO}:{frm}", "-f", "{{.Id}}", timeout=30)
    if not k.ok or k.out.strip() != ctx.ha_old_id:
        why.append(f"{HA_ROLLBACK_REPO}:{frm} is not the running image {ctx.ha_old_id} "
                   f"({k.out.strip() or k.tail(100)}): no rollback image")
    if why:
        ctx.log(f"  H7b: {'; '.join(why)}")
        return "not started: the checks before HA's stop failed (M12 H7b): " + "; ".join(why)
    return _ha_steps(ctx, comp, [("docker", ("stop", "-t", "120", comp["container"])),
                                 ("compose", ("up", "-d", comp["service"]))])


def _ha_steps(ctx: Ctx, comp: dict, steps) -> str | None:
    for kind, args in steps:
        step(ctx, f"ha-{args[0]}")
        if kind == "docker" and args[0] == "stop":
            ctx.ha_restart_at = ctx.ops.now().strftime("%Y-%m-%dT%H:%M:%SZ")
            ctx.touched = True
        if kind == "docker":
            r = ctx.ops.docker(*args, timeout=900)
        else:
            r = ctx.ops.compose(comp["project"], comp["file"], *args)
        ctx.log(f"  {kind} {' '.join(args)}: exit {r.rc}")
        if not r.ok:
            return f"{kind} {' '.join(args)} failed: {r.tail(200)}"
    return None


def ha_checks(ctx: Ctx, comp: dict, wait_s: float = 600, step: float = 15, players_fail: bool = True) -> list[str]:
    """H8 against the snapshot taken before the restart. players_fail=False
    (a rollback's check): a player difference is put in ctx.player_notes for
    the notice, never a failure (K4)."""
    ops, before, problems = ctx.ops, ctx.ha_before or {}, []
    ctx.player_notes = []
    r = ops.docker("inspect", comp["container"], "-f", "{{.Config.StopTimeout}}", timeout=30)
    if not r.ok or r.out.strip() != "120":
        problems.append(f"StopTimeout is {r.out.strip() or 'unreadable'}, not 120 (stop_grace_period lost)")
    since = ctx.ha_restart_at or "30m"
    k = ops.docker("events", "--since", since, "--until", "0s", "--filter", f"container={comp['container']}",
                   "--filter", "event=kill", "--format", "{{.Actor.Attributes.signal}}", timeout=30)
    if not k.ok:
        problems.append(f"cannot read docker events for {comp['container']} ({k.tail(100)}): cannot tell whether HA "
                        f"was killed with signal 9")
    elif "9" in k.out.split():
        problems.append("HA was killed with signal 9 (unclean shutdown)")
    # M12 H7: the mounts the compose file gives HA, and no unclean-shutdown line.
    if ctx.ha_binds is None:
        problems += [f"after the start: {w}" for w in ha_files_ok(ctx, comp)]
    mi = ops.docker("inspect", comp["container"], "-f", "{{json .Mounts}}", timeout=30)
    try:
        mounts = {(m.get("Source"), m.get("Destination")) for m in json.loads(mi.out)} if mi.ok else None
    except (ValueError, AttributeError, TypeError):
        mounts = None
    if mounts is None:
        problems.append(f"cannot read {comp['container']}'s mounts")
    else:
        problems += [f"{src} is not mounted at {tgt}" for src, tgt in ctx.ha_binds or [] if (src, tgt) not in mounts]
    lg = ops.docker("logs", "--since", since, comp["container"], timeout=60)
    if not lg.ok:
        problems.append(f"cannot read HA's log since the restart ({lg.tail(100)})")
    elif "was shutdown cleanly" in lg.out + lg.err:
        problems.append("HA's log says the database was not shut down cleanly (\"could not validate that the "
                        "sqlite3 database ... was shutdown cleanly\")")
    tries = max(1, int(wait_s // step)) + 1
    flapping = ctx.rules.settings.get("flapping_players", [])
    for i in range(tries):
        try:
            diff, players = _ha_diff(before, ha_snapshot(ctx), flapping)
        except (StepFailed, ValueError) as e:
            diff, players = [f"HA not answering: {e}"], []
        if not (diff or players) or i == tries - 1:
            break
        ops.sleep(step)
    problems += diff
    if players_fail:
        problems += players
    else:
        ctx.player_notes = players
    c = ops.replay("upgrade-ha-check", ctx.sd / "runs" / f"{ctx.stamp}-replay-check.json", check_only=True)
    if not c.ok:
        problems.append(f"voice_replay --check-only failed (exit {c.rc}): {c.tail(200)}")
    st, body = ops.http_get("http://localhost/probe/voice_tracer")
    try:
        vt = json.loads(body) if st == 200 else {}
    except ValueError:
        vt = {}
    if not vt.get("connected") or vt.get("too_old", 0):
        problems.append(f"voice_tracer not reconnected cleanly: {body[:160] if body else st}")
    return problems


# A player moving between these is not a difference (K4).
RESTING_PLAYER = {"idle", "off", "paused", "standby", "on"}


def _ha_diff(before: dict, after: dict, flapping=()) -> tuple[list[str], list[str]]:
    """(differences, player differences) between two snapshots. Players on
    the rules' flapping_players list are skipped; a move between resting
    states is not a difference (K4)."""
    out, players = [], []
    for eid, b in (before.get("entities") or {}).items():
        a = after["entities"].get(eid)
        if eid.startswith("media_player."):
            if eid in flapping:
                continue
            if a is None:
                players.append(f"{eid} missing after the restart")
            elif a["state"] != b["state"] and not {a["state"], b["state"]} <= RESTING_PLAYER:
                players.append(f"{eid}: {b['state']} before, {a['state']} after")
        elif a is None:
            out.append(f"{eid} missing after the restart")
        elif eid.startswith("assist_satellite.") and a["state"] == "unavailable":
            out.append(f"{eid} unavailable")
        elif eid.startswith("select.") and a["state"] != b["state"]:
            out.append(f"{eid}: {b['state']} before, {a['state']} after")
    if "rest_command" in before and before.get("rest_command") != after.get("rest_command"):
        out.append(f"rest_command services changed: {before.get('rest_command')} -> {after.get('rest_command')}")
    if "ma_started" in before:
        # A read that failed is a failed check, not a skipped one (K12).
        if not before["ma_started"] or not after.get("ma_started"):
            out.append("cannot read Music Assistant's start time: cannot tell whether HA's restart restarted it")
        elif before["ma_started"] != after["ma_started"]:
            out.append("Music Assistant restarted (its start time changed)")
    return out, players


def ha_rollback(ctx: Ctx, comp: dict, frm: str) -> str | None:
    ops, old = ctx.ops, ctx.ha_old_id
    if not old:
        return "no previous image id recorded"
    cur = ops.docker("image", "inspect", f"{comp['image']}:{frm}", "-f", "{{.Id}}", timeout=30)
    steps = []
    if not cur.ok or cur.out.strip() != old:
        steps.append(("docker", ("tag", old, f"{comp['image']}:{frm}")))
    if container_state(ctx, comp)[0] != "absent":          # never `docker stop` a container that does not exist
        steps.append(("docker", ("stop", "-t", "120", comp["container"])))
    steps.append(("compose", ("up", "-d", comp["service"])))
    return _ha_steps(ctx, comp, steps)


# One component, start to end.

@dataclass
class Outcome:
    name: str
    frm: str
    to: str
    # kept | rolled_back | rollback_failed | not_started | not_applied | out_of_step | recovered
    result: str
    detail: str = ""
    commit: str | None = None
    state: str = ""      # the fact that matters (what git has, what runs): first in the notice, never cut


# What Docker says when the container is not there ("Error: No such
# object: x" from `docker inspect`, "No such container: x" from the API).
NO_SUCH = re.compile(r"no such (container|object)", re.I)


def container_state(ctx, comp: dict) -> tuple[str, str | None, str]:
    """(kind, version, phrase) for the component's container, read-only.
    kind is one of: runs (version set), stopped, absent (Docker said there
    is no such container), unknown (the inspect itself failed: never read
    as absent or stopped), none (no container to inspect). The phrase says
    "Home Assistant is down" only when HA is known to be stopped or absent
    (sprint M18 fix 2, G4). `ctx` may be a Ctx or an Ops."""
    ops = getattr(ctx, "ops", ctx)
    c = comp.get("container")
    if not c or not comp.get("image"):
        return "none", None, "unknown (no container to inspect)"
    down = "; Home Assistant is down)" if comp.get("deploy") == "ha" else ")"
    r = ops.docker("inspect", c, "-f", "{{.Config.Image}} {{.State.Running}}", timeout=30)
    if not r.ok:
        if NO_SUCH.search(r.out + r.err):
            return "absent", None, f"nothing ({c} does not exist" + down
        return "unknown", None, f"unknown (cannot inspect {c}: {r.tail(160)})"
    img, _, running = r.out.strip().partition(" ")
    ver = img[len(comp["image"]) + 1:] if img.startswith(comp["image"] + ":") else img
    if running.strip() != "true":
        return "stopped", None, f"nothing ({c} is stopped" + down
    return "runs", ver, ver


def what_runs(ctx, comp: dict) -> tuple[str | None, str]:
    """(the version the component's container runs, or None; a phrase for
    a notice). Read-only; never more than is known (container_state)."""
    _, ver, phrase = container_state(ctx, comp)
    return ver, phrase


def head_pin(ctx: Ctx, comp: dict) -> str | None:
    """The pin as committed at HEAD (not the working tree), or None."""
    try:
        text = ctx.ops.git("show", f"HEAD:{comp['file']}")
    except StepFailed:
        return None
    hits = [m.group(1) for ln in text.splitlines() if (m := re.search(comp["match"], ln))]
    return hits[0] if len(hits) == 1 else None


def apply_one(ctx: Ctx, p: Plan) -> Outcome:
    o = _apply_one(ctx, p)
    clear_intent(ctx)                 # finished, whichever way (a process that dies leaves it)
    return o


def _apply_one(ctx: Ctx, p: Plan) -> Outcome:
    comp, frm, to = p.comp, p.pin, p.to.version
    ctx.plan = p
    ops, log, name = ctx.ops, ctx.log, comp["name"]
    log(f"── {name}: {frm} -> {to}")
    # Preconditions that belong to this component only.
    if not ctx.ignore_window:
        late = outside_window(ops, ctx.rules)
        if late:
            return Outcome(name, frm, to, "not_started", f"the night window closed: {late}")
    procs = ops.pgrep(r"scripts/deploy_tag\.sh (deploy|plan|seed)")
    if procs:
        return Outcome(name, frm, to, "not_started", f"a deploy is in progress ({procs[0][:120]})")
    if comp["deploy"] == "kronk":
        if not ctx.prod_tag:
            return Outcome(name, frm, to, "not_started", "production is not on a tag (see above)")
        left = undeployed(ops, ctx.rules, ctx.prod_tag)
        if left:
            return Outcome(name, frm, to, "not_started",
                           f"HEAD has changes not deployed to production ({ctx.prod_tag}): {', '.join(left[:6])}; "
                           f"an unattended deploy would ship them")
    if comp["deploy"] in ("ha", "compose"):
        why = compose_hash_ok(ops, comp) or bind_files_unreleased(ops, comp)
        if why:
            return Outcome(name, frm, to, "not_started", why)
    if comp["deploy"] == "ha":
        try:
            ctx.ha_before = ha_snapshot(ctx)
        except (StepFailed, ValueError) as e:
            return Outcome(name, frm, to, "not_started", f"cannot snapshot HA before the restart: {e}")
        why = ha_precheck(ctx, ctx.ha_before) + ha_files_ok(ctx, comp)
        if not ctx.ha_before.get("ma_started"):
            why.append("cannot read Music Assistant's start time (docker inspect music-assistant-server): "
                       "the check that HA's restart leaves it alone could not run")
        if why:
            return Outcome(name, frm, to, "not_started", "HA restart declined: " + "; ".join(why))
    info: list[str] = []
    busy = house_busy(ops, ctx.rules, ops.now(), ctx.own, info)
    for n in info:
        log(f"  {n}")
    if busy:
        return Outcome(name, frm, to, "not_started", "house busy: " + "; ".join(busy))

    # Pin, commit. The record says where HEAD was and, for telemetry and HA,
    # the config hash of what runs: a recovery acts only when the world is
    # still what the record says (sprint M18 fix 2, G1).
    extra = {"head": ops.git("rev-parse", "HEAD").strip()}
    if comp["deploy"] in ("ha", "compose"):
        extra["hash_from"], _ = config_hash(ops, comp)
    set_intent(ctx, comp, frm, to, "pin-commit", **extra)
    try:
        write_pin(ops.repo, comp, frm, to)
        ops.git("add", "--", comp["file"])
        ops.git("commit", "-q", "-m", f"upgrade: {name} {frm} -> {to}", "-m",
                f"Monthly patch upgrade by scripts/upgrade_minor.sh (sprint M18). Release: {p.to.url or 'n/a'}. "
                f"Run log: {shown_path(ctx.sd / 'runs' / f'{ctx.stamp}-minor.log', ops.repo)}", "--", comp["file"])
        commit = ops.git("rev-parse", "--short", "HEAD").strip()
    except (StepFailed, RulesError, OSError) as e:
        return pin_not_committed(ctx, comp, frm, to, f"the pin commit failed: {e}")
    log(f"  committed {commit}: upgrade: {name} {frm} -> {to}")
    hashed = {}
    if comp["deploy"] in ("ha", "compose"):
        hashed["hash_to"], err = config_hash(ops, comp)
        if err:
            step(ctx, "bring-up", commit=commit)
            return not_applied(ctx, comp, frm, to, f"after the pin commit: {err}", commit)
    step(ctx, "bring-up", commit=commit, **hashed)

    # Bring up. Until a step changes what runs (ctx.touched), a failure is
    # "not applied": git is put back and nothing is restarted.
    ctx.touched = False
    tag = f"upgrade-{ctx.stamp}-{name}"
    try:
        if comp["deploy"] == "kronk":
            err = kronk_up(ctx, comp, tag)
        elif comp["deploy"] == "ha":
            err = ha_up(ctx, comp, frm, to)
        else:
            err = compose_up(ctx, comp)
    except (StepFailed, RulesError, OSError) as e:
        err = str(e)
    if err and not ctx.touched:
        log(f"  NOT APPLIED (nothing deployed): {err}")
        return not_applied(ctx, comp, frm, to, err, commit)
    step(ctx, "verify")
    problems = [err] if err else verify(ctx, comp, to)
    if not problems:
        log(f"  verified: {name} {to} kept")
        if comp["deploy"] == "kronk":
            ctx.prod_tag = tag
        return Outcome(name, frm, to, "kept", commit=commit)

    log(f"  FAILED: {'; '.join(problems)}")
    step(ctx, "rollback")
    return rollback(ctx, comp, frm, to, problems, commit)


def pin_not_committed(ctx: Ctx, comp: dict, frm: str, to: str, err: str) -> Outcome:
    """The pin edit or its commit failed: put the file back if it was edited."""
    name = comp["name"]
    ctx.log(f"  NOT APPLIED (no commit): {err}")
    try:
        if head_pin(ctx, comp) == frm and read_pin(ctx.ops.repo, comp).value == to:
            write_pin(ctx.ops.repo, comp, to, frm)
            ctx.ops.git("add", "--", comp["file"])          # the index back to HEAD's content too
    except (StepFailed, RulesError, OSError) as e:
        _, runs = what_runs(ctx, comp)
        return Outcome(name, frm, to, "out_of_step", f"{err}; putting the file back failed: {e}",
                       state=f"OUT OF STEP: {comp['file']} has an uncommitted pin edit; git (HEAD) has {head_pin(ctx, comp)}, "
                             f"running is {runs}")
    _, runs = what_runs(ctx, comp)
    return Outcome(name, frm, to, "not_applied", err, state=f"not applied; git is at {head_pin(ctx, comp)}, running is {runs}")


def not_applied(ctx: Ctx, comp: dict, frm: str, to: str, err: str, commit: str) -> Outcome:
    """The pin commit exists and nothing was deployed: revert the run's own
    pin commit, so git is back in step with what runs."""
    ops, name = ctx.ops, comp["name"]
    step(ctx, "revert")
    try:
        head = ops.git("rev-parse", "--short", "HEAD").strip()
        if head != commit:
            raise StepFailed(f"HEAD is {head}, not the pin commit {commit}")
        ops.git("revert", "--no-commit", commit)
        ops.git("commit", "-q", "-m", f"upgrade: not applied, {name} back {to} -> {frm}", "-m",
                f"Reverts {commit}: the upgrade stopped before anything was deployed: {' '.join(err.split())[:300]}")
        rev = ops.git("rev-parse", "--short", "HEAD").strip()
    except (StepFailed, RulesError, OSError) as e:
        _, runs = what_runs(ctx, comp)
        ctx.log(f"  OUT OF STEP: the revert of {commit} failed: {e}")
        return Outcome(name, frm, to, "out_of_step", f"{err}; the revert of {commit} failed: {e}", commit,
                       state=f"OUT OF STEP: git is at {to} (commit {commit}), running is {runs}")
    _, runs = what_runs(ctx, comp)
    ctx.log(f"  reverted {commit} as {rev}: git is at {frm}, running is {runs}")
    return Outcome(name, frm, to, "not_applied", err, commit,
                   state=f"not applied; git is at {frm} again (revert {rev}), running is {runs}")


def rollback(ctx: Ctx, comp: dict, frm: str, to: str, problems: list[str], commit: str) -> Outcome:
    """Undo what the bring-up did. Only called once a step changed what
    runs (ctx.touched): a failure before that is not_applied, not this."""
    ops, log, name = ctx.ops, ctx.log, comp["name"]
    reason = "; ".join(problems)[:300]
    log(f"  rolling back {name} to {frm}")
    try:
        write_pin(ops.repo, comp, to, frm)
        ops.git("add", "--", comp["file"])
        ops.git("commit", "-q", "-m", f"upgrade: roll back {name} {to} -> {frm}", "-m",
                f"Verification failed: {reason}", "--", comp["file"])
        if comp["deploy"] == "kronk":
            log(f"  deploy_tag.sh deploy {ctx.prod_tag} (the previous tag)")
            ctx.redeploys += 1
            r = ops.deploy_tag("deploy", ctx.prod_tag)
            err = None if r.ok else f"deploy of the previous tag {ctx.prod_tag} failed (exit {r.rc}): {r.tail(200)}"
        elif comp["deploy"] == "ha":
            err = ha_rollback(ctx, comp, frm)
        else:
            r = ops.compose(comp["project"], comp["file"], "up", "-d", "--no-deps", comp["service"],
                            env_file=comp.get("env_file"))
            err = None if r.ok else f"compose up of {frm} failed: {r.tail(200)}"
        after = [err] if err else verify(ctx, comp, frm, full=False)
    except (StepFailed, RulesError, OSError) as e:
        after = [f"rollback step raised: {e}"]
    if after:
        why = " ".join("; ".join(after).split())[:300]
        log(f"  ROLLBACK FAILED: {why}")
        _, runs = what_runs(ctx, comp)
        gitp = head_pin(ctx, comp)
        return Outcome(name, frm, to, "rollback_failed",
                       f"upgrade failed: {' '.join(reason.split())}; rollback failed: {why}", commit,
                       state=f"ROLLBACK FAILED; running is {runs}; git has {gitp}")
    log(f"  rolled back and verified: {name} {frm}")
    return Outcome(name, frm, to, "rolled_back", reason + _players_after(ctx), commit, state=f"rolled back to {frm}")


def _players_after(ctx: Ctx) -> str:
    return (f"; after the rollback, players differ (reported, not a failure): {', '.join(ctx.player_notes)}"
            if ctx.player_notes else "")


def error_outcome(ctx: Ctx, p: Plan, e: Exception) -> Outcome:
    """An error escaped a component: say exactly where git and the running
    version stand, never more than is known."""
    comp, frm, to = p.comp, p.pin, p.to.version
    gitp = head_pin(ctx, comp)
    ver, runs = what_runs(ctx, comp)
    dirty = tree_state(ctx.ops, ctx.rules)
    if gitp == frm and ver == frm and not dirty:
        return Outcome(comp["name"], frm, to, "not_started", f"stopped by an error before any change: {e}",
                       state=f"not started; git is at {frm}, running is {runs}")
    return Outcome(comp["name"], frm, to, "out_of_step", f"stopped by an error: {e}",
                   state=f"OUT OF STEP: git is at {gitp}, running is {runs}" + (f"; {'; '.join(dirty)}" if dirty else ""))


# ═══ Recovery from an interrupted run (F3) ══════════════════════════════

def pin_vs_running(ops: Ops, p: Plan) -> tuple[str, str] | None:
    """Planning-time check, every component, every run: the pin at HEAD must
    be what its container runs. A pin commit ahead of what runs is never
    CURRENT. Returns (action, detail) to replace the plan's, or None. A
    container that cannot be inspected is a skip, not a mismatch (G4)."""
    c = p.comp
    if p.action not in ("upgrade", "current") or not c.get("container") or not c.get("image"):
        return None
    kind, ver, runs = container_state(ops, c)
    if ver == p.pin:
        return None
    if kind == "unknown":
        return "skip", f"cannot tell what {c['container']} runs: {runs}"
    return "mismatch", (f"pin {p.pin} ({c['file']}) but {c['container']} runs {runs}: git and what runs are out of step; "
                        f"not upgraded")


def _pin_back(ctx: Ctx, comp: dict, frm: str, to: str, why: str) -> str:
    write_pin(ctx.ops.repo, comp, to, frm)
    ctx.ops.git("add", "--", comp["file"])
    ctx.ops.git("commit", "-q", "-m", f"upgrade: roll back {comp['name']} {to} -> {frm}", "-m", why, "--", comp["file"])
    return ctx.ops.git("rev-parse", "--short", "HEAD").strip()


def unexplained_commits(ctx: Ctx, comp: dict, it: dict) -> str | None:
    """None when every commit between where HEAD was when the run started
    (the record's `head`) and HEAD now is the run's own for this component
    (the pin commit, its rollback or revert, a recovery's pin-back) or a
    quarterly report; else what a person (or anything else) added."""
    head0 = it.get("head")
    if not head0:
        return "the record does not say where HEAD was when the run started"
    try:
        ctx.ops.git("merge-base", "--is-ancestor", head0, "HEAD")
    except StepFailed:
        return f"HEAD does not descend from {head0[:10]}, where the run started"
    own = re.compile(rf"^upgrade: (roll back |not applied, )?{re.escape(comp['name'])} |^upgrade report: ")
    bad = []
    for ln in ctx.ops.git("log", "--format=%h%x09%ae%x09%s", f"{head0}..HEAD").splitlines():
        sha, email, subject = (ln.split("\t", 2) + ["", ""])[:3]
        if ln.strip() and not (email == COMMIT_EMAIL and own.match(subject)):
            bad.append(f"{sha} {subject}")
    if bad:
        return (f"HEAD has {len(bad)} commit(s) since the run started that the record does not explain "
                f"({'; '.join(bad[:4])})")
    return None


def intent_facts(ctx: Ctx, comp: dict, it: dict) -> dict:
    """Read-only: where git, the tags and the running version stand."""
    ops = ctx.ops
    dirty = [ln[3:] for ln in ops.git("status", "--porcelain", "--untracked-files=normal").splitlines() if ln.strip()]
    kind, ver, runs = container_state(ctx, comp)
    f = {"git": head_pin(ctx, comp), "kind": kind, "ver": ver, "runs": runs, "dirty": dirty, "prod_tag": None,
         "unexplained": unexplained_commits(ctx, comp, it)}
    exists = lambda t: bool(t) and bool(ops.git("rev-parse", "-q", "--verify", f"refs/tags/{t}", check=False).strip())  # noqa: E731
    f["tag"] = it.get("tag") if exists(it.get("tag")) else None
    if comp.get("deploy") == "kronk":
        f["prod_tag"], _ = production_tag(ops, ctx.rules)
        f["prev_exists"] = exists(it.get("prev_tag"))
    desc = f"git has {f['git']}, running is {runs}"
    if f["prod_tag"]:
        desc += f", production runs tag {f['prod_tag']}"
    if f["tag"]:
        desc += f", tag {f['tag']} exists"
    if dirty:
        desc += f", the working tree has uncommitted changes ({', '.join(dirty[:5])})"
    f["desc"] = desc
    return f


def recover(ops: Ops, rules: Rules, sd: Path, status: dict, now: dt.datetime, log: RunLog, log_rel: str,
            stamp: str, own: Own, dry_run: bool, late: str | None = None) -> int | None:
    """An earlier run died inside a component (its intent record is still
    in the status file). Compare git, the tags and what runs with the
    record; act only when they are what the record says, else stop and say
    what is out of step. None: nothing to recover, go on; else the run's
    exit code."""
    it = status.get("intent")
    if not it:
        return None
    name, frm, to = it.get("component"), it.get("from"), it.get("to")
    log(f"an earlier run ({it.get('run')}) stopped inside {name} {frm} -> {to} at step {it.get('step')!r} "
        f"({it.get('at')}): checking git, the tags and what runs")
    ctx = Ctx(ops, rules, log, sd, stamp, now, it.get("prev_tag"), own=own, status=None if dry_run else status)
    ctx.ha_old_id = it.get("ha_old_id")
    ctx.outside_window = late
    try:
        comp = rules.component(name)
    except KeyError:
        o = Outcome(str(name), str(frm), str(to), "out_of_step", "the interrupted component is no longer in the rules",
                    state="OUT OF STEP: unknown component in the interrupted run's record")
        return _recovered(ctx, status, o, log_rel, dry_run)
    try:
        # Home Assistant down comes first, before the tree, the window and
        # the month, and by the smallest action (sprint M18 fix 2, G2).
        o = _ha_down_first(ctx, comp, it, dry_run) if comp.get("deploy") == "ha" else None
        if o == "dry":
            return None
        if o is None:
            f = intent_facts(ctx, comp, it)
            log(f"  {f['desc']}")
            o = _recover_one(ctx, comp, it, f, dry_run)
    except (StepFailed, RulesError, OSError) as e:
        o = Outcome(name, frm, to, "out_of_step", f"cannot read git or what runs: {e}",
                    state="OUT OF STEP: an interrupted run; git and what runs could not be read")
    if o is None:                      # dry run: said what it would do
        return None
    return _recovered(ctx, status, o, log_rel, dry_run)


def start_stopped_ha(ctx: Ctx, comp: dict) -> str | None:
    """`docker start` the stopped container itself (no compose, no
    recreate, no pin or git change) and wait for HA to answer. None when it
    runs and answers; else why not."""
    c = comp["container"]
    r = ctx.ops.docker("start", c, timeout=300)
    ctx.log(f"  docker start {c}: exit {r.rc}")
    if not r.ok:
        return f"docker start {c} failed: {r.tail(200)}"
    problems = health(ctx, comp)
    return f"{c} started, but HA does not answer: {'; '.join(problems)}" if problems else None


def _ha_down_first(ctx: Ctx, comp: dict, it: dict, dry_run: bool):
    """HA stopped: start the same container. HA's container missing (a
    compose up cut short): create it on the rollback image, without a stop,
    after M12 H7b. Returns None to go on with the recovery (ctx.ha_back
    says what was done), "dry" in a dry run that would act, or an Outcome
    when HA could not be brought back."""
    name, frm, to, log, c = comp["name"], it["from"], it["to"], ctx.log, comp["container"]
    kind, _, _ = container_state(ctx, comp)
    if kind not in ("stopped", "absent"):
        return None

    def down(result: str, why: str) -> Outcome:
        _, runs = what_runs(ctx, comp)
        word = "ROLLBACK FAILED" if result == "rollback_failed" else "OUT OF STEP"
        return Outcome(name, frm, to, result, why, state=f"{word}; running is {runs}; git has {head_pin(ctx, comp)}")
    if kind == "stopped":
        if dry_run:
            log(f"  a real run would start the stopped {c} container (docker start, the same container), wait for HA, "
                f"then go on with the recovery")
            return "dry"
        err = start_stopped_ha(ctx, comp)
        if err:
            return down("rollback_failed", err)
        ctx.ha_back = f"Home Assistant was down ({c} was stopped); the same container was started again"
        log(f"  {ctx.ha_back}")
        return None
    if dry_run:
        log(f"  a real run would create {c} on the rollback image {comp['image']}:{frm} (compose up, no stop) after "
            f"M12 H7b, then go on with the recovery")
        return "dry"
    # compose up reads the working tree's file: it must say `frm`, or the
    # pin is put back by a commit first when the tree allows it.
    try:
        disk = read_pin(ctx.ops.repo, comp).value
    except (RulesError, OSError) as e:
        return down("out_of_step", f"cannot read the pin in {comp['file']}: {e}")
    if disk == to:
        dirty = tree_state(ctx.ops, ctx.rules)
        unexpl = unexplained_commits(ctx, comp, it)
        if head_pin(ctx, comp) != to or unexpl or any(w.startswith("working tree not clean") for w in dirty):
            return down("out_of_step", f"{comp['file']} says {to} and the pin cannot be put back by a commit "
                                       f"({unexpl or '; '.join(dirty) or 'HEAD is not the pin commit'}); {_OOS_TODO}")
        _pin_back(ctx, comp, frm, to, f"Recovering an interrupted run ({it.get('run')}, step {it.get('step')}): "
                                      f"no {c} container.")
    elif disk != frm:
        return down("out_of_step", f"{comp['file']} says {disk}, neither {frm} nor {to}; {_OOS_TODO}")
    files = ha_files_ok(ctx, comp)
    if files:
        return down("rollback_failed", "not created: M12 H7b: " + "; ".join(files))
    if not ctx.ha_old_id:
        i = ctx.ops.docker("image", "inspect", f"{HA_ROLLBACK_REPO}:{frm}", "-f", "{{.Id}}", timeout=30)
        ctx.ha_old_id = i.out.strip() if i.ok else None
    err = ha_rollback(ctx, comp, frm)
    problems = [err] if err else health(ctx, comp)
    if problems:
        return down("rollback_failed", "; ".join(problems))
    ctx.ha_back = f"Home Assistant was down (no {c} container); it was created again on {frm}, the rollback image"
    log(f"  {ctx.ha_back}")
    return None


_OOS_TODO = ("nothing was touched; put what should run back by hand, make git's pin match it, "
             "then `python3 scripts/upgrades.py ack`")


def _recover_one(ctx: Ctx, comp: dict, it: dict, f: dict, dry_run: bool) -> Outcome | None:
    """The recovery table (sprint M18 fix 2, G1). The record is acted on
    only when the world is exactly what it says; anything else is OUT OF
    STEP with nothing touched. No recovery deploys over something a person
    deployed."""
    name, frm, to = comp["name"], it["from"], it["to"]
    oos = lambda why: Outcome(name, frm, to, "out_of_step", f"{why}; {_OOS_TODO}",  # noqa: E731
                              state=f"OUT OF STEP after an interrupted run (step {it.get('step')}): {f['desc']}")
    if f["dirty"]:
        return oos("the working tree is not clean; the run does not touch uncommitted changes")
    if f["unexplained"]:
        return oos(f["unexplained"])
    if f["git"] not in (frm, to):
        return oos(f"git has {f['git']}, neither {frm} nor {to}")
    c = comp.get("container")
    if comp["deploy"] == "kronk":
        prod, prev, own = f["prod_tag"], it.get("prev_tag"), it.get("tag")
        if own and prod == own:
            # The run's own deploy reached production: deploy the recorded previous tag.
            if not f.get("prev_exists"):
                return oos(f"production runs the run's own tag {own}, and the recorded previous tag {prev} does not exist")
            return _finish_rollback(ctx, comp, it, f, dry_run)
        if prod and prod == prev:
            if f["ver"] != frm:
                return oos(f"production runs the recorded previous tag {prev}, but {c} runs {f['runs']}, not {frm}")
            return _pin_back_only(ctx, comp, it, f, dry_run)
        return oos(f"production runs tag {prod or 'unknown'}; the record has the previous tag {prev} and the run's tag "
                   f"{own or '(not made yet)'}: someone else deployed in between")
    # Telemetry and Home Assistant (a stopped or missing HA was dealt with first).
    if f["kind"] != "runs":
        return oos(f"{c} is not running ({f['runs']}); a recovery starts nothing the record does not account for")
    if f["ver"] == frm:
        return _pin_back_only(ctx, comp, it, f, dry_run)
    if f["ver"] != to:
        return oos(f"{c} runs {f['runs']}, neither {frm} nor {to}")
    why = _recovery_guards(ctx, comp, it, f)
    if why:
        return oos(why)
    return _finish_rollback(ctx, comp, it, f, dry_run)


def _recovery_guards(ctx: Ctx, comp: dict, it: dict, f: dict) -> str | None:
    """The guards an upgrade passes before its `up`, for the recovery's:
    the container is the one the run created, the file at HEAD (and its
    env file) still gives the config hash the record has for that commit,
    no bind-mounted file changed after the start, and for HA M12's H7b."""
    ops, c = ctx.ops, comp["container"]
    have, err = label_hash(ops, comp)
    if err:
        return err
    if not it.get("hash_to") or have != it["hash_to"]:
        return (f"{c} is not the container the run started (its config hash {have} is not the run's "
                f"{it.get('hash_to')}): it was recreated since")
    want = it.get("hash_to") if f["git"] == it["to"] else it.get("hash_from")
    cur, err = config_hash(ops, comp)
    if err:
        return err
    if not want or cur != want:
        return (f"{comp['file']} at HEAD (or its env file) no longer gives the config hash the record has "
                f"({want}, now {cur}): a recreate would bring up something the run never checked")
    why = bind_files_unreleased(ops, comp)
    if why:
        return why
    if comp["deploy"] == "ha":
        files = ha_files_ok(ctx, comp)
        if files:
            return "M12 H7b: " + "; ".join(files)
    return None


def _pin_back_only(ctx: Ctx, comp: dict, it: dict, f: dict, dry_run: bool) -> Outcome | None:
    """The previous version runs: put git back if it is ahead; restart nothing."""
    name, frm, to, log = comp["name"], it["from"], it["to"], ctx.log
    if f["git"] == frm:
        if dry_run:
            log(f"  a real run would clear the record: nothing is out of step ({frm} in git and running)")
            return None
        return Outcome(name, frm, to, "recovered", f"interrupted at step {it.get('step')}",
                       state=f"recovered: nothing was out of step ({frm} in git and running)")
    if dry_run:
        log(f"  a real run would put the pin back to {frm} (a commit); nothing is restarted")
        return None
    try:
        sha = _pin_back(ctx, comp, frm, to, f"Recovering an interrupted run ({it.get('run')}, step {it.get('step')}): "
                                            f"git had {to} while {frm} ran.")
    except (StepFailed, RulesError, OSError) as e:
        return Outcome(name, frm, to, "out_of_step", f"putting the pin back failed: {e}",
                       state=f"OUT OF STEP after an interrupted run: {f['desc']}")
    return Outcome(name, frm, to, "recovered", f"interrupted at step {it.get('step')}", sha,
                   state=f"recovered: git had {to} while {frm} ran; pin put back to {frm} (commit {sha}), nothing restarted")


def _finish_rollback(ctx: Ctx, comp: dict, it: dict, f: dict, dry_run: bool) -> Outcome | None:
    """The run's new version runs unverified: finish the rollback to the
    recorded previous version, inside the night window with the house quiet."""
    name, frm, to, log = comp["name"], it["from"], it["to"], ctx.log
    busy = house_busy(ctx.ops, ctx.rules, ctx.ops.now(), ctx.own)
    if busy or ctx.outside_window:
        why = "; ".join(busy) if busy else ctx.outside_window
        if dry_run:
            log(f"  a real run would finish the rollback to {frm} when the house is quiet (now: {why})")
            return None
        return Outcome(name, frm, to, "deferred", why,
                       state=f"{to} runs unverified after an interrupted run; the rollback to {frm} waits for "
                             f"a quiet house in the night window")
    if dry_run:
        log(f"  a real run would finish the rollback to {frm} now")
        return None
    try:
        if f["git"] == to:
            _pin_back(ctx, comp, frm, to, f"Recovering an interrupted run ({it.get('run')}, step {it.get('step')}).")
        if comp["deploy"] == "kronk":
            ctx.redeploys += 1
            r = ctx.ops.deploy_tag("deploy", it["prev_tag"])
            err = None if r.ok else f"deploy of the previous tag {it['prev_tag']} failed (exit {r.rc}): {r.tail(200)}"
        elif comp["deploy"] == "ha":
            if not ctx.ha_old_id:
                i = ctx.ops.docker("image", "inspect", f"{HA_ROLLBACK_REPO}:{frm}", "-f", "{{.Id}}", timeout=30)
                ctx.ha_old_id = i.out.strip() if i.ok else None
            err = ha_rollback(ctx, comp, frm)
        else:
            r = ctx.ops.compose(comp["project"], comp["file"], "up", "-d", "--no-deps", comp["service"],
                                env_file=comp.get("env_file"))
            err = None if r.ok else f"compose up of {frm} failed: {r.tail(200)}"
        after = [err] if err else verify(ctx, comp, frm, full=False)
    except (StepFailed, RulesError, OSError) as e:
        after = [f"recovery step raised: {e}"]
    if after:
        _, runs = what_runs(ctx, comp)
        return Outcome(name, frm, to, "rollback_failed", "; ".join(after), state=f"ROLLBACK FAILED; running is {runs}; "
                       f"git has {head_pin(ctx, comp)}")
    return Outcome(name, frm, to, "recovered", f"interrupted at step {it.get('step')}" + _players_after(ctx),
                   state=f"recovered: finished the rollback: {frm} runs again, git has {frm}")


def _recovered(ctx: Ctx, status: dict, o: Outcome, log_rel: str, dry_run: bool) -> int | None:
    log, now = ctx.log, ctx.now
    if dry_run:
        log(f"  a real run would stop here: {o.state}")
        return None
    if ctx.ha_back:
        o.state = f"{ctx.ha_back}; {o.state}"
    log(f"  {o.result}: {o.state}" + (f" ({_cut(o.detail, 400)})" if o.detail else ""))
    month = now.strftime("%Y-%m")
    m = status.setdefault("months", {}).setdefault(month, {"attempts": 0, "done": False})
    if o.result == "deferred":
        add_attention(status, "interrupted", f"Upgrade interrupted inside {o.name}: {o.state} ({o.detail})", now)
        if not status["intent"].get("noticed"):
            status["intent"]["noticed"] = now.isoformat(timespec="seconds")
            deliver(ctx.ops, status, {"id": f"interrupted-{o.name}-{ctx.stamp}", "tag": NOTIFY_TAG,
                                      "title": f"Kronk upgrade: interrupted ({o.name}); rollback waiting",
                                      "body": f"{o.name} {o.frm} -> {o.to}: {o.state} (now: {_cut(o.detail, 300)}). "
                                              f"Log: {log_rel}", "created": now.isoformat(timespec="seconds")}, now, log)
        status["last_run"] = {"kind": "minor", "at": now.isoformat(timespec="seconds"), "result": "deferred",
                              "summary": o.state, "log": log_rel}
        save_status(ctx.sd, status, now)
        return 0
    status.pop("intent", None)
    if o.result in BLOCKING:
        block(status, o, now)
    else:
        m.setdefault("held_back", {})[o.name] = f"interrupted run recovered on {now.date()}; not retried this month"
    title, body = notice_text([o], [], log_rel, ctx.redeploys)
    if o.result == "recovered":
        title = f"Kronk upgrade: an interrupted run was recovered ({o.name})" + (
            ": Home Assistant was down" if "Home Assistant was down" in o.state else "")
    status["last_run"] = {"kind": "minor", "at": now.isoformat(timespec="seconds"), "result": o.result,
                          "summary": title, "log": log_rel, "outcomes": [o.__dict__]}
    add_attention(status, f"interrupted:{o.name}", f"{title}. {o.state}. Details: {log_rel}", now)
    deliver(ctx.ops, status, {"id": f"recover-{ctx.stamp}", "tag": NOTIFY_TAG, "title": title, "body": body,
                              "created": now.isoformat(timespec="seconds")}, now, log)
    save_status(ctx.sd, status, now)
    log(f"done: {o.result} (this run stops here; the next one plans as usual)")
    return {"recovered": 1}.get(o.result, 2)


def mismatch_notice(ops: Ops, status: dict, m: dict, month: str, plans: list[Plan], now: dt.datetime,
                    log: RunLog, log_rel: str) -> None:
    """Pins that are not what runs: one notice and a warning per month and
    set of mismatches."""
    mm = [p for p in plans if p.action == "mismatch"]
    if not mm:
        return
    key = sorted(f"{p.comp['name']}:{p.pin}" for p in mm)
    add_attention(status, f"mismatch:{month}", "Pin and running version differ: "
                  + "; ".join(f"{p.comp['name']}: {p.detail}" for p in mm), now)
    if m.get("mismatch_noticed") == key:
        return
    m["mismatch_noticed"] = key
    deliver(ops, status, {"id": f"mismatch-{now.strftime('%Y%m%dT%H%M%SZ')}", "tag": NOTIFY_TAG,
                          "title": f"Kronk upgrade: pin and running version differ ({', '.join(p.comp['name'] for p in mm)})",
                          "body": "\n".join(f"{p.comp['name']}: {p.detail}" for p in mm) + f"\nLog: {log_rel}",
                          "created": now.isoformat(timespec="seconds")}, now, log)


# ═══ The monthly run ═════════════════════════════════════════════════════

DEFAULT_STATE = {"kept": "upgraded", "not_started": "not started", "not_applied": "not applied",
                 "rollback_failed": "ROLLBACK FAILED", "out_of_step": "OUT OF STEP", "recovered": "recovered"}
BLOCKING = ("rollback_failed", "out_of_step")       # stop the run; later runs decline until `ack`


def _cut(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def notice_text(outcomes: list[Outcome], plans: list[Plan], log_rel: str, redeploys: int = 0) -> tuple[str, str]:
    """Title and body. Each line puts the state first (what was done, what
    git has, what runs) and the detail after, so a cut never takes the fact."""
    by = {k: [o for o in outcomes if o.result == k] for k in
          ("kept", "rolled_back", "rollback_failed", "not_started", "not_applied", "out_of_step", "recovered")}
    down = any("Home Assistant is down" in o.state for o in outcomes)
    if by["rollback_failed"]:
        title = f"Kronk upgrade: ROLLBACK FAILED ({by['rollback_failed'][0].name})"
    elif by["out_of_step"]:
        title = f"Kronk upgrade: OUT OF STEP ({by['out_of_step'][0].name})"
    else:
        parts = [f"{len(by[k])} {w}" for k, w in (("kept", "upgraded"), ("rolled_back", "rolled back"),
                                                   ("not_applied", "not applied"), ("recovered", "recovered")) if by[k]]
        title = "Kronk upgrade: " + (", ".join(parts) if parts else "nothing applied")
    if down:
        title += ": Home Assistant is down"
    lines = []
    for k in ("rollback_failed", "out_of_step", "recovered", "kept", "rolled_back", "not_applied", "not_started"):
        for o in by[k]:
            state = o.state or ({"rolled_back": f"rolled back to {o.frm}"}.get(k) or DEFAULT_STATE[k])
            line = f"{o.name} {o.frm} -> {o.to}: {state}"
            if k in BLOCKING:
                line += "; needs you: later runs wait until `python3 scripts/upgrades.py ack`"
            if o.detail:
                line += f" ({_cut(o.detail, 300)})"
            lines.append(line)
    if redeploys:
        lines.append(f"Production (the Kronk stack) was redeployed {redeploys} time(s) by this run: "
                     f"every service with a bind mount was recreated each time.")
    skipped = [p for p in plans if p.action == "skip"]
    if skipped:
        lines.append("Skipped: " + "; ".join(f"{p.comp['name']} ({_cut(p.detail, 80)})" for p in skipped))
    lines.append(f"Log: {log_rel}")
    return title, "\n".join(lines)


def minor_run(ops: Ops, rules: Rules, sd: Path, dry_run: bool = False, ignore_month: bool = False,
              ignore_window: bool = False) -> int:
    now = ops.now()
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    month = now.strftime("%Y-%m")
    log_path = None if dry_run else sd / "runs" / f"{stamp}-minor.log"
    log = RunLog(log_path)
    log_rel = shown_path(sd / "runs" / f"{stamp}-minor.log", ops.repo)
    log(f"upgrade_minor {'DRY RUN ' if dry_run else ''}{now.isoformat(timespec='seconds')} "
        f"repo {ops.repo} branch rule {rules.settings['expected_branch']!r}")

    own = Own(ua=f"kronk-upgrade/{stamp}-{secrets.token_hex(6)}")
    ops.client_ua = own.ua
    lock_fd = None
    if dry_run:
        held = lock_held(sd)
        log(f"lock {sd / 'lock'}: {'HELD by ' + lock_holder(sd) if held else 'free'} (dry run takes no lock)")
    else:
        sd.mkdir(parents=True, exist_ok=True)
        ops.upgrade_token = secrets.token_hex(16)
        lock_fd = take_lock(sd, f"pid={os.getpid()}\nrun=upgrade_minor {stamp}\ntoken={ops.upgrade_token}\n")
        if lock_fd is None:
            log(f"declined: another upgrade run holds the lock ({lock_holder(sd)})")
            return 0
    try:
        try:
            status = load_status(sd)
        except StatusCorrupt as e:
            return status_corrupt(ops, e, log, notify=not dry_run)
        late = None if ignore_window else outside_window(ops, rules)
        if not dry_run and status.get("unreported"):
            report_unreported(ops, sd, status, now, log)
        if status.get("intent"):
            # Before anything else, the month's bookkeeping included: a run
            # that died inside a component is dealt with first (F3). A
            # component that is down is brought back at any hour; anything
            # that disrupts a running service waits for the night window.
            rc = recover(ops, rules, sd, status, now, log, log_rel, stamp, own, dry_run, late)
            if rc is not None:
                return rc
        if late and not dry_run:
            # A boot catch-up (Persistent=true) at 19:00 does nothing: no
            # attempt counted, nothing planned (F8).
            log(f"declined: {late}")
            status["last_run"] = {"kind": "minor", "at": now.isoformat(timespec="seconds"), "result": "outside_window",
                                  "summary": f"declined: {late}", "log": log_rel}
            save_status(sd, status, now)
            return 0
        months = status.setdefault("months", {})
        m = months.setdefault(month, {"attempts": 0, "done": False})
        if not dry_run and not ignore_month:
            if m.get("done"):
                log(f"this month's run ({month}) is done; nothing to do (--ignore-month to run anyway)")
                return 0
            if m["attempts"] >= rules.settings["max_attempts"]:
                log(f"{m['attempts']} attempts this month already; next try next month")
                return 0
        if not dry_run:
            m["attempts"] += 1
            retry_pending(ops, status, now, log)
            save_status(sd, status, now)
        blocked = [w for w in run_declines_blocked(status)]
        if blocked and not dry_run:
            # Checked before planning: a failed rollback or an out-of-step
            # component can leave a pin that would otherwise read as CURRENT.
            log("declined: " + "; ".join(blocked))
            status["last_run"] = {"kind": "minor", "at": now.isoformat(timespec="seconds"), "result": "declined",
                                  "summary": f"declined (attempt {m['attempts']} of {rules.settings['max_attempts']}): "
                                             + "; ".join(blocked)[:400], "log": log_rel}
            save_status(sd, status, now)
            return 0

        plans = [safe_plan(rules, c, ops.repo, ops.web, now) for c in rules.components]
        for p in plans:
            mm = pin_vs_running(ops, p)
            if mm:
                p.action, p.detail = mm
            elif p.comp["name"] in (m.get("held_back") or {}) and p.action == "upgrade":
                p.action, p.detail = "skip", f"held back this month: {m['held_back'][p.comp['name']]}"
            elif not ignore_month and p.comp["name"] in (m.get("components") or {}) and p.action == "upgrade":
                done = m["components"][p.comp["name"]]
                p.action, p.detail = "skip", f"done this month: {done.get('result')} ({done.get('to')})"
        log("plan:")
        for p in plans:
            log(f"  {p.comp['name']:<26} {str(p.pin or '-'):<22} {p.action.upper():<9} {p.detail}")
            for n in p.notes[:6]:
                log(f"  {'':<26} {'':<22} {'':<9} . {n}")
            for r in p.reports[:40]:
                log(f"  {'':<26} {'':<22} {'':<9} > {r}")
        todo = [p for p in plans if p.action == "upgrade"]
        log(f"lookups: {ops.web.requests} request(s) to {', '.join(sorted(ops.web.hosts)) or 'none'}")
        if not dry_run:
            mismatch_notice(ops, status, m, month, plans, now, log, log_rel)
            save_status(sd, status, now)
        if not todo:
            log("nothing to upgrade this month; no notice")
            if not dry_run:
                m["done"] = True
                status["last_run"] = {"kind": "minor", "at": now.isoformat(timespec="seconds"),
                                      "result": "nothing", "summary": "nothing to upgrade", "log": log_rel}
                save_status(sd, status, now)
            return 0

        info: list[str] = []
        declines = ([late] if late else []) + run_declines(ops, rules, status, now, own, info)
        for n in info:
            log(n)
        prod_tag, kronk_why = (None, [])
        if any(p.comp["deploy"] == "kronk" for p in todo):
            prod_tag, kronk_why = production_tag(ops, rules)
        if dry_run:
            log("would do, in order (each its own commit; verify = image, health, corpus, replay, stack checks):")
            for p in todo:
                log(f"  {p.comp['name']}: {_would(p, prod_tag)}")
            log("then: one notice through scripts/lib/notify.sh with what changed; "
                "the status file data/upgrades/status.json for /services")
            if any(p.comp["deploy"] == "kronk" for p in todo):
                log(f"production runs tag: {prod_tag or 'unknown'}" + (f" ({'; '.join(kronk_why)})" if kronk_why else ""))
            if prod_tag:
                left = undeployed(ops, rules, prod_tag)
                log(f"HEAD vs {prod_tag}: " + (f"undeployed changes in {', '.join(left[:8])} (Kronk-stack components would be declined)"
                                              if left else "differs only in paths that do not reach the Kronk containers"))
            for p in todo:
                if p.comp["deploy"] in ("ha", "compose"):
                    log(f"{p.comp['file']} vs running {p.comp['container']}: {compose_hash_ok(ops, p.comp) or 'matches (config hash)'}")
                    log(f"bind-mounted files of {p.comp['container']}: "
                        f"{bind_files_unreleased(ops, p.comp) or 'none changed since it started'}")
            if declines:
                log("a real run now would DECLINE: " + "; ".join(declines))
            else:
                log("a real run now would go ahead (house quiet, tree clean, on the expected branch)")
            log("dry run: nothing written, nothing changed, nothing sent")
            return 0

        if declines:
            log("declined: " + "; ".join(declines))
            status["last_run"] = {"kind": "minor", "at": now.isoformat(timespec="seconds"), "result": "declined",
                                  "summary": f"declined (attempt {m['attempts']} of {rules.settings['max_attempts']}): "
                                             + "; ".join(declines)[:400], "log": log_rel}
            if m["attempts"] >= rules.settings["max_attempts"]:
                m["done"] = True
                deliver(ops, status, {"id": f"minor-{month}-declined", "tag": NOTIFY_TAG,
                                      "title": "Kronk upgrade: this month's patch run never found a quiet moment",
                                      "body": f"Declined {m['attempts']} times. Last reasons: {'; '.join(declines)[:500]}. "
                                              f"Pending: {', '.join(p.comp['name'] + ' ' + p.to.version for p in todo)}. "
                                              f"Next try next month, or run scripts/upgrade_minor.sh --ignore-month. Log: {log_rel}",
                                      "created": now.isoformat(timespec="seconds")}, now, log)
                add_attention(status, f"declined:{month}", f"Monthly upgrade declined {m['attempts']} times in {month}; "
                              f"see {log_rel}", now)
            save_status(sd, status, now)
            return 0

        ctx = Ctx(ops, rules, log, sd, stamp, now, prod_tag, own=own, status=status, ignore_window=ignore_window)
        log("baseline: offline corpus")
        t = ops.run_tests()
        if not t.ok:
            return _baseline_declined(ctx, status, m, month, log_rel, f"offline corpus fails before any change: {t.tail(200)}")
        log("baseline: live dry-run replay on the dev path")
        rows, err = run_replay(ctx, "baseline")
        if rows is None:
            return _baseline_declined(ctx, status, m, month, log_rel, f"live replay cannot run: {err}")
        ctx.baseline = rows

        outcomes: list[Outcome] = []
        for p in todo:
            if p.comp["deploy"] == "kronk" and kronk_why:
                outcomes.append(Outcome(p.comp["name"], p.pin, p.to.version, "not_started", "; ".join(kronk_why)))
                log(f"── {p.comp['name']}: not started: {'; '.join(kronk_why)}")
                continue
            try:
                o = apply_one(ctx, p)
            except (StepFailed, RulesError, OSError) as e:
                log(f"  ERROR: {e}")
                o = error_outcome(ctx, p, e)
            outcomes.append(o)
            if o.result != "not_started":
                # This month has an outcome for it; one not started (house
                # busy, the window closed, a precondition) goes again the
                # next night while attempts remain (K6).
                m.setdefault("components", {})[o.name] = {"result": o.result, "to": o.to}
                # Written when the component ends: a run killed before its
                # notice leaves them for the next run to report (K3).
                status["unreported"] = {"run": stamp, "log": log_rel, "redeploys": ctx.redeploys,
                                        "outcomes": [x.__dict__ for x in outcomes if x.result != "not_started"]}
                save_status(sd, status, now)
            if o.result == "not_started":
                log(f"  not started: {o.detail}")
            if o.result in BLOCKING:
                block(status, o, now)
                log(f"stopping: {o.name} {'rollback failed' if o.result == 'rollback_failed' else 'is out of step'}; "
                    f"the remaining components wait")
                break

        changed = [o for o in outcomes if o.result != "not_started"]
        result = next((r for r in ("rollback_failed", "out_of_step", "rolled_back", "not_applied")
                       if any(o.result == r for o in outcomes)), "upgraded" if changed else "not_started")
        title, body = notice_text(outcomes, plans, log_rel, ctx.redeploys)
        pending = [p.comp["name"] for p in todo if p.comp["name"] not in (m.get("components") or {})]
        last_try = m["attempts"] >= rules.settings["max_attempts"]
        m["done"] = not pending or last_try
        status["last_run"] = {"kind": "minor", "at": now.isoformat(timespec="seconds"), "result": result,
                              "summary": title, "log": log_rel,
                              "outcomes": [o.__dict__ for o in outcomes]}
        if any(o.result in ("rolled_back", "not_applied") for o in outcomes):
            add_attention(status, f"rolled_back:{month}", f"{title}. Details: {log_rel}", now)
        if changed:
            deliver(ops, status, {"id": f"minor-{stamp}", "tag": NOTIFY_TAG, "title": title, "body": body,
                                  "created": now.isoformat(timespec="seconds")}, now, log)
            status.pop("unreported", None)          # sent, or pending (retried hourly)
        elif last_try and m.get("components"):
            # Earlier nights applied something; these never started (K6).
            deliver(ops, status, {"id": f"minor-{month}-ended", "tag": NOTIFY_TAG,
                                  "title": f"Kronk upgrade: this month's patch run ended with {len(pending)} not started "
                                           f"(attempts ran out)", "body": body,
                                  "created": now.isoformat(timespec="seconds")}, now, log)
            add_attention(status, f"declined:{month}", f"Monthly upgrade: {', '.join(pending)} never started in {month}; "
                          f"see {log_rel}", now)
        elif last_try:
            # The month ends with nothing applied: say so, as a month of declines does (F7).
            month_ended_empty(ops, status, m, month, rules, now, log, log_rel,
                              "applied nothing: every pending component was not started",
                              [f"{o.name} {o.to}: {o.detail}" for o in outcomes])
        else:
            log("nothing was changed or tried; no notice")
        save_status(sd, status, now)
        log(f"done: {result}")
        return {"rollback_failed": 2, "out_of_step": 2, "rolled_back": 1, "not_applied": 1}.get(result, 0)
    finally:
        if lock_fd is not None:
            os.close(lock_fd)


def report_unreported(ops: Ops, sd: Path, status: dict, now: dt.datetime, log: RunLog) -> None:
    """An earlier run ended (killed) after finishing components and before
    its notice: send what it did now, once (K3)."""
    u = status.pop("unreported")
    outs = [Outcome(**o) for o in u.get("outcomes", [])]
    if outs:
        title, body = notice_text(outs, [], u.get("log", "?"), u.get("redeploys", 0))
        title += f" (run {u.get('run')}, which ended before its notice)"
        log(f"an earlier run ({u.get('run')}) ended before its notice: reporting {len(outs)} outcome(s) now")
        deliver(ops, status, {"id": f"unreported-{u.get('run')}", "tag": NOTIFY_TAG, "title": title, "body": body,
                              "created": now.isoformat(timespec="seconds")}, now, log)
    save_status(sd, status, now)


def _baseline_declined(ctx: Ctx, status: dict, m: dict, month: str, log_rel: str, why: str) -> int:
    ctx.log(f"declined: {why}")
    status["last_run"] = {"kind": "minor", "at": ctx.now.isoformat(timespec="seconds"), "result": "declined",
                          "summary": why[:400], "log": log_rel}
    if m["attempts"] >= ctx.rules.settings["max_attempts"]:
        month_ended_empty(ctx.ops, status, m, month, ctx.rules, ctx.now, ctx.log, log_rel,
                          "applied nothing: the baseline (offline corpus, live replay) could not be taken", [why])
    save_status(ctx.sd, status, ctx.now)
    return 0


def month_ended_empty(ops: Ops, status: dict, m: dict, month: str, rules: Rules, now: dt.datetime, log: RunLog,
                      log_rel: str, what: str, reasons: list[str]) -> None:
    """The last attempt of the month and nothing was applied: one notice and
    the /services warning, naming the reasons."""
    m["done"] = True
    text = "; ".join(_cut(r, 240) for r in reasons)
    deliver(ops, status, {"id": f"minor-{month}-empty", "tag": NOTIFY_TAG,
                          "title": f"Kronk upgrade: this month's patch run {what.split(':')[0]}",
                          "body": f"{m['attempts']} attempts in {month}; {what}. Reasons: {_cut(text, 700)}. "
                                  f"Next try next month, or run scripts/upgrade_minor.sh --ignore-month. Log: {log_rel}",
                          "created": now.isoformat(timespec="seconds")}, now, log)
    add_attention(status, f"declined:{month}", f"Monthly upgrade {what} in {month} ({m['attempts']} attempts): "
                  f"{_cut(text, 300)}; see {log_rel}", now)


def _would(p: Plan, prod_tag: str | None) -> str:
    c, frm, to = p.comp, p.pin, p.to.version
    head = f"{c['file']}: {frm} -> {to}; commit 'upgrade: {c['name']} {frm} -> {to}'; "
    if c["deploy"] == "kronk":
        return head + (f"docker pull {c['image']}:{to} (digest checked); " if c.get("image") else "") + f"git tag upgrade-<run time>-{c['name']}; deploy_tag.sh deploy it (a full production redeploy; rollback: pin back, deploy {prod_tag or '<previous tag>'}, another)"
    if c["deploy"] == "ha":
        return head + (f"docker tag <running id> {HA_ROLLBACK_REPO}:{frm}; docker pull {c['image']}:{to}; "
                       f"docker stop -t 120 {c['container']}; compose -p {c['project']} up -d {c['service']}; "
                       f"M12 H8 checks (rollback: pin back, image by id, same restart)")
    return head + (f"compose -p {c['project']} pull {c['service']}; up -d --no-deps {c['service']} "
                   f"(rollback: pin back, up -d with the local previous image)")


def shown_path(p: Path, repo: Path) -> str:
    """A path as a reader should see it: repo-relative when inside the repo."""
    try:
        return str(p.resolve().relative_to(repo.resolve()))
    except ValueError:
        return str(p)


def lock_holder(sd: Path) -> str:
    """Who holds the lock, as the holder wrote it (never the token)."""
    try:
        return " ".join(ln for ln in (sd / "lock").read_text().splitlines() if ln and not ln.startswith("token="))
    except OSError:
        return "unknown"


def lock_held(sd: Path) -> bool:
    p = sd / "lock"
    if not p.exists():
        return False
    fd = os.open(p, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def take_lock(sd: Path, holder: str = "") -> int | None:
    """The upgrade lock (flock on data/upgrades/lock), or None when held.
    The holder's description is written into the file for deploy_tag.sh
    and for a person reading it."""
    fd = os.open(sd / "lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    os.ftruncate(fd, 0)
    os.write(fd, (holder or f"pid={os.getpid()}\n").encode())
    return fd


def wait_lock(ops: Ops, sd: Path, holder: str, minutes: int, log) -> int | None:
    """The lock, waiting up to `minutes` for the holder to finish."""
    waited = 0
    while True:
        fd = take_lock(sd, holder)
        if fd is not None or waited >= minutes:
            return fd
        if waited == 0:
            log(f"waiting for the upgrade lock (held by: {lock_holder(sd)}), up to {minutes} minutes")
        ops.sleep(60)
        waited += 1


# ═══ Inventory ═══════════════════════════════════════════════════════════

def running_version(ops: Ops, comp: dict) -> str:
    """What actually runs, as a short string (read-only)."""
    if comp["kind"] == "llama-build":
        out = []
        for u in comp.get("units", []):
            path = Path.home() / ".config" / "systemd" / "user" / f"{u}.service"
            act = ops.run(["systemctl", "--user", "is-active", f"{u}.service"], timeout=10).out.strip() or "?"
            try:
                m = re.search(r"llama-cpp/([^/\s]+)/llama-server", path.read_text())
                out.append(f"{u}: {m.group(1) if m else '?'} ({act})")
            except OSError:
                out.append(f"{u}: unit not installed")
        return "; ".join(out)
    if comp["kind"] == "host-venv":
        sp = list(Path(os.path.expanduser(comp["venv"])).glob(f"lib/python3*/site-packages/{comp['package']}-*.dist-info"))
        return sp[0].name.split("-")[1].removesuffix(".dist") if sp else "not installed"
    if comp.get("running"):
        r = ops.run(comp["running"]["cmd"], timeout=30)
        m = re.search(comp["running"]["regex"], r.out + r.err)
        return m.group(1) if (r.ok and m) else f"unreadable ({r.tail(60)})"
    if comp.get("container") and comp["kind"] == "image":
        r = ops.docker("inspect", comp["container"], "-f", "{{.Config.Image}} {{.Image}}", timeout=30)
        if not r.ok:
            return "not running"
        img, iid = r.out.split()
        tag = ops.docker("image", "inspect", img, "-f", "{{.Id}}", timeout=30)
        same = "same id as the tag" if tag.ok and tag.out.strip() == iid else "NOT the tag's current image"
        return f"{img.rsplit(':', 1)[-1]} ({iid[7:19]}, {same})"
    return "-"


def inventory(ops: Ops, rules: Rules, now: dt.datetime) -> list[dict]:
    rows = []
    for c in rules.components:
        row = {"name": c["name"], "kind": c["kind"], "minor": c["minor"], "file": c.get("file", c.get("venv", "-")),
               "pin": "-", "running": "-", "newest_minor": "-", "newest_any": "-", "source": c.get("releases", "-"),
               "notes": []}
        try:
            if c["kind"] == "lockfile":
                pins = read_lock(ops.repo, c)
                row["pin"] = f"{len(pins)} packages"
                row["running"] = lock_vs_running(ops, c, pins)
                p = plan_lockfile(rules, c, ops.repo, ops.web, now)
                row["newest_minor"] = f"{len(p.reports)} patch release(s)"
                row["lock_patches"] = p.reports
                majors = []
                for pkg, ver in sorted(pins.items()):
                    if vkey(c, ver) is None:
                        continue
                    try:
                        r, _ = newest(c, ver, pypi_releases(ops.web, pkg), "major")
                    except LookupFailed:
                        continue
                    if r:
                        majors.append(f"{pkg} {ver} -> {r.version}")
                row["newest_any"] = f"{len(majors)} newer minor/major"
                row["lock_majors"] = majors
                row["notes"] += p.notes
            elif c["kind"] == "host-venv":
                row["running"] = running_version(ops, c)
                row["pin"] = "none in the repo"
                rels = pypi_releases(ops.web, c["releases"].split(":", 1)[1])
                if vkey(c, row["running"]):
                    r, _ = newest(c, row["running"], rels, "minor")
                    row["newest_minor"] = r.version if r else "none newer"
                row["newest_any"] = max((r.version for r in rels if vkey(c, r.version)), key=lambda v: vkey(c, v), default="-")
            elif c["kind"] == "llama-build":
                row["pin"] = "per unit (ExecStart build directory)"
                row["running"] = running_version(ops, c)
                rel = builds(ops.web, c)
                row["newest_any"] = max((r.version for r in rel if vkey(c, r.version)), key=lambda v: vkey(c, v), default="-")
                row["newest_minor"] = "n/a (nothing is minor)"
            elif c["kind"] == "blueprint":
                pin = read_pin(ops.repo, c)
                row["pin"] = pin.value
                row["running"] = "HA (installed copy, see ha/blueprints)"
                body, _ = ops.web.get(c["upstream_url"])
                m = re.search(r"^\s*version:\s*(\d{8})\s*$", body.decode("utf-8", "replace"), re.M)
                row["newest_any"] = m.group(1) if m else "unreadable"
                row["newest_minor"] = "n/a"
                row["source"] = "raw.githubusercontent.com (upstream file)"
            else:
                pin = read_pin(ops.repo, c)
                row["pin"] = pin.value
                row["file"] = f"{pin.file}:{pin.line}"
                row["running"] = running_version(ops, c)
                if not c.get("releases"):
                    row["newest_minor"] = row["newest_any"] = "no source"
                elif c["kind"] == "tool" and c["releases"].startswith("pypi:"):
                    rels = pypi_releases(ops.web, c["releases"].split(":", 1)[1])
                    row["newest_any"] = max((r.version for r in rels if re.fullmatch(r"\d+\.\d+\.\d+", r.version)),
                                            key=lambda v: tuple(map(int, v.split("."))), default="-")
                else:
                    rels = image_releases(ops.web, c, pin.value if vkey(c, pin.value) else _fake_pin(c, row["running"]))
                    ref = pin.value if vkey(c, pin.value) else _fake_pin(c, row["running"])
                    if ref and vkey(c, ref):
                        r1, _ = newest(c, ref, rels, "minor")
                        r2, _ = newest(c, ref, rels, "major")
                        row["newest_minor"] = r1.version if r1 else "none newer"
                        row["newest_any"] = (r2 or r1).version if (r2 or r1) else ref
                        if not vkey(c, pin.value):
                            row["notes"].append(f"pin not exact; newest compared with what runs ({ref})")
                    else:
                        row["notes"].append("pin not exact and running version not comparable")
                    if c["minor"] == "excluded" and row["newest_any"] in (pin.value, "-"):
                        row["newest_any"] = raw_latest(ops.web, c)
        except (LookupFailed, RulesError, OSError, ValueError) as e:
            row["notes"].append(f"lookup/read failed: {e}")
        rows.append(row)
    return rows


def builds(web: Web, comp: dict) -> list[Release]:
    """llama.cpp-style releases: every build is flagged pre-release on
    GitHub, so the flag says nothing (rule: ignore_prerelease_flag)."""
    rel = github_releases(web, comp["releases"].split(":", 1)[1], comp.get("tag_prefix", ""))
    if comp.get("ignore_prerelease_flag"):
        for r in rel:
            r.prerelease = False
    return [r for r in rel if not r.prerelease]


def raw_latest(web: Web, comp: dict) -> str:
    """Upstream's newest stable and pre-release tags, as published, for a
    component whose pin the rule cannot compare (an excluded fork)."""
    kind, repo = _kind_src(comp.get("releases", ""))
    if kind != "github":
        return "-"
    rel = github_releases(web, repo)
    st = next((r for r in rel if not r.prerelease), None)
    pre = next((r for r in rel if r.prerelease), None)
    return (f"upstream stable {st.version if st else 'none on the first page'}"
            + (f", pre-release {pre.version}" if pre else ""))


def _fake_pin(comp: dict, running: str) -> str | None:
    """For a floating pin, the running version in the rule's shape (nginx
    1.29.8 -> 1.29.8-alpine), so the inventory can say what is newer."""
    m = re.match(r"^(\S+)", running or "")
    if not m:
        return None
    v = m.group(1)
    for cand in (v, f"{v}-alpine", f"{v}-slim"):
        if vkey(comp, cand):
            return cand
    return None


def lock_vs_running(ops: Ops, comp: dict, pins: dict[str, str]) -> str:
    code = ("import importlib.metadata as m, json; "
            "print(json.dumps({d.metadata['Name'].lower(): d.version for d in m.distributions()}))")
    r = ops.docker("exec", comp["container"], "python", "-c", code, timeout=60)
    if not r.ok:
        return f"unreadable ({r.tail(60)})"
    have = {pkg_name(k): v for k, v in json.loads(r.out).items()}
    diff = [f"{k}: lock {v}, runs {have.get(k, 'absent')}" for k, v in pins.items() if have.get(k) != v]
    return "matches the lock" if not diff else "; ".join(diff[:5])


def inventory_markdown(rows: list[dict], web: Web, now: dt.datetime) -> str:
    out = [f"Inventory read {now.isoformat(timespec='minutes')} by `python3 scripts/upgrades.py inventory`.", "",
           "| Component | Rule | Pin | Written in | Running | Newest minor (rule) | Newest of any kind | Source |",
           "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        out.append("| " + " | ".join(str(x).replace("|", "/") for x in (
            r["name"], r["minor"], r["pin"], r["file"], r["running"], r["newest_minor"], r["newest_any"], r["source"])) + " |")
    notes = [(r["name"], n) for r in rows for n in r["notes"]]
    if notes:
        out += ["", "Notes:", ""] + [f"- {n}: {t}" for n, t in notes]
    for r in rows:
        if r.get("lock_patches") or r.get("lock_majors"):
            out += ["", f"{r['name']}: patch releases available: {', '.join(r.get('lock_patches') or ['none'])}.",
                    f"Newer minor/major: {', '.join(r.get('lock_majors') or ['none'])}."]
    out += ["", f"Hosts contacted: {', '.join(sorted(web.hosts))} ({web.requests} requests)."]
    return "\n".join(out) + "\n"


# ═══ The quarterly check: the major-release report ═══════════════════════

def gather_majors(ops: Ops, rules: Rules, now: dt.datetime) -> list[dict]:
    """Per component: the major release available and the releases between,
    with their notes where the source has them."""
    out = []
    for c in rules.components:
        e = {"comp": c, "name": c["name"], "pin": None, "newest": None, "between": [], "note": "", "fetched": []}
        try:
            if c["minor"] == "excluded":
                e["note"] = c.get("why", "excluded")
            elif c["kind"] == "lockfile":
                pins = read_lock(ops.repo, c)
                majors = []
                for pkg, ver in sorted(pins.items()):
                    if vkey(c, ver) is None:
                        continue
                    try:
                        r, _ = newest(c, ver, pypi_releases(ops.web, pkg), "major")
                    except LookupFailed as ex:
                        majors.append(f"{pkg}: lookup failed ({ex})")
                        continue
                    if r:
                        majors.append(f"{pkg} {ver} -> {r.version}")
                p = plan_lockfile(rules, c, ops.repo, ops.web, now)
                e["pin"] = f"{len(pins)} packages"
                e["lock_majors"], e["lock_patches"] = majors, p.reports
            elif c["kind"] == "llama-build":
                rel = builds(ops.web, c)
                e["pin"] = running_version(ops, c)
                best = max((r for r in rel if vkey(c, r.version)), key=lambda r: vkey(c, r.version), default=None)
                e["newest"] = best
            elif c["kind"] == "host-venv":
                e["pin"] = running_version(ops, c)
                rels = pypi_releases(ops.web, c["releases"].split(":", 1)[1])
                if vkey(c, e["pin"]):
                    e["newest"] = max((r for r in rels if classify(c, e["pin"], r.version) in ("minor", "major")),
                                      key=lambda r: vkey(c, r.version), default=None)
            elif c["kind"] in ("image", "base-image"):
                pin = read_pin(ops.repo, c).value
                ref = pin if vkey(c, pin) else _fake_pin(c, running_version(ops, c))
                e["pin"] = pin if ref == pin else f"{pin} (runs {ref})"
                if ref and vkey(c, ref):
                    rels = image_releases(ops.web, c, ref, deep=c["name"] == "homeassistant")
                    best, _ = newest(c, ref, rels, "major")
                    if best:
                        best = fill_date(ops.web, c, best)
                        e["newest"] = best
                        e["between"] = sorted((r for r in rels if not r.prerelease and classify(c, ref, r.version) == "major"),
                                              key=lambda r: vkey(c, r.version))
                else:
                    e["note"] = "pin not an exact version and the running version is not comparable"
            else:
                e["note"] = "report only (see the inventory)"
        except Exception as ex:  # noqa: BLE001 - LookupFailed, or an answer of a shape no check foresaw
            e["note"] = f"could not be read: {ex}"
        out.append(e)
    return out


_LINKDEF = re.compile(r"^\[[^\]]+\]:\s*https?://")


def _has_word(low: str, w: str) -> bool:
    """`w` in `low` at the start of a word ('tts' is not in 'watts'; the
    stem 'deprecat' and 'callback' still match 'deprecated', 'callbacks')."""
    return re.search(r"(?<![a-z0-9])" + re.escape(w), low) is not None


def scan_notes(text: str, points: dict[str, list[str]], skip: str | None = None) -> dict[str, list[str]]:
    """Lines of release-note text that mention each integration point.
    Markdown link definitions and lines matching `skip` are ignored."""
    hits: dict[str, list[str]] = {}
    skip_re = re.compile(skip) if skip else None
    for line in (text or "").splitlines():
        s = line.strip(" -*\t")
        if len(s) < 8 or _LINKDEF.match(s) or (skip_re and skip_re.search(s)):
            continue
        low = s.lower()
        for point, words in points.items():
            if any(_has_word(low, w) for w in words):
                hits.setdefault(point, []).append(s[:300])
    return hits


def html_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<(br|/p|/li|/h[1-6]|/div|li|h[1-6])[^>]*>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        text = text.replace(a, b)
    return "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines() if ln.strip())


def ha_blog_notes(ops: Ops, releases: list[Release]) -> list[tuple[str, str, str | None]]:
    """(version, url, text or None) for each monthly .0 release's blog post."""
    out = []
    for r in releases:
        if not r.version.endswith(".0"):
            continue
        m = re.search(r"https://www\.home-assistant\.io/blog/\d{4}/\d{2}/\d{2}/release-\d+/?", r.notes or "")
        if not m:
            out.append((r.version, r.url or "", None))
            continue
        try:
            body, _ = ops.web.get(m.group(0))
            out.append((r.version, m.group(0), html_text(body.decode("utf-8", "replace"))))
        except LookupFailed:
            out.append((r.version, m.group(0), None))
    return out


def write_major_report(ops: Ops, rules: Rules, now: dt.datetime, majors: list[dict]) -> str:
    rep = rules.report
    points: dict = rep.get("integration_points", {})
    affects: dict = rep.get("affects", {})
    generic = rep.get("generic_keywords", ["breaking", "deprecat", "removed", "security", "cve"])
    date = now.strftime("%Y-%m-%d")
    L = [f"# Major-upgrade report, {date}", "",
         f"Written by `scripts/upgrade_major_check.sh` (sprint M18) on {now.isoformat(timespec='minutes')}. "
         "It upgrades nothing. Each major upgrade waits for Drew's greenlight and a declared window, one "
         "component per window, following `docs/runbooks/major-upgrade.md`.", ""]
    have = [e for e in majors if e.get("newest") or e.get("lock_majors")]
    L += ["## Version changes", "", "| Component | Pinned / running | Newest release | Released | Rule |", "| --- | --- | --- | --- | --- |"]
    for e in majors:
        n = e.get("newest")
        if e["comp"]["kind"] == "lockfile":
            newest_s = f"{len(e.get('lock_majors') or [])} package(s) with a newer minor/major"
            when = "-"
        else:
            newest_s = n.version if n else ("none" if not e["note"] else e["note"])
            when = n.date.date().isoformat() if n and n.date else "-"
        L.append(f"| {e['name']} | {e['pin'] or '-'} | {newest_s} | {when} | {e['comp']['minor']} |")
    L.append("")
    # Release notes per component.
    L += ["## Release notes that touch Kronk's integration points", ""]
    order = rep.get("first", []) + [e["name"] for e in majors if e["name"] not in rep.get("first", [])]
    by_name = {e["name"]: e for e in majors}
    for name in order:
        e = by_name.get(name)
        if not e or not e.get("newest") or e["comp"]["kind"] in ("lockfile",):
            continue
        c = e["comp"]
        pin_s = e["pin"] if len(str(e["pin"])) <= 60 else "see the table"
        L += [f"### {name}: {pin_s} -> {e['newest'].version}", ""]
        texts: list[tuple[str, str, str | None]] = []
        if c.get("releases", "").startswith("github:"):
            for r in e["between"]:
                if r.notes is not None:
                    texts.append((r.version, r.url or "", r.notes))
            missing = [r.version for r in e["between"] if r.notes is None]
            if name == "homeassistant":
                texts += ha_blog_notes(ops, [r for r in e["between"] if r.notes is not None])
            if missing:
                L.append(f"Not fetched (outside the release pages read): {', '.join(missing[:30])}"
                         + (" …" if len(missing) > 30 else "") + ".")
        else:
            L.append(f"No release-note source for {name} ({c.get('releases', 'none')}); read the project's changelog by hand.")
        kw = points if name == "homeassistant" else {
            "General (breaking, deprecated, removed, security)": c.get("report_keywords", generic)}
        found: dict[str, list[tuple[str, str, str]]] = {}
        unreadable = []
        for ver, url, text in texts:
            if text is None:
                unreadable.append(f"{ver} ({url})")
                continue
            for point, lines in scan_notes(text, kw, c.get("report_skip")).items():
                for ln in lines:
                    found.setdefault(point, []).append((ver, url, ln))
        if unreadable:
            L.append(f"Could not be fetched: {', '.join(unreadable)}.")
        if not found:
            L.append("No release-note line mentions an integration point." if texts else "")
        for point, items in found.items():
            sysd = affects.get(point)
            L += ["", f"**{point}**" + (f" (Kronk: {sysd})" if sysd else ""), ""]
            seen = set()
            for ver, url, ln in items:
                if ln in seen:
                    continue
                seen.add(ln)
                L.append(f"- {ver}: {ln}")
                if len(seen) >= rep.get("max_lines_per_point", 25):
                    L.append(f"- … {len(items) - len(seen)} more lines; see the release notes")
                    break
        L.append("")
    locks = [e for e in majors if e["comp"]["kind"] == "lockfile"]
    if locks:
        L += ["## Python lockfiles", ""]
        for e in locks:
            L.append(f"- {e['name']}: patch releases: {', '.join(e.get('lock_patches') or ['none'])}; "
                     f"newer minor/major: {', '.join(e.get('lock_majors') or ['none'])}.")
        L.append("")
    excl = [e for e in majors if e["comp"]["minor"] == "excluded"]
    if excl:
        L += ["## Excluded", ""] + [f"- {e['name']}: {e['note']}" for e in excl] + [""]
    prop = [n for n in order if by_name.get(n) and by_name[n].get("newest")]
    L += ["## Proposed order", "",
          "One component per window; each through `docs/runbooks/major-upgrade.md`. "
          + ("Home Assistant first (plan M18 step 5: M8 and M15 may need newer HA). " if "homeassistant" in prop else ""), ""]
    L += [f"{i}. {n}" for i, n in enumerate(prop, 1)] + [""]
    L += [f"Lookups: {ops.web.requests} request(s) to {', '.join(sorted(ops.web.hosts))}.", ""]
    return "\n".join(L)


def major_run(ops: Ops, rules: Rules, sd: Path, dry_run: bool = False, out: Path | None = None,
              notify: bool = True) -> int:
    now = ops.now()
    date = now.strftime("%Y-%m-%d")
    log = RunLog(None if (dry_run or out is not None) else sd / "runs" / f"{now.strftime('%Y%m%dT%H%M%SZ')}-major.log")
    log(f"upgrade_major_check {'DRY RUN ' if dry_run else ''}{now.isoformat(timespec='seconds')}")
    majors = gather_majors(ops, rules, now)
    text = write_major_report(ops, rules, now, majors)
    names = [f"{e['name']} {e['pin']} -> {e['newest'].version}" for e in majors if e.get("newest")]
    if dry_run:
        print(text)
        log("dry run: report not written, nothing committed, nothing sent")
        return 0
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        log(f"report written to {out} (no commit, no notice)")
        return 0
    sd.mkdir(parents=True, exist_ok=True)
    fd = wait_lock(ops, sd, f"pid={os.getpid()}\nrun=upgrade_major_check {now.strftime('%Y%m%dT%H%M%SZ')}\n",
                   rules.settings["major_lock_wait_minutes"], log)
    if fd is None:
        log(f"STOPPED: the upgrade lock is still held ({lock_holder(sd)}); the report was not written. "
            f"Run scripts/upgrade_major_check.sh when it is free.")
        try:
            ops.notify(NOTIFY_TAG, "Kronk: the quarterly upgrade check did not run",
                       f"The upgrade lock stayed held ({lock_holder(sd)}). Run scripts/upgrade_major_check.sh by hand.")
        except Exception as ex:  # noqa: BLE001
            log(f"notice: notifier raised {ex!r}")
        return 1
    try:
        return _major_write(ops, rules, sd, now, date, text, names, notify, log)
    finally:
        os.close(fd)


def _major_write(ops: Ops, rules: Rules, sd: Path, now: dt.datetime, date: str, text: str, names: list[str],
                 notify: bool, log) -> int:
    try:
        status = load_status(sd)
    except StatusCorrupt as e:
        return status_corrupt(ops, e, log, notify=True)
    rel = f"docs/upgrades/{date}.md"
    why = tree_state(ops, rules)
    if not why:
        path = ops.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        ops.git("add", "--", rel)
        ops.git("commit", "-q", "-m", f"upgrade report: {date} (major releases)", "-m",
                "Written by scripts/upgrade_major_check.sh (sprint M18). Upgrades nothing.", "--", rel)
        where = rel
        log(f"report committed: {rel}")
    else:
        path = sd / "reports" / f"{date}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        where = str(path.relative_to(ops.repo)) if path.is_relative_to(ops.repo) else str(path)
        log(f"report written outside git ({'; '.join(why)}): {where}")
    status["major"] = {"at": now.isoformat(timespec="seconds"), "report": where, "components": names}
    if names and notify:
        deliver(ops, status, {"id": f"major-{date}", "tag": NOTIFY_TAG,
                              "title": f"Kronk: {len(names)} major upgrade(s) need scheduling",
                              "body": f"{'; '.join(names)[:600]}. Report: {where}. Nothing was upgraded; "
                                      f"greenlight and declare a window, or decline.",
                              "created": now.isoformat(timespec="seconds")}, now, log)
    elif not names:
        log("no major release available; no notice")
    save_status(sd, status, now)
    return 0


# ═══ The hourly unit and an unfinished record (G2) ═══════════════════════
#
# A record with the lock free means the run that wrote it died. The hourly
# `notices` command does two things about it and nothing else: if the record
# is Home Assistant's and HA's container is stopped, it starts that container
# (start_stopped_ha: no compose, no pin or git change); and it raises the
# /services warning and sends one notice. The next monthly run (or
# `scripts/upgrade_minor.sh --ignore-window`) does the recovery itself.

def hourly_start_ha(ops: Ops, rules: Rules, sd: Path, status: dict, log: RunLog) -> str | None:
    it = status["intent"]
    try:
        comp = rules.component(it.get("component"))
    except KeyError:
        return None
    if comp.get("deploy") != "ha" or container_state(ops, comp)[0] != "stopped":
        return None
    ctx = Ctx(ops, rules, log, sd, "hourly", ops.now())
    err = start_stopped_ha(ctx, comp)
    return f"Home Assistant is down: {err}" if err else "Home Assistant was down; started again"


def hourly_notice(ops: Ops, status: dict, now: dt.datetime, log: RunLog, back: str | None) -> None:
    it = status["intent"]
    name = it.get("component")
    text = (f"Upgrade interrupted inside {name} {it.get('from')} -> {it.get('to')} at step {it.get('step')!r} "
            f"({it.get('at')}, run {it.get('run')}). The next monthly run recovers it, or "
            f"scripts/upgrade_minor.sh --ignore-window now; `python3 scripts/upgrades.py ack` drops the record "
            f"for a person to take over." + (f" {back}." if back else ""))
    add_attention(status, "interrupted", text, now)
    if it.get("noticed") and not back:
        return
    it["noticed"] = now.isoformat(timespec="seconds")
    deliver(ops, status, {"id": f"interrupted-{name}-{now.strftime('%Y%m%dT%H%M%SZ')}", "tag": NOTIFY_TAG,
                          "title": f"Kronk upgrade: a run was interrupted ({name})" + (f": {back}" if back else ""),
                          "body": text, "created": now.isoformat(timespec="seconds")}, now, log)


# ═══ CLI ═════════════════════════════════════════════════════════════════

def main(argv=None, ops: Ops | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    mi = sub.add_parser("minor", help="the monthly patch run")
    mi.add_argument("--dry-run", action="store_true")
    mi.add_argument("--ignore-month", action="store_true", help="run even if this month's run is done")
    mi.add_argument("--ignore-window", action="store_true",
                    help="run outside the night window (a run by hand; the house checks still apply)")
    ma = sub.add_parser("major", help="the quarterly major-release report")
    ma.add_argument("--dry-run", action="store_true")
    ma.add_argument("--out", help="write the report here; no commit, no notice")
    sub.add_parser("inventory", help="pins, what runs, what is newest (markdown)")
    sub.add_parser("notices", help="retry notices HA did not take")
    sub.add_parser("ack", help="clear the /services warning and a failed-rollback block")
    sub.add_parser("status", help="print the status file")
    sub.add_parser("test-notice", help="send one notice now, marked TEST (install check)")
    args = ap.parse_args(argv)
    rules = load_rules()
    ops = ops or Ops()
    sd = state_dir(ops.repo)
    if args.cmd == "minor":
        return minor_run(ops, rules, sd, args.dry_run, args.ignore_month, args.ignore_window)
    if args.cmd == "major":
        return major_run(ops, rules, sd, args.dry_run, Path(args.out) if args.out else None)
    if args.cmd == "inventory":
        now = ops.now()
        print(inventory_markdown(inventory(ops, rules, now), ops.web, now))
        return 0
    if args.cmd in ("notices", "ack", "status"):
        try:
            load_status(sd)
        except StatusCorrupt as e:
            return status_corrupt(ops, e, print, notify=False)
    if args.cmd == "notices":
        status = load_status(sd)
        if not status.get("pending_notices") and not status.get("intent"):
            return 0
        fd = take_lock(sd)
        if fd is None:
            print("upgrades: a run holds the lock; it retries notices itself")
            return 0
        try:
            status = load_status(sd)
            log = RunLog(None)
            back = hourly_start_ha(ops, rules, sd, status, log) if status.get("intent") else None
            left = retry_pending(ops, status, ops.now(), log)
            if status.get("intent"):
                hourly_notice(ops, status, ops.now(), log, back)
            save_status(sd, status, ops.now())
            return 0 if left == 0 else 1
        finally:
            os.close(fd)
    if args.cmd == "ack":
        fd = take_lock(sd) if sd.exists() else None
        if sd.exists() and fd is None:
            print("upgrades: a run holds the lock; try again when it ends")
            return 1
        try:
            status = load_status(sd)
            print(f"cleared: {len(status.get('attention', []))} warning(s)"
                  + (", the failed-rollback block" if status.get("rollback_failed") else "")
                  + (", the out-of-step block" if status.get("out_of_step") else ""))
            status["acknowledged"] = {"at": ops.now().isoformat(timespec="seconds"),
                                      "attention": status.get("attention", []),
                                      "rollback_failed": status.get("rollback_failed"),
                                      "out_of_step": status.get("out_of_step")}
            status["attention"], status["rollback_failed"], status["out_of_step"] = [], None, None
            if sd.exists():
                save_status(sd, status, ops.now())
            return 0
        finally:
            if fd is not None:
                os.close(fd)
    if args.cmd == "status":
        print(json.dumps(load_status(sd), indent=2, sort_keys=True))
        return 0
    if args.cmd == "test-notice":
        ok = ops.notify(NOTIFY_TAG, "Kronk: TEST upgrade notice",
                        "Install check for the upgrade cadence (sprint M18). Patch runs: days 1-7 of each month "
                        "at 03:30; the major-upgrade report: the 8th of Jan/Apr/Jul/Oct. Nothing was upgraded.")
        print("test notice " + ("sent" if ok else "NOT delivered"))
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
