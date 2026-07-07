"""Model access layer with multi-model routing (PRD §9).

Policy-driven model choice per tier: frontier model for planning/judgment,
fast-cheap for extraction/formatting. Agents never construct API calls —
they ask a ModelClient, and the client applies the routing table.

StubModelClient keeps the whole system runnable (and testable) with no API
key; AnthropicModelClient is the real path (pip install hive[llm]).
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

# Routing tiers -> model IDs (owner's routing decision, 2026-07-06):
#   planner    -> Opus   (coordinator judgment)
#   specialist -> Sonnet (research + maker: the doing tier)
#   extractor  -> Haiku  (quick replies, triage, extraction/formatting)
#   frontier   -> Fable  (big projects only; must be proposed to the owner via
#                         an approval card before any job runs on this tier)
DEFAULT_ROUTING: dict[str, str] = {
    "planner": "claude-opus-4-8",
    "specialist": "claude-sonnet-5",
    "extractor": "claude-haiku-4-5",
    "frontier": "claude-fable-5",
}

# Coordinator review prompts start with this exact prefix — the stub (and test
# doubles) key on it to return well-formed verdicts for review calls only.
REVIEW_PROMPT_PREFIX = "Review a specialist's output against its acceptance criterion."

# Rough $/1M-token prices for spend attribution (input, output).
# Sonnet 5 sticker is $3/$15 (intro $2/$10 through 2026-08-31) — track sticker.
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5": (10.00, 50.00),
}


class ModelResponse(BaseModel):
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    stop_reason: str = "end_turn"

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ModelClient(Protocol):
    def complete(
        self, tier: str, system: str, prompt: str, tools: list[str] | None = None
    ) -> ModelResponse: ...


class StubModelClient:
    """Deterministic offline client. Charges fake spend so budget/trace paths
    are exercised end-to-end without an API key."""

    def __init__(self, routing: dict[str, str] | None = None) -> None:
        self.routing = routing or dict(DEFAULT_ROUTING)
        self.calls: list[tuple[str, str]] = []  # (tier, prompt) for assertions
        self.tool_requests: list[tuple[str, list[str]]] = []  # (tier, tools)

    def complete(
        self, tier: str, system: str, prompt: str, tools: list[str] | None = None
    ) -> ModelResponse:
        self.calls.append((tier, prompt))
        if tools:
            self.tool_requests.append((tier, list(tools)))
        model = self.routing.get(tier, self.routing["specialist"])
        via = f" tools={','.join(tools)}" if tools else ""
        if prompt.startswith(REVIEW_PROMPT_PREFIX):
            # Review prompts get a well-formed verdict so the real parse path
            # (not the unparsed fallback) is exercised offline.
            text = "VERDICT: pass — [stub review] output conforms to the criterion."
        else:
            text = f"[stub:{tier}{via}] response to: {prompt[:120]}"
        input_tokens = max(1, len(system) // 4 + len(prompt) // 4)
        output_tokens = max(1, len(text) // 4)
        return ModelResponse(
            text=text,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usd=_estimate_usd(model, input_tokens, output_tokens),
        )


class AnthropicModelClient:
    """Real client over the official Anthropic SDK. Adaptive thinking on;
    effort left at default. Server-side tools (web search) are passed through
    the registry; pause_turn is continued automatically up to a guard limit.
    Kept minimal on purpose — harden per-call options (streaming for long
    outputs, retries) as real workloads land."""

    def __init__(
        self,
        routing: dict[str, str] | None = None,
        max_tokens: int = 16000,
        max_continuations: int = 5,
    ) -> None:
        import anthropic  # optional dependency: pip install hive[llm]

        self._client = anthropic.Anthropic()
        self.routing = routing or dict(DEFAULT_ROUTING)
        self.max_tokens = max_tokens
        self.max_continuations = max_continuations

    def complete(
        self, tier: str, system: str, prompt: str, tools: list[str] | None = None
    ) -> ModelResponse:
        from hive.actions.registry import anthropic_tool_defs

        model = self.routing.get(tier, self.routing["specialist"])
        tool_defs = anthropic_tool_defs(tools or [])
        messages: list[dict] = [{"role": "user", "content": prompt}]
        kwargs: dict = dict(
            model=model, max_tokens=self.max_tokens, system=system,
            thinking={"type": "adaptive"},
        )
        if tool_defs:
            kwargs["tools"] = tool_defs

        input_tokens = output_tokens = 0
        response = None
        for _ in range(self.max_continuations + 1):
            response = self._client.messages.create(messages=messages, **kwargs)
            input_tokens += response.usage.input_tokens
            output_tokens += response.usage.output_tokens
            if response.stop_reason != "pause_turn":
                break
            # Server-side tool loop hit its iteration limit — re-send the turn
            # as-is and the server resumes where it left off.
            messages = [{"role": "user", "content": prompt},
                        {"role": "assistant", "content": response.content}]

        text = "".join(b.text for b in response.content if b.type == "text")
        return ModelResponse(
            text=text,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usd=_estimate_usd(model, input_tokens, output_tokens),
            stop_reason=response.stop_reason or "end_turn",
        )


class OllamaModelClient:
    """Free local models via Ollama (http://localhost:11434). $0 per call.

    All tiers route to one local model by default — a 7B is nowhere near
    Opus/Sonnet quality, so this is the dev/free tier: real generated output
    for testing workflows and the UI without spending a rupee. Server-side
    tools (web_search) are Anthropic-only and are skipped here with a note in
    the prompt, so agents know they're working from model knowledge.
    Uses stdlib urllib on purpose — no extra dependency for a local call.
    """

    def __init__(
        self,
        model: str = "mistral:7b-instruct-q4_K_M",
        base_url: str = "http://localhost:11434",
        routing: dict[str, str] | None = None,
        timeout: float = 300.0,
    ) -> None:
        # Same routing keys as the other clients; every tier maps to the local
        # model unless overridden (e.g. a bigger local model for "planner").
        self.routing = routing or {tier: model for tier in DEFAULT_ROUTING}
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def complete(
        self, tier: str, system: str, prompt: str, tools: list[str] | None = None
    ) -> ModelResponse:
        import json
        import urllib.request

        model = self.routing.get(tier, next(iter(self.routing.values())))
        if tools:
            # No server-side tools locally — tell the model instead of faking it.
            prompt += (
                "\n\n(Note: live web search is unavailable in this environment. "
                "Answer from your knowledge and clearly mark claims you cannot verify.)"
            )
        payload = json.dumps({
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/chat", data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as raw:
            body = json.loads(raw.read().decode("utf-8"))
        return ModelResponse(
            text=body.get("message", {}).get("content", ""),
            model=f"ollama/{model}",
            input_tokens=body.get("prompt_eval_count", 0),
            output_tokens=body.get("eval_count", 0),
            usd=0.0,  # local inference is free
        )


def _estimate_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    in_price, out_price = _PRICING.get(model, (5.00, 25.00))
    return input_tokens / 1_000_000 * in_price + output_tokens / 1_000_000 * out_price
