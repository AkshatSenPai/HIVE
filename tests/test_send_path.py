"""The send path: approve => email executes (outbox/.eml), edit => manual,
missing recipient => FAILED loudly, earned L2 => auto-send with audit."""

import smtplib

import pytest

from hive.actions.email import OutboxEmailSender, SmtpEmailSender, make_email_sender
from hive.agents.model import StubModelClient
from hive.config import HiveConfig
from hive.events.bus import Event
from hive.governance.approvals import ApprovalStatus
from hive.jobs.models import JobState
from hive.policy.autonomy import AutonomyLevel
from hive.runtime import Runtime
from tests.conftest import EXAMPLE_ADAPTER


def make_runtime(tmp_path, **cfg) -> Runtime:
    config = HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive", **cfg)
    return Runtime(config, model=StubModelClient())


def lead(reply_to="priya@acme.example") -> Event:
    metadata = {"subject": "Acme fit-out", "raw_context": "2,400 sq yd. Send a proposal."}
    if reply_to:
        metadata["reply_to"] = reply_to
    return Event(type="lead.new", metadata=metadata)


def approve_pending(rt) -> object:
    card = rt.store.list_cards(ApprovalStatus.PENDING.value)[0]
    return rt.coordinator.resolve_approval(card.id, ApprovalStatus.APPROVED)


def test_approve_executes_send_to_outbox(tmp_path):
    rt = make_runtime(tmp_path)
    rt.trigger(lead())
    card = rt.store.list_cards(ApprovalStatus.PENDING.value)[0]
    assert any("priya@acme.example" in effect for effect in card.downstream_effects)

    job = rt.coordinator.resolve_approval(card.id, ApprovalStatus.APPROVED)
    assert job.state is JobState.DONE
    emails = list(rt.config.outbox_dir.glob("*.eml"))
    assert len(emails) == 1
    raw = emails[0].read_bytes().decode("utf-8", errors="replace")
    assert "To: priya@acme.example" in raw
    assert "Subject:" in raw
    types = [e["type"] for e in rt.trace.read(job.id)]
    assert "send_executed" in types


def test_missing_recipient_fails_loudly_not_silently(tmp_path):
    rt = make_runtime(tmp_path)
    rt.trigger(lead(reply_to=None))
    job = approve_pending(rt)
    assert job.state is JobState.FAILED
    assert "no recipient" in job.error
    assert list(rt.config.outbox_dir.glob("*.eml")) == []
    assert "send_failed" in [e["type"] for e in rt.trace.read(job.id)]


def test_edit_completes_without_sending(tmp_path):
    rt = make_runtime(tmp_path)
    rt.trigger(lead())
    card = rt.store.list_cards(ApprovalStatus.PENDING.value)[0]
    job = rt.coordinator.resolve_approval(card.id, ApprovalStatus.EDITED, note="I'll send it myself")
    assert job.state is JobState.DONE
    assert list(rt.config.outbox_dir.glob("*.eml")) == []  # owner took it manual


def test_reject_sends_nothing(tmp_path):
    rt = make_runtime(tmp_path)
    rt.trigger(lead())
    card = rt.store.list_cards(ApprovalStatus.PENDING.value)[0]
    job = rt.coordinator.resolve_approval(card.id, ApprovalStatus.REJECTED)
    assert job.state is JobState.CANCELLED
    assert list(rt.config.outbox_dir.glob("*.eml")) == []


def test_earned_l2_auto_sends_with_audit(tmp_path):
    rt = make_runtime(tmp_path)
    # The send step must not be a hard checkpoint for the dial to apply.
    send_step = rt.adapter.workflows["lead_to_proposal"].steps[-1]
    assert send_step.action_kind == "send"
    send_step.checkpoint = False
    rt.coordinator.gate.dial.set_level("lead_to_proposal.send", AutonomyLevel.L2_AUTO_AUDIT)

    job = rt.trigger(lead())
    assert job.state is JobState.DONE  # no card, no owner touch
    assert job.owner_touches == 0
    assert len(list(rt.config.outbox_dir.glob("*.eml"))) == 1
    sends = [e for e in rt.trace.read(job.id) if e["type"] == "send_executed"]
    assert sends and sends[0]["payload"]["auto"] is True


def test_sent_email_body_is_the_latest_draft(tmp_path):
    rt = make_runtime(tmp_path)
    rt.trigger(lead())
    approve_pending(rt)
    raw = list(rt.config.outbox_dir.glob("*.eml"))[0].read_bytes().decode("utf-8", errors="replace")
    drafts = [a for a in rt.store.list_artifacts(rt.store.list_jobs()[0].id)
              if a["artifact_type"] == "Draft"]
    first_line = drafts[-1]["body"].splitlines()[0][:40]
    assert first_line in raw


# -- backends -------------------------------------------------------------------


def test_make_sender_defaults_to_outbox(tmp_path):
    config = HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive")
    assert make_email_sender(config).name == "outbox"


def test_smtp_backend_requires_creds(tmp_path):
    config = HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive",
                        email_backend="smtp")
    with pytest.raises(ValueError, match="HIVE_SMTP_HOST"):
        make_email_sender(config)


def test_smtp_sender_flow(monkeypatch):
    calls = {}

    class FakeSmtp:
        def __init__(self, host, port, timeout=None):
            calls["conn"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            calls["tls"] = True

        def login(self, user, password):
            calls["login"] = (user, password)

        def send_message(self, message):
            calls["to"] = message["To"]
            calls["subject"] = message["Subject"]

    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    sender = SmtpEmailSender("smtp.example.com", 587, "u@example.com", "pw", "hive@example.com")
    detail = sender.send("client@x.com", "Proposal", "body", "job_1")
    assert calls == {"conn": ("smtp.example.com", 587), "tls": True,
                     "login": ("u@example.com", "pw"), "to": "client@x.com",
                     "subject": "Proposal"}
    assert "smtp.example.com" in detail


def test_outbox_filenames_unique(tmp_path):
    sender = OutboxEmailSender(tmp_path / "out")
    sender.send("a@x.com", "s", "b", "job_1")
    sender.send("a@x.com", "s", "b", "job_1")  # same second, same job
    assert len(list((tmp_path / "out").glob("*.eml"))) == 2
