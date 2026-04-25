# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

bl_info = {
    "name": "AI Assistant",
    "author": "Blender Foundation",
    "version": (0, 1, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > AI",
    "description": (
        "Conversational AI assistant for Blender. Step 1 scaffold: chat UI, "
        "preferences, and harness skeleton. No network calls yet."
    ),
    "warning": "Experimental scaffold. Provider clients land in step 2.",
    "doc_url": "{BLENDER_MANUAL_URL}/addons/system/ai_assistant.html",
    "support": "OFFICIAL",
    "category": "System",
}


if "bpy" in locals():
    import importlib

    for _mod_name in ("harness", "providers", "properties", "preferences", "operators", "ui"):
        if _mod_name in locals():
            importlib.reload(locals()[_mod_name])


import bpy

from . import harness
from . import providers  # noqa: F401 — registers provider implementations on import
from . import properties
from . import preferences
from . import operators
from . import ui


def register():
    properties.register()
    preferences.register()
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()
    preferences.unregister()
    properties.unregister()
