"""Eval harness (PRD §9, non-negotiable) — golden cases per workflow.

Every SOP / prompt / model change runs the suite before it ships; scores are
appended to a history file so drift is visible over time.

Two kinds of checks, honestly separated:

- **Structural checks** run on ANY backend (including the offline stub):
  final job state, artifacts produced, approval gating, spend cap. This is
  the fast regression suite — it catches broken plumbing in seconds.
- **Content checks** (must_contain / must_not_contain / max_words against a
  produced artifact) need real generation. On the stub backend they are
  SKIPPED and reported as skipped — never silently passed.

Cases are adapter-owned (they encode business expectations, like SOPs):
`adapters/<business>/evals/**/*.yaml`. Each case runs in a fresh throwaway
data dir, so evals never contaminate real jobs, budgets, or dial streaks.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from hive.config import HiveConfig
from hive.events.bus import Event
from hive.governance.approvals import ApprovalStatus
from hive.runtime import Runtime

# -- case schema ---------------------------------------------------------------


class CaseEvent(BaseModel):
    type: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class StructuralChecks(BaseModel):
    final_state: str | None = None
    artifacts: list[str] = Field(default_factory=list)  # required subset of artifact_types
    pending_card: bool | None = None
    max_usd: float | None = None


class ContentCheck(BaseModel):
    artifact: str  # artifact_type, e.g. "Draft"
    field: str = "body"
    must_contain: list[str] = Field(default_factory=list)  # case-insensitive
    must_not_contain: list[str] = Field(default_factory=list)
    max_words: int | None = None


class EvalCase(BaseModel):
    name: str
    workflow: str
    event: CaseEvent
    checks: StructuralChecks = Field(default_factory=StructuralChecks)
    content: list[ContentCheck] = Field(default_factory=list)


# -- results -------------------------------------------------------------------


class CheckOutcome(BaseModel):
    check: str
    ok: bool
    detail: str = ""


class CaseResult(BaseModel):
    name: str
    workflow: str
    backend: str
    passed: bool
    outcomes: list[CheckOutcome] = Field(default_factory=list)
    content_skipped: bool = False
    job_state: str = ""
    spend_usd: float = 0.0
    spend_tokens: int = 0
    ts: str = ""

    @property
    def summary(self) -> str:
        done = sum(1 for o in self.outcomes if o.ok)
        skip = " (content skipped: stub backend)" if self.content_skipped else ""
        return f"{done}/{len(self.outcomes)} checks{skip}"


class SuiteResult(BaseModel):
    adapter: str
    backend: str
    cases: list[CaseResult] = Field(default_factory=list)
    ts: str = ""

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def score(self) -> float:
        return self.passed / len(self.cases) if self.cases else 0.0


# -- loading -------------------------------------------------------------------


def load_cases(adapter_dir: str | Path, workflow: str | None = None) -> list[EvalCase]:
    evals_dir = Path(adapter_dir) / "evals"
    if not evals_dir.is_dir():
        return []
    cases = []
    for path in sorted(evals_dir.rglob("*.yaml")):
        case = EvalCase.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if workflow is None or case.workflow == workflow:
            cases.append(case)
    return cases


# -- running -------------------------------------------------------------------


def run_case(
    case: EvalCase,
    adapter_dir: str | Path,
    backend: str = "stub",
    ollama_model: str | None = None,
) -> CaseResult:
    """Run one golden case in an isolated throwaway environment."""
    outcomes: list[CheckOutcome] = []
    with tempfile.TemporaryDirectory(prefix=f"hive-eval-{case.name}-", ignore_cleanup_errors=True) as tmp:
        kwargs: dict[str, Any] = dict(
            adapter_dir=Path(adapter_dir), data_dir=Path(tmp), model_backend=backend
        )
        if ollama_model:
            kwargs["ollama_model"] = ollama_model
        rt = Runtime(HiveConfig(**kwargs))
        try:
            job = rt.trigger(Event(type=case.event.type, metadata=dict(case.event.metadata)))
            if job is None:
                return CaseResult(
                    name=case.name, workflow=case.workflow, backend=backend, passed=False,
                    outcomes=[CheckOutcome(check="trigger", ok=False,
                                           detail=f"no workflow handles '{case.event.type}'")],
                    ts=_now(),
                )

            checks = case.checks
            if checks.final_state is not None:
                outcomes.append(CheckOutcome(
                    check=f"final_state == {checks.final_state}",
                    ok=job.state.value == checks.final_state,
                    detail=f"got {job.state.value}",
                ))
            produced = {a["artifact_type"] for a in rt.store.list_artifacts(job.id)}
            for required in checks.artifacts:
                outcomes.append(CheckOutcome(
                    check=f"produces {required}",
                    ok=required in produced,
                    detail=f"produced: {sorted(produced)}",
                ))
            if checks.pending_card is not None:
                has_card = any(
                    c.job_id == job.id
                    for c in rt.store.list_cards(ApprovalStatus.PENDING.value)
                )
                outcomes.append(CheckOutcome(
                    check=f"pending approval card == {checks.pending_card}",
                    ok=has_card is checks.pending_card,
                    detail=f"got {has_card}",
                ))
            if checks.max_usd is not None:
                outcomes.append(CheckOutcome(
                    check=f"spend <= ${checks.max_usd}",
                    ok=job.spend_usd <= checks.max_usd,
                    detail=f"spent ${job.spend_usd:.4f}",
                ))

            content_skipped = False
            if case.content:
                if backend == "stub":
                    content_skipped = True  # canned text can't be judged — skip, don't fake
                else:
                    artifacts = rt.store.list_artifacts(job.id)
                    for check in case.content:
                        outcomes.extend(_content_outcomes(check, artifacts))

            return CaseResult(
                name=case.name, workflow=case.workflow, backend=backend,
                passed=all(o.ok for o in outcomes),
                outcomes=outcomes, content_skipped=content_skipped,
                job_state=job.state.value, spend_usd=job.spend_usd,
                spend_tokens=job.spend_tokens, ts=_now(),
            )
        finally:
            rt.store.close()  # release the sqlite file so the tmp dir can delete


def _content_outcomes(check: ContentCheck, artifacts: list[dict]) -> list[CheckOutcome]:
    target = next((a for a in artifacts if a["artifact_type"] == check.artifact), None)
    if target is None or check.field not in target:
        return [CheckOutcome(check=f"{check.artifact}.{check.field} exists", ok=False,
                             detail="artifact or field missing")]
    text = str(target[check.field])
    lowered = text.lower()
    outcomes = []
    for needle in check.must_contain:
        outcomes.append(CheckOutcome(
            check=f"{check.artifact}.{check.field} contains '{needle}'",
            ok=needle.lower() in lowered,
        ))
    for needle in check.must_not_contain:
        outcomes.append(CheckOutcome(
            check=f"{check.artifact}.{check.field} avoids '{needle}'",
            ok=needle.lower() not in lowered,
        ))
    if check.max_words is not None:
        words = len(text.split())
        outcomes.append(CheckOutcome(
            check=f"{check.artifact}.{check.field} <= {check.max_words} words",
            ok=words <= check.max_words,
            detail=f"{words} words",
        ))
    return outcomes


def run_suite(
    cases: list[EvalCase],
    adapter_dir: str | Path,
    backend: str = "stub",
    history_dir: str | Path | None = None,
    ollama_model: str | None = None,
) -> SuiteResult:
    suite = SuiteResult(adapter=Path(adapter_dir).name, backend=backend, ts=_now())
    for case in cases:
        suite.cases.append(run_case(case, adapter_dir, backend, ollama_model))
    if history_dir is not None:
        _append_history(suite, Path(history_dir))
    return suite


def _append_history(suite: SuiteResult, history_dir: Path) -> None:
    """One JSONL line per case per run — the 'scores tracked over time' file."""
    history_dir.mkdir(parents=True, exist_ok=True)
    with (history_dir / "history.jsonl").open("a", encoding="utf-8") as f:
        for case in suite.cases:
            f.write(json.dumps({
                "ts": suite.ts, "adapter": suite.adapter, "backend": suite.backend,
                "workflow": case.workflow, "case": case.name, "passed": case.passed,
                "checks_ok": sum(1 for o in case.outcomes if o.ok),
                "checks_total": len(case.outcomes),
                "content_skipped": case.content_skipped,
                "job_state": case.job_state,
                "spend_usd": case.spend_usd, "spend_tokens": case.spend_tokens,
            }) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
