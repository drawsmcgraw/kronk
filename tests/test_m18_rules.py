"""Sprint M18, criteria 2 and 14: the version rules as data
(scripts/upgrade_rules.toml) and the version comparison for every component,
with real version strings and awkward ones. No sockets."""
import pytest

from tests.m18_helpers import NOW, U, iso

pytestmark = pytest.mark.usefixtures("no_network")


# ── the rules file ───────────────────────────────────────────────────────

def test_rules_file_loads_and_every_component_has_a_comment_and_a_rule():
    r = U.load_rules(env={})
    names = [c["name"] for c in r.components]
    assert len(names) == len(set(names))
    text = U.RULES_FILE.read_text()
    blocks = text.split("\n[[component]]\n")[1:]
    assert len(blocks) == len(r.components)
    for b in blocks:                       # a comment per component (criterion 2)
        assert b.lstrip("\n").startswith("#") or "\n#" in b.split("name =")[0], b[:80]


def test_what_is_never_automatic():
    r = U.load_rules(env={})
    by = {c["name"]: c for c in r.components}
    assert by["music-assistant"]["minor"] == "excluded"
    assert by["bgutil-pot-provider"]["minor"] == "excluded"
    assert by["llama.cpp"]["minor"] == "none"
    for c in r.components:
        if c["kind"] in ("lockfile", "llama-build", "host-venv", "blueprint"):
            assert c["minor"] != "auto" and "deploy" not in c, c["name"]
    auto = [c["name"] for c in r.components if c["minor"] == "auto"]
    assert set(auto) >= {"tempo", "grafana", "wyoming-piper", "searxng", "litellm", "homeassistant", "nginx"}
    assert auto[-1] == "homeassistant"     # last: every other replay goes through HA


def test_rules_refuse_ma_or_llama_as_automatic(tmp_path):
    bad = U.RULES_FILE.read_text().replace('minor = "excluded"\nwhy = "Music Assistant', 'minor = "auto"\ndeploy = "compose"\nwhy = "Music Assistant')
    p = tmp_path / "r.toml"
    p.write_text(bad)
    with pytest.raises(U.RulesError, match="music-assistant"):
        U.load_rules(p, env={})


@pytest.mark.parametrize("mutation, msg", [
    ('minor = "report"\nreleases = "pypi"\ncontainer = "kronk-orchestrator-1"',
     'minor = "auto"\ndeploy = "kronk"\nreleases = "pypi"\ncontainer = "kronk-orchestrator-1"'),
])
def test_rules_refuse_a_deployable_lockfile(tmp_path, mutation, msg):
    p = tmp_path / "r.toml"
    p.write_text(U.RULES_FILE.read_text().replace(mutation, msg, 1))
    with pytest.raises(U.RulesError, match="lockfile"):
        U.load_rules(p, env={})


def test_expected_branch_is_a_setting_with_an_env_override():
    assert U.load_rules(env={}).settings["expected_branch"] == "main"
    assert U.load_rules(env={"KRONK_UPGRADE_BRANCH": "refactor"}).settings["expected_branch"] == "refactor"


# ── versions, per component ──────────────────────────────────────────────

def C(name):
    return U.load_rules(env={}).component(name)


