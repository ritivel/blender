# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

import importlib.util
import sys
import types
import unittest
from pathlib import Path


ADDON_DIR = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "_ai_assistant_test"


def _install_bpy_stub() -> None:
    bpy = types.ModuleType("bpy")
    bpy_props = types.ModuleType("bpy.props")
    bpy_props.StringProperty = lambda **_kwargs: None
    bpy_props.EnumProperty = lambda **_kwargs: None
    bpy_props.IntProperty = lambda **_kwargs: None
    bpy_types = types.ModuleType("bpy.types")
    bpy_types.Operator = object
    bpy.props = bpy_props
    bpy.types = bpy_types
    bpy.context = types.SimpleNamespace(window_manager=None)
    bpy.app = types.SimpleNamespace(
        timers=types.SimpleNamespace(
            is_registered=lambda _callback: False,
            register=lambda _callback, first_interval=0.0: None,
            unregister=lambda _callback: None,
        )
    )
    bpy.ops = types.SimpleNamespace(
        ai_assistant=types.SimpleNamespace(
            permission_prompt=lambda *args, **kwargs: None,
        ),
    )
    sys.modules["bpy"] = bpy
    sys.modules["bpy.props"] = bpy_props
    sys.modules["bpy.types"] = bpy_types


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_operators_module():
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(ADDON_DIR)]
    sys.modules[PACKAGE_NAME] = package

    _install_bpy_stub()
    _load_module(PACKAGE_NAME + ".harness", ADDON_DIR / "harness.py")
    _load_module(PACKAGE_NAME + ".permissions", ADDON_DIR / "permissions.py")

    preferences = types.ModuleType(PACKAGE_NAME + ".preferences")
    preferences.get_prefs = lambda _context: None
    sys.modules[PACKAGE_NAME + ".preferences"] = preferences

    return _load_module(PACKAGE_NAME + ".operators", ADDON_DIR / "operators.py")


operators = _load_operators_module()


class _Session:
    def __init__(self, messages):
        self.messages = [
            types.SimpleNamespace(role=role, content=content)
            for role, content in messages
        ]


class BuildHistoryTest(unittest.TestCase):
    def test_skipped_empty_assistant_does_not_leave_adjacent_user_turns(self):
        history = operators._build_history(
            _Session(
                (
                    ("user", "Before stop"),
                    ("assistant", ""),
                    ("user", "After stop"),
                )
            ),
            "",
        )

        self.assertEqual([(m.role, m.content) for m in history], [
            ("user", "Before stop\n\nAfter stop"),
        ])

    def test_preserves_alternating_non_empty_turns(self):
        history = operators._build_history(
            _Session(
                (
                    ("user", "Question"),
                    ("assistant", "Answer"),
                    ("user", "Follow-up"),
                )
            ),
            "System prompt",
        )

        self.assertEqual([(m.role, m.content) for m in history], [
            ("system", "System prompt"),
            ("user", "Question"),
            ("assistant", "Answer"),
            ("user", "Follow-up"),
        ])

    def test_consecutive_same_role_messages_are_coalesced(self):
        history = operators._build_history(
            _Session(
                (
                    ("user", "First"),
                    ("user", "Second"),
                    ("assistant", "Reply"),
                    ("assistant", "More reply"),
                )
            ),
            "",
        )

        self.assertEqual([(m.role, m.content) for m in history], [
            ("user", "First\n\nSecond"),
            ("assistant", "Reply\n\nMore reply"),
        ])


if __name__ == "__main__":
    unittest.main()
