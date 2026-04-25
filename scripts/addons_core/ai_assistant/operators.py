# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Operators backing the AI Assistant chat UI.

Step 2 runs the provider on a worker thread and surfaces incremental
output to the chat panel via :func:`bpy.app.timers.register`. The worker
thread never touches ``bpy`` data; all data writes happen on the main
thread inside the timer callback.

A small global ``_state`` object holds the in-flight request. When the
add-on is unregistered or a Stop is requested, the cancel event is set;
the worker thread checks the event between chunks and exits.
"""

from __future__ import annotations

import queue
import threading
import weakref

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from . import harness
from .preferences import get_prefs


# Time between timer ticks that drain queued chunks into the chat. Tuned
# small enough that streaming feels responsive; large enough that we
# don't spam tag_redraw on idle conversations.
_TICK_INTERVAL = 0.05


class _RequestState:
    __slots__ = ("thread", "queue", "cancel", "scene_ref", "msg_index")

    def __init__(self, thread, q, cancel, scene, msg_index):
        self.thread = thread
        self.queue = q
        self.cancel = cancel
        self.scene_ref = weakref.ref(scene)
        self.msg_index = msg_index


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


def _build_history(session, system_prompt: str) -> list[harness.Message]:
    history: list[harness.Message] = []
    if system_prompt:
        history.append(harness.Message(role="system", content=system_prompt))
    for m in session.messages:
        if not m.content:
            # Skip the empty placeholder we appended for streaming.
            continue
        history.append(harness.Message(role=m.role, content=m.content))
    return history


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


def _drain_tick():
    global _state
    state = _state
    if state is None:
        return None  # Unregister timer.

    scene = state.scene_ref()
    if scene is None:
        # Scene was freed; abort.
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

    if error:
        if 0 <= state.msg_index < len(session.messages):
            existing = session.messages[state.msg_index].content
            sep = "\n\n" if existing else ""
            session.messages[state.msg_index].content = existing + sep + "[error] " + error

    _redraw_view3d()

    if finished:
        # Drop empty placeholder if model produced nothing and there was no error.
        if (
            not error
            and 0 <= state.msg_index < len(session.messages)
            and not session.messages[state.msg_index].content.strip()
        ):
            session.messages.remove(state.msg_index)
        session.busy = False
        _state = None
        return None

    return _TICK_INTERVAL


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
        tools = harness.default_registry().all()

        q: queue.Queue = queue.Queue()
        cancel = threading.Event()
        thread = threading.Thread(
            target=_worker,
            args=(provider, history, tools, q, cancel),
            daemon=True,
            name="ai_assistant.worker",
        )
        _state = _RequestState(thread, q, cancel, context.scene, msg_index)
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