@pytest.mark.parametrize("name, pin, cand, want", [
    # Home Assistant: patch within the month is minor, a new month is major.
    ("homeassistant", "2026.5.4", "2026.5.5", "minor"),
    ("homeassistant", "2026.5.4", "2026.5.10", "minor"),
    ("homeassistant", "2026.5.4", "2026.6.0", "major"),
    ("homeassistant", "2026.5.4", "2027.1.0", "major"),
    ("homeassistant", "2026.5.4", "2026.5.4", "not-newer"),
    ("homeassistant", "2026.5.4", "2026.5.3", "not-newer"),
    ("homeassistant", "2026.5.4", "2026.6.0b0", None),
    ("homeassistant", "2026.5.4", "2026.12.0.dev202611170234", None),
    ("homeassistant", "2026.5.4", "stable", None),
    ("homeassistant", "2026.5.4", "2026.5", None),
    # LiteLLM: v-prefixed semver; suffixed tags are not releases.
    ("litellm", "v1.88.1", "v1.88.6", "minor"),
    ("litellm", "v1.88.1", "v1.89.0", "major"),
    ("litellm", "v1.88.1", "v1.110.0", "major"),
    ("litellm", "v1.88.1", "v1.88.6-stable", None),
    ("litellm", "v1.88.1", "main-v1.88.2-nightly", None),
    ("litellm", "v1.88.1", "1.88.2", None),
    # SearXNG: rolling date builds; any newer build is the monthly move.
    ("searxng", "2026.11.9-57ff753ad", "2026.11.17-b99cf7496", "minor"),
    ("searxng", "2026.11.9-57ff753ad", "2027.1.2-0123456", "minor"),
    ("searxng", "2026.11.9-57ff753ad", "2026.11.9-0000000aa", "not-newer"),
    ("searxng", "2026.11.9-57ff753ad", "2026.10.4-d48c4b555", "not-newer"),
    ("searxng", "2026.11.9-57ff753ad", "latest", None),
    ("searxng", "2026.11.9-57ff753ad", "2026.11.17", None),
    # Tempo, Grafana, Piper: patch is minor, minor or major is major.
    ("tempo", "3.1.1", "3.1.2", "minor"),
    ("tempo", "3.1.1", "3.2.0", "major"),
    ("tempo", "3.1.1", "3.1.2-amd64", None),
    ("tempo", "3.1.1", "main-6d180dd-2464-1", None),
    ("grafana", "13.2.4", "13.2.10", "minor"),
    ("grafana", "13.2.4", "13.3.1", "major"),
    ("grafana", "13.2.4", "13.2.5-ubuntu", None),
    ("wyoming-piper", "2.2.2", "2.2.3", "minor"),
    ("wyoming-piper", "2.2.2", "2.5.4", "major"),
    # Base image (once pinned exactly).
    ("python-base-orchestrator", "3.12.13-slim", "3.12.16-slim", "minor"),
    ("python-base-orchestrator", "3.12.13-slim", "3.13.1-slim", "major"),
    ("python-base-orchestrator", "3.12.13-slim", "3.12.16-slim-trixie", None),
    # Python lockfiles: X.Y means X.Y.0; pre and post releases are not understood.
    ("lock-orchestrator", "16.0", "16.0.1", "minor"),
    ("lock-orchestrator", "0.115.0", "0.115.14", "minor"),
    ("lock-orchestrator", "0.115.0", "0.116.0", "major"),
    ("lock-orchestrator", "2026.2.25", "2026.10.23", "major"),
    ("lock-orchestrator", "1.45.1", "1.45.2rc1", None),
    ("lock-orchestrator", "1.45.1", "1.45.2.post1", None),
    ("lock-orchestrator", "1.45.1", "1.45.2.1", None),
    # llama.cpp: any build is major (and minor = none anyway).
    ("llama.cpp", "9611", "b12498", "major"),
    # wyoming-faster-whisper (report only).
    ("wyoming-faster-whisper", "3.1.0", "3.1.4", "minor"),
    ("wyoming-faster-whisper", "3.1.0", "3.9.0", "major"),
])
def test_classify(name, pin, cand, want):
    assert U.classify(C(name), pin, cand) == want


@pytest.mark.parametrize("name, pin", [
    ("nginx", "1.29-alpine"),                       # floating minor tag
    ("python-base-orchestrator", "3.12-slim"),      # floating
    ("uv", "uv"),                                   # unpinned
    ("lock-orchestrator", "0.66b1"),                # pre-release pin
    ("lock-tool_service", "2.9.0.post0"),
    ("homeassistant", "stable"),
    ("homeassistant", "2026.5.4 "),
    ("litellm", "main-latest"),
])
def test_a_pin_the_rule_cannot_parse_is_refused_never_guessed(name, pin):
    assert U.vkey(C(name), pin) is None
    with pytest.raises(U.RulesError, match="not an exact version"):
        U.classify(C(name), pin, "1.0.0")


def test_numbers_compare_as_numbers_not_text():
    c = C("litellm")
    assert U.vkey(c, "v1.110.0") > U.vkey(c, "v1.88.9")
    assert U.vkey(C("homeassistant"), "2026.10.1") > U.vkey(C("homeassistant"), "2026.9.9")


def test_newest_honours_age_dates_and_prereleases():
    c = C("grafana")
    rel = [U.Release("13.2.7", U._date(iso(3))), U.Release("13.2.6", U._date(iso(21))),
           U.Release("13.2.8", None), U.Release("13.2.9", U._date(iso(40)), prerelease=True),
           U.Release("13.3.1", U._date(iso(30)))]
    r, notes = U.newest(c, "13.2.4", rel, "minor", NOW, 14)
    assert r.version == "13.2.6"
    assert any("13.2.7: 3 days old" in n for n in notes)
    assert any("13.2.8: release date unknown" in n for n in notes)
    assert U.newest(c, "13.2.4", rel, "major")[0].version == "13.3.1"


