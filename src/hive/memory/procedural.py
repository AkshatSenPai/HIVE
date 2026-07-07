"""Procedural memory: versioned SOPs the agents execute (PRD §4, §7).

An SOP is a markdown file with YAML frontmatter. The frontmatter is the
machine-readable contract (trigger, steps, budget, checkpoints, done); the
body is the human/agent-readable instructions. If a smart intern couldn't
follow it, agents can't.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class SOPStep(BaseModel):
    id: str
    agent: str
    action: str
    action_kind: str = "internal"  # internal | send | spend | publish | contract | live_settings
    inputs: list[str] = Field(default_factory=list)
    output: str = ""
    checkpoint: bool = False
    done_when: str = ""


class SOP(BaseModel):
    name: str
    version: int = 1
    trigger: str  # event type that opens a job, e.g. "lead.new"
    description: str = ""
    budget: dict[str, Any] = Field(default_factory=dict)
    sla_hours: float | None = None
    steps: list[SOPStep] = Field(default_factory=list)
    body: str = ""  # the markdown instructions below the frontmatter

    def step_key(self, step_id: str) -> str:
        return f"{self.name}.{step_id}"


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        raise ValueError("SOP file must start with YAML frontmatter (---)")
    _, frontmatter, body = text.split("---", 2)
    return yaml.safe_load(frontmatter) or {}, body.strip()


def load_sop(path: str | Path) -> SOP:
    meta, body = _split_frontmatter(Path(path).read_text(encoding="utf-8"))
    meta["body"] = body
    return SOP.model_validate(meta)


def load_sops(workflows_dir: str | Path) -> dict[str, SOP]:
    sops: dict[str, SOP] = {}
    for path in sorted(Path(workflows_dir).glob("*.md")):
        sop = load_sop(path)
        sops[sop.name] = sop
    return sops
