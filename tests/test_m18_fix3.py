"""Sprint M18 fix 3: the coordinator's rulings H1 to H9 on the third
end-to-end report (docs/sprint/M18-e2e3.md), against the fake house of
tests/m18_helpers.py. No sockets, no docker, no notifier; git is real in a
temporary repository with its own user."""
import datetime as _dt
import json

import pytest

from tests.m18_helpers import (NOW, FakeOps, Killed, U, base_files, gh_release, git, kill_before, kill_on,
                               make_repo, rules, standard_web)

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture(autouse=True)
def _no_state_env(monkeypatch):
    monkeypatch.delenv("KRONK_UPGRADE_STATE", raising=False)
    monkeypatch.delenv("KRONK_UPGRADE_BRANCH", raising=False)


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


def mutating(ops):
    """Calls that change what runs."""
    out = []
    for c in ops.calls:
        if c[0] == "deploy_tag" and c[1] == "deploy":
            out.append(c)
        elif c[0] == "docker" and c[1] in ("stop", "start", "tag", "pull", "rm"):
            out.append(c)
        elif c[0] == "compose" and c[3] in ("up", "pull"):
            out.append(c)
    return out


def killed(ops, sd, only, undo):
    with pytest.raises(Killed):
        run(ops, sd, only)
    undo()
    ops.calls.clear()
    ops.clock = NOW + _dt.timedelta(days=1)


def all_refs(repo):
    return git(repo, "for-each-ref", "--format=%(refname) %(objectname)") + git(repo, "rev-parse", "HEAD")


def _kill_litellm_in_verify(ops):
    return kill_before(ops, "run_tests", lambda a: ops.containers["kronk-litellm-1"].endswith("v1.88.6"))


def _kill_ha_in_verify(ops):
    return kill_before(ops, "run_tests", lambda a: ops.containers["homeassistant"].endswith("2026.5.5"))


def _kill_after_ha_stop(ops):
    return kill_on(ops, "docker", lambda a: a[0] == "stop")


# ── H1 (X1): a recovery is held to the run's tree checks ─────────────────

@pytest.mark.parametrize("move", [("checkout", "-q", "--detach"), ("checkout", "-q", "-b", "work")])
@pytest.mark.parametrize("who", ["litellm", "homeassistant"])
def test_a_recovery_off_the_expected_branch_commits_deploys_and_restarts_nothing(house, move, who):
    """The tester's X1: killed in verify, then a person detaches HEAD or
    switches to another branch holding the same commits."""
    ops, sd = house
    killed(ops, sd, [who], (_kill_litellm_in_verify if who == "litellm" else _kill_ha_in_verify)(ops))
    git(ops.repo, *move)
    refs = all_refs(ops.repo)
    assert run(ops, sd, [who]) == 2
    assert mutating(ops) == []
    assert all_refs(ops.repo) == refs
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "out_of_step"
    assert ("HEAD is detached" if "--detach" in move else "on branch 'work', expected 'main'") in o["detail"]
    assert "recovered" not in ops.notices[-1][1]


def test_a_stopped_ha_is_still_started_off_the_expected_branch(house):
    """H1's one exception: `docker start` of a stopped HA needs no tree."""
    ops, sd = house
    killed(ops, sd, ["homeassistant"], _kill_after_ha_stop(ops))
    git(ops.repo, "checkout", "-q", "--detach")
    run(ops, sd, ["homeassistant"])
    assert mutating(ops) == [("docker", "start", "homeassistant")] and "homeassistant" not in ops.stopped
