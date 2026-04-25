# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Mesh-creation tools.

The single tool here, ``mesh.add_primitive``, wraps the
``bpy.ops.mesh.primitive_*`` family with a typed JSON schema so the
model can pick a primitive without us exposing raw operator ids.
"""

from __future__ import annotations

from .. import harness
from . import _common


# Map of primitive name → bpy.ops.mesh function name. Names match Blender's
# Add menu vocabulary so the model can use familiar terms.
_PRIMITIVES = {
    "CUBE": "primitive_cube_add",
    "PLANE": "primitive_plane_add",
    "UV_SPHERE": "primitive_uv_sphere_add",
    "ICO_SPHERE": "primitive_ico_sphere_add",
    "CYLINDER": "primitive_cylinder_add",
    "CONE": "primitive_cone_add",
    "TORUS": "primitive_torus_add",
    "MONKEY": "primitive_monkey_add",
    "GRID": "primitive_grid_add",
    "CIRCLE": "primitive_circle_add",
}


_ADD_PRIMITIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "description": "Primitive to create.",
            "enum": list(_PRIMITIVES.keys()),
        },
        "location": {
            "type": "array",
            "description": "World-space location [x, y, z]. Defaults to the origin.",
            "items": {"type": "number"},
            "minItems": 3,
            "maxItems": 3,
        },
        "size": {
            "type": "number",
            "description": "Size hint passed to the operator (e.g. cube edge length).",
        },
        "name": {
            "type": "string",
            "description": "Optional name to assign to the new object.",
        },
    },
    "required": ["type"],
    "additionalProperties": False,
}


def _add_primitive(args: dict) -> str:
    import bpy

    raw_type = args.get("type")
    if not raw_type:
        raise ValueError("'type' is required")
    op_name = _PRIMITIVES.get(str(raw_type).upper())
    if op_name is None:
        raise ValueError(
            "Unknown primitive {!r}. Allowed: {}".format(
                raw_type, sorted(_PRIMITIVES.keys()),
            )
        )

    location = _common.vec3(args.get("location"), default=(0.0, 0.0, 0.0))
    op_kwargs: dict = {"location": location}

    size = args.get("size")
    if size is not None:
        op_kwargs["size"] = float(size)

    op = getattr(bpy.ops.mesh, op_name)
    op(**op_kwargs)

    new_obj = bpy.context.view_layer.objects.active
    rename = args.get("name")
    if rename and new_obj is not None:
        new_obj.name = str(rename)

    return _common.to_json({
        "primitive": str(raw_type).upper(),
        "object": new_obj.name if new_obj is not None else None,
        "location": list(location),
    })


def register(registry) -> None:
    registry.register(harness.ToolSpec(
        name="mesh.add_primitive",
        description=(
            "Add a primitive mesh (cube, sphere, cylinder, …) at the given "
            "location. The new object is selected and made active. Reversible "
            "via the regular Blender undo stack."
        ),
        permission="write",
        parameters=_ADD_PRIMITIVE_SCHEMA,
        run=_add_primitive,
    ))
