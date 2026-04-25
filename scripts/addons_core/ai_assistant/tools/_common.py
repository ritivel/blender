# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared helpers for the bundled AI-assistant tools.

Each tool runs on the main Blender thread (the agent loop is structured
to dispatch tool execution from the timer callback rather than the
worker thread), so it is safe to import ``bpy`` inside the tool body.
``bpy`` is intentionally imported lazily here so unit tests can exercise
the registry shape without a Blender host.
"""

from __future__ import annotations

import json


def _bpy():
    import bpy  # local import — keeps unit tests importable without bpy.
    return bpy


def get_scene():
    bpy = _bpy()
    return bpy.context.scene


def get_object(name: str):
    bpy = _bpy()
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError("No object named {!r}".format(name))
    return obj


def vec3(value, default=(0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    """Coerce model-supplied input into a 3-tuple of floats.

    Accepts ``[x, y, z]`` or ``{"x": ..., "y": ..., "z": ...}``. Falls
    back to ``default`` for missing components so the model is allowed to
    omit axes it does not care about.
    """
    if value is None:
        return tuple(float(c) for c in default)
    if isinstance(value, dict):
        return (
            float(value.get("x", default[0])),
            float(value.get("y", default[1])),
            float(value.get("z", default[2])),
        )
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return (float(value[0]), float(value[1]), float(value[2]))
    raise ValueError("Expected a 3-vector, got {!r}".format(value))


def to_json(payload) -> str:
    """Serialise a tool result to a compact JSON string."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
