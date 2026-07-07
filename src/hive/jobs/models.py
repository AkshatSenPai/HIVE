"""Job model — every workflow instance is a persistent, inspectable,
resumable job with explicit states (PRD §4, [PROVEN])."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JobState(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    AWAITING_APPROVAL = "awaiting_approval"
    ESCALATED = "escalated"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(BaseModel):
    id: str = Field(default_factory=lambda: f"job_{uuid.uuid4().hex[:12]}")
    workflow: str  # SOP name from the adapter
    adapter: str
    state: JobState = JobState.QUEUED
    trigger_event_id: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    context: dict[str, Any] = Field(default_factory=dict)  # trigger payload
    artifact_ids: list[str] = Field(default_factory=list)
    spend_tokens: int = 0
    spend_usd: float = 0.0
    owner_touches: int = 0  # PRD §13 headline metric
    error: str = ""
