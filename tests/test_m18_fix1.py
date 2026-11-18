"""Sprint M18 fix 1: the monthly run against a fake house that records the
run's own work the way the real one does (FakeOps: each replay row is a run
on HA's kronk-dev pipeline that reaches nginx as POST /dev/voice/api/chat
and that voice_tracer holds; each deploy leaves deploy_tag.sh's /api/chat
check in nginx's log with the User-Agent it was handed). No sockets, no
docker, no notifier; git is real in a temporary repository."""
import json

import pytest

from tests.m18_helpers import (NOW, ROOT, FakeOps, U, base_files, gh_release, git, household_chat, make_repo,
                               rules, standard_web)

pytestmark = pytest.mark.usefixtures("no_network")

COMPS = ["tempo", "grafana", "searxng", "litellm", "homeassistant"]


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


def run(ops, sd, only=COMPS, **kw):
    return U.minor_run(ops, rules(only), sd, **kw)


def status(sd):
    return json.loads((sd / "status.json").read_text())


def subjects(repo):
    return git(repo, "log", "--format=%s").splitlines()[::-1]


# ── F1: the run's own work is not the household ──────────────────────────

def test_the_runs_own_replays_and_deploy_checks_do_not_stop_any_component(house):
    ops, sd = house
    assert run(ops, sd) == 0
    # The traces were left: the baseline and five verify replays, two deploys.
    assert sum("/dev/voice/api/chat" in ln for ln in ops.nginx_own) == 6 * 4
    assert sum('"POST /api/chat' in ln for ln in ops.nginx_own) == 2
    assert len(ops.held_ids) == 24
    # And every component still started, and was kept.
    assert subjects(ops.repo)[1:] == [
        "upgrade: tempo 3.1.1 -> 3.1.2", "upgrade: grafana 13.2.4 -> 13.2.6",
        "upgrade: searxng 2026.11.9-57ff753ad -> 2026.11.17-b99cf7496", "upgrade: litellm v1.88.1 -> v1.88.6",
        "upgrade: homeassistant 2026.5.4 -> 2026.5.5"]
    assert [o["result"] for o in status(sd)["last_run"]["outcomes"]] == ["kept"] * 5
    assert ops.notices[0][1] == "Kronk upgrade: 5 upgraded"


def test_the_deploy_check_carries_this_runs_user_agent(house):
    ops, sd = house
    run(ops, sd, only=["litellm"])
    chat = [ln for ln in ops.nginx_own if '"POST /api/chat' in ln]
    assert len(chat) == 1 and '"kronk-upgrade/20261201T083000Z-' in chat[0]


def test_seven_nights_with_the_runs_own_traffic_apply_on_the_first(house):
    """The tester's case (M18-e2e B1): seven nights, each with a full
    baseline. Before the fix: seven nights of `not started`, no notice."""
    ops, sd = house
    for night in range(1, 8):
        ops.clock = NOW + __import__("datetime").timedelta(days=night - 1)
        run(ops, sd)
    m = status(sd)["months"]["2026-12"]
    assert (m["attempts"], m["done"]) == (1, True)
    assert len(ops.notices) == 1 and ops.notices[0][1] == "Kronk upgrade: 5 upgraded"
    assert sum(1 for c in ops.calls if c == ("run_tests",)) == 6       # one baseline, five verifies, night 1 only


def test_a_household_request_before_the_run_still_declines(house):
    ops, sd = house
    household_chat(ops)
    assert run(ops, sd) == 0
    st = status(sd)
    assert st["last_run"]["result"] == "declined"
    assert "1 chat/voice request(s) in the last 5 minutes" in st["last_run"]["summary"]
    assert subjects(ops.repo) == ["initial"]


def test_a_household_request_between_components_stops_the_rest(house):
    ops, sd = house
    orig = ops.replay

    def replay(label, out, check_only=False):
        r = orig(label, out, check_only)
        if label == "upgrade-grafana":
            household_chat(ops, path="/voice/api/chat")          # someone talks to a puck
        return r
    ops.replay = replay
    run(ops, sd)
    res = {o["name"]: (o["result"], o["detail"]) for o in status(sd)["last_run"]["outcomes"]}
    assert res["tempo"][0] == res["grafana"][0] == "kept"
    for name in ("searxng", "litellm", "homeassistant"):
        assert res[name] == ("not_started", "house busy: 1 chat/voice request(s) in the last 5 minutes")


@pytest.mark.parametrize("ua", ["curl/8.14.1", "kronk-upgrade/20261130T083000Z-aaaaaaaaaaaa", "Mozilla/5.0"])
def test_a_production_chat_request_with_any_other_user_agent_counts(house, ua):
    """A hand-run deploy (curl), another run's marker, the web UI: all count."""
    ops, sd = house
    household_chat(ops, ua=ua, path="/message")
    run(ops, sd, only=["tempo"])
    assert status(sd)["last_run"]["result"] == "declined"


