# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Per-scene chat session storage for the AI Assistant add-on.

Step 1 of the plan only needs three things:

* A list of chat messages (role + text).
* The current draft message the user is typing.
* A "busy" flag so the UI can show feedback while the harness is thinking.

Provider configuration lives in :mod:`preferences`, not here, because it is
shared across scenes and persists in user preferences.
"""

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup


CHAT_ROLES = (
    ("user", "User", "Message authored by the human"),
    ("assistant", "Assistant", "Message authored by the AI"),
    ("system", "System", "System or tool message"),
)


class AIAssistantMessage(PropertyGroup):
    role: EnumProperty(
        name="Role",
        items=CHAT_ROLES,
        default="user",
    )
    content: StringProperty(
        name="Content",
        default="",
    )


class AIAssistantSession(PropertyGroup):
    messages: CollectionProperty(type=AIAssistantMessage)
    draft: StringProperty(
        name="Message",
        description="Text to send to the AI assistant",
        default="",
    )
    busy: BoolProperty(
        name="Busy",
        description="True while the harness is processing a turn",
        default=False,
    )
    active_message_index: IntProperty(default=0)


_classes = (
    AIAssistantMessage,
    AIAssistantSession,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ai_assistant = PointerProperty(type=AIAssistantSession)


def unregister():
    if hasattr(bpy.types.Scene, "ai_assistant"):
        del bpy.types.Scene.ai_assistant
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
