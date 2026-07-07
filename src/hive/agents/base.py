"""Agent body (PRD §4, the Zenith chat_core pattern): role prompt +
least-privilege toolset + per-agent budget, wrapped in loop guards.

Every model call goes through the same chokepoint: kill switch check, step
charge, token charge, trace record. Specialists subclass and implement
`perform`, returning a typed Artifact — never free prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hive.actions.registry import ToolNotPermitted
from hive.artifacts import Artifact, Escalation, EscalationReason
from hive.agents.model import ModelClient, ModelResponse
from hive.observability.trace import TraceWriter
from hive.policy.budgets import Budget, KillSwitch


@dataclass
class AgentContext:
    """Everything an agent is allowed to touch for one job."""

    job_id: str
    model: ModelClient
    budget: Budget
    kill_switch: KillSwitch
    trace: TraceWriter
    tools: list[str] = field(default_factory=list)  # least-privilege names
    profile: str = ""  # adapter profile.md — the business voice/context


class Agent:
    name: str = "agent"
    tier: str = "specialist"  # model routing tier
    role_prompt: str = "You are a specialist agent."

    def ask_model(
        self, ctx: AgentContext, prompt: str, tools: list[str] | None = None
    ) -> ModelResponse:
        """The single chokepoint for model access — kill switch, budget,
        least-privilege tool check, trace. In that order."""
        ctx.kill_switch.check()
        ctx.budget.charge_step()
        requested = tools or []
        denied = [t for t in requested if t not in ctx.tools]
        if denied:
            raise ToolNotPermitted(self.name, denied)  # PRD §11.3, hard stop
        system = f"{self.role_prompt}\n\nBusiness profile:\n{ctx.profile}"
        response = ctx.model.complete(self.tier, system, prompt, tools=requested)
        ctx.budget.charge_tokens(response.total_tokens, response.usd)
        ctx.trace.record(
            ctx.job_id,
            "model_call",
            agent=self.name,
            payload={
                "tier": self.tier, "model": response.model,
                "tools": requested, "prompt": prompt[:200],
            },
            tokens=response.total_tokens,
            usd=response.usd,
        )
        return response

    def escalate(self, ctx: AgentContext, reason: EscalationReason, question: str, context: str = "") -> Escalation:
        escalation = Escalation(
            reason=reason, question=question, context=context,
            produced_by=self.name, job_id=ctx.job_id,
        )
        ctx.trace.record(ctx.job_id, "escalation", agent=self.name,
                         payload={"reason": reason.value, "question": question})
        return escalation

    def perform(self, ctx: AgentContext, task: dict) -> Artifact:
        raise NotImplementedError
