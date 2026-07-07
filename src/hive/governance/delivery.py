"""Digest delivery (PRD §3.2): the digest goes to the owner on a schedule.

Sinks (P0, both free):
- **VaultSink** (always on): each digest is archived to the semantic vault at
  `digests/YYYY-MM-DD.md` — visible in Obsidian and the dashboard Memory page.
- **TelegramSink** (dormant until configured): Telegram bots are free — create
  one via @BotFather, set HIVE_TELEGRAM_BOT_TOKEN + HIVE_TELEGRAM_CHAT_ID, and
  the daily digest lands in your chat. stdlib urllib, no dependency.

The scheduler fires once per local day at `digest_time` (default 09:00) and
guards against double sends across restarts via kv_state. Sink failures are
reported, never fatal — a broken Telegram token must not kill the server.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from datetime import datetime, time as dtime
from typing import TYPE_CHECKING, Any, Protocol

from hive.governance.digest import build_digest

if TYPE_CHECKING:
    from hive.memory.semantic import Vault
    from hive.runtime import Runtime

_STATE_KEY = "digest_last_date"


class DigestSink(Protocol):
    name: str

    def send(self, text: str, date_str: str) -> str: ...


class VaultSink:
    """Archive into semantic memory — Obsidian-visible, Memory-page-visible."""

    name = "vault"

    def __init__(self, vault: "Vault") -> None:
        self.vault = vault

    def send(self, text: str, date_str: str) -> str:
        path = self.vault.write(f"digests/{date_str}.md", text)
        return str(path)


class TelegramSink:
    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str, timeout: float = 15.0) -> None:
        self.bot_token, self.chat_id, self.timeout = bot_token, chat_id, timeout

    def send(self, text: str, date_str: str) -> str:
        payload = json.dumps({
            "chat_id": self.chat_id,
            "text": text[:4000],  # Telegram caps messages at 4096 chars
        }).encode("utf-8")
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as raw:
            body = json.loads(raw.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(f"telegram API error: {body}")
        return f"sent to chat {self.chat_id}"


def default_sinks(runtime: "Runtime") -> list[Any]:
    sinks: list[Any] = [VaultSink(runtime.vault)]
    config = runtime.config
    if config.telegram_bot_token and config.telegram_chat_id:
        sinks.append(TelegramSink(config.telegram_bot_token, config.telegram_chat_id))
    return sinks


class DigestScheduler:
    """Once-per-day delivery at config.digest_time (owner-local clock)."""

    def __init__(self, runtime: "Runtime", sinks: list[Any] | None = None,
                 check_seconds: float = 30.0) -> None:
        self.runtime = runtime
        self.sinks = sinks if sinks is not None else default_sinks(runtime)
        self.check_seconds = check_seconds

    def due(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        if self.runtime.store.load_state(_STATE_KEY) == now.date().isoformat():
            return False  # already delivered today (survives restarts)
        hour, minute = (int(part) for part in self.runtime.config.digest_time.split(":"))
        return now.time() >= dtime(hour, minute)

    def deliver(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Send through every sink; per-sink failures reported, never raised.
        Manual sends count as the day's digest (no scheduled double-send)."""
        now = now or datetime.now()
        date_str = now.date().isoformat()
        with self.runtime.lock:
            text = build_digest(self.runtime.store, self.runtime.adapter.policies)
        results = []
        for sink in self.sinks:
            try:
                results.append({"sink": sink.name, "ok": True, "detail": sink.send(text, date_str)})
            except Exception as exc:
                results.append({"sink": sink.name, "ok": False, "detail": str(exc)})
        self.runtime.store.save_state(_STATE_KEY, date_str)
        return results

    def run_if_due(self, now: datetime | None = None) -> list[dict[str, Any]] | None:
        return self.deliver(now) if self.due(now) else None

    def watch(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            self.run_if_due()
            stop.wait(self.check_seconds)
