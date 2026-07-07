"""Escalations (PRD §3.3): agents stop-and-ask; guessing is a defect."""

from __future__ import annotations

from hive.artifacts import Escalation


class EscalationQueue:
    """P0: in-memory queue surfaced through the CLI/digest."""

    def __init__(self) -> None:
        self._items: list[Escalation] = []

    def raise_escalation(self, escalation: Escalation) -> Escalation:
        self._items.append(escalation)
        return escalation

    def pending(self) -> list[Escalation]:
        return list(self._items)

    def resolve(self, escalation_id: str) -> None:
        self._items = [e for e in self._items if e.id != escalation_id]
