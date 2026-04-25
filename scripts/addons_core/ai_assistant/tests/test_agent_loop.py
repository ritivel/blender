# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""End-to-end tests for the operators-side agent loop.

The real send operator runs on Blender's main thread. Here we exercise
the pure-Python helpers (`_run_pending_tools`, `_select_tools`, the
permission-mode mapping, and the tool-log preview) without spinning a
real worker thread or scheduling timers.
"""

import importlib.util
import sys
import threading
import types
import unittest
from pathlib import Path


ADDON_DIR = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "_ai_assistant_test_loop"


def _install_bpy_stub() -> None:
    bpy = types.ModuleType("bpy")
    bpy_props = types.ModuleType("bpy.props")
    bpy_props.StringProperty = lambda **_kwargs: None
    bpy_types = types.ModuleType("bpy.types")
    bpy_types.Operator = object
    bpy.props = bpy_props
    bpy.types = bpy_types
    bpy.context = types.SimpleNamespace(window_manager=None)
    bpy.app = types.SimpleNamespace(
        timers=types.SimpleNamespace(
            is_registered=lambda _cb: False,
            register=lambda _cb, first_interval=0.0: None,
            unregister=lambda _cb: None,
        )
    )
    sys.modules["bpy"] = bpy
    sys.modules["bpy.props"] = bpy_props
    sys.modules["bpy.types"] = bpy_types


def _load(module_name, path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _bootstrap():
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(ADDON_DIR)]
    sys.modules[PACKAGE_NAME] = package

    _install_bpy_stub()
    _load(PACKAGE_NAME + ".harness", ADDON_DIR / "harness.py")

    prefs_stub = types.ModuleType(PACKAGE_NAME + ".preferences")
    prefs_stub.get_prefs = lambda _ctx=None: None
    sys.modules[PACKAGE_NAME + ".preferences"] = prefs_stub

    return _load(PACKAGE_NAME + ".operators", ADDON_DIR / "operators.py")


operators = _bootstrap()
harness = sys.modules[PACKAGE_NAME + ".harness"]


class _Msg:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class _Session:
    def __init__(self):
        self.messages: list[_Msg] = []
        self.active_message_index = 0
        self.busy = False
        self.draft = ""

    # Mimic Blender CollectionProperty operations used by operators._append_message.
    def __getattr__(self, name):  # pragma: no cover — for completeness
        raise AttributeError(name)


class _MessageList:
    """Acts like both a CollectionProperty.add()-er and a list."""

    def __init__(self):
        self._items: list[_Msg] = []

    def add(self):
        msg = _Msg("user", "")
        self._items.append(msg)
        return msg

    def __len__(self):
        return len(self._items)

    def __getitem__(self, i):
        return self._items[i]

    def __iter__(self):
        return iter(self._items)

    def remove(self, i):
        del self._items[i]

    def clear(self):
        self._items.clear()


def _make_session() -> _Session:
    s = _Session()
    s.messages = _MessageList()
    return s


class _Scene:
    """Minimal stand-in for a bpy scene that supports weakref."""

    def __init__(self, session):
        self.ai_assistant = session


class FakeProvider:
    """Records the histories it was asked to stream and replays scripted chunks."""

    def __init__(self, scripts):
        self._scripts = list(scripts)
        self.histories: list[list[harness.Message]] = []

    def stream(self, messages, _tools):
        self.histories.append(list(messages))
        chunks = self._scripts.pop(0) if self._scripts else []
        for chunk in chunks:
            yield chunk


class RunPendingToolsTest(unittest.TestCase):
    def _make_state(self, tools, calls, placeholder_text):
        session = _make_session()
        operators._append_message(session, "user", "do something")
        idx = operators._append_message(session, "assistant", placeholder_text)
        history = [harness.Message(role="user", content="do something")]
        scene = _Scene(session)
        state = operators._RequestState(
            thread=None, q=None, cancel=threading.Event(), scene=scene,
            msg_index=idx, history=history, tools=tools, provider=FakeProvider([]),
        )
        state.pending_tool_calls = list(calls)
        return state, session

    def test_executes_each_pending_call_and_records_log(self):
        invocations = []

        def _record(args):
            invocations.append(args)
            return '{"objects": []}'

        tool = harness.ToolSpec(
            name="scene.list_objects", description="d", permission="read",
            parameters={"type": "object"}, run=_record,
        )
        call = harness.ToolCall(id="tc_1", name="scene.list_objects", arguments={})
        state, session = self._make_state([tool], [call], "thinking")

        operators._run_pending_tools(state, session)

        self.assertEqual(invocations, [{}])
        roles = [m.role for m in session.messages]
        self.assertEqual(roles, ["user", "assistant", "tool"])
        self.assertIn("scene.list_objects", session.messages[2].content)
        # History should now contain the assistant turn (with tool_calls)
        # and a tool result message.
        self.assertEqual(state.history[-2].role, "assistant")
        self.assertEqual(state.history[-2].tool_calls[0].id, "tc_1")
        self.assertEqual(state.history[-1].role, "tool")
        self.assertEqual(state.history[-1].tool_results[0].call_id, "tc_1")
        self.assertFalse(state.history[-1].tool_results[0].is_error)

    def test_unknown_tool_is_logged_as_error(self):
        call = harness.ToolCall(id="tc_x", name="not.a.tool", arguments={})
        state, session = self._make_state([], [call], "")

        operators._run_pending_tools(state, session)

        self.assertEqual(session.messages[-1].role, "tool")
        self.assertIn("unknown or denied", session.messages[-1].content)
        self.assertTrue(state.history[-1].tool_results[0].is_error)

    def test_failing_tool_is_marked_error_in_result(self):
        def _boom(_args):
            raise RuntimeError("bad")

        tool = harness.ToolSpec(
            name="explode", description="d", permission="write",
            parameters={"type": "object"}, run=_boom,
        )
        call = harness.ToolCall(id="tc_b", name="explode", arguments={})
        state, session = self._make_state([tool], [call], "")

        operators._run_pending_tools(state, session)

        result = state.history[-1].tool_results[0]
        self.assertTrue(result.is_error)
        self.assertIn("bad", result.content)


class PermissionFilterTest(unittest.TestCase):
    """`_select_tools` should respect `prefs.permission_mode`."""

    def test_deny_returns_empty(self):
        prefs = types.SimpleNamespace(permission_mode="deny")
        # Should not even need to consult the registry: returns immediately.
        self.assertEqual(operators._select_tools(prefs), [])

    def test_ask_filters_to_read_only(self):
        # Sub in a small registry to avoid loading the real bpy-using tools.
        custom = harness.ToolRegistry()
        custom.register(harness.ToolSpec(
            name="r", description="r", permission="read",
            parameters={"type": "object"}, run=lambda _a: "",
        ))
        custom.register(harness.ToolSpec(
            name="w", description="w", permission="write",
            parameters={"type": "object"}, run=lambda _a: "",
        ))
        custom.register(harness.ToolSpec(
            name="x", description="x", permission="exec",
            parameters={"type": "object"}, run=lambda _a: "",
        ))
        with unittest.mock.patch.object(harness, "default_registry", return_value=custom):
            result = operators._select_tools(types.SimpleNamespace(permission_mode="ask"))
        self.assertEqual(sorted(t.name for t in result), ["r"])


class HardStopTest(unittest.TestCase):
    def test_start_next_step_returns_false_after_max_steps(self):
        session = _make_session()
        operators._append_message(session, "user", "hi")
        idx = operators._append_message(session, "assistant", "")
        scene = _Scene(session)
        state = operators._RequestState(
            thread=None, q=None, cancel=threading.Event(), scene=scene,
            msg_index=idx, history=[], tools=[], provider=FakeProvider([]),
        )
        state.step = operators._MAX_AGENT_STEPS

        ok = operators._start_next_step(state, session)
        self.assertFalse(ok)
        self.assertIn("hard-stop", session.messages[-1].content)


class BuildHistoryWithToolsTest(unittest.TestCase):
    def test_tool_role_messages_are_skipped_from_provider_history(self):
        session = _make_session()
        operators._append_message(session, "user", "make a cube")
        operators._append_message(session, "assistant", "Sure")
        operators._append_message(session, "tool", "[tool] mesh.add_primitive(…) → ok")
        operators._append_message(session, "assistant", "Done.")

        history = operators._build_history(session, "")
        roles = [m.role for m in history]
        self.assertEqual(roles, ["user", "assistant"])
        self.assertEqual(history[1].content, "Sure\n\nDone.")


if __name__ == "__main__":
    import unittest.mock  # noqa: F401 — re-export for the patch above
    unittest.main()
