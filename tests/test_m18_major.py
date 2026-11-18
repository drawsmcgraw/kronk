"""Sprint M18, criteria 9 and 14: the quarterly major-release check and
its report writer, with release notes from a fake internet. It writes the
report, commits it when the tree allows, then tells Drew; it upgrades
nothing. The notifier is stubbed."""
import json

import pytest

from tests.m18_helpers import NOW, FakeOps, FakeWeb, U, base_files, gh_release, git, hub_tag, make_repo, rules

pytestmark = pytest.mark.usefixtures("no_network")

ONLY = ["grafana", "litellm", "homeassistant", "lock-orchestrator", "music-assistant"]
BLOG = "https://www.home-assistant.io/blog/2026/06/03/release-20266/"
BLOG_HTML = f"""<html><head><script>var x = "ollama in a script is not a note";</script></head><body>
<h2>Backward-incompatible changes</h2>
<ul><li>The <b>Ollama</b> integration now requires a model to be selected per conversation agent.</li>
<li>Assist pipeline: the debug_recording_dir option moved under the pipeline settings.</li>
<li>Wyoming satellites must be re-added after the upgrade.</li>
<li>Something about Zigbee that Kronk does not use.</li></ul></body></html>"""


def web():
    gh = "https://api.github.com/repos/"
    return FakeWeb({
        gh + "home-assistant/core/releases?": [
            gh_release("2026.7.1", 20, body="- Fix Music Assistant media browsing ([#1001])\n- Fix a Zigbee thing ([#1002])"),
            gh_release("2026.7.0", 30, body="Release notes: https://www.home-assistant.io/blog/2026/07/01/release-20267/"),
            gh_release("2026.6.0", 60, body=f"Release notes: {BLOG}"),
            gh_release("2026.6.0b1", 62, pre=True, body="- Ollama beta"),
            gh_release("2026.5.4", 190)],
        "https://www.home-assistant.io/blog/2026/06/03/": BLOG_HTML.encode(),
        "https://www.home-assistant.io/blog/2026/07/01/": OSError("timed out"),
        "https://ghcr.io/token": {"token": "anon"},
        "https://ghcr.io/v2/home-assistant/home-assistant/tags/list": {"tags": ["2026.5.4", "2026.6.0", "2026.7.0", "2026.7.1"]},
        gh + "grafana/grafana/releases?": [gh_release("v13.3.1", 21, body="### Breaking changes\n- Removed the legacy alerting API"),
                                            gh_release("v13.2.6", 21)],
        "https://hub.docker.com/v2/repositories/grafana/grafana/tags?": {"results": [hub_tag("13.3.1", 21)]},
        gh + "BerriAI/litellm/releases?": [gh_release("v1.110.0", 13, body="## What's Changed\n- deprecate the old callbacks API\n- faster"),
                                            gh_release("v1.109.0", 20, body="- nothing")],
        "https://ghcr.io/v2/berriai/litellm/tags/list": {"tags": ["v1.88.1", "v1.110.0", "v1.109.0", "v1.95.0"]},
        "https://pypi.org/pypi/fastapi/json": {"releases": {"0.115.0": [{"upload_time_iso_8601": "2024-09-17T00:00:00Z"}],
                                                            "0.163.2": [{"upload_time_iso_8601": "2026-10-01T00:00:00Z"}]}},
    })


@pytest.fixture
def house(tmp_path, monkeypatch):
    files = base_files()
    files["orchestrator/requirements.lock"] = "fastapi==0.115.0 \\\n    --hash=sha256:a\n"
    files["docker-compose.ma.yml"] = "services:\n  music-assistant:\n    image: kronk/music-assistant:2.11.0b5-kronk.9\n"
    repo = make_repo(tmp_path, files)
    ops = FakeOps(repo, web())
    return ops, tmp_path / "state"


def test_report_lists_version_changes_notes_systems_and_an_order(house):
    ops, _ = house
    r = rules(ONLY)
    text = U.write_major_report(ops, r, NOW, U.gather_majors(ops, r, NOW))
    assert text.startswith("# Major-upgrade report, 2026-12-01")
    assert "It upgrades nothing." in text
    assert "| homeassistant | 2026.5.4 | 2026.7.1 |" in text
    assert "| grafana | 13.2.4 | 13.3.1 |" in text
    assert "| litellm | v1.88.1 | v1.110.0 |" in text
    # HA: blog post and patch notes scanned for the integration points, each with Kronk's systems.
    assert "**Ollama conversation integration** (Kronk: the conversation entities behind the kronk and kronk-dev pipelines" in text
    assert "- 2026.6.0: The Ollama integration now requires a model to be selected per conversation agent." in text
    assert "debug_recording_dir option moved" in text
    assert "**Wyoming** (Kronk: the Whisper entries" in text
    assert "- 2026.7.1: Fix Music Assistant media browsing ([#1001])" in text
    assert "Zigbee" not in text
    assert "ollama in a script" not in text
    assert "2026.6.0b1" not in text                                    # pre-releases are not upgrade targets
    # What could not be fetched is said, not invented.
    assert "Could not be fetched: 2026.7.0 (https://www.home-assistant.io/blog/2026/07/01/release-20267/)" in text
    # Others: generic keywords.
    assert "- 13.3.1: Removed the legacy alerting API" in text
    assert "- 1.110.0: deprecate the old callbacks API" in text or "- v1.110.0: deprecate the old callbacks API" in text
    assert "Not fetched (outside the release pages read): v1.95.0" in text
    # Lockfiles and the exclusion.
    assert "lock-orchestrator: patch releases: none; newer minor/major: fastapi 0.115.0 -> 0.163.2." in text
    assert "music-assistant: Music Assistant is excluded" in text
    # Order: HA first, one component per window.
    order = text.split("## Proposed order")[1]
    assert order.index("1. homeassistant") < order.index("grafana") < order.index("litellm")
    assert "Lookups:" in text and "www.home-assistant.io" in text


