"""Tracing (PRD §9): every job = a complete trace; every rupee attributed.

P0 seed: append-only JSONL per job. Replay tooling reads these files.
Grows toward OpenTelemetry-style spans when it earns it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TraceWriter:
    def __init__(self, trace_dir: str | Path) -> None:
        self.trace_dir = Path(trace_dir)
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        return self.trace_dir / f"{job_id}.jsonl"

    def record(
        self,
        job_id: str,
        event_type: str,
        agent: str = "",
        payload: dict[str, Any] | None = None,
        tokens: int = 0,
        usd: float = 0.0,
    ) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "type": event_type,
            "agent": agent,
            "tokens": tokens,
            "usd": usd,
            "payload": payload or {},
        }
        with self._path(job_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def read(self, job_id: str) -> list[dict[str, Any]]:
        path = self._path(job_id)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
