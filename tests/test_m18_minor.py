"""Sprint M18, criteria 2-8 and 14: the monthly minor run
(scripts/upgrades.py minor) with every external step stubbed (FakeOps) and
git real in a temporary repository: the dry run, the order, one commit per
component, verify, rollback (including a rollback that fails), the
declines and their bounded retries. No sockets, no docker, no notifier."""
import json
from pathlib import Path

import pytest

from tests.m18_helpers import (NOW, FakeOps, U, base_files, gh_release, git, iso, make_repo, rules,
                               standard_web)

pytestmark = pytest.mark.usefixtures("no_network")

COMPS = ["tempo", "grafana", "searxng", "litellm", "homeassistant"]


@pytest.fixture
def house(tmp_path):
    repo = make_repo(tmp_path, base_files())
    ops = FakeOps(repo, standard_web())
    return ops, tmp_path / "state"


def run(ops, sd, dry=False, only=COMPS, ignore_month=False, **settings):
    return U.minor_run(ops, rules(only, **settings), sd, dry_run=dry, ignore_month=ignore_month)


def log_subjects(repo, n=20):
    return git(repo, "log", f"-{n}", "--format=%s").splitlines()[::-1]


def status(sd):
    return json.loads((sd / "status.json").read_text())


MUTATING_DOCKER = {"pull", "stop", "tag", "rm", "rmi", "start", "restart", "kill", "run", "create"}


def mutating_calls(ops):
    out = []
    for c in ops.calls:
        if c[0] == "docker" and c[1] in MUTATING_DOCKER:
            out.append(c)
        elif c[0] == "compose" and c[3] in ("up", "pull", "down", "stop", "restart", "rm"):
            out.append(c)
        elif c[0] == "deploy_tag" and c[1] != "status":
            out.append(c)
        elif c[0] in ("run_tests", "replay"):
            out.append(c)
        elif c[0] == "run" and c[1][0] == "git" and any(a in c[1] for a in ("commit", "tag", "push", "add", "revert")):
            out.append(c)
    return out


# ── dry run ──────────────────────────────────────────────────────────────

def test_dry_run_changes_nothing_and_says_what_it_would_do(house, capsys):
    ops, sd = house
    head = git(ops.repo, "rev-parse", "HEAD")
    assert run(ops, sd, dry=True) == 0
    out = capsys.readouterr().out
    assert git(ops.repo, "rev-parse", "HEAD") == head
    assert git(ops.repo, "tag").split() == ["sprint-X"]
    assert not sd.exists()                                   # no lock, no status, no log
    assert ops.notices == []
    assert mutating_calls(ops) == []
    assert "would do, in order" in out
    for frag in ("tempo: docker-compose.telemetry.yml: 3.1.1 -> 3.1.2", "grafana: docker-compose.telemetry.yml: 13.2.4 -> 13.2.6",
                 "searxng: docker-compose.yml: 2026.11.9-57ff753ad -> 2026.11.17-b99cf7496",
                 "litellm: docker-compose.yml: v1.88.1 -> v1.88.6"):
        assert frag in out
    assert "homeassistant" in out and "CURRENT" in out
    assert "a real run now would go ahead" in out
    assert "nothing written, nothing changed, nothing sent" in out


def test_dry_run_reports_what_would_decline(house, capsys):
    ops, sd = house
    ops.states[2]["state"] = "playing"
    run(ops, sd, dry=True)
    out = capsys.readouterr().out
    assert "would DECLINE: media_player.kitchen is playing" in out
    assert ops.notices == [] and mutating_calls(ops) == []


# ── nothing to do ────────────────────────────────────────────────────────

def test_nothing_to_do_sends_nothing(house):
    ops, sd = house
    assert run(ops, sd, only=["homeassistant", "nginx"]) == 0
    assert ops.notices == []
    st = status(sd)
    assert st["last_run"]["result"] == "nothing"
    assert st["months"]["2026-12"]["done"] is True
    assert log_subjects(ops.repo) == ["initial"]


# ── the happy path: order, commits, tags, one notice ─────────────────────

