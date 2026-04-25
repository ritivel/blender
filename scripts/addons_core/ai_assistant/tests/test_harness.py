# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tests for the typed tool harness (ToolSpec, ToolRegistry, permissions)."""

import importlib.util
import sys
import unittest
from pathlib import Path


ADDON_DIR = Path(__file__).resolve().parents[1]


def _load_harness():
    spec = importlib.util.spec_from_file_location(
        "_ai_assistant_harness", ADDON_DIR / "harness.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


harness = _load_harness()


def _make_spec(name, permission="read"):
    return harness.ToolSpec(
        name=name,
        description="test " + name,
        permission=permission,
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        run=lambda _args: "ok",
    )


class ToolSpecValidationTest(unittest.TestCase):
    def test_rejects_unknown_permission_class(self):
        with self.assertRaises(ValueError):
            harness.ToolSpec(
                name="bad",
                description="bad",
                permission="banana",
                parameters={},
                run=lambda _args: "",
            )

    def test_accepts_each_known_permission(self):
        for perm in harness.PERMISSIONS:
            spec = _make_spec("p_" + perm, permission=perm)
            self.assertEqual(spec.permission, perm)


class ToolRegistryTest(unittest.TestCase):
    def test_register_get_and_all_round_trip(self):
        reg = harness.ToolRegistry()
        spec = _make_spec("scene.list_objects")
        reg.register(spec)
        self.assertIs(reg.get("scene.list_objects"), spec)
        self.assertEqual([t.name for t in reg.all()], ["scene.list_objects"])

    def test_duplicate_registration_is_rejected(self):
        reg = harness.ToolRegistry()
        reg.register(_make_spec("a"))
        with self.assertRaises(ValueError):
            reg.register(_make_spec("a"))

    def test_filter_by_permission_returns_matching_tools_only(self):
        reg = harness.ToolRegistry()
        reg.register(_make_spec("r", permission="read"))
        reg.register(_make_spec("w", permission="write"))
        reg.register(_make_spec("x", permission="exec"))
        names = sorted(t.name for t in reg.filter_by_permission(["read", "exec"]))
        self.assertEqual(names, ["r", "x"])
        self.assertEqual(reg.filter_by_permission([]), [])


class PermissionModeTest(unittest.TestCase):
    def test_deny_blocks_all_tools(self):
        self.assertEqual(harness.allowed_permissions("deny"), set())

    def test_ask_and_session_advertise_full_catalogue(self):
        # Step 4 moves the gate from list-filtering to per-call: in any
        # non-`deny` mode the model sees every tool, but write/exec
        # calls are then individually gated by `permissions.decide`.
        self.assertEqual(
            harness.allowed_permissions("ask"),
            {"read", "write", "exec"},
        )
        self.assertEqual(
            harness.allowed_permissions("session"),
            {"read", "write", "exec"},
        )

    def test_always_permits_every_class(self):
        self.assertEqual(
            harness.allowed_permissions("always"),
            {"read", "write", "exec"},
        )

    def test_unknown_mode_falls_back_to_full_access(self):
        # Mirrors the operators-side behaviour: a config we do not
        # recognise should not silently disable tools.
        self.assertEqual(
            harness.allowed_permissions(""),
            {"read", "write", "exec"},
        )


class MessageToolFieldsTest(unittest.TestCase):
    def test_assistant_message_carries_tool_calls(self):
        call = harness.ToolCall(id="tc_1", name="scene.list_objects", arguments={})
        msg = harness.Message(role="assistant", content="thinking…", tool_calls=[call])
        self.assertEqual(msg.tool_calls[0].name, "scene.list_objects")
        self.assertEqual(msg.tool_results, [])

    def test_tool_message_carries_tool_results(self):
        result = harness.ToolResult(call_id="tc_1", content="{}", is_error=False)
        msg = harness.Message(role="tool", tool_results=[result])
        self.assertEqual(msg.tool_results[0].call_id, "tc_1")
        self.assertEqual(msg.tool_calls, [])


class StreamChunkShapeTest(unittest.TestCase):
    def test_default_chunk_is_blank(self):
        chunk = harness.StreamChunk()
        self.assertEqual(chunk.delta_text, "")
        self.assertIsNone(chunk.tool_call)
        self.assertIsNone(chunk.error)
        self.assertIsNone(chunk.finish_reason)


if __name__ == "__main__":
    unittest.main()
