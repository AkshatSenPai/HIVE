"""Action layer — the tool registry (PRD §4, least privilege per §11.3).

P0 scope: **server-side tools only** — capabilities Anthropic's infrastructure
executes during a model call (web search). They need no client-side execution
loop, which keeps the P0 agent body simple. `vault_read` is not a model tool:
HIVE reads the notes a step lists and puts them in the prompt itself
(memory/vault_context.py), on every backend. Email send runs at the approval
gate; a browser agent lands later with a proper tool-use loop.

Tool names are HIVE-internal. Adapters grant them per agent in tools.yaml;
the Agent base enforces that an agent can only request granted tools.
"""

from __future__ import annotations

from typing import Any

# HIVE tool name -> Anthropic server-tool definition.
# web_search_20260209 runs on the routed Opus 5.5, Sonnet 5.5 and Fable 5.2 (and
# Opus 4.6+, Sonnet 4.6+) — NOT on Haiku 5.5. Grant web_search only to agents
# on the planner/specialist/frontier tiers, never the extractor tier.
# max_uses bounds fan-out cost per call (PRD §10 discipline).
ANTHROPIC_SERVER_TOOLS: dict[str, dict[str, Any]] = {
    "web_search": {"type": "web_search_20260209", "name": "web_search", "max_uses": 8},
}


class ToolNotPermitted(Exception):
    """An agent requested a tool outside its least-privilege grant."""

    def __init__(self, agent: str, tools: list[str]) -> None:
        self.agent, self.tools = agent, tools
        super().__init__(f"agent '{agent}' is not granted tools: {tools}")


def anthropic_tool_defs(names: list[str]) -> list[dict[str, Any]]:
    """Map granted HIVE tool names to API tool definitions.

    Names without a server-side spec are skipped. vault_read is one: HIVE
    serves it as prompt context, so it never reaches the API as a tool.
    """
    return [dict(spec) for name in names if (spec := ANTHROPIC_SERVER_TOOLS.get(name))]