def test_components_go_one_at_a_time_in_order_each_its_own_commit(house):
    ops, sd = house
    assert run(ops, sd) == 0
    assert log_subjects(ops.repo) == [
        "initial",
        "upgrade: tempo 3.1.1 -> 3.1.2",
        "upgrade: grafana 13.2.4 -> 13.2.6",
        "upgrade: searxng 2026.11.9-57ff753ad -> 2026.11.17-b99cf7496",
        "upgrade: litellm v1.88.1 -> v1.88.6",
    ]
    author = git(ops.repo, "log", "-1", "--format=%an <%ae>")
    assert author.strip() == "Kronk upgrade_minor <upgrade_minor@kronk.invalid>"
    assert set(git(ops.repo, "tag").split()) == {"sprint-X", "upgrade-20261201-searxng", "upgrade-20261201-litellm"}
    deploys = [c for c in ops.calls if c[0] == "deploy_tag" and c[1] == "deploy"]
    assert deploys == [("deploy_tag", "deploy", "upgrade-20261201-searxng"), ("deploy_tag", "deploy", "upgrade-20261201-litellm")]
    assert ops.containers["kronk-telemetry-tempo-1"] == "grafana/tempo:3.1.2"
    assert ops.containers["kronk-litellm-1"] == "ghcr.io/berriai/litellm:v1.88.6"
    # One notice, every change named, from and to.
    (tag, title, body), = ops.notices
    assert tag == "kronk-upgrades" and title == "Kronk upgrade: 4 upgraded"
    for line in ("tempo 3.1.1 -> 3.1.2: upgraded", "grafana 13.2.4 -> 13.2.6: upgraded",
                 "litellm v1.88.1 -> v1.88.6: upgraded", f"Log: {sd}/runs/20261201T083000Z-minor.log"):
        assert line in body
    st = status(sd)
    assert st["last_run"]["result"] == "upgraded" and st["months"]["2026-12"]["done"] is True
    assert (sd / "runs").is_dir() and list((sd / "runs").glob("*-minor.log"))
    # Baseline corpus and replay ran before any change; each upgrade ran both again.
    kinds = [c[0] for c in ops.calls if c[0] in ("run_tests", "replay") and not (c[0] == "replay" and c[2])]
    assert kinds[:2] == ["run_tests", "replay"] and kinds.count("run_tests") == 5


def test_it_pushes_nothing_and_never_names_music_assistant_in_a_write(house):
    ops, sd = house
    run(ops, sd)
    gits = [c[1] for c in ops.calls if c[0] == "run" and c[1][0] == "git"]
    assert not any("push" in g for g in gits)
    for c in mutating_calls(ops):
        assert "music-assistant" not in json.dumps(c)
    assert ops.containers["music-assistant-server"].endswith("2.11.0b5-kronk.9")


# ── verify fails: roll back, continue ────────────────────────────────────

def test_a_failed_health_check_rolls_back_and_the_others_continue(house):
    ops, sd = house
    ops.bad_versions = {"13.2.6"}
    assert run(ops, sd) == 1
    subj = log_subjects(ops.repo)
    assert "upgrade: grafana 13.2.4 -> 13.2.6" in subj
    assert "upgrade: roll back grafana 13.2.6 -> 13.2.4" in subj
    assert subj[-1] == "upgrade: litellm v1.88.1 -> v1.88.6"              # later components still went
    assert ops.containers["kronk-telemetry-grafana-1"] == "grafana/grafana:13.2.4"
    assert U.read_pin(ops.repo, rules().component("grafana")).value == "13.2.4"
    (_, title, body), = ops.notices
    assert title == "Kronk upgrade: 3 upgraded, 1 rolled back"
    assert "grafana 13.2.4 -> 13.2.6: rolled back to 13.2.4 (http://localhost:3000/api/health: HTTP 500)" in body
    st = status(sd)
    assert st["last_run"]["result"] == "rolled_back"
    assert any(a["id"].startswith("rolled_back:") for a in st["attention"])


def test_a_replay_regression_rolls_back_a_kronk_stack_component_by_deploying_the_previous_tag(house):
    ops, sd = house
    ops.replay_regress = {"v1.88.6": ["row-a"]}
    assert run(ops, sd, only=["litellm"]) == 1
    deploys = [c[2] for c in ops.calls if c[0] == "deploy_tag" and c[1] == "deploy"]
    assert deploys == ["upgrade-20261201-litellm", "sprint-X"]
    assert ops.containers["kronk-litellm-1"] == "ghcr.io/berriai/litellm:v1.88.1"
    assert "row-a" in ops.notices[0][2]


