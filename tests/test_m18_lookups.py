"""Sprint M18, criteria 1, 2 and 14: reading and writing pins, and the
read-only lookups (registries, release pages, PyPI) against a fake
internet: allowed hosts only, a generic client name, every host recorded.
No sockets."""
import json
import urllib.error

import pytest

from tests.m18_helpers import NOW, FakeWeb, U, gh_release, iso, rules, standard_web

pytestmark = pytest.mark.usefixtures("no_network")


def C(name):
    return U.load_rules(env={}).component(name)


# ── pins ─────────────────────────────────────────────────────────────────

def test_read_and_write_pin_touch_only_the_pin(tmp_path):
    c = dict(C("tempo"), file="t.yml")
    (tmp_path / "t.yml").write_text("services:\n  tempo:\n    image: grafana/tempo:3.1.1   # pinned\n  x:\n    image: other:1\n")
    p = U.read_pin(tmp_path, c)
    assert (p.value, p.line) == ("3.1.1", 3)
    U.write_pin(tmp_path, c, "3.1.1", "3.1.2")
    assert (tmp_path / "t.yml").read_text() == "services:\n  tempo:\n    image: grafana/tempo:3.1.2   # pinned\n  x:\n    image: other:1\n"
    with pytest.raises(U.RulesError, match="expected '3.1.1'"):
        U.write_pin(tmp_path, c, "3.1.1", "3.1.3")


@pytest.mark.parametrize("text, n", [("nothing here\n", 0), ("image: grafana/tempo:1.0.0\nimage: grafana/tempo:2.0.0\n", 2)])
def test_a_pin_must_be_on_exactly_one_line(tmp_path, text, n):
    (tmp_path / "t.yml").write_text(text)
    with pytest.raises(U.RulesError, match=f"{n} lines"):
        U.read_pin(tmp_path, dict(C("tempo"), file="t.yml"))


def test_every_real_pin_reads_from_the_repo():
    r = U.load_rules(env={})
    for c in r.components:
        if c.get("match") and c["kind"] not in ("llama-build",):
            U.read_pin(U.REPO, c)              # exactly one line each
    # Exact under the rule (the monthly run may move it, so no literal value here).
    assert U.vkey(r.component("homeassistant"), U.read_pin(U.REPO, r.component("homeassistant")).value)


def test_read_lock_normalises_names(tmp_path):
    (tmp_path / "r.lock").write_text("ruamel-yaml==0.19.1 \\\n    --hash=sha256:aa\nPyYAML==6.0.2 \\\n# comment\nzope.interface==7.0\n")
    assert U.read_lock(tmp_path, {"file": "r.lock"}) == {"ruamel-yaml": "0.19.1", "pyyaml": "6.0.2", "zope-interface": "7.0"}


# ── lookups: read-only, allowed hosts, nothing about this system ─────────

def test_web_refuses_other_hosts_and_plain_http():
    w = FakeWeb()
    for url in ("http://pypi.org/pypi/x/json", "https://kronk.local/x", "https://192.168.1.14/x", "https://example.com/"):
        with pytest.raises(U.LookupFailed, match="refused"):
            w.get(url)
    assert w.seen == []


def test_web_sends_a_generic_client_name_and_nothing_else():
    w = FakeWeb({"https://pypi.org/pypi/x/json": {"releases": {}}})
    w.json("https://pypi.org/pypi/x/json")
    (url, hdrs), = w.seen
    assert hdrs == {"User-agent": "upgrade-check/1.0"}
    assert "kronk" not in json.dumps(hdrs).lower()
    assert w.hosts == {"pypi.org"} and w.requests == 1
    w.json("https://pypi.org/pypi/x/json")      # cached
    assert w.requests == 1


def test_lookup_errors_become_lookup_failed():
    w = FakeWeb({"https://pypi.org/": urllib.error.URLError("no route")})
    with pytest.raises(U.LookupFailed):
        w.get("https://pypi.org/pypi/x/json")


def test_ghcr_tags_follow_the_link_header():
    w = FakeWeb({
        "https://ghcr.io/token": {"token": "t"},
        "https://ghcr.io/v2/a/b/tags/list?n=1000&last=x": ({"tags": ["2"]}, {}),
        "https://ghcr.io/v2/a/b/tags/list?n=1000": ({"tags": ["1"]}, {"link": '</v2/a/b/tags/list?n=1000&last=x>; rel="next"'}),
    })
    assert U.ghcr_tags(w, "a/b") == ["1", "2"]


