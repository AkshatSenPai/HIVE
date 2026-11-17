# Real `vault_read`: HIVE inside the Obsidian vault

Short design · 2026-11-17 · build plan Phase 1, item 4 ("Real vault")

## Intent

Make HIVE's memory something agents actually use. Statura's post-pack
research will read the reel library (`wiki/`) and recent posts (`outputs/`),
and approved packs will be filed back into `outputs/`, where the owner films
from them. The owner chose their existing Obsidian vault
(`C:\Users\Akshat Singh\ZenithVault`, also used by Zenith), with HIVE kept
to its own subfolder.

## Goals, non-goals, anti-goals

- **Goals**
  - A `HIVE/` folder in the vault with `raw/`, `wiki/`, `outputs/` and a
    `CLAUDE.md` map.
  - `vault_read` works on all four backends (stub, Ollama, Claude plan, API).
  - Approved work is filed into `outputs/`.
- **Non-goals**
  - A model browsing the vault by itself.
  - Search or embeddings.
  - The reel library (item 8) and the Statura adapter (item 5); they plug
    in later by naming their reads.
- **Anti-goals**
  - HIVE reading or writing anything outside `ZenithVault/HIVE/`.
  - Text in the vault steering an agent.
  - HIVE overwriting the owner's edits.

## Approaches

| Approach | What it trades off |
|---|---|
| **1. HIVE reads, the prompt carries it** *(picked)* | Works on every backend and costs nothing extra. It also cannot leave the folder. The cost: an agent can't fetch a note its step didn't name. |
| 2. Model browses with a `vault_read` tool | Needs a client-side tool loop that only the paid API supports; it would have to be built three ways. It adds more calls and more risk. |
| 3. Claude Code's own Read/Grep, scoped to the folder | Works only on the Claude plan, and the folder limit would rest entirely on the CLI's permission rules. |

Approach 1 was picked because it is the only one that works on every backend
today. It is also the one where HIVE, not the model, decides what gets read.

## Design

- **Location.** `HIVE_VAULT_DIR=C:\Users\Akshat Singh\ZenithVault\HIVE`.
  - That folder becomes the vault root, and the existing path checks keep
    every read and write inside it.
  - The owner adds this line to `.env` (HIVE never reads `.env`).
- **Layout.** At startup HIVE creates `raw/`, `wiki/`, `outputs/` and
  `CLAUDE.md` if they are missing. It never overwrites them.
  - The map says what each folder is for and how HIVE names files.
  - It also says that agents read only what a workflow step lists.
  - Daily digests keep going to `digests/`.
- **Declaring reads.** An SOP step gets an optional `reads:` list of files or
  folders inside the HIVE folder.
- **Reading.** Reading happens when the acting agent is granted `vault_read`
  *and* its step lists reads.
  - HIVE adds the map to the prompt, then the listed notes, newest first.
  - Size limits: 30,000 characters in total and 8,000 per note (builder's
    call; about 7.5k tokens, roughly 1.5 cents on Sonnet). Whatever doesn't
    fit is left out, and the trace says so.
  - Notes are fenced as untrusted, because `wiki/` will hold scraped reel
    transcripts. The map is not fenced: it is the owner's own guidance.
- **Trace.** Each read is recorded as a `vault_read` event with the paths and
  character counts included, plus anything skipped.
- **Filing.** When the owner approves (or edits) a card, HIVE writes the
  job's final draft to `outputs/<workflow>/<date>-<job_id>.md`.
  - The file gets front matter: job, workflow, subject, decision and cost.
  - HIVE never overwrites an existing file.
- **First user.** `market_scan`'s `scan` step reads `outputs/market_scan`,
  so a new scan sees past picks and doesn't repeat them. Statura's
  `post_pack` will declare its own reads when that adapter is built.

## Left to the builder

The map's exact wording, tie-breaking when notes share a timestamp, and the
tests.
