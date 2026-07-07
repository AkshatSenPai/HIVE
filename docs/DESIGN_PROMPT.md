# HIVE Dashboard — Claude Design Prompts (multi-page edition)

The Claude Design app exports **one HTML file per page**, so the dashboard is
a classic multi-page site: eight self-contained HTML files in `web/`, linked
by a shared sidebar. The backend serves the whole folder — no build step, no
React, no glue.

> **How to use this file:** for EACH page you generate, paste **Part A
> (shared conventions)** plus **that page's brief from Part B** into the
> Claude Design app as one prompt. Save each export into `web/` under the
> exact filename given. `hive serve` → everything is live.
>
> Suggested order: `index.html` first (it sets the visual language), then
> `approvals.html`, `tasks.html`, and the rest. Reuse the first page's look
> for consistency — if the app supports referencing a previous design, do that.

| Page | File |
|---|---|
| Home (orb + command bar) | `web/index.html` |
| Tasks | `web/tasks.html` |
| Approvals | `web/approvals.html` |
| Agents | `web/agents.html` |
| Workflows | `web/workflows.html` |
| Memory | `web/memory.html` |
| Digest | `web/digest.html` |
| Settings | `web/settings.html` |

---

# PART A — Shared conventions (paste with EVERY page)

You are building one page of **HIVE**, an operating system for running a
business with AI agents. A coordinator agent plans, specialist agents execute,
and the owner (me) governs from this dashboard: gives commands, watches the
system work, and approves or rejects consequential actions. This is a real
control panel wired to a live local API — every number rendered must come from
the API, never invented.

## Hard technical rules (do not deviate)

