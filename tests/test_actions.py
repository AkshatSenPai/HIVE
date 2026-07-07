"""Action layer: tool registry, least-privilege enforcement, web search wiring."""

import pytest

from hive.actions.registry import ToolNotPermitted, anthropic_tool_defs
from hive.agents.base import Agent, AgentContext
from hive.agents.model import StubModelClient
from hive.events.bus import Event
from hive.observability.trace import TraceWriter
from hive.policy.budgets import Budget, KillSwitch


def make_ctx(tmp_path, tools: list[str]) -> AgentContext:
    return AgentContext(
        job_id="job_t", model=StubModelClient(), budget=Budget(),
        kill_switch=KillSwitch(), trace=TraceWriter(tmp_path / "traces"),
        tools=tools,
    )


def test_registry_maps_web_search():
    defs = anthropic_tool_defs(["web_search"])
    assert defs == [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}]


def test_registry_skips_non_server_tools():
    # vault_read is granted in tools.yaml but has no server-side spec yet
    assert anthropic_tool_defs(["vault_read"]) == []


def test_agent_cannot_request_ungranted_tool(tmp_path):
    ctx = make_ctx(tmp_path, tools=[])  # nothing granted
    agent = Agent()
    with pytest.raises(ToolNotPermitted):
        agent.ask_model(ctx, "hi", tools=["web_search"])


def test_agent_can_use_granted_tool(tmp_path):
    ctx = make_ctx(tmp_path, tools=["web_search"])
    agent = Agent()
    agent.ask_model(ctx, "hi", tools=["web_search"])
    assert ctx.model.tool_requests == [("specialist", ["web_search"])]


def test_research_uses_web_search_in_venture_studio(tmp_path):
    """End-to-end: the venture-studio adapter grants research web_search,
    and the market scan actually requests it."""
    from hive.config import HiveConfig
    from hive.runtime import Runtime
    from tests.conftest import REPO_ROOT

    config = HiveConfig(adapter_dir=REPO_ROOT / "adapters" / "venture-studio",
                        data_dir=tmp_path / ".hive")
    rt = Runtime(config, model=StubModelClient())
    rt.trigger(Event(type="owner.request", metadata={"subject": "viable apps"}))
    assert ("specialist", ["web_search"]) in rt.model.tool_requests


def test_research_degrades_without_web_search(runtime):
    """The example adapter does NOT grant web_search — research must not request it."""
    runtime.trigger(Event(type="lead.new", metadata={"subject": "Acme", "raw_context": "hi"}))
    assert runtime.model.tool_requests == []
