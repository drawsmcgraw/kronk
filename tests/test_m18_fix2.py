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