def test_github_releases_strip_the_prefix_and_keep_prerelease_flags():
    w = FakeWeb({"https://api.github.com/repos/grafana/tempo/releases?": [gh_release("v3.1.1", 1), gh_release("v3.2.0-rc.1", 1, pre=True)]})
    rel = U.github_releases(w, "grafana/tempo", "v")
    assert [(r.version, r.prerelease) for r in rel] == [("3.1.1", False), ("3.2.0-rc.1", True)]


def test_same_prefix_for_registry_filters():
    assert U._same_prefix(C("tempo"), "3.1.1") == "3.1."
    assert U._same_prefix(C("homeassistant"), "2026.5.4") == "2026.5."
    assert U._same_prefix(C("litellm"), "v1.88.1") == "v1.88."
    assert U._same_prefix(C("searxng"), "2026.11.9-57ff753ad") == ""


# ── planning against the fake internet ───────────────────────────────────

def _plan(name, repo=U.REPO, web=None):
    r = rules()
    return U.plan_component(r, r.component(name), repo, web or standard_web(), NOW)


def test_plan_takes_the_newest_patch_old_enough_and_never_a_major(tmp_path):
    from tests.m18_helpers import base_files, make_repo
    repo = make_repo(tmp_path, base_files())
    p = _plan("grafana", repo)
    assert (p.action, p.to.version) == ("upgrade", "13.2.6")          # 13.2.7 is 3 days old; 13.3.1 is major
    assert any("13.2.7: 3 days old" in n for n in p.notes)
    p = _plan("tempo", repo)
    assert (p.action, p.to.version) == ("upgrade", "3.1.2")           # 3.2.0 is major
    p = _plan("searxng", repo)
    assert (p.action, p.to.version) == ("upgrade", "2026.11.17-b99cf7496")
    p = _plan("litellm", repo)
    assert (p.action, p.to.version) == ("upgrade", "v1.88.6")         # date from the GitHub release by tag
    assert _plan("homeassistant", repo).action == "current"           # 2026.5.4 is the last 2026.5
    assert _plan("wyoming-piper", repo).action == "current"           # 2.5.4 is major


def test_plan_skips_a_floating_pin_without_a_lookup(tmp_path):
    from tests.m18_helpers import base_files, make_repo
    repo = make_repo(tmp_path, base_files())
    w = standard_web()
    p = _plan("nginx", repo, w)
    assert p.action == "skip" and "not an exact version" in p.detail and w.requests == 0


def test_plan_never_looks_up_excluded_components():
    w = standard_web()
    p = _plan("music-assistant", web=w)
    assert p.action == "excluded" and w.requests == 0
    assert _plan("llama.cpp", web=w).action == "none" and w.requests == 0


def test_a_registry_that_cannot_be_read_is_a_reported_skip(tmp_path):
    from tests.m18_helpers import base_files, make_repo
    repo = make_repo(tmp_path, base_files())
    w = standard_web()
    w.table["https://api.github.com/repos/grafana/tempo/releases?"] = urllib.error.URLError("timed out")
    p = _plan("tempo", repo, w)
    assert p.action == "skip" and "lookup failed" in p.detail


def test_a_candidate_missing_from_the_registry_is_not_taken(tmp_path):
    from tests.m18_helpers import base_files, make_repo
    repo = make_repo(tmp_path, base_files())
    w = standard_web()
    w.table["https://ghcr.io/v2/berriai/litellm/tags/list"] = {"tags": ["v1.88.1"]}
    w.table["https://api.github.com/repos/BerriAI/litellm/releases?"] = [gh_release("v1.88.6", 100)]
    p = _plan("litellm", repo, w)
    assert p.action == "current" and any("not in the registry" in n for n in p.notes)


def test_lockfile_plan_lists_patch_releases_and_skips_prereleases(tmp_path):
    (tmp_path / "orchestrator").mkdir()
    (tmp_path / "orchestrator" / "requirements.lock").write_text(
        "fastapi==0.115.0 \\\n    --hash=sha256:a\nopentelemetry-instrumentation==0.66b1 \\\n    --hash=sha256:b\n")
    w = FakeWeb({"https://pypi.org/pypi/fastapi/json": {"releases": {
        "0.115.14": [{"upload_time_iso_8601": iso(60)}], "0.115.15": [{"upload_time_iso_8601": iso(2)}],
        "0.116.0": [{"upload_time_iso_8601": iso(90)}], "0.115.13": [{"upload_time_iso_8601": iso(90), "yanked": True}]}}})
    r = rules()
    p = U.plan_component(r, r.component("lock-orchestrator"), tmp_path, w, NOW)
    assert p.action == "report"
    assert p.reports == ["fastapi 0.115.0 -> 0.115.14"]
    assert any("0.66b1" in n for n in p.notes)
