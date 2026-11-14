# HIVE Owner-Channel Voice — Design Spec

*2026-07-08 · pulls PRD P4 "voice ops" forward · defers the P4 agent phone-calls*

## Goal

Let the owner run HIVE hands-free: **speak** commands and approval decisions,
and **hear** briefings, the digest, and approval cards read aloud. Local-first
and $0 by default, consistent with the rest of HIVE (stub/Ollama/outbox).

Reuses the Zenith voice stack: **Whisper** (STT) + **Kokoro** (TTS).

## Scope (this build)

Four voice intents, all push-to-talk, all in the browser:

1. **Command** — speak → dispatch a job (`POST /jobs`) → spoken confirmation.
2. **Brief** — "give me a brief" / "brief me" / "what's happening" / "status"
   → HIVE speaks a short, prioritized status briefing.
3. **Read-aloud** — "read the digest" → HIVE speaks the digest.
4. **Approval review** — "review approvals" → HIVE reads each pending card
   aloud; owner speaks approve / reject / skip / next / stop; consequential
   decisions require a spoken **read-back + confirm**.

## Non-goals (explicitly deferred)

- Agent phone-calls (telephony, realtime turn-taking, legal disclosure) — P4.
- Wake word / always-on mic (push-to-talk only; privacy).
- Proactive spoken announcements ("you have a new approval").
- LLM-generated conversational brief (v1 brief is deterministic; LLM later).

## Architecture

```
Browser (every page)                     HIVE backend (FastAPI)
┌────────────────────────┐               ┌─────────────────────────────────┐
│ web/hive-voice.js       │  mic WAV  →   │ POST /voice/transcribe → STT     │
│  · floating mic button  │               │ POST /voice/speak      → TTS     │
│  · push-to-talk         │  ← reply WAV  │ GET  /brief            → text    │
│  · status pill          │               │ VoiceBackend: stub | local       │
│  · intent routing       │  reuse existing endpoints:                       │
└─────────┬──────────────┘   POST /jobs · GET/POST /approvals · GET /digest  │
          └──────────────────────────────▶ existing Runtime / Coordinator    │
                                          └─────────────────────────────────┘
```

Voice is another **front-end over the same runtime** — not a rearchitecture.
The widget reads state from the **API, not the DOM**.

### Frontend: a floating widget (does NOT edit the generated pages)

`web/hive-voice.js` is a self-contained overlay injected via one
`<script src="hive-voice.js">` include on each page (same mechanism as
`hive-transitions.js`). It renders its own floating mic button + status pill;
it never touches the Claude-Design-generated React, so a future re-export of
the pages won't clobber voice. Respects the design system's dark/cyan look.

Push-to-talk: hold the button (or a hotkey) to record; release to send. The
mic is never open otherwise.

**Intent routing** (deterministic keyword match on the transcript):

| Transcript contains | Intent |
|---|---|
| brief · briefing · what's happening · status · catch me up | **brief** → `GET /brief` → speak |
| review · approvals · what needs me · anything waiting | **approval review** loop |
| digest | **read digest** → `GET /digest` → speak |
| (anything else) | **command** → `POST /jobs {subject: transcript}` → speak result |

### Backend: a `VoiceBackend`, selectable like the model backends

Mirrors `stub | ollama | anthropic`. New `src/hive/voice/`:

- `VoiceBackend` protocol: `transcribe(wav_bytes) -> str`, `speak(text) -> wav_bytes`.
- **`StubVoiceBackend`** (default): `transcribe` returns a configured canned
  string (so the loop + tests run with no audio); `speak` returns a short
  valid WAV (a soft tone / near-silence). **No install, fully testable headless.**
