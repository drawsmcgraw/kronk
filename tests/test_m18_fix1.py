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
