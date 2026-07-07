"""End-to-end: event -> plan -> research -> maker -> approval card -> decision."""

from hive.events.bus import Event
from hive.governance.approvals import ApprovalStatus
from hive.jobs.models import JobState


def make_lead_event() -> Event:
    return Event(
        type="lead.new",
        metadata={
            "subject": "Acme — office fit-out",
            "raw_context": "We need a 2,400 sq yd fit-out. Please send a proposal.",
            "source": "email:test",
            "reply_to": "buyer@acme.example",  # approve => send executes for real
        },
    )


def test_job_runs_to_awaiting_approval(runtime):
    job = runtime.trigger(make_lead_event())
    assert job is not None
    assert job.state is JobState.AWAITING_APPROVAL
    assert job.spend_tokens > 0
    assert job.spend_usd > 0
    # plan + brief + draft + deliverable were produced and registered
    assert len(job.artifact_ids) == 4


def test_untrusted_context_is_fenced_before_model(runtime):
    runtime.trigger(make_lead_event())
    research_prompts = [p for tier, p in runtime.model.calls if "research brief" in p.lower()]
    assert research_prompts, "research agent never ran"
    assert "<external-content" in research_prompts[0]


def test_approval_card_created_and_approve_finishes_job(runtime):
    job = runtime.trigger(make_lead_event())
    cards = runtime.store.list_cards(status=ApprovalStatus.PENDING.value)
    assert len(cards) == 1
    card = cards[0]
    assert card.job_id == job.id
    assert card.action_kind == "send"
    assert card.step_key == "lead_to_proposal.send"

    finished = runtime.coordinator.resolve_approval(card.id, ApprovalStatus.APPROVED)
    assert finished.state is JobState.DONE
    assert finished.owner_touches == 1
    assert runtime.store.get_card(card.id).status is ApprovalStatus.APPROVED


def test_rejection_cancels_job(runtime):
    runtime.trigger(make_lead_event())
    card = runtime.store.list_cards(status=ApprovalStatus.PENDING.value)[0]
    job = runtime.coordinator.resolve_approval(card.id, ApprovalStatus.REJECTED, note="wrong tone")
    assert job.state is JobState.CANCELLED


def test_trace_written(runtime):
    job = runtime.trigger(make_lead_event())
    entries = runtime.trace.read(job.id)
    types = [e["type"] for e in entries]
    assert "job_opened" in types
    assert "model_call" in types
    assert "approval_requested" in types
    # every rupee attributed: model calls carry token + usd numbers
    assert all(e["tokens"] > 0 for e in entries if e["type"] == "model_call")


def test_budget_blowout_escalates(runtime):
    # Shrink the per-job budget so the first model call trips it.
    runtime.adapter.policies["budgets"]["per_job"] = {"max_tokens": 1}
    job = runtime.trigger(make_lead_event())
    assert job.state is JobState.ESCALATED
    assert "budget exceeded" in job.error
    assert runtime.coordinator.escalations.pending()
