"""Budgets & kill switch (PRD §10).

Per-job budgets pause-and-escalate at the cap. Loop guards (max steps) live
here too. Charging past a cap raises BudgetExceeded — the caller escalates,
it never guesses its way through.
"""

from __future__ import annotations

from pydantic import BaseModel


class BudgetExceeded(Exception):
    def __init__(self, what: str, spent: float, cap: float) -> None:
        self.what, self.spent, self.cap = what, spent, cap
        super().__init__(f"budget exceeded: {what} spent={spent} cap={cap}")


class KillSwitch:
    """One global pause button (PRD §11.7). Checked before every model call."""

    def __init__(self) -> None:
        self._engaged = False

    def engage(self) -> None:
        self._engaged = True

    def release(self) -> None:
        self._engaged = False

    @property
    def engaged(self) -> bool:
        return self._engaged

    def check(self) -> None:
        if self._engaged:
            raise RuntimeError("kill switch engaged — all agent activity paused")


class Budget(BaseModel):
    """Tracks spend against hard caps. One instance per job (or per agent)."""

    max_steps: int = 40
    max_tokens: int = 200_000
    max_usd: float = 2.0

    steps: int = 0
    tokens: int = 0
    usd: float = 0.0

    def charge_step(self) -> None:
        self.steps += 1
        if self.steps > self.max_steps:
            raise BudgetExceeded("steps", self.steps, self.max_steps)

    def charge_tokens(self, n: int, usd: float = 0.0) -> None:
        self.tokens += n
        self.usd += usd
        if self.tokens > self.max_tokens:
            raise BudgetExceeded("tokens", self.tokens, self.max_tokens)
        if self.usd > self.max_usd:
            raise BudgetExceeded("usd", round(self.usd, 4), self.max_usd)

    @property
    def near_edge(self) -> bool:
        """>=80% of any cap — agents must escalate rather than push through."""
        return (
            self.steps >= 0.8 * self.max_steps
            or self.tokens >= 0.8 * self.max_tokens
            or self.usd >= 0.8 * self.max_usd
        )
