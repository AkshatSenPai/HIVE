"""Dial persistence across restarts + the global daily budget brake."""

from hive.agents.coordinator import Coordinator
from hive.agents.model import StubModelClient
from hive.config import HiveConfig
from hive.events.bus import Event
from hive.governance.approvals import ApprovalStatus
from hive.jobs.models import JobState
from hive.runtime import Runtime
from tests.conftest import EXAMPLE_ADAPTER


def lead(subject="Acme") -> Event:
    return Event(type="lead.new", metadata={
        "subject": subject, "raw_context": "ctx", "reply_to": "buyer@acme.example",
    })


def test_dial_streak_survives_restart(tmp_path):
    config = HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive")
    rt1 = Runtime(config, model=StubModelClient())
    rt1.trigger(lead())
    card = rt1.store.list_cards(ApprovalStatus.PENDING.value)[0]
    rt1.coordinator.resolve_approval(card.id, ApprovalStatus.APPROVED)
    assert rt1.coordinator.gate.dial.steps["lead_to_proposal.send"].consecutive_approvals == 1

    # "Restart": a brand-new runtime over the same data dir
    rt2 = Runtime(config, model=StubModelClient())
    rec = rt2.coordinator.gate.dial.steps["lead_to_proposal.send"]
    assert rec.consecutive_approvals == 1  # trust survived the restart


def test_dial_threshold_comes_from_policies(runtime):
    # example adapter policies.yaml sets upgrade_threshold: 10
    assert runtime.coordinator.gate.dial.upgrade_threshold == 10


def test_global_daily_cap_blocks_new_jobs(tmp_path):
    config = HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive")
    rt = Runtime(config, model=StubModelClient())
    # First job runs and records spend.
    job1 = rt.trigger(lead("first"))
    assert job1.state is JobState.AWAITING_APPROVAL
    # Shrink today's cap below what job1 already spent.
    rt.adapter.policies.setdefault("budgets", {})["global_daily"] = {"max_usd": job1.spend_usd / 2}
    # Second job must be refused before any model call.
    calls_before = len(rt.model.calls)
    job2 = rt.trigger(lead("second"))
    assert job2.state is JobState.ESCALATED
    assert "global daily budget exhausted" in job2.error
    assert len(rt.model.calls) == calls_before  # zero tokens spent on the refusal
    assert rt.coordinator.escalations.pending()
