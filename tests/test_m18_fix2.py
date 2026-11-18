"""Sprint M18 fix 2: the coordinator's rulings G1 to G5 on the second
end-to-end report (docs/sprint/M18-e2e2.md), against the fake house of
tests/m18_helpers.py. No sockets, no docker, no notifier; git is real in a
temporary repository with its own user."""
import datetime as _dt
import json

import pytest

from tests.m18_helpers import (NOW, ROOT, FakeOps, U, base_files, gh_release, git, make_repo, rules,
                               standard_web)

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture(autouse=True)
def _no_state_env(monkeypatch):
    monkeypatch.delenv("KRONK_UPGRADE_STATE", raising=False)


def ha_patch_available(ops):
    ops.web.table["https://ghcr.io/v2/home-assistant/home-assistant/tags/list"] = {"tags": ["2026.5.4", "2026.5.5", "2026.6.0"]}
    ops.web.table["https://api.github.com/repos/home-assistant/core/releases?"] = [
        gh_release("2026.6.0", 30), gh_release("2026.5.5", 20), gh_release("2026.5.4", 190)]


@pytest.fixture
def house(tmp_path):
    repo = make_repo(tmp_path, base_files())
    ops = FakeOps(repo, standard_web())
    ha_patch_available(ops)
    return ops, tmp_path / "state"


def run(ops, sd, only, **kw):
    return U.minor_run(ops, rules(only), sd, **kw)


def status(sd):
    return json.loads((sd / "status.json").read_text())


def subjects(repo):
    return git(repo, "log", "--format=%s").splitlines()[::-1]


def mutating(ops):
    """Calls that change what runs or git's refs."""
    out = []
    for c in ops.calls:
        if c[0] == "deploy_tag" and c[1] == "deploy":
            out.append(c)
        elif c[0] == "docker" and c[1] in ("stop", "start", "tag", "pull", "rm"):
            out.append(c)
        elif c[0] == "compose" and c[3] in ("up", "pull"):
            out.append(c)
    return out


# ── G4 (N2): what runs is one of three answers, never more than is known ──

def _inspect_fails(ops, container, err="error during connect: context deadline exceeded"):
    orig = ops.docker

    def docker(*args, **kw):
        if args[0] == "inspect" and args[1] == container:
            ops.calls.append(("docker",) + args)
            return U.Result(1, "", err)
        return orig(*args, **kw)
    ops.docker = docker


@pytest.mark.parametrize("deploy, container", [("ha", "homeassistant"), ("kronk", "kronk-litellm-1")])
def test_what_runs_has_three_answers_and_says_does_not_exist_only_when_docker_does(house, deploy, container):
    ops, _ = house
    comp = rules().component({"ha": "homeassistant", "kronk": "litellm"}[deploy])
    ver, phrase = U.what_runs(ops, comp)
    assert ver == {"ha": "2026.5.4", "kronk": "v1.88.1"}[deploy] and phrase == ver
    ops.stopped.add(container)
    assert U.what_runs(ops, comp)[1].startswith(f"nothing ({container} is stopped")
    ops.stopped.clear()
    _inspect_fails(ops, container)
    ver, phrase = U.what_runs(ops, comp)
    assert ver is None
    assert phrase == f"unknown (cannot inspect {container}: error during connect: context deadline exceeded)"
    assert "does not exist" not in phrase and "Home Assistant is down" not in phrase
    ops.docker = type(ops).docker.__get__(ops)
    del ops.containers[container]
    assert U.what_runs(ops, comp)[1] == f"nothing ({container} does not exist" + (
        "; Home Assistant is down)" if deploy == "ha" else ")")


def test_a_failed_inspect_never_reads_as_home_assistant_down(house):
    """The tester's N2: docker inspect fails from HA's bring-up on while HA
    runs 2026.5.4 throughout."""
    ops, sd = house
    orig = ops.docker
    failing = []

    def docker(*args, **kw):
        if args[:4] == ("inspect", "homeassistant", "-f", "{{.Image}}"):
            failing.append(True)
        if failing and args[0] == "inspect" and args[1] == "homeassistant":
            ops.calls.append(("docker",) + args)
            return U.Result(1, "", "injected failure")
        return orig(*args, **kw)
    ops.docker = docker
    assert run(ops, sd, ["homeassistant"]) == 1
    (_, title, body), = ops.notices
    assert title == "Kronk upgrade: 1 not applied"
    assert "running is unknown (cannot inspect homeassistant: injected failure)" in body
    assert "does not exist" not in body and "Home Assistant is down" not in body
    assert "homeassistant" not in ops.stopped


