# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Sidebar UI for the AI Assistant.

A single N-panel under the "AI" tab in the 3D Viewport. Step 7 of the plan
promotes this into a dedicated editor space; until then the sidebar is
the only surface so the diff stays small.
"""

import bpy
from bpy.types import Panel, UIList


_ROLE_ICONS = {
    "user": "USER",
    "assistant": "OUTLINER_OB_LIGHT",
    "system": "SETTINGS",
    "tool": "TOOL_SETTINGS",
}


class AI_ASSISTANT_UL_messages(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        icon_id = _ROLE_ICONS.get(item.role, "DOT")
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row()
            row.label(text=item.role.title(), icon=icon_id)
            preview = item.content.strip().splitlines()[0] if item.content.strip() else "(empty)"
            row.label(text=preview[:80])
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(text="", icon=icon_id)


class VIEW3D_PT_ai_assistant(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI"
    bl_label = "AI Assistant"

    def draw(self, context):
        layout = self.layout
        session = context.scene.ai_assistant

        layout.template_list(
            "AI_ASSISTANT_UL_messages",
            "",
            session,
            "messages",
            session,
            "active_message_index",
            rows=6,
        )

        if 0 <= session.active_message_index < len(session.messages):
            active = session.messages[session.active_message_index]
            box = layout.box()
            box.label(text="{}:".format(active.role.title()), icon=_ROLE_ICONS.get(active.role, "DOT"))
            for line in active.content.splitlines() or [""]:
                box.label(text=line)

        layout.separator()
        layout.label(text="Message:")
        layout.prop(session, "draft", text="")

        row = layout.row(align=True)
        if session.busy:
            row.operator("ai_assistant.stop", icon="CANCEL")
        else:
            row.operator("ai_assistant.send", icon="PLAY")
        row.operator("ai_assistant.clear", icon="X")

        if session.busy:
            layout.label(text="Streaming…", icon="SORTTIME")


class VIEW3D_PT_ai_assistant_quickprompts(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI"
    bl_label = "Quick Prompts"
    bl_parent_id = "VIEW3D_PT_ai_assistant"
    bl_options = {"DEFAULT_CLOSED"}

    _PROMPTS = (
        ("Describe the current scene", "Describe the current scene briefly."),
        ("Explain selected object", "Explain what the selected object is and how it is configured."),
        ("Suggest cleanups", "Suggest cleanups for unused materials, orphan data, and naming issues."),
    )

    def draw(self, context):
        layout = self.layout
        for label, prompt in self._PROMPTS:
            op = layout.operator("ai_assistant.set_draft", text=label, icon="GREASEPENCIL")
            op.text = prompt


_classes = (
    AI_ASSISTANT_UL_messages,
    VIEW3D_PT_ai_assistant,
    VIEW3D_PT_ai_assistant_quickprompts,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
