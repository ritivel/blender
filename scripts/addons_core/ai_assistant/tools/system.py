# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Gated escape hatches: raw operator dispatch and Python eval.

Both tools live under the ``exec`` permission class. They unlock most of
Blender's surface area for the agent, but they are also the most
dangerous, so they are subject to:

* An *allowlist* of operator namespaces (``bpy.run_operator``) — so an
  ill-advised model cannot, say, save over arbitrary files via
  ``wm.save_as_mainfile``.
* A restricted ``eval`` namespace (``python.eval_in_sandbox``) — no file
  I/O, no ``import``, no ``__builtins__`` — that lets the model compute
  values from ``bpy.data`` without becoming a remote code-execution
  back door.

The full permission UX (modal popups, per-call confirmation) lands in
step 4. Until then ``operators.py`` filters these out unless the user
has set the global permission mode to "Always allow".
"""

from __future__ import annotations

from collections.abc import Mapping

from .. import harness
from . import _common


# Operator namespaces the model may call via ``bpy.run_operator``. Picked
# to cover the day-to-day modelling / animation surface without exposing
# file I/O, preferences mutation, or addon installation.
_OPERATOR_ALLOWLIST: tuple[str, ...] = (
    "mesh",
    "object",
    "transform",
    "anim",
    "curve",
    "material",
    "node",
    "render",
    "view3d",
    "screen",
    "uv",
    "sculpt",
    "paint",
    "armature",
    "pose",
    "collection",
)


_RUN_OPERATOR_SCHEMA = {
    "type": "object",
    "properties": {
        "operator": {
            "type": "string",
            "description": (
                "Operator id in the form 'module.name', e.g. "
                "'object.shade_smooth'. Allowed modules: " + ", ".join(_OPERATOR_ALLOWLIST)
            ),
        },
        "kwargs": {
            "type": "object",
            "description": "Keyword arguments forwarded to the operator.",
        },
    },
    "required": ["operator"],
    "additionalProperties": False,
}


_EVAL_SCHEMA = {
    "type": "object",
    "properties": {
        "expression": {
            "type": "string",
            "description": (
                "Single Python expression evaluated in a restricted namespace "
                "with read-only access to bpy. No imports, no builtins, no "
                "I/O. Use this for derived values (counts, sums, …) the "
                "other tools do not expose."
            ),
        },
    },
    "required": ["expression"],
    "additionalProperties": False,
}


def _run_operator(args: dict) -> str:
    import bpy

    op_id = args.get("operator")
    if not op_id or "." not in op_id:
        raise ValueError("'operator' must be a 'module.name' string")
    module, _, name = op_id.partition(".")
    if module not in _OPERATOR_ALLOWLIST:
        raise ValueError(
            "Operator namespace {!r} is not on the allowlist {}".format(
                module, _OPERATOR_ALLOWLIST,
            )
        )
    op_module = getattr(bpy.ops, module, None)
    if op_module is None:
        raise ValueError("Unknown operator module {!r}".format(module))
    op = getattr(op_module, name, None)
    if op is None:
        raise ValueError("Unknown operator {!r}".format(op_id))

    raw_kwargs = args.get("kwargs") or {}
    if not isinstance(raw_kwargs, dict):
        raise ValueError("'kwargs' must be an object")

    result = op(**raw_kwargs)
    # Operator results in Blender are sets of result strings, e.g. {'FINISHED'}.
    if isinstance(result, set):
        result_list = sorted(result)
    else:
        result_list = [str(result)]
    return _common.to_json({"operator": op_id, "result": result_list})


# Curated read-only subset of the bpy API exposed to the sandbox.
# ``bpy.data`` and ``bpy.context`` are useful for "count meshes", "sum
# polygons", "describe selection", etc. We deliberately omit ``bpy.ops``
# (use ``bpy.run_operator`` with allowlist) and ``bpy.app`` (handlers,
# timers — too easy to wedge Blender from a one-liner). Values reachable
# through ``bpy.data`` and ``bpy.context`` are wrapped so expressions can
# traverse attributes, indexes, and collections without calling live Blender
# methods such as ``remove()``, ``new()``, ``select_set()``, or file writers.
def _build_sandbox_globals() -> dict:
    import bpy

    safe_builtins = {
        "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
        "enumerate": enumerate, "filter": filter, "float": float,
        "int": int, "len": len, "list": list, "map": map, "max": max,
        "min": min, "range": range, "round": round, "set": set,
        "sorted": sorted, "str": str, "sum": sum, "tuple": tuple,
        "zip": zip,
    }
    # ``__builtins__`` must be a *dict* — Python's eval treats a module
    # there as the "trusted" case and exposes the full builtins.
    return {
        "__builtins__": safe_builtins,
        "bpy": _SafeBpy(bpy),
    }


class _SafeBpy:
    """Minimal proxy exposing read-only ``data`` and ``context`` of ``bpy``."""

    __slots__ = ("_bpy",)

    def __init__(self, bpy):
        self._bpy = bpy

    @property
    def data(self):
        return _sandbox_readonly(self._bpy.data)

    @property
    def context(self):
        return _sandbox_readonly(self._bpy.context)

    def __repr__(self) -> str:
        return "<SafeBpy data+context>"


_IMMUTABLE_SANDBOX_TYPES = (type(None), bool, int, float, str, bytes)


def _sandbox_readonly(value):
    if isinstance(value, _IMMUTABLE_SANDBOX_TYPES):
        return value
    if isinstance(value, _ReadOnlyBpyProxy):
        return value
    return _ReadOnlyBpyProxy(value)


class _BlockedBpyCallable:
    """Placeholder for methods that would escape read-only sandbox access."""

    __slots__ = ("_name",)

    def __init__(self, name: str):
        self._name = name

    def __call__(self, *_args, **_kwargs):
        raise TypeError(
            "Calling bpy methods is not permitted in python.eval_in_sandbox "
            "({!r} is read-only).".format(self._name)
        )

    def __repr__(self) -> str:
        return "<blocked bpy method {!r}>".format(self._name)


class _ReadOnlyBpyProxy:
    """Read-only view over bpy objects, collections, and scalar containers."""

    __slots__ = ("_target",)

    def __init__(self, target):
        object.__setattr__(self, "_target", target)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        value = getattr(self._target, name)
        if callable(value):
            return _BlockedBpyCallable(name)
        return _sandbox_readonly(value)

    def __getitem__(self, key):
        return _sandbox_readonly(self._target[key])

    def __iter__(self):
        return (_sandbox_readonly(item) for item in self._target)

    def __len__(self) -> int:
        return len(self._target)

    def __bool__(self) -> bool:
        return bool(self._target)

    def __contains__(self, item) -> bool:
        if isinstance(item, _ReadOnlyBpyProxy):
            item = item._target
        return item in self._target

    def __setattr__(self, name: str, value) -> None:
        raise TypeError("bpy objects exposed to python.eval_in_sandbox are read-only")

    def __delattr__(self, name: str) -> None:
        raise TypeError("bpy objects exposed to python.eval_in_sandbox are read-only")

    def keys(self):
        if isinstance(self._target, Mapping):
            return tuple(_sandbox_readonly(item) for item in self._target.keys())
        keys = getattr(self._target, "keys", None)
        if not callable(keys):
            raise TypeError("Wrapped bpy object does not provide keys()")
        return tuple(_sandbox_readonly(item) for item in keys())

    def values(self):
        if isinstance(self._target, Mapping):
            return tuple(_sandbox_readonly(item) for item in self._target.values())
        values = getattr(self._target, "values", None)
        if not callable(values):
            raise TypeError("Wrapped bpy object does not provide values()")
        return tuple(_sandbox_readonly(item) for item in values())

    def items(self):
        if isinstance(self._target, Mapping):
            return tuple(
                (_sandbox_readonly(key), _sandbox_readonly(value))
                for key, value in self._target.items()
            )
        items = getattr(self._target, "items", None)
        if not callable(items):
            raise TypeError("Wrapped bpy object does not provide items()")
        return tuple(
            (_sandbox_readonly(key), _sandbox_readonly(value))
            for key, value in items()
        )

    def get(self, key, default=None):
        if isinstance(self._target, Mapping):
            return _sandbox_readonly(self._target.get(key, default))
        get = getattr(self._target, "get", None)
        if not callable(get):
            raise TypeError("Wrapped bpy object does not provide get()")
        return _sandbox_readonly(get(key, default))

    def __repr__(self) -> str:
        return "<read-only bpy value {!r}>".format(self._target)


def _coerce_for_json(value):
    """Best-effort conversion of arbitrary eval results into JSON-friendly types."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, _ReadOnlyBpyProxy):
        target = value._target
        if isinstance(target, Mapping):
            return {str(k): _coerce_for_json(v) for k, v in target.items()}
        if isinstance(target, (list, tuple)):
            return [_coerce_for_json(v) for v in target]
        if isinstance(target, set):
            return sorted([_coerce_for_json(v) for v in target], key=repr)
        return repr(target)
    if isinstance(value, (list, tuple)):
        return [_coerce_for_json(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _coerce_for_json(v) for k, v in value.items()}
    if isinstance(value, set):
        return sorted([_coerce_for_json(v) for v in value], key=repr)
    return repr(value)


def _eval_in_sandbox(args: dict) -> str:
    expression = args.get("expression")
    if not expression or not isinstance(expression, str):
        raise ValueError("'expression' must be a non-empty string")
    if "\n" in expression or ";" in expression:
        raise ValueError(
            "Expression must be a single statement; use multiple tool calls "
            "instead of compound expressions."
        )
    if "__" in expression:
        raise ValueError("Dunder access is not permitted in the sandbox.")
    if "import" in expression.split():
        raise ValueError("'import' is not permitted in the sandbox.")

    globs = _build_sandbox_globals()
    try:
        result = eval(expression, globs, {})  # noqa: S307 — sandboxed namespace
    except Exception as err:  # noqa: BLE001 — surface to the model
        raise ValueError("eval failed: {}".format(err)) from err

    return _common.to_json({
        "expression": expression,
        "result": _coerce_for_json(result),
    })


def register(registry) -> None:
    registry.register(harness.ToolSpec(
        name="bpy.run_operator",
        description=(
            "Run a bpy.ops operator with keyword arguments. The operator "
            "namespace is restricted to a fixed allowlist of editing-related "
            "modules; file, preferences, and add-on operators are not "
            "callable from this tool."
        ),
        permission="exec",
        parameters=_RUN_OPERATOR_SCHEMA,
        run=_run_operator,
    ))
    registry.register(harness.ToolSpec(
        name="python.eval_in_sandbox",
        description=(
            "Evaluate a single Python expression in a restricted namespace. "
            "Has read access to bpy.data and bpy.context plus a small set of "
            "safe builtins. Use this for derived numbers (counts, sums, "
            "averages) that the structured tools do not return directly."
        ),
        permission="exec",
        parameters=_EVAL_SCHEMA,
        run=_eval_in_sandbox,
    ))


__all__ = ("register", "_OPERATOR_ALLOWLIST")
