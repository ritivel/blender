# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Add-on preferences for the AI Assistant.

Provider configuration is intentionally kept lightweight in step 1: the
real network clients land in step 2. The fields defined here cover the
shape we need so users can configure the add-on once and not have to
revisit preferences when later steps activate the real providers.
"""

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import AddonPreferences


PROVIDERS = (
    ("echo", "Echo (offline)", "Local stub provider used by step 1; replies with a canned message"),
    ("anthropic", "Anthropic", "Claude models via api.anthropic.com (requires step 2)"),
    ("openai", "OpenAI", "GPT models via api.openai.com (requires step 2)"),
    ("custom", "OpenAI-compatible", "Self-hosted or third-party OpenAI-compatible endpoint (requires step 2)"),
)


PERMISSION_MODES = (
    ("ask", "Ask each time", "Prompt for confirmation before every tool call"),
    ("session", "Allow for session", "Confirm once per tool, then trust for the rest of the session"),
    ("always", "Always allow", "Run tools without prompting (development only)"),
    ("deny", "Deny all", "Disable tool execution entirely"),
)


DEFAULT_SYSTEM_PROMPT = (
    "You are an AI assistant embedded inside Blender, a 3D creation suite. "
    "You help the user model, animate, render, and script. Prefer concise "
    "answers. When making scene changes, describe the change first; tool "
    "execution is gated by the user's permission settings."
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

        layout.separator()
        layout.label(text="System prompt:")
        layout.prop(self, "system_prompt", text="")

        layout.separator()
        layout.prop(self, "permission_mode")

        layout.separator()
        box = layout.box()
        box.label(text="Step 1 scaffold — provider clients land in step 2", icon="INFO")
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
