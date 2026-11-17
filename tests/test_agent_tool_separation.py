"""Web tools and remote exec never share an agent loop (sprint M4).

Decided by Drew 2026-11-09 (plan "M4. Separate web tools from remote exec",
option A): a fetched page can carry instructions, and the next round of the
same loop could run a command. The devops agent keeps only remote_exec;
documentation reaches a devops task through the coordinator (ask_research,
then ask_devops). Two layers, both tested here:

1. Configuration: no agent's tool list holds a web tool and remote_exec.
2. The loop: run_stream rejects any tool the agent was not offered, so a
   model that emits web_search inside the devops loop gets a rejection, not
   a search.
"""
import os
from unittest.mock import patch

import pytest

# Same defaults as test_agentic_loop.py. This module imports agents (and so
# tools) at collection time, before that file runs; tools reads
# TOOL_SERVICE_URL at import, and other test files assert the fake host.
os.environ.setdefault("LLM_SERVICE_URL",   "http://fake-llm:8002")
os.environ.setdefault("TOOL_SERVICE_URL",  "http://fake-tools:8003")
os.environ.setdefault("COORDINATOR_MODEL", "gemma-4-e4b")

import agents  # noqa: E402

WEB_TOOLS = {"web_search", "fetch_url"}
# Tools that reach managed hosts or the LAN. http_check joined in sprint M5:
# it is exempt from the /fetch address block, so a web-reading loop must
# never hold it either.
EXEC_TOOLS = {"remote_exec", "http_check"}


def _all_agents():
    return list(agents.AGENTS.values()) + [agents.COORDINATOR]


# ── configuration ────────────────────────────────────────────────────────────

def test_devops_holds_remote_exec_and_http_check_only():
    # M4 left devops with remote_exec alone; M5 added http_check.
    devops = agents.AGENTS["devops"]
    assert devops.tool_names == ["remote_exec", "http_check"]
    assert [d["function"]["name"] for d in devops.tool_defs()] == ["remote_exec", "http_check"]


def test_only_devops_holds_http_check():
    """Sprint M5, criterion 13: no other agent, and no agent with web tools."""
    holders = [a.name for a in _all_agents()
               if "http_check" in a.tool_names
               or "http_check" in {d["function"]["name"] for d in a.tool_defs()}]
    assert holders == ["devops"]


def test_devops_prompt_does_not_offer_web_lookups():
    prompt = agents.AGENTS["devops"].system_prompt
    assert "web_search" not in prompt
    assert "fetch_url" not in prompt
    assert "look up" not in prompt.lower()


def test_other_agents_web_tools_unchanged():
    assert set(agents.AGENTS["research"].tool_names) == WEB_TOOLS
    assert set(agents.AGENTS["coding"].tool_names) == WEB_TOOLS
    assert "remote_exec" not in agents.AGENTS["coding"].tool_names


@pytest.mark.parametrize("agent", _all_agents(), ids=lambda a: a.name)
def test_no_agent_holds_web_tools_and_remote_exec(agent):
    """Fails the suite if a future edit recombines them on any agent,
    checked on both the declared names and the definitions sent to the LLM."""
    for names in (set(agent.tool_names),
                  {d["function"]["name"] for d in agent.tool_defs()}):
        assert not (names & WEB_TOOLS and names & EXEC_TOOLS), (
            f"{agent.name} holds {sorted(names & WEB_TOOLS)} and "
            f"{sorted(names & EXEC_TOOLS)} in one loop")


# ── the loop ─────────────────────────────────────────────────────────────────

