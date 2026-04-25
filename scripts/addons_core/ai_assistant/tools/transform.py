# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Object-level translate / rotate / scale tools.

These edit ``object.location`` / ``object.rotation_euler`` /
``object.scale`` directly rather than going through ``bpy.ops.transform.*``
so they work without an active 3D-viewport context. The active object
is used when ``name`` is not specified.
"""

from __future__ import annotations

import math

from .. import harness
from . import _common


def _resolve_object(args: dict):
    import bpy
    name = args.get("name")
    if name:
        return _common.get_object(str(name))
    obj = bpy.context.view_layer.objects.active
    if obj is None:
        raise ValueError("No active object; pass 'name' to target a specific object")
    return obj


_VECTOR_SCHEMA = {
    "type": "array",
    "description": "[x, y, z] vector.",
    "items": {"type": "number"},
    "minItems": 3,
    "maxItems": 3,
}


_TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Target object name (default: active)."},
        "vector": dict(_VECTOR_SCHEMA, description="Translation delta [x, y, z]."),
        "absolute": {
            "type": "boolean",
            "description": (
                "If true, set location to 'vector' instead of adding to it. "
                "Defaults to false (delta translation)."
            ),
        },
    },
    "required": ["vector"],
    "additionalProperties": False,
}


_ROTATE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Target object name (default: active)."},
        "euler": dict(_VECTOR_SCHEMA, description="Euler XYZ rotation [rx, ry, rz]."),
        "unit": {
            "type": "string",
            "enum": ["radians", "degrees"],
            "description": "Unit of 'euler'. Defaults to radians.",
        },
        "absolute": {
            "type": "boolean",
            "description": (
                "If true, set rotation_euler to 'euler' instead of adding to it. "
                "Defaults to false (delta rotation)."
            ),
        },
    },
    "required": ["euler"],
    "additionalProperties": False,
}


_SCALE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Target object name (default: active)."},
        "factor": dict(_VECTOR_SCHEMA, description="Per-axis scale factor [sx, sy, sz]."),
        "uniform": {
            "type": "number",
            "description": "Convenience: apply the same scale on all axes. Overrides 'factor'.",
        },
        "absolute": {
            "type": "boolean",
            "description": (
                "If true, set scale to 'factor' instead of multiplying. "
                "Defaults to false (multiplicative scale)."
            ),
        },
    },
    "additionalProperties": False,
}


def _translate(args: dict) -> str:
    obj = _resolve_object(args)
    vec = _common.vec3(args.get("vector"))
    if args.get("absolute"):
        obj.location = vec
    else:
        obj.location = (
            float(obj.location[0]) + vec[0],
            float(obj.location[1]) + vec[1],
            float(obj.location[2]) + vec[2],
        )
    return _common.to_json({"object": obj.name, "location": list(obj.location)})


def _rotate(args: dict) -> str:
    obj = _resolve_object(args)
    euler = _common.vec3(args.get("euler"))
    if args.get("unit", "radians") == "degrees":
        euler = tuple(math.radians(c) for c in euler)
    if args.get("absolute"):
        obj.rotation_euler = euler
    else:
        obj.rotation_euler = (
            float(obj.rotation_euler[0]) + euler[0],
            float(obj.rotation_euler[1]) + euler[1],
            float(obj.rotation_euler[2]) + euler[2],
        )
    return _common.to_json({
        "object": obj.name,
        "rotation_euler": list(obj.rotation_euler),
    })


def _scale(args: dict) -> str:
    obj = _resolve_object(args)
    uniform = args.get("uniform")
    if uniform is not None:
        u = float(uniform)
        factor = (u, u, u)
    else:
        factor = _common.vec3(args.get("factor"), default=(1.0, 1.0, 1.0))
    if args.get("absolute"):
        obj.scale = factor
    else:
        obj.scale = (
            float(obj.scale[0]) * factor[0],
            float(obj.scale[1]) * factor[1],
            float(obj.scale[2]) * factor[2],
        )
    return _common.to_json({"object": obj.name, "scale": list(obj.scale)})


def register(registry) -> None:
    registry.register(harness.ToolSpec(
        name="transform.translate",
        description=(
            "Translate an object by a [x, y, z] delta (default) or set its "
            "absolute location when 'absolute' is true."
        ),
        permission="write",
        parameters=_TRANSLATE_SCHEMA,
        run=_translate,
    ))
    registry.register(harness.ToolSpec(
        name="transform.rotate",
        description=(
            "Rotate an object by Euler XYZ angles. Unit is radians unless "
            "'unit' is set to 'degrees'. Set 'absolute' to overwrite the "
            "existing rotation rather than adding to it."
        ),
        permission="write",
        parameters=_ROTATE_SCHEMA,
        run=_rotate,
    ))
    registry.register(harness.ToolSpec(
        name="transform.scale",
        description=(
            "Multiply an object's scale by a per-axis factor. Pass 'uniform' "
            "for a single-axis shortcut. Set 'absolute' to overwrite."
        ),
        permission="write",
        parameters=_SCALE_SCHEMA,
        run=_scale,
    ))
