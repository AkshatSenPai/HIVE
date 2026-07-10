# HIVE — TODO

Status as of 2026-07-11. The P0 machinery + dashboard + owner-channel voice are
built and tested (**125 tests green** on the 3.11 voice venv; 124 + 1 skipped on
the stub-only 3.14 interpreter). Local voice (Whisper + Kokoro) now runs
end-to-end. What remains is below.

Legend: 🟢 free / buildable now · 💳 needs a funded API key (money) · 🔑 needs
free credentials (you set up) · 🧊 deferred to a later PRD phase.

---

## Part 1 — What's left in the VOICE part

Owner-channel voice is **built and now runs for real** (Whisper STT + Kokoro TTS,
stub/local backends, floating widget, `/brief`, approval-review with read-back+confirm,
hardened by an adversarial review). Local voice uses **the same stack as Zenith** —
`faster-whisper` + the `kokoro` package (`KPipeline`), models auto-downloading from
HuggingFace. What's left:

### To make voice actually work (not the stub tone/canned text) — ✅ DONE 2026-07-11
- [x] 🟢 `[voice]` extra in `pyproject.toml` (`faster-whisper`, `kokoro`, `torch`, `soundfile`, `numpy`) — install is `pip install -e ".[voice]"`.
- [x] 🟢 Local stack installed in a **Python 3.11 venv** (`.venv`; kokoro/spacy/blis have no 3.14 wheels). `LocalVoiceBackend` was rewritten off `kokoro-onnx` onto the `kokoro` package to match Zenith; models **auto-download from HuggingFace** on first use (no manual weight fetch), reusing Zenith's cache.
- [x] 🟢 `HIVE_VOICE_BACKEND=local` round-trip smoke test passes on **real models** (text → Kokoro → WAV → Whisper → text). Full suite: **125 green** on the 3.11 venv; **124 + 1 skipped** on 3.14 (stub path untouched).
- [x] 👤 **Owner-verified the live mic loop in the browser (2026-07-11)** — real mic, hold-to-talk, spoken commands + brief + approval review all work. Tuning that made it usable: Whisper `base` was too weak (mis-heard "brief" → "breeze"); bumped default to `small`, and fixed the mic capture (16 kHz AudioContext + low-pass + AGC) so consonants survive.

