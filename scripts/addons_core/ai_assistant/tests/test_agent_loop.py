# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""End-to-end tests for the operators-side agent loop.

The real send operator runs on Blender's main thread. Here we exercise
the pure-Python helpers (`_process_pending_tools`, `_select_tools`,
the permission-mode mapping, and the tool-log preview) without spinning
a real worker thread or scheduling timers.
"""

import importlib.util
import queue
import sys
import threading
import types
import unittest
import unittest.mock  # noqa: F401 — used by `mock.patch.object` below.
from pathlib import Path


ADDON_DIR = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "_ai_assistant_test_loop"


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
            is_registered=lambda _cb: False,
            register=lambda _cb, first_interval=0.0: None,
            unregister=lambda _cb: None,
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
    _load(PACKAGE_NAME + ".permissions", ADDON_DIR / "permissions.py")

    prefs_stub = types.ModuleType(PACKAGE_NAME + ".preferences")
    prefs_stub.get_prefs = lambda _ctx=None: None
    sys.modules[PACKAGE_NAME + ".preferences"] = prefs_stub

    return _load(PACKAGE_NAME + ".operators", ADDON_DIR / "operators.py")


operators = _bootstrap()
harness = sys.modules[PACKAGE_NAME + ".harness"]
permissions = sys.modules[PACKAGE_NAME + ".permissions"]


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
        self.trusted_tools = _TrustedToolList()


class _TrustedToolEntry:
    def __init__(self, name=""):
        self.name = name


class _TrustedToolList:
    def __init__(self):
        self._items: list[_TrustedToolEntry] = []

    def add(self):
        e = _TrustedToolEntry()
        self._items.append(e)
        return e

    def remove(self, i):
        del self._items[i]

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)


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


def _make_state(tools, calls, placeholder_text, permission_mode="always"):
    """Return ``(state, session, scene)`` for use in tests.

    The ``scene`` object must outlive the state because ``_RequestState``
    only keeps a weakref. In real use Blender anchors the scene via
    ``bpy.data.scenes``; here the test must hold its own reference.
    """
    session = _make_session()
    operators._append_message(session, "user", "do something")
    idx = operators._append_message(session, "assistant", placeholder_text)
    history = [harness.Message(role="user", content="do something")]
    scene = _Scene(session)
    state = operators._RequestState(
        thread=None, q=None, cancel=threading.Event(), scene=scene,
        msg_index=idx, history=history, tools=tools, provider=FakeProvider([]),
        permission_mode=permission_mode,
    )
    state.pending_tool_calls = list(calls)
    state.round_tool_calls = list(calls)
    return state, session, scene


class ProcessPendingToolsTest(unittest.TestCase):
    def setUp(self):
        permissions.clear_session()

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
        state, session, scene = _make_state([tool], [call], "thinking")

        finished = operators._process_pending_tools(state, session)
        self.assertTrue(finished)
        operators._commit_tool_round(state, session)

        self.assertEqual(invocations, [{}])
        roles = [m.role for m in session.messages]
        self.assertEqual(roles, ["user", "assistant", "tool"])
        self.assertIn("scene.list_objects", session.messages[2].content)
        self.assertEqual(state.history[-2].role, "assistant")
        self.assertEqual(state.history[-2].tool_calls[0].id, "tc_1")
        self.assertEqual(state.history[-1].role, "tool")
        self.assertEqual(state.history[-1].tool_results[0].call_id, "tc_1")
        self.assertFalse(state.history[-1].tool_results[0].is_error)

    def test_unknown_tool_is_logged_as_error(self):
        call = harness.ToolCall(id="tc_x", name="not.a.tool", arguments={})
        state, session, scene = _make_state([], [call], "")

        finished = operators._process_pending_tools(state, session)
        self.assertTrue(finished)
        operators._commit_tool_round(state, session)

        self.assertEqual(session.messages[-1].role, "tool")
        self.assertIn("denied", session.messages[-1].content.lower())
        self.assertTrue(state.history[-1].tool_results[0].is_error)

    def test_failing_tool_is_marked_error_in_result(self):
        def _boom(_args):
            raise RuntimeError("bad")

        tool = harness.ToolSpec(
            name="explode", description="d", permission="write",
            parameters={"type": "object"}, run=_boom,
        )
        call = harness.ToolCall(id="tc_b", name="explode", arguments={})
        state, session, scene = _make_state([tool], [call], "")

        finished = operators._process_pending_tools(state, session)
        self.assertTrue(finished)
        operators._commit_tool_round(state, session)

        result = state.history[-1].tool_results[0]
        self.assertTrue(result.is_error)
        self.assertIn("bad", result.content)


class PermissionGateInLoopTest(unittest.TestCase):
    """The agent loop must consult :mod:`permissions` per call."""

    def setUp(self):
        permissions.clear_session()

    def test_deny_mode_short_circuits_write_call(self):
        ran = []

        def _run(args):
            ran.append(args)
            return "ok"

        tool = harness.ToolSpec(
            name="mesh.add_primitive", description="d", permission="write",
            parameters={"type": "object"}, run=_run,
        )
        call = harness.ToolCall(id="tc_1", name="mesh.add_primitive", arguments={})
        state, session, scene = _make_state(
            [tool], [call], "", permission_mode="deny",
        )

        finished = operators._process_pending_tools(state, session)
        self.assertTrue(finished)
        self.assertEqual(ran, [])
        self.assertTrue(state.tool_results[0].is_error)
        self.assertIn("denied", state.tool_results[0].content.lower())

    def test_always_mode_skips_popup_and_runs_write_call(self):
        ran = []

        def _run(args):
            ran.append(args)
            return "ok"

        tool = harness.ToolSpec(
            name="mesh.add_primitive", description="d", permission="write",
            parameters={"type": "object"}, run=_run,
        )
        call = harness.ToolCall(id="tc_1", name="mesh.add_primitive", arguments={})
        state, session, scene = _make_state(
            [tool], [call], "", permission_mode="always",
        )

        finished = operators._process_pending_tools(state, session)
        self.assertTrue(finished)
        self.assertEqual(ran, [{}])
        self.assertFalse(state.tool_results[0].is_error)

    def test_ask_mode_pauses_until_decision_then_resumes(self):
        ran = []

        def _run(args):
            ran.append(args)
            return "ok"

        write_tool = harness.ToolSpec(
            name="mesh.add_primitive", description="add a primitive",
            permission="write", parameters={"type": "object"}, run=_run,
        )
        call = harness.ToolCall(
            id="tc_1", name="mesh.add_primitive", arguments={"type": "CUBE"},
        )
        state, session, scene = _make_state(
            [write_tool], [call], "", permission_mode="ask",
        )

        # Pretend the modal popup has not been answered yet by stubbing
        # out the operator invocation.
        invoked = []

        def _fake_popup(*_args, **kwargs):
            invoked.append(kwargs)

        original = operators._invoke_permission_popup
        operators._invoke_permission_popup = lambda *a, **k: invoked.append((a, k))
        try:
            finished = operators._process_pending_tools(state, session)
            self.assertFalse(finished)
            self.assertEqual(ran, [])
            self.assertIsNotNone(state.awaiting_decision)

            state.pending_decision = permissions.DECISION_ONCE
            finished = operators._process_pending_tools(state, session)
        finally:
            operators._invoke_permission_popup = original

        self.assertTrue(finished)
        self.assertEqual(ran, [{"type": "CUBE"}])
        self.assertIsNone(state.awaiting_decision)

    def test_ask_mode_records_denial_when_user_chooses_deny(self):
        ran = []
        write_tool = harness.ToolSpec(
            name="mesh.add_primitive", description="d", permission="write",
            parameters={"type": "object"}, run=lambda a: ran.append(a) or "ok",
        )
        call = harness.ToolCall(id="tc_1", name="mesh.add_primitive", arguments={})
        state, session, scene = _make_state(
            [write_tool], [call], "", permission_mode="ask",
        )

        original = operators._invoke_permission_popup
        operators._invoke_permission_popup = lambda *a, **k: None
        try:
            self.assertFalse(operators._process_pending_tools(state, session))
            state.pending_decision = permissions.DECISION_DENY
            self.assertTrue(operators._process_pending_tools(state, session))
        finally:
            operators._invoke_permission_popup = original

        self.assertEqual(ran, [])
        self.assertTrue(state.tool_results[0].is_error)

    def test_session_trust_bypasses_popup_on_subsequent_calls(self):
        ran = []
        write_tool = harness.ToolSpec(
            name="mesh.add_primitive", description="d", permission="write",
            parameters={"type": "object"}, run=lambda a: ran.append(a) or "ok",
        )
        call_a = harness.ToolCall(id="tc_a", name="mesh.add_primitive", arguments={})
        state, session, scene = _make_state(
            [write_tool], [call_a], "", permission_mode="session",
        )

        original = operators._invoke_permission_popup
        operators._invoke_permission_popup = lambda *a, **k: None
        try:
            self.assertFalse(operators._process_pending_tools(state, session))
            state.pending_decision = permissions.DECISION_SESSION
            self.assertTrue(operators._process_pending_tools(state, session))
        finally:
            operators._invoke_permission_popup = original

        # Second turn: the same tool should run without a popup.
        operators._commit_tool_round(state, session)
        call_b = harness.ToolCall(id="tc_b", name="mesh.add_primitive", arguments={})
        state.pending_tool_calls = [call_b]
        state.round_tool_calls = [call_b]
        self.assertTrue(operators._process_pending_tools(state, session))
        self.assertEqual(len(ran), 2)

    def test_project_trust_persists_via_scene_property(self):
        ran = []
        write_tool = harness.ToolSpec(
            name="mesh.add_primitive", description="d", permission="write",
            parameters={"type": "object"}, run=lambda a: ran.append(a) or "ok",
        )
        call = harness.ToolCall(id="tc_p", name="mesh.add_primitive", arguments={})
        state, session, scene = _make_state(
            [write_tool], [call], "", permission_mode="ask",
        )

        original = operators._invoke_permission_popup
        operators._invoke_permission_popup = lambda *a, **k: None
        try:
            self.assertFalse(operators._process_pending_tools(state, session))
            state.pending_decision = permissions.DECISION_ALWAYS
            self.assertTrue(operators._process_pending_tools(state, session))
        finally:
            operators._invoke_permission_popup = original

        self.assertEqual(ran, [{}])
        self.assertEqual(
            [t.name for t in session.trusted_tools],
            ["mesh.add_primitive"],
        )


class PermissionFilterTest(unittest.TestCase):
    """`_select_tools` should respect `prefs.permission_mode`."""

    def test_deny_returns_empty(self):
        prefs = types.SimpleNamespace(permission_mode="deny")
        self.assertEqual(operators._select_tools(prefs), [])

    def test_ask_advertises_full_catalogue(self):
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
        self.assertEqual(sorted(t.name for t in result), ["r", "w", "x"])


class HardStopTest(unittest.TestCase):
    def test_start_next_step_returns_false_after_max_steps(self):
        session = _make_session()
        operators._append_message(session, "user", "hi")
        idx = operators._append_message(session, "assistant", "")
        scene = _Scene(session)
        state = operators._RequestState(
            thread=None, q=None, cancel=threading.Event(), scene=scene,
            msg_index=idx, history=[], tools=[], provider=FakeProvider([]),
            permission_mode="always",
        )
        state.step = operators._MAX_AGENT_STEPS

        ok = operators._start_next_step(state, session)
        self.assertFalse(ok)
        self.assertIn("hard-stop", session.messages[-1].content)


class DrainTickTest(unittest.TestCase):
    def setUp(self):
        permissions.clear_session()

    def tearDown(self):
        operators._state = None
        permissions.clear_session()

    def test_finish_reason_survives_until_worker_sentinel_arrives(self):
        invocations = []

        def _record(args):
            invocations.append(args)
            return "ok"

        tool = harness.ToolSpec(
            name="scene.list_objects", description="d", permission="read",
            parameters={"type": "object"}, run=_record,
        )
        call = harness.ToolCall(
            id="tc_1", name="scene.list_objects", arguments={"filter": "mesh"},
        )

        session = _make_session()
        session.busy = True
        operators._append_message(session, "user", "inspect scene")
        idx = operators._append_message(session, "assistant", "")
        scene = _Scene(session)
        q = queue.Queue()
        state = operators._RequestState(
            thread=None, q=q, cancel=threading.Event(), scene=scene,
            msg_index=idx, history=[harness.Message(role="user", content="inspect scene")],
            tools=[tool], provider=FakeProvider([]),
            permission_mode="always",
        )
        state.step = operators._MAX_AGENT_STEPS
        operators._state = state

        q.put(harness.StreamChunk(tool_call=call))
        q.put(harness.StreamChunk(finish_reason="tool_use"))

        self.assertEqual(operators._drain_tick(), operators._TICK_INTERVAL)
        self.assertEqual(state.finish_reason, "tool_use")
        self.assertEqual(invocations, [])

        q.put(None)
        self.assertIsNone(operators._drain_tick())

        self.assertEqual(invocations, [{"filter": "mesh"}])
        self.assertIsNone(operators._state)
        self.assertFalse(session.busy)
        self.assertEqual(state.history[-2].tool_calls[0].id, "tc_1")
        self.assertEqual(state.history[-1].tool_results[0].content, "ok")


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
