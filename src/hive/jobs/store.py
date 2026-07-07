"""SQLite persistence for jobs and approval cards (episodic memory's spine).

SQLite until it hurts (PRD §17); the store is the only module that knows the
schema, so a Postgres swap stays local.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from hive.artifacts import Artifact
from hive.governance.approvals import ApprovalCard
from hive.jobs.models import Job

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    workflow TEXT NOT NULL,
    adapter TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approval_cards (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_job ON artifacts (job_id);
CREATE TABLE IF NOT EXISTS kv_state (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
"""


class JobStore:
    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False so the same store can back the CLI and the
        # (threaded) API server. Fine for a single-owner P0 dashboard; revisit
        # with a connection pool / Postgres when concurrency is real.
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- jobs ---------------------------------------------------------------

    def save_job(self, job: Job) -> None:
        self._conn.execute(
            "INSERT INTO jobs (id, state, workflow, adapter, data) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET state=excluded.state, data=excluded.data",
            (job.id, job.state.value, job.workflow, job.adapter, job.model_dump_json()),
        )
        self._conn.commit()

    def get_job(self, job_id: str) -> Job | None:
        row = self._conn.execute("SELECT data FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job.model_validate(json.loads(row[0])) if row else None

    def list_jobs(self, state: str | None = None) -> list[Job]:
        if state:
            rows = self._conn.execute("SELECT data FROM jobs WHERE state = ?", (state,)).fetchall()
        else:
            rows = self._conn.execute("SELECT data FROM jobs").fetchall()
        return [Job.model_validate(json.loads(r[0])) for r in rows]

    # -- approval cards -----------------------------------------------------

    def save_card(self, card: ApprovalCard) -> None:
        self._conn.execute(
            "INSERT INTO approval_cards (id, job_id, status, data) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, data=excluded.data",
            (card.id, card.job_id, card.status.value, card.model_dump_json()),
        )
        self._conn.commit()

    def get_card(self, card_id: str) -> ApprovalCard | None:
        row = self._conn.execute(
            "SELECT data FROM approval_cards WHERE id = ?", (card_id,)
        ).fetchone()
        return ApprovalCard.model_validate(json.loads(row[0])) if row else None

    def list_cards(self, status: str | None = None) -> list[ApprovalCard]:
        if status:
            rows = self._conn.execute(
                "SELECT data FROM approval_cards WHERE status = ?", (status,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT data FROM approval_cards").fetchall()
        return [ApprovalCard.model_validate(json.loads(r[0])) for r in rows]

    # -- artifacts ----------------------------------------------------------
    # Stored as (kind, json) so the API can serve produced content — briefs,
    # drafts, shortlists — without a fragile polymorphic loader.

    def save_artifact(self, job_id: str, artifact: Artifact) -> None:
        self._conn.execute(
            "INSERT INTO artifacts (id, job_id, kind, data) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (artifact.id, job_id, artifact.__class__.__name__, artifact.model_dump_json()),
        )
        self._conn.commit()

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT kind, data FROM artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
        if not row:
            return None
        # 'artifact_type' (the class), distinct from a Draft's own 'kind' field.
        return {"artifact_type": row[0], **json.loads(row[1])}

    def list_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT kind, data FROM artifacts WHERE job_id = ? ORDER BY rowid",
            (job_id,),
        ).fetchall()  # insertion order: the last Draft is the newest rework
        return [{"artifact_type": r[0], **json.loads(r[1])} for r in rows]

    # -- kv state -------------------------------------------------------------
    # Small persistent singletons (e.g. the autonomy dial) that must survive
    # process restarts but don't deserve their own table.

    def save_state(self, key: str, data: str) -> None:
        self._conn.execute(
            "INSERT INTO kv_state (key, data) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET data=excluded.data",
            (key, data),
        )
        self._conn.commit()

    def load_state(self, key: str) -> str | None:
        row = self._conn.execute("SELECT data FROM kv_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    # -- spend queries --------------------------------------------------------

    def spend_on(self, day: "date") -> float:
        """Total $ spent by jobs last touched on the given UTC date."""
        return sum(j.spend_usd for j in self.list_jobs() if j.updated_at.date() == day)

    def close(self) -> None:
        self._conn.close()
