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
