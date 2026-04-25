# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Agent harness — provider, tool, and chunk types.

The harness mediates between the Blender UI and an LLM provider. It
mirrors the shape we observed in Claude Code, OpenAI Codex CLI, and
Cursor Composer:

* A *Provider* turns a list of chat messages into a stream of
  :class:`StreamChunk` objects (text deltas plus errors / done markers).
* A *Tool* is a typed callable the model can invoke.
* The *agent loop* alternates ``provider.stream`` and ``tool.run`` until
  the model emits a final text answer or a stop condition triggers.

Step 2 wires in real streaming providers (Anthropic, OpenAI,
OpenAI-compatible). Tool execution still lands in step 3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator


@dataclass
class Message:
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str


@dataclass
class StreamChunk:
    """One incremental update from a provider."""
    delta_text: str = ""
    error: str | None = None


@dataclass
class ToolSpec:
    name: str
    description: str
    permission: str  # "read" | "write" | "exec"
    run: Callable[[dict], str]


@dataclass
class TurnResult:
    """Result of a single agent turn delivered back to the UI."""
    messages: list[Message] = field(default_factory=list)
    finished: bool = True


class Provider:
    """Provider interface.

    Real implementations override :meth:`stream`. :meth:`respond` collects
    a stream into one :class:`TurnResult` and is provided as a convenience
    for synchronous call sites.
    """

    name = ""

    def stream(
        self,
        messages: Iterable[Message],
        tools: Iterable[ToolSpec],
    ) -> Iterator[StreamChunk]:
        raise NotImplementedError

    def respond(
        self,
        messages: Iterable[Message],
        tools: Iterable[ToolSpec],
    ) -> TurnResult:
        text_parts: list[str] = []
        error: str | None = None
        for chunk in self.stream(messages, tools):
            if chunk.error:
                error = chunk.error
                break
            if chunk.delta_text:
                text_parts.append(chunk.delta_text)
        msgs: list[Message] = []
        text = "".join(text_parts)
        if text:
            msgs.append(Message(role="assistant", content=text))
        if error:
            msgs.append(Message(role="system", content="[error] " + error))
        return TurnResult(messages=msgs)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, tool: ToolSpec) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())


def _noop_run(_args: dict) -> str:
    return "ok"


_default_registry = ToolRegistry()
_default_registry.register(
    ToolSpec(
        name="noop",
        description="No-op tool used to validate the harness wiring.",
        permission="read",
        run=_noop_run,
    )
)


def default_registry() -> ToolRegistry:
    return _default_registry


def make_provider(prefs) -> Provider:
    """Construct a provider from add-on preferences.

    Lazy-imports :mod:`providers` so this module stays importable in
    environments where the providers package is not present (e.g. unit
    tests of the harness shape).
    """
    from . import providers
    return providers.build(prefs)
