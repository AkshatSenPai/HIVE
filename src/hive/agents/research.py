"""Research agent (PRD §5 v1 roster): enrichment and context briefs.

Never sends anything. P0 gathers from the prompt context and the vault;
web tools land in the action layer later and are consumed through the
least-privilege toolset, not imported here.
"""

from __future__ import annotations

from hive.agents.base import Agent, AgentContext
from hive.artifacts import Brief
from hive.fencing import fence


class ResearchAgent(Agent):
    name = "research"
    tier = "specialist"
    role_prompt = (
        "You are the Research agent for this business. You produce short, factual "
        "briefs: who the prospect/subject is, what they need, relevant market or "
        "competitor context. Cite what you used. If information is missing or "
        "ambiguous, say so explicitly instead of guessing."
    )

    def perform(self, ctx: AgentContext, task: dict) -> Brief:
        subject = task.get("subject", "unknown subject")
        raw_context = task.get("raw_context", "")
        # Use web search when this adapter grants it; degrade gracefully when not.
        tools = [t for t in ("web_search",) if t in ctx.tools]
        prompt = f"Prepare a research brief on: {subject}"
        if tools:
            prompt += (
                "\nSearch the web for current evidence; cite what you find. "
                "Mark anything you could not verify as unverified."
            )
        if raw_context:
            # Trigger payloads come from the outside world — always fenced.
            prompt += "\n\n" + fence(raw_context, source=task.get("source", "trigger"))
        if feedback := task.get("feedback"):
            # Coordinator review feedback (internal, trusted — not fenced).
            prompt += f"\n\nReviewer feedback on your previous attempt — address all of it:\n{feedback}"
        response = self.ask_model(ctx, prompt, tools=tools)
        sources = [task.get("source", "trigger")] + (["web_search"] if tools else [])
        return Brief(
            subject=subject,
            summary=response.text,
            findings=[response.text],
            sources=sources,
            confidence=0.7,
            produced_by=self.name,
            job_id=ctx.job_id,
        )