def test_a_held_household_run_still_declines_between_components(house):
    ops, sd = house
    orig = ops.replay

    def replay(label, out, check_only=False):
        r = orig(label, out, check_only)
        if label == "upgrade-tempo":
            ops.held_ids.append("01JHOUSEHOLD0001")        # a puck request on the kronk pipeline
        return r
    ops.replay = replay
    run(ops, sd, only=["tempo", "grafana"])
    o = {x["name"]: x for x in status(sd)["last_run"]["outcomes"]}
    assert o["tempo"]["result"] == "kept"
    assert o["grafana"]["result"] == "not_started"
    assert "voice_tracer holds 1 run(s) in the correction window that are not on kronk-dev" in o["grafana"]["detail"]


def test_dev_path_requests_and_dev_held_runs_are_not_a_decline(house):
    ops, sd = house
    household_chat(ops, path="/dev/voice/api/chat")
    ops.held_ids, ops.dev_runs = ["01JDEVBYHAND01"], {"01JDEVBYHAND01"}   # a replay run by hand on kronk-dev
    assert run(ops, sd, only=["tempo"]) == 0
    assert status(sd)["last_run"]["result"] == "upgraded"


def test_when_ha_cannot_list_dev_runs_only_the_runs_own_are_set_aside(house):
    ops, sd = house
    ops.dev_list_ok = False
    ops.held_ids, ops.dev_runs = ["01JDEVBYHAND01"], {"01JDEVBYHAND01"}   # by hand: not this run's
    run(ops, sd, only=["tempo"])
    st = status(sd)
    assert st["last_run"]["result"] == "declined"
    assert "could not be read" in st["last_run"]["summary"]
    # Without the by-hand run, the run's own replay runs are set aside by id.
    ops.held_ids, ops.dev_runs = [], set()
    assert run(ops, sd, only=["tempo", "grafana"], ignore_month=True) == 0
    assert [o["result"] for o in status(sd)["last_run"]["outcomes"]] == ["kept", "kept"]


def test_an_unreadable_state_file_with_runs_held_is_not_quiet(house):
    ops, sd = house
    ops.held = 2
    ops.voice_tracer_held_ids = lambda rel: None
    run(ops, sd, only=["tempo"])
    assert "cannot be read to tell whose" in status(sd)["last_run"]["summary"]


def test_ops_hands_deploy_tag_the_marker_and_the_script_uses_it(tmp_path):
    seen = {}

    class O(U.Ops):
        def run(self, args, timeout=600, env=None):
            seen["args"], seen["env"] = args, env
            return U.Result(0)
    o = O(tmp_path)
    o.client_ua = "kronk-upgrade/x-1"
    o.deploy_tag("deploy", "t")
    assert seen["env"] == {"KRONK_CLIENT_UA": "kronk-upgrade/x-1"}
    text = (ROOT / "scripts" / "deploy_tag.sh").read_text()
    assert 'ua=(-A "$KRONK_CLIENT_UA")' in text
    assert 'curl -sf "${ua[@]}" --max-time 60 -H \'Content-Type: application/json\' http://localhost/api/chat' in text


def test_the_nginx_line_parser_reads_path_and_user_agent():
    ln = '172.18.0.1 - - [18/Nov/2026:12:01:03 +0000] "POST /dev/voice/api/chat HTTP/1.1" 200 172 "-" "kronk-upgrade/a b"'
    assert U.CHAT_ROUTE.search(ln).group(1) == "/dev/voice/api/chat"
    assert U.UA_FIELD.search(ln).group(1) == "kronk-upgrade/a b"
    assert U.CHAT_ROUTE.search(ln.replace("/dev/voice/api/chat", "/api/chats")) is None


# ── F2: an outcome says what happened ────────────────────────────────────

def _mutating(ops):
    return [c for c in ops.calls if (c[0] == "docker" and c[1] in ("pull", "stop", "tag", "start", "restart", "kill"))
            or (c[0] == "compose" and c[3] in ("up", "pull", "stop", "restart")) or (c[0] == "deploy_tag" and c[1] == "deploy")]


def test_the_testers_same_day_rerun_now_works(house):
    """M18-e2e B2's input: litellm rolled back at 08:30, rerun the same day
    with --ignore-month (as the guide says). Before: `git tag` collided and
    the notice said ROLLBACK FAILED with nothing deployed."""
    ops, sd = house
    ops.replay_regress = {"v1.88.6": ["row-a"]}
    assert run(ops, sd, only=["litellm"]) == 1
    ops.replay_regress = {}
    ops.clock = NOW.replace(hour=9, minute=40)
    assert run(ops, sd, only=["litellm"], ignore_month=True) == 0
    assert ops.notices[-1][1] == "Kronk upgrade: 1 upgraded"
    assert "ROLLBACK FAILED" not in "".join(n[1] + n[2] for n in ops.notices)
    assert set(git(ops.repo, "tag").split()) == {"sprint-X", "upgrade-20261201T083000Z-litellm",
                                                  "upgrade-20261201T094000Z-litellm"}
    assert ops.containers["kronk-litellm-1"] == "ghcr.io/berriai/litellm:v1.88.6"


