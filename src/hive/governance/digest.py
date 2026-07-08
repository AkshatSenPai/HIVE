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


def _money_phrase(usd: float) -> str:
    """TTS-friendly money: '4 cents', '1 cent', '1 dollar', '18,000 dollars'."""
    usd = float(usd)
    if usd < 1:
        cents = round(usd * 100)
        return f"{cents} cent" + ("" if cents == 1 else "s")
    body = f"{usd:,.0f}" if usd == round(usd) else f"{usd:,.2f}"
    return f"{body} " + ("dollar" if usd == 1 else "dollars")


def build_brief(store: JobStore, policies: dict[str, Any] | None = None) -> str:
    """A short, prioritized, spoken-style status briefing (PRD §3.2, voice).

    The speakable cousin of the digest: prioritized (what needs you first),
    conversational, a few sentences. Deterministic and $0 — same source data
    as the digest, phrased for the ear."""
    jobs = store.list_jobs()
    pending = store.list_cards(status=ApprovalStatus.PENDING.value)
    attention = [j for j in jobs if j.state in (JobState.ESCALATED, JobState.FAILED)]
    inflight = [
        j for j in jobs
        if j.state in (JobState.QUEUED, JobState.PLANNING, JobState.EXECUTING, JobState.REVIEWING)
    ]

    parts: list[str] = []

    if not pending and not attention:
        parts.append("All clear — nothing is waiting on you right now.")
    else:
        bits = []
        if pending:
            bits.append(f"{len(pending)} approval" + ("" if len(pending) == 1 else "s") + " waiting")
        if attention:
            bits.append(f"{len(attention)} job" + ("" if len(attention) == 1 else "s") + " needing attention")
        parts.append("You have " + " and ".join(bits) + ".")

    if pending:
        detail = "; ".join(f"{c.title}, {_money_phrase(c.cost_so_far_usd)}" for c in pending[:3])
        parts.append("Waiting for you: " + detail + ".")
        if len(pending) > 3:
            parts.append(f"And {len(pending) - 3} more in the approvals queue.")

    if attention:
        detail = "; ".join(
            f"{j.workflow} {j.state.value}" + (f", {j.error}" if j.error else "")
            for j in attention[:3]
        )
        parts.append("Needs attention: " + detail + ".")

    if inflight:
        parts.append(f"{len(inflight)} job" + ("" if len(inflight) == 1 else "s") + " in progress.")

    spent_today = store.spend_on(datetime.now(timezone.utc).date())
    cap = (policies or {}).get("budgets", {}).get("global_daily", {}).get("max_usd")
    money = f"You've spent {_money_phrase(spent_today)} today"
    if cap:
        try:  # a misconfigured (non-numeric) cap must not 500 the brief
            money += f", of a {_money_phrase(float(cap))} daily cap"
        except (TypeError, ValueError):
            pass
    parts.append(money + ".")

    return " ".join(parts)
