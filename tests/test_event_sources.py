"""Event sources: file inbox (parse, archive, quarantine, sanitize) and the
token-gated webhook."""

import json

from fastapi.testclient import TestClient

from hive.agents.model import StubModelClient
from hive.api import create_app
from hive.config import HiveConfig
from hive.events.sources import FileInboxSource, sanitize_subject
from hive.jobs.models import JobState
from hive.runtime import Runtime
from tests.conftest import EXAMPLE_ADAPTER


def make_runtime(tmp_path, **cfg) -> Runtime:
    config = HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive", **cfg)
    return Runtime(config, model=StubModelClient())


# -- subject sanitization ------------------------------------------------------


def test_sanitize_subject():
    assert sanitize_subject("  Hello   world \n second line") == "Hello world"
    assert sanitize_subject("\n\n  \nreal subject") == "real subject"
    assert sanitize_subject("a" * 500) == "a" * 150
    assert sanitize_subject("bad\x00control\x1fchars") == "bad control chars"
    assert sanitize_subject("", fallback="lead-42") == "lead-42"


# -- file inbox ------------------------------------------------------------------


def test_json_drop_opens_job_and_archives(tmp_path):
    rt = make_runtime(tmp_path)
    source = FileInboxSource(rt)
    (source.inbox / "lead1.json").write_text(json.dumps({
        "type": "lead.new",
        "subject": "Acme fit-out",
        "raw_context": "2,400 sq yd office, Gurgaon. Send a proposal.",
        "source": "email-bridge",
    }), encoding="utf-8")

    assert source.poll_once() == 1
    jobs = rt.store.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].state is JobState.AWAITING_APPROVAL
    assert jobs[0].context["source"] == "inbox:email-bridge"
    # consumed file was archived, inbox is empty
    assert not list(source.inbox.glob("*.json"))
    assert len(list(source.processed.iterdir())) == 1


def test_txt_drop_uses_first_line_as_subject_and_fences_body(tmp_path):
    rt = make_runtime(tmp_path)
    source = FileInboxSource(rt)
    (source.inbox / "note.txt").write_text(
        "Office renovation enquiry from Priya\nWe need 2,400 sq yd done in 3 months.",
        encoding="utf-8",
    )
    assert source.poll_once() == 1
    job = rt.store.list_jobs()[0]
    assert job.context["subject"] == "Office renovation enquiry from Priya"
    # the body reached the research prompt inside a fence
    research_prompts = [p for _, p in rt.model.calls if "research brief" in p.lower()]
    assert "<external-content" in research_prompts[0]
    assert "3 months" in research_prompts[0]


def test_malformed_json_is_quarantined_not_crashing(tmp_path):
    rt = make_runtime(tmp_path)
    source = FileInboxSource(rt)
    (source.inbox / "broken.json").write_text("{not json", encoding="utf-8")
    (source.inbox / "good.txt").write_text("valid lead\nbody", encoding="utf-8")

    assert source.poll_once() == 1  # the good file still processed
    failed = list(source.failed.iterdir())
    assert any("broken.json" in f.name for f in failed)
    assert any(f.name.endswith(".error.txt") for f in failed)


def test_unmatched_event_type_archives_without_job(tmp_path):
    rt = make_runtime(tmp_path)
    source = FileInboxSource(rt)
    (source.inbox / "odd.json").write_text(json.dumps({"type": "no.such.trigger"}), encoding="utf-8")
    assert source.poll_once() == 0
    assert rt.store.list_jobs() == []
    assert len(list(source.processed.iterdir())) == 1  # consumed, not stuck


def test_non_inbox_files_ignored(tmp_path):
    rt = make_runtime(tmp_path)
    source = FileInboxSource(rt)
    (source.inbox / "image.png").write_bytes(b"\x89PNG")
    assert source.poll_once() == 0
    assert (source.inbox / "image.png").exists()  # left alone


# -- webhook ---------------------------------------------------------------------


def test_webhook_disabled_without_token(tmp_path):
    rt = make_runtime(tmp_path)  # no webhook_token
    client = TestClient(create_app(rt))
    resp = client.post("/events", json={"type": "lead.new", "subject": "x"})
    assert resp.status_code == 503


def test_webhook_rejects_bad_token(tmp_path):
    rt = make_runtime(tmp_path, webhook_token="s3cret")
    client = TestClient(create_app(rt))
    assert client.post("/events", json={"type": "lead.new", "subject": "x"}).status_code == 401
    assert client.post("/events", json={"type": "lead.new", "subject": "x"},
                       headers={"X-Hive-Token": "wrong"}).status_code == 401


def test_webhook_opens_job_with_valid_token(tmp_path):
    rt = make_runtime(tmp_path, webhook_token="s3cret")
    client = TestClient(create_app(rt))
    resp = client.post("/events", json={
        "type": "lead.new", "subject": "Acme", "raw_context": "please quote", "source": "crm",
    }, headers={"X-Hive-Token": "s3cret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched"] is True
    assert body["job"]["state"] == "awaiting_approval"
    assert body["job"]["context"]["source"] == "webhook:crm"


def test_webhook_unmatched_type_is_not_an_error(tmp_path):
    rt = make_runtime(tmp_path, webhook_token="s3cret")
    client = TestClient(create_app(rt))
    resp = client.post("/events", json={"type": "ghost.event", "subject": "x"},
                       headers={"X-Hive-Token": "s3cret"})
    assert resp.status_code == 200
    assert resp.json() == {"matched": False, "job": None}