def test_a_pin_whose_container_cannot_be_inspected_is_skipped_not_a_mismatch(house):
    ops, sd = house
    _inspect_fails(ops, "kronk-litellm-1")
    run(ops, sd, ["litellm"])
    log = next((sd / "runs").glob("*-minor.log")).read_text()
    assert "SKIP" in log and "cannot tell what kronk-litellm-1 runs" in log
    assert not any("differ" in t for _, t, _ in ops.notices)


# ── G1 (N1): a recovery acts by itself only on the world the record describes ──

from tests.m18_helpers import Killed, kill_before, kill_on  # noqa: E402


def killed(ops, sd, only, undo):
    with pytest.raises(Killed):
        run(ops, sd, only)
    undo()
    ops.calls.clear()
    ops.clock = NOW + _dt.timedelta(days=1)


def deploys(ops):
    return [c for c in ops.calls if c[0] == "deploy_tag" and c[1] == "deploy"]


def person_deploys(ops, tag, at="HEAD"):
    git(ops.repo, "tag", tag, at)
    ops.deploy_tag("deploy", tag)
    ops.calls.clear()


def _kill_in_verify(ops):
    return kill_before(ops, "run_tests", lambda a: ops.containers["kronk-litellm-1"].endswith("v1.88.6"))


def _kill_before_deploy(ops):
    return kill_before(ops, "deploy_tag", lambda a: a[0] == "deploy")


@pytest.mark.parametrize("kill", ["verify", "deploy"])
@pytest.mark.parametrize("at", ["HEAD", "HEAD~1"])
def test_a_tag_a_person_deployed_after_the_kill_is_never_deployed_over(house, kill, at):
    """The tester's N1, four variants: sprint-Y at HEAD (the pin commit) or
    before it, after a kill in verify or before the deploy."""
    ops, sd = house
    killed(ops, sd, ["litellm"], (_kill_in_verify if kill == "verify" else _kill_before_deploy)(ops))
    person_deploys(ops, "sprint-Y", at)
    head = git(ops.repo, "rev-parse", "HEAD")
    assert run(ops, sd, ["litellm"]) == 2
    assert deploys(ops) == [] and mutating(ops) == [] and ops.prod_tag == "sprint-Y"
    assert git(ops.repo, "rev-parse", "HEAD") == head
    st = status(sd)
    o, = st["last_run"]["outcomes"]
    assert o["result"] == "out_of_step"
    assert "production runs tag sprint-Y" in o["detail"] and "sprint-X" in o["detail"]
    assert "upgrade-20261201T083000Z-litellm" in o["detail"] or kill == "deploy"
    assert "nothing was touched" in o["detail"]
    assert st["out_of_step"]["component"] == "litellm" and "intent" not in st
    (_, title, body), = ops.notices
    assert title == "Kronk upgrade: OUT OF STEP (litellm)" and "sprint-Y" in body


def test_the_runs_own_tag_in_production_still_finishes_the_rollback(house):
    ops, sd = house
    killed(ops, sd, ["litellm"], _kill_in_verify(ops))
    assert run(ops, sd, ["litellm"]) == 1
    assert deploys(ops) == [("deploy_tag", "deploy", "sprint-X")]


def test_the_previous_tag_running_puts_the_pin_back_and_restarts_nothing(house):
    ops, sd = house
    killed(ops, sd, ["litellm"], _kill_before_deploy(ops))
    assert run(ops, sd, ["litellm"]) == 1
    assert mutating(ops) == []
    assert "pin put back to v1.88.1" in status(sd)["last_run"]["outcomes"][0]["state"]


def test_a_previous_tag_that_no_longer_exists_is_out_of_step(house):
    ops, sd = house
    killed(ops, sd, ["litellm"], _kill_in_verify(ops))
    git(ops.repo, "tag", "-d", "sprint-X")
    assert run(ops, sd, ["litellm"]) == 2
    assert mutating(ops) == []
    assert "the recorded previous tag sprint-X does not exist" in status(sd)["last_run"]["outcomes"][0]["detail"]