def test_a_tag_that_exists_elsewhere_is_not_applied_and_git_is_put_back(house):
    ops, sd = house
    git(ops.repo, "tag", "upgrade-20261201T083000Z-litellm")          # at "initial", not at the pin commit
    assert run(ops, sd, only=["litellm"]) == 1
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "not_applied"
    assert o["state"].startswith("not applied; git is at v1.88.1 again (revert ")
    assert o["state"].endswith("), running is v1.88.1")
    assert "exists at" in o["detail"]
    assert subjects(ops.repo)[-2:] == ["upgrade: litellm v1.88.1 -> v1.88.6", "upgrade: not applied, litellm back v1.88.6 -> v1.88.1"]
    assert U.read_pin(ops.repo, rules().component("litellm")).value == "v1.88.1"
    assert git(ops.repo, "status", "--porcelain") == ""
    assert _mutating(ops) == []                                        # nothing deployed, nothing rolled back
    (_, title, body), = ops.notices
    assert title == "Kronk upgrade: 1 not applied" and "ROLLBACK" not in title + body
    assert body.startswith("litellm v1.88.1 -> v1.88.6: not applied; git is at v1.88.1 again")
    st = status(sd)
    assert not st.get("rollback_failed") and not st.get("out_of_step")    # later runs are not blocked


def test_a_tag_that_already_points_at_the_pin_commit_is_reused(house):
    ops, sd = house
    ctx = U.Ctx(ops, rules(["litellm"]), U.RunLog(None), sd, "20261201T083000Z", NOW, "sprint-X")
    git(ops.repo, "tag", "t-same")
    assert U.kronk_up(ctx, rules().component("litellm"), "t-same") is None
    assert not any(c[0] == "run" and c[1][-2:] == ("tag", "t-same") for c in ops.calls)
    assert ("deploy_tag", "deploy", "t-same") in ops.calls


def test_a_failed_pin_commit_puts_the_file_back(house):
    ops, sd = house
    orig = ops.git

    def git_(*args, check=True):
        if args[0] == "commit":
            raise U.StepFailed("git commit: fatal: unable to write new index file")
        return orig(*args, check=check)
    ops.git = git_
    assert run(ops, sd, only=["tempo"]) == 1
    o, = status(sd)["last_run"]["outcomes"]
    assert (o["result"], o["state"]) == ("not_applied", "not applied; git is at 3.1.1, running is 3.1.1")
    assert git(ops.repo, "status", "--porcelain") == ""
    assert _mutating(ops) == []


def test_a_revert_that_fails_says_exactly_what_is_out_of_step_and_blocks(house):
    ops, sd = house
    git(ops.repo, "tag", "upgrade-20261201T083000Z-litellm")
    orig = ops.git

    def git_(*args, check=True):
        if args[0] == "revert":
            raise U.StepFailed("git revert: error: could not revert")
        return orig(*args, check=check)
    ops.git = git_
    assert run(ops, sd, only=["litellm", "homeassistant"]) == 2
    st = status(sd)
    o = st["last_run"]["outcomes"]
    assert [x["result"] for x in o] == ["out_of_step"]                 # HA never started
    assert o[0]["state"].startswith("OUT OF STEP: git is at v1.88.6 (commit ") and o[0]["state"].endswith("running is v1.88.1")
    assert st["out_of_step"]["component"] == "litellm"
    assert ops.notices[0][1] == "Kronk upgrade: OUT OF STEP (litellm)"
    # The next run declines on it until ack.
    assert run(ops, sd, only=["litellm"], ignore_month=True) == 0
    assert "left litellm out of step" in status(sd)["last_run"]["summary"]


def test_an_error_before_any_change_is_not_called_a_rollback(house):
    ops, sd = house
    orig = ops.git

    def git_(*args, check=True):
        if args[0] == "diff":
            raise U.StepFailed("git diff: fatal: bad object sprint-X")
        return orig(*args, check=check)
    ops.git = git_
    run(ops, sd, only=["litellm"])
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "not_started" and "stopped by an error before any change" in o["detail"]
    assert not status(sd).get("rollback_failed")


def test_notice_lines_put_the_state_first_and_cut_only_the_detail():
    o = U.Outcome("homeassistant", "2026.5.4", "2026.5.5", "rollback_failed", "x" * 2000,
                  state="ROLLBACK FAILED; running is nothing (homeassistant is stopped; Home Assistant is down); git has 2026.5.4")
    title, body = U.notice_text([o], [], "l")
    assert title == "Kronk upgrade: ROLLBACK FAILED (homeassistant): Home Assistant is down"
    first = body.splitlines()[0]
    assert first.startswith("homeassistant 2026.5.4 -> 2026.5.5: ROLLBACK FAILED; running is nothing "
                            "(homeassistant is stopped; Home Assistant is down); git has 2026.5.4; needs you")
    assert first.endswith("…)") and len(first) < 600


# ── F4: a failure before HA was stopped does not restart HA ──────────────