def test_a_flapping_row_does_not_fail_an_upgrade(house):
    ops, sd = house
    ops.replay_regress = {"3.1.2": ["flap"]}           # inc-0930-search-deep-house-ytm fails
    assert run(ops, sd, only=["tempo"]) == 0
    assert ops.containers["kronk-telemetry-tempo-1"] == "grafana/tempo:3.1.2"


def test_the_offline_corpus_failing_rolls_back(house):
    ops, sd = house
    ops.tests_fail_versions = {"3.1.2"}
    assert run(ops, sd, only=["tempo"]) == 1
    assert ops.containers["kronk-telemetry-tempo-1"] == "grafana/tempo:3.1.1"
    assert "offline corpus failed" in ops.notices[0][2]


def test_a_replay_that_cannot_complete_after_the_change_rolls_back_unverified(house):
    ops, sd = house
    orig = ops.replay

    def replay(label, out, check_only=False):
        if label == "upgrade-tempo":
            return U.Result(3, "", "stopped: media_player state changed")
        return orig(label, out, check_only)
    ops.replay = replay
    assert run(ops, sd, only=["tempo"]) == 1
    assert ops.containers["kronk-telemetry-tempo-1"] == "grafana/tempo:3.1.1"
    assert "replay could not complete (exit 3" in ops.notices[0][2]


def test_a_failed_deploy_rolls_back(house):
    ops, sd = house
    ops.deploy_fail_tags = {"upgrade-20261201-searxng"}
    assert run(ops, sd, only=["searxng"]) == 1
    assert ops.containers["kronk-searxng-1"] == "searxng/searxng:2026.11.9-57ff753ad"
    assert log_subjects(ops.repo)[-1] == "upgrade: roll back searxng 2026.11.17-b99cf7496 -> 2026.11.9-57ff753ad"


# ── a rollback that fails ────────────────────────────────────────────────

def test_a_failed_rollback_stops_the_run_and_blocks_later_runs_until_ack(house, capsys):
    ops, sd = house
    ops.bad_versions = {"3.1.2"}
    ops.cannot_return = {"3.1.1"}
    assert run(ops, sd) == 2
    assert ops.containers["kronk-telemetry-grafana-1"] == "grafana/grafana:13.2.4"     # never attempted
    assert not any("grafana" in s for s in log_subjects(ops.repo))
    (_, title, body), = ops.notices
    assert title == "Kronk upgrade: ROLLBACK FAILED (tempo)"
    assert "needs you" in body and "upgrades.py ack" in body
    st = status(sd)
    assert st["rollback_failed"]["component"] == "tempo"
    assert any(a["id"] == "rollback_failed" for a in st["attention"])
    # The next run declines on it, even by hand.
    ops.notices.clear()
    ops.bad_versions, ops.cannot_return = set(), set()
    assert run(ops, sd, ignore_month=True) == 0
    assert status(sd)["last_run"]["result"] == "declined"
    assert "the last rollback failed" in status(sd)["last_run"]["summary"]
    # ack clears it (the operator fixed things by hand).
    capsys.readouterr()
    assert U.main(["ack"], ops=_ops_with_state(ops, sd)) == 0
    st = status(sd)
    assert st["rollback_failed"] is None and st["attention"] == []
    assert st["acknowledged"]["rollback_failed"]["component"] == "tempo"


def _ops_with_state(ops, sd):
    import os
    os.environ["KRONK_UPGRADE_STATE"] = str(sd)
    return ops


@pytest.fixture(autouse=True)
def _no_state_env(monkeypatch):
    monkeypatch.delenv("KRONK_UPGRADE_STATE", raising=False)
    yield


# ── declines ─────────────────────────────────────────────────────────────

def _satellite_busy(ops):
    ops.states[0]["state"] = "responding"


def _satellite_recent(ops):
    ops.states[0]["last_changed"] = iso(60 / 86400)


def _playing(ops):
    ops.states[2]["state"] = "playing"


def _held(ops):
    ops.held = 1


def _chat(ops):
    ops.nginx_log = '172.18.0.1 - - [01/Dec/2026:08:28:00 +0000] "POST /api/chat HTTP/1.1" 200 512 "-" "HomeAssistant"\n'


