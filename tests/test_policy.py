from hive.policy import AutonomyDial, AutonomyLevel, GateDecision, PolicyGate


def test_ungated_action_allows():
    gate = PolicyGate()
    assert gate.evaluate("internal", "w.enrich") is GateDecision.ALLOW


def test_gated_action_requires_approval_at_default_level():
    gate = PolicyGate()
    assert gate.evaluate("send", "w.send") is GateDecision.REQUIRE_APPROVAL


def test_earned_autonomy_opens_send_but_never_spend():
    gate = PolicyGate()
    gate.dial.set_level("w.send", AutonomyLevel.L2_AUTO_AUDIT)
    gate.dial.set_level("w.pay", AutonomyLevel.L3_AUTONOMOUS)
    assert gate.evaluate("send", "w.send") is GateDecision.ALLOW
    assert gate.evaluate("spend", "w.pay") is GateDecision.REQUIRE_APPROVAL  # PRD §11.1


def test_upgrade_proposed_after_streak_and_reset_on_rejection():
    dial = AutonomyDial(upgrade_threshold=3)
    assert not dial.record_approval("w.send")
    assert not dial.record_approval("w.send")
    assert dial.record_approval("w.send")  # third in a row -> propose
    dial.record_rejection("w.send")
    rec = dial.steps["w.send"]
    assert rec.consecutive_approvals == 0 and not rec.upgrade_proposed


def test_ratify_upgrade_bumps_one_level():
    dial = AutonomyDial()
    assert dial.ratify_upgrade("w.send") is AutonomyLevel.L2_AUTO_AUDIT
