"""Runtime configuration. Env vars override defaults; no secrets here —
credentials stay in the environment / OS keychain, referenced by tools.yaml.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


class HiveConfig(BaseModel):
    adapter_dir: Path = Field(default_factory=lambda: _repo_root() / "adapters" / "example")
    data_dir: Path = Field(default_factory=lambda: _repo_root() / ".hive")
    web_dir: Path = Field(default_factory=lambda: _repo_root() / "web")  # frontend served same-origin
    vault_override: Path | None = None  # point at an existing Obsidian vault folder
    # Model backend: "stub" (offline canned, $0) · "ollama" (local models, $0)
    # · "anthropic" (live API, costs money, needs ANTHROPIC_API_KEY)
    model_backend: str = "stub"
    ollama_model: str = "mistral:7b-instruct-q4_K_M"
    ollama_url: str = "http://localhost:11434"
    # Event sources
    inbox_event_type: str = "lead.new"  # event type for bare .txt/.md inbox drops
    inbox_poll_seconds: float = 5.0
    webhook_token: str | None = None  # unset => POST /events is disabled (secure by default)
    # Digest delivery
    digest_time: str = "09:00"  # local time, daily
    telegram_bot_token: str | None = None  # free via @BotFather; unset => sink dormant
    telegram_chat_id: str | None = None
    # Email send path: "outbox" writes .eml files to <data_dir>/outbox ($0, no
    # creds — you send them yourself); "smtp" sends for real via any provider.
    email_backend: str = "outbox"
    email_from: str = "hive@localhost"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    # Voice: "stub" (canned, no install, $0) · "local" (faster-whisper +
    # kokoro-onnx, local, $0, needs models). Owner-channel voice only.
    voice_backend: str = "stub"
    whisper_model: str = "base"
    kokoro_voice: str = "af_heart"
    voice_model_override: Path | None = None  # where the Kokoro ONNX + voices live

    @property
    def outbox_dir(self) -> Path:
        return self.data_dir / "outbox"

    @property
    def voice_model_dir(self) -> Path:
        return self.voice_model_override or (self.data_dir / "voice-models")

    @property
    def use_llm(self) -> bool:  # kept for API/back-compat: "costs real money?"
        return self.model_backend == "anthropic"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobs.sqlite3"

    @property
    def trace_dir(self) -> Path:
        return self.data_dir / "traces"

    @property
    def vault_dir(self) -> Path:
        return self.vault_override or (self.data_dir / "vault")

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @classmethod
    def from_env(cls) -> "HiveConfig":
        kwargs = {}
        if adapter := os.environ.get("HIVE_ADAPTER_DIR"):
            kwargs["adapter_dir"] = Path(adapter)
        if data := os.environ.get("HIVE_DATA_DIR"):
            kwargs["data_dir"] = Path(data)
        if web := os.environ.get("HIVE_WEB_DIR"):
            kwargs["web_dir"] = Path(web)
        if vault := os.environ.get("HIVE_VAULT_DIR"):
            kwargs["vault_override"] = Path(vault)
        if backend := os.environ.get("HIVE_MODEL_BACKEND"):
            kwargs["model_backend"] = backend.lower()
        elif os.environ.get("HIVE_USE_LLM", "").lower() in ("1", "true", "yes"):
            kwargs["model_backend"] = "anthropic"  # back-compat
        if om := os.environ.get("HIVE_OLLAMA_MODEL"):
            kwargs["ollama_model"] = om
        if ou := os.environ.get("HIVE_OLLAMA_URL"):
            kwargs["ollama_url"] = ou
        if iet := os.environ.get("HIVE_INBOX_EVENT_TYPE"):
            kwargs["inbox_event_type"] = iet
        if token := os.environ.get("HIVE_WEBHOOK_TOKEN"):
            kwargs["webhook_token"] = token
        if dt := os.environ.get("HIVE_DIGEST_TIME"):
            kwargs["digest_time"] = dt
        if tg := os.environ.get("HIVE_TELEGRAM_BOT_TOKEN"):
            kwargs["telegram_bot_token"] = tg
        if chat := os.environ.get("HIVE_TELEGRAM_CHAT_ID"):
            kwargs["telegram_chat_id"] = chat
        if eb := os.environ.get("HIVE_EMAIL_BACKEND"):
            kwargs["email_backend"] = eb.lower()
        if ef := os.environ.get("HIVE_EMAIL_FROM"):
            kwargs["email_from"] = ef
        if sh := os.environ.get("HIVE_SMTP_HOST"):
            kwargs["smtp_host"] = sh
        if sp := os.environ.get("HIVE_SMTP_PORT"):
            kwargs["smtp_port"] = int(sp)
        if su := os.environ.get("HIVE_SMTP_USER"):
            kwargs["smtp_user"] = su
        if spw := os.environ.get("HIVE_SMTP_PASSWORD"):
            kwargs["smtp_password"] = spw
        if vb := os.environ.get("HIVE_VOICE_BACKEND"):
            kwargs["voice_backend"] = vb.lower()
        if wm := os.environ.get("HIVE_WHISPER_MODEL"):
            kwargs["whisper_model"] = wm
        if kv := os.environ.get("HIVE_KOKORO_VOICE"):
            kwargs["kokoro_voice"] = kv
        if vmd := os.environ.get("HIVE_VOICE_MODEL_DIR"):
            kwargs["voice_model_override"] = Path(vmd)
        return cls(**kwargs)
