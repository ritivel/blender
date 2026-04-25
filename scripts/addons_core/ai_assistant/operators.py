# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Operators backing the AI Assistant chat UI.

Step 1 only ships synchronous operators that run against the offline
:class:`harness.EchoProvider`. Step 2 swaps the synchronous call for a
modal operator that polls a worker thread.
"""

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from . import harness
from .preferences import get_prefs


def _append_message(session, role: str, content: str) -> None:
    msg = session.messages.add()
    msg.role = role
    msg.content = content
    session.active_message_index = len(session.messages) - 1


def _build_history(session, system_prompt: str) -> list[harness.Message]:
    history: list[harness.Message] = []
    if system_prompt:
        history.append(harness.Message(role="system", content=system_prompt))
    for m in session.messages:
        history.append(harness.Message(role=m.role, content=m.content))
    return history


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
        session = context.scene.ai_assistant
        prefs = get_prefs(context)
        system_prompt = prefs.system_prompt if prefs else ""

        user_text = session.draft.strip()
        session.draft = ""
        _append_message(session, "user", user_text)

        session.busy = True
        try:
            provider = harness.make_provider(prefs)
            tools = harness.default_registry().all()
            history = _build_history(session, system_prompt)
            result = provider.respond(history, tools)
            for m in result.messages:
                _append_message(session, m.role, m.content)
        finally:
            session.busy = False

        return {"FINISHED"}


class AI_ASSISTANT_OT_clear(Operator):
    """Clear the chat history"""
    bl_idname = "ai_assistant.clear"
    bl_label = "Clear"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        session = getattr(context.scene, "ai_assistant", None)
        return session is not None and len(session.messages) > 0

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
    AI_ASSISTANT_OT_clear,
    AI_ASSISTANT_OT_set_draft,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