@pytest.mark.parametrize("fail", ["pull", "tag"])
def test_a_failure_before_the_stop_does_not_touch_ha(house, fail):
    ops, sd = house
    if fail == "pull":
        ops.pull_fail = {"ghcr.io/home-assistant/home-assistant:2026.5.5"}
    else:
        orig = ops.docker

        def docker(*args, timeout=600):
            if args[0] == "tag":
                ops.calls.append(("docker",) + args)
                return U.Result(1, "", "Error response from daemon: no such image")
            return orig(*args, timeout=timeout)
        ops.docker = docker
    assert run(ops, sd, only=["homeassistant"]) == 1
    assert not any(c[:2] == ("docker", "stop") for c in ops.calls)
    assert not any(c[0] == "compose" and c[3] == "up" for c in ops.calls)
    assert ops.containers["homeassistant"].endswith(":2026.5.4") and "homeassistant" not in ops.stopped
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "not_applied" and o["state"].endswith("running is 2026.5.4")
    assert U.read_pin(ops.repo, rules().component("homeassistant")).value == "2026.5.4"


# ── F3: a run killed mid-component is seen by the next run ───────────────
# Each row of the tester's table (M18-e2e B3): the run is killed at a step
# (a BaseException, which no handler in the script catches, like SIGKILL:
# nothing after it runs), then the next night runs on the same house.

import datetime as _dt


class Killed(BaseException):
    pass


def kill_on(ops, kind, pred):
    """Kill the run when the call `kind` (git|docker|deploy_tag) matches pred(args), after it ran."""
    orig = getattr(ops, kind)

    def wrapped(*args, **kw):
        r = orig(*args, **kw)
        if pred(args):
            raise Killed(f"killed after {kind} {args[:3]}")
        return r
    setattr(ops, kind, wrapped)
    return lambda: setattr(ops, kind, orig)


def kill_before(ops, kind, pred):
    orig = getattr(ops, kind)

    def wrapped(*args, **kw):
        if pred(args):
            raise Killed(f"killed before {kind} {args[:3]}")
        return orig(*args, **kw)
    setattr(ops, kind, wrapped)
    return lambda: setattr(ops, kind, orig)


def killed_run(ops, sd, only, undo):
    with pytest.raises(Killed):
        run(ops, sd, only=only)
    undo()
    ops.calls.clear()
    ops.clock = NOW + _dt.timedelta(days=1)


def deploys(ops):
    return [c for c in ops.calls if c[0] == "deploy_tag" and c[1] == "deploy"]


def test_killed_after_the_pin_commit_before_the_tag(house):
    ops, sd = house
    undo = kill_before(ops, "git", lambda a: a[0] == "tag")
    killed_run(ops, sd, ["litellm"], undo)
    it = status(sd)["intent"]
    assert (it["component"], it["step"], it["from"], it["to"], it["prev_tag"]) == ("litellm", "tag", "v1.88.1", "v1.88.6", "sprint-X")
    assert run(ops, sd, only=["litellm"]) == 1
    st = status(sd)
    assert "intent" not in st
    o, = st["last_run"]["outcomes"]
    assert o["result"] == "recovered"
    assert o["state"].startswith("recovered: git had v1.88.6 while v1.88.1 ran; pin put back to v1.88.1")
    assert subjects(ops.repo)[-1] == "upgrade: roll back litellm v1.88.6 -> v1.88.1"
    assert deploys(ops) == []                                   # nothing restarted
    assert ops.notices[-1][1] == "Kronk upgrade: an interrupted run was recovered (litellm)"
    assert "litellm" in st["months"]["2026-12"]["held_back"]
    # The night after, planning goes on as usual, without litellm this month.
    ops.clock = NOW + _dt.timedelta(days=2)
    assert run(ops, sd, only=["litellm", "tempo"]) == 0
    assert subjects(ops.repo)[-1] == "upgrade: tempo 3.1.1 -> 3.1.2"


def test_killed_after_the_tag_before_the_deploy(house):
    ops, sd = house
    undo = kill_before(ops, "deploy_tag", lambda a: a[0] == "deploy")
    killed_run(ops, sd, ["litellm"], undo)
    assert status(sd)["intent"]["step"] == "deploy"
    assert "upgrade-20261201T083000Z-litellm" in git(ops.repo, "tag")
    assert run(ops, sd, only=["litellm"]) == 1
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "recovered" and "pin put back to v1.88.1" in o["state"]
    assert deploys(ops) == []
    assert ops.containers["kronk-litellm-1"].endswith(":v1.88.1")


def test_killed_during_verify_finishes_the_rollback(house):
    ops, sd = house
    undo = kill_before(ops, "run_tests", lambda a: ops.containers["kronk-litellm-1"].endswith("v1.88.6"))
    killed_run(ops, sd, ["litellm"], undo)
    assert ops.containers["kronk-litellm-1"].endswith(":v1.88.6") and status(sd)["intent"]["step"] == "verify"
    assert run(ops, sd, only=["litellm"]) == 1
    assert deploys(ops) == [("deploy_tag", "deploy", "sprint-X")]           # the previous tag
    assert ops.containers["kronk-litellm-1"].endswith(":v1.88.1")
    o, = status(sd)["last_run"]["outcomes"]
    assert o["state"] == "recovered: finished the rollback: v1.88.1 runs again, git has v1.88.1"
    assert "redeployed 1 time(s)" in ops.notices[-1][2]


