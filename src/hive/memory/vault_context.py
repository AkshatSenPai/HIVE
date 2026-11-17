"""What an agent gets from the vault (build plan, Phase 1: "Real vault").

HIVE reads the vault, not the model: a workflow step lists the notes it needs
(`reads:` in the SOP), and HIVE puts the map plus those notes into the agent's
prompt. That works the same on every backend (stub, Ollama, Claude plan, API)
and cannot reach outside the vault root.

Notes are fenced as untrusted: `raw/` and `wiki/` will hold scraped material
such as reel transcripts. The map is the owner's own guidance, so it isn't.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from hive.fencing import fence
from hive.jobs.models import Job
from hive.memory.semantic import MAP_FILE, Vault, VaultPathError

MAX_TOTAL_CHARS = 30_000  # all notes together (the map comes on top)
MAX_NOTE_CHARS = 8_000    # one note


class VaultContext(BaseModel):
    text: str
    included: list[tuple[str, int]] = Field(default_factory=list)  # (path, characters used)
    skipped: list[str] = Field(default_factory=list)  # over budget, or outside the vault


def build_vault_context(
    vault: Vault,
    reads: list[str],
    max_total: int = MAX_TOTAL_CHARS,
    max_note: int = MAX_NOTE_CHARS,
) -> VaultContext:
    """The map, then each listed note (newest first within each read),
    de-duplicated, each cut at `max_note`; notes that would pass `max_total`
    are skipped and listed, never silently dropped."""
    parts: list[str] = []
    if vault.exists(MAP_FILE):
        parts.append(f"Vault map ({MAP_FILE}):\n{vault.read(MAP_FILE).strip()}")

    included: list[tuple[str, int]] = []
    skipped: list[str] = []
    empty: list[str] = []
    seen: set[str] = set()
    blocks: list[str] = []
    used = 0
    for read in reads:
        try:
            paths = [p for p in vault.notes(read) if p != MAP_FILE]
        except VaultPathError:
            skipped.append(read)
            continue
        if not paths:
            empty.append(read)
        for path in paths:
            if path in seen:
                continue
            seen.add(path)
            content = vault.read(path)
            if len(content) > max_note:
                content = content[:max_note] + f"\n[cut at {max_note:,} characters]"
                chars = max_note
            else:
                chars = len(content)
            if used + chars > max_total:
                skipped.append(path)
                continue
            used += chars
            included.append((path, chars))
            blocks.append(fence(content, source=f"vault:{path}"))

    if blocks:
        parts.append("Vault notes (newest first):\n\n" + "\n\n".join(blocks))
    parts.extend(f"(nothing in {read} yet)" for read in empty)
    return VaultContext(text="\n\n".join(parts), included=included, skipped=skipped)


def output_note(job: Job, body: str, decision: str, decided_at: datetime) -> tuple[str, str]:
    """Where an approved job is filed, and the note to file:
    `outputs/<workflow>/<YYYY-MM-DD>-<job_id>.md` with front matter."""
    path = f"outputs/{job.workflow}/{decided_at:%Y-%m-%d}-{job.id}.md"
    subject = str(job.context.get("subject", "")).replace("\n", " ").replace('"', "'")
    front = (
        "---\n"
        f"job: {job.id}\n"
        f"workflow: {job.workflow}\n"
        f'subject: "{subject}"\n'
        f"decision: {decision}\n"
        f"cost_usd: {job.spend_usd:.4f}\n"
        f"decided_at: {decided_at.isoformat(timespec='seconds')}\n"
        "---\n\n"
    )
    return path, front + body.strip() + "\n"