def test_a_commit_the_record_does_not_explain_is_out_of_step(house):
    ops, sd = house
    killed(ops, sd, ["litellm"], _kill_in_verify(ops))
    (ops.repo / "docs" / "note.md").write_text("a person's work\n")
    git(ops.repo, "add", "docs/note.md")
    git(ops.repo, "commit", "-qm", "docs: a note")
    assert run(ops, sd, ["litellm"]) == 2
    assert mutating(ops) == []
    d = status(sd)["last_run"]["outcomes"][0]["detail"]
    assert "1 commit(s) since the run started that the record does not explain" in d and "docs: a note" in d


def test_a_component_running_neither_version_while_production_runs_the_previous_tag_is_out_of_step(house):
    """A deploy cut short: litellm recreated, the orchestrator not yet."""
    ops, sd = house
    killed(ops, sd, ["litellm"], _kill_before_deploy(ops))
    ops.containers["kronk-litellm-1"] = "ghcr.io/berriai/litellm:v1.88.6"
    assert run(ops, sd, ["litellm"]) == 2 and mutating(ops) == []


def _kill_grafana_in_verify(ops):
    return kill_before(ops, "run_tests", lambda a: ops.containers["kronk-telemetry-grafana-1"].endswith("13.2.6"))


def test_a_telemetry_recovery_passes_the_config_hash_guard_or_touches_nothing(house):
    ops, sd = house
    killed(ops, sd, ["grafana"], _kill_grafana_in_verify(ops))
    ops.config_extra["grafana"] = "GF_SERVER_ROOT_URL changed in .env.telemetry"
    assert run(ops, sd, ["grafana"]) == 2
    assert mutating(ops) == []
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "out_of_step" and "config hash" in o["detail"]
    assert ops.containers["kronk-telemetry-grafana-1"].endswith(":13.2.6")


def test_a_telemetry_recovery_passes_the_bind_mount_guard_or_touches_nothing(house):
    ops, sd = house
    killed(ops, sd, ["grafana"], _kill_grafana_in_verify(ops))
    ops.mtimes[str(ops.repo / "telemetry/grafana/provisioning/datasources/tempo.yaml")] = ops.clock
    assert run(ops, sd, ["grafana"]) == 2
    assert mutating(ops) == []
    assert "a recreate would load it unreleased" in status(sd)["last_run"]["outcomes"][0]["detail"]


def test_a_telemetry_container_recreated_by_a_person_is_not_rolled_back(house):
    ops, sd = house
    killed(ops, sd, ["grafana"], _kill_grafana_in_verify(ops))
    ops.labels["kronk-telemetry-grafana-1"] = "a-hand-made-container"
    assert run(ops, sd, ["grafana"]) == 2 and mutating(ops) == []
    assert "not the container the run started" in status(sd)["last_run"]["outcomes"][0]["detail"]


def test_a_clean_telemetry_recovery_still_rolls_back(house):
    ops, sd = house
    killed(ops, sd, ["grafana"], _kill_grafana_in_verify(ops))
    assert run(ops, sd, ["grafana"]) == 1
    assert ops.containers["kronk-telemetry-grafana-1"].endswith(":13.2.4")
    assert status(sd)["last_run"]["outcomes"][0]["state"] == \
        "recovered: finished the rollback: 13.2.4 runs again, git has 13.2.4"


def test_a_stopped_telemetry_container_is_out_of_step_not_started(house):
    ops, sd = house
    killed(ops, sd, ["grafana"], _kill_grafana_in_verify(ops))
    ops.stopped.add("kronk-telemetry-grafana-1")
    assert run(ops, sd, ["grafana"]) == 2 and mutating(ops) == []


# ── G2 (K1, K2): Home Assistant down is dealt with first, by the smallest action ──

def _kill_after_ha_stop(ops):
    return kill_on(ops, "docker", lambda a: a[0] == "stop")


def ha_calls(ops, *what):
    return [c for c in ops.calls if c[0] == "docker" and c[1] in what]