- Output **one self-contained HTML file** for this page: all CSS/JS inline.
  No build step, no npm, no imports of other local files. (Other pages exist
  as separate files — link to them, don't import them.)
- Style with **Tailwind CSS via Play CDN**; use **Alpine.js via CDN** for
  state; native `fetch` for data. Google Fonts allowed (one clean sans, e.g.
  Inter or Space Grotesk — use the same on every page).
- Call the API with **relative paths only** (`fetch('/jobs')`). Never
  absolute/localhost URLs.
- **Use only the endpoints given in this prompt.** No invented endpoints, no
  fictional data, no placeholder numbers. Empty states are welcome; fake data
  is not.
- Poll the data this page displays every **4 seconds**; on fetch failure show
  a small "reconnecting…" indicator, keep polling.
- Respect `prefers-reduced-motion` (disable ambient animation).

## Visual language — "mission control in deep space"

Dark cosmic operations cockpit, identical across all pages: near-black
blue/indigo background (~`#05070f`), glassy panel cards with subtle 1px
borders and soft inner glow, neon accents used sparingly and meaningfully,
generous spacing, dense but scannable. Calm and trustworthy — clarity beats
flash. State colors, consistent everywhere: green = done/ok, blue = active/
in-flight, amber = awaiting approval / gated, red = escalated/failed/kill,
gray = queued/idle/cancelled.

## Shared shell (identical on every page)

- **Left sidebar** (slim, dark): "HIVE" wordmark on top, then nav links —
  plain anchors between the pages:
  `Home → /` · `Tasks → /tasks.html` · `Approvals → /approvals.html` ·
  `Agents → /agents.html` · `Workflows → /workflows.html` ·
  `Memory → /memory.html` · `Digest → /digest.html` ·
  `Settings → /settings.html`.
  Highlight the current page. On the **Approvals** link, show a live
  pending-count badge (poll `GET /approvals`, count `cards`). Bottom of the
  sidebar: owner chip "Akshat — Owner".
- **Header**: page title on the left; on the right a **KILL SWITCH** — a
  clearly-labeled red toggle wired to `GET /killswitch` →
  `{"engaged": bool}` and `POST /killswitch` body `{"engaged": true|false}`.
  Engaging it must feel serious: confirm once, then shift the page's accents
  to red with a banner "ALL AGENT ACTIVITY PAUSED". Also fetch `GET /health`
  → `{"status","adapter","kill_switch"}` and show the adapter name as a small
  chip in the header.

## Shared enums (render every value, not just the happy path)

- Job `state`: `queued` `planning` `executing` `reviewing`
  `awaiting_approval` `escalated` `done` `failed` `cancelled`
- Approval card `status`: `pending` `approved` `edited` `rejected`
- `action_kind`: `internal` `send` `spend` `publish` `contract`
  `live_settings` (all but `internal` are consequential/gated — style them
  with the amber warning tone)

---

# PART B — Page briefs (paste ONE per generation, after Part A)

---

## B1. `web/index.html` — Home

The command center. Centerpiece is the **Coordinator Orb**; below it the
command bar; right column has live activity and system overview.

### The Coordinator Orb (get this exactly right)

- A **dense volumetric particle cloud** — hundreds of small glowing dots
  arranged as a 3D sphere (points distributed on/inside a sphere, projected,
  slowly rotating), with faint constellation lines between nearby particles.
  "A galaxy compressed into a ball."
- Explicitly **NOT** a ring, an outlined circle, a lens-flare hoop, or a flat
  gradient disk. Volume of particles, not an outline.
- `<canvas>` + `requestAnimationFrame`, 300–500 particles, pause when the tab
  is hidden. Slow rotation, gentle breathing pulse.
- **Slow hue drift:** the whole orb sits in ONE hue at a time and slowly melts
  to the next — hold ~15–20s, cross-fade ~5s: warm amber/orange → violet/
  purple → magenta → cyan → back. Ambient and mesmerizing; never strobing,
  never rainbow-at-once.
- **State-reactive:** while a command (`POST /jobs`) is in flight → brighter,
  faster shimmer + a dispatch beam pulse toward the agent nodes; kill switch
  engaged → deep red, nearly frozen.
- Centered on the orb: **COORDINATOR**, subtitle = adapter name from
  `GET /health`.

### Around the orb

Agent nodes (glowing circular icon chips) connected to the orb by faint
beams. Populate from `GET /adapter` → `agents[]`, skipping the `coordinator`
entry (that's the orb). Under each node: name, its `model`, and "working…"
while a job runs, else "idle". Lay out radially for any agent count.

### Below the orb

- **Command bar**: wide input, placeholder "Give a command to the
  coordinator…", send button. Submit → `POST /jobs` with body
  `{"subject": "<text>"}` → returns `{"job": Job, "pending_cards": [Card]}`.
  Disable the button while in flight (orb shows working state). On success:
  toast "Task dispatched → {job.id} ({job.state})"; if `pending_cards` is
  non-empty, the toast links to `/approvals.html`. A 422 response means no
  workflow matched — toast that gently.
- **Quick actions**: one chip per workflow from `GET /adapter` →
  `workflows[]` (show `name`; clicking pre-fills the command bar with the
  workflow's `description`).

### Right column (two glass panels)

- **Live Activity**: from `GET /jobs` → `{"jobs":[...]}` sorted by
  `updated_at` desc — one row per job: time, workflow, state badge. Plus one
  row per pending card from `GET /approvals` ("approval waiting: {title}").
  For the newest job also fetch `GET /jobs/{id}/trace` →
  `{"trace":[{ts,type,agent,tokens,usd}]}` and interleave its entries
  (e.g. "research — model_call") for agent-level texture.
- **System Overview**: computed from `/jobs` + `/approvals` — job counts by
  state (donut or stat cluster), total spend (sum `spend_usd`, `spend_tokens`),
  pending approvals count, total `owner_touches`. No fictional numbers.

Job object fields used here:
`{ id, workflow, state, created_at, updated_at, spend_tokens, spend_usd, owner_touches, error }`

---

## B2. `web/tasks.html` — Tasks

Everything HIVE is doing or has done.

- **Board/list of all jobs** from `GET /jobs` → `{"jobs":[Job]}`: columns/
  cards with state badge (distinct color per state), workflow, created time,
  spend ($ + tokens), `owner_touches`, and `error` text when present. Filter
  chips by state (client-side or `GET /jobs?state=...`). Default sort:
  newest first.
- **Job detail** (drawer or panel on click): `GET /jobs/{id}` →
  `{"job": Job, "artifacts":[Artifact]}`. Render artifacts as readable
  documents, discriminated on `artifact_type`:
  - `Brief`: `subject`, `summary`, `findings[]` list, `confidence` (0..1 — show as a small meter)
  - `Draft`: `title`, `body` (multiline text — give it room), `kind`
  - `Plan`: `goal`, `steps[]` (`{id, agent, action, checkpoint}` — mini pipeline, lock icon on checkpoints), `notes`
  - `Deliverable`: `title`, `summary`, `parts[]`
  - `Escalation`: `reason`, `question` — style as an alert needing the owner
- **Collapsible trace** in the detail: `GET /jobs/{id}/trace` →
  `{"trace":[{ts, type, agent, tokens, usd, payload}]}` as a monospace event
  log with per-row token/$ chips.

```
Job { id, workflow, adapter, state, created_at, updated_at,
      context:{subject,...}, artifact_ids[], spend_tokens, spend_usd,
      owner_touches, error }
```

---

## B3. `web/approvals.html` — Approvals (the heart of the app)

Where the owner exercises judgment. Make it the best page.

- Queue of pending cards: `GET /approvals` → `{"cards":[Card]}`.
- Each card is a rich panel:
  - `title`, `action_kind` as an amber warning badge, `cost_so_far_usd`
  - the agent's `reasoning`
  - `downstream_effects` as a bullet list
  - `artifact_preview` in a large readable document block — this is the exact
    content that would go out; give it room and typography
- Three actions per card: **Approve** (green), **Edit** (amber — reveals a
  note textarea, submits decision `edit` with the note), **Reject** (red —
  note optional). All → `POST /approvals/{id}/decision` with body
  `{"decision":"approve"|"edit"|"reject", "note":""}` → returns
  `{"job": Job, "card": Card}`. After deciding: refresh the queue, toast the
  resulting job state ("job_xxx → done"). 404 → the card was already decided;
  refresh.
- A secondary tab/filter to view decided cards (`GET /approvals?status=approved`
  etc.) with their `decided_at` and `owner_note`.
- Empty state: "Nothing waiting on you — the hive is working."

```
Card { id, job_id, step_key, action_kind, title, artifact_id,
       artifact_preview, reasoning, cost_so_far_usd,
       downstream_effects[], status, created_at, decided_at, owner_note }
```

---

## B4. `web/agents.html` — Agents

Who works here, and on what brain.

- Roster from `GET /adapter` → `agents[]`:
  `{ name, tier, model, tools[], steps[] }`.
- One card per agent: name, `tier` chip, **the model it runs on** (`model`,
  e.g. `claude-opus-4-8` / `claude-sonnet-5` — make the model prominent),
  its least-privilege `tools` list, and the workflow `steps` it owns
  (e.g. `lead_to_proposal.enrich`).
- Mark the `coordinator` entry visually as the orchestrator (crown/orb motif —
  it's the center of the system, it plans and reviews but never does
  specialist work).
- Read-only. No add/edit controls — the roster is code+config, not UI-editable.

---

## B5. `web/workflows.html` — Workflows

The playbook: versioned SOPs the agents execute.

- From `GET /adapter` → `workflows[]`:
  `{ name, version, trigger, description, steps[{id, agent, action_kind, checkpoint}] }`.
- One card per workflow: name + `v{version}`, trigger chip (e.g.
  `owner.request`), description, and the steps as a small left-to-right
  pipeline diagram — each step shows its `id` and owning `agent`; steps with
  `checkpoint: true` or a gated `action_kind` (anything ≠ `internal`) get a
  lock icon and amber tone (those block on owner approval).
- Read-only.

---

## B6. `web/memory.html` — Memory

The semantic vault: a folder of Obsidian-compatible markdown the agents (and
owner) share.

- Two-pane browser:
  - Left: file tree from `GET /vault` → `{"root": "<abs path>", "files":
    ["clients/acme.md", ...]}` — paths are flat with `/` separators; group
    into folders.
  - Right: selected file via `GET /vault/file?path=<path>` →
    `{"path","content"}` — render the markdown nicely (a tiny inline
    markdown-to-HTML converter for headings/bold/lists/links is fine).
- Read-only, with a persistent hint: "This vault is plain markdown — edit it
  in Obsidian at {root}" (show the real `root` from the API).
- Empty state: "Vault is empty — agents write here as they work."
- Errors: 404 file → inline message; 400 → "invalid path".

---

## B7. `web/digest.html` — Digest

The owner's summary view.

- `GET /digest` → `{"text": "=== HIVE DAILY DIGEST ===\n..."}` — render the
  text in a monospace-styled glass panel, preserving line breaks. Refresh
  button + the standard 4s poll.
- Around it, computed from `GET /jobs`: a small stat row (jobs done, in
  flight, escalated/failed, total spend) as context above the text block.

---

## B8. `web/settings.html` — Settings

Read-only system configuration. **No fake toggles** — nothing here is
UI-editable yet except the kill switch (which lives in the shared header).

- From `GET /system` →
  ```
  { "adapter": "...", "use_llm": false,
    "model_routing": { "planner":"claude-opus-4-8",
                       "specialist":"claude-sonnet-5",
                       "extractor":"claude-haiku-4-5",
                       "frontier":"claude-fable-5" },
    "policies": { "gated_actions":[...],
                  "budgets": {"per_job": {...}, "global_daily": {...}},
                  "autonomy": {...}, "escalation": {...} },
    "kill_switch": false }
  ```
- Panels:
  - **Mode**: `use_llm` as a badge — `true` → "LIVE MODELS", `false` →
    "OFFLINE STUB (no API cost)".
  - **Model routing**: the tier → model table (planner / specialist /
    extractor / frontier). Note under frontier: "big projects only — always
    owner-approved".
  - **Budgets**: per-job caps (steps / tokens / $) and global daily cap as
    stat cards.
  - **Governance**: `gated_actions` as amber chips; autonomy defaults;
    escalation triggers as a list.
  - **Kill switch** state mirrored here too.
