# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Render the active viewport so the model can "see" the scene.

The tool wraps ``bpy.ops.render.opengl`` (a fast OpenGL viewport render)
and writes the result to a path under the user's temp directory. The
returned JSON includes the absolute path so the user (or a follow-up
tool) can attach the image to the conversation.

Step 3 only ships the path-returning version. Direct image attachment to
provider requests is a step 6 problem (multi-modal MCP bridge).
"""

from __future__ import annotations

import os
import tempfile
import time

from .. import harness
from . import _common


_SCREENSHOT_SCHEMA = {
    "type": "object",
    "properties": {
        "resolution_x": {
            "type": "integer",
            "description": "Optional override for the render resolution X.",
            "minimum": 16,
            "maximum": 8192,
        },
        "resolution_y": {
            "type": "integer",
            "description": "Optional override for the render resolution Y.",
            "minimum": 16,
            "maximum": 8192,
        },
    },
    "additionalProperties": False,
}


def _default_path() -> str:
    """Return a unique screenshot path under the system temp directory.

    The path is hard-coded to ``tempfile.gettempdir()`` so the model
    cannot direct writes outside that sandboxed directory; this keeps
    the tool safe to mark as ``read`` for permission purposes.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return os.path.join(
        tempfile.gettempdir(),
        "blender_ai_assistant_screenshot_{}.png".format(stamp),
    )


def _screenshot(args: dict) -> str:
    import bpy

    out_path = _default_path()

    scene = bpy.context.scene
    render = scene.render

    saved = {
        "filepath": render.filepath,
        "file_format": render.image_settings.file_format,
        "resolution_x": render.resolution_x,
        "resolution_y": render.resolution_y,
    }

    res_x = args.get("resolution_x")
    res_y = args.get("resolution_y")
    try:
        render.image_settings.file_format = "PNG"
        render.filepath = out_path
        if res_x is not None:
            render.resolution_x = int(res_x)
        if res_y is not None:
            render.resolution_y = int(res_y)
        bpy.ops.render.opengl(write_still=True)
    finally:
        render.filepath = saved["filepath"]
        render.image_settings.file_format = saved["file_format"]
        render.resolution_x = saved["resolution_x"]
        render.resolution_y = saved["resolution_y"]

    size = None
    try:
        size = os.path.getsize(out_path)
    except OSError:
        pass

    return _common.to_json({
        "path": out_path,
        "size_bytes": size,
        "resolution": [render.resolution_x, render.resolution_y],
    })


def register(registry) -> None:
    registry.register(harness.ToolSpec(
        name="viewport.screenshot",
        description=(
            "Render the active 3D viewport via OpenGL and write a PNG to "
            "disk. Returns the absolute path. Use this when you need to "
            "describe what the user is looking at; the image itself can be "
            "attached to the next user message."
        ),
        permission="read",
        parameters=_SCREENSHOT_SCHEMA,
        run=_screenshot,
    ))
