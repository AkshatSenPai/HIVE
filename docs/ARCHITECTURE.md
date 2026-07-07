# HIVE — Base Architecture & Design Decisions

*2026-07-06 · companion to HIVE PRD v0.2 · covers the P0 base only*

## What this base is

The P0 spine from PRD §12, runnable end-to-end today with a stub model:

```
Event ──▶ EventBus ──▶ Coordinator ──▶ Job (FSM, SQLite)
                          │  plan-as-artifact from the SOP
                          ├──▶ Research ─▶ Brief ┐
                          ├──▶ Maker ────▶ Draft ┼─▶ Deliverable
                          │                      │
                          └── PolicyGate ────────┴─▶ ApprovalCard ─▶ owner
                                                        (CLI for P0)
   every model call: KillSwitch → Budget → ModelClient(routing) → Trace
```

## Decisions taken (PRD §17 open questions)

| Question | Decision | Why |
|---|---|---|
| Same repo as Zenith? | **Clean split.** | Zenith is the parts bin, not a dependency; lifting patterns beats importing code. Revisit if a shared `zenith-core` lib emerges. |
| Claude Agent SDK vs hand-rolled coordinator? | **Neither hardcoded.** Agents talk to a `ModelClient` protocol; the hand-rolled loop is ~50 lines. The Agent SDK can be evaluated later by writing one more `ModelClient`/runtime impl without touching agents. |
| MCP-first how far? | Deferred to the action layer (P1/P2). `tools.yaml` reserves the mount point; only least-privilege tool *names* exist today. |
| Jobs DB | **SQLite**, isolated behind `JobStore`. Postgres swap stays local to one file. |
| Which business is adapter #1? | Two placeholders prove neutrality: `adapters/example` (inbound lead → proposal, the PRD's agency example) and `adapters/venture-studio` (owner-initiated market scan → shortlist → owner picks — the intended primary use). Real adapter = rewrite 5 files, no core changes. |
| Language/stack | Python 3.11+ / pydantic v2 / pyyaml. Two runtime deps; `anthropic` is an optional extra. |

## Module map (PRD § → code)

