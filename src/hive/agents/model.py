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

# Routing tiers -> model IDs (owner's routing decision 2026-07-06; moved to the
# current Claude generation 2026-11-14):
#   planner    -> Opus   (coordinator judgment)
#   specialist -> Sonnet (research + maker: the doing tier)
#   extractor  -> Haiku  (quick replies, triage, extraction/formatting)
#   frontier   -> Fable  (big projects only; must be proposed to the owner via
#                         an approval card before any job runs on this tier)
DEFAULT_ROUTING: dict[str, str] = {
    "planner": "claude-opus-5-5",
    "specialist": "claude-sonnet-5-5",
    "extractor": "claude-haiku-5-5",
    "frontier": "claude-fable-5-2",
}

# Effort per tier, sent explicitly as output_config.effort. These equal each
# model's API default (Opus 5.5 and Haiku 5.5 default to "medium"; Sonnet 5.5
# and Fable 5.2 to "high"), so behaviour matches "effort left at default" —
# written out so it's visible and tunable. Tune per tier with `hive eval` once
# real models run; effort levels don't map 1:1 across model generations.
DEFAULT_EFFORT: dict[str, str] = {
    "planner": "medium",
    "specialist": "high",
    "extractor": "medium",
    "frontier": "high",
}

# Coordinator review prompts start with this exact prefix — the stub (and test
# doubles) key on it to return well-formed verdicts for review calls only.
REVIEW_PROMPT_PREFIX = "Review a specialist's output against its acceptance criterion."

# Rough $/1M-token prices for spend attribution (input, output), first-party API
# rates as of 2026-11. The routed models come first; older IDs stay so spend is
# still attributed if a routing override or a future fallback serves them.
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-2": (8.00, 40.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5": (10.00, 50.00),
}

# Models whose price steps up once a single request's prompt passes a size:
# model -> (prompt-token threshold, input $/1M, output $/1M) above it.
_LONG_PROMPT_PRICING: dict[str, tuple[int, float, float]] = {
    "claude-haiku-5-5": (100_000, 0.50, 2.50),
}


class ModelRefusal(Exception):
    """The model declined the request (stop_reason "refusal").

    Raised instead of returning, so a declined or partial answer can never be
    passed downstream as if it were real output. The agent chokepoint charges
    `response` (tokens the call consumed) to the budget, then the coordinator
    escalates to the owner — stop-and-ask, never a silent retry or guess.
    """

    def __init__(self, model: str, category: str | None, explanation: str | None,
                 response: "ModelResponse") -> None:
        self.model, self.category, self.explanation = model, category, explanation
        self.response = response
        super().__init__(f"{model} declined the request (category: {category or 'unspecified'})")


class ModelUnavailable(Exception):
    """The model backend couldn't serve the call at all: the `claude` CLI is
    missing or signed out, a plan usage limit was hit, the call timed out, or
    it returned an error. Nothing usable came back, so the coordinator
    escalates to the owner instead of guessing or retrying elsewhere."""


