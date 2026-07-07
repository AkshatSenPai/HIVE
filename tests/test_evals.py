"""Eval harness: case loading, structural checks, honest content skipping,
failure detection, history tracking."""

import json

from hive.evals.harness import (
    CaseEvent,
    ContentCheck,
    EvalCase,
    StructuralChecks,
    load_cases,
    run_case,
    run_suite,
)
from tests.conftest import EXAMPLE_ADAPTER, REPO_ROOT

VENTURE_ADAPTER = REPO_ROOT / "adapters" / "venture-studio"


def test_cases_load_from_both_adapters():
    example = load_cases(EXAMPLE_ADAPTER)
    venture = load_cases(VENTURE_ADAPTER)
    assert {c.name for c in example} == {"basic_lead", "sparse_lead"}
    assert {c.name for c in venture} == {"niche_scan", "viable_apps"}
    assert all(c.workflow == "market_scan" for c in venture)


def test_workflow_filter():
    assert load_cases(EXAMPLE_ADAPTER, workflow="nope") == []


def test_structural_checks_pass_on_stub():
    case = load_cases(EXAMPLE_ADAPTER, workflow="lead_to_proposal")[0]
    result = run_case(case, EXAMPLE_ADAPTER, backend="stub")
    assert result.passed, [o.model_dump() for o in result.outcomes if not o.ok]
    assert result.job_state == "awaiting_approval"


def test_content_checks_skipped_on_stub_not_faked():
    case = next(c for c in load_cases(EXAMPLE_ADAPTER) if c.name == "basic_lead")
    assert case.content  # this case HAS content checks
    result = run_case(case, EXAMPLE_ADAPTER, backend="stub")
    assert result.content_skipped is True
    # none of the recorded outcomes are content checks
    assert all("contains" not in o.check for o in result.outcomes)


def test_failing_expectation_fails_the_case():
    case = EvalCase(
        name="wrong_state", workflow="lead_to_proposal",
        event=CaseEvent(type="lead.new", metadata={"subject": "x", "raw_context": "y"}),
        checks=StructuralChecks(final_state="done"),  # wrong: send is gated
    )
    result = run_case(case, EXAMPLE_ADAPTER, backend="stub")
    assert result.passed is False
    assert "got awaiting_approval" in result.outcomes[0].detail


def test_unknown_trigger_fails_gracefully():
    case = EvalCase(
        name="no_workflow", workflow="ghost",
        event=CaseEvent(type="ghost.event"),
    )
    result = run_case(case, EXAMPLE_ADAPTER, backend="stub")
    assert result.passed is False
    assert "no workflow" in result.outcomes[0].detail


def test_suite_writes_history(tmp_path):
    cases = load_cases(VENTURE_ADAPTER)
    suite = run_suite(cases, VENTURE_ADAPTER, backend="stub", history_dir=tmp_path / "evals")
    assert suite.passed == len(cases) == 2
    assert suite.score == 1.0
    lines = (tmp_path / "evals" / "history.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    entry = json.loads(lines[0])
    assert entry["adapter"] == "venture-studio"
    assert entry["backend"] == "stub"
    assert entry["passed"] is True
    assert entry["content_skipped"] is True

    # a second run appends — history accumulates over time
    run_suite(cases, VENTURE_ADAPTER, backend="stub", history_dir=tmp_path / "evals")
    lines = (tmp_path / "evals" / "history.jsonl").read_text().strip().splitlines()
    assert len(lines) == 4


def test_content_check_logic_directly():
    """Unit-test the content matcher without a model: feed artifacts in."""
    from hive.evals.harness import _content_outcomes

    artifacts = [{"artifact_type": "Draft", "body": "Demand signal: forum complaints. Kill criteria: <100 installs."}]
    check = ContentCheck(artifact="Draft", field="body",
                         must_contain=["demand signal", "kill criteria"],
                         must_not_contain=["as an ai"], max_words=50)
    outcomes = _content_outcomes(check, artifacts)
    assert all(o.ok for o in outcomes)

    bad = ContentCheck(artifact="Draft", field="body", must_contain=["monetization"])
    assert not _content_outcomes(bad, artifacts)[0].ok

    missing = ContentCheck(artifact="Deliverable", field="body", must_contain=["x"])
    assert not _content_outcomes(missing, artifacts)[0].ok
