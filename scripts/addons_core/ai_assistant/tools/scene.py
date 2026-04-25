# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Read-only scene introspection tools.

These never mutate the .blend file and are always available regardless
of permission mode (assuming tools are enabled at all). They cover the
common questions a chat agent needs to ground its answers: "what
objects are in the scene?", "what is the active object?", and
"what does this object look like in detail?".
"""

from __future__ import annotations

from .. import harness
from . import _common


_LIST_OBJECTS_SCHEMA = {
    "type": "object",
    "properties": {
        "type_filter": {
            "type": "string",
            "description": (
                "Optional Blender object type to filter on, e.g. 'MESH', "
                "'LIGHT', 'CAMERA'. If omitted all objects are returned."
            ),
        },
        "limit": {
            "type": "integer",
            "description": "Cap the number of objects returned (default 200).",
            "minimum": 1,
            "maximum": 5000,
        },
    },
    "additionalProperties": False,
}


_GET_OBJECT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Object datablock name.",
        },
    },
    "required": ["name"],
    "additionalProperties": False,
}


_SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Object to select. Pass an empty string to deselect all.",
        },
        "extend": {
            "type": "boolean",
            "description": (
                "If true, add the object to the current selection instead of "
                "replacing it. Defaults to false."
            ),
        },
        "active": {
            "type": "boolean",
            "description": "If true, also make the object the active object.",
        },
    },
    "required": ["name"],
    "additionalProperties": False,
}


def _list_objects(args: dict) -> str:
    scene = _common.get_scene()
    type_filter = args.get("type_filter")
    if type_filter is not None:
        type_filter = str(type_filter).upper()
    limit = int(args.get("limit") or 200)

    rows: list[dict] = []
    for obj in scene.objects:
        if type_filter and obj.type != type_filter:
            continue
        rows.append({
            "name": obj.name,
            "type": obj.type,
            "hidden": bool(getattr(obj, "hide_viewport", False)),
            "selected": bool(getattr(obj, "select_get", lambda: False)()),
        })
        if len(rows) >= limit:
            break

    return _common.to_json({
        "scene": scene.name,
        "count": len(rows),
        "objects": rows,
    })


def _get_object(args: dict) -> str:
    name = args.get("name")
    if not name:
        raise ValueError("'name' is required")
    obj = _common.get_object(name)

    location = tuple(float(c) for c in obj.location)
    rotation = tuple(float(c) for c in obj.rotation_euler)
    scale = tuple(float(c) for c in obj.scale)
    dimensions = tuple(float(c) for c in obj.dimensions)

    payload = {
        "name": obj.name,
        "type": obj.type,
        "location": list(location),
        "rotation_euler": list(rotation),
        "scale": list(scale),
        "dimensions": list(dimensions),
        "data": obj.data.name if obj.data is not None else None,
        "parent": obj.parent.name if obj.parent is not None else None,
        "hidden": bool(getattr(obj, "hide_viewport", False)),
    }
    if obj.type == "MESH" and obj.data is not None:
        mesh = obj.data
        payload["mesh"] = {
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "polygons": len(mesh.polygons),
            "materials": [m.name for m in mesh.materials if m is not None],
        }
    return _common.to_json(payload)


def _select(args: dict) -> str:
    import bpy

    name = args.get("name", "")
    extend = bool(args.get("extend", False))
    make_active = bool(args.get("active", True))

    if not extend:
        for obj in bpy.context.view_layer.objects:
            obj.select_set(False)

    if not name:
        bpy.context.view_layer.objects.active = None
        return _common.to_json({"selected": [], "active": None})

    obj = _common.get_object(name)
    obj.select_set(True)
    if make_active:
        bpy.context.view_layer.objects.active = obj

    selected = [o.name for o in bpy.context.view_layer.objects if o.select_get()]
    active = bpy.context.view_layer.objects.active
    return _common.to_json({
        "selected": selected,
        "active": active.name if active is not None else None,
    })


def register(registry) -> None:
    registry.register(harness.ToolSpec(
        name="scene.list_objects",
        description=(
            "List objects in the current scene with their type and selection "
            "state. Use this before mutating tools to ground the model in the "
            "scene's actual contents."
        ),
        permission="read",
        parameters=_LIST_OBJECTS_SCHEMA,
        run=_list_objects,
    ))
    registry.register(harness.ToolSpec(
        name="scene.get_object",
        description=(
            "Return detailed metadata for a single object: transform, parent, "
            "data block, and (for meshes) primitive counts."
        ),
        permission="read",
        parameters=_GET_OBJECT_SCHEMA,
        run=_get_object,
    ))
    registry.register(harness.ToolSpec(
        name="scene.select",
        description=(
            "Select an object by name. Set 'name' to an empty string to "
            "deselect all. Pass extend=true to keep the existing selection."
        ),
        permission="write",
        parameters=_SELECT_SCHEMA,
        run=_select,
    ))
