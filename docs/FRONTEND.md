# HIVE Frontend — Stack & Design Brief

The dashboard is a **single self-contained `web/index.html`**, served same-origin
by the API (`hive serve` → `/`). This document is the contract: the locked
stack, the hard rules that make it drop-in, the views it needs, and the exact
API shapes it renders. The last section is a **paste-ready prompt** for the
Claude Design app.

---

## 1. The stack (locked)

| Layer | Choice | Why |
|---|---|---|
| File | one `index.html`, everything inline or CDN | it's served as a static file; **no build step, no npm, no bundler** |
| Styling | **Tailwind CSS via Play CDN** (`<script src="https://cdn.tailwindcss.com"></script>`) | zero build, what the design app outputs natively |
| Reactivity | **Alpine.js via CDN** (`<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js"></script>`) | declarative state (`x-data`/`x-for`/`@click`) with no build; right size for a dashboard. Plain vanilla JS is an acceptable fallback |
| Data | native `fetch`, **relative paths** | same-origin → no CORS, no absolute URLs |
| Routing | none — in-page view switching via Alpine state | single page; a hash router is optional, not needed |
| Fonts/icons | Google Fonts / an icon CDN, optional | cosmetic only |

**Not React-via-CDN:** it needs either in-browser Babel (slow) or precompiled
JSX (a build step) — both fight the single-file rule. Alpine gives clean
reactivity with neither.

**Offline note:** the CDNs above need internet at page load. That's fine for a
local tool with a connection. If you ever need fully-offline, vendor Tailwind +
Alpine as files into `web/` and reference them relatively — the server already
serves anything in that folder.

---

## 2. Hard rules (non-negotiable — these make it drop-in)

1. **One file.** All CSS and JS inline or via CDN `<script>`. No imports of
   local `.js`/`.css` you'd have to build.
2. **Relative API paths only.** `fetch('/jobs')`, never
   `fetch('http://localhost:8000/jobs')`. The UI and API share an origin.
3. **No invented endpoints.** Use only the ones in §4. If a view seems to need
   data no endpoint provides, note it — the backend adds the endpoint, the UI
   doesn't fake it.
4. **Read the enums.** Job states, card statuses, and action kinds are fixed
   sets (§4). Render every value; don't assume only the happy path.
5. **Poll for freshness.** No websockets yet — poll `/jobs` and `/approvals`
   every ~4s so the board stays live. (Websockets are a future backend add.)

---

## 3. Views the dashboard needs

Mapped to endpoints (§4). One page, switchable sections.

| View | Shows | Endpoints |
|---|---|---|
| **Ask bar** (always visible) | a text box + send button → opens an owner-initiated job | `POST /jobs` |
| **Jobs board** | every job as a card/row: state, workflow, spend ($ + tokens), owner-touches; filter by state | `GET /jobs`, `GET /adapter` |
| **Job detail** | one job + its produced artifacts (brief/draft/shortlist bodies), spend, trace link | `GET /jobs/{id}`, `GET /jobs/{id}/trace` |
| **Approvals queue** | pending cards: title, action kind, cost so far, reasoning, downstream effects, artifact preview; Approve / Edit / Reject buttons | `GET /approvals`, `POST /approvals/{id}/decision` |
| **Digest** | the owner summary text block | `GET /digest` |
| **Header controls** | adapter name, global **kill-switch** toggle (this is a big red pause) | `GET /health`, `GET`/`POST /killswitch` |

The **approvals queue is the emotional center of the app** — it's where the
owner spends judgment. Make cards scannable and the three actions obvious.

---

## 4. API contract (exact shapes)

Base URL is the page's own origin. All bodies are JSON. Full live docs at
`/docs`.

### Enums (render all values)

- **Job `state`:** `queued` · `planning` · `executing` · `reviewing` ·
  `awaiting_approval` · `escalated` · `done` · `failed` · `cancelled`
- **Card `status`:** `pending` · `approved` · `edited` · `rejected`
- **`action_kind`:** `internal` · `send` · `spend` · `publish` · `contract` ·
  `live_settings` (the last five are the ones that gate)

### Endpoints

```
GET /health
 → { "status": "ok", "adapter": "venture-studio", "kill_switch": false }

GET /adapter
 → { "name": "...",
     "workflows": [ { "name","version","trigger","description",
                      "steps":[ {"id","agent","action_kind","checkpoint"} ] } ],
     "metrics": { ... } }

POST /jobs
 body: { "subject": "Search the market for viable apps",   // required
         "type": "owner.request",   // optional; default owner.request
         "raw_context": "", "source": "api", "metadata": {} }
 → { "job": <Job>, "pending_cards": [ <Card> ] }
 (422 if no workflow handles the trigger type)

GET /jobs            → { "jobs": [ <Job>, ... ] }   // ?state= to filter
GET /jobs/{id}       → { "job": <Job>, "artifacts": [ <Artifact>, ... ] }  // 404 if missing
GET /jobs/{id}/trace → { "job_id": "...", "trace": [ <TraceEntry>, ... ] }

GET  /approvals               → { "cards": [ <Card>, ... ] }  // default status=pending
GET  /approvals/{id}          → { "card": <Card> }            // 404 if missing
POST /approvals/{id}/decision
 body: { "decision": "approve" | "edit" | "reject", "note": "" }
 → { "job": <Job>, "card": <Card> }                            // 404 if card missing

GET /digest       → { "text": "=== HIVE DAILY DIGEST ===\n..." }
GET /killswitch   → { "engaged": false }
POST /killswitch  body: { "engaged": true } → { "engaged": true }
```