def test_killed_during_verify_waits_for_a_quiet_house_to_roll_back(house):
    ops, sd = house
    undo = kill_before(ops, "run_tests", lambda a: ops.containers["kronk-litellm-1"].endswith("v1.88.6"))
    killed_run(ops, sd, ["litellm"], undo)
    ops.states[2]["state"] = "playing"
    assert run(ops, sd, only=["litellm"]) == 0
    st = status(sd)
    assert st["intent"]["component"] == "litellm" and st["last_run"]["result"] == "deferred"
    assert deploys(ops) == []
    assert ops.notices[-1][1] == "Kronk upgrade: interrupted (litellm); rollback waiting"
    assert any(a["id"] == "interrupted" for a in st["attention"])
    n = len(ops.notices)
    ops.clock += _dt.timedelta(days=1)
    run(ops, sd, only=["litellm"])
    assert len(ops.notices) == n                                       # one notice, not one a night
    ops.states[2]["state"] = "idle"
    ops.clock += _dt.timedelta(days=1)
    assert run(ops, sd, only=["litellm"]) == 1
    assert ops.containers["kronk-litellm-1"].endswith(":v1.88.1") and "intent" not in status(sd)


def test_grafana_killed_after_the_pin_commit_before_the_pull(house):
    ops, sd = house
    undo = kill_before(ops, "compose", lambda a: a[2] == "pull")
    killed_run(ops, sd, ["grafana"], undo)
    assert run(ops, sd, only=["grafana"]) == 1
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "recovered" and "pin put back to 13.2.4" in o["state"]
    assert U.read_pin(ops.repo, rules().component("grafana")).value == "13.2.4"
    assert not any(c[0] == "compose" and c[3] in ("up", "pull") for c in ops.calls)


def test_ha_killed_after_the_pin_commit_before_the_pull(house):
    ops, sd = house
    undo = kill_before(ops, "docker", lambda a: a[0] == "pull")
    killed_run(ops, sd, ["homeassistant"], undo)
    assert status(sd)["intent"]["ha_old_id"]
    assert run(ops, sd, only=["homeassistant"]) == 1
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "recovered" and "pin put back to 2026.5.4" in o["state"]
    assert not any(c[:2] == ("docker", "stop") for c in ops.calls)


def test_ha_killed_after_the_stop_is_brought_back_on_the_previous_image(house):
    ops, sd = house
    undo = kill_on(ops, "docker", lambda a: a[0] == "stop")
    killed_run(ops, sd, ["homeassistant"], undo)
    assert "homeassistant" in ops.stopped
    # Even with nothing to upgrade and HA unreadable (it is down), the next
    # run brings it back first, at once.
    ops.web.table["https://ghcr.io/v2/home-assistant/home-assistant/tags/list"] = {"tags": ["2026.5.4"]}
    assert run(ops, sd, only=["homeassistant"]) == 1
    assert "homeassistant" not in ops.stopped
    assert ops.containers["homeassistant"] == "ghcr.io/home-assistant/home-assistant:2026.5.4"
    assert U.read_pin(ops.repo, rules().component("homeassistant")).value == "2026.5.4"
    o, = status(sd)["last_run"]["outcomes"]
    assert o["state"] == ("recovered: Home Assistant was down after an interrupted upgrade; it runs 2026.5.4 again, "
                          "git has 2026.5.4")
    assert ops.notices[-1][1] == "Kronk upgrade: an interrupted run was recovered (homeassistant): Home Assistant was down"


def test_killed_in_the_rollback_with_the_pin_restored_but_not_committed(house):
    ops, sd = house
    ops.bad_versions = {"13.2.6"}
    undo = kill_before(ops, "git", lambda a: a[0] == "commit" and "roll back" in " ".join(a))
    killed_run(ops, sd, ["grafana"], undo)
    ops.bad_versions = set()
    assert run(ops, sd, only=["grafana"]) == 2
    st = status(sd)
    o, = st["last_run"]["outcomes"]
    assert o["result"] == "out_of_step"
    assert "the working tree has uncommitted changes (docker-compose.telemetry.yml)" in o["state"]
    assert st["out_of_step"]["component"] == "grafana"
    assert ops.notices[-1][1].startswith("Kronk upgrade: OUT OF STEP (grafana)")
    assert git(ops.repo, "status", "--porcelain").strip()               # left for a person: not touched


def test_a_pin_ahead_of_what_runs_is_never_current(house):
    """No intent record at all (a hand edit, or a record lost): planning
    compares the pin with what runs, every component, every run."""
    ops, sd = house
    p = ops.repo / "docker-compose.yml"
    p.write_text(p.read_text().replace("litellm:v1.88.1", "litellm:v1.88.6"))
    git(ops.repo, "commit", "-qam", "pin bumped by hand, not deployed")
    ops.web.table["https://ghcr.io/v2/berriai/litellm/tags/list"] = {"tags": ["v1.88.1", "v1.88.6"]}
    assert run(ops, sd, only=["litellm"]) == 0
    st = status(sd)
    assert any(a["id"] == "mismatch:2026-12" for a in st["attention"])
    (_, title, body), = ops.notices
    assert title == "Kronk upgrade: pin and running version differ (litellm)"
    assert body.startswith("litellm: pin v1.88.6 (docker-compose.yml) but kronk-litellm-1 runs v1.88.1")
    ops.clock += _dt.timedelta(days=1)
    run(ops, sd, only=["litellm"], ignore_month=True)
    assert len(ops.notices) == 1                                         # once a month


