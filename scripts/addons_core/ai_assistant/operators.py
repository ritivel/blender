# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Operators backing the AI Assistant chat UI.

Step 3 promotes the previously single-shot send into a full agent loop:

1. The send operator builds the initial conversation, picks an
   in-memory placeholder assistant message, and starts a worker thread
   that streams from the provider.
2. The worker thread *only* speaks HTTP — it never touches ``bpy``
   data. Text deltas and tool-call requests are pushed onto a queue.
3. A timer callback (:func:`_drain_tick`) drains the queue on the main
   thread, appends text deltas to the chat, and accumulates pending
   tool calls.
4. When the worker emits a ``finish_reason``:
   * ``stop`` — turn is complete; tear down state.
   * ``tool_use`` — execute each pending tool call on the main thread,
     append a tool log entry to the chat, append the tool result to the
     in-memory history, and spawn a fresh worker for the next round.
   A hard cap (:data:`_MAX_AGENT_STEPS`) bounds the loop so a model
   that never produces a final answer cannot wedge the UI.

Tool execution runs on the main thread so it can safely touch
``bpy.data`` and ``bpy.context``. The cancel event can interrupt the
worker between chunks and also short-circuits the tool loop.
"""

from __future__ import annotations

import json
import queue
import threading
import weakref

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from . import harness
from .preferences import get_prefs


_TICK_INTERVAL = 0.05

# Hard cap on agent loop iterations within a single user turn. Mirrors
# the "max-steps" knob in Claude Code / Codex CLI: at the limit we stop
# even if the model is still asking for more tools, so a buggy or
# adversarial conversation cannot run unbounded.
_MAX_AGENT_STEPS = 12

# Cap on how much of a tool result we render into the visible chat log.
_TOOL_LOG_PREVIEW = 240


class _RequestState:
    __slots__ = (
        "thread", "queue", "cancel", "scene_ref", "msg_index",
        "history", "tools", "provider", "step", "pending_tool_calls",
        "finish_reason",
    )

    def __init__(self, thread, q, cancel, scene, msg_index, history, tools, provider):
        self.thread = thread
        self.queue = q
        self.cancel = cancel
        self.scene_ref = weakref.ref(scene)
        self.msg_index = msg_index
        self.history: list[harness.Message] = history
        self.tools: list[harness.ToolSpec] = tools
        self.provider = provider
        self.step = 0
        self.pending_tool_calls: list[harness.ToolCall] = []
        self.finish_reason: str | None = None


_state: _RequestState | None = None


def _redraw_view3d():
    wm = bpy.context.window_manager
    if wm is None:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _append_message(session, role: str, content: str) -> int:
    msg = session.messages.add()
    msg.role = role
    msg.content = content
    session.active_message_index = len(session.messages) - 1
    return session.active_message_index


def _append_history_message(
    history: list[harness.Message],
    role: str,
    content: str,
) -> None:
    if history and history[-1].role == role and not history[-1].tool_calls:
        history[-1].content += "\n\n" + content
    else:
        history.append(harness.Message(role=role, content=content))


def _build_history(session, system_prompt: str) -> list[harness.Message]:
    """Reconstruct provider history from persistent chat session.

    ``tool`` and ``tool_call`` messages are skipped. They are present for
    the user's benefit but are scoped to the turn they originated in;
    there is no portable way to round-trip Anthropic ``tool_use`` ids
    across users turns when only flat text is persisted, so we drop them
    and let the model re-introspect via ``scene.list_objects`` if it
    needs prior context.
    """
    system_messages: list[harness.Message] = []
    turns: list[harness.Message] = []
    if system_prompt:
        _append_history_message(system_messages, "system", system_prompt)
    for m in session.messages:
        if not m.content or not m.content.strip():
            continue
        if m.role == "system":
            _append_history_message(system_messages, "system", m.content)
        elif m.role in {"user", "assistant"}:
            _append_history_message(turns, m.role, m.content)
    return system_messages + turns


def _worker(provider, history, tools, q: queue.Queue, cancel: threading.Event):
    try:
        for chunk in provider.stream(history, tools):
            if cancel.is_set():
                break
            q.put(chunk)
    except Exception as err:  # noqa: BLE001 — worker thread, surface as chunk
        q.put(harness.StreamChunk(error="Provider crashed: {}".format(err)))
    finally:
        q.put(None)  # Sentinel: stream finished.


def _summarise_args(arguments: dict) -> str:
    try:
        text = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = repr(arguments)
    if len(text) > _TOOL_LOG_PREVIEW:
        text = text[: _TOOL_LOG_PREVIEW - 1] + "…"
    return text


def _summarise_result(content: str) -> str:
    text = content or ""
    if len(text) > _TOOL_LOG_PREVIEW:
        text = text[: _TOOL_LOG_PREVIEW - 1] + "…"
    return text


def _execute_tool(tool: harness.ToolSpec, arguments: dict) -> harness.ToolResult:
    try:
        result_text = tool.run(arguments or {})
    except Exception as err:  # noqa: BLE001 — surface to the model
        return harness.ToolResult(
            call_id="",
            content="error: {}".format(err),
            is_error=True,
        )
    if not isinstance(result_text, str):
        result_text = json.dumps(result_text, default=str)
    return harness.ToolResult(call_id="", content=result_text, is_error=False)


def _run_pending_tools(state: _RequestState, session) -> None:
    """Execute pending tool calls and update history + chat session."""
    tool_results: list[harness.ToolResult] = []
    by_name = {t.name: t for t in state.tools}

    for call in state.pending_tool_calls:
        if state.cancel.is_set():
            break
        tool = by_name.get(call.name)
        if tool is None:
            log = "[tool] {}({}) → unknown or denied".format(
                call.name, _summarise_args(call.arguments),
            )
            _append_message(session, "tool", log)
            tool_results.append(harness.ToolResult(
                call_id=call.id,
                content="Unknown or denied tool: {}".format(call.name),
                is_error=True,
            ))
            continue

        result = _execute_tool(tool, call.arguments)
        result.call_id = call.id
        log = "[tool] {}({}) → {}".format(
            call.name, _summarise_args(call.arguments), _summarise_result(result.content),
        )
        _append_message(session, "tool", log)
        tool_results.append(result)

    # Record the assistant turn (with tool calls) and the tool results in
    # the in-memory history so the next provider call sees them.
    placeholder_idx = state.msg_index
    if 0 <= placeholder_idx < len(session.messages):
        assistant_text = session.messages[placeholder_idx].content
    else:
        assistant_text = ""
    state.history.append(harness.Message(
        role="assistant",
        content=assistant_text,
        tool_calls=list(state.pending_tool_calls),
    ))
    state.history.append(harness.Message(
        role="tool",
        tool_results=tool_results,
    ))
    state.pending_tool_calls = []


def _start_next_step(state: _RequestState, session) -> bool:
    """Spawn the next worker thread for this turn. Returns False at hard-stop."""
    state.step += 1
    if state.step > _MAX_AGENT_STEPS:
        if 0 <= state.msg_index < len(session.messages):
            existing = session.messages[state.msg_index].content
            sep = "\n\n" if existing else ""
            session.messages[state.msg_index].content = (
                existing + sep + "[hard-stop] Agent reached max steps; stopping."
            )
        return False

    # New placeholder for the next assistant turn so the user sees a
    # natural progression in the chat list.
    state.msg_index = _append_message(session, "assistant", "")

    state.queue = queue.Queue()
    state.cancel.clear()
    state.finish_reason = None
    state.thread = threading.Thread(
        target=_worker,
        args=(state.provider, state.history, state.tools, state.queue, state.cancel),
        daemon=True,
        name="ai_assistant.worker",
    )
    state.thread.start()
    return True


def _finalise(state: _RequestState, session, error: str | None) -> None:
    """Tear down the request and clear UI busy state."""
    if error and 0 <= state.msg_index < len(session.messages):
        existing = session.messages[state.msg_index].content
        sep = "\n\n" if existing else ""
        session.messages[state.msg_index].content = existing + sep + "[error] " + error
    if (
        not error
        and 0 <= state.msg_index < len(session.messages)
        and not session.messages[state.msg_index].content.strip()
    ):
        session.messages.remove(state.msg_index)
    session.busy = False


def _drain_tick():
    global _state
    state = _state
    if state is None:
        return None  # Unregister timer.

    scene = state.scene_ref()
    if scene is None:
        state.cancel.set()
        _state = None
        return None

    try:
        session = scene.ai_assistant
    except (AttributeError, ReferenceError):
        state.cancel.set()
        _state = None
        return None

    finished = False
    error: str | None = None
    while True:
        try:
            chunk = state.queue.get_nowait()
        except queue.Empty:
            break
        if chunk is None:
            finished = True
            break
        if chunk.error:
            error = chunk.error
            finished = True
            break
        if chunk.delta_text and 0 <= state.msg_index < len(session.messages):
            session.messages[state.msg_index].content += chunk.delta_text
        if chunk.tool_call is not None:
            state.pending_tool_calls.append(chunk.tool_call)
        if chunk.finish_reason:
            state.finish_reason = chunk.finish_reason

    _redraw_view3d()

    if not finished:
        return _TICK_INTERVAL

    # Stream ended for this provider call.
    if error:
        _finalise(state, session, error)
        _state = None
        return None

    if state.finish_reason == "tool_use" and state.pending_tool_calls and not state.cancel.is_set():
        _run_pending_tools(state, session)
        if not _start_next_step(state, session):
            _finalise(state, session, None)
            _state = None
            return None
        return _TICK_INTERVAL

    # Drop empty placeholder produced by, for instance, a tool-only turn
    # whose follow-up never arrived.
    _finalise(state, session, None)
    _state = None
    return None


def _select_tools(prefs) -> list[harness.ToolSpec]:
    mode = getattr(prefs, "permission_mode", "ask") if prefs else "ask"
    allowed = harness.allowed_permissions(mode)
    if not allowed:
        return []
    return harness.default_registry().filter_by_permission(allowed)


class AI_ASSISTANT_OT_send(Operator):
    """Send the current draft message to the AI assistant"""
    bl_idname = "ai_assistant.send"
    bl_label = "Send"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        session = getattr(context.scene, "ai_assistant", None)
        return session is not None and not session.busy and bool(session.draft.strip())

    def execute(self, context):
        global _state
        if _state is not None:
            self.report({"ERROR"}, "A request is already in flight.")
            return {"CANCELLED"}

        session = context.scene.ai_assistant
        prefs = get_prefs(context)
        system_prompt = prefs.system_prompt if prefs else ""

        user_text = session.draft.strip()
        session.draft = ""
        _append_message(session, "user", user_text)

        history = _build_history(session, system_prompt)

        # Append empty assistant placeholder for streaming output.
        msg_index = _append_message(session, "assistant", "")
        session.busy = True

        provider = harness.make_provider(prefs)
        tools = _select_tools(prefs)

        q: queue.Queue = queue.Queue()
        cancel = threading.Event()
        thread = threading.Thread(
            target=_worker,
            args=(provider, history, tools, q, cancel),
            daemon=True,
            name="ai_assistant.worker",
        )
        _state = _RequestState(
            thread=thread, q=q, cancel=cancel, scene=context.scene,
            msg_index=msg_index, history=history, tools=tools, provider=provider,
        )
        _state.step = 1
        thread.start()

        if not bpy.app.timers.is_registered(_drain_tick):
            bpy.app.timers.register(_drain_tick, first_interval=_TICK_INTERVAL)

        return {"FINISHED"}


class AI_ASSISTANT_OT_stop(Operator):
    """Cancel the in-flight assistant request"""
    bl_idname = "ai_assistant.stop"
    bl_label = "Stop"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        session = getattr(context.scene, "ai_assistant", None)
        return session is not None and session.busy

    def execute(self, context):
        if _state is not None:
            _state.cancel.set()
        # Don't clear _state here: the worker will push its sentinel and
        # the timer will tear down cleanly on the next tick.
        return {"FINISHED"}


class AI_ASSISTANT_OT_clear(Operator):
    """Clear the chat history"""
    bl_idname = "ai_assistant.clear"
    bl_label = "Clear"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        session = getattr(context.scene, "ai_assistant", None)
        return session is not None and len(session.messages) > 0 and not session.busy

    def execute(self, context):
        session = context.scene.ai_assistant
        session.messages.clear()
        session.active_message_index = 0
        return {"FINISHED"}


class AI_ASSISTANT_OT_set_draft(Operator):
    """Set the chat draft programmatically (used by quick-prompt buttons)"""
    bl_idname = "ai_assistant.set_draft"
    bl_label = "Set Draft"
    bl_options = {"INTERNAL"}

    text: StringProperty(default="")

    def execute(self, context):
        context.scene.ai_assistant.draft = self.text
        return {"FINISHED"}


_classes = (
    AI_ASSISTANT_OT_send,
    AI_ASSISTANT_OT_stop,
    AI_ASSISTANT_OT_clear,
    AI_ASSISTANT_OT_set_draft,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    global _state
    if _state is not None:
        _state.cancel.set()
        _state = None
    if bpy.app.timers.is_registered(_drain_tick):
        bpy.app.timers.unregister(_drain_tick)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
