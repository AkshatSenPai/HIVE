"""HTTP API — the surface a frontend (dashboard, Telegram bridge) talks to.

A thin JSON layer over the existing Runtime / JobStore. Every owner action the
CLI can do is an endpoint here. No business logic lives in this file: it maps
requests onto the coordinator and store, nothing more.

Run it:  hive serve            (or: uvicorn "hive.api:create_app" --factory)
Docs at: http://127.0.0.1:8000/docs  (OpenAPI, auto-generated — build against it)

Requires the api extra:  pip install -e ".[api]"
"""

from __future__ import annotations

import hmac
from pathlib import Path
from typing import Any, Literal

from fastapi import Body, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from hive.events.bus import Event
from hive.governance.approvals import ApprovalStatus
from hive.governance.digest import build_digest
from hive.runtime import Runtime

_DECISION_MAP: dict[str, ApprovalStatus] = {
    "approve": ApprovalStatus.APPROVED,
    "edit": ApprovalStatus.EDITED,
    "reject": ApprovalStatus.REJECTED,
}


class TriggerRequest(BaseModel):
    type: str = Field(default="owner.request", description="event type; default is an owner-initiated request")
    subject: str = Field(description="the request text, or the lead/order subject")
    raw_context: str = Field(default="", description="untrusted payload; fenced before it reaches any prompt")
    source: str = "api"
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionRequest(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    note: str = ""


class KillRequest(BaseModel):
    engaged: bool


class RatifyRequest(BaseModel):
    # Module-level on purpose: with `from __future__ import annotations`,
    # FastAPI can't resolve models defined inside create_app.
    step_key: str


class SpeakRequest(BaseModel):
    text: str


def create_app(runtime: Runtime | None = None, web_dir: Path | None = None) -> FastAPI:
    rt = runtime or Runtime()
    web_dir = web_dir if web_dir is not None else rt.config.web_dir
    lock = rt.lock  # shared with every other producer (inbox watcher, CLI)

    app = FastAPI(title="HIVE", version="0.0.1", summary="Autonomous business OS — owner API")
    # Dev-permissive CORS so a separately-served frontend can call in.
    # Tighten allow_origins to the real dashboard origin before production.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- meta ---------------------------------------------------------------

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "adapter": rt.adapter.name, "kill_switch": rt.kill_switch.engaged}

    @app.get("/adapter")
    def adapter() -> dict[str, Any]:
        routing: dict[str, str] = getattr(rt.model, "routing", {})
        tiers = {a.name: a.tier for a in rt.coordinator.roster.values()}
        tiers[rt.coordinator.name] = rt.coordinator.tier
        agents = []
        for name, cfg in rt.adapter.tools.get("agents", {}).items():
            tier = tiers.get(name, "specialist")
            agents.append({
                "name": name,
                "tier": tier,
                "model": routing.get(tier, ""),
                "tools": cfg.get("tools", []),
                "steps": [
                    sop.step_key(s.id)
                    for sop in rt.adapter.workflows.values()
                    for s in sop.steps
                    if s.agent == name
                ],
            })
        return {
            "name": rt.adapter.name,
            "profile": rt.adapter.profile,
            "agents": agents,
            "workflows": [
                {
                    "name": sop.name,
                    "version": sop.version,
                    "trigger": sop.trigger,
                    "description": sop.description,
                    "steps": [
                        {"id": s.id, "agent": s.agent, "action_kind": s.action_kind, "checkpoint": s.checkpoint}
                        for s in sop.steps
                    ],
                }
                for sop in rt.adapter.workflows.values()
            ],
            "metrics": rt.adapter.metrics,
        }

    @app.get("/system")
    def system() -> dict[str, Any]:
        """Read-only system configuration for the Settings page. No secrets:
        model routing, policies, mode flags, and today's spend."""
        from datetime import datetime, timezone

        return {
            "adapter": rt.adapter.name,
            "use_llm": rt.config.use_llm,
            "model_backend": rt.config.model_backend,  # stub | ollama | anthropic
            "model_routing": getattr(rt.model, "routing", {}),
            "policies": rt.adapter.policies,
            "kill_switch": rt.kill_switch.engaged,
            "spend_today_usd": round(rt.store.spend_on(datetime.now(timezone.utc).date()), 6),
            "global_daily_cap_usd": rt.adapter.policies.get("budgets", {}).get("global_daily", {}).get("max_usd"),
            "voice": {"backend": rt.voice.name, "ready": rt.voice.ready},
        }

    # -- jobs ---------------------------------------------------------------

    @app.post("/jobs")
    def create_job(req: TriggerRequest) -> dict[str, Any]:
        metadata = {"subject": req.subject, "raw_context": req.raw_context, "source": req.source, **req.metadata}
        event = Event(type=req.type, source=req.source, metadata=metadata)
        with lock:
            job = rt.trigger(event)
        if job is None:
            raise HTTPException(422, f"no workflow in adapter '{rt.adapter.name}' handles trigger '{req.type}'")
        pending = [c for c in rt.store.list_cards(ApprovalStatus.PENDING.value) if c.job_id == job.id]
        return {"job": job, "pending_cards": pending}

    @app.get("/jobs")
    def list_jobs(state: str | None = None) -> dict[str, Any]:
        jobs = sorted(rt.store.list_jobs(state), key=lambda j: j.created_at, reverse=True)
        return {"jobs": jobs}

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = rt.store.get_job(job_id)
        if job is None:
            raise HTTPException(404, f"no such job: {job_id}")
        return {"job": job, "artifacts": rt.store.list_artifacts(job_id)}

    @app.get("/jobs/{job_id}/trace")
    def get_trace(job_id: str) -> dict[str, Any]:
        return {"job_id": job_id, "trace": rt.trace.read(job_id)}

    # -- approvals ----------------------------------------------------------

    @app.get("/approvals")
    def list_approvals(status: str = ApprovalStatus.PENDING.value) -> dict[str, Any]:
        return {"cards": rt.store.list_cards(status)}

    @app.get("/approvals/{card_id}")
    def get_approval(card_id: str) -> dict[str, Any]:
        card = rt.store.get_card(card_id)
        if card is None:
            raise HTTPException(404, f"no such approval card: {card_id}")
        return {"card": card}

    @app.post("/approvals/{card_id}/decision")
    def decide(card_id: str, req: DecisionRequest) -> dict[str, Any]:
        from hive.agents.coordinator import DecisionRefused

        try:
            with lock:
                job = rt.coordinator.resolve_approval(card_id, _DECISION_MAP[req.decision], note=req.note)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except DecisionRefused as exc:  # already decided, or paused
            raise HTTPException(409, str(exc)) from exc
        return {"job": job, "card": rt.store.get_card(card_id)}

    # -- webhook event source -------------------------------------------------

    @app.post("/events")
    def webhook_event(
        req: TriggerRequest,
        x_hive_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """External systems push events here. Secure by default: disabled
        until HIVE_WEBHOOK_TOKEN is set; token compared in constant time.
        Payloads are external content — they ride in raw_context and get
        fenced before any prompt."""
        expected = rt.config.webhook_token
        if not expected:
            raise HTTPException(503, "webhook source disabled — set HIVE_WEBHOOK_TOKEN to enable")
        if not x_hive_token or not hmac.compare_digest(x_hive_token, expected):
            raise HTTPException(401, "invalid or missing X-Hive-Token header")
        event = Event(
            type=req.type,
            source="webhook",
            metadata={
                "subject": req.subject,
                "raw_context": req.raw_context,
                "source": f"webhook:{req.source}",
                **req.metadata,
            },
        )
        job = rt.trigger(event)  # trigger() takes the runtime lock itself
        if job is None:
            # An unmatched event type is not a caller error — nothing subscribed.
            return {"matched": False, "job": None}
        return {"matched": True, "job": job}

    # -- memory (semantic vault — Obsidian-compatible markdown) --------------

    @app.get("/vault")
    def vault_list() -> dict[str, Any]:
        return {"root": str(rt.vault.root), "files": rt.vault.list()}

    @app.get("/vault/file")
    def vault_file(path: str) -> dict[str, str]:
        from hive.memory.semantic import VaultPathError

        try:
            return {"path": path, "content": rt.vault.read(path)}
        except FileNotFoundError:
            raise HTTPException(404, f"no such vault file: {path}")
        except VaultPathError:
            raise HTTPException(400, "path escapes the vault")

    # -- governance ---------------------------------------------------------

    @app.get("/digest")
    def digest() -> dict[str, str]:
        return {"text": build_digest(rt.store, rt.adapter.policies)}

    @app.get("/brief")
    def brief() -> dict[str, str]:
        """Short, prioritized, spoken-style briefing — the voice 'give me a brief'."""
        from hive.governance.digest import build_brief

        return {"text": build_brief(rt.store, rt.adapter.policies)}

    # -- voice (owner-channel; STT in, TTS out) ------------------------------

    @app.post("/voice/transcribe")
    def voice_transcribe(audio: bytes = Body(..., media_type="audio/wav")) -> dict[str, str]:
        """Speech -> text. Body is a 16 kHz mono WAV from the browser."""
        try:
            return {"text": rt.voice.transcribe(audio)}
        except Exception as exc:
            raise HTTPException(500, f"transcription failed: {exc}") from exc

    @app.post("/voice/speak")
    def voice_speak(req: SpeakRequest) -> Response:
        """Text -> speech. Returns a WAV the browser plays."""
        try:
            wav = rt.voice.speak(req.text)
        except Exception as exc:
            raise HTTPException(500, f"speech synthesis failed: {exc}") from exc
        return Response(content=wav, media_type="audio/wav")

    @app.post("/digest/send")
    def digest_send() -> dict[str, Any]:
        """Deliver the digest through every configured sink right now.
        Counts as today's scheduled send."""
        from hive.governance.delivery import DigestScheduler

        return {"results": DigestScheduler(rt).deliver()}

    @app.get("/autonomy")
    def autonomy() -> dict[str, Any]:
        dial = rt.coordinator.gate.dial
        return {
            "upgrade_threshold": dial.upgrade_threshold,
            "steps": {
                key: {
                    "level": int(rec.level), "level_name": rec.level.name,
                    "consecutive_approvals": rec.consecutive_approvals,
                    "upgrade_proposed": rec.upgrade_proposed,
                }
                for key, rec in dial.steps.items()
            },
        }

    @app.post("/autonomy/ratify")
    def ratify(req: RatifyRequest) -> dict[str, Any]:
        with lock:
            if req.step_key not in rt.coordinator.gate.dial.steps:
                raise HTTPException(404, f"no autonomy record for step '{req.step_key}'")
            level = rt.coordinator.ratify_autonomy(req.step_key)
        return {"step_key": req.step_key, "level": int(level), "level_name": level.name}

    @app.get("/killswitch")
    def get_kill() -> dict[str, bool]:
        return {"engaged": rt.kill_switch.engaged}

    @app.post("/killswitch")
    def set_kill(req: KillRequest) -> dict[str, bool]:
        rt.kill_switch.engage() if req.engaged else rt.kill_switch.release()
        return {"engaged": rt.kill_switch.engaged}

    # Frontend last: mounting at "/" is a catch-all, so it must come AFTER every
    # API route or it would shadow them. Serve the SPA/HTML only if present —
    # drop your Claude Design export at web/index.html and it appears here.
    # html=True serves index.html at "/" and 404s fall back to it (SPA-friendly).
    if (web_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")

    return app
