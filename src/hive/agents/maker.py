"""Maker agent (PRD §5 v1 roster): produces the business's core artifact.

What "the artifact" is (proposal, listing, activation email...) comes from
the adapter's SOP, not from this class. Never sends anything.
"""

from __future__ import annotations

from hive.agents.base import Agent, AgentContext
from hive.artifacts import Brief, Draft


class MakerAgent(Agent):
    name = "maker"
    tier = "specialist"
    role_prompt = (
        "You are the Maker agent for this business. You turn a research brief into "
        "the business's core deliverable, in the business's voice, following the "
        "SOP instructions you are given. Output the deliverable only."
    )

    def perform(self, ctx: AgentContext, task: dict) -> Draft:
        brief: Brief | None = task.get("brief")
        kind = task.get("kind", "draft")
        instructions = task.get("instructions", "")
        prompt = f"Produce a {kind}.\n\nSOP instructions:\n{instructions}"
        if brief is not None:
            prompt += f"\n\nResearch brief:\n{brief.summary}"
        if feedback := task.get("feedback"):
            # Coordinator review feedback (internal, trusted — not fenced).
            prompt += f"\n\nReviewer feedback on your previous attempt — address all of it:\n{feedback}"
        response = self.ask_model(ctx, prompt)
        return Draft(
            kind=kind,
            title=f"{kind}: {task.get('subject', 'untitled')}",
            body=response.text,
            inputs_used=[brief.id] if brief else [],
            produced_by=self.name,
            job_id=ctx.job_id,
        )
