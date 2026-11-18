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
