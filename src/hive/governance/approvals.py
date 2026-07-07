"""Approval cards (PRD §3.1) — the owner's primary surface.

A card shows the exact artifact, the agent's reasoning, cost so far, and
downstream effects, and blocks on Approve / Edit / Reject. P0 delivery is the
CLI/dashboard; Telegram and voice plug in later as extra channels.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    EDITED = "edited"      # owner approved with modifications
    REJECTED = "rejected"


class ApprovalCard(BaseModel):
    id: str = Field(default_factory=lambda: f"card_{uuid.uuid4().hex[:12]}")
    job_id: str
    step_key: str           # "<workflow>.<step>" — feeds the autonomy dial
    action_kind: str        # send / spend / publish / contract / live_settings
    title: str
    artifact_id: str        # what exactly would go out
    artifact_preview: str   # rendered for the owner
    reasoning: str          # why the agent believes this is right
    cost_so_far_usd: float = 0.0
    downstream_effects: list[str] = Field(default_factory=list)
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: datetime | None = None
    owner_note: str = ""

    def decide(self, status: ApprovalStatus, note: str = "") -> "ApprovalCard":
        self.status = status
        self.owner_note = note
        self.decided_at = datetime.now(timezone.utc)
        return self

    def render(self) -> str:
        effects = "\n".join(f"|     - {e}" for e in self.downstream_effects) or "|     - (none listed)"
        return (
            f"+-- APPROVAL CARD {self.id} -- [{self.status.value.upper()}]\n"
            f"| job:        {self.job_id}\n"
            f"| action:     {self.action_kind}  (step: {self.step_key})\n"
            f"| title:      {self.title}\n"
            f"| cost so far: ${self.cost_so_far_usd:.4f}\n"
            f"| reasoning:  {self.reasoning}\n"
            f"| downstream effects:\n{effects}\n"
            f"| artifact preview:\n"
            f"|   {self.artifact_preview[:800]}\n"
            f"+-- approve / edit / reject"
        )
