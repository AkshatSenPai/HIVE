"""Coordinator (PRD §4, [HARD]) — the single manager.

Decomposes jobs against the SOP, staffs specialists, reviews outputs,
assembles deliverables, and decides human involvement. Planning quality is
the product; for P0 the plan is derived 1:1 from the SOP (a plan the owner
already ratified by writing the SOP), with one planner-tier model call to
sanity-check fit. Free-form planning comes later, behind the eval harness.

Never does specialist work. Never sends anything (PRD §5).
"""

from __future__ import annotations

from datetime import datetime, timezone

from hive.adapter import BusinessAdapter
from hive.agents.base import Agent, AgentContext
from hive.agents.maker import MakerAgent
from hive.agents.model import ModelClient
from hive.agents.research import ResearchAgent
from hive.artifacts import Artifact, Deliverable, Escalation, EscalationReason, Plan, PlanStep
from hive.events.bus import Event
from hive.governance.approvals import ApprovalCard, ApprovalStatus
from hive.governance.escalation import EscalationQueue
from hive.jobs.fsm import transition
from hive.jobs.models import Job, JobState
from hive.jobs.store import JobStore
from hive.memory.procedural import SOP
from hive.observability.trace import TraceWriter
from hive.policy.autonomy import AutonomyDial
from hive.policy.budgets import Budget, BudgetExceeded, KillSwitch
from hive.policy.gates import GateDecision, PolicyGate

_DIAL_STATE_KEY = "autonomy_dial"


class ReviewFailed(Exception):
    """A specialist's output failed coordinator review after all allowed
    reworks. Never accepted by exhaustion — the owner decides."""

    def __init__(self, step_id: str, feedback: str, rounds: int) -> None:
        self.step_id, self.feedback, self.rounds = step_id, feedback, rounds
        super().__init__(f"step '{step_id}' failed review after {rounds} rework(s)")


def _parse_verdict(text: str) -> tuple[str, str]:
    """Extract 'pass' | 'revise' | 'unparsed' from a review reply."""
    for line in text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("verdict:"):
            verdict = stripped.split(":", 1)[1].strip()
            if verdict.startswith("revise"):
                return "revise", text
            if verdict.startswith("pass"):
                return "pass", text
    return "unparsed", text


def _artifact_content(artifact: Artifact) -> str:
    """The reviewable substance of an artifact, per type."""
    for field in ("body", "summary"):
        value = getattr(artifact, field, None)
        if value:
            return str(value)
    return artifact.model_dump_json()


