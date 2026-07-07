"""Consequential-action gate (PRD §11.1).

Send / spend / publish / sign / change-live actions block on the owner until
the step has earned a higher autonomy level. Spend NEVER fully opens: even at
L3 a spend action still gates.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from hive.policy.autonomy import AutonomyDial, AutonomyLevel

ALWAYS_GATED_KINDS = {"spend"}  # PRD §11.1: "spend gates never fully open"


class GateDecision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class PolicyGate(BaseModel):
    """Decides whether an action may proceed, must gate, or is forbidden."""

    gated_kinds: set[str] = Field(
        default_factory=lambda: {"send", "spend", "publish", "contract", "live_settings"}
    )
    dial: AutonomyDial = Field(default_factory=AutonomyDial)

    def evaluate(self, action_kind: str, step_key: str) -> GateDecision:
        if action_kind not in self.gated_kinds:
            return GateDecision.ALLOW
        if action_kind in ALWAYS_GATED_KINDS:
            return GateDecision.REQUIRE_APPROVAL
        if self.dial.level(step_key) >= AutonomyLevel.L2_AUTO_AUDIT:
            return GateDecision.ALLOW
        return GateDecision.REQUIRE_APPROVAL
