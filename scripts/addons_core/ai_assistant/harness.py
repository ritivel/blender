# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Agent harness skeleton.

The harness is the part of the add-on that mediates between the Blender UI
and an LLM provider. The shape mirrors what we observed in Claude Code,
OpenAI Codex CLI, and Cursor's Composer:

* A *Provider* turns a list of chat messages into one or more model
  responses (text and/or tool calls).
* A *Tool* is a typed callable the model can invoke.
* The *agent loop* alternates ``provider.respond`` and ``tool.run`` until
  the model emits a final text answer or a stop condition triggers.

Only the offline ``EchoProvider`` and a single ``noop`` tool exist in this
step. They are enough to exercise the UI end-to-end and to lock in the
interface that real providers (step 2) and real tools (step 3) plug into.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable


@dataclass
class Message:
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str


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
    """Provider interface. Real implementations land in step 2."""

    def respond(self, messages: Iterable[Message], tools: Iterable[ToolSpec]) -> TurnResult:
        raise NotImplementedError


class EchoProvider(Provider):
    """Offline provider used to validate the UI without network access."""

    def respond(self, messages: Iterable[Message], tools: Iterable[ToolSpec]) -> TurnResult:
        last_user = next(
            (m for m in reversed(list(messages)) if m.role == "user"),
            None,
        )
        text = (
            "(echo provider — step 1 scaffold)\n"
            "I received: {!r}\n"
            "Real providers (Anthropic, OpenAI, OpenAI-compatible) are wired up in step 2."
        ).format(last_user.content if last_user else "")
        return TurnResult(messages=[Message(role="assistant", content=text)])


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

    Step 1 only knows the offline echo provider. Selecting a real provider
    in preferences still returns ``EchoProvider`` here; step 2 replaces
    this dispatch table with real HTTP clients.
    """
    return EchoProvider()
