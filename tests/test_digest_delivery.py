"""Digest scheduling & delivery: enriched content, once-a-day guard, vault
archiving, Telegram payload shape, and autonomy ratification."""

import json
import urllib.request
from datetime import datetime

from fastapi.testclient import TestClient

from hive.agents.model import StubModelClient
from hive.api import create_app
from hive.config import HiveConfig
from hive.events.bus import Event
from hive.governance.approvals import ApprovalStatus
from hive.governance.delivery import DigestScheduler, TelegramSink, VaultSink, default_sinks
from hive.governance.digest import build_digest
from hive.policy.autonomy import AutonomyDial, AutonomyLevel, StepRecord
from hive.runtime import Runtime
from tests.conftest import EXAMPLE_ADAPTER


def make_runtime(tmp_path, **cfg) -> Runtime:
    config = HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive", **cfg)
    return Runtime(config, model=StubModelClient())


def seed_proposal(store, step_key="lead_to_proposal.send") -> None:
    dial = AutonomyDial()
    dial.steps[step_key] = StepRecord(
        level=AutonomyLevel.L1_GATED, consecutive_approvals=10, upgrade_proposed=True
    )
    store.save_state("autonomy_dial", dial.model_dump_json())


# -- digest content --------------------------------------------------------------


def test_digest_shows_daily_spend_and_cap(tmp_path):
    rt = make_runtime(tmp_path)
    rt.trigger(Event(type="lead.new", metadata={"subject": "Acme", "raw_context": "x"}))
    text = build_digest(rt.store, rt.adapter.policies)
    assert "spend today: $" in text
    assert "/ $20.0 daily cap" in text  # from example policies.yaml


def test_digest_surfaces_improvement_proposals(tmp_path):
    rt = make_runtime(tmp_path)
    seed_proposal(rt.store)
    text = build_digest(rt.store, rt.adapter.policies)
    assert "improvement proposals:" in text
    assert "lead_to_proposal.send" in text
    assert "upgrade L1_GATED to L2_AUTO_AUDIT" in text
    assert "hive ratify lead_to_proposal.send" in text


# -- scheduler --------------------------------------------------------------------


def test_due_respects_time_and_once_per_day(tmp_path):
    rt = make_runtime(tmp_path, digest_time="09:00")
    sched = DigestScheduler(rt)
    assert not sched.due(datetime(2026, 7, 6, 8, 59))   # before time
    assert sched.due(datetime(2026, 7, 6, 9, 0))        # at time
    sched.deliver(datetime(2026, 7, 6, 9, 0))
    assert not sched.due(datetime(2026, 7, 6, 18, 0))   # already sent today
    assert not sched.due(datetime(2026, 7, 7, 8, 0))    # next day, before time
    assert sched.due(datetime(2026, 7, 7, 9, 30))       # next day, after time


def test_deliver_archives_into_vault(tmp_path):
    rt = make_runtime(tmp_path)
    results = DigestScheduler(rt).deliver(datetime(2026, 7, 6, 9, 0))
    assert results == [{"sink": "vault", "ok": True, "detail": results[0]["detail"]}]
    assert rt.vault.exists("digests/2026-07-06.md")
    assert "HIVE DAILY DIGEST" in rt.vault.read("digests/2026-07-06.md")


def test_run_if_due_none_when_not_due(tmp_path):
    rt = make_runtime(tmp_path, digest_time="23:59")
    assert DigestScheduler(rt).run_if_due(datetime(2026, 7, 6, 10, 0)) is None


def test_sink_failure_reported_not_raised(tmp_path):
    class BrokenSink:
        name = "broken"

        def send(self, text, date_str):
            raise RuntimeError("boom")

    rt = make_runtime(tmp_path)
    results = DigestScheduler(rt, sinks=[VaultSink(rt.vault), BrokenSink()]).deliver(
        datetime(2026, 7, 6, 9, 0)
    )
    assert results[0]["ok"] is True
    assert results[1] == {"sink": "broken", "ok": False, "detail": "boom"}


def test_default_sinks_include_telegram_only_when_configured(tmp_path):
    rt = make_runtime(tmp_path)
    assert [s.name for s in default_sinks(rt)] == ["vault"]
    config2 = HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive2",
                         telegram_bot_token="TOK", telegram_chat_id="42")
    rt2 = Runtime(config2, model=StubModelClient())
    assert [s.name for s in default_sinks(rt2)] == ["vault", "telegram"]


def test_telegram_sink_payload(monkeypatch):
    captured = {}

    class FakeResponse:
        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    detail = TelegramSink("TOK123", "chat42").send("digest text", "2026-07-06")
    assert "botTOK123/sendMessage" in captured["url"]
    assert captured["body"] == {"chat_id": "chat42", "text": "digest text"}
    assert "chat42" in detail


# -- ratification loop -------------------------------------------------------------


def test_ratify_via_api_bumps_level_and_persists(tmp_path):
    config = HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive")
    rt = Runtime(config, model=StubModelClient())
    seed_proposal(rt.store)
    rt.coordinator.gate.dial = AutonomyDial.model_validate_json(
        rt.store.load_state("autonomy_dial")
    )
    client = TestClient(create_app(rt))

    dial_view = client.get("/autonomy").json()
    assert dial_view["steps"]["lead_to_proposal.send"]["upgrade_proposed"] is True

    resp = client.post("/autonomy/ratify", json={"step_key": "lead_to_proposal.send"})
    assert resp.json() == {"step_key": "lead_to_proposal.send", "level": 2,
                           "level_name": "L2_AUTO_AUDIT"}

    # persisted: a fresh runtime over the same store sees the new level
    rt2 = Runtime(config, model=StubModelClient())
    assert rt2.coordinator.gate.dial.steps["lead_to_proposal.send"].level == AutonomyLevel.L2_AUTO_AUDIT
    # and the proposal is cleared from the digest
    assert "improvement proposals" not in build_digest(rt2.store, rt2.adapter.policies)


def test_ratify_unknown_step_404(tmp_path):
    rt = make_runtime(tmp_path)
    client = TestClient(create_app(rt))
    assert client.post("/autonomy/ratify", json={"step_key": "ghost.step"}).status_code == 404


def test_digest_send_endpoint(tmp_path):
    rt = make_runtime(tmp_path)
    client = TestClient(create_app(rt))
    body = client.post("/digest/send").json()
    assert body["results"][0]["sink"] == "vault"
    assert body["results"][0]["ok"] is True
    # archived digest is browsable through the Memory endpoints
    files = client.get("/vault").json()["files"]
    assert any(f.startswith("digests/") for f in files)