def test_a_stopped_ha_is_started_first_even_with_a_dirty_tree(house):
    """The tester's K1: killed between the stop and compose up, then an
    untracked file in the tree. HA comes back (the same container), git and
    the pin are not touched, and the rest is OUT OF STEP with HA running."""
    ops, sd = house
    killed(ops, sd, ["homeassistant"], _kill_after_ha_stop(ops))
    assert "homeassistant" in ops.stopped
    (ops.repo / "notes.txt").write_text("a person's notes\n")
    head = git(ops.repo, "rev-parse", "HEAD")
    assert run(ops, sd, ["homeassistant"]) == 2
    assert "homeassistant" not in ops.stopped
    assert ha_calls(ops, "start") == [("docker", "start", "homeassistant")]
    assert ha_calls(ops, "stop") == [] and not any(c[0] == "compose" and c[3] == "up" for c in ops.calls)
    assert git(ops.repo, "rev-parse", "HEAD") == head
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "out_of_step"
    assert o["state"].startswith("Home Assistant was down (homeassistant was stopped); the same container was "
                                 "started again; OUT OF STEP")
    (_, title, _), = ops.notices
    assert title == "Kronk upgrade: OUT OF STEP (homeassistant)"


def test_a_stopped_ha_is_started_then_the_pin_is_put_back_by_a_commit(house):
    ops, sd = house
    killed(ops, sd, ["homeassistant"], _kill_after_ha_stop(ops))
    ops.web.table["https://ghcr.io/v2/home-assistant/home-assistant/tags/list"] = {"tags": ["2026.5.4"]}
    assert run(ops, sd, ["homeassistant"]) == 1
    assert ha_calls(ops, "start") == [("docker", "start", "homeassistant")] and ha_calls(ops, "stop") == []
    assert ops.containers["homeassistant"].endswith(":2026.5.4") and "homeassistant" not in ops.stopped
    assert subjects(ops.repo)[-1] == "upgrade: roll back homeassistant 2026.5.5 -> 2026.5.4"
    o, = status(sd)["last_run"]["outcomes"]
    assert o["state"].startswith("Home Assistant was down (homeassistant was stopped); the same container was "
                                 "started again; recovered: git had 2026.5.5 while 2026.5.4 ran")
    assert ops.notices[-1][1] == "Kronk upgrade: an interrupted run was recovered (homeassistant): Home Assistant was down"


def test_ha_is_started_even_at_seven_in_the_evening_with_the_house_busy(house):
    ops, sd = house
    killed(ops, sd, ["homeassistant"], _kill_after_ha_stop(ops))
    ops.clock = NOW.replace(hour=0) + _dt.timedelta(days=1)            # 19:00 local
    ops.states[2]["state"] = "playing"
    run(ops, sd, ["homeassistant"])
    assert "homeassistant" not in ops.stopped


def _ha_gone_after_stop(ops):
    """compose up cut between removing the old container and creating the new one."""
    def gone(a):
        if a[0] == "stop":
            del ops.containers["homeassistant"]
            ops.stopped.discard("homeassistant")
            return True
        return False
    return kill_on(ops, "docker", gone)


def test_a_missing_ha_container_is_created_on_the_rollback_image_without_a_stop(house):
    ops, sd = house
    killed(ops, sd, ["homeassistant"], _ha_gone_after_stop(ops))
    ops.containers.pop("homeassistant", None)
    assert run(ops, sd, ["homeassistant"]) == 1
    assert ha_calls(ops, "stop") == []
    assert ("compose", "kronk-ha", "docker-compose.ha.yml", "config", "--format", "json") in ops.calls   # H7b
    assert ops.containers["homeassistant"] == "ghcr.io/home-assistant/home-assistant:2026.5.4"
    assert U.read_pin(ops.repo, rules().component("homeassistant")).value == "2026.5.4"
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "recovered" and o["state"].startswith("Home Assistant was down (no homeassistant container)")


def test_a_missing_ha_container_is_not_created_when_h7b_fails(house):
    ops, sd = house
    killed(ops, sd, ["homeassistant"], _ha_gone_after_stop(ops))
    ops.containers.pop("homeassistant", None)
    ops.existing_paths.discard("/home/drew/kronk-voice-recordings")
    assert run(ops, sd, ["homeassistant"]) == 2
    assert not any(c[0] == "compose" and c[3] == "up" for c in ops.calls) and ha_calls(ops, "stop") == []
    st = status(sd)
    assert st["rollback_failed"]["component"] == "homeassistant"
    assert ops.notices[-1][1].endswith(": Home Assistant is down")