| PRD concept | Code | Status |
|---|---|---|
| Event bus (§4) | `events/bus.py` + `events/sources.py` | in-process pub/sub. Producers: manual (CLI/UI), **file inbox** (`<data_dir>/inbox/`, archive/quarantine, `hive watch` or auto with `hive serve`), **webhook** (`POST /events`, token-gated, off by default). Email bridge + schedule still pending |
| Coordinator (§4) | `agents/coordinator.py` | plan derived 1:1 from SOP + planner sanity-check; free planning waits for eval maturity. Reviews every specialist output vs `done_when` with bounded rework → escalate (see simplification 4) |
| Job queue/FSM (§4) | `jobs/` | explicit states, illegal transitions raise, SQLite persistence |
| Specialists (§5) | `agents/research.py`, `agents/maker.py` | stub-model ready; roster registered in the Coordinator |
| Typed artifacts (§9) | `artifacts.py` | Brief/Draft/Plan/Deliverable/Escalation — the only handoff currency |
| Approval cards (§3.1) | `governance/approvals.py` | artifact + reasoning + cost + effects; CLI render |
| Digest (§3.2) | `governance/digest.py` + `governance/delivery.py` | jobs/spend (incl. today-vs-cap)/approvals/stuck/**improvement proposals** (earned autonomy upgrades + `hive ratify`). Scheduled daily (`digest_time`, default 09:00 local) inside `hive serve`; sinks: vault archive (`digests/YYYY-MM-DD.md`, Obsidian-visible) always, Telegram when free BotFather creds are set |
| Escalations (§3.3) | `governance/escalation.py` + `Escalation` artifact | stop-and-ask path wired through budget blowouts |
| Autonomy dial (§3) | `policy/autonomy.py` | L0–L3, streak tracking, owner-ratified upgrades, rejection resets |
| Gates (§11.1) | `policy/gates.py` | send/spend/publish/contract/live_settings; **spend never opens** |
| Budgets + kill switch (§10, §11.7) | `policy/budgets.py` | steps/tokens/$ caps, pause-and-escalate, global switch |
| Injection fencing (§11.2) | `fencing.py` | wraps all trigger payloads before prompts; escape attempts neutralized |
| Memory fabric (§4) | `memory/` | semantic vault (path-safe), episodic queries over jobs DB, procedural SOP loader |
| Multi-model routing (§9) | `agents/model.py` | tiers: planner → Opus, specialist → Sonnet, extractor → Haiku, frontier → Fable (owner-gated); adaptive thinking on. Three backends: `stub` ($0, canned), `ollama` ($0, real local inference — dev/free tier; no server-side tools), `anthropic` (live, paid) |
| Action layer (§4) | `actions/registry.py` | server-side tools only for P0 — `web_search` (Anthropic-hosted, max_uses-capped); least privilege enforced at the agent chokepoint |
| Traces (§9) | `observability/trace.py` | JSONL per job; tokens + $ on every model call |
| Adapters (§6) | `adapter.py` + `adapters/example/` | profile/workflows/tools/policies/metrics |
| SOPs (§7) | `memory/procedural.py` | YAML frontmatter (machine contract) + markdown body (intern-readable instructions) |

## Deliberately NOT built (and why)

Per PRD phasing — these queue behind pilot reliability:

- **Eval harness (§9)** — next build after the base; nothing about the SOP
  ladder (simulation → shadow → gated) works without it.
- **Action layer** (MCP tools, browser agent, computer use, voice, payments) —
  P1/P2+. Today the only "action" is producing artifacts; the gated `send`
  step is where the first real tool lands.
- **Reflection loop** — P3, FRONTIER; ship late, evaluate hard.
- **Telegram/dashboard delivery** — approval cards render in the CLI; extra
  channels are delivery mechanisms, not architecture.
- **Sub-swarms, QA/Finance/Growth agents** — after the static roster is boring.

## HTTP API (added 2026-07-06)

`src/hive/api/app.py` — a thin FastAPI layer over `Runtime`/`JobStore`, the
surface the frontend consumes (the CLI and API are peer front-ends over the
same core; no business logic in either). One long-lived `Runtime` per server
process keeps the in-memory autonomy dial consistent across requests; the
SQLite store persists everything else. Endpoints mirror the CLI verbs (jobs,
approvals, digest, trace, killswitch) and are documented live at `/docs`
(OpenAPI). CORS is dev-open; `check_same_thread=False` on the store lets the
threaded server share one connection (fine for a single owner; revisit for
concurrency). Run with `hive serve`.

**Frontend delivery (decided 2026-07-06):** the UI is a static export from the
Claude Design app — a single self-contained `index.html`. It's served
**same-origin** by the API: `create_app` mounts `web/` (config `web_dir`) at
`/` via `StaticFiles(html=True)`, registered **after** all API routes so it
can never shadow an endpoint (test: `test_static_ui_served_without_shadowing_api`).
Consequences: one process (`hive serve`) serves both UI and JSON at one URL,
the frontend's JS uses relative paths (no CORS in practice — the open CORS
policy only matters if the UI is ever hosted on a different origin), and
swapping in a new design is dropping a file. A plain placeholder lives at
`web/index.html` and doubles as the fetch-pattern reference; it's overwritten
by the real export. The static mount is conditional — no `index.html`, no
mount, `/` 404s, API unaffected (test: `test_no_ui_when_absent`).

## Known P0 simplifications (intentional, tracked)

1. ~~Artifacts live in memory only.~~ **Fixed 2026-07-06:** artifacts now
   persist to an `artifacts` table (`store.save_artifact` / `list_artifacts`),
   so `GET /jobs/{id}` serves produced briefs/drafts/shortlists. The
   Coordinator still keeps an in-memory copy for same-run preview.
2. ~~Autonomy dial not persisted.~~ **Fixed 2026-07-06:** the dial round-trips
   through a `kv_state` table (loaded at Coordinator init, saved on every
   owner decision); `upgrade_threshold` now comes from policies.yaml.
3. **Event bus is synchronous/in-process** — partially addressed 2026-07-06:
   real producers now exist (file inbox watcher thread + webhook endpoint),
   serialized through a shared runtime RLock, so concurrent sources are safe.
   Jobs still run inline on the producing thread (a slow live-model job blocks
   that producer until done); a real queue/worker split comes when that hurts.
   Subject lines from external sources are sanitized but reach prompts
   un-fenced — bodies are the fenced channel; keep real content in bodies.
4. ~~Coordinator "review" is a placeholder.~~ **Fixed 2026-07-06:** the
   coordinator now reviews every specialist output against the SOP step's
   `done_when` immediately after production (review-before-consumption — a
   bad brief is caught before the maker uses it). Verdict protocol:
   `VERDICT: pass|revise` + actionable feedback; revise → the specialist
   reruns with the feedback injected; capped by `policies.review.max_reworks`
   (default 1); exhaustion → ESCALATED (never accept-by-exhaustion). Every
   attempt is persisted for audit. Unparseable verdicts fail OPEN but are
   traced as `unparsed` — review is a net, not a wall; revisit this choice
   when live-model verdict formatting proves reliable. Review can be disabled
   per adapter (`review.enabled: false`). Note: reviews add one planner-tier
   call per specialist step (~doubles per-job calls) — that's the PRD's
   intended design, and the cost shows up honestly in traces and evals.
5. ~~Global daily budget not enforced.~~ **Fixed 2026-07-06:** before a job
   runs, the day's spend (UTC, from the jobs DB) is checked against
   `budgets.global_daily.max_usd`; at/over the cap the job is refused at
   QUEUED → ESCALATED with zero model calls, and the owner is asked to raise
   the cap or wait. `/system` reports `spend_today_usd` vs the cap.
6. **`AnthropicModelClient` is minimal** — non-streaming, default effort;
   handles server-tool `pause_turn` continuations (guarded). Add streaming
   for long outputs and structured outputs (`output_config.format`) when the
   Maker produces schema'd artifacts via LLM. **Still never exercised against
   the live API** — first `HIVE_USE_LLM=1` run may need a tweak.
7. **Action layer is server-side-only.** `web_search` works through the model
   call itself; client-side tools (vault read/write, email send, browser)
   need a tool-use execution loop that doesn't exist yet. `vault_read` is
   granted in tools.yaml but is a no-op today.

## The venture-studio arc (owner's primary use case)

"Ask HIVE for viable apps → pick one → ship it to the App Store" decomposes
into stages with very different difficulty tags:

| Stage | Workflow | Needs | Tag |
|---|---|---|---|
| Market scan → ranked shortlist → owner picks | `market_scan` (built) | web_search tool for live signals (P1) | PROVEN |
| Validate pick (landing page, waitlist) | future SOP | publish gate + one web deploy tool | HARD |
| Build the product | future `ship_product` | Maker with code tools / coding-agent runtime | HARD |
| Launch (App Store / Play / web) | future SOP | browser agent (App Store Connect), spend gates (dev accounts, domains) | HARD→FRONTIER |

The architecture is unchanged across all four — each stage is an SOP plus
action-layer tools, gated like everything else. Build them in order; don't
start `ship_product` until scans are producing picks worth building.

## Suggested build order from here

1. ~~HTTP API for the frontend~~ — **done 2026-07-06** (`hive serve`).
2. ~~Web search tool for Research~~ — **done 2026-07-06** (server-side
   `web_search` via the action registry; venture-studio grants it to
   research; least privilege enforced + traced).
3. ~~Dial persistence~~ — **done 2026-07-06** (kv_state).
4. ~~Global daily budget brake~~ — **done 2026-07-06.**
5. ~~Eval harness~~ — **done 2026-07-06** (`hive eval`): golden cases in
   `adapters/<biz>/evals/**/*.yaml`; structural checks on any backend,
   content checks on real backends only (skipped-not-faked on stub);
   history appended to `<data_dir>/evals/history.jsonl`; CI-friendly exit
   code. Ollama backend added same day — free real-generation evals.
6. **First live-model run** — exercise `AnthropicModelClient` for real
   (needs an API key, deferred until budget exists): one market scan
   end-to-end, verify search results, spend attribution, pause_turn path.
7. ~~First real send path~~ — **done 2026-07-07** (`actions/email.py`).
   The send executes AT THE GATE, never inside an agent — no prompt loop ever
   holds a send capability, so prompt injection cannot reach it. Semantics:
   **Approve** = HIVE emails the latest Draft to `job.context.reply_to`
   (missing recipient → FAILED loudly, warned on the card up front);
   **Edit** = job completes, HIVE sends nothing (owner takes it manual);
   **Reject** = cancelled. Earned **L2** on a non-checkpoint send step
   auto-sends with an audit trace — the dial's promise, live. Backends:
   `outbox` (default, $0 — real .eml files in `<data_dir>/outbox`, open them
   in a mail client) and `smtp` (stdlib, STARTTLS; any provider — a free
   Gmail app password works). SMTP is tested against a mock; first real
   delivery unverified until creds exist.
8. ~~Inbox/webhook event source~~ — **done 2026-07-06**: file inbox
   (`hive watch`, auto-started inside `hive serve`) + token-gated
   `POST /events`. An email bridge = anything that saves messages into the
   inbox dir; native IMAP polling can come later if needed.
9. ~~Digest on a schedule + Telegram delivery~~ — **done 2026-07-06**:
   `DigestScheduler` thread in `hive serve` (once per local day, kv-guarded
   across restarts, manual `hive digest --send` / `POST /digest/send` counts
   as the day's send). Sinks: vault archive always; Telegram dormant until
   `HIVE_TELEGRAM_BOT_TOKEN` + `HIVE_TELEGRAM_CHAT_ID` are set (bots are free
   via @BotFather — the sink is built and tested against a mocked API; first
   real send unverified until creds exist). Approval-card *push* delivery to
   Telegram (not just the digest) is a later addition.
10. **`ship_product` SOP** — only after scans are boring (see the arc above).