def _dev_voice(ops):
    ops.nginx_log = '172.18.0.1 - - [01/Dec/2026:08:28:00 +0000] "POST /dev/voice/api/chat HTTP/1.1" 200 512\n'


def _deploying(ops):
    ops.deploying = ["4242 bash scripts/deploy_tag.sh deploy sprint-M18"]


def _dirty(ops):
    (ops.repo / "scratch.txt").write_text("wip\n")


def _modified(ops):
    (ops.repo / "docs" / "readme.md").write_text("edited\n")


def _branch(ops):
    git(ops.repo, "checkout", "-q", "-b", "refactor")


def _ha_down(ops):
    ops.ha_up = False


@pytest.mark.parametrize("setup, reason", [
    (_satellite_busy, "assist_satellite.kitchen is responding"),
    (_satellite_recent, "assist_satellite.kitchen changed state 60s ago"),
    (_playing, "media_player.kitchen is playing"),
    (_held, "voice_tracer holds 1 run(s)"),
    (_chat, "1 chat/voice request(s) in the last 5 minutes"),
    (_dev_voice, "1 chat/voice request(s) in the last 5 minutes"),
    (_deploying, "a deploy is in progress"),
    (_dirty, "working tree not clean (1 path(s): scratch.txt)"),
    (_modified, "working tree not clean (1 path(s): docs/readme.md)"),
    (_branch, "on branch 'refactor', expected 'main'"),
    (_ha_down, "cannot read HA's states"),
])
def test_it_declines_when_the_house_is_busy_or_the_tree_is_not_ours(house, setup, reason):
    ops, sd = house
    setup(ops)
    head = git(ops.repo, "rev-parse", "HEAD")
    assert run(ops, sd) == 0
    st = status(sd)
    assert st["last_run"]["result"] == "declined"
    assert reason in st["last_run"]["summary"]
    assert "attempt 1 of 7" in st["last_run"]["summary"]
    assert git(ops.repo, "rev-parse", "HEAD") == head
    assert ops.notices == []
    assert mutating_calls(ops) == []


def test_it_declines_while_another_run_holds_the_lock(house):
    ops, sd = house
    sd.mkdir()
    fd = U.take_lock(sd)
    try:
        assert run(ops, sd) == 0
        assert not (sd / "status.json").exists() and mutating_calls(ops) == []
        assert U.lock_held(sd)
    finally:
        import os
        os.close(fd)
    assert not U.lock_held(sd)


def test_the_expected_branch_is_the_setting(house):
    ops, sd = house
    _branch(ops)
    assert run(ops, sd, only=["tempo"], expected_branch="refactor") == 0
    assert status(sd)["last_run"]["result"] == "upgraded"


def test_declines_retry_a_bounded_number_of_times_then_say_so(house):
    ops, sd = house
    _playing(ops)
    for i in range(1, 8):
        assert run(ops, sd) == 0
        assert status(sd)["months"]["2026-12"]["attempts"] == i
    st = status(sd)
    assert st["months"]["2026-12"]["done"] is True
    (_, title, body), = ops.notices
    assert title == "Kronk upgrade: this month's patch run never found a quiet moment"
    assert "Declined 7 times" in body and "media_player.kitchen is playing" in body
    assert any(a["id"] == "declined:2026-12" for a in st["attention"])
    calls = len(ops.calls)
    assert run(ops, sd) == 0                                   # an 8th firing does nothing at all
    assert len(ops.calls) == calls


def test_a_done_month_is_not_run_again_unless_asked(house):
    ops, sd = house
    run(ops, sd, only=["tempo"])
    n = len(ops.notices)
    assert run(ops, sd, only=["tempo"]) == 0 and len(ops.notices) == n
    assert status(sd)["months"]["2026-12"]["attempts"] == 1


def test_the_baseline_must_pass_before_anything_changes(house):
    ops, sd = house
    ops.tests_fail = True
    assert run(ops, sd) == 0
    st = status(sd)
    assert st["last_run"]["result"] == "declined" and "offline corpus fails before any change" in st["last_run"]["summary"]
    assert log_subjects(ops.repo) == ["initial"]


