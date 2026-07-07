"""Coordinator review loop: pass, rework-then-pass, exhaustion escalates,
unparsed fails open, and review can be disabled per adapter."""

import pytest

from hive.agents.model import REVIEW_PROMPT_PREFIX, StubModelClient
from hive.config import HiveConfig
from hive.events.bus import Event
from hive.jobs.models import JobState
from hive.runtime import Runtime
from tests.conftest import EXAMPLE_ADAPTER


class ScriptedReviewClient(StubModelClient):
    """Stub that answers review prompts from a script (then falls back to pass)."""

    def __init__(self, review_replies: list[str]) -> None:
        super().__init__()
        self.review_replies = list(review_replies)

    def complete(self, tier, system, prompt, tools=None):
        response = super().complete(tier, system, prompt, tools)
        # Key on the review prompt's prefix — NOT on "VERDICT:" appearing in the
        # prompt, because rework prompts embed the reviewer's feedback text.
        if prompt.startswith(REVIEW_PROMPT_PREFIX) and self.review_replies:
            return response.model_copy(update={"text": self.review_replies.pop(0)})
        return response


def lead() -> Event:
    return Event(type="lead.new", metadata={"subject": "Acme", "raw_context": "2400 sq yd"})


def make_runtime(tmp_path, model) -> Runtime:
    config = HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive")
    return Runtime(config, model=model)


def trace_types(rt, job) -> list[str]:
    return [e["type"] for e in rt.trace.read(job.id)]


def test_happy_path_reviews_every_specialist_step(runtime):
    job = runtime.trigger(lead())
    assert job.state is JobState.AWAITING_APPROVAL
    entries = runtime.trace.read(job.id)
    reviews = [e for e in entries if e["type"] == "review"]
    assert [r["payload"]["step"] for r in reviews] == ["enrich", "draft"]
    assert all(r["payload"]["verdict"] == "pass" for r in reviews)
    assert len(job.artifact_ids) == 4  # no reworks: plan, brief, draft, deliverable


def test_revise_triggers_rework_then_passes(tmp_path):
    model = ScriptedReviewClient([
        "VERDICT: pass — brief fine.",                       # enrich review
        "VERDICT: revise — missing per-sq-yd pricing.",      # draft review #1
        "VERDICT: pass — pricing added.",                    # draft review #2 (rework)
    ])
    rt = make_runtime(tmp_path, model)
    job = rt.trigger(lead())
    assert job.state is JobState.AWAITING_APPROVAL
    assert "rework" in trace_types(rt, job)
    # the rework produced an extra Draft: plan, brief, draft v1, draft v2, deliverable
    assert len(job.artifact_ids) == 5
    # the specialist actually saw the feedback
    rework_prompts = [p for _, p in model.calls if "Reviewer feedback" in p]
    assert any("per-sq-yd pricing" in p for p in rework_prompts)


def test_exhausted_reworks_escalate_never_accept(tmp_path):
    model = ScriptedReviewClient([
        "VERDICT: revise — brief is vague.",   # enrich review #1
        "VERDICT: revise — still vague.",      # enrich review #2 (after 1 rework = cap)
    ])
    rt = make_runtime(tmp_path, model)
    job = rt.trigger(lead())
    assert job.state is JobState.ESCALATED
    assert "failed review" in job.error
    pending = rt.coordinator.escalations.pending()
    assert pending and pending[0].reason.value == "low_confidence"


def test_unparsed_verdict_fails_open_but_is_traced(tmp_path):
    model = ScriptedReviewClient([
        "Looks good to me!",   # no VERDICT marker at all
        "Ship it.",
    ])
    rt = make_runtime(tmp_path, model)
    job = rt.trigger(lead())
    assert job.state is JobState.AWAITING_APPROVAL  # fail-open: job proceeds
    reviews = [e for e in rt.trace.read(job.id) if e["type"] == "review"]
    assert all(r["payload"]["verdict"] == "unparsed" for r in reviews)


def test_review_can_be_disabled(tmp_path):
    rt = make_runtime(tmp_path, StubModelClient())
    rt.adapter.policies["review"] = {"enabled": False}
    job = rt.trigger(lead())
    assert job.state is JobState.AWAITING_APPROVAL
    assert "review" not in trace_types(rt, job)
    # only 3 model calls: plan check, brief, draft — no review calls
    assert len(rt.model.calls) == 3
