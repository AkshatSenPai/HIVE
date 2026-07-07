"""Legal state transitions for jobs. Anything else raises."""

from __future__ import annotations

from datetime import datetime, timezone

from hive.jobs.models import Job, JobState

TRANSITIONS: dict[JobState, set[JobState]] = {
    # queued -> escalated: a job can be refused before planning starts
    # (e.g. the global daily budget is already exhausted)
    JobState.QUEUED: {JobState.PLANNING, JobState.ESCALATED, JobState.CANCELLED},
    JobState.PLANNING: {JobState.EXECUTING, JobState.ESCALATED, JobState.FAILED, JobState.CANCELLED},
    JobState.EXECUTING: {JobState.REVIEWING, JobState.ESCALATED, JobState.FAILED, JobState.CANCELLED},
    JobState.REVIEWING: {
        JobState.EXECUTING,  # coordinator sends work back
        JobState.AWAITING_APPROVAL,
        JobState.DONE,  # only when no step in the plan is gated
        JobState.ESCALATED,
        JobState.FAILED,
        JobState.CANCELLED,
    },
    JobState.AWAITING_APPROVAL: {
        JobState.DONE,       # approved (send executed, if any)
        JobState.EXECUTING,  # owner edited / requested changes
        JobState.CANCELLED,  # rejected
        JobState.FAILED,     # approved, but the send action itself failed
    },
    JobState.ESCALATED: {JobState.PLANNING, JobState.EXECUTING, JobState.CANCELLED, JobState.FAILED},
    JobState.DONE: set(),
    JobState.FAILED: {JobState.QUEUED},  # explicit retry only
    JobState.CANCELLED: set(),
}


class InvalidTransition(Exception):
    pass


def transition(job: Job, to: JobState) -> Job:
    if to not in TRANSITIONS[job.state]:
        raise InvalidTransition(f"{job.id}: {job.state.value} -> {to.value} is not allowed")
    job.state = to
    job.updated_at = datetime.now(timezone.utc)
    return job
