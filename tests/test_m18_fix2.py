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