def test_the_major_run_writes_and_commits_the_report_then_notifies(house):
    ops, sd = house
    seen_at_notify = []
    orig = ops.notify

    def notify(tag, title, body):
        seen_at_notify.append((ops.repo / "docs/upgrades/2026-12-01.md").exists())
        return orig(tag, title, body)
    ops.notify = notify
    assert U.major_run(ops, rules(ONLY), sd) == 0
    assert seen_at_notify == [True]                       # on disk before the notice goes out
    assert git(ops.repo, "log", "-1", "--format=%s").strip() == "upgrade report: 2026-12-01 (major releases)"
    assert git(ops.repo, "show", "--stat", "--format=", "HEAD").split()[0] == "docs/upgrades/2026-12-01.md"
    (_, title, body), = ops.notices
    assert title == "Kronk: 3 major upgrade(s) need scheduling"
    assert "homeassistant 2026.5.4 -> 2026.7.1" in body and "Report: docs/upgrades/2026-12-01.md" in body
    assert "Nothing was upgraded" in body
    st = json.loads((sd / "status.json").read_text())
    assert st["major"]["report"] == "docs/upgrades/2026-12-01.md"


def test_the_major_run_upgrades_nothing(house):
    ops, sd = house
    U.major_run(ops, rules(ONLY), sd)
    assert not any(c[0] in ("deploy_tag", "compose", "run_tests", "replay") for c in ops.calls)
    assert not any(c[0] == "docker" and c[1] in ("pull", "stop", "tag", "run") for c in ops.calls)
    assert U.read_pin(ops.repo, rules().component("homeassistant")).value == "2026.5.4"


def test_off_the_expected_branch_the_report_is_written_outside_git_and_the_notice_says_where(house):
    ops, sd = house
    git(ops.repo, "checkout", "-q", "-b", "refactor")
    assert U.major_run(ops, rules(ONLY), sd) == 0
    assert not (ops.repo / "docs/upgrades/2026-12-01.md").exists()
    assert (sd / "reports" / "2026-12-01.md").exists()
    assert git(ops.repo, "log", "-1", "--format=%s").strip() == "initial"
    assert f"Report: {sd / 'reports' / '2026-12-01.md'}" in ops.notices[0][2]


def test_no_major_release_sends_nothing(house):
    ops, sd = house
    ops.web.table.clear()
    ops.web.table["https://ghcr.io/token"] = {"token": "anon"}
    U.major_run(ops, rules(["homeassistant"]), sd)
    assert ops.notices == []


def test_dry_run_prints_the_report_and_writes_nothing(house, capsys):
    ops, sd = house
    assert U.major_run(ops, rules(ONLY), sd, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "# Major-upgrade report, 2026-12-01" in out
    assert not sd.exists() and ops.notices == []
    assert git(ops.repo, "log", "-1", "--format=%s").strip() == "initial"


def test_out_writes_only_that_file(house, tmp_path):
    ops, sd = house
    out = tmp_path / "r.md"
    U.major_run(ops, rules(ONLY), sd, out=out)
    assert out.read_text().startswith("# Major-upgrade report")
    assert ops.notices == [] and not sd.exists()


def test_scan_notes_and_html_text():
    hits = U.scan_notes("- Ollama: new option\n- unrelated\n* Matter bridge fix", {"O": ["ollama"], "M": ["matter"]})
    assert hits == {"O": ["Ollama: new option"], "M": ["Matter bridge fix"]}
    assert U.html_text("<p>a &amp; b</p><script>x</script><li>c</li>") == "a & b\nc"
    # Words, not substrings; link definitions and skipped lines are not notes.
    assert U.scan_notes("Watts Vision gains a mode\nNew TTS voices", {"T": ["tts"]}) == {"T": ["New TTS voices"]}
    assert U.scan_notes("[ollama docs]: https://x/ollama\nchore(prices): 3 deprecated\nDeprecated the X API",
                        {"O": ["ollama", "deprecat"]}, r"^chore\(prices\)") == {"O": ["Deprecated the X API"]}
