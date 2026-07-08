"""Approval-path safety (from the voice review): the kill switch blocks
decisions, and a card is decided at most once — no duplicate consequential
sends on a retry / double-click / stale re-approve."""

import pytest
from fastapi.testclient import TestClient

from hive.agents.coordinator import DecisionRefused
from hive.agents.model import StubModelClient
from hive.api import create_app
from hive.config import HiveConfig
from hive.events.bus import Event
from hive.governance.approvals import ApprovalStatus
from hive.jobs.models import JobState
from hive.runtime import Runtime
from tests.conftest import EXAMPLE_ADAPTER


def make_runtime(tmp_path) -> Runtime:
    return Runtime(HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive"), model=StubModelClient())


def lead() -> Event:
    return Event(type="lead.new", metadata={"subject": "Acme", "raw_context": "x", "reply_to": "b@acme.example"})


def pending_card(rt):
    return rt.store.list_cards(ApprovalStatus.PENDING.value)[0]


def test_kill_switch_blocks_approval_and_send(tmp_path):
    rt = make_runtime(tmp_path)
    rt.trigger(lead())
    card = pending_card(rt)
    rt.kill_switch.engage()
    with pytest.raises(DecisionRefused, match="paused"):
        rt.coordinator.resolve_approval(card.id, ApprovalStatus.APPROVED)
    # nothing sent; card still pending; job still awaiting — the emergency stop held
    assert list(rt.config.outbox_dir.glob("*.eml")) == []
    assert rt.store.get_card(card.id).status is ApprovalStatus.PENDING
    assert rt.store.get_job(card.job_id).state is JobState.AWAITING_APPROVAL
    # release -> it now goes through
    rt.kill_switch.release()
    job = rt.coordinator.resolve_approval(card.id, ApprovalStatus.APPROVED)
    assert job.state is JobState.DONE
    assert len(list(rt.config.outbox_dir.glob("*.eml"))) == 1


def test_card_decided_once_no_duplicate_send(tmp_path):
    rt = make_runtime(tmp_path)
    rt.trigger(lead())
    card = pending_card(rt)
    rt.coordinator.resolve_approval(card.id, ApprovalStatus.APPROVED)
    assert len(list(rt.config.outbox_dir.glob("*.eml"))) == 1
    with pytest.raises(DecisionRefused, match="already"):
        rt.coordinator.resolve_approval(card.id, ApprovalStatus.APPROVED)  # retry / double-click
    assert len(list(rt.config.outbox_dir.glob("*.eml"))) == 1  # still exactly one


def test_api_decision_paused_returns_409(tmp_path):
    rt = make_runtime(tmp_path)
    rt.trigger(lead())
    card = pending_card(rt)
    rt.kill_switch.engage()
    client = TestClient(create_app(rt))
    resp = client.post(f"/approvals/{card.id}/decision", json={"decision": "approve"})
    assert resp.status_code == 409
    assert "paused" in resp.json()["detail"]


def test_api_decision_already_decided_returns_409(tmp_path):
    rt = make_runtime(tmp_path)
    rt.trigger(lead())
    card = pending_card(rt)
    client = TestClient(create_app(rt))
    assert client.post(f"/approvals/{card.id}/decision", json={"decision": "approve"}).status_code == 200
    resp = client.post(f"/approvals/{card.id}/decision", json={"decision": "approve"})
    assert resp.status_code == 409
    assert "already" in resp.json()["detail"]
