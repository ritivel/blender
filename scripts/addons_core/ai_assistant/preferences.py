# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Add-on preferences for the AI Assistant.

Holds provider configuration (shared across scenes) and the global tool
permission mode used by the agent loop. Per-scene chat state lives in
:mod:`properties` instead.
"""

import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty
from bpy.types import AddonPreferences

from . import permissions


PROVIDERS = (
    ("echo", "Echo (offline)", "Local stub provider; streams a canned reply with a configuration hint"),
    ("anthropic", "Anthropic", "Claude models via api.anthropic.com"),
    ("openai", "OpenAI", "GPT models via api.openai.com"),
    ("custom", "OpenAI-compatible", "Self-hosted or third-party OpenAI-compatible endpoint"),
)


PERMISSION_MODES = (
    (
        "ask",
        "Ask each time",
        "Show the modal permission popup before every write/exec tool call",
    ),
    (
        "session",
        "Allow for session",
        "Show the popup once per tool; remember the answer until Blender exits",
    ),
    (
        "always",
        "Always allow",
        "Run every tool the model requests without prompting (development only)",
    ),
    (
        "deny",
        "Deny all",
        "Block every tool call. The model is told tools are unavailable",
    ),
)


DEFAULT_SYSTEM_PROMPT = (
    "You are an AI assistant embedded inside Blender, a 3D creation suite. "
    "You help the user model, animate, render, and script. Prefer concise "
    "answers. You have access to scene introspection tools "
    "(scene.list_objects, scene.get_object), mutation tools "
    "(mesh.add_primitive, transform.translate/rotate/scale, scene.select), "
    "and gated escape hatches (bpy.run_operator, python.eval_in_sandbox, "
    "viewport.screenshot). Prefer the structured tools over raw operator "
    "or Python eval; reach for those only when the structured surface is "
    "insufficient. Always describe the change you intend to make before "
    "calling a mutating tool."
)


class AIAssistantPreferences(AddonPreferences):
    bl_idname = __package__

    provider: EnumProperty(
        name="Provider",
        items=PROVIDERS,
        default="echo",
    )
    model: StringProperty(
        name="Model",
        description="Model identifier passed to the provider",
        default="claude-sonnet-4-6",
    )
    base_url: StringProperty(
        name="Base URL",
        description="Override the provider base URL (used by the 'OpenAI-compatible' provider)",
        default="",
    )
    api_key_env: StringProperty(
        name="API Key Env Var",
        description=(
            "Environment variable that holds the API key. The key is read from the "
            "environment at request time and never stored in the .blend file."
        ),
        default="ANTHROPIC_API_KEY",
    )
    system_prompt: StringProperty(
        name="System Prompt",
        description="Prepended to every conversation",
        default=DEFAULT_SYSTEM_PROMPT,
    )
    max_tokens: IntProperty(
        name="Max Tokens",
        description="Hard cap on response length",
        default=4096,
        min=64,
        soft_max=32768,
    )
    permission_mode: EnumProperty(
        name="Tool Permissions",
        items=PERMISSION_MODES,
        default="ask",
    )

    def draw(self, context):
        layout = self.layout

        col = layout.column(align=True)
        col.prop(self, "provider")
        col.prop(self, "model")
        if self.provider == "custom":
            col.prop(self, "base_url")
        col.prop(self, "api_key_env")
        col.prop(self, "max_tokens")

        layout.separator()
        layout.label(text="System prompt:")
        layout.prop(self, "system_prompt", text="")

        layout.separator()
        layout.prop(self, "permission_mode")

        # Per-call popup decisions accumulate here until the user
        # exits Blender or actively revokes them. Render the list so
        # the user can see what they have trusted.
        session_trusted = permissions.session_trusted_tools()
        if session_trusted:
            layout.separator()
            box = layout.box()
            box.label(text="Tools trusted for this session:", icon="UNLOCKED")
            for name in session_trusted:
                row = box.row(align=True)
                row.label(text=name, icon="TOOL_SETTINGS")
                op = row.operator(
                    "ai_assistant.revoke_trust", text="", icon="X",
                )
                op.scope = "session"
                op.tool_name = name

        layout.separator()
        box = layout.box()
        box.label(text="API key resolution order:", icon="INFO")
        box.label(text="  1. Environment variable named above")
        box.label(text="  2. $XDG_CONFIG_HOME/blender/ai_assistant/<env-var>")
        box.label(text="See doc/guides/ai_assistant_plan.md for the full plan")


def get_prefs(context=None):
    """Return the add-on preferences, or ``None`` if the add-on is disabled."""
    ctx = context or bpy.context
    addon = ctx.preferences.addons.get(__package__)
    return addon.preferences if addon else None


_classes = (AIAssistantPreferences,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
