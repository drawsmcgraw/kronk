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


# ── H2 (X2, Y1): a read that fails is unknown, never a fact ──────────────

def _fail_once(ops, kind, pred, result=None):
    """The next call `kind` whose args match pred fails once (a read that failed)."""
    orig, done = getattr(ops, kind), []

    def wrapped(*args, **kw):
        if not done and pred(args):
            done.append(True)
            ops.calls.append((kind,) + args)
            return result or U.Result(1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock")
        return orig(*args, **kw)
    setattr(ops, kind, wrapped)


def test_an_unreadable_production_tag_is_unknown_not_someone_elses_deploy(house):
    """The tester's X2: killed in verify, nobody touches anything, and at
    the next run `deploy_tag.sh status` fails once."""
    ops, sd = house
    killed(ops, sd, ["litellm"], _kill_litellm_in_verify(ops))
    _fail_once(ops, "deploy_tag", lambda a: a[0] == "status")
    assert run(ops, sd, ["litellm"]) == 1
    assert mutating(ops) == []
    st = status(sd)
    assert st["intent"]["component"] == "litellm" and not st["intent"].get("blocked")
    assert not st.get("out_of_step") and not st.get("rollback_failed")
    (_, title, body), = ops.notices
    assert "someone else deployed" not in body and "OUT OF STEP" not in title
    assert "could not read production's tag (deploy_tag.sh status): Cannot connect to the Docker daemon" in body
    assert "will look again" in body
    # The next night the read works: the rollback the record describes is finished.
    ops.calls.clear()
    ops.clock += _dt.timedelta(days=1)
    assert run(ops, sd, ["litellm"]) == 1
    assert [c for c in mutating(ops) if c[0] == "deploy_tag"] == [("deploy_tag", "deploy", "sprint-X")]
    assert "intent" not in status(sd)


def test_someone_elses_deploy_is_said_only_when_another_tag_was_read(house):
    """Mutant r11: the sentence is the fact "production runs tag sprint-Y"."""
    ops, sd = house
    killed(ops, sd, ["litellm"], _kill_litellm_in_verify(ops))
    git(ops.repo, "tag", "sprint-Y", "HEAD~1")
    ops.prod_tag = "sprint-Y"
    assert run(ops, sd, ["litellm"]) == 2
    d = status(sd)["last_run"]["outcomes"][0]["detail"]
    assert "production runs tag sprint-Y" in d and "someone else deployed in between" in d


def test_production_on_no_tag_is_out_of_step_without_blaming_a_deploy(house):
    ops, sd = house
    killed(ops, sd, ["litellm"], _kill_litellm_in_verify(ops))
    _fail_once(ops, "deploy_tag", lambda a: a[0] == "status", U.Result(0, "CONTAINER SERVICE RUNNING_FROM CREATED\n"
               "kronk-orchestrator-1 orchestrator working tree 1 hour ago\n"))
    assert run(ops, sd, ["litellm"]) == 2 and mutating(ops) == []
    d = status(sd)["last_run"]["outcomes"][0]["detail"]
    assert "production's orchestrator runs no tag" in d and "someone else deployed" not in d


def test_a_failed_inspect_of_a_stopped_ha_is_unknown_and_the_hourly_start_still_comes(house, monkeypatch):
    """The tester's Y1, first input: the first `docker inspect homeassistant`
    of the monthly recovery fails once."""
    ops, sd = house
    killed(ops, sd, ["homeassistant"], _kill_after_ha_stop(ops))
    _fail_once(ops, "docker", lambda a: a[:2] == ("inspect", "homeassistant"))
    run(ops, sd, ["homeassistant"])
    assert mutating(ops) == [] and "homeassistant" in ops.stopped
    st = status(sd)
    assert st["intent"]["component"] == "homeassistant" and not st.get("out_of_step")
    assert "could not read what homeassistant runs" in ops.notices[-1][2]
    monkeypatch.setenv("KRONK_UPGRADE_STATE", str(sd))
    ops.clock += _dt.timedelta(minutes=50)
    U.main(["notices"], ops=ops)
    assert "homeassistant" not in ops.stopped


def test_a_failed_docker_start_in_the_monthly_recovery_is_retried_by_the_hourly_command(house, monkeypatch):
    """The tester's Y1, second input: ROLLBACK FAILED keeps the record,
    marked blocked; the hourly command starts HA behind the block."""
    ops, sd = house
    killed(ops, sd, ["homeassistant"], _kill_after_ha_stop(ops))
    _fail_once(ops, "docker", lambda a: a[0] == "start", U.Result(1, "", "Error response from daemon: busy"))
    assert run(ops, sd, ["homeassistant"]) == 2
    st = status(sd)
    assert st["rollback_failed"]["component"] == "homeassistant"
    assert st["intent"]["blocked"] == "rollback_failed"
    monkeypatch.setenv("KRONK_UPGRADE_STATE", str(sd))
    ops.clock += _dt.timedelta(minutes=50)
    U.main(["notices"], ops=ops)
    assert "homeassistant" not in ops.stopped
    # The next night: the block declines the run; nothing else is touched.
    ops.calls.clear()
    ops.clock += _dt.timedelta(days=1)
    assert run(ops, sd, ["homeassistant"]) == 0 and mutating(ops) == []
    assert status(sd)["last_run"]["result"] == "declined"


def test_a_git_read_that_fails_is_unknown(house):
    ops, sd = house
    killed(ops, sd, ["litellm"], _kill_litellm_in_verify(ops))
    _fail_once(ops, "run", lambda a: "merge-base" in a[0], U.Result(128, "", "fatal: unable to read index"))
    assert run(ops, sd, ["litellm"]) == 1 and mutating(ops) == []
    st = status(sd)
    assert st["intent"]["component"] == "litellm" and not st.get("out_of_step")
    assert "could not read git" in ops.notices[-1][2]


def test_a_failed_inspect_with_production_on_the_previous_tag_is_unknown(house):
    ops, sd = house
    killed(ops, sd, ["litellm"], kill_before(ops, "deploy_tag", lambda a: a[0] == "deploy"))
    orig = ops.docker
    ops.docker = lambda *a, **kw: (U.Result(1, "", "context deadline exceeded") if a[:2] == ("inspect", "kronk-litellm-1")
                                   else orig(*a, **kw))
    assert run(ops, sd, ["litellm"]) == 1 and mutating(ops) == []
    assert not status(sd).get("out_of_step") and status(sd)["intent"]["component"] == "litellm"


def test_a_failed_rollback_in_a_run_keeps_the_record_so_a_stopped_ha_is_started(house, monkeypatch):
    """HA's upgrade fails verify, the rollback stops HA and its compose up
    fails: ROLLBACK FAILED with HA stopped. The record stays (blocked)."""
    ops, sd = house
    ops.bad_versions.add("2026.5.5")
    orig = ops.compose

    def compose(project, file, *args, **kw):
        if args[0] == "up" and "2026.5.4" in (ops.repo / file).read_text():
            ops.calls.append(("compose", project, file) + args)
            return U.Result(1, "", "Error response from daemon: no space left on device")
        return orig(project, file, *args, **kw)
    ops.compose = compose
    assert run(ops, sd, ["homeassistant"]) == 2 and "homeassistant" in ops.stopped
    assert status(sd)["intent"]["blocked"] == "rollback_failed"
    monkeypatch.setenv("KRONK_UPGRADE_STATE", str(sd))
    U.main(["notices"], ops=ops)
    assert "homeassistant" not in ops.stopped


def test_a_blocked_record_is_shown_as_needing_ack_by_the_api_and_deploy(tmp_path):
    """The record now outlives a block (H2): the API and deploy_tag.sh do
    not offer a recovery that will not act on it."""
    import subprocess
    import upgrades_api
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tests.m18_helpers import ROOT
    st = {"intent": {"component": "litellm", "from": "v1.88.1", "to": "v1.88.6", "step": "verify",
                     "run": "20261201T083000Z", "at": "2026-12-01T08:41:00+00:00", "blocked": "out_of_step"}}
    (tmp_path / "st").mkdir()
    U.save_status(tmp_path / "st", st, NOW)
    upgrades_api.STATUS_FILE = str(tmp_path / "st" / "status.json")
    app = FastAPI()
    app.include_router(upgrades_api.router)
    w = TestClient(app).get("/api/upgrades").json()["warnings"][0]
    assert "OUT OF STEP" in w and "ack" in w and "recovers it" not in w
    r = subprocess.run(["bash", str(ROOT / "scripts" / "deploy_tag.sh"), "deploy", "m18-fix3-no-such-tag"],
                       capture_output=True, text=True, timeout=30,
                       env={"PATH": __import__("os").environ["PATH"], "HOME": str(tmp_path),
                            "KRONK_UPGRADE_STATE": str(tmp_path / "st"), "KRONK_RELEASES": str(tmp_path / "rel"),
                            "KRONK_LIVE_CONFIG": str(tmp_path / "cfg")})
    assert r.returncode == 1 and "blocked (out_of_step)" in r.stderr and "--ignore-window" not in r.stderr


# ── H3 (Y2, Y3, Y6): the hourly command ──────────────────────────────────

def notices_cmd(ops, sd, monkeypatch):
    monkeypatch.setenv("KRONK_UPGRADE_STATE", str(sd))
    return U.main(["notices"], ops=ops)


def test_the_hourly_command_does_not_start_an_ha_the_run_never_stopped(house, monkeypatch):
    """The tester's Y3: HA's record at `ha-pull` (the run never stopped
    HA); a person stops HA later. The hourly command only warns."""
    ops, sd = house
    killed(ops, sd, ["homeassistant"], kill_on(ops, "docker", lambda a: a[0] == "pull"))
    assert status(sd)["intent"]["step"] == "ha-pull"
    ops.stopped.add("homeassistant")
    for _ in range(3):
        notices_cmd(ops, sd, monkeypatch)
        ops.clock += _dt.timedelta(hours=1)
    assert mutating(ops) == [] and "homeassistant" in ops.stopped
    assert [t for _, t, _ in ops.notices] == ["Kronk upgrade: a run was interrupted (homeassistant)"]


def test_a_start_that_keeps_failing_is_tried_every_hour_and_reported_once_then_once_when_it_works(house, monkeypatch):
    """The tester's Y2: HA's record, HA stopped, `docker start` failing."""
    ops, sd = house
    killed(ops, sd, ["homeassistant"], _kill_after_ha_stop(ops))
    orig = ops.docker
    failing = [True]
    ops.docker = lambda *a, **kw: (U.Result(1, "", "Error response from daemon: busy") if a[0] == "start" and failing
                                   else orig(*a, **kw))
    for _ in range(5):
        notices_cmd(ops, sd, monkeypatch)
        ops.clock += _dt.timedelta(hours=1)
    titles = [t for _, t, _ in ops.notices]
    assert titles == ["Kronk upgrade: a run was interrupted (homeassistant): Home Assistant is down: "
                      "docker start homeassistant failed: Error response from daemon: busy"]
    failing.clear()
    notices_cmd(ops, sd, monkeypatch)
    ops.clock += _dt.timedelta(hours=1)
    notices_cmd(ops, sd, monkeypatch)
    assert "homeassistant" not in ops.stopped
    assert [t for _, t, _ in ops.notices][1:] == [
        "Kronk upgrade: a run was interrupted (homeassistant): Home Assistant was down; started again"]


def test_the_hourly_wait_for_ha_fits_inside_its_unit_and_the_status_is_written_first(house, monkeypatch):
    from tests.m18_helpers import ROOT
    unit = (ROOT / "systemd" / "kronk-upgrade-notices.service").read_text()
    limit = int(__import__("re").search(r"^TimeoutStartSec=(\d+)m$", unit, __import__("re").M).group(1)) * 60
    tries = U.HOURLY_HA_WAIT_S // 10 + 1
    worst = U.HOURLY_START_TIMEOUT_S + tries * 15 + (tries - 1) * 10      # each ha-api GET may take 15 s
    assert worst + 300 <= limit, (worst, limit)
    ops, sd = house
    killed(ops, sd, ["homeassistant"], _kill_after_ha_stop(ops))
    ops.ha_up = False                                                    # HA starts and does not answer yet

    def die(*a, **kw):
        raise Killed("the unit's timeout")
    monkeypatch.setattr(ops, "sleep", die)
    with pytest.raises(Killed):
        notices_cmd(ops, sd, monkeypatch)
    st = status(sd)
    assert st["intent"]["ha_started_at"] and "started it at" in st["attention"][-1]["text"]


def test_the_nightly_rollback_waiting_notice_is_sent_after_the_hourly_one(house, monkeypatch):
    """The tester's Y6."""
    ops, sd = house
    killed(ops, sd, ["litellm"], _kill_litellm_in_verify(ops))
    notices_cmd(ops, sd, monkeypatch)
    assert ops.notices[-1][1] == "Kronk upgrade: a run was interrupted (litellm)"
    ops.states[2]["state"] = "playing"
    assert run(ops, sd, ["litellm"]) == 0
    assert ops.notices[-1][1] == "Kronk upgrade: interrupted (litellm); rollback waiting"
    n = len(ops.notices)
    ops.clock += _dt.timedelta(days=1)
    run(ops, sd, ["litellm"])
    notices_cmd(ops, sd, monkeypatch)
    assert len(ops.notices) == n                                         # once each


# ── H4 (Y4): a replay stopped by its own row is a failed verify ──────────

def _replay_hook(ops, hook):
    """hook(label, ids, out, result) -> result, after the fake replay ran."""
    orig = ops.replay

    def replay(label, out, check_only=False, ids=None):
        r = orig(label, out, check_only, ids)
        return r if check_only else hook(label, ids, out, r)
    ops.replay = replay


@pytest.mark.parametrize("who", ["litellm", "homeassistant"])
def test_a_row_that_changed_a_player_fails_verify_at_once(house, who):
    """The tester's Y4: the first executed row comes back with a player
    change (idle to playing) and exit 3."""
    ops, sd = house

    def hook(label, ids, out, r):
        if label == f"upgrade-{who}":
            d = json.loads(out.read_text())
            d["rows"] = d["rows"][:1]
            d["rows"][0]["player_change"] = {"media_player.kitchen": ["idle", "playing"]}
            out.write_text(json.dumps(d))
            return U.Result(3, "", "voice_replay: STOPPED: media_player state changed after row-a")
        return r
    _replay_hook(ops, hook)
    assert run(ops, sd, [who]) == 1
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "rolled_back" and "row-a changed a player after it executed" in o["detail"]
    assert ("sleep", U.REPLAY_RETRY_WAIT_S) not in ops.calls                   # no retry


def test_a_retry_that_wrote_only_some_of_its_rows_is_not_complete(house):
    ops, sd = house
    ops.replay_stops = [None, 1]           # the baseline whole; verify stopped after one row

    def hook(label, ids, out, r):
        if label.endswith("-retry1"):
            d = json.loads(out.read_text())
            d["rows"] = d["rows"][:1]
            out.write_text(json.dumps(d))
        return r
    _replay_hook(ops, hook)
    assert run(ops, sd, ["litellm"]) == 1
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "rolled_back" and "wrote 1 of 3 selected row(s)" in o["detail"]


def test_a_kept_upgrade_whose_replay_was_stopped_and_retried_says_so(house):
    ops, sd = house
    ops.replay_stops = [None, 2]
    assert run(ops, sd, ["litellm"]) == 0
    (_, title, body), = ops.notices
    assert title == "Kronk upgrade: 1 upgraded"
    assert "the live replay was stopped by a player 1 time(s) and retried" in body


# ── H5 (Y5): an absent HA is created only past the run's checks ─────────

def _ha_gone_after_stop(ops):
    """compose up cut between removing the old container and creating the new one."""
    def gone(a):
        if a[0] == "stop":
            del ops.containers["homeassistant"]
            ops.stopped.discard("homeassistant")
            return True
        return False
    return kill_on(ops, "docker", gone)


PRIVILEGED = "    privileged: true\n"


def _ha_file_back_with_privileged(ops):
    f = ops.repo / "docker-compose.ha.yml"
    f.write_text(f.read_text().replace(":2026.5.5\n", ":2026.5.4\n" + PRIVILEGED))
    ops.config_extra["homeassistant"] = "privileged"


@pytest.mark.parametrize("how", ["commit", "uncommitted", "env file", "detached"])
def test_an_absent_ha_is_not_created_from_a_file_the_run_never_checked(house, how):
    """The tester's Y5: HA's record, no container, and the compose file at
    the old pin with a person's change in it (committed or not), an env
    change only the config hash sees, or a detached HEAD."""
    ops, sd = house
    killed(ops, sd, ["homeassistant"], _ha_gone_after_stop(ops))
    if how in ("commit", "uncommitted"):
        _ha_file_back_with_privileged(ops)
        if how == "commit":
            git(ops.repo, "commit", "-qam", "ha: privileged")
    elif how == "env file":
        ops.config_extra["homeassistant"] = "TZ changed in an env file"
    else:
        git(ops.repo, "checkout", "-q", "--detach")
    assert run(ops, sd, ["homeassistant"]) == 2
    assert not any(c[0] == "compose" and c[3] == "up" for c in ops.calls) and "homeassistant" not in ops.containers
    st = status(sd)
    o, = st["last_run"]["outcomes"]
    assert o["result"] == "out_of_step" and "was not created" in o["detail"]
    assert st["intent"]["blocked"] == "out_of_step"
    (_, title, _), = ops.notices
    assert title == "Kronk upgrade: OUT OF STEP (homeassistant): Home Assistant is down"


def test_nothing_was_touched_is_said_only_when_nothing_was(house):
    """HA stopped is started (H1's exception), then the tree is not clean:
    OUT OF STEP, and the notice does not say nothing was touched."""
    ops, sd = house
    killed(ops, sd, ["homeassistant"], _kill_after_ha_stop(ops))
    (ops.repo / "notes.txt").write_text("a person's notes\n")
    assert run(ops, sd, ["homeassistant"]) == 2
    o, = status(sd)["last_run"]["outcomes"]
    assert "nothing was touched" not in o["detail"] and "nothing else was touched" in o["detail"]
    assert "nothing was touched" not in ops.notices[-1][2]


def test_a_clean_absent_ha_is_still_created(house):
    ops, sd = house
    killed(ops, sd, ["homeassistant"], _ha_gone_after_stop(ops))
    assert run(ops, sd, ["homeassistant"]) == 1
    assert ops.containers["homeassistant"].endswith(":2026.5.4")
    assert status(sd)["last_run"]["outcomes"][0]["result"] == "recovered"


# ── H6 (Y7): the branch has one source ───────────────────────────────────

def _branch_file(home, text):
    f = home / ".config" / "kronk" / "upgrade.env"
    f.parent.mkdir(parents=True)
    f.write_text(text)
    return f


def test_the_branch_comes_from_the_rules_the_file_outside_the_repo_or_the_variable(tmp_path):
    assert U.load_rules(env={}).settings["expected_branch"] == "main"
    _branch_file(tmp_path, "# set at install\nKRONK_UPGRADE_BRANCH=feature/m18\n")
    assert U.load_rules(env={"HOME": str(tmp_path)}).settings["expected_branch"] == "feature/m18"
    assert U.load_rules(env={"HOME": str(tmp_path), "KRONK_UPGRADE_BRANCH": "refactor"}).settings["expected_branch"] == "refactor"
    _branch_file(tmp_path / "x", "KRONK_UPGRADE_BRANCH=other\n")
    assert U.load_rules(env={"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "x" / ".config")}
                        ).settings["expected_branch"] == "other"


@pytest.mark.parametrize("bad", ["a b", "HEAD", "-x", "a..b", "x.lock", "@{-1}", "main\nevil"])
def test_a_branch_name_git_would_refuse_is_refused(tmp_path, bad):
    with pytest.raises(U.RulesError, match="not a valid branch name"):
        U.load_rules(env={"KRONK_UPGRADE_BRANCH": bad})


def test_a_by_hand_dry_run_agrees_with_the_timer_through_the_file(tmp_path, capsys):
    repo = make_repo(tmp_path, base_files(), branch="feature/m18")
    ops = FakeOps(repo, standard_web())
    _branch_file(tmp_path / "home", "KRONK_UPGRADE_BRANCH=feature/m18\n")
    r = U.load_rules(env={"HOME": str(tmp_path / "home")})
    r.components = [c for c in r.components if c["name"] == "grafana"]
    U.minor_run(ops, r, tmp_path / "state", dry_run=True)
    out = capsys.readouterr().out
    assert "a real run now would go ahead" in out and "on branch" not in out


def test_the_units_carry_no_branch_and_the_drop_in_points_at_the_one_file():
    from tests.m18_helpers import ROOT
    for name in ("kronk-upgrade-minor.service", "kronk-upgrade-major-check.service"):
        text = (ROOT / "systemd" / name).read_text()
        assert not any(ln.startswith("Environment=KRONK_UPGRADE_BRANCH") for ln in text.splitlines()), name
    doc = (ROOT / "docs" / "features" / "upgrade-cadence.md").read_text()
    assert "EnvironmentFile=%h/.config/kronk/upgrade.env" in doc and "git check-ref-format --branch" in doc


# ── H7 (Y8): how HA stopped is read from the stopped container ───────────

def _upgrade_ups(ops):
    return [c for c in ops.calls if c[0] == "compose" and c[1] == "kronk-ha" and c[3] == "up"]


@pytest.mark.parametrize("code, oom", [("137", "false"), ("0", "true")])
def test_ha_killed_at_the_end_of_its_grace_period_fails_the_stop_before_the_new_one_is_created(house, code, oom):
    ops, sd = house
    ops.stop_exit, ops.oom_killed = code, oom
    assert run(ops, sd, ["homeassistant"]) == 1
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "rolled_back" and "did not shut down inside the 120 s grace period" in o["detail"]
    assert len(_upgrade_ups(ops)) == 1                       # the rollback's; the upgrade's up never ran
    assert [c for c in ops.calls if c[:2] == ("docker", "stop")] == [("docker", "stop", "-t", "120", "homeassistant")]
    assert not any(c[:2] == ("docker", "events") for c in ops.calls)
    assert ops.containers["homeassistant"].endswith(":2026.5.4") and "homeassistant" not in ops.stopped


def test_a_stop_whose_exit_code_cannot_be_read_is_a_failed_check(house):
    ops, sd = house
    orig = ops.docker
    ops.docker = lambda *a, **kw: (U.Result(1, "", "context deadline exceeded") if "{{.State.ExitCode}} {{.State.OOMKilled}}" in a
                                   else orig(*a, **kw))
    assert run(ops, sd, ["homeassistant"]) == 1
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "rolled_back" and "cannot read how homeassistant stopped" in o["detail"]


def test_a_clean_stop_passes_and_no_docker_events_is_read(house):
    ops, sd = house
    assert run(ops, sd, ["homeassistant"]) == 0
    assert ("docker", "inspect", "homeassistant", "-f", "{{.State.ExitCode}} {{.State.OOMKilled}}") in ops.calls
    assert not any(c[:2] == ("docker", "events") for c in ops.calls)


# ── H8 (Y9): ack says what it leaves, and refuses two cases without a flag ──

def ack(ops, sd, monkeypatch, *flags):
    monkeypatch.setenv("KRONK_UPGRADE_STATE", str(sd))
    return U.main(["ack", *flags], ops=ops)


def test_ack_refuses_while_ha_is_stopped_and_says_what_it_leaves_with_the_flag(house, monkeypatch, capsys):
    ops, sd = house
    killed(ops, sd, ["homeassistant"], _kill_after_ha_stop(ops))
    capsys.readouterr()
    assert ack(ops, sd, monkeypatch) == 1
    out = capsys.readouterr().out
    assert "git has 2026.5.5" in out and "running is nothing (homeassistant is stopped" in out
    assert "refused" in out and "--yes-leave-as-is" in out
    assert status(sd)["intent"]["component"] == "homeassistant"
    assert ack(ops, sd, monkeypatch, "--yes-leave-as-is") == 0
    out = capsys.readouterr().out
    assert "cleared" in out and "left as is: Home Assistant is stopped" in out
    assert "intent" not in status(sd) and "homeassistant" in ops.stopped


def test_ack_refuses_while_production_runs_the_runs_unverified_tag(house, monkeypatch, capsys):
    ops, sd = house
    killed(ops, sd, ["litellm"], _kill_litellm_in_verify(ops))
    capsys.readouterr()
    assert ack(ops, sd, monkeypatch) == 1
    out = capsys.readouterr().out
    assert "production runs tag upgrade-20261201T083000Z-litellm" in out and "unverified" in out and "refused" in out
    assert ack(ops, sd, monkeypatch, "--yes-leave-as-is") == 0
    assert "left as is: production on the run's unverified tag upgrade-20261201T083000Z-litellm" in capsys.readouterr().out


def test_ack_with_nothing_dangerous_clears_and_still_says_what_runs(house, monkeypatch, capsys):
    ops, sd = house
    killed(ops, sd, ["litellm"], kill_before(ops, "deploy_tag", lambda a: a[0] == "deploy"))
    capsys.readouterr()
    assert ack(ops, sd, monkeypatch) == 0
    out = capsys.readouterr().out
    assert "git has v1.88.6" in out and "running is v1.88.1" in out and "production runs tag sprint-X" in out
    assert "intent" not in status(sd)


# ── H9: one status schema for the script, the API and deploy_tag.sh ─────

_INTENT = {"component": "litellm", "from": "v1.88.1", "to": "v1.88.6", "step": "verify"}
RESIDUE = [
    {"months": {"2026-12": {"attempts": 1, "components": {"grafana": "kept"}}}},
    {"intent": {**_INTENT, "head": 5}},
    {"unreported": {"outcomes": [{"name": "a", "frm": "1", "to": "2", "result": "kept", "detail": 7}]}},
    {"rollback_failed": {"component": "litellm", "detail": 7}},
    {"months": {"2026-12": {"attempts": -5}}},
    {"intent": {}},
    {"attention": ["a warning as a string"]},
    {"pending_notices": [{"id": "n", "tag": "t", "title": "x", "body": "b", "attempts": -1}]},
    {"last_run": {"outcomes": [{"name": "a", "frm": "1", "to": "2", "result": "kept", "extra": 1}]}},
]
DEEP = b'{"x": ' + b"[" * 3000 + b"]" * 3000 + b"}"


def _raw(x):
    return x if isinstance(x, bytes) else json.dumps(x).encode()


@pytest.mark.parametrize("bad", RESIDUE + [DEEP, b"[1, 2]"], ids=range(len(RESIDUE) + 2))
def test_a_status_file_off_the_schema_is_corrupt_for_the_script_and_one_warning_for_the_api(house, bad, monkeypatch):
    import upgrades_api
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    ops, sd = house
    sd.mkdir()
    raw = _raw(bad)
    (sd / "status.json").write_bytes(raw)
    assert run(ops, sd, ["tempo"]) == 2
    monkeypatch.setenv("KRONK_UPGRADE_STATE", str(sd))
    for cmd in ("notices", "ack", "status"):
        assert U.main([cmd], ops=ops) == 2
    assert (sd / "status.json").read_bytes() == raw
    upgrades_api.STATUS_FILE = str(sd / "status.json")
    app = FastAPI()
    app.include_router(upgrades_api.router)
    w = TestClient(app).get("/api/upgrades").json()["warnings"]
    assert len(w) == 1 and w[0].startswith("upgrade status file unreadable")


def test_an_empty_intent_is_corrupt_for_deploy_tag_too(tmp_path):
    import os
    import subprocess
    from tests.m18_helpers import ROOT
    (tmp_path / "st").mkdir()
    (tmp_path / "st" / "status.json").write_text('{"intent": {}}')
    r = subprocess.run(["bash", str(ROOT / "scripts" / "deploy_tag.sh"), "deploy", "m18-fix3-no-such-tag"],
                       capture_output=True, text=True, timeout=30,
                       env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "KRONK_UPGRADE_STATE": str(tmp_path / "st"),
                            "KRONK_RELEASES": str(tmp_path / "rel"), "KRONK_LIVE_CONFIG": str(tmp_path / "cfg")})
    assert r.returncode == 1 and "cannot tell whether an upgrade run was interrupted" in r.stderr
    assert "intent.component is missing" in r.stderr


def test_every_file_the_script_writes_fits_the_schema(house, monkeypatch):
    """A control across the paths that write: a recovery, a block kept with
    its record, the hourly notices, ack."""
    ops, sd = house
    killed(ops, sd, ["homeassistant"], _kill_after_ha_stop(ops))
    (ops.repo / "notes.txt").write_text("x\n")
    run(ops, sd, ["homeassistant"])
    assert U.load_status(sd)["intent"]["blocked"]
    monkeypatch.setenv("KRONK_UPGRADE_STATE", str(sd))
    ops.notify_ok = False
    U.main(["notices"], ops=ops)
    U.main(["ack", "--yes-leave-as-is"], ops=ops)
    U.load_status(sd)


# ── H9: the loader compares files by resolved path and names folded ──────

def _rules_with(tmp_path, old, new):
    text = U.RULES_FILE.read_text()
    assert text.count(old) == 1, old
    p = tmp_path / "r.toml"
    p.write_text(text.replace(old, new))
    return p


_GRAFANA_FILE = 'order = 20\nfile = "docker-compose.telemetry.yml"'
_GRAFANA_PROJECT = 'project = "kronk-telemetry"\nservice = "grafana"'


@pytest.mark.parametrize("old, new, why", [
    (_GRAFANA_FILE, f'order = 20\nfile = "{U.REPO.resolve() / "docker-compose.yml"}"', "the Kronk stack's compose file"),
    (_GRAFANA_FILE, 'order = 20\nfile = "Docker-Compose.yml"', "the Kronk stack's compose file"),
    (_GRAFANA_FILE, 'order = 20\nfile = "DOCKER-COMPOSE.MA.YML"', "Music Assistant stack"),
    (_GRAFANA_PROJECT, 'project = "kronk_ma"\nservice = "grafana"', "case variant"),
    (_GRAFANA_PROJECT, 'project = "Kronk_MA"\nservice = "grafana"', "case variant"),
    ('order = 301\nfile = "docker-compose.ma.yml"', 'order = 301\nfile = "Docker-Compose.MA.yml"\ndeploy = "kronk"',
     "Music Assistant stack"),
])
def test_the_loader_folds_case_separators_and_paths(tmp_path, old, new, why):
    with pytest.raises(U.RulesError, match=why):
        U.load_rules(_rules_with(tmp_path, old, new), env={})


def test_a_status_temp_directory_is_ignored_silently(house):
    import os
    ops, sd = house
    d = sd / ".status.adir"
    d.mkdir(parents=True)
    os.utime(d, (NOW.timestamp() - 2 * 86400,) * 2)
    run(ops, sd, ["tempo"])
    log = next((sd / "runs").glob("*-minor.log")).read_text()
    assert d.is_dir() and ".status.adir" not in log


# ── H9: a component without a container has its own recovery row ─────────

@pytest.mark.parametrize("pin, result", [("3.12.8-slim", "recovered"), ("3.12.7-slim", "recovered"),
                                         ("3.12.9-slim", "out_of_step")])
def test_a_record_for_a_component_without_a_container(tmp_path, pin, result):
    files = {**base_files(), "orchestrator/Dockerfile": "FROM python:3.12.7-slim\n"}
    repo = make_repo(tmp_path, files)
    ops = FakeOps(repo, standard_web())
    sd = tmp_path / "state"
    head = git(repo, "rev-parse", "HEAD").strip()
    if pin != "3.12.7-slim":
        (repo / "orchestrator/Dockerfile").write_text(f"FROM python:{pin}\n")
        git(repo, "-c", "user.email=upgrade_minor@kronk.invalid", "commit", "-qam",
            f"upgrade: python-base-orchestrator 3.12.7-slim -> {pin}")
    U.save_status(sd, {"intent": {"component": "python-base-orchestrator", "from": "3.12.7-slim", "to": "3.12.8-slim",
                                  "step": "verify", "deploy": "kronk", "run": "20261201T083000Z", "head": head,
                                  "prev_tag": "sprint-X", "at": "2026-12-01T08:40:00+00:00"}}, NOW)
    ops.clock = NOW + _dt.timedelta(days=1)
    run(ops, sd, ["python-base-orchestrator"])
    o = status(sd)["last_run"]["outcomes"][0]
    assert o["result"] == result and mutating(ops) == []
    if result == "recovered":
        assert "nothing to recover" in o["state"] and "intent" not in status(sd)


# ── The player list (Drew's contents; three browser players added as ruled) ──

BROWSERS = ["media_player.camerons_macbook_pro", "media_player.drew_s_windows_firefox",
            "media_player.web_firefox_on_linux"]


@pytest.mark.parametrize("eid", BROWSERS)
def test_a_browser_player_coming_online_during_the_restart_does_not_fail_the_upgrade(house, eid):
    ops, sd = house
    ops.states.append({"entity_id": eid, "state": "unavailable", "last_changed": "2026-11-01T00:00:00Z"})
    orig = ops.compose

    def compose(project, file, *args, **kw):
        r = orig(project, file, *args, **kw)
        if args[0] == "up" and project == "kronk-ha":
            next(s for s in ops.states if s["entity_id"] == eid)["state"] = "idle"
        return r
    ops.compose = compose
    assert run(ops, sd, ["homeassistant"]) == 0
    assert set(BROWSERS) <= set(rules().settings["flapping_players"])