def test_a_replay_that_cannot_run_declines_the_run(house):
    ops, sd = house
    ops.replay_rc = 2
    assert run(ops, sd) == 0
    assert "live replay cannot run" in status(sd)["last_run"]["summary"]
    assert log_subjects(ops.repo) == ["initial"]


# ── Kronk-stack and compose preconditions, per component ─────────────────

def test_undeployed_work_on_the_branch_declines_the_kronk_stack_components_only(house):
    ops, sd = house
    (ops.repo / "orchestrator").mkdir()
    (ops.repo / "orchestrator" / "main.py").write_text("# new\n")
    git(ops.repo, "add", "-A")
    git(ops.repo, "commit", "-q", "-m", "work not yet deployed")
    assert run(ops, sd, only=["tempo", "litellm"]) == 0
    subj = log_subjects(ops.repo)
    assert subj[-1] == "upgrade: tempo 3.1.1 -> 3.1.2"
    assert not any("litellm" in s for s in subj)
    body = ops.notices[0][2]
    assert "litellm v1.88.1 -> v1.88.6: not started (HEAD has changes not deployed to production (sprint-X): orchestrator/main.py" in body


def test_docs_only_changes_after_the_production_tag_do_not_block(house):
    ops, sd = house
    (ops.repo / "docs" / "note.md").write_text("more\n")
    (ops.repo / "docker-compose.ha.yml").write_text((ops.repo / "docker-compose.ha.yml").read_text() + "# a comment\n")
    git(ops.repo, "add", "-A")
    git(ops.repo, "commit", "-qm", "docs")
    assert run(ops, sd, only=["litellm"]) == 0
    assert log_subjects(ops.repo)[-1] == "upgrade: litellm v1.88.1 -> v1.88.6"


def test_production_not_on_a_tag_declines_the_kronk_stack_components(house):
    ops, sd = house
    orig = ops.deploy_tag

    def deploy_tag(*args):
        if args[0] == "status":
            return U.Result(0, "CONTAINER SERVICE RUNNING_FROM CREATED\nkronk-orchestrator-1 orchestrator working tree 1 hour ago\n")
        return orig(*args)
    ops.deploy_tag = deploy_tag
    run(ops, sd, only=["litellm"])
    o, = status(sd)["last_run"]["outcomes"]
    assert (o["result"], o["detail"]) == ("not_started", "kronk-orchestrator-1 runs from the working tree, not a tag")
    assert not any("litellm" in s for s in log_subjects(ops.repo))
    assert ops.notices == []


def test_a_compose_file_that_does_not_describe_what_runs_declines_that_component(house):
    ops, sd = house
    ops.hashes_match = False
    run(ops, sd, only=["tempo"])
    assert log_subjects(ops.repo) == ["initial"]
    st = status(sd)
    assert st["last_run"]["result"] == "not_started"
    assert st["months"]["2026-12"]["done"] is False                # tomorrow's firing tries again
    assert ops.notices == []                                     # nothing changed or tried


# ── Home Assistant: M12's runbook in code ────────────────────────────────

@pytest.fixture
def ha_house(house):
    ops, sd = house
    ops.web.table["https://ghcr.io/v2/home-assistant/home-assistant/tags/list"] = {"tags": ["2026.5.4", "2026.5.5", "2026.6.0"]}
    ops.web.table["https://api.github.com/repos/home-assistant/core/releases?"] = [
        gh_release("2026.6.0", 30), gh_release("2026.5.5", 20), gh_release("2026.5.4", 190)]
    return ops, sd


def test_ha_patch_upgrade_follows_the_restart_runbook(ha_house):
    ops, sd = ha_house
    old_id = ops.images["ghcr.io/home-assistant/home-assistant:2026.5.4"]
    assert run(ops, sd, only=["homeassistant"]) == 0
    seq = [c for c in ops.calls if (c[0] == "docker" and c[1] in ("tag", "pull", "stop")) or (c[0] == "compose" and c[3] == "up")]
    assert seq == [
        ("docker", "tag", old_id, "kronk/ha-rollback:2026.5.4"),
        ("docker", "pull", "ghcr.io/home-assistant/home-assistant:2026.5.5"),
        ("docker", "stop", "-t", "120", "homeassistant"),
        ("compose", "kronk-ha", "docker-compose.ha.yml", "up", "-d", "homeassistant"),
    ]
    assert ops.containers["homeassistant"] == "ghcr.io/home-assistant/home-assistant:2026.5.5"
    assert ("replay", "upgrade-ha-check", True) in ops.calls                # H8's voice_replay --check-only
    assert ops.images["kronk/ha-rollback:2026.5.4"] == old_id                # kept for rollback
    assert log_subjects(ops.repo)[-1] == "upgrade: homeassistant 2026.5.4 -> 2026.5.5"
    assert ops.notices[0][1] == "Kronk upgrade: 1 upgraded"