def test_a_rollback_never_stops_a_container_that_does_not_exist(house):
    ops, sd = house
    orig = ops.compose

    def compose(project, file, *args, **kw):
        if args[0] == "up" and project == "kronk-ha" and "2026.5.5" in (ops.repo / file).read_text():
            ops.containers.pop("homeassistant", None)
            ops.calls.append(("compose", project, file) + args)
            return U.Result(1, "", "Error response from daemon: conflict")
        return orig(project, file, *args, **kw)
    ops.compose = compose
    assert run(ops, sd, ["homeassistant"]) == 1
    assert ha_calls(ops, "stop") == [("docker", "stop", "-t", "120", "homeassistant")]   # the upgrade's only
    assert ops.containers["homeassistant"].endswith(":2026.5.4")
    assert status(sd)["last_run"]["outcomes"][0]["result"] == "rolled_back"


def test_the_dry_run_says_it_would_start_ha_and_changes_nothing(house, capsys):
    ops, sd = house
    killed(ops, sd, ["homeassistant"], _kill_after_ha_stop(ops))
    before = (sd / "status.json").read_text()
    capsys.readouterr()
    run(ops, sd, ["homeassistant"], dry_run=True)
    assert "a real run would start the stopped homeassistant container (docker start" in capsys.readouterr().out
    assert (sd / "status.json").read_text() == before and "homeassistant" in ops.stopped


def notices_cmd(ops, sd, monkeypatch):
    monkeypatch.setenv("KRONK_UPGRADE_STATE", str(sd))
    return U.main(["notices"], ops=ops)


def test_the_hourly_unit_starts_a_stopped_ha_and_says_so_within_the_hour(house, monkeypatch):
    """K2: killed on the 7th at 03:30 local; the next monthly firing is the
    1st of next month. The hourly unit brings HA back and raises the
    warning and the notice; it changes nothing else."""
    ops, sd = house
    ops.clock = NOW + _dt.timedelta(days=6)
    _kill_after_ha_stop(ops)
    with pytest.raises(Killed):
        run(ops, sd, ["homeassistant"])
    ops.docker = type(ops).docker.__get__(ops)
    ops.calls.clear()
    head = git(ops.repo, "rev-parse", "HEAD")
    ops.clock += _dt.timedelta(minutes=50)
    notices_cmd(ops, sd, monkeypatch)
    assert "homeassistant" not in ops.stopped
    assert [c for c in mutating(ops)] == [("docker", "start", "homeassistant")]
    assert git(ops.repo, "rev-parse", "HEAD") == head
    st = status(sd)
    assert st["intent"]["component"] == "homeassistant" and st["intent"]["noticed"]
    assert any(a["id"] == "interrupted" for a in st["attention"])
    (_, title, body), = ops.notices
    assert title == "Kronk upgrade: a run was interrupted (homeassistant): Home Assistant was down; started again"
    assert "step 'ha-stop'" in body
    ops.calls.clear()
    ops.clock += _dt.timedelta(hours=1)
    notices_cmd(ops, sd, monkeypatch)
    assert mutating(ops) == [] and len(ops.notices) == 1                # once


def test_the_hourly_unit_only_warns_about_a_record_of_another_component(house, monkeypatch):
    ops, sd = house
    killed(ops, sd, ["litellm"], _kill_in_verify(ops))
    notices_cmd(ops, sd, monkeypatch)
    assert mutating(ops) == []
    assert ops.notices[-1][1] == "Kronk upgrade: a run was interrupted (litellm)"
    assert any(a["id"] == "interrupted" for a in status(sd)["attention"])


def test_the_hourly_unit_does_nothing_while_a_run_holds_the_lock(house, monkeypatch):
    ops, sd = house
    killed(ops, sd, ["homeassistant"], _kill_after_ha_stop(ops))
    fd = U.take_lock(sd, "pid=1\nrun=upgrade_minor x\n")
    try:
        notices_cmd(ops, sd, monkeypatch)
    finally:
        import os
        os.close(fd)
    assert mutating(ops) == [] and ops.notices == [] and "homeassistant" in ops.stopped