class ModelResponse(BaseModel):
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    stop_reason: str = "end_turn"
    # Server-side web searches that actually ran during the call. Backends
    # that can't search (stub, Ollama) leave it at 0, so a brief can say
    # honestly whether anything was looked up.
    web_searches: int = 0

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
    """Real client over the official Anthropic SDK. Adaptive thinking on (the
    current models can't disable it); effort set per tier (DEFAULT_EFFORT).
    Server-side tools (web search) are passed through the registry; pause_turn
    is continued automatically up to a guard limit. A refusal raises
    ModelRefusal before any content is read. Kept minimal on purpose — harden
    per-call options (streaming for long outputs, retries) as real workloads
    land."""

    def __init__(
        self,
        routing: dict[str, str] | None = None,
        effort: dict[str, str] | None = None,
        max_tokens: int = 16000,
        max_continuations: int = 5,
    ) -> None:
        import anthropic  # optional dependency: pip install hive[llm]

        self._client = anthropic.Anthropic()
        self.routing = routing or dict(DEFAULT_ROUTING)
        self.effort = effort or dict(DEFAULT_EFFORT)
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
        if effort := self.effort.get(tier):
            kwargs["output_config"] = {"effort": effort}
        if tool_defs:
            kwargs["tools"] = tool_defs

        input_tokens = output_tokens = web_searches = 0
        usd = 0.0
        response = None
        for _ in range(self.max_continuations + 1):
            response = self._client.messages.create(messages=messages, **kwargs)
            input_tokens += response.usage.input_tokens
            output_tokens += response.usage.output_tokens
            server_tools = getattr(response.usage, "server_tool_use", None)
            web_searches += int(getattr(server_tools, "web_search_requests", 0) or 0)
            # Price each request on its own: some rates depend on prompt size.
            usd += _estimate_usd(model, response.usage.input_tokens,
                                 response.usage.output_tokens)
            if response.stop_reason != "pause_turn":
                break
            # Server-side tool loop hit its iteration limit — re-send the turn
            # as-is and the server resumes where it left off.
            messages = [{"role": "user", "content": prompt},
                        {"role": "assistant", "content": response.content}]

        stop_reason = response.stop_reason or "end_turn"
        if stop_reason == "refusal":
            # Content is empty (declined up front) or partial (declined
            # mid-answer) — never read it as output. stop_details can be None.
            details = getattr(response, "stop_details", None)
            raise ModelRefusal(
                model,
                category=getattr(details, "category", None),
                explanation=getattr(details, "explanation", None),
                response=ModelResponse(
                    text="", model=model, input_tokens=input_tokens,
                    output_tokens=output_tokens, usd=usd, stop_reason=stop_reason,
                    web_searches=web_searches,
                ),
            )

        text = "".join(b.text for b in response.content if b.type == "text")
        return ModelResponse(
            text=text,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usd=usd,
            stop_reason=stop_reason,
            web_searches=web_searches,
        )


