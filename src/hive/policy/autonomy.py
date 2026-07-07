"""Autonomy dial (PRD §3, [HARD]).

Per-workflow, per-step trust levels. Steps EARN upgrades via track record:
after N consecutive approved runs the dial proposes an upgrade; the owner
ratifies it like any other change. Spend gates never fully open (PRD §11.1).
"""

from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel, Field


class AutonomyLevel(IntEnum):
    L0_DRAFT_ONLY = 0     # output is a draft; a human executes
    L1_GATED = 1          # agent executes, but blocks on approval card
    L2_AUTO_AUDIT = 2     # executes automatically, logged for audit
    L3_AUTONOMOUS = 3     # fully autonomous


class StepRecord(BaseModel):
    level: AutonomyLevel = AutonomyLevel.L1_GATED
    consecutive_approvals: int = 0
    upgrade_proposed: bool = False


class AutonomyDial(BaseModel):
    """Tracks trust per '<workflow>.<step>' key."""

    upgrade_threshold: int = Field(default=10, description="consecutive approvals before proposing an upgrade")
    steps: dict[str, StepRecord] = Field(default_factory=dict)

    def level(self, step_key: str) -> AutonomyLevel:
        return self.steps.get(step_key, StepRecord()).level

    def set_level(self, step_key: str, level: AutonomyLevel) -> None:
        rec = self.steps.setdefault(step_key, StepRecord())
        rec.level = level
        rec.consecutive_approvals = 0
        rec.upgrade_proposed = False

    def record_approval(self, step_key: str) -> bool:
        """Record an owner approval. Returns True when an upgrade should be proposed."""
        rec = self.steps.setdefault(step_key, StepRecord())
        rec.consecutive_approvals += 1
        if (
            not rec.upgrade_proposed
            and rec.consecutive_approvals >= self.upgrade_threshold
            and rec.level < AutonomyLevel.L3_AUTONOMOUS
        ):
            rec.upgrade_proposed = True
            return True
        return False

    def record_rejection(self, step_key: str) -> None:
        """Any rejection resets the streak — trust is earned, not assumed."""
        rec = self.steps.setdefault(step_key, StepRecord())
        rec.consecutive_approvals = 0
        rec.upgrade_proposed = False

    def ratify_upgrade(self, step_key: str) -> AutonomyLevel:
        """Owner approves the proposed upgrade: bump one level, reset streak."""
        rec = self.steps.setdefault(step_key, StepRecord())
        if rec.level < AutonomyLevel.L3_AUTONOMOUS:
            rec.level = AutonomyLevel(rec.level + 1)
        rec.consecutive_approvals = 0
        rec.upgrade_proposed = False
        return rec.level