### Tuning (once local voice runs)
- [ ] 🟢 Pick the "hive voice" — `HIVE_KOKORO_VOICE` (default `af_heart`; Kokoro has several).
- [x] 🟢 Tune Whisper size vs latency — default bumped `base` → `small` (Zenith's CPU default; `base` mis-heard real mic speech, e.g. "brief" → "breeze"). `medium` for more accuracy, slower. Also fixed the browser mic capture (16 kHz AudioContext + low-pass + AGC/NS) so consonants survive.

### Voice polish (optional, non-blocking)
- [ ] 🟢 Real VAD/silence auto-listen in the approval-review loop (currently a fixed ~4.5s window).
- [ ] 🟢 Barge-in (let the owner interrupt HIVE while it's speaking).
- [ ] 🧊 Proactive spoken announcements ("you have a new approval") — deferred by design.
- [ ] 🧊 Wake word — explicitly out of scope (push-to-talk only, for privacy).

### The OTHER half of voice (agent phone-calls) — 🧊 deferred, PRD P4 FRONTIER
- [ ] 🧊 Outbound/inbound agent calls: telephony (Twilio/SIP) + realtime turn-taking (VAD, endpointing, barge-in, sub-second latency) + legal AI-disclosure. Kokoro/Whisper are the *easy* part here; the telephony + realtime + legal is the frontier work. Build near-last.

---

## Part 2 — What's left in the PROJECT

### The one thing that matters most
- [ ] 💳 **First live-model run.** The entire system has only ever run on the stub or local Ollama — *never against a real frontier model*. One real market scan on Sonnet (a few cents) converts "architecturally complete" into "proven." This is the biggest validation gap. Blocked on a funded API key.
- [ ] 💳 The PRD's actual **P0 exit bar** is unmet: "10 real jobs, ≤2 owner touches each, zero ungated actions, cost in class" — needs real models + a real business + real volume. The machine is done; the *proving* isn't.

### Free, buildable now (P1 remainder / polish)
- [ ] 🟢 **`vault_read` as a real tool** — agents are *granted* it but it's a no-op today; wiring it lets agents read the Obsidian/semantic vault for context (makes them smarter, free). Highest-value free item.
- [ ] 🟢 **Async job queue / worker split** — jobs currently run inline on the producing thread; a slow live-model job blocks its producer. Matters once on real models.
- [ ] 🟢 **Coordinator real planning** — today it maps the SOP 1:1; genuine decomposition is the PRD's "make-or-break," but can only be *evaluated* with real models (pair with the live run).
- [ ] 🟢 **Telegram approval-card push** (not just the daily digest) — approve from your phone.
- [ ] 🟢 **v2 roster agents** — Comms/Inbox triage, Analyst/Reporting (free to build + stub-test).
- [ ] 🟢 **More golden eval cases** per workflow; try `ollama pull qwen2.5:7b-instruct` and re-run `hive eval` (better local quality than Mistral).

### Free credentials, when you want them (🔑 ~2 min each)
- [ ] 🔑 Telegram bot (via @BotFather) → set `HIVE_TELEGRAM_BOT_TOKEN` + `HIVE_TELEGRAM_CHAT_ID` → digest on your phone. (Sink is built + mocked-tested; first real send unverified.)
- [ ] 🔑 SMTP app password (e.g. Gmail) → `HIVE_EMAIL_BACKEND=smtp` + `HIVE_SMTP_*` → real email send (default is the `.eml` outbox).

### Later PRD phases — 🧊 deferred (HARD→FRONTIER, in order)
- [ ] 🧊 **P2 (Hands):** browser agent (Playwright + vision for no-API web tasks) · sandboxed Computer Use · Ops agent · a second workflow.
- [ ] 🧊 **P3 (Learning):** reflection loop (mine episodic memory → propose SOP updates through the eval harness → owner ratifies) · QA agent · autonomy upgrades earned in production.
- [ ] 🧊 **P4 (Reach):** agent voice calls (above) · gated payments via capped virtual cards · Growth agent · multi-adapter (a real second business).
- [ ] 🧊 **P5 (Decide):** private edge vs. product — not before P3 is boring.

### The venture-studio "ship it" arc (the actual product goal)
- [ ] 💳 Real market scans that produce genuine opportunities (needs real models + web search — web search is *built*; models gated on money).
- [ ] 🧊 `ship_product` SOP — a coding-agent Maker that builds the picked app (HARD).
- [ ] 🧊 Launch path — browser agent driving App Store Connect etc. (HARD→FRONTIER).

### Security / hardening (surfaced by the voice review — mostly future)
- [ ] 🧊 API is unauthenticated + CORS-open **by design** for local single-owner use. If HIVE ever goes hosted/multi-user: add auth, tighten CORS, and add a server-side confirm token for consequential actions.

---

## Done (for reference — don't redo)

Orchestration spine (event bus · coordinator · job FSM · SQLite) · 3 agents +
typed artifacts · coordinator review loop · governance (approval cards ·
escalations · autonomy dial w/ persistence + ratification · budgets + global
daily brake · kill switch — now enforced on the approval path too · fencing) ·
action layer w/ server-side `web_search` (least-privilege enforced) · gated
email send path (outbox/SMTP, executes at the gate, idempotent) · memory (vault ·
episodic · procedural SOPs) · 3 model backends (stub/Ollama/Anthropic) · event
sources (file inbox · token-gated webhook) · eval harness w/ golden cases ·
scheduled digest + delivery (vault + Telegram-ready) · FastAPI over the runtime ·
8-page dashboard (live-wired, cross-fade transitions, hex-bee favicon) ·
owner-channel voice (stub + real local Whisper/Kokoro, round-trip-verified,
hardened). Prior work pushed to github.com/AkshatSenPai/HIVE; the 2026-07-11
local-voice enablement (kokoro package + 3.11 venv) is on the working tree, not
yet committed.
