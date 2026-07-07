"""Action layer — the tool registry (PRD §4, least privilege per §11.3).

P0 scope: **server-side tools only** — capabilities Anthropic's infrastructure
executes during a model call (web search). They need no client-side execution
loop, which keeps the P0 agent body simple. Client-side tools (vault read,
email send, browser agent) land here later with a proper tool-use loop.

Tool names are HIVE-internal. Adapters grant them per agent in tools.yaml;
the Agent base enforces that an agent can only request granted tools.
"""

from __future__ import annotations

from typing import Any

# HIVE tool name -> Anthropic server-tool definition.
# web_search_20260209 runs on Opus 4.8/4.7/4.6, Sonnet 5, Sonnet 4.6 — NOT on
# Haiku. Grant web_search only to agents on the planner/specialist tiers.
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

    Names without a server-side spec (e.g. vault_read, which is a future
    client-side tool) are skipped — granting them is harmless today.
    """
    return [dict(spec) for name in names if (spec := ANTHROPIC_SERVER_TOOLS.get(name))]