def test_dry_run_reports_an_unfinished_intent_and_changes_nothing(house, capsys):
    ops, sd = house
    undo = kill_on(ops, "docker", lambda a: a[0] == "stop")
    killed_run(ops, sd, ["homeassistant"], undo)
    before = (sd / "status.json").read_text()
    head = git(ops.repo, "rev-parse", "HEAD")
    capsys.readouterr()
    run(ops, sd, only=["homeassistant"], dry_run=True)
    out = capsys.readouterr().out
    assert "stopped inside homeassistant 2026.5.4 -> 2026.5.5 at step 'ha-stop'" in out
    assert "a real run would finish the rollback to 2026.5.4 now (it is down: no wait)" in out
    assert (sd / "status.json").read_text() == before and git(ops.repo, "rev-parse", "HEAD") == head
    assert "homeassistant" in ops.stopped


# ── F5: M12's H7b and H7 checks around HA's restart ──────────────────────

def _stops(ops):
    return [c for c in ops.calls if c[:2] == ("docker", "stop")]


def test_ha_is_not_started_without_the_recordings_directory(house):
    ops, sd = house
    ops.existing_paths.discard("/home/drew/kronk-voice-recordings")
    assert run(ops, sd, only=["homeassistant"]) == 0
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "not_started"
    assert "/home/drew/kronk-voice-recordings (bind-mounted by docker-compose.ha.yml at /voice_recordings) does not exist" in o["detail"]
    assert _stops(ops) == [] and subjects(ops.repo) == ["initial"]


def test_ha_is_not_started_when_its_compose_file_does_not_render(house):
    ops, sd = house
    orig = ops.compose

    def compose(project, file, *args, **kw):
        if args[:3] == ("config", "--format", "json"):
            return U.Result(15, "", "yaml: line 12: did not find expected key")
        return orig(project, file, *args, **kw)
    ops.compose = compose
    run(ops, sd, only=["homeassistant"])
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "not_started" and "does not render" in o["detail"] and _stops(ops) == []


@pytest.mark.parametrize("what", ["directory", "image", "rollback-tag"])
def test_a_precondition_lost_right_before_the_stop_is_never_a_stop(house, what):
    ops, sd = house
    orig = ops.docker

    def docker(*args, timeout=600):
        r = orig(*args, timeout=timeout)
        if args[0] == "pull":
            if what == "directory":
                ops.existing_paths.discard("/home/drew/kronk-voice-recordings")
            elif what == "image":
                ops.images.pop(args[1], None)
            else:
                ops.images["kronk/ha-rollback:2026.5.4"] = "sha256:" + "e" * 64
        return r
    ops.docker = docker
    assert run(ops, sd, only=["homeassistant"]) == 1
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "not_applied" and "M12 H7b" in o["detail"]
    assert {"directory": "does not exist", "image": "is not present locally",
            "rollback-tag": "no rollback image"}[what] in o["detail"]
    assert _stops(ops) == [] and "homeassistant" not in ops.stopped
    assert U.read_pin(ops.repo, rules().component("homeassistant")).value == "2026.5.4"


def _on_ha_up(ops, new, old):
    orig = ops.compose

    def compose(project, file, *args, **kw):
        r = orig(project, file, *args, **kw)
        if args[0] == "up" and project == "kronk-ha":
            (new if ops.containers["homeassistant"].endswith("2026.5.5") else old)()
        return r
    ops.compose = compose


def test_a_mount_missing_after_the_start_rolls_back(house):
    ops, sd = house
    _on_ha_up(ops, lambda: setattr(ops, "ha_mounted", [("/etc/localtime", "/etc/localtime")]),
              lambda: setattr(ops, "ha_mounted", list(ops.ha_binds)))
    assert run(ops, sd, only=["homeassistant"]) == 1
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "rolled_back" and "/home/drew/kronk-voice-recordings is not mounted at /voice_recordings" in o["detail"]
    assert ops.containers["homeassistant"].endswith(":2026.5.4")


def test_an_unclean_shutdown_line_after_the_start_rolls_back(house):
    ops, sd = house
    bad = ("2026-12-01 08:31:02 WARNING (Recorder) [homeassistant.components.recorder.util] The system could not "
           "validate that the sqlite3 database at //config/home-assistant_v2.db was shutdown cleanly\n")
    _on_ha_up(ops, lambda: setattr(ops, "ha_log", bad), lambda: setattr(ops, "ha_log", ""))
    assert run(ops, sd, only=["homeassistant"]) == 1
    o, = status(sd)["last_run"]["outcomes"]
    assert o["result"] == "rolled_back" and "not shut down cleanly" in o["detail"]


# ── F6: a registry that answers nonsense skips that component ────────────

import http.client as _http


class _ReadFails:
    """The answer starts, then the body is cut short."""


