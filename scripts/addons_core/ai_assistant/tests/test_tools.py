# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tests for the bundled tool implementations.

Each test substitutes a minimal in-memory ``bpy`` stand-in so the tool
body executes without a Blender host. Coverage focuses on JSON-shape
contracts, mutation arithmetic, allowlist enforcement, and the lazy
loader in :func:`harness.default_registry`.
"""

import importlib
import importlib.util
import json
import math
import sys
import types
import unittest
from pathlib import Path


ADDON_DIR = Path(__file__).resolve().parents[1]
PACKAGE = "_ai_tools_test"


class _Collection:
    """Tiny stand-in for ``bpy.context.view_layer.objects``."""

    def __init__(self, items):
        self._items = list(items)
        self.active = self._items[0] if self._items else None

    def __iter__(self):
        return iter(self._items)


def _make_object(name, type_="MESH", location=(0, 0, 0), data=None):
    obj = types.SimpleNamespace(
        name=name,
        type=type_,
        location=list(location),
        rotation_euler=[0.0, 0.0, 0.0],
        scale=[1.0, 1.0, 1.0],
        dimensions=[1.0, 1.0, 1.0],
        data=data,
        parent=None,
        hide_viewport=False,
    )
    obj._selected = False
    obj.select_set = lambda v, _o=obj: setattr(_o, "_selected", bool(v))
    obj.select_get = lambda _o=obj: _o._selected
    return obj


def _install_bpy_stub(scene_objects=(), ops=None):
    """Build a minimal ``bpy`` covering the surface our tools touch."""
    bpy = types.ModuleType("bpy")
    bpy.props = types.SimpleNamespace(StringProperty=lambda **_kw: None)
    bpy.types = types.SimpleNamespace(Operator=object)
    bpy.app = types.SimpleNamespace(timers=types.SimpleNamespace(
        is_registered=lambda _cb: False,
        register=lambda _cb, first_interval=0.0: None,
        unregister=lambda _cb: None,
    ))

    scene = types.SimpleNamespace(name="Scene", objects=list(scene_objects))
    bpy.context = types.SimpleNamespace(
        scene=scene,
        view_layer=types.SimpleNamespace(objects=_Collection(scene_objects)),
    )
    bpy.data = types.SimpleNamespace(
        objects={obj.name: obj for obj in scene_objects},
    )
    bpy.ops = ops if ops is not None else types.SimpleNamespace()
    sys.modules["bpy"] = bpy
    return bpy


def _drop_modules() -> None:
    for name in list(sys.modules.keys()):
        if name == PACKAGE or name.startswith(PACKAGE + "."):
            del sys.modules[name]


def _bootstrap():
    """Reload the addon as a synthetic top-level package against the current bpy."""
    _drop_modules()

    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ADDON_DIR)]
    sys.modules[PACKAGE] = package

    spec = importlib.util.spec_from_file_location(
        PACKAGE + ".harness", ADDON_DIR / "harness.py",
    )
    harness_mod = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE + ".harness"] = harness_mod
    spec.loader.exec_module(harness_mod)

    tools_init = importlib.util.spec_from_file_location(
        PACKAGE + ".tools", ADDON_DIR / "tools" / "__init__.py",
        submodule_search_locations=[str(ADDON_DIR / "tools")],
    )
    tools_pkg = importlib.util.module_from_spec(tools_init)
    sys.modules[PACKAGE + ".tools"] = tools_pkg
    tools_init.loader.exec_module(tools_pkg)

    registry = harness_mod.ToolRegistry()
    tools_pkg.register_default_tools(registry)
    return registry, harness_mod, tools_pkg


class SceneToolsTest(unittest.TestCase):
    def test_list_objects_returns_compact_json(self):
        objs = [_make_object("Cube"), _make_object("Light", "LIGHT")]
        _install_bpy_stub(scene_objects=objs)
        registry, _h, _t = _bootstrap()

        result = json.loads(registry.get("scene.list_objects").run({}))
        self.assertEqual(result["count"], 2)
        names = {row["name"] for row in result["objects"]}
        self.assertEqual(names, {"Cube", "Light"})

    def test_list_objects_filters_by_type(self):
        objs = [_make_object("Cube"), _make_object("Light", "LIGHT")]
        _install_bpy_stub(scene_objects=objs)
        registry, _h, _t = _bootstrap()

        out = json.loads(registry.get("scene.list_objects").run({"type_filter": "MESH"}))
        self.assertEqual([r["name"] for r in out["objects"]], ["Cube"])

    def test_get_object_reports_unknown_name(self):
        _install_bpy_stub(scene_objects=[])
        registry, _h, _t = _bootstrap()

        with self.assertRaises(ValueError):
            registry.get("scene.get_object").run({"name": "Nope"})

    def test_get_object_reports_transform_and_dimensions(self):
        cube = _make_object("Cube", location=(1, 2, 3))
        cube.dimensions = [2.0, 2.0, 2.0]
        _install_bpy_stub(scene_objects=[cube])
        registry, _h, _t = _bootstrap()

        out = json.loads(registry.get("scene.get_object").run({"name": "Cube"}))
        self.assertEqual(out["location"], [1.0, 2.0, 3.0])
        self.assertEqual(out["dimensions"], [2.0, 2.0, 2.0])
        self.assertEqual(out["type"], "MESH")


class TransformToolsTest(unittest.TestCase):
    def setUp(self):
        self.cube = _make_object("Cube", location=(1.0, 2.0, 3.0))
        _install_bpy_stub(scene_objects=[self.cube])
        self.registry, _h, _t = _bootstrap()

    def test_translate_delta_adds_to_location(self):
        self.registry.get("transform.translate").run({"vector": [1.0, 0.5, -1.0]})
        self.assertEqual(list(self.cube.location), [2.0, 2.5, 2.0])

    def test_translate_absolute_overwrites_location(self):
        self.registry.get("transform.translate").run({
            "vector": [10.0, 20.0, 30.0], "absolute": True,
        })
        self.assertEqual(list(self.cube.location), [10.0, 20.0, 30.0])

    def test_rotate_in_degrees_absolute(self):
        self.registry.get("transform.rotate").run({
            "euler": [180.0, 0.0, 0.0], "unit": "degrees", "absolute": True,
        })
        self.assertAlmostEqual(self.cube.rotation_euler[0], math.pi)

    def test_uniform_scale_overrides_factor_arg(self):
        self.registry.get("transform.scale").run({"uniform": 2.5})
        self.assertEqual(list(self.cube.scale), [2.5, 2.5, 2.5])

    def test_scale_without_active_object_errors(self):
        _install_bpy_stub(scene_objects=[])
        registry, _h, _t = _bootstrap()
        with self.assertRaises(ValueError):
            registry.get("transform.scale").run({"factor": [2.0, 2.0, 2.0]})


class MeshToolsTest(unittest.TestCase):
    def test_add_primitive_dispatches_and_renames_object(self):
        new_obj = _make_object("Cube.001")
        called = {}

        def _primitive_cube_add(**kw):
            called.update(kw)
            return {"FINISHED"}

        objs_collection = _Collection([new_obj])
        bpy = _install_bpy_stub(scene_objects=[new_obj], ops=types.SimpleNamespace(
            mesh=types.SimpleNamespace(primitive_cube_add=_primitive_cube_add),
        ))
        bpy.context.view_layer.objects = objs_collection

        registry, _h, _t = _bootstrap()
        out = json.loads(registry.get("mesh.add_primitive").run({
            "type": "CUBE",
            "location": [1.0, 0.0, 0.0],
            "name": "Hero",
        }))
        self.assertEqual(out["primitive"], "CUBE")
        self.assertEqual(called["location"], (1.0, 0.0, 0.0))
        self.assertEqual(new_obj.name, "Hero")

    def test_add_primitive_rejects_unknown_type(self):
        bpy = _install_bpy_stub(scene_objects=[], ops=types.SimpleNamespace(
            mesh=types.SimpleNamespace(),
        ))
        registry, _h, _t = _bootstrap()
        with self.assertRaises(ValueError):
            registry.get("mesh.add_primitive").run({"type": "SUSHI"})


class SystemToolsTest(unittest.TestCase):
    def test_run_operator_rejects_namespaces_outside_allowlist(self):
        _install_bpy_stub(ops=types.SimpleNamespace(
            wm=types.SimpleNamespace(
                save_as_mainfile=lambda **_kw: {"FINISHED"},
            ),
        ))
        registry, _h, _t = _bootstrap()
        with self.assertRaises(ValueError):
            registry.get("bpy.run_operator").run({
                "operator": "wm.save_as_mainfile",
                "kwargs": {"filepath": "/tmp/whatever.blend"},
            })

    def test_run_operator_dispatches_to_allowlisted_module(self):
        called = {}

        def _shade_smooth(**kw):
            called.update(kw)
            return {"FINISHED"}

        _install_bpy_stub(ops=types.SimpleNamespace(
            object=types.SimpleNamespace(shade_smooth=_shade_smooth),
        ))
        registry, _h, _t = _bootstrap()

        out = json.loads(registry.get("bpy.run_operator").run({
            "operator": "object.shade_smooth",
            "kwargs": {},
        }))
        self.assertEqual(out["result"], ["FINISHED"])

    def test_run_operator_rejects_unknown_operator_name(self):
        _install_bpy_stub(ops=types.SimpleNamespace(
            object=types.SimpleNamespace(),
        ))
        registry, _h, _t = _bootstrap()
        with self.assertRaises(ValueError):
            registry.get("bpy.run_operator").run({
                "operator": "object.does_not_exist",
            })

    def test_python_eval_blocks_imports_and_dunders(self):
        _install_bpy_stub()
        registry, _h, _t = _bootstrap()
        tool = registry.get("python.eval_in_sandbox")
        with self.assertRaises(ValueError):
            tool.run({"expression": "import os"})
        with self.assertRaises(ValueError):
            tool.run({"expression": "().__class__.__bases__"})
        with self.assertRaises(ValueError):
            tool.run({"expression": "1; 2"})

    def test_python_eval_returns_pure_expression_result(self):
        _install_bpy_stub()
        registry, _h, _t = _bootstrap()
        out = json.loads(registry.get("python.eval_in_sandbox").run({
            "expression": "sum(range(5))",
        }))
        self.assertEqual(out["result"], 10)


class DefaultToolSetTest(unittest.TestCase):
    def test_register_default_tools_registers_full_tool_set(self):
        _install_bpy_stub()
        registry, harness_mod, _ = _bootstrap()
        names = sorted(t.name for t in registry.all())
        expected = {
            "scene.list_objects", "scene.get_object", "scene.select",
            "mesh.add_primitive",
            "transform.translate", "transform.rotate", "transform.scale",
            "bpy.run_operator", "python.eval_in_sandbox",
            "viewport.screenshot",
        }
        self.assertEqual(set(names), expected)
        for tool in registry.all():
            self.assertEqual(tool.parameters.get("type", "object"), "object")
            self.assertIn(tool.permission, harness_mod.PERMISSIONS)


if __name__ == "__main__":
    unittest.main()
