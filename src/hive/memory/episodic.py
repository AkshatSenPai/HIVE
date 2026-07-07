"""Episodic memory: what has happened (PRD §4).

The jobs DB *is* the episodic store; this module is the query surface over
it. The reflection loop (P3, [FRONTIER]) will mine these queries for
patterns — deliberately not built yet.
"""

from __future__ import annotations

from hive.jobs.models import Job, JobState
from hive.jobs.store import JobStore


class EpisodicMemory:
    def __init__(self, store: JobStore) -> None:
        self._store = store

    def history(self, workflow: str | None = None) -> list[Job]:
        jobs = self._store.list_jobs()
        if workflow:
            jobs = [j for j in jobs if j.workflow == workflow]
        return sorted(jobs, key=lambda j: j.created_at)

    def outcomes(self, workflow: str) -> dict[str, int]:
        """Done/failed/cancelled counts — the raw material for reflection."""
        counts: dict[str, int] = {}
        for job in self.history(workflow):
            if job.state in (JobState.DONE, JobState.FAILED, JobState.CANCELLED):
                counts[job.state.value] = counts.get(job.state.value, 0) + 1
        return counts
