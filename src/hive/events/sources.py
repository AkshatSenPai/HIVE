"""Event sources (PRD §4): producers that feed the event bus.

P0 sources, both credential-free:

- **File inbox** (here): drop a file into `<data_dir>/inbox/` and HIVE opens a
  job. The universal bridge — anything that can write a file (an email tool,
  Zapier, a cron script) can feed HIVE. Processed files are archived under
  `inbox/processed/`, malformed ones quarantined under `inbox/failed/` —
  nothing is silently lost.
- **Webhook** (`POST /events` in the API): push events over HTTP, gated by a
  shared token.

File formats:
- `*.json` — `{"type": "...", "subject": "...", "raw_context": "...",
  "source": "...", "metadata": {...}}`; all fields optional. `type` defaults
  to the configured inbox event type; `subject` to the filename.
- `*.txt` / `*.md` — the whole body becomes `raw_context` (untrusted, fenced
  downstream); the first line becomes a sanitized subject.

Inbox content is EXTERNAL and untrusted: bodies ride in `raw_context`, which
agents always fence before prompting. Subjects are sanitized (one line,
length-capped) but do reach prompts as plain text — keep that in mind and
keep the real content in the body.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from hive.events.bus import Event

if TYPE_CHECKING:
    from hive.jobs.models import Job
    from hive.runtime import Runtime

_SUBJECT_MAX = 150
_ACCEPTED = {".json", ".txt", ".md"}


def sanitize_subject(text: str, fallback: str = "untitled") -> str:
    """First non-empty line, whitespace collapsed, control chars stripped,
    length capped. Subjects reach prompts un-fenced — keep them inert."""
    for line in text.splitlines():
        cleaned = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", line)).strip()
        if cleaned:
            return cleaned[:_SUBJECT_MAX]
    return fallback


class FileInboxSource:
    """Polls a directory for dropped files and turns each into an event."""

    def __init__(self, runtime: "Runtime") -> None:
        self.runtime = runtime
        self.inbox = runtime.config.inbox_dir
        self.processed = self.inbox / "processed"
        self.failed = self.inbox / "failed"
        for directory in (self.inbox, self.processed, self.failed):
            directory.mkdir(parents=True, exist_ok=True)
        self.default_type = runtime.config.inbox_event_type

    # -- parsing ---------------------------------------------------------------

    def _event_from_file(self, path: Path) -> Event:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("inbox json must be an object")
            metadata = dict(data.get("metadata", {}))
            metadata.setdefault("subject", sanitize_subject(str(data.get("subject", "")), fallback=path.stem))
            metadata.setdefault("raw_context", str(data.get("raw_context", "")))
            metadata.setdefault("source", f"inbox:{data.get('source', path.name)}")
            if "reply_to" in data:  # email bridges put the sender here
                metadata.setdefault("reply_to", str(data["reply_to"]))
            return Event(type=str(data.get("type", self.default_type)), source="inbox", metadata=metadata)
        # .txt / .md: body is untrusted context; first line becomes the subject
        return Event(
            type=self.default_type,
            source="inbox",
            metadata={
                "subject": sanitize_subject(text, fallback=path.stem),
                "raw_context": text,
                "source": f"inbox:{path.name}",
            },
        )

    # -- polling ---------------------------------------------------------------

    def poll_once(self, on_job: Callable[["Job"], None] | None = None) -> int:
        """Process every waiting file. Returns how many produced a job."""
        jobs = 0
        for path in sorted(self.inbox.iterdir()):
            if not path.is_file() or path.suffix.lower() not in _ACCEPTED:
                continue
            try:
                event = self._event_from_file(path)
                job = self.runtime.trigger(event)
                self._archive(path, self.processed)
                if job is not None:
                    jobs += 1
                    if on_job:
                        on_job(job)
            except Exception as exc:  # quarantine, never crash the watcher
                self._archive(path, self.failed)
                (self.failed / f"{path.name}.error.txt").write_text(str(exc), encoding="utf-8")
        return jobs

    def watch(
        self,
        interval: float | None = None,
        stop: threading.Event | None = None,
        on_job: Callable[["Job"], None] | None = None,
    ) -> None:
        """Blocking poll loop; runs until `stop` is set (or forever)."""
        interval = interval or self.runtime.config.inbox_poll_seconds
        stop = stop or threading.Event()
        while not stop.is_set():
            self.poll_once(on_job=on_job)
            stop.wait(interval)

    def _archive(self, path: Path, target_dir: Path) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        destination = target_dir / f"{stamp}-{path.name}"
        counter = 0
        while destination.exists():  # same name, same second
            counter += 1
            destination = target_dir / f"{stamp}-{counter}-{path.name}"
        path.rename(destination)