def test_the_api_shows_an_unfinished_record_as_a_warning(tmp_path):
    import upgrades_api
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    (tmp_path / "s.json").write_text(json.dumps({"intent": {
        "component": "homeassistant", "from": "2026.5.4", "to": "2026.5.5", "step": "ha-stop",
        "at": "2026-12-07T08:31:00+00:00"}}))
    upgrades_api.STATUS_FILE = str(tmp_path / "s.json")
    app = FastAPI()
    app.include_router(upgrades_api.router)
    r = TestClient(app).get("/api/upgrades").json()
    assert r["warnings"][0].startswith("An upgrade run was interrupted inside homeassistant 2026.5.4 -> 2026.5.5 "
                                       "(step ha-stop, 2026-12-07T08:31:00+00:00)")
    assert r["ack"] and r["intent"]["component"] == "homeassistant"


# ── K3: outcomes a killed run never reported are reported by the next ────

def test_upgrades_kept_before_a_kill_are_reported_by_the_next_run(house):
    ops, sd = house
    killed(ops, sd, ["tempo", "grafana", "litellm"], _kill_in_verify(ops))
    assert [o["name"] for o in status(sd)["unreported"]["outcomes"]] == ["tempo", "grafana"]
    run(ops, sd, ["tempo", "grafana", "litellm"])
    late = [n for n in ops.notices if "ended before its notice" in n[1]]
    (_, title, body), = late
    assert title == "Kronk upgrade: 2 upgraded (run 20261201T083000Z, which ended before its notice)"
    assert "tempo 3.1.1 -> 3.1.2: upgraded" in body and "grafana 13.2.4 -> 13.2.6: upgraded" in body
    assert "unreported" not in status(sd)
    assert any("interrupted run was recovered (litellm)" in t for _, t, _ in ops.notices)


def test_a_kill_right_before_the_notice_is_reported_the_next_night(house):
    ops, sd = house
    killed(ops, sd, ["tempo"], kill_before(ops, "notify", lambda a: True))
    assert ops.notices == []
    assert run(ops, sd, ["tempo"]) == 0
    (_, title, body), = ops.notices
    assert title == "Kronk upgrade: 1 upgraded (run 20261201T083000Z, which ended before its notice)"
    assert body.startswith("tempo 3.1.1 -> 3.1.2: upgraded")
    ops.clock += _dt.timedelta(days=1)
    run(ops, sd, ["tempo"])
    assert len(ops.notices) == 1                                           # once


def test_a_run_that_sends_its_notice_leaves_nothing_unreported(house):
    ops, sd = house
    run(ops, sd, ["tempo"])
    assert "unreported" not in status(sd) and len(ops.notices) == 1


# ── K4: players that change on their own while HA restarts ───────────────

def _player_after_up(ops, eid, state):
    """From HA's first `compose up` on, `eid` has `state` (it stays so through a rollback)."""
    orig = ops.compose

    def compose(project, file, *args, **kw):
        r = orig(project, file, *args, **kw)
        if args[0] == "up" and project == "kronk-ha":
            if not any(s["entity_id"] == eid for s in ops.states):
                ops.states.append({"entity_id": eid, "state": "idle", "last_changed": "2026-11-01T00:00:00Z"})
            next(s for s in ops.states if s["entity_id"] == eid)["state"] = state
        return r
    ops.compose = compose


def _add_player(ops, eid, state="idle"):
    ops.states.append({"entity_id": eid, "state": state, "last_changed": "2026-11-01T00:00:00Z"})


def test_a_player_that_goes_unavailable_fails_the_upgrade_but_not_its_rollback(house):
    ops, sd = house
    _player_after_up(ops, "media_player.kitchen", "unavailable")
    assert run(ops, sd, ["homeassistant"]) == 1
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "rolled_back" and ops.containers["homeassistant"].endswith(":2026.5.4")
    assert "media_player.kitchen: idle before, unavailable after" in o["detail"]
    assert "after the rollback, players differ (reported, not a failure): media_player.kitchen" in o["detail"]
    assert "rollback_failed" not in status(sd) or not status(sd)["rollback_failed"]


@pytest.mark.parametrize("eid", ["media_player.xbr_49x800d", "media_player.sonos_move_derp",
                                 "media_player.sonos_move_derp_2"])
