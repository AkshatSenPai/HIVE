"""Daily digest (PRD §3.2): jobs done/in-flight/stuck, spend, approvals
waiting, anomalies, and the system's own improvement proposals."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from hive.governance.approvals import ApprovalStatus
from hive.jobs.models import JobState
from hive.policy.autonomy import AutonomyDial, AutonomyLevel

if TYPE_CHECKING:  # runtime import would be circular (store persists ApprovalCards)
    from hive.jobs.store import JobStore


def build_digest(store: JobStore, policies: dict[str, Any] | None = None) -> str:
    jobs = store.list_jobs()
    by_state: dict[str, int] = {}
    total_usd = 0.0
    total_tokens = 0
    for job in jobs:
        by_state[job.state.value] = by_state.get(job.state.value, 0) + 1
        total_usd += job.spend_usd
        total_tokens += job.spend_tokens

    pending_cards = store.list_cards(status=ApprovalStatus.PENDING.value)
    stuck = [j for j in jobs if j.state in (JobState.ESCALATED, JobState.FAILED)]

    lines = ["=== HIVE DAILY DIGEST ===", ""]
    lines.append(f"jobs: {len(jobs)} total")
    for state, count in sorted(by_state.items()):
        lines.append(f"  {state}: {count}")
    lines.append(f"spend: {total_tokens} tokens / ${total_usd:.4f} (all time)")

    spent_today = store.spend_on(datetime.now(timezone.utc).date())
    cap = (policies or {}).get("budgets", {}).get("global_daily", {}).get("max_usd")
    lines.append(f"spend today: ${spent_today:.4f}" + (f" / ${cap} daily cap" if cap else ""))

    lines.append(f"approvals waiting: {len(pending_cards)}")
    for card in pending_cards:
        lines.append(f"  - {card.id}: {card.title}")
    if stuck:
        lines.append("needs attention:")
        for job in stuck:
            lines.append(f"  - {job.id} [{job.state.value}] {job.error or job.workflow}")

    # Improvement proposals (PRD §3.2 / §3): autonomy upgrades EARNED by
    # approval streaks, waiting on owner ratification.
    if raw := store.load_state("autonomy_dial"):
        dial = AutonomyDial.model_validate_json(raw)
        proposals = [(key, rec) for key, rec in dial.steps.items() if rec.upgrade_proposed]
        if proposals:
            lines.append("improvement proposals:")
            for key, rec in proposals:
                next_level = AutonomyLevel(min(rec.level + 1, AutonomyLevel.L3_AUTONOMOUS))
                lines.append(
                    f"  - {key}: {rec.consecutive_approvals} consecutive approvals "
                    f"-> upgrade {rec.level.name} to {next_level.name}  (hive ratify {key})"
                )
    return "\n".join(lines)
