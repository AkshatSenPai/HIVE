"""Business adapters — the anti-bias mechanism (PRD §6).

Everything company-specific lives in adapters/<business>/. HIVE core never
hardcodes a business. Starting a new venture = writing a new adapter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from hive.memory.procedural import SOP, load_sops


class BusinessAdapter(BaseModel):
    name: str
    root: Path
    profile: str  # profile.md contents: offer, ICP, positioning, voice
    workflows: dict[str, SOP] = Field(default_factory=dict)
    tools: dict[str, Any] = Field(default_factory=dict)      # tools.yaml
    policies: dict[str, Any] = Field(default_factory=dict)   # policies.yaml
    metrics: dict[str, Any] = Field(default_factory=dict)    # metrics.yaml

    model_config = {"arbitrary_types_allowed": True}

    def workflow_for_trigger(self, event_type: str) -> SOP | None:
        for sop in self.workflows.values():
            if sop.trigger == event_type:
                return sop
        return None

    def agent_tools(self, agent_name: str) -> list[str]:
        """Least-privilege toolset for an agent (PRD §11.3)."""
        return list(self.tools.get("agents", {}).get(agent_name, {}).get("tools", []))


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_adapter(adapter_dir: str | Path) -> BusinessAdapter:
    root = Path(adapter_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"adapter directory not found: {root}")
    profile_path = root / "profile.md"
    if not profile_path.exists():
        raise FileNotFoundError(f"adapter is missing profile.md: {root}")
    return BusinessAdapter(
        name=root.name,
        root=root,
        profile=profile_path.read_text(encoding="utf-8"),
        workflows=load_sops(root / "workflows") if (root / "workflows").is_dir() else {},
        tools=_load_yaml(root / "tools.yaml"),
        policies=_load_yaml(root / "policies.yaml"),
        metrics=_load_yaml(root / "metrics.yaml"),
    )