def test_a_listed_flapping_player_does_not_fail_the_upgrade(house, eid):
    ops, sd = house
    _add_player(ops, eid, "off")
    _player_after_up(ops, eid, "unavailable")
    assert run(ops, sd, ["homeassistant"]) == 0
    assert status(sd)["last_run"]["outcomes"][0]["result"] == "kept"


@pytest.mark.parametrize("before, after", [("idle", "off"), ("off", "standby"), ("paused", "idle"), ("on", "idle")])
def test_a_player_moving_between_resting_states_does_not_fail_the_upgrade(house, before, after):
    ops, sd = house
    next(s for s in ops.states if s["entity_id"] == "media_player.kitchen")["state"] = before
    _player_after_up(ops, "media_player.kitchen", after)
    assert run(ops, sd, ["homeassistant"]) == 0


def test_the_flapping_players_are_data_in_the_rules():
    text = U.RULES_FILE.read_text()
    assert "flapping_players = [" in text
    assert rules().settings["flapping_players"] == [
        "media_player.xbr_49x800d", "media_player.sonos_move_derp", "media_player.sonos_move_derp_2"]


# ── K5: a replay stopped by a player during verify is retried ────────────

def _verify_replays(ops):
    return [c for c in ops.calls if c[0] == "replay" and c[1].startswith("upgrade-tempo")]


def test_a_replay_stopped_once_during_verify_is_retried_for_the_remaining_rows(house):
    ops, sd = house
    orig = ops.replay

    def replay(label, out, check_only=False, ids=None):
        if label == "upgrade-tempo":
            ops.replay_stops.append(2)
        return orig(label, out, check_only, ids)
    ops.replay = replay
    assert run(ops, sd, ["tempo"]) == 0
    assert status(sd)["last_run"]["outcomes"][0]["result"] == "kept"
    assert _verify_replays(ops) == [("replay", "upgrade-tempo", False),
                                    ("replay", "upgrade-tempo-retry1", False, ("row-c", "inc-0930-search-deep-house-ytm"))]
    assert ("sleep", 300) in ops.calls


def test_a_replay_stopped_four_times_is_a_failed_verify(house):
    ops, sd = house
    orig = ops.replay

    def replay(label, out, check_only=False, ids=None):
        if label.startswith("upgrade-tempo"):
            ops.replay_stops.append(1 if label == "upgrade-tempo" else 0)
        return orig(label, out, check_only, ids)
    ops.replay = replay
    assert run(ops, sd, ["tempo"]) == 1
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "rolled_back"
    assert "stopped by a player 4 times over 15 minutes" in o["detail"]
    assert len(_verify_replays(ops)) == 4 and sum(c == ("sleep", 300) for c in ops.calls) == 3


def test_a_stopped_baseline_still_declines_the_night(house):
    ops, sd = house
    ops.replay_stops.append(1)
    assert run(ops, sd, ["tempo"]) == 0
    assert status(sd)["last_run"]["result"] == "declined" and subjects(ops.repo) == ["initial"]


# ── K6: components not started are tried the next night ─────────────────

def _player_starts_after(ops, label):
    orig = ops.replay

    def replay(lbl, out, check_only=False, ids=None):
        r = orig(lbl, out, check_only, ids)
        if lbl == label:
            ops.states[2]["state"] = "playing"
        return r
    ops.replay = replay
    return lambda: setattr(ops, "replay", orig)


def test_components_not_started_because_the_house_got_busy_go_the_next_night(house):
    """The tester's K6: tempo kept, then a player starts."""
    ops, sd = house
    undo = _player_starts_after(ops, "upgrade-tempo")
    run(ops, sd, ["tempo", "grafana", "litellm"])
    undo()
    o = {x["name"]: x["result"] for x in status(sd)["last_run"]["outcomes"]}
    assert o == {"tempo": "kept", "grafana": "not_started", "litellm": "not_started"}
    assert status(sd)["months"]["2026-12"]["done"] is False
    ops.states[2]["state"] = "idle"
    ops.clock += _dt.timedelta(days=1)
    run(ops, sd, ["tempo", "grafana", "litellm"])
    assert subjects(ops.repo)[-2:] == ["upgrade: grafana 13.2.4 -> 13.2.6", "upgrade: litellm v1.88.1 -> v1.88.6"]
    m = status(sd)["months"]["2026-12"]
    assert m["done"] is True and set(m["components"]) == {"tempo", "grafana", "litellm"}
    ops.clock += _dt.timedelta(days=1)
    n = len(ops.notices)
    run(ops, sd, ["tempo", "grafana", "litellm"])
    assert len(ops.notices) == n and "done; nothing to do" in next(
        (sd / "runs").glob("20261203*-minor.log")).read_text()


