# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

bl_info = {
    "name": "AI Assistant",
    "author": "Blender Foundation",
    "version": (0, 3, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > AI",
    "description": (
        "Conversational AI assistant for Blender with streaming providers "
        "(Anthropic, OpenAI, OpenAI-compatible) and a typed tool harness "
        "for scene introspection, mesh creation, transforms, and gated "
        "operator / Python-eval execution."
    ),
    "warning": "Experimental. Tool execution is gated by the global permission mode in preferences.",
    "doc_url": "{BLENDER_MANUAL_URL}/addons/system/ai_assistant.html",
    "support": "OFFICIAL",
    "category": "System",
}


if "bpy" in locals():
    import importlib

    for _mod_name in ("harness", "providers", "tools", "properties", "preferences", "operators", "ui"):
        if _mod_name in locals():
            importlib.reload(locals()[_mod_name])


import bpy

from . import harness
from . import providers  # noqa: F401 — registers provider implementations on import
from . import tools  # noqa: F401 — registered lazily by harness.default_registry()
from . import properties
from . import preferences
from . import operators
from . import ui


def register():
    properties.register()
    preferences.register()
    operators.register()
    ui.register()
    # Reset the cached default registry so reloads pick up new tool defs.
    harness.reset_default_registry()


def unregister():
    ui.unregister()
    operators.unregister()
    preferences.unregister()
    properties.unregister()
    harness.reset_default_registry()
