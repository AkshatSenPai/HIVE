"""Owner-initiated workflow pattern: 'hive ask' -> market scan -> shortlist ->
owner picks at the checkpoint. Second adapter, zero core changes (PRD §6)."""

import pytest

from hive.agents.model import StubModelClient
from hive.config import HiveConfig
from hive.events.bus import Event
from hive.governance.approvals import ApprovalStatus
from hive.jobs.models import JobState
from hive.runtime import Runtime
from tests.conftest import REPO_ROOT

VENTURE_ADAPTER = REPO_ROOT / "adapters" / "venture-studio"


@pytest.fixture
def studio(tmp_path):
    config = HiveConfig(adapter_dir=VENTURE_ADAPTER, data_dir=tmp_path / ".hive")
    return Runtime(config, model=StubModelClient())


def ask_event() -> Event:
    return Event(
        type="owner.request",
        source="owner",
        metadata={"subject": "Search the market for viable apps we can make", "source": "owner:cli"},
    )


def test_adapter_loads(studio):
    assert studio.adapter.name == "venture-studio"
    sop = studio.adapter.workflow_for_trigger("owner.request")
    assert sop is not None and sop.name == "market_scan"
    assert [s.id for s in sop.steps] == ["scan", "shortlist", "review"]


def test_scan_runs_to_owner_checkpoint(studio):
    job = studio.trigger(ask_event())
    assert job is not None
    assert job.state is JobState.AWAITING_APPROVAL
    cards = studio.store.list_cards(status=ApprovalStatus.PENDING.value)
    assert len(cards) == 1
    card = cards[0]
    # the checkpoint gates even though nothing external is sent
    assert card.step_key == "market_scan.review"
    assert card.action_kind == "internal"


def test_owner_pick_finishes_scan(studio):
    studio.trigger(ask_event())
    card = studio.store.list_cards(status=ApprovalStatus.PENDING.value)[0]
    job = studio.coordinator.resolve_approval(card.id, ApprovalStatus.APPROVED, note="build #2")
    assert job.state is JobState.DONE
    assert job.owner_touches == 1
