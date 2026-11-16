"""AnthropicModelClient against a fake SDK (no network, no API key, $0).

Covers what the move to the current Claude models (Opus 5.5 / Sonnet 5.5 /
Haiku 5.5 / Fable 5.2) changed: routing, explicit per-tier effort, per-request
pricing, and refusals raising instead of leaking empty or partial output —
plus the coordinator turning a refusal into an owner escalation.
"""

import sys
import types
from types import SimpleNamespace

import pytest

from hive.agents.model import (
    DEFAULT_EFFORT,
    DEFAULT_ROUTING,
    AnthropicModelClient,
    ModelRefusal,
    ModelResponse,
    _estimate_usd,
)
from hive.artifacts import EscalationReason
from hive.config import HiveConfig
from hive.jobs.models import JobState
from hive.runtime import Runtime
from tests.conftest import EXAMPLE_ADAPTER
from tests.test_pipeline import make_lead_event


def _response(stop_reason="end_turn", text="ok", input_tokens=100, output_tokens=20,
              stop_details=None):
    content = [SimpleNamespace(type="text", text=text)] if text else []
    return SimpleNamespace(
        content=content, stop_reason=stop_reason, stop_details=stop_details,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


@pytest.fixture
def fake_sdk(monkeypatch):
    """Install a fake `anthropic` module; returns a setter for canned responses."""
    holder = {}

    def install(*responses):
        messages = FakeMessages(responses)
        holder["messages"] = messages
        module = types.ModuleType("anthropic")
        module.Anthropic = lambda: SimpleNamespace(messages=messages)
        monkeypatch.setitem(sys.modules, "anthropic", module)
        return messages

    return install


def test_routing_points_at_current_models():
    assert DEFAULT_ROUTING == {
        "planner": "claude-opus-5-5",
        "specialist": "claude-sonnet-5-5",
        "extractor": "claude-haiku-5-5",
        "frontier": "claude-fable-5-2",
    }
    assert set(DEFAULT_EFFORT) == set(DEFAULT_ROUTING)


@pytest.mark.parametrize("tier", sorted(DEFAULT_ROUTING))
def test_request_shape_per_tier(fake_sdk, tier):
    messages = fake_sdk(_response())
    response = AnthropicModelClient().complete(tier, "sys", "hello")
    sent = messages.requests[0]
    assert sent["model"] == DEFAULT_ROUTING[tier]
    assert sent["output_config"] == {"effort": DEFAULT_EFFORT[tier]}
    assert sent["thinking"] == {"type": "adaptive"}  # can't be disabled on these models
    for rejected in ("temperature", "top_p", "top_k", "tool_choice", "fallbacks"):
        assert rejected not in sent
    assert response.text == "ok"
    assert response.model == DEFAULT_ROUTING[tier]


def test_web_search_tool_passed_through(fake_sdk):
    messages = fake_sdk(_response())
    AnthropicModelClient().complete("specialist", "sys", "scan", tools=["web_search", "vault_read"])
    assert messages.requests[0]["tools"] == [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}
    ]


def test_pause_turn_continues_and_prices_each_request(fake_sdk):
    messages = fake_sdk(
        _response(stop_reason="pause_turn", text="", input_tokens=1000, output_tokens=100),
        _response(text="done", input_tokens=1500, output_tokens=200),
    )
    response = AnthropicModelClient().complete("specialist", "sys", "scan")
    assert len(messages.requests) == 2
    assert messages.requests[1]["messages"][1]["role"] == "assistant"
    assert response.text == "done"
    assert (response.input_tokens, response.output_tokens) == (2500, 300)
    expected = _estimate_usd("claude-sonnet-5-5", 1000, 100) + _estimate_usd("claude-sonnet-5-5", 1500, 200)
    assert response.usd == pytest.approx(expected)


def test_counts_web_searches_across_continuations(fake_sdk):
    first = _response(stop_reason="pause_turn", text="")
    first.usage.server_tool_use = SimpleNamespace(web_search_requests=3)
    second = _response(text="done")
    second.usage.server_tool_use = SimpleNamespace(web_search_requests=2)
    fake_sdk(first, second)
    response = AnthropicModelClient().complete("specialist", "sys", "scan", tools=["web_search"])
    assert response.web_searches == 5


def test_no_server_tool_usage_means_no_searches(fake_sdk):
    fake_sdk(_response())  # usage has no server_tool_use at all
    assert AnthropicModelClient().complete("specialist", "sys", "hi").web_searches == 0


def test_refusal_raises_with_category_and_spend(fake_sdk):
    details = SimpleNamespace(category="cyber", explanation="declined by classifier")
    fake_sdk(_response(stop_reason="refusal", text="partial answer", stop_details=details))
    with pytest.raises(ModelRefusal) as caught:
        AnthropicModelClient().complete("planner", "sys", "hello")
    refusal = caught.value
    assert refusal.model == "claude-opus-5-5"
    assert refusal.category == "cyber"
    assert refusal.explanation == "declined by classifier"
    assert refusal.response.text == ""  # partial output is never passed on
    assert refusal.response.total_tokens == 120
    assert refusal.response.usd > 0


def test_refusal_without_stop_details(fake_sdk):
    fake_sdk(_response(stop_reason="refusal", text=""))
    with pytest.raises(ModelRefusal) as caught:
        AnthropicModelClient().complete("specialist", "sys", "hello")
    assert caught.value.category is None


def test_pricing_current_models():
    per_million = lambda model: _estimate_usd(model, 1_000_000, 1_000_000)
    assert per_million("claude-opus-5-5") == pytest.approx(24.00)
    assert per_million("claude-sonnet-5-5") == pytest.approx(12.00)
    assert per_million("claude-fable-5-2") == pytest.approx(48.00)
    # Haiku 5.5: $0.10/$0.50 up to a 100K-token prompt, $0.50/$2.50 above it
    assert _estimate_usd("claude-haiku-5-5", 100_000, 0) == pytest.approx(0.01)
    assert _estimate_usd("claude-haiku-5-5", 200_000, 0) == pytest.approx(0.10)
    # unknown models are priced high (Opus rates), never free
    assert per_million("some-future-model") == pytest.approx(30.00)


class RefusingModel:
    """Model double: the research step's model declines."""

    routing = dict(DEFAULT_ROUTING)

    def complete(self, tier, system, prompt, tools=None):
        if tier == "planner":
            return ModelResponse(text="VERDICT: pass — fine", model="claude-opus-5-5",
                                 input_tokens=10, output_tokens=5, usd=0.0001)
        raise ModelRefusal(
            "claude-sonnet-5-5", category="general_harms", explanation="declined",
            response=ModelResponse(text="", model="claude-sonnet-5-5",
                                   input_tokens=300, output_tokens=0, usd=0.0006,
                                   stop_reason="refusal"),
        )


def test_coordinator_escalates_on_refusal(tmp_path):
    rt = Runtime(HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive"),
                 model=RefusingModel())
    job = rt.trigger(make_lead_event())

    assert job.state is JobState.ESCALATED
    assert "declined" in job.error
    escalation = rt.coordinator.escalations.pending()[-1]
    assert escalation.reason is EscalationReason.MODEL_REFUSAL
    assert "general_harms" in escalation.question
    # the refused call's tokens are still attributed to the job
    assert job.spend_tokens >= 300
    events = [entry["type"] for entry in rt.trace.read(job.id)]
    assert "model_refusal" in events
    # nothing went out for approval on a refused job
    assert rt.store.list_cards(status="pending") == []
