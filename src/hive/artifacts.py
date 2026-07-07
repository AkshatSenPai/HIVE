"""Typed artifacts — the only currency agents exchange.

PRD §9 "Structured everything": agents never hand off free prose. Every
handoff is one of these models, validated at the boundary.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Artifact(BaseModel):
    """Base for everything passed between agents."""

    id: str = Field(default_factory=lambda: _new_id("art"))
    created_at: datetime = Field(default_factory=utcnow)
    produced_by: str = ""  # agent name
    job_id: str = ""


class Brief(Artifact):
    """Research output: enrichment / market / competitor / context brief."""

    subject: str
    summary: str
    findings: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    confidence: float = 0.5  # 0..1; low confidence must escalate, not guess


class Draft(Artifact):
    """Maker output: the business's core artifact (adapter-defined kind)."""

    kind: str  # e.g. "proposal", "listing", "email_sequence"
    title: str
    body: str
    inputs_used: list[str] = Field(default_factory=list)  # artifact ids


class PlanStep(BaseModel):
    id: str
    agent: str
    action: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    checkpoint: bool = False  # True => output gates on owner approval
    done_when: str = ""


class Plan(Artifact):
    """Plan-as-artifact (PRD §4): reviewable object, not hidden chain of thought."""

    goal: str
    workflow: str  # SOP name this plan was derived from
    steps: list[PlanStep] = Field(default_factory=list)
    notes: str = ""


class Deliverable(Artifact):
    """Coordinator-assembled final output for one job."""

    title: str
    parts: list[str] = Field(default_factory=list)  # artifact ids
    summary: str = ""


class EscalationReason(str, Enum):
    AMBIGUITY = "ambiguity"
    MISSING_INFO = "missing_info"
    LOW_CONFIDENCE = "low_confidence"
    OUT_OF_WORKFLOW = "out_of_workflow"
    BUDGET_EDGE = "budget_edge"


class Escalation(Artifact):
    """Stop-and-ask. Guessing is a defect (PRD §11.4)."""

    reason: EscalationReason
    question: str
    context: str = ""