def _web_with(prefix, answer):
    w = standard_web()
    if answer is _ReadFails:
        orig = w._fake_open

        def opener(req, timeout=None):
            if req.full_url.startswith(prefix):
                class R:
                    headers = {}

                    def read(self):
                        raise _http.IncompleteRead(b'{"results": [', 4096)

                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        return False
                return R()
            return orig(req, timeout)
        w._open = opener
    else:
        w.table[prefix] = answer
    return w


GH, HUB = "https://api.github.com/repos/", "https://hub.docker.com/v2/repositories/"
SHAPES = [
    ("searxng", HUB + "searxng/searxng/tags?", ["2026.11.17-b99cf7496"]),              # tag list is a JSON list
    ("searxng", HUB + "searxng/searxng/tags?", "rate limited"),                        # a JSON string
    ("searxng", HUB + "searxng/searxng/tags?", {"results": "none"}),                   # results not a list
    ("searxng", HUB + "searxng/searxng/tags?", {"results": [{"name": 7}]}),            # a name that is a number
    ("grafana", GH + "grafana/grafana/releases?", ["v13.2.6"]),                         # a list of non-objects
    ("grafana", GH + "grafana/grafana/releases?", [{"tag_name": 13.26, "published_at": "2026-11-01T00:00:00Z"}]),
    ("grafana", GH + "grafana/grafana/releases?", [{"tag_name": "v13.2.6", "published_at": 1761955200}]),
    ("litellm", "https://ghcr.io/token", ["anon"]),                                     # a token answer that is a list
    ("litellm", "https://ghcr.io/v2/berriai/litellm/tags/list", (b"<html><body>Bad gateway</body></html>", {})),
    ("litellm", "https://ghcr.io/v2/berriai/litellm/tags/list", {"tags": [1, 2]}),
    ("litellm", GH + "BerriAI/litellm/releases/tags/v1.88.6", ["not", "an", "object"]),
    ("grafana", GH + "grafana/grafana/releases?", _ReadFails),                          # IncompleteRead
    ("tempo", HUB + "grafana/tempo/tags/", (b"<!doctype html>", {})),                 # handled before the fix too
    ("tempo", GH + "grafana/tempo/releases?", {"message": "API rate limit exceeded"}),  # handled before the fix too
]


@pytest.mark.parametrize("name, prefix, answer", SHAPES, ids=[f"{n}-{i}" for i, (n, _, _) in enumerate(SHAPES)])
def test_a_malformed_answer_skips_that_component_with_a_reason(tmp_path, name, prefix, answer):
    repo = make_repo(tmp_path, base_files())
    r = rules()
    p = U.safe_plan(r, r.component(name), repo, _web_with(prefix, answer), NOW)
    assert p.action == "skip", (p.action, p.detail)
    assert "lookup failed" in p.detail


@pytest.mark.parametrize("name, prefix, answer", SHAPES[:12], ids=[f"{n}-{i}" for i, (n, _, _) in enumerate(SHAPES[:12])])
def test_the_run_goes_on_past_a_malformed_answer(house, name, prefix, answer):
    ops, sd = house
    ops.web = _web_with(prefix, answer)
    ha_patch_available(ops)
    assert run(ops, sd, only=["tempo", name]) == 0
    st = status(sd)
    assert st["last_run"]["result"] == "upgraded"
    assert [o["name"] for o in st["last_run"]["outcomes"]] == ["tempo"]
    assert f"Skipped: {name} (lookup failed" in ops.notices[0][2]


def test_a_malformed_lockfile_answer_is_a_note(tmp_path):
    (tmp_path / "orchestrator").mkdir()
    (tmp_path / "orchestrator" / "requirements.lock").write_text("fastapi==0.115.0 \\\n    --hash=sha256:a\n")
    w = U.Web(opener=_web_with("https://pypi.org/", {"releases": {"0.115.14": "x"}})._open)
    r = rules()
    p = U.plan_component(r, r.component("lock-orchestrator"), tmp_path, w, NOW)
    assert p.action == "report" and any("fastapi: lookup failed" in n for n in p.notes)


# ── F7: a month that ends with nothing applied says so ───────────────────

def _undeployed_work(ops):
    (ops.repo / "orchestrator").mkdir()
    (ops.repo / "orchestrator" / "main.py").write_text("# new\n")
    git(ops.repo, "add", "-A")
    git(ops.repo, "commit", "-q", "-m", "work not yet deployed")


def test_seven_nights_of_not_started_end_with_one_notice_and_the_warning(house):
    """The tester's K4: HEAD carries undeployed work, so every Kronk-stack
    component is not started, seven nights running."""
    ops, sd = house
    _undeployed_work(ops)
    for night in range(7):
        ops.clock = NOW + _dt.timedelta(days=night)
        assert run(ops, sd, only=["searxng", "litellm"]) == 0
        assert len(ops.notices) == (1 if night == 6 else 0)
    st = status(sd)
    assert st["months"]["2026-12"]["done"] is True
    (_, title, body), = ops.notices
    assert title == "Kronk upgrade: this month's patch run applied nothing"
    assert "7 attempts in 2026-12; applied nothing: every pending component was not started" in body
    assert "HEAD has changes not deployed to production (sprint-X): orchestrator/main.py" in body
    assert any(a["id"] == "declined:2026-12" and "orchestrator/main.py" in a["text"] for a in st["attention"])