def test_ha_failure_rolls_back_to_the_previous_image_by_id(ha_house):
    ops, sd = ha_house
    old_id = ops.images["ghcr.io/home-assistant/home-assistant:2026.5.4"]
    ops.bad_versions = {"2026.5.5"}
    # Something moved the old tag meanwhile: the rollback must re-tag the kept id.
    orig = ops.docker

    def docker(*args, timeout=600):
        r = orig(*args, timeout=timeout)
        if args[:2] == ("pull", "ghcr.io/home-assistant/home-assistant:2026.5.5"):
            ops.images["ghcr.io/home-assistant/home-assistant:2026.5.4"] = "sha256:" + "f" * 64
        return r
    ops.docker = docker
    assert run(ops, sd, only=["homeassistant"]) == 1
    assert ("docker", "tag", old_id, "ghcr.io/home-assistant/home-assistant:2026.5.4") in ops.calls
    stops = [c for c in ops.calls if c[:2] == ("docker", "stop")]
    assert stops == [("docker", "stop", "-t", "120", "homeassistant")] * 2
    assert ops.containers["homeassistant"] == "ghcr.io/home-assistant/home-assistant:2026.5.4"
    assert log_subjects(ops.repo)[-1] == "upgrade: roll back homeassistant 2026.5.5 -> 2026.5.4"


def test_ha_is_not_restarted_when_a_select_changed_recently(ha_house):
    ops, sd = ha_house
    ops.states[3]["last_changed"] = iso(5 / 1440)
    run(ops, sd, only=["homeassistant"])
    assert not any(c[:2] == ("docker", "stop") for c in ops.calls)
    assert "select.kitchen_assistant_2 changed 5 min ago (under 15)" in status(sd)["last_run"]["outcomes"][0]["detail"]


def test_ha_diff_names_what_did_not_come_back():
    before = {"entities": {"assist_satellite.kitchen": {"state": "idle"}, "select.k_wake_word_2": {"state": "Hey Jarvis"},
                           "media_player.office": {"state": "paused"}},
              "rest_command": ["kronk_music_voice"], "ma_started": "t1"}
    after = {"entities": {"assist_satellite.kitchen": {"state": "unavailable"}, "select.k_wake_word_2": {"state": "Okay Nabu"}},
             "rest_command": [], "ma_started": "t2"}
    d = U._ha_diff(before, after)
    assert d == ["assist_satellite.kitchen unavailable", "select.k_wake_word_2: Hey Jarvis before, Okay Nabu after",
                 "media_player.office missing after the restart", "rest_command services changed: ['kronk_music_voice'] -> []",
                 "Music Assistant restarted (its start time changed)"]


# ── replay comparison ────────────────────────────────────────────────────

def test_replay_regressions_count_only_rows_that_passed_before():
    base = [{"id": "a", "status": "pass"}, {"id": "b", "status": "fail"}, {"id": "c", "status": "xpass"},
            {"id": "d", "status": "pass"}, {"id": "e", "status": "xfail"}]
    now = [{"id": "a", "status": "fail"}, {"id": "b", "status": "fail"}, {"id": "c", "status": "fail"},
           {"id": "d", "status": "fail"}, {"id": "e", "status": "fail"}, {"id": "new", "status": "fail"}]
    assert U.replay_regressions(base, now, ["d"]) == ["a", "c"]


def test_the_flapper_list_is_data():
    assert "eval-shuffle-it" in U.load_rules(env={}).replay["flappers"]


def test_paths_are_shown_repo_relative_when_inside_the_repo(tmp_path):
    assert U.shown_path(tmp_path / "data" / "upgrades" / "runs" / "x.log", tmp_path) == "data/upgrades/runs/x.log"
    assert U.shown_path(Path("/elsewhere/x.log"), tmp_path) == "/elsewhere/x.log"
