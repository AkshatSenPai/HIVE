"""Action layer: tool registry, least-privilege enforcement, web search wiring."""

import pytest

from hive.actions.registry import ToolNotPermitted, anthropic_tool_defs
from hive.agents.base import Agent, AgentContext
from hive.agents.model import DEFAULT_ROUTING, ModelResponse, StubModelClient
from hive.agents.research import ResearchAgent
from hive.config import HiveConfig
from hive.events.bus import Event
from hive.observability.trace import TraceWriter
from hive.policy.budgets import Budget, BudgetExceeded, KillSwitch
from hive.runtime import Runtime
from tests.conftest import REPO_ROOT


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


class FixedModel:
    """Returns one canned response with a chosen cost and search count."""

    routing = dict(DEFAULT_ROUTING)

    def __init__(self, usd: float = 0.0, web_searches: int = 0) -> None:
        self.usd, self.web_searches = usd, web_searches
        self.prompts: list[str] = []

    def complete(self, tier, system, prompt, tools=None):
        self.prompts.append(prompt)
        return ModelResponse(text="brief text", model="claude-sonnet-5-5", input_tokens=70_000,
                             output_tokens=4_000, usd=self.usd, web_searches=self.web_searches)


def _ctx(tmp_path, model, budget=None, tools=("web_search",)) -> AgentContext:
    return AgentContext(job_id="job_t", model=model, budget=budget or Budget(),
                        kill_switch=KillSwitch(), trace=TraceWriter(tmp_path / "traces"),
                        tools=list(tools))


def test_call_that_crosses_the_cap_is_still_traced(tmp_path):
    """First live scan, 2026-11-16: the $0.37 research call crossed the $0.32
    cap and was missing from the trace. Trace first, then charge."""
    ctx = _ctx(tmp_path, FixedModel(usd=0.37, web_searches=6), budget=Budget(max_usd=0.32))
    with pytest.raises(BudgetExceeded):
        Agent().ask_model(ctx, "scan", tools=["web_search"])
    calls = [e for e in ctx.trace.read("job_t") if e["type"] == "model_call"]
    assert len(calls) == 1
    assert calls[0]["usd"] == pytest.approx(0.37)
    assert calls[0]["payload"]["web_searches"] == 6
    assert ctx.budget.usd == pytest.approx(0.37)  # still charged to the job


def test_research_gets_the_sop_instructions(tmp_path):
    config = HiveConfig(adapter_dir=REPO_ROOT / "adapters" / "venture-studio",
                        data_dir=tmp_path / ".hive")
    rt = Runtime(config, model=StubModelClient())
    rt.trigger(Event(type="owner.request", metadata={"subject": "viable apps"}))
    research = [p for _, p in rt.model.calls if p.startswith("Prepare a research brief")]
    assert research, "research never ran"
    assert "Do the 'scan' step." in research[0]
    assert "## scan (Research)" in research[0]
    assert "demand signal" in research[0]


@pytest.mark.parametrize("searches, cited", [(0, False), (3, True)])
def test_brief_cites_web_search_only_when_a_search_ran(tmp_path, searches, cited):
    ctx = _ctx(tmp_path, FixedModel(web_searches=searches))
    brief = ResearchAgent().perform(ctx, {"subject": "viable apps", "source": "owner:cli"})
    assert ("web_search" in brief.sources) is cited
    assert brief.sources[0] == "owner:cli"


def test_research_degrades_without_web_search(runtime):
    """The example adapter does NOT grant web_search — research must not request it."""
    runtime.trigger(Event(type="lead.new", metadata={"subject": "Acme", "raw_context": "hi"}))
    assert runtime.model.tool_requests == []
