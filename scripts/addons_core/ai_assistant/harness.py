# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Agent harness — provider, tool, and chunk types.

The harness mediates between the Blender UI and an LLM provider. It
mirrors the shape we observed in Claude Code, OpenAI Codex CLI, and
Cursor Composer:

* A *Provider* turns a list of chat messages into a stream of
  :class:`StreamChunk` objects (text deltas, tool-call requests, errors,
  and stop markers).
* A *Tool* is a typed callable the model can invoke. Each tool declares
  a JSON-schema parameter description, a permission class, and a Python
  callable that produces a string result.
* The *agent loop* alternates ``provider.stream`` and ``tool.run`` until
  the model emits a final text answer (``finish_reason == "stop"``) or a
  hard-stop is reached.

Step 3 introduces the full tool plumbing: ``ToolCall``/``ToolResult``,
parameter schemas, tool-aware ``Message`` content, and provider stream
chunks that carry tool calls. The actual tools live in :mod:`tools`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator


# Permission classes mirror Claude Code's tool taxonomy:
#   "read"  — pure introspection, no side effects on the .blend file.
#   "write" — mutates scene / datablocks; reversible via undo.
#   "exec"  — arbitrary code (Python eval, raw bpy.ops). Most dangerous.
#
# Step 4 promotes these into a modal permission popup. Until then the
# agent loop in :mod:`operators` only respects ``deny`` (no tools at all)
# and ``always`` (run everything). ``ask``/``session`` modes filter the
# tool list down to ``read``-only tools so the model cannot mutate the
# scene without an explicit opt-in.
PERMISSIONS = ("read", "write", "exec")


@dataclass
class ToolCall:
    """One tool invocation requested by the model."""
    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    """Result of running a :class:`ToolCall`, sent back to the model."""
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    """A single message in the agent conversation.

    For ``role == "assistant"`` ``tool_calls`` may be populated when the
    model decided to invoke tools instead of (or in addition to) a text
    reply. For ``role == "tool"`` ``tool_results`` carries the outputs
    of those calls; the corresponding tool call ids match.
    """
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


@dataclass
class StreamChunk:
    """One incremental update from a provider.

    Exactly one of ``delta_text``, ``tool_call``, ``error``, or
    ``finish_reason`` is typically populated. ``finish_reason`` mirrors
    OpenAI's vocabulary: ``"stop"`` ends the agent loop, ``"tool_use"``
    triggers a tool execution pass, and ``"length"`` indicates the
    provider truncated the response.
    """
    delta_text: str = ""
    tool_call: ToolCall | None = None
    error: str | None = None
    finish_reason: str | None = None


@dataclass
class ToolSpec:
    """Type-safe tool description.

    ``parameters`` is a JSON-schema fragment (object type) that providers
    forward to the model so it knows how to call the tool. ``run`` is
    invoked with the parsed argument dict on the *main* thread; tools
    that touch ``bpy`` may rely on this guarantee.
    """
    name: str
    description: str
    permission: str
    parameters: dict
    run: Callable[[dict], str]

    def __post_init__(self) -> None:
        if self.permission not in PERMISSIONS:
            raise ValueError(
                "ToolSpec {!r} has unknown permission {!r} (expected one of {})".format(
                    self.name, self.permission, PERMISSIONS,
                )
            )


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
    """Ordered collection of :class:`ToolSpec` keyed by name."""

    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, tool: ToolSpec) -> None:
        if tool.name in self._tools:
            raise ValueError("Tool {!r} is already registered".format(tool.name))
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def filter_by_permission(self, allowed: Iterable[str]) -> list[ToolSpec]:
        allowed_set = set(allowed)
        return [t for t in self._tools.values() if t.permission in allowed_set]


def _noop_run(_args: dict) -> str:
    return "ok"


_NOOP = ToolSpec(
    name="noop",
    description="No-op tool used to validate the harness wiring.",
    permission="read",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    run=_noop_run,
)


def default_registry() -> ToolRegistry:
    """Return the registry used by the operator and tests.

    The first call lazily imports :mod:`tools` and registers the bundled
    tool set. Importing :mod:`tools` is deferred so this module stays
    importable in environments without ``bpy`` (e.g. unit tests of the
    harness shape).
    """
    global _default_registry
    if _default_registry is None:
        registry = ToolRegistry()
        registry.register(_NOOP)
        try:
            from . import tools as _tools_pkg
            _tools_pkg.register_default_tools(registry)
        except Exception:  # noqa: BLE001 — if tools fail to load, keep noop only
            pass
        _default_registry = registry
    return _default_registry


_default_registry: ToolRegistry | None = None


def reset_default_registry() -> None:
    """Discard the cached default registry (used by tests)."""
    global _default_registry
    _default_registry = None


def make_provider(prefs) -> Provider:
    """Construct a provider from add-on preferences.

    Lazy-imports :mod:`providers` so this module stays importable in
    environments where the providers package is not present (e.g. unit
    tests of the harness shape).
    """
    from . import providers
    return providers.build(prefs)


def allowed_permissions(permission_mode: str) -> set[str]:
    """Return the set of permission classes enabled by a UI mode.

    Used by the agent loop to filter the tool list before sending it to
    the provider. Step 4 will replace this with a modal popup that asks
    per-tool, but the same coarse mapping defines the floor: ``deny``
    blocks every tool, ``ask`` and ``session`` permit only read tools,
    and ``always`` permits everything.
    """
    if permission_mode == "deny":
        return set()
    if permission_mode in ("ask", "session"):
        return {"read"}
    return {"read", "write", "exec"}
