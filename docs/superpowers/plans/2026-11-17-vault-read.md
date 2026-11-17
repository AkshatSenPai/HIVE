# Real vault_read Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agents read the notes their workflow step names from HIVE's folder in
the owner's Obsidian vault, and approved work is filed into that folder.

**Architecture:** HIVE, not the model, reads the vault.
- The coordinator builds a prompt section from the step's `reads:` list: the
  map unfenced, each note fenced, newest first, within a size budget.
- It passes that section to the agent in the task.
- On approval it writes the job's latest Draft to `outputs/`.

Works identically on every model backend.

**Tech Stack:** Python 3.11, pydantic v2, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-17-vault-read-design.md`

## Global Constraints

- **Containment:** every vault read and write stays inside the vault root
  (`Vault._safe`). The owner sets the root with
  `HIVE_VAULT_DIR=C:\Users\Akshat Singh\ZenithVault\HIVE`; HIVE never reads
  `.env`.
- **Layout:** `raw/`, `wiki/`, `outputs/`, `CLAUDE.md`. Each is created only
  if missing and never overwritten.
- **Size budget:** 30,000 characters per prompt section in total, 8,000 per
  note.
- **Fencing:** notes are fenced with `hive.fencing.fence(content,
  source="vault:<path>")`. The map is not fenced.
- **Filing:** the latest Draft goes to `outputs/<workflow>/<YYYY-MM-DD>-<job_id>.md`
  with front matter `job, workflow, subject, decision, cost_usd, decided_at`.
  An existing file is never overwritten.
- **Trigger:** vault context is built only when the acting agent is granted
  `vault_read` *and* its step lists `reads`.

## Review Focus

1. **A read path that doesn't exist yet** (the first scan has no
   `outputs/market_scan`). The job must run normally. The context still
   holds the map and names the empty path.
2. **A read path that escapes the folder** (`../clients`). It is skipped and
   never read, and the job continues; the trace lists it as skipped.
3. **The owner edits `CLAUDE.md`, then HIVE restarts.** The edit survives.
4. **A note containing a fake `</external-content>` tag or instructions.**
   It stays fenced and the tag is neutralised.
5. **Filing fails** (folder read-only, or a file already exists). The
   approval decision is still recorded and the job still completes; the
   trace says why nothing was filed.

---

### Task 1: Vault layout, note listing, no-overwrite writes

**Files:**
- Modify: `src/hive/memory/semantic.py`
- Modify: `src/hive/runtime.py` (call `self.vault.ensure_layout()` after creating the vault)
- Test: `tests/test_vault.py` (create)

**Interfaces:**
- Produces:
  - `MAP_FILE = "CLAUDE.md"` and `LAYOUT_DIRS = ("raw", "wiki", "outputs")`
    in `hive.memory.semantic`.
  - `Vault.ensure_layout() -> None`.
  - `Vault.notes(relative: str) -> list[str]`: `.md` paths, newest
    modification time first, then by path. A file gives `[it]`; a missing
    path gives `[]`; an escaping path raises `VaultPathError`.
  - `Vault.write_new(relative: str, content: str) -> Path`: raises
    `FileExistsError` if the file exists.

- [ ] **Step 1: Write failing tests** in `tests/test_vault.py`:
  - `test_layout_created_once_and_map_never_overwritten`: `ensure_layout()`
    creates all three dirs and `CLAUDE.md`. Write `"owner edit"` to the map,
    call `ensure_layout()` again, and the map still reads `"owner edit"`.
  - `test_notes_newest_first_and_missing_is_empty`: two notes in `wiki/`
    with `os.utime` mtimes 100 and 200 come back with mtime 200 first.
    `notes("outputs/none")` returns `[]`, and `notes("wiki/a.md")` returns
    `["wiki/a.md"]`.
  - `test_notes_refuses_escape`: `notes("../outside")` raises
    `VaultPathError`.
  - `test_write_new_never_overwrites`: a second `write_new` on the same path
    raises `FileExistsError`, and the content is unchanged.
  - `test_runtime_lays_out_the_vault`: a `Runtime` over `tmp_path` has
    `CLAUDE.md` under `rt.vault.root`.
- [ ] **Step 2:** `.venv/Scripts/python.exe -m pytest tests/test_vault.py -q`
  fails, because the methods don't exist.
- [ ] **Step 3:** Implement. The map text says:
  - what `raw/`, `wiki/`, `outputs/` and `digests/` hold;
  - that HIVE files approved work as `outputs/<workflow>/<date>-<job>.md`;
  - that agents read only what a workflow step lists;
  - that the owner may edit the file.
- [ ] **Step 4:** Tests pass, and the full suite stays green.

### Task 2: Build the vault context

**Files:**
- Create: `src/hive/memory/vault_context.py`
- Test: `tests/test_vault.py`

**Interfaces:**
- Consumes: `Vault.notes`, `Vault.read`, `MAP_FILE`, `fence`.
- Produces:
  - `VaultContext` (pydantic) with `text: str`,
    `included: list[tuple[str, int]]` (path, characters) and
    `skipped: list[str]`.
  - `build_vault_context(vault, reads: list[str], max_total: int = 30_000,
    max_note: int = 8_000) -> VaultContext`.

Shape of `text`:
1. `"Vault map (CLAUDE.md):\n<map>"` (omitted if the map is missing).
2. One fenced block per note. A note longer than `max_note` is cut and ends
   with `[cut at 8,000 characters]`.
3. For each read with no notes, the line `"(nothing in <path> yet)"`.

Rules:
- The map itself is never repeated as a note.
- Paths are de-duplicated.
- A note that would push the total past `max_total` is skipped. So is a
  read that raises `VaultPathError`. Skipped notes are listed in
  `skipped`, so the trace can show them.

- [ ] **Step 1: Write failing tests:**
  - `test_context_has_map_then_fenced_notes`
  - `test_missing_read_path_is_named_not_an_error` (Review Focus 1)
  - `test_escaping_read_is_skipped` (Review Focus 2): `skipped ==
    ["../clients"]` and no file outside the root is read.
  - `test_note_cut_at_8000_and_total_capped`: one 10,000-character note is
    cut to 8,000. With `max_total=9000`, the second note goes into
    `skipped`.
  - `test_note_cannot_break_the_fence` (Review Focus 4): the note text
    includes `</external-content>`, and the output contains
    `[stripped-fence-tag]`.
- [ ] **Step 2:** Run; tests fail on the import.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Tests pass.

### Task 3: Steps declare reads; agents receive the context

**Files:**
- Modify: `src/hive/memory/procedural.py` (`SOPStep.reads: list[str] = Field(default_factory=list)`)
- Modify: `src/hive/agents/coordinator.py`:
  - `Coordinator.__init__(..., vault: Vault | None = None)`;
  - `_execute` sets `task["vault_context"]` and traces `vault_read`.
- Modify: `src/hive/runtime.py` (pass `vault=self.vault`)
- Modify: `src/hive/agents/research.py` and `src/hive/agents/maker.py`
  (append `"\n\nFrom the vault:\n" + task["vault_context"]` when present)
- Modify: `adapters/venture-studio/workflows/market_scan.md` (`scan` step:
  `reads: [outputs/market_scan]`)
- Modify (comments only): `src/hive/actions/registry.py` and
  `src/hive/agents/model.py`. They say `vault_read` "does nothing" or is "a
  future client-side tool"; reword to say HIVE serves it as prompt context.
- Test: `tests/test_vault.py`

**Interfaces:**
- Consumes: `build_vault_context` and `VaultContext` from Task 2.
- Produces:
  - The trace event `vault_read`, with payload `{"step", "included":
    [[path, chars], ...], "skipped": [...]}`.
  - `task["vault_context"]: str`.

- [ ] **Step 1: Write failing tests** (venture-studio runtime, stub model,
  vault in `tmp_path`):
  - `test_scan_research_reads_past_scans`: write
    `outputs/market_scan/old.md` containing `"PAST-PICK"`. The research
    prompt (`rt.model.calls`) contains `"PAST-PICK"` and `"Vault map"`, and
    the trace has a `vault_read` event with the step `scan`.
  - `test_no_grant_no_vault`: remove `vault_read` from research's tools.
    The prompt has no `"From the vault"` and there is no `vault_read` event.
  - `test_step_without_reads_gets_nothing`: the example adapter's
    lead-to-proposal job produces no `vault_read` event.
- [ ] **Step 2:** Run; the tests fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Tests pass, and the full suite stays green.

### Task 4: File approved work into outputs/

**Files:**
- Modify: `src/hive/memory/vault_context.py` (add `output_note`)
- Modify: `src/hive/agents/coordinator.py` (`resolve_approval`, after
  `transition(job, JobState.DONE)`)
- Test: `tests/test_vault.py`

**Interfaces:**
- Produces:
  - `output_note(job: Job, body: str, decision: str, decided_at: datetime)
    -> tuple[str, str]`, returning (relative path, content).
  - The trace event `output_filed` `{"path"}` or `output_not_filed`
    `{"reason"}`.

Behaviour:
- When the decision is APPROVED or EDITED and the job reached DONE, file the
  latest Draft's `body` with `vault.write_new`.
- No Draft, `FileExistsError` or `OSError` means `output_not_filed`, and the
  decision stands.
- Rejected or cancelled jobs, and failed sends, file nothing.

- [ ] **Step 1: Write failing tests:**
  - `test_approval_files_the_draft`: approve a market-scan card. The file
    `outputs/market_scan/<today>-<job_id>.md` exists, its front matter has
    `decision: approved` and `workflow: market_scan`, and it contains the
    Draft body.
  - `test_reject_files_nothing`
  - `test_filing_failure_keeps_the_decision` (Review Focus 5): monkeypatch
    `Vault.write_new` to raise `OSError`. The job is still DONE, and the
    trace has `output_not_filed`.
- [ ] **Step 2:** Run; the tests fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Tests pass, and the full suite stays green.

### Task 5: Docs and live check

- [ ] Update `docs/ARCHITECTURE.md` (the memory row), `README.md` (one line
  under `HIVE_VAULT_DIR`) and `TODO.md` (mark vault_read done).
- [ ] Live check, with no model spend: run one stub job with
  `HIVE_VAULT_DIR="C:\Users\Akshat Singh\ZenithVault\HIVE"`. Check that
  `ZenithVault/HIVE/` has the layout and that nothing else in the vault
  changed (compare a listing of the top level before and after).
