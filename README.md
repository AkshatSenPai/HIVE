# HIVE

An operating system for running a business with AI agents: a coordinator
plans, specialist agents execute, the owner governs through approvals,
digests, and escalations. The business itself plugs in as an **adapter** —
HIVE core stays business-agnostic.

This repo is the **P0 base**: the orchestration spine (event bus → coordinator
→ job FSM), the three-agent pilot roster (Coordinator, Research, Maker),
budgets + kill switch, the approval/escalation/digest surfaces, external-content
fencing, JSONL traces, and one example adapter. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how it maps to the PRD and
what comes next.

## Quick start

```powershell
# from the repo root
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

pytest                 # prove the spine works
hive eval              # run the adapter's golden eval cases (free, offline)
hive demo              # inbound-event workflow: lead email -> proposal -> approval card
hive approvals         # see the approval card the demo produced
hive approve <card_id> # finish the job
hive digest            # owner's summary view
hive trace <job_id>    # full replayable trace

# owner-initiated workflow (second adapter, zero core changes):
hive -a adapters/venture-studio ask "Search the market for viable apps we can make"
hive -a adapters/venture-studio approve <card_id>   # = "build this one"
```

### Voice (local, optional — owner channel)

Owner-channel voice defaults to a **stub** (canned transcript + a tone), so
everything above runs with no models. For **real** local voice — `faster-whisper`
(STT) + `kokoro` (TTS), the same stack as Zenith, $0 and offline — use a
**Python 3.11** venv (kokoro/spacy/blis ship no 3.14 wheels):

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[voice,api]"     # CPU torch; ~1 GB of deps
$env:HIVE_VOICE_BACKEND = "local" # flip on the real backend
pytest -k roundtrip               # text -> Kokoro -> WAV -> Whisper -> text
hive -a adapters/venture-studio serve
```

Models **auto-download from HuggingFace on first use** (Kokoro-82M ~330 MB; the
Whisper weights per `HIVE_WHISPER_MODEL`) — no manual weight fetch. Knobs:
`HIVE_WHISPER_MODEL` (`small` — `base` is too weak for real mic speech),
`HIVE_KOKORO_VOICE` (`af_heart`), `HIVE_KOKORO_LANG` (`a`=American),
`HIVE_VOICE_MODEL_DIR` (optional; defaults to the shared HF cache).
Any of these can go in a **repo-root `.env`** (loaded at CLI startup) instead of
the shell — copy `.env.example` and uncomment what you need.

### HTTP API (for the frontend)

The dashboard talks to HIVE over JSON, not the CLI:

```powershell
pip install -e ".[api]"
hive -a adapters/venture-studio serve      # http://127.0.0.1:8000
# UI at /   ·   interactive API docs at /docs
```

**Frontend goes in `web/`** — a multi-page set of self-contained HTML files
(`index.html`, `tasks.html`, `approvals.html`, …) served same-origin by the
API. Drop exported pages in and they're live; their JS calls the API with
**relative paths** (`fetch('/jobs')`), so there's no CORS, no build step, and
nothing else to run. API routes always take precedence over the static mount,
so the UI can never shadow an endpoint.

**Building the frontend?** [docs/DESIGN_PROMPT.md](docs/DESIGN_PROMPT.md) has
per-page prompts for the Claude Design app (Part A shared conventions + Part B
page briefs). [docs/FRONTEND.md](docs/FRONTEND.md) holds the stack rationale
and full API contract.

| Method & path | Does |
|---|---|
| `GET /health` | liveness + kill-switch state |
| `GET /adapter` | mounted business: workflows, triggers, metrics |
| `POST /jobs` | open a job (`{"subject": "..."}` = owner request; or `{"type":"lead.new",...}`) |
| `GET /jobs` · `GET /jobs/{id}` | list jobs / one job **with its produced artifacts** |
| `GET /jobs/{id}/trace` | full replayable trace |
| `GET /approvals` | pending approval cards |
| `POST /approvals/{id}/decision` | `{"decision":"approve"\|"reject"\|"edit","note":""}` |
| `GET /digest` | owner digest |
| `GET`·`POST /killswitch` | read / toggle the global pause |

CORS is open in dev so a separately-served frontend can call in; lock it down before production.

### Event sources (how work arrives)

Besides the CLI and the UI, two hands-off sources run with `hive serve`:

- **File inbox** — drop a file into `<data_dir>/inbox/` and HIVE opens a job.
  `.json` (`{"type","subject","raw_context","source"}`) or plain `.txt`/`.md`
  (first line = subject, body = untrusted context, fenced). Processed files
  are archived to `inbox/processed/`, malformed ones quarantined to
  `inbox/failed/` with an error note. Standalone: `hive watch`.
- **Webhook** — `POST /events` with header `X-Hive-Token`. Disabled unless
  `HIVE_WEBHOOK_TOKEN` is set; unmatched event types return
  `{"matched": false}` rather than an error.

Anything that can write a file or make an HTTP call can feed HIVE — an email
bridge, Zapier, a cron script, another agent.

### The send path (what "approve" actually does)

Approving a `send` card makes HIVE email the deliverable to the job's
`reply_to`. Default backend is the **outbox** (`<data_dir>/outbox/*.eml` —
real email files, no credentials, send them yourself); set
`HIVE_EMAIL_BACKEND=smtp` + `HIVE_SMTP_HOST/PORT/USER/PASSWORD` +
`HIVE_EMAIL_FROM` for real delivery. **Edit** completes the job without HIVE
sending (you take it manual); **Reject** cancels. A send step that has earned
L2 autonomy sends automatically, fully audited in the trace. The send always
executes at the approval gate — no agent ever holds a send tool.

### Daily digest

`hive serve` delivers the digest once a day (`HIVE_DIGEST_TIME`, default
09:00 local): archived to the vault at `digests/YYYY-MM-DD.md` (visible in
Obsidian + the Memory page), and to **Telegram** if you set
`HIVE_TELEGRAM_BOT_TOKEN` + `HIVE_TELEGRAM_CHAT_ID` (bots are free — message
@BotFather to create one). Manual send: `hive digest --send`. The digest also
surfaces **improvement proposals** — workflow steps that earned an autonomy
upgrade through consecutive approvals — which you ratify with
`hive ratify <step_key>` (or `POST /autonomy/ratify`).

Two workflow patterns ship with the base: **inbound-event** (a lead arrives →
proposal package, `adapters/example`) and **owner-initiated** (you ask HIVE for
something → researched shortlist → you pick at a checkpoint,
`adapters/venture-studio`). Both are plain SOPs — no special-case code.

The demo uses a deterministic **stub model** so everything runs with zero API
cost. To use real models, `pip install -e ".[llm]"`, set `ANTHROPIC_API_KEY`,
and set `HIVE_USE_LLM=1`.

## Layout

```
adapters/example/       # the business mount point (profile, SOPs, tools, policies, metrics)
src/hive/
  events/               # event bus (triggers open jobs)
  agents/               # coordinator + research + maker, model routing layer
  jobs/                 # job model, FSM, SQLite store
  policy/               # budgets, kill switch, autonomy dial, action gates
  governance/           # approval cards, escalations, digest
  memory/               # semantic vault, episodic queries, procedural SOPs
  observability/        # per-job JSONL traces
  api/                  # FastAPI HTTP layer the frontend calls (+ serves web/)
  fencing.py            # external-content fencing (prompt-injection defense)
  cli.py                # P0 owner surface (CLI)
web/                    # frontend — drop your Claude Design export as index.html
tests/                  # FSM, budgets, gates, fencing, adapter, pipeline, API
```

## Environment variables

| Variable          | Meaning                                   | Default            |
|-------------------|-------------------------------------------|--------------------|
| `HIVE_ADAPTER_DIR`| which business adapter to mount           | `adapters/example` |
| `HIVE_DATA_DIR`   | where jobs DB / traces / vault live       | `.hive/`           |
| `HIVE_VAULT_DIR`  | point semantic memory at an existing Obsidian vault | `.hive/vault` |
| `HIVE_WEB_DIR`    | where the frontend pages live             | `web/`             |
| `HIVE_MODEL_BACKEND` | `stub` (canned, $0) · `ollama` (local models, $0) · `anthropic` (live, paid) | `stub` |
| `HIVE_OLLAMA_MODEL` | local model when backend is `ollama`    | `mistral:7b-instruct-q4_K_M` |
| `HIVE_OLLAMA_URL` | Ollama server                             | `http://localhost:11434` |
| `ANTHROPIC_API_KEY` | only needed when backend is `anthropic` | —                  |

**Free real AI:** with [Ollama](https://ollama.com) installed,
`HIVE_MODEL_BACKEND=ollama hive -a adapters/venture-studio ask "..."` runs the
whole pipeline on a local model at $0 — real generated briefs and shortlists
(at local-model quality), full spend/trace accounting showing $0.0000. The
`stub` backend stays the default for tests and instant plumbing checks.

## Safety constitution (enforced in code, not vibes)

- Consequential actions (send/spend/publish/contract/live-settings) gate on an
  approval card; **spend never fully opens** even at max autonomy.
- All external content is fenced before it reaches a prompt.
- Per-job budgets hard-stop and escalate; one global kill switch.
- Every model call is traced with tokens and dollars attributed.
- Stop-and-ask on ambiguity — guessing is a defect.