### Object shapes

```
Job {
  id, workflow, adapter, state,           // state ∈ the enum above
  trigger_event_id, created_at, updated_at,
  context: {},                            // trigger payload (e.g. {subject, source})
  artifact_ids: [],
  spend_tokens: int, spend_usd: float,
  owner_touches: int,                     // headline metric — surface it
  error: ""                               // set when escalated/failed
}

Card {                                    // ApprovalCard
  id, job_id, step_key, action_kind,      // action_kind ∈ the enum
  title, artifact_id,
  artifact_preview: "...",                // the exact thing that would go out — show this
  reasoning: "...",                       // why the agent thinks it's right
  cost_so_far_usd: float,
  downstream_effects: [ "..." ],
  status,                                 // status ∈ the enum
  created_at, decided_at, owner_note
}

Artifact {                               // each has "artifact_type" + its own fields
  artifact_type: "Brief"|"Draft"|"Plan"|"Deliverable"|"Escalation",
  id, created_at, produced_by, job_id,
  // Brief:       subject, summary, findings[], sources[], confidence(0..1)
  // Draft:       kind, title, body, inputs_used[]
  // Plan:        goal, workflow, steps[], notes
  // Deliverable: title, parts[], summary
  // Escalation:  reason, question, context
}

TraceEntry { ts, job_id, type, agent, tokens, usd, payload }
```

---

## 5. Paste-ready prompt for the Claude Design app

> **Superseded:** use **`docs/DESIGN_PROMPT.md`** — the finalized, fully
> self-contained prompt (visual direction locked 2026-07-06: cosmic cockpit,
> particle-cloud Coordinator Orb with slow hue drift, agent nodes from real
> adapter data, approvals queue + kill switch added). It embeds the full API
> contract, so nothing needs to be pasted in from §4.
>
> The version below is kept as the earlier, visually-neutral draft.

> Copy everything below into the Claude Design app. Adjust the visual direction
> to taste — the technical rules and the API contract must stay exact.

---

Build a single self-contained `index.html` dashboard for **HIVE**, an operating
system for running a business with AI agents. It is the owner's control panel.

**Hard technical rules (do not deviate):**
- Output **one HTML file**, everything inline. **No build step, no npm, no
  imports of local files.**
- Style with **Tailwind via Play CDN**. Use **Alpine.js via CDN** for state and
  interactivity. Native `fetch` for data.
- Call the backend API with **relative paths only** (e.g. `fetch('/jobs')`) —
  the file is served same-origin. Never use absolute/localhost URLs.
- Use only the endpoints listed below. Do not invent endpoints or fake data.
- **Poll** `/jobs` and `/approvals` every 4 seconds to stay live.

**The app has these views on one page (switchable, no page reloads):**
1. An always-visible **ask bar** — a text input + "Send to HIVE" button that
   `POST /jobs` with `{ "subject": <text> }` and shows the new job.
2. A **jobs board** — all jobs from `GET /jobs`, each showing state (as a
   colored badge), workflow, spend ($ and tokens), and owner-touches; filter by
   state.
3. A **job detail** panel — `GET /jobs/{id}`, rendering the job plus its
   produced artifacts (show Draft/Deliverable `body`/`summary`, Brief
   `summary`+`findings`, Plan `steps`), with a link to the trace.
4. An **approvals queue** — the heart of the app. `GET /approvals` (pending),
   each card showing title, `action_kind`, `cost_so_far_usd`, `reasoning`,
   `downstream_effects`, and `artifact_preview`, with three clear actions —
   **Approve / Edit / Reject** — that `POST /approvals/{id}/decision` with
   `{ "decision": ..., "note": ... }`.
5. A **digest** panel — `GET /digest`, render the `text`.
6. A **header** — show adapter name from `GET /health`, and a prominent
   **kill-switch** toggle wired to `GET`/`POST /killswitch` (engaging it is the
   global pause; make it feel serious/red).

**Data shapes** (fields you'll render): [paste §4 "Object shapes" and "Enums"
here]. Render every enum value, not just the happy path — a job can be
`escalated` or `failed`, a card can be `rejected`.

**Tone:** a calm, trustworthy operations cockpit — the owner supervises agents
here and approves consequential actions, so clarity and confidence matter more
than flash. Dense but scannable. [Add your own visual direction: palette,
typography, mood.]

---

## 6. Installing the result

1. Export the HTML from the design app.
2. Save it as `web/index.html` (replace the placeholder).
3. `hive serve` → open `http://127.0.0.1:8000/`.

If a screen needs data no endpoint provides, tell me — I add the endpoint, the
UI stays honest.
