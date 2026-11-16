"""ClaudeCodeModelClient — HIVE on the owner's Claude plan via the `claude` CLI.

Every test injects a fake process runner, so nothing here calls the real CLI or
uses any plan quota. What's pinned down: the isolation flags, least-privilege
tool mapping, prompt on stdin, no API key reaching the child process, result
parsing, and every failure becoming ModelUnavailable / ModelRefusal (which the
coordinator escalates to the owner).
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from hive.agents.model import (
    DEFAULT_EFFORT,
    DEFAULT_ROUTING,
    ClaudeCodeModelClient,
    ModelRefusal,
    ModelUnavailable,
)
from hive.artifacts import EscalationReason
from hive.config import HiveConfig
from hive.jobs.models import JobState
from hive.runtime import Runtime
from tests.conftest import EXAMPLE_ADAPTER, REPO_ROOT
from tests.test_pipeline import make_lead_event

FAKE_BIN = "C:/fake/claude.exe"


def _result(**overrides):
    payload = {
        "type": "result", "subtype": "success", "is_error": False,
        "result": "ready", "total_cost_usd": 0.0123,
        "usage": {"input_tokens": 40, "cache_creation_input_tokens": 10,
                  "cache_read_input_tokens": 5, "output_tokens": 7},
    }
    payload.update(overrides)
    return json.dumps(payload)


class FakeRun:
    """Stands in for subprocess.run; records each call."""

    def __init__(self, stdout="", returncode=0, stderr="", raises=None):
        self.stdout, self.returncode, self.stderr, self.raises = stdout, returncode, stderr, raises
        self.calls: list[dict] = []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": list(argv), **kwargs})
        if self.raises:
            raise self.raises
        return SimpleNamespace(stdout=self.stdout, stderr=self.stderr, returncode=self.returncode)


def make_client(run, which=lambda name: FAKE_BIN):
    return ClaudeCodeModelClient(run=run, which=which)


def _flag(argv, name):
    return argv[argv.index(name) + 1]


@pytest.mark.parametrize("tier", sorted(DEFAULT_ROUTING))
def test_command_isolates_the_call_and_routes_by_tier(tier):
    run = FakeRun(stdout=_result())
    make_client(run).complete(tier, "You are HIVE's agent.", "Reply with: ready")
    call = run.calls[0]
    argv = call["argv"]
    assert argv[0] == FAKE_BIN and argv[1] == "-p"
    assert _flag(argv, "--output-format") == "json"
    assert _flag(argv, "--model") == DEFAULT_ROUTING[tier]
    assert _flag(argv, "--effort") == DEFAULT_EFFORT[tier]
    assert _flag(argv, "--system-prompt") == "You are HIVE's agent."
    for flag in ("--safe-mode", "--strict-mcp-config", "--disable-slash-commands",
                 "--no-session-persistence"):
        assert flag in argv, flag
    assert _flag(argv, "--tools") == ""          # no tools unless granted
    assert "--allowedTools" not in argv
    assert "--fallback-model" not in argv          # never a silent model switch
    assert "Reply with: ready" not in argv         # the prompt goes in on stdin
    assert call["input"] == "Reply with: ready"


def test_runs_in_an_empty_temp_folder_not_the_repo():
    run = FakeRun(stdout=_result())
    make_client(run).complete("specialist", "sys", "hi")
    workdir = Path(run.calls[0]["cwd"])
    assert workdir.name.startswith("hive-claude-")
    assert REPO_ROOT not in workdir.parents
    assert not workdir.exists()  # cleaned up after the call


def test_api_keys_never_reach_the_cli(monkeypatch):
    """With a key in the environment the CLI would bill the API, not the plan."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "should-not-leak")
    monkeypatch.setenv("HIVE_SOMETHING", "kept")
    run = FakeRun(stdout=_result())
    make_client(run).complete("specialist", "sys", "hi")
    env = run.calls[0]["env"]
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["HIVE_SOMETHING"] == "kept"


