import pytest

from hive.policy import Budget, BudgetExceeded, KillSwitch


def test_step_cap():
    budget = Budget(max_steps=2)
    budget.charge_step()
    budget.charge_step()
    with pytest.raises(BudgetExceeded):
        budget.charge_step()


def test_token_and_usd_caps():
    budget = Budget(max_tokens=100, max_usd=0.01)
    budget.charge_tokens(50, usd=0.005)
    with pytest.raises(BudgetExceeded):
        budget.charge_tokens(60, usd=0.0)
    budget2 = Budget(max_tokens=1000, max_usd=0.01)
    with pytest.raises(BudgetExceeded):
        budget2.charge_tokens(10, usd=0.02)


def test_near_edge():
    budget = Budget(max_steps=10)
    for _ in range(8):
        budget.charge_step()
    assert budget.near_edge


def test_kill_switch():
    switch = KillSwitch()
    switch.check()  # no-op while released
    switch.engage()
    with pytest.raises(RuntimeError):
        switch.check()
    switch.release()
    switch.check()


# -- per-job caps: policy ceiling, SOP may only tighten (HIVE build plan, 2026-11-16) --

from pathlib import Path

import yaml

from hive.config import HiveConfig
from hive.runtime import Runtime
from tests.conftest import EXAMPLE_ADAPTER, REPO_ROOT


@pytest.mark.parametrize("adapter, per_job", [
    ("example", 0.32),         # about Rs 30
    ("venture-studio", 0.75),  # about Rs 70: a live scan's research call alone is about $0.37
])
def test_shipped_caps_match_build_plan(adapter, per_job):
    """$1/day (about Rs 94) in every shipped adapter; per-job cap sized to its work."""
    policies = yaml.safe_load((REPO_ROOT / "adapters" / adapter / "policies.yaml").read_text(encoding="utf-8"))
    assert policies["budgets"]["global_daily"]["max_usd"] == 1.0
    assert policies["budgets"]["per_job"]["max_usd"] == per_job


def test_market_scan_budget_fits_under_its_policy():
    """The scan's own budget is what applies (tighten-only), so it must not sit
    below the policy by accident and starve a live scan."""
    from hive.adapter import load_adapter
    adapter = load_adapter(REPO_ROOT / "adapters" / "venture-studio")
    sop = adapter.workflows["market_scan"]
    per_job = adapter.policies["budgets"]["per_job"]
    assert sop.budget["max_usd"] == per_job["max_usd"]
    assert sop.budget["max_tokens"] == per_job["max_tokens"]


def _coordinator_and_sop(tmp_path: Path):
    rt = Runtime(HiveConfig(adapter_dir=EXAMPLE_ADAPTER, data_dir=tmp_path / ".hive"))
    sop = next(iter(rt.adapter.workflows.values()))
    return rt.coordinator, sop


def test_sop_budget_can_tighten_the_job_cap(tmp_path):
    coordinator, sop = _coordinator_and_sop(tmp_path)
    budget = coordinator._job_budget(sop.model_copy(update={"budget": {"max_usd": 0.10, "max_steps": 5}}))
    assert budget.max_usd == 0.10
    assert budget.max_steps == 5


def test_sop_budget_cannot_raise_the_job_cap(tmp_path):
    coordinator, sop = _coordinator_and_sop(tmp_path)
    budget = coordinator._job_budget(sop.model_copy(update={"budget": {"max_usd": 5.0, "max_tokens": 10**9}}))
    assert budget.max_usd == 0.32  # policy ceiling holds
    assert budget.max_tokens == 150_000


def test_sop_budget_cannot_exceed_builtin_default(tmp_path):
    coordinator, sop = _coordinator_and_sop(tmp_path)
    del coordinator.adapter.policies["budgets"]["per_job"]["max_usd"]  # policy leaves it out
    budget = coordinator._job_budget(sop.model_copy(update={"budget": {"max_usd": 50.0}}))
    assert budget.max_usd == Budget().max_usd
