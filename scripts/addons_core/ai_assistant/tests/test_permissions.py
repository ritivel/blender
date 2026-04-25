# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tests for the per-call permission gate (Step 4 of the plan).

The :mod:`permissions` module is pure Python and does not depend on
``bpy``. The scene argument is duck-typed to anything exposing
``ai_assistant.trusted_tools`` with Blender CollectionProperty
semantics; the helpers below provide a minimal stand-in.
"""

import importlib.util
import sys
import unittest
from pathlib import Path


ADDON_DIR = Path(__file__).resolve().parents[1]


def _load_permissions():
    spec = importlib.util.spec_from_file_location(
        "_ai_assistant_permissions", ADDON_DIR / "permissions.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


permissions = _load_permissions()


class _TrustEntry:
    def __init__(self, name=""):
        self.name = name


class _TrustList:
    def __init__(self):
        self._items: list[_TrustEntry] = []

    def add(self):
        e = _TrustEntry()
        self._items.append(e)
        return e

    def remove(self, i):
        del self._items[i]

    def __iter__(self):
        return iter(self._items)


class _Session:
    def __init__(self):
        self.trusted_tools = _TrustList()


class _Scene:
    def __init__(self):
        self.ai_assistant = _Session()


class DecideTest(unittest.TestCase):
    def setUp(self):
        permissions.clear_session()

    def test_deny_mode_blocks_everything(self):
        scene = _Scene()
        for perm in ("read", "write", "exec"):
            self.assertEqual(
                permissions.decide("deny", scene, "any.tool", perm),
                permissions.DENY,
            )

    def test_read_tools_always_allowed_outside_deny(self):
        scene = _Scene()
        for mode in ("ask", "session", "always"):
            self.assertEqual(
                permissions.decide(mode, scene, "scene.list_objects", "read"),
                permissions.ALLOW,
            )

    def test_always_mode_allows_write_and_exec(self):
        scene = _Scene()
        for perm in ("write", "exec"):
            self.assertEqual(
                permissions.decide("always", scene, "x", perm),
                permissions.ALLOW,
            )

    def test_ask_mode_prompts_for_write_and_exec(self):
        scene = _Scene()
        for perm in ("write", "exec"):
            self.assertEqual(
                permissions.decide("ask", scene, "x", perm),
                permissions.PROMPT,
            )

    def test_session_trust_only_applies_in_session_mode(self):
        scene = _Scene()
        permissions.trust_for_session("mesh.add_primitive")

        self.assertEqual(
            permissions.decide("session", scene, "mesh.add_primitive", "write"),
            permissions.ALLOW,
        )
        # In `ask` mode, session trust is intentionally ignored — the
        # user asked to be prompted every time.
        self.assertEqual(
            permissions.decide("ask", scene, "mesh.add_primitive", "write"),
            permissions.PROMPT,
        )

    def test_project_trust_short_circuits_in_every_mode_except_deny(self):
        scene = _Scene()
        permissions.trust_for_project(scene, "mesh.add_primitive")

        for mode in ("ask", "session", "always"):
            self.assertEqual(
                permissions.decide(mode, scene, "mesh.add_primitive", "write"),
                permissions.ALLOW,
            )
        # Deny still wins — the user explicitly disabled tool execution.
        self.assertEqual(
            permissions.decide("deny", scene, "mesh.add_primitive", "write"),
            permissions.DENY,
        )


class ApplyDecisionTest(unittest.TestCase):
    def setUp(self):
        permissions.clear_session()

    def test_once_runs_without_persisting_anything(self):
        scene = _Scene()
        outcome = permissions.apply_decision(
            scene, "mesh.add_primitive", permissions.DECISION_ONCE,
        )
        self.assertEqual(outcome, permissions.ALLOW)
        self.assertFalse(permissions.is_trusted_for_session("mesh.add_primitive"))
        self.assertFalse(
            permissions.is_trusted_for_project(scene, "mesh.add_primitive"),
        )

    def test_session_decision_adds_to_session_trust(self):
        scene = _Scene()
        outcome = permissions.apply_decision(
            scene, "mesh.add_primitive", permissions.DECISION_SESSION,
        )
        self.assertEqual(outcome, permissions.ALLOW)
        self.assertTrue(permissions.is_trusted_for_session("mesh.add_primitive"))

    def test_always_decision_adds_to_project_trust(self):
        scene = _Scene()
        outcome = permissions.apply_decision(
            scene, "mesh.add_primitive", permissions.DECISION_ALWAYS,
        )
        self.assertEqual(outcome, permissions.ALLOW)
        self.assertTrue(
            permissions.is_trusted_for_project(scene, "mesh.add_primitive"),
        )

    def test_deny_decision_returns_deny_outcome(self):
        scene = _Scene()
        outcome = permissions.apply_decision(
            scene, "mesh.add_primitive", permissions.DECISION_DENY,
        )
        self.assertEqual(outcome, permissions.DENY)
        self.assertFalse(permissions.is_trusted_for_session("mesh.add_primitive"))

    def test_unknown_decision_fails_closed(self):
        scene = _Scene()
        outcome = permissions.apply_decision(scene, "mesh.add_primitive", "garbage")
        self.assertEqual(outcome, permissions.DENY)


class TrustListMaintenanceTest(unittest.TestCase):
    def setUp(self):
        permissions.clear_session()

    def test_session_trust_can_be_revoked(self):
        permissions.trust_for_session("a")
        permissions.trust_for_session("b")
        self.assertEqual(permissions.session_trusted_tools(), ["a", "b"])

        permissions.revoke_session_trust("a")
        self.assertEqual(permissions.session_trusted_tools(), ["b"])

    def test_project_trust_does_not_duplicate(self):
        scene = _Scene()
        permissions.trust_for_project(scene, "mesh.add_primitive")
        permissions.trust_for_project(scene, "mesh.add_primitive")
        self.assertEqual(
            permissions.project_trusted_tools(scene), ["mesh.add_primitive"],
        )

    def test_project_trust_can_be_revoked(self):
        scene = _Scene()
        permissions.trust_for_project(scene, "a")
        permissions.trust_for_project(scene, "b")
        permissions.revoke_project_trust(scene, "a")
        self.assertEqual(permissions.project_trusted_tools(scene), ["b"])

    def test_clear_session_resets_trust(self):
        permissions.trust_for_session("a")
        permissions.clear_session()
        self.assertEqual(permissions.session_trusted_tools(), [])

    def test_works_when_scene_lacks_session(self):
        # Edge case: scene lookup returning a None-like session.
        class _NoSession:
            ai_assistant = None

        permissions.trust_for_project(_NoSession(), "x")  # No-op, no crash.
        self.assertFalse(
            permissions.is_trusted_for_project(_NoSession(), "x"),
        )


class DenialMessageTest(unittest.TestCase):
    def test_deny_mode_message_mentions_global_setting(self):
        msg = permissions.denial_message("mesh.add_primitive", "deny")
        self.assertIn("Deny all", msg)

    def test_other_modes_request_no_retry(self):
        msg = permissions.denial_message("mesh.add_primitive", "ask")
        self.assertIn("denied by the user", msg)
        self.assertIn("Do not retry", msg)


if __name__ == "__main__":
    unittest.main()
