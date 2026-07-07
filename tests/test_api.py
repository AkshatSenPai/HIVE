"""API surface: the endpoints a frontend will call, over the stub runtime."""

import pytest
from fastapi.testclient import TestClient

from hive.agents.model import StubModelClient
from hive.api import create_app
from hive.config import HiveConfig
from hive.runtime import Runtime
from tests.conftest import EXAMPLE_ADAPTER


@pytest.fixture
def client(tmp_path):
    config = HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive")
    runtime = Runtime(config, model=StubModelClient())
    return TestClient(create_app(runtime))


def _open_job(client) -> dict:
    resp = client.post("/jobs", json={
        "type": "lead.new",
        "subject": "Acme — fit-out",
        "raw_context": "We need a 2,400 sq yd fit-out. Please send a proposal.",
        "metadata": {"reply_to": "buyer@acme.example"},
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_health_and_adapter(client):
    assert client.get("/health").json()["adapter"] == "example"
    adapter = client.get("/adapter").json()
    assert adapter["workflows"][0]["trigger"] == "lead.new"
    assert adapter["metrics"]["targets"]["ungated_consequential_actions"] == 0


def test_create_job_returns_job_and_card(client):
    body = _open_job(client)
    assert body["job"]["state"] == "awaiting_approval"
    assert body["job"]["spend_usd"] > 0
    assert len(body["pending_cards"]) == 1
    assert body["pending_cards"][0]["action_kind"] == "send"


def test_unknown_trigger_is_422(client):
    resp = client.post("/jobs", json={"type": "no.such.trigger", "subject": "x"})
    assert resp.status_code == 422


def test_job_detail_exposes_persisted_artifacts(client):
    job_id = _open_job(client)["job"]["id"]
    detail = client.get(f"/jobs/{job_id}").json()
    kinds = {a["artifact_type"] for a in detail["artifacts"]}
    assert {"Plan", "Brief", "Draft", "Deliverable"} <= kinds  # full content, not just ids
    assert detail["artifacts"], "artifacts were not persisted"


def test_trace_endpoint(client):
    job_id = _open_job(client)["job"]["id"]
    trace = client.get(f"/jobs/{job_id}/trace").json()["trace"]
    assert any(e["type"] == "model_call" for e in trace)


def test_approval_flow(client):
    _open_job(client)
    cards = client.get("/approvals").json()["cards"]
    assert len(cards) == 1
    card_id = cards[0]["id"]
    resp = client.post(f"/approvals/{card_id}/decision", json={"decision": "approve"})
    assert resp.status_code == 200
    assert resp.json()["job"]["state"] == "done"
    assert resp.json()["job"]["owner_touches"] == 1
    # pending queue is now empty
    assert client.get("/approvals").json()["cards"] == []


def test_reject_cancels(client):
    _open_job(client)
    card_id = client.get("/approvals").json()["cards"][0]["id"]
    resp = client.post(f"/approvals/{card_id}/decision", json={"decision": "reject", "note": "off tone"})
    assert resp.json()["job"]["state"] == "cancelled"


def test_bad_card_is_404(client):
    resp = client.post("/approvals/card_missing/decision", json={"decision": "approve"})
    assert resp.status_code == 404


def test_killswitch_toggle(client):
    assert client.get("/killswitch").json()["engaged"] is False
    assert client.post("/killswitch", json={"engaged": True}).json()["engaged"] is True
    assert client.get("/health").json()["kill_switch"] is True


def test_digest(client):
    _open_job(client)
    assert "HIVE DAILY DIGEST" in client.get("/digest").json()["text"]


def test_adapter_exposes_agent_roster(client):
    agents = {a["name"]: a for a in client.get("/adapter").json()["agents"]}
    assert agents["coordinator"]["tier"] == "planner"
    assert agents["coordinator"]["model"] == "claude-opus-4-8"
    assert agents["research"]["model"] == "claude-sonnet-5"
    assert agents["research"]["tools"] == ["vault_read"]
    assert "lead_to_proposal.enrich" in agents["research"]["steps"]


def test_system_settings_readonly(client):
    system = client.get("/system").json()
    assert system["model_routing"]["planner"] == "claude-opus-4-8"
    assert system["model_routing"]["frontier"] == "claude-fable-5"
    assert "send" in system["policies"]["gated_actions"]
    assert system["use_llm"] is False


def test_vault_endpoints(tmp_path):
    config = HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive")
    runtime = Runtime(config, model=StubModelClient())
    runtime.vault.write("clients/acme.md", "# Acme\nnotes here")
    c = TestClient(create_app(runtime))

    files = c.get("/vault").json()["files"]
    assert files == ["clients/acme.md"]
    body = c.get("/vault/file", params={"path": "clients/acme.md"}).json()
    assert "notes here" in body["content"]
    assert c.get("/vault/file", params={"path": "missing.md"}).status_code == 404
    assert c.get("/vault/file", params={"path": "../../secrets.txt"}).status_code == 400


def test_static_ui_served_without_shadowing_api(tmp_path):
    """The frontend mounts at '/', but API routes registered earlier still win."""
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<h1>my frontend</h1>", encoding="utf-8")
    (web / "tasks.html").write_text("<h1>tasks page</h1>", encoding="utf-8")
    config = HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive", web_dir=web)
    app = create_app(Runtime(config, model=StubModelClient()), web_dir=web)
    c = TestClient(app)
    assert "my frontend" in c.get("/").text            # UI at root
    assert "tasks page" in c.get("/tasks.html").text   # multi-page: every file in web/ is served
    assert c.get("/health").json()["status"] == "ok"   # API not shadowed


def test_no_ui_when_absent(tmp_path):
    empty = tmp_path / "no_web"
    config = HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive", web_dir=empty)
    app = create_app(Runtime(config, model=StubModelClient()), web_dir=empty)
    c = TestClient(app)
    assert c.get("/health").json()["status"] == "ok"   # API still fine
    assert c.get("/").status_code == 404               # nothing mounted at root
