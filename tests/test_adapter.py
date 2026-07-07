from hive.memory.procedural import SOP


def test_adapter_loads(adapter):
    assert adapter.name == "example"
    assert "Business Profile" in adapter.profile
    assert adapter.policies["gated_actions"]
    assert adapter.metrics["targets"]["owner_touches_per_job"] == 2


def test_workflow_parsed(adapter):
    sop = adapter.workflows["lead_to_proposal"]
    assert isinstance(sop, SOP)
    assert sop.trigger == "lead.new"
    assert [s.id for s in sop.steps] == ["enrich", "draft", "send"]
    assert sop.steps[2].checkpoint is True
    assert sop.steps[2].action_kind == "send"
    assert "smart intern" in sop.body


def test_trigger_lookup(adapter):
    assert adapter.workflow_for_trigger("lead.new").name == "lead_to_proposal"
    assert adapter.workflow_for_trigger("nope") is None


def test_least_privilege_tools(adapter):
    assert adapter.agent_tools("research") == ["vault_read"]
    assert adapter.agent_tools("nonexistent") == []