def _scripted_stream(first_round_calls, answer="Done."):
    """Fake llm.stream: round 1 emits the given tool calls, round 2 answers.
    Records the messages each round saw."""
    seen = []

    async def fake_stream(messages, model, tools=None):
        seen.append(list(messages))
        if len(seen) == 1:
            yield {"tool_calls": first_round_calls}
        else:
            yield {"token": answer}
        yield {"usage": {}}

    return fake_stream, seen


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", [
    ("web_search", {"query": "pm2 logs nostream flag"}),
    ("fetch_url", {"url": "https://pm2.keymetrics.io/docs/usage/log-management/"}),
])
async def test_devops_loop_rejects_web_tool(tool, args):
    fake_stream, seen = _scripted_stream(
        [{"id": "c1", "function": {"name": tool, "arguments": args}}])
    executed = []

    async def fake_execute(name, a):
        executed.append(name)
        return "should not run"

    with patch("agents.llm.stream", new=fake_stream), \
         patch("agents.tools.execute", new=fake_execute), \
         patch("agents.emit") as emit:
        events = [ev async for ev in agents.run_stream(
            agents.AGENTS["devops"], "how do I read pm2 logs?", [])]

    assert executed == []                      # the web tool never ran
    tool_msgs = [m for m in seen[1] if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert "REJECTED" in tool_msgs[0]["content"]
    assert "not available to the devops agent" in tool_msgs[0]["content"]
    emit.assert_any_call("tool_rejected", agent="devops", tool=tool)
    # No narration announces a search that never happened; the turn still ends.
    assert not any(e["type"] == "narration" for e in events)
    assert events[-1] == {"type": "done", "model": agents.AGENTS["devops"].model, "ok": True}


@pytest.mark.asyncio
async def test_devops_loop_still_runs_remote_exec_next_to_a_rejected_call():
    """A rejected call in the same round does not block the tool it holds."""
    fake_stream, seen = _scripted_stream([
        {"id": "c1", "function": {"name": "web_search", "arguments": {"query": "x"}}},
        {"id": "c2", "function": {"name": "remote_exec",
                                  "arguments": {"command": "uptime", "host": "magicmirror"}}},
    ])
    executed = []

    async def fake_execute(name, a):
        executed.append(name)
        return agents.tools.ToolResult("[TOOL: remote_exec — magicmirror]\nexit=0\nup 3 days")

    with patch("agents.llm.stream", new=fake_stream), \
         patch("agents.tools.execute", new=fake_execute):
        [ev async for ev in agents.run_stream(agents.AGENTS["devops"], "uptime?", [])]

    assert executed == ["remote_exec"]
    tool_msgs = {m["tool_call_id"]: m["content"] for m in seen[1] if m["role"] == "tool"}
    assert "REJECTED" in tool_msgs["c1"]
    assert "up 3 days" in tool_msgs["c2"]


@pytest.mark.asyncio
async def test_specialist_cannot_delegate_with_an_ask_tool():
    """The same check holds the depth-2 cap: only the coordinator is offered
    ask_* tools, so a specialist emitting one is rejected, not delegated."""
    fake_stream, seen = _scripted_stream(
        [{"id": "c1", "function": {"name": "ask_research",
                                   "arguments": {"query": "pm2 docs"}}}])

    async def no_delegation(*a, **kw):
        raise AssertionError("a specialist must not delegate")

    with patch("agents.llm.stream", new=fake_stream), \
         patch("agents.run_delegated", new=no_delegation):
        events = [ev async for ev in agents.run_stream(agents.AGENTS["devops"], "x", [])]

    assert not any(e["type"] == "delegating" for e in events)
    assert "REJECTED" in [m for m in seen[1] if m["role"] == "tool"][0]["content"]


def test_coordinator_offers_both_halves_of_the_chain():
    """The chain the plan relies on: research for documentation, then devops."""
    names = {d["function"]["name"] for d in agents.COORDINATOR.tool_defs()}
    assert {"ask_research", "ask_devops"} <= names


def test_ask_devops_description_covers_the_mirror_and_the_chain():
    """What the coordinator reads when choosing a specialist. Without the
    mirror clause, "check the uptime on the mirror" went to ask_home 5/5 on
    dev (home's description ends "updating the magic mirror"), and home has
    no way to read uptime. The same sentence names updating and upgrading as
    home's: without it, "please upgrade the mirror to the latest version"
    went to ask_devops 9/10 (M4 fix 1). The last sentence is how docs reach
    devops (M4). Substrings only; the routing evidence is the dev A/B."""
    desc = next(d["function"]["description"] for d in agents.COORDINATOR.tool_defs()
                if d["function"]["name"] == "ask_devops")
    assert "magic mirror" in desc and "uptime" in desc
    assert "never updating or upgrading it: that is ask_home" in desc
    home = next(d["function"]["description"] for d in agents.COORDINATOR.tool_defs()
                if d["function"]["name"] == "ask_home")
    assert "updating or upgrading the magic mirror" in home
    assert "no web access" in desc and "ask research first" in desc