def test_the_window_closing_between_components_leaves_the_rest_for_the_next_night(house):
    ops, sd = house
    orig = ops.replay

    def replay(lbl, out, check_only=False, ids=None):
        r = orig(lbl, out, check_only, ids)
        if lbl == "upgrade-tempo":
            ops.clock = ops.clock.replace(hour=10, minute=1)                 # 05:01 local
        return r
    ops.replay = replay
    run(ops, sd, ["tempo", "grafana"])
    ops.replay = orig
    assert status(sd)["months"]["2026-12"]["done"] is False
    ops.clock = NOW + _dt.timedelta(days=1)
    run(ops, sd, ["tempo", "grafana"])
    assert subjects(ops.repo)[-1] == "upgrade: grafana 13.2.4 -> 13.2.6"


def test_when_attempts_run_out_the_notice_lists_what_never_started(house):
    ops, sd = house
    for night in range(7):
        ops.clock = NOW + _dt.timedelta(days=night)
        ops.states[2]["state"] = "idle"
        undo = _player_starts_after(ops, "upgrade-tempo" if night == 0 else "upgrade-baseline")
        run(ops, sd, ["tempo", "grafana"])
        undo()
    m = status(sd)["months"]["2026-12"]
    assert m["done"] is True and m["attempts"] == 7
    assert "grafana 13.2.4 -> 13.2.6: not started (house busy" in ops.notices[-1][2]


# ── K7: a Kronk-stack image is pulled and checked before the tag and deploy ──

def test_a_moved_tag_on_a_kronk_stack_component_is_not_applied_and_nothing_is_deployed(house):
    ops, sd = house
    ref = "ghcr.io/berriai/litellm:v1.88.6"
    ops.repo_digests[ref] = ["ghcr.io/berriai/litellm@sha256:" + "9" * 64]
    assert run(ops, sd, ["litellm"]) == 1
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "not_applied" and "moved between planning" in o["detail"]
    assert deploys(ops) == [] and ops.prod_tag == "sprint-X"
    assert "upgrade-20261201T083000Z-litellm" not in git(ops.repo, "tag")
    assert ("docker", "pull", ref) in ops.calls


def test_the_kronk_stack_pull_comes_before_the_tag(house):
    ops, sd = house
    assert run(ops, sd, ["litellm"]) == 0
    order = [c for c in ops.calls if c[:2] == ("docker", "pull") or (c[0] == "run" and "tag" in c[1] and "git" in c[1][0])
             or c[:2] == ("deploy_tag", "deploy")]
    assert order[0] == ("docker", "pull", "ghcr.io/berriai/litellm:v1.88.6")
    assert order[-1] == ("deploy_tag", "deploy", "upgrade-20261201T083000Z-litellm")


# ── K10: the quarterly check gives up on the lock before its unit's timeout ──

def test_the_quarterly_check_waits_twenty_minutes_at_most_under_its_unit_timeout(house):
    import os
    import re as _re
    ops, sd = house
    assert rules().settings["major_lock_wait_minutes"] == 20
    unit = (ROOT / "systemd" / "kronk-upgrade-major-check.service").read_text()
    timeout = int(_re.search(r"^TimeoutStartSec=(\d+)m$", unit, _re.M).group(1))
    assert timeout >= 20 + 5                     # the lookups and the report fit beside the wait
    sd.mkdir()
    fd = U.take_lock(sd, "pid=1\nrun=upgrade_minor x\n")
    sleeps = []
    ops.sleep = sleeps.append
    try:
        assert U.major_run(ops, rules(), sd) == 1
    finally:
        os.close(fd)
    assert len(sleeps) == 20 and set(sleeps) == {60}
    assert ops.notices[-1][1] == "Kronk: the quarterly upgrade check did not run"