- **`LocalVoiceBackend`**: `faster-whisper` (CT2, CPU) for STT; `kokoro` (the
  `KPipeline` package, torch-driven, CPU) for TTS — **the same stack as Zenith**.
  Enabled with `HIVE_VOICE_BACKEND=local`. Lazy-loads models on first use; both
  auto-download from HuggingFace (Kokoro-82M ~330 MB; the Whisper weights). Needs
  the `[voice]` extra on **Python 3.11** (spacy/blis have no 3.14 wheels).

  *(Implementation note: an earlier draft targeted `kokoro-onnx` with manually
  fetched `.onnx`/`.bin` weights; the shipped backend uses the `kokoro` package
  to stay identical to Zenith and to reuse Zenith's already-cached weights.)*

Config (env): `HIVE_VOICE_BACKEND` (`stub`|`local`, default `stub`),
`HIVE_WHISPER_MODEL` (default `base`), `HIVE_KOKORO_VOICE` (default a chosen
voice), `HIVE_VOICE_MODEL_DIR` (where the Kokoro ONNX + voices live).

### API endpoints (new, in `api/app.py`)

```
POST /voice/transcribe   body: audio/wav bytes (16 kHz mono)
                         → { "text": "..." }
POST /voice/speak        body: { "text": "..." }
                         → audio/wav bytes
GET  /brief              → { "text": "<spoken-style briefing>" }
```

`GET /system` gains a `voice: {backend, ready}` field (reusing the existing
Settings endpoint rather than adding a new one) so the widget can show whether
voice is live or stub.

### Audio format (no ffmpeg)

Browser captures mic via the Web Audio API, downsamples to **16 kHz mono PCM**,
and posts a small WAV. Backend reads it with stdlib `wave` + `numpy` → a float32
array straight into faster-whisper (which accepts arrays — no ffmpeg needed).
Kokoro returns a 24 kHz float array → backend writes a WAV → browser plays it
via an `<audio>` element. This deliberately avoids MediaRecorder/webm +
server-side ffmpeg decoding (ffmpeg is not installed).

### `build_brief()` — the spoken briefing

New `build_brief(store, policies) -> str` in `governance/` beside `build_digest`.
Deterministic, $0, phrased for the ear. Prioritized:

1. Headline — approvals waiting + anything needing attention, or "All clear."
2. The pending approvals (top few): title + cost.
3. Escalated / failed jobs: id + short reason.
4. In-flight count.
5. Spend today vs the daily cap.

Kept to a few natural sentences. Example:
> "You have two approvals waiting: send the Northwind proposal, sixty-two cents;
> and charge the invoice batch, two thousand four hundred dollars. One job
> escalated — a pricing conflict on the Delta account. Three jobs in progress.
> You've spent four cents today."

## Safety (voice-specific)

- **Read-back + confirm on consequential decisions.** A voice approve/reject on
  a `send`/`spend`/`publish`/`contract`/`live_settings` card is echoed back and
  requires a second word: *"You said approve — sending the proposal to
  Northwind. Say confirm."* Only "confirm" executes. Navigation words
  (skip/next/stop/repeat) need no confirm. Keeps voice inside the safety
  constitution — mis-transcription can't fire a consequential action.
- **Kill switch mutes action.** If engaged, a spoken command is answered with
  "HIVE is paused" and nothing dispatches.
- **Push-to-talk only** — no always-on mic, no wake word.
- **Fencing** is unchanged: voice commands are the trusted owner channel;
  nothing external is transcribed here. (Inbound-caller transcription — which
  would be fenced — is the deferred phone-call work.)

## Testing

- **Headless round-trip (local backend):** `text → Kokoro → WAV → Whisper →
  text`; assert the transcription roughly matches (normalized contains). This
  verifies the real pipeline with no live mic.
- **Stub-backend tests:** `/voice/transcribe` and `/voice/speak` endpoints,
  `build_brief()` content, intent routing (unit-test the keyword matcher),
  and the approval-review confirm logic.
- **Live mic/speaker:** the one thing the owner verifies in the browser
  (agent sandbox has no audio device).

## Module / file layout

```
src/hive/voice/__init__.py
src/hive/voice/backends.py      # VoiceBackend protocol, StubVoiceBackend, LocalVoiceBackend, make_voice_backend(config)
src/hive/governance/digest.py   # + build_brief()
src/hive/api/app.py             # + /voice/transcribe, /voice/speak, /brief
src/hive/config.py              # + voice_backend, whisper_model, kokoro_voice, voice_model_dir
web/hive-voice.js               # floating widget (injected on all pages)
tests/test_voice.py             # stub + round-trip + intent routing + brief
```

## Rollout

1. Build everything against the **stub** backend — endpoints, widget, brief,
   intent routing, safety/confirm, tests. Verify headless + in-browser (stub
   speaks a tone, "transcribes" canned text) so the full loop is proven.
2. Then, together (**Python 3.11 venv** — kokoro/spacy/blis have no 3.14 wheels):
   `py -3.11 -m venv .venv` → `.venv\Scripts\pip install -e ".[voice,dev,api]"`
   (CPU torch). The models **auto-download from HuggingFace on first use** — no
   manual weight fetch — reusing anything Zenith already cached. Flip
   `HIVE_VOICE_BACKEND=local`, run the round-trip test (`pytest -k roundtrip`),
   and you confirm the live mic/speaker in the browser.