class Coordinator(Agent):
    name = "coordinator"
    tier = "planner"
    role_prompt = (
        "You are the Coordinator. You plan against the SOP, delegate to specialist "
        "agents, review their outputs, and assemble the deliverable. You do not do "
        "specialist work and you never send anything externally. On ambiguity, "
        "missing information, or low confidence you stop and escalate to the owner."
    )

    def __init__(
        self,
        adapter: BusinessAdapter,
        store: JobStore,
        trace: TraceWriter,
        model: ModelClient,
        kill_switch: KillSwitch | None = None,
        gate: PolicyGate | None = None,
        escalations: EscalationQueue | None = None,
        email_sender=None,  # EmailSender protocol; None => sends fail loudly
    ) -> None:
        self.email_sender = email_sender
        self.adapter = adapter
        self.store = store
        self.trace = trace
        self.model = model
        self.kill_switch = kill_switch or KillSwitch()
        self.gate = gate or PolicyGate(
            gated_kinds=set(
                adapter.policies.get(
                    "gated_actions", ["send", "spend", "publish", "contract", "live_settings"]
                )
            )
        )
        self.escalations = escalations or EscalationQueue()
        self.roster: dict[str, Agent] = {a.name: a for a in (ResearchAgent(), MakerAgent())}
        self.artifacts: dict[str, Artifact] = {}  # P0 artifact store (in-memory)
        # The autonomy dial persists — earned trust must survive restarts.
        if saved := store.load_state(_DIAL_STATE_KEY):
            self.gate.dial = AutonomyDial.model_validate_json(saved)
        if threshold := adapter.policies.get("autonomy", {}).get("upgrade_threshold"):
            self.gate.dial.upgrade_threshold = threshold

    # -- job lifecycle --------------------------------------------------------

    def handle_event(self, event: Event) -> Job | None:
        sop = self.adapter.workflow_for_trigger(event.type)
        if sop is None:
            return None  # not our workflow; other listeners may care
        job = Job(
            workflow=sop.name,
            adapter=self.adapter.name,
            trigger_event_id=event.id,
            context=dict(event.metadata),
        )
        self.store.save_job(job)
        self.trace.record(job.id, "job_opened", agent=self.name,
                          payload={"event": event.type, "workflow": sop.name})
        return self.run_job(job, sop)

    def run_job(self, job: Job, sop: SOP) -> Job:
        # Global daily brake (PRD §10): if the day's spend already hit the cap,
        # refuse to start — escalate to the owner instead of quietly queuing.
        daily_cap = self.adapter.policies.get("budgets", {}).get("global_daily", {}).get("max_usd")
        if daily_cap is not None:
            spent_today = self.store.spend_on(datetime.now(timezone.utc).date())
            if spent_today >= daily_cap:
                transition(job, JobState.ESCALATED)
                job.error = f"global daily budget exhausted: ${spent_today:.4f} >= ${daily_cap}"
                self.store.save_job(job)
                self.escalations.raise_escalation(Escalation(
                    reason=EscalationReason.BUDGET_EDGE,
                    question=f"Daily cap ${daily_cap} is spent (${spent_today:.4f}). "
                             f"Raise the cap in policies.yaml, or let job {job.id} wait for tomorrow?",
                    produced_by=self.name, job_id=job.id,
                ))
                self.trace.record(job.id, "escalation", agent=self.name,
                                  payload={"reason": "global_daily_budget", "cap": daily_cap})
                return job

        budget = Budget(**self.adapter.policies.get("budgets", {}).get("per_job", {}))
        ctx = AgentContext(
            job_id=job.id, model=self.model, budget=budget,
            kill_switch=self.kill_switch, trace=self.trace,
            profile=self.adapter.profile,
        )
        try:
            transition(job, JobState.PLANNING)
            plan = self._plan(ctx, job, sop)

            transition(job, JobState.EXECUTING)
            outputs = self._execute(ctx, job, sop, plan)

            transition(job, JobState.REVIEWING)
            deliverable = self._assemble(ctx, job, outputs)

            self._gate_or_finish(job, sop, deliverable, budget)
        except BudgetExceeded as exc:
            transition(job, JobState.ESCALATED)
            job.error = str(exc)
            self.escalations.raise_escalation(
                self.escalate(ctx, reason=EscalationReason.BUDGET_EDGE,
                              question=f"Job {job.id} hit its budget cap: {exc}. Raise cap, or cancel?")
            )
        except ReviewFailed as exc:
            transition(job, JobState.ESCALATED)
            job.error = str(exc)
            self.escalations.raise_escalation(
                self.escalate(
                    ctx, reason=EscalationReason.LOW_CONFIDENCE,
                    question=(f"Job {job.id}: step '{exc.step_id}' failed review after "
                              f"{exc.rounds} rework(s). Accept the latest attempt manually, "
                              f"adjust the SOP, or cancel?"),
                    context=exc.feedback[:1000],
                )
            )
        finally:
            job.spend_tokens = budget.tokens
            job.spend_usd = round(budget.usd, 6)
            self.store.save_job(job)
        return job

    # -- phases ----------------------------------------------------------------

    def _plan(self, ctx: AgentContext, job: Job, sop: SOP) -> Plan:
        steps = [
            PlanStep(
                id=s.id, agent=s.agent, action=s.action,
                inputs={"names": s.inputs}, checkpoint=s.checkpoint, done_when=s.done_when,
            )
            for s in sop.steps
        ]
        review = self.ask_model(
            ctx,
            f"Job context: {job.context}\nSOP '{sop.name}' v{sop.version} steps: "
            f"{[s.id for s in sop.steps]}. Does this SOP fit the trigger? Note any mismatch.",
        )
        plan = Plan(
            goal=sop.description or sop.name, workflow=sop.name,
            steps=steps, notes=review.text, produced_by=self.name, job_id=job.id,
        )
        self._keep(job, plan)
        return plan

    def _execute(self, ctx: AgentContext, job: Job, sop: SOP, plan: Plan) -> dict[str, Artifact]:
        review_cfg = self.adapter.policies.get("review", {})
        review_enabled: bool = review_cfg.get("enabled", True)
        max_reworks: int = review_cfg.get("max_reworks", 1)

        outputs: dict[str, Artifact] = {}
        for sop_step in sop.steps:
            if sop_step.agent == self.name:
                continue  # coordinator's own steps are plan/review/assemble
            agent = self.roster.get(sop_step.agent)
            if agent is None:
                raise ValueError(f"SOP step '{sop_step.id}' names unknown agent '{sop_step.agent}'")
            # Least privilege per step: the acting agent gets exactly the tools
            # the adapter grants it — nothing carries over between agents.
            grants = self.adapter.agent_tools(sop_step.agent)
            ctx.tools = grants
            task: dict = {
                "subject": job.context.get("subject", job.workflow),
                "raw_context": job.context.get("raw_context", ""),
                "source": job.context.get("source", "trigger"),
                "kind": sop_step.output or sop_step.action,
                "instructions": sop.body,
            }
            for name in sop_step.inputs:
                if name in outputs:
                    task[name] = outputs[name]
            artifact = agent.perform(ctx, task)
            self._keep(job, artifact)

            # Review-before-consumption: the coordinator judges each specialist
            # output against the step's done_when BEFORE downstream steps use it.
            if review_enabled:
                rounds = 0
                while True:
                    verdict, feedback = self._review(ctx, sop_step, artifact)
                    if verdict != "revise":
                        break
                    if rounds >= max_reworks:
                        raise ReviewFailed(sop_step.id, feedback, rounds)
                    rounds += 1
                    task["feedback"] = feedback
                    ctx.tools = grants  # restore after the review call
                    artifact = agent.perform(ctx, task)
                    self._keep(job, artifact)  # every attempt stays on the record
                    self.trace.record(job.id, "rework", agent=sop_step.agent,
                                      payload={"step": sop_step.id, "round": rounds,
                                               "artifact": artifact.id})

            outputs[sop_step.output or sop_step.id] = artifact
            self.trace.record(job.id, "step_done", agent=sop_step.agent,
                              payload={"step": sop_step.id, "artifact": artifact.id})
        ctx.tools = []  # don't leak the last specialist's grants forward
        return outputs

    def _review(self, ctx: AgentContext, sop_step, artifact: Artifact) -> tuple[str, str]:
        """One review round. Returns (verdict, feedback). Unparseable replies
        fail OPEN (counted as pass) but are traced — review is a safety net,
        not a wall that stalls jobs on a formatting hiccup."""
        saved_tools = ctx.tools
        ctx.tools = []  # the coordinator reviews with no tools
        try:
            from hive.agents.model import REVIEW_PROMPT_PREFIX

            prompt = (
                f"{REVIEW_PROMPT_PREFIX}\n"
                f"Step: {sop_step.id} (action: {sop_step.action}, agent: {sop_step.agent})\n"
                f"Done when: {sop_step.done_when or 'no explicit criterion — judge fitness for purpose'}\n\n"
                f"Output to review:\n{_artifact_content(artifact)[:4000]}\n\n"
                'Reply starting with exactly "VERDICT: pass" or "VERDICT: revise". '
                "If revise, follow with concrete, actionable feedback the specialist "
                "can apply — name what is missing or wrong, not just that it is."
            )
            response = self.ask_model(ctx, prompt)
        finally:
            ctx.tools = saved_tools
        verdict, feedback = _parse_verdict(response.text)
        self.trace.record(ctx.job_id, "review", agent=self.name,
                          payload={"step": sop_step.id, "verdict": verdict,
                                   "artifact": artifact.id})
        if verdict == "unparsed":
            return "pass", response.text  # fail-open, already traced as unparsed
        return verdict, feedback

    def _assemble(self, ctx: AgentContext, job: Job, outputs: dict[str, Artifact]) -> Deliverable:
        deliverable = Deliverable(
            title=f"{job.workflow} — {job.context.get('subject', job.id)}",
            parts=[a.id for a in outputs.values()],
            summary=f"Assembled from: {', '.join(outputs.keys())}",
            produced_by=self.name, job_id=job.id,
        )
        self._keep(job, deliverable)
        return deliverable

    def _gate_or_finish(self, job: Job, sop: SOP, deliverable: Deliverable, budget: Budget) -> None:
        """One approval at the end (PRD §7): gate on the SOP's gated step."""
        gated_step = next(
            (s for s in sop.steps if s.checkpoint or self.gate.evaluate(s.action_kind, sop.step_key(s.id)) != GateDecision.ALLOW),
            None,
        )
        if gated_step is None:
            # Earned autonomy path (L2+): no card, but a send step still
            # EXECUTES — auto-with-audit, exactly what the dial promises.
            send_step = next((s for s in sop.steps if s.action_kind == "send"), None)
            if send_step is not None:
                try:
                    detail = self._execute_send(job, subject=deliverable.title)
                    self.trace.record(job.id, "send_executed", agent=self.name,
                                      payload={"detail": detail, "auto": True,
                                               "step_key": sop.step_key(send_step.id)})
                except Exception as exc:
                    transition(job, JobState.FAILED)
                    job.error = f"auto-send failed: {exc}"
                    self.trace.record(job.id, "send_failed", agent=self.name,
                                      payload={"error": str(exc), "auto": True})
                    return
            transition(job, JobState.DONE)
            self.trace.record(job.id, "job_done", agent=self.name)
            return
        preview_parts = []
        for artifact_id in deliverable.parts:
            artifact = self.artifacts.get(artifact_id)
            if artifact is not None and hasattr(artifact, "body"):
                preview_parts.append(artifact.body)  # type: ignore[attr-defined]
        effects = [gated_step.done_when or f"{gated_step.action_kind} executes"]
        if gated_step.action_kind == "send":
            recipient = job.context.get("reply_to") or "NO RECIPIENT — job context lacks reply_to; send will fail"
            effects.append(f"on approve: email goes to {recipient}")
        card = ApprovalCard(
            job_id=job.id,
            step_key=sop.step_key(gated_step.id),
            action_kind=gated_step.action_kind,
            title=deliverable.title,
            artifact_id=deliverable.id,
            artifact_preview="\n---\n".join(preview_parts) or deliverable.summary,
            reasoning=f"SOP '{sop.name}' step '{gated_step.id}' ({gated_step.action_kind}) requires owner approval.",
            cost_so_far_usd=round(budget.usd, 6),
            downstream_effects=effects,
        )
        self.store.save_card(card)
        transition(job, JobState.AWAITING_APPROVAL)
        self.trace.record(job.id, "approval_requested", agent=self.name, payload={"card": card.id})

    # -- owner decisions ---------------------------------------------------------

    def resolve_approval(self, card_id: str, status: ApprovalStatus, note: str = "") -> Job:
        card = self.store.get_card(card_id)
        if card is None:
            raise KeyError(f"no such approval card: {card_id}")
        job = self.store.get_job(card.job_id)
        if job is None:
            raise KeyError(f"card {card_id} references missing job {card.job_id}")
        card.decide(status, note)
        self.store.save_card(card)
        job.owner_touches += 1
        if status in (ApprovalStatus.APPROVED, ApprovalStatus.EDITED):
            # APPROVE on a send card = HIVE sends. EDIT = the owner takes
            # delivery manual: job completes, nothing is sent by HIVE.
            if status is ApprovalStatus.APPROVED and card.action_kind == "send":
                try:
                    detail = self._execute_send(job, subject=card.title)
                    self.trace.record(job.id, "send_executed", agent=self.name,
                                      payload={"detail": detail, "card": card.id})
                except Exception as exc:
                    transition(job, JobState.FAILED)
                    job.error = f"approved send failed: {exc}"
                    self.trace.record(job.id, "send_failed", agent=self.name,
                                      payload={"error": str(exc), "card": card.id})
                    self.store.save_state(_DIAL_STATE_KEY, self.gate.dial.model_dump_json())
                    self.store.save_job(job)
                    return job
            transition(job, JobState.DONE)
            if status is ApprovalStatus.APPROVED:
                if self.gate.dial.record_approval(card.step_key):
                    self.trace.record(job.id, "autonomy_upgrade_proposed", agent=self.name,
                                      payload={"step_key": card.step_key})
            else:
                self.gate.dial.record_rejection(card.step_key)  # edits reset the streak
        else:
            transition(job, JobState.CANCELLED)
            self.gate.dial.record_rejection(card.step_key)
        self.store.save_state(_DIAL_STATE_KEY, self.gate.dial.model_dump_json())
        self.store.save_job(job)
        self.trace.record(job.id, "approval_decided", agent=self.name,
                          payload={"card": card.id, "status": status.value})
        return job

    def _execute_send(self, job: Job, subject: str) -> str:
        """The actual consequential action. Composes the email from persisted
        artifacts (latest Draft body) — never from a model call. Raises on any
        problem; callers translate that into FAILED + a visible error."""
        if self.email_sender is None:
            raise RuntimeError("no email sender configured (see HIVE_EMAIL_BACKEND)")
        recipient = str(job.context.get("reply_to", "")).strip()
        if "@" not in recipient:
            raise ValueError("no recipient: job context has no 'reply_to' address")
        drafts = [a for a in self.store.list_artifacts(job.id) if a["artifact_type"] == "Draft"]
        if not drafts:
            raise ValueError("nothing to send: job produced no Draft artifact")
        return self.email_sender.send(
            to=recipient, subject=subject, body=drafts[-1]["body"], job_id=job.id
        )

    def ratify_autonomy(self, step_key: str):
        """Owner ratifies a proposed autonomy upgrade (from the digest)."""
        level = self.gate.dial.ratify_upgrade(step_key)
        self.store.save_state(_DIAL_STATE_KEY, self.gate.dial.model_dump_json())
        return level

    # -- helpers -------------------------------------------------------------------

    def _keep(self, job: Job, artifact: Artifact) -> None:
        self.artifacts[artifact.id] = artifact
        job.artifact_ids.append(artifact.id)
        self.store.save_artifact(job.id, artifact)  # persist so the API can serve it
