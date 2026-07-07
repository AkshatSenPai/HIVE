"""Event bus (PRD §4, [PROVEN]) — in-process for P0.

Metadata-first (the Zenith M7 watcher pattern): events carry cheap metadata;
payload bodies are fetched lazily by whoever handles the event, so idle
listening costs ~zero tokens. Email/webhook/schedule sources plug in later
as producers; for P0 the only producer is the CLI (manual trigger).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, Field


class Event(BaseModel):
    id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    type: str  # e.g. "lead.new", "order.created", "schedule.daily_digest"
    source: str = "manual"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


Handler = Callable[[Event], None]


class EventBus:
    """Synchronous pub/sub. Deliberately boring; swap for a queue when needed."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event: Event) -> int:
        """Deliver to all handlers for the type. Returns handler count."""
        handlers = self._handlers.get(event.type, [])
        for handler in handlers:
            handler(event)
        return len(handlers)