def test_seven_nights_without_a_baseline_end_with_one_notice(house):
    ops, sd = house
    ops.tests_fail = True
    for night in range(7):
        ops.clock = NOW + _dt.timedelta(days=night)
        run(ops, sd, only=["tempo"])
    (_, title, body), = ops.notices
    assert title == "Kronk upgrade: this month's patch run applied nothing"
    assert "the baseline (offline corpus, live replay) could not be taken" in body and "offline corpus fails" in body
    assert any(a["id"] == "declined:2026-12" for a in status(sd)["attention"])


def test_a_month_of_mixed_declines_and_not_started_ends_with_one_notice(house):
    ops, sd = house
    _undeployed_work(ops)
    ops.states[2]["state"] = "playing"
    for night in range(7):
        ops.clock = NOW + _dt.timedelta(days=night)
        if night == 6:
            ops.states[2]["state"] = "idle"
        run(ops, sd, only=["litellm"])
    assert [n[1] for n in ops.notices] == ["Kronk upgrade: this month's patch run applied nothing"]


# ── F8: the monthly run works only inside the night window ───────────────

def test_a_boot_catch_up_at_seven_in_the_evening_does_nothing_and_counts_nothing(house):
    ops, sd = house
    ops.clock = NOW.replace(hour=0, minute=0) + _dt.timedelta(days=1)    # 19:00 local on Dec 1
    assert run(ops, sd) == 0
    st = status(sd)
    assert st["last_run"]["result"] == "outside_window"
    assert "outside the night window 02:00-05:00 local (now 19:00)" in st["last_run"]["summary"]
    assert st.get("months", {}).get("2026-12", {}).get("attempts", 0) == 0
    assert ops.web.requests == 0 and _mutating(ops) == [] and ops.notices == []
    ops.clock = NOW + _dt.timedelta(days=1)                               # the next night, 03:30
    assert run(ops, sd, only=["tempo"]) == 0
    assert status(sd)["last_run"]["result"] == "upgraded"


def test_the_window_closing_between_components_leaves_the_rest(house):
    ops, sd = house
    orig = ops.replay

    def replay(label, out, check_only=False):
        r = orig(label, out, check_only)
        if label == "upgrade-tempo":
            ops.clock = NOW.replace(hour=10, minute=1)                    # 05:01 local
        return r
    ops.replay = replay
    run(ops, sd, only=["tempo", "grafana"])
    o = {x["name"]: x for x in status(sd)["last_run"]["outcomes"]}
    assert o["tempo"]["result"] == "kept"
    assert o["grafana"]["result"] == "not_started" and o["grafana"]["detail"].startswith("the night window closed")


def test_a_run_by_hand_can_ignore_the_window_but_not_the_house(house):
    ops, sd = house
    ops.clock = NOW.replace(hour=19)                                      # 14:00 local
    assert run(ops, sd, only=["tempo"], ignore_window=True) == 0
    assert status(sd)["last_run"]["result"] == "upgraded"
    household_chat(ops)
    assert run(ops, sd, only=["grafana"], ignore_window=True, ignore_month=True) == 0
    assert status(sd)["last_run"]["result"] == "declined"


@pytest.mark.parametrize("hhmm, inside", [("23:30", True), ("04:59", True), ("05:00", False), ("12:00", False)])
def test_a_window_may_cross_midnight(house, hhmm, inside):
    ops, _ = house
    r = rules(night_window=["23:00", "05:00"])
    r.settings["night_window_t"] = [_dt.time(23), _dt.time(5)]
    h, m = map(int, hhmm.split(":"))
    ops.local_now = lambda: _dt.datetime(2026, 12, 1, h, m)
    assert (U.outside_window(ops, r) is None) is inside


def test_the_rules_refuse_a_window_that_is_not_two_times(tmp_path):
    f = tmp_path / "r.toml"
    f.write_text('[settings]\nnight_window = ["02:00"]\n')
    with pytest.raises(U.RulesError, match="night_window"):
        U.load_rules(f, env={})


def test_a_down_ha_is_brought_back_at_any_hour_but_a_redeploy_waits_for_the_night(house):
    ops, sd = house
    undo = kill_before(ops, "run_tests", lambda a: ops.containers["kronk-litellm-1"].endswith("v1.88.6"))
    killed_run(ops, sd, ["litellm"], undo)
    ops.clock = NOW.replace(hour=19)                                      # 14:00 local
    assert run(ops, sd, only=["litellm"]) == 0
    assert status(sd)["last_run"]["result"] == "deferred" and deploys(ops) == []
    assert "night window" in status(sd)["last_run"]["summary"]


def test_user_units_carry_no_ordering_on_system_units():
    for f in (ROOT / "systemd").glob("kronk-upgrade-*.service"):
        text = f.read_text()
        assert "docker.service" not in text.split("[Unit]", 1)[1], f.name
        assert "network-online.target" not in text.split("[Unit]", 1)[1], f.name