def test_only_granted_mapped_tools_are_switched_on():
    run = FakeRun(stdout=_result())
    make_client(run).complete("specialist", "sys", "scan", tools=["web_search", "vault_read", "bash"])
    argv = run.calls[0]["argv"]
    assert _flag(argv, "--tools") == "WebSearch"
    assert _flag(argv, "--allowedTools") == "WebSearch"


def test_parses_text_tokens_and_reported_cost():
    run = FakeRun(stdout=_result(result="Here is the brief."))
    response = make_client(run).complete("specialist", "sys", "hi")
    assert response.text == "Here is the brief."
    assert response.model == "claude-code/claude-sonnet-5-5"
    assert (response.input_tokens, response.output_tokens) == (55, 7)  # cache tokens count as input
    assert response.usd == pytest.approx(0.0123)  # API-equivalent cost from Claude Code


def test_estimates_cost_when_the_cli_reports_none():
    run = FakeRun(stdout=_result(total_cost_usd=None))
    assert make_client(run).complete("specialist", "sys", "hi").usd > 0


@pytest.mark.parametrize("run, needle", [
    (FakeRun(stdout=_result(is_error=True, subtype="error_during_execution",
                            result="Claude usage limit reached")), "usage limit"),
    (FakeRun(stdout="", returncode=1, stderr="Invalid API key · Please run /login"), "login"),
    (FakeRun(stdout="not json at all", returncode=0), "no result"),
    (FakeRun(raises=subprocess.TimeoutExpired(cmd="claude", timeout=600)), "timed out"),
    (FakeRun(raises=FileNotFoundError("claude")), "could not start"),
])
def test_failures_become_model_unavailable(run, needle):
    with pytest.raises(ModelUnavailable) as caught:
        make_client(run).complete("planner", "sys", "hi")
    assert needle in str(caught.value)


def test_missing_cli_is_model_unavailable():
    run = FakeRun(stdout=_result())
    with pytest.raises(ModelUnavailable, match="not found"):
        make_client(run, which=lambda name: None).complete("planner", "sys", "hi")
    assert run.calls == []


def test_refusal_raises_model_refusal():
    run = FakeRun(stdout=_result(stop_reason="refusal", result="partial",
                                 stop_details={"category": "cyber", "explanation": "x"}))
    with pytest.raises(ModelRefusal) as caught:
        make_client(run).complete("planner", "sys", "hi")
    assert caught.value.category == "cyber"
    assert caught.value.response.text == ""


def test_backend_selected_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HIVE_MODEL_BACKEND", "claude-code")
    monkeypatch.setenv("HIVE_CLAUDE_BIN", "C:/tools/claude.exe")
    config = HiveConfig.from_env()
    assert config.model_backend == "claude_code"
    assert config.use_llm is False  # runs on the plan, not paid API credits
    rt = Runtime(config.model_copy(update={"adapter_dir": EXAMPLE_ADAPTER,
                                           "data_dir": tmp_path / ".hive"}))
    assert isinstance(rt.model, ClaudeCodeModelClient)
    assert rt.model.claude_bin == "C:/tools/claude.exe"


class UnavailableModel:
    routing = dict(DEFAULT_ROUTING)

    def complete(self, tier, system, prompt, tools=None):
        raise ModelUnavailable("claude-sonnet-5-5 via Claude Code failed: Claude usage limit reached")


def test_coordinator_escalates_when_backend_unavailable(tmp_path):
    rt = Runtime(HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive"),
                 model=UnavailableModel())
    job = rt.trigger(make_lead_event())
    assert job.state is JobState.ESCALATED
    escalation = rt.coordinator.escalations.pending()[-1]
    assert escalation.reason is EscalationReason.MODEL_UNAVAILABLE
    assert "usage limit" in escalation.question
    events = [entry["type"] for entry in rt.trace.read(job.id)]
    assert "model_unavailable" in events
    assert rt.store.list_cards(status="pending") == []