def _claude_code_usage(data: dict) -> tuple[int, int, int]:
    """(input tokens, output tokens, web searches) for one headless call.

    Claude Code runs each WebSearch as its own request on a small model
    (Haiku 5.5, seen 2026-11-17). Those tokens and the search count appear
    only in the per-model `modelUsage` totals; the top-level `usage` covers
    the main model alone and reports 0 searches. So sum `modelUsage` when
    present. Cache writes and reads count as input."""
    per_model = data.get("modelUsage")
    if isinstance(per_model, dict) and per_model:
        rows = [row for row in per_model.values() if isinstance(row, dict)]
        total = lambda *keys: sum(int(row.get(k) or 0) for row in rows for k in keys)
        return (total("inputTokens", "cacheCreationInputTokens", "cacheReadInputTokens"),
                total("outputTokens"), total("webSearchRequests"))
    usage = data.get("usage") or {}
    input_tokens = sum(int(usage.get(k) or 0) for k in (
        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    searches = int((usage.get("server_tool_use") or {}).get("web_search_requests") or 0)
    return input_tokens, int(usage.get("output_tokens") or 0), searches


# HIVE tool name -> Claude Code built-in tool. Only tools listed here can ever
# be switched on for a headless call; everything else stays off (PRD §11.3).
# vault_read has no Claude Code equivalent yet, so granting it enables nothing.
CLAUDE_CODE_TOOLS: dict[str, str] = {"web_search": "WebSearch"}

# Environment variables that would make the `claude` CLI bill an API key
# instead of the signed-in plan. Removed from the child process so this
# backend can only ever run on the plan.
_API_KEY_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


class ClaudeCodeModelClient:
    """Runs each model call through the local `claude` CLI (Claude Code) in
    headless mode, so HIVE uses the owner's Claude plan instead of a paid API
    key. Same routing and effort tables as AnthropicModelClient, so switching
    to the API later is one setting (HIVE_MODEL_BACKEND=anthropic).

    Isolation — the call must not pick up the owner's own Claude Code setup:
      * --safe-mode: no CLAUDE.md, skills, plugins, hooks, MCP servers or custom
        agents (sign-in, model choice and built-in tools still work);
        --strict-mcp-config and --disable-slash-commands on top of that.
      * --tools: only the Claude Code tools mapped from the agent's granted
        HIVE tools (web_search -> WebSearch); "" means no tools at all.
      * --system-prompt replaces Claude Code's own system prompt with HIVE's.
      * runs in a fresh empty temp folder, with --no-session-persistence;
      * API-key variables are removed, so usage always counts against the plan.

    Spend: `usd` is the API-equivalent cost Claude Code reports for the call
    (total_cost_usd), so HIVE's caps keep the same meaning on both backends.
    """

    def __init__(
        self,
        routing: dict[str, str] | None = None,
        effort: dict[str, str] | None = None,
        claude_bin: str = "claude",
        timeout: float = 600.0,
        run=None,
        which=None,
    ) -> None:
        import shutil
        import subprocess

        self.routing = routing or dict(DEFAULT_ROUTING)
        self.effort = effort or dict(DEFAULT_EFFORT)
        self.claude_bin = claude_bin
        self.timeout = timeout
        self._run = run or subprocess.run       # injectable for tests
        self._which = which or shutil.which

    def command(self, model: str, effort: str | None, system: str,
                tools: list[str]) -> list[str]:
        """The exact argv for one headless call (the prompt goes in on stdin)."""
        enabled = [CLAUDE_CODE_TOOLS[t] for t in tools if t in CLAUDE_CODE_TOOLS]
        argv = [
            self.claude_bin, "-p",
            "--output-format", "json",
            "--model", model,
            "--system-prompt", system,
            "--safe-mode", "--strict-mcp-config", "--disable-slash-commands",
            "--no-session-persistence",
            "--tools", ",".join(enabled),
        ]
        if enabled:
            argv += ["--allowedTools", ",".join(enabled)]
        if effort:
            argv += ["--effort", effort]
        return argv

    def complete(
        self, tier: str, system: str, prompt: str, tools: list[str] | None = None
    ) -> ModelResponse:
        import json
        import os
        import subprocess
        import tempfile

        model = self.routing.get(tier, self.routing["specialist"])
        binary = self._which(self.claude_bin)
        if binary is None:
            raise ModelUnavailable(
                f"the `{self.claude_bin}` CLI was not found on PATH "
                "(install Claude Code, or set HIVE_CLAUDE_BIN)")
        argv = self.command(model, self.effort.get(tier), system, tools or [])
        argv[0] = binary
        env = {k: v for k, v in os.environ.items() if k not in _API_KEY_ENV}

        with tempfile.TemporaryDirectory(prefix="hive-claude-") as workdir:
            try:
                proc = self._run(
                    argv, input=prompt, capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    timeout=self.timeout, cwd=workdir, env=env,
                )
            except subprocess.TimeoutExpired:
                raise ModelUnavailable(
                    f"{model} via Claude Code timed out after {self.timeout:.0f}s") from None
            except OSError as exc:
                raise ModelUnavailable(f"could not start the Claude Code CLI: {exc}") from exc

        try:
            data = json.loads(proc.stdout or "")
        except json.JSONDecodeError:
            detail = (proc.stderr or proc.stdout or "no output").strip()[:300]
            raise ModelUnavailable(
                f"Claude Code returned no result (exit {proc.returncode}): {detail}") from None
        if not isinstance(data, dict):
            raise ModelUnavailable(f"Claude Code returned unexpected output: {str(data)[:300]}")

        input_tokens, output_tokens, web_searches = _claude_code_usage(data)
        cost = data.get("total_cost_usd")
        usd = float(cost) if cost is not None else _estimate_usd(model, input_tokens, output_tokens)
        served = f"claude-code/{model}"

        if data.get("stop_reason") == "refusal":
            details = data.get("stop_details") or {}
            raise ModelRefusal(
                model, category=details.get("category"), explanation=details.get("explanation"),
                response=ModelResponse(text="", model=served, input_tokens=input_tokens,
                                       output_tokens=output_tokens, usd=usd,
                                       stop_reason="refusal", web_searches=web_searches),
            )
        if data.get("is_error") or proc.returncode != 0 or data.get("subtype") not in (None, "success"):
            detail = str(data.get("result") or data.get("subtype") or proc.stderr or "error").strip()[:300]
            raise ModelUnavailable(f"{model} via Claude Code failed: {detail}")

        return ModelResponse(
            text=str(data.get("result") or ""),
            model=served,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usd=usd,
            stop_reason=str(data.get("stop_reason") or "end_turn"),
            web_searches=web_searches,
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
    """Estimated $ for ONE request (some rates step up with that request's
    prompt size). Unknown models are priced at Opus rates — over, not under."""
    in_price, out_price = _PRICING.get(model, (5.00, 25.00))
    long_prompt = _LONG_PROMPT_PRICING.get(model)
    if long_prompt and input_tokens > long_prompt[0]:
        in_price, out_price = long_prompt[1], long_prompt[2]
    return input_tokens / 1_000_000 * in_price + output_tokens / 1_000_000 * out_price
