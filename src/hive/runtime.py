"""Composition root: wires adapter, store, bus, coordinator into a runtime."""

from __future__ import annotations

import threading

from hive.adapter import BusinessAdapter, load_adapter
from hive.agents.coordinator import Coordinator
from hive.agents.model import (
    AnthropicModelClient,
    ModelClient,
    OllamaModelClient,
    StubModelClient,
)
from hive.config import HiveConfig
from hive.events.bus import Event, EventBus
from hive.jobs.models import Job
from hive.jobs.store import JobStore
from hive.memory.semantic import Vault
from hive.observability.trace import TraceWriter
from hive.policy.budgets import KillSwitch


class Runtime:
    def __init__(self, config: HiveConfig | None = None, model: ModelClient | None = None) -> None:
        self.config = config or HiveConfig.from_env()
        self.adapter: BusinessAdapter = load_adapter(self.config.adapter_dir)
        self.store = JobStore(self.config.db_path)
        self.trace = TraceWriter(self.config.trace_dir)
        self.vault = Vault(self.config.vault_dir)  # semantic memory (Obsidian-compatible)
        self.kill_switch = KillSwitch()
        self.model: ModelClient = model or self._make_model()
        from hive.actions.email import make_email_sender
        from hive.voice import make_voice_backend

        self.email_sender = make_email_sender(self.config)
        self.voice = make_voice_backend(self.config)
        self.coordinator = Coordinator(
            adapter=self.adapter,
            store=self.store,
            trace=self.trace,
            model=self.model,
            kill_switch=self.kill_switch,
            email_sender=self.email_sender,
        )
        self.bus = EventBus()
        for sop in self.adapter.workflows.values():
            self.bus.subscribe(sop.trigger, self._on_event)
        self._last_job: Job | None = None
        # Multiple producers (API threads, inbox watcher) share one runtime.
        # RLock so callers that already hold it (e.g. the API around a
        # multi-step mutation) can call trigger() without deadlocking.
        self.lock = threading.RLock()

    def _make_model(self) -> ModelClient:
        backend = self.config.model_backend
        if backend == "anthropic":
            return AnthropicModelClient()
        if backend == "ollama":
            return OllamaModelClient(
                model=self.config.ollama_model, base_url=self.config.ollama_url
            )
        return StubModelClient()

    def _on_event(self, event: Event) -> None:
        self._last_job = self.coordinator.handle_event(event)

    def trigger(self, event: Event) -> Job | None:
        """Publish an event and return the job it opened (if any).
        Thread-safe: jobs from any source run one at a time."""
        with self.lock:
            self._last_job = None
            self.bus.publish(event)
            return self._last_job
