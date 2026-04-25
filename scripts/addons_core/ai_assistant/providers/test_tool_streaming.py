#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tests for tool-call request shaping and SSE delta parsing.

Both providers should:
* forward declared tools in the request body in their native format,
* assemble streamed tool-call fragments into one StreamChunk per call,
* surface ``finish_reason`` consistently across providers, and
* round-trip prior assistant tool_calls + tool result messages back
  into the wire-format payload.
"""

import importlib
import json
import pathlib
import sys
import types
import unittest
from unittest import mock


if __package__:
    from . import AnthropicProvider, OpenAIProvider
    from .. import harness
else:
    _ADDONS_CORE = pathlib.Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(_ADDONS_CORE))

    ai_assistant = types.ModuleType("ai_assistant")
    ai_assistant.__path__ = [str(_ADDONS_CORE / "ai_assistant")]
    sys.modules.setdefault("ai_assistant", ai_assistant)

    harness = importlib.import_module("ai_assistant.harness")
    providers = importlib.import_module("ai_assistant.providers")
    AnthropicProvider = providers.AnthropicProvider
    OpenAIProvider = providers.OpenAIProvider


def _spec(name, permission="write"):
    return harness.ToolSpec(
        name=name,
        description="test " + name,
        permission=permission,
        parameters={
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "additionalProperties": False,
        },
        run=lambda _args: "ok",
    )


class AnthropicRequestShapingTest(unittest.TestCase):
    def test_tools_are_forwarded_in_anthropic_format(self):
        provider = AnthropicProvider(
            model="claude-test", api_key="k", base_url="http://example.test", max_tokens=100,
        )
        with mock.patch(
            AnthropicProvider.__module__ + ".post_sse", return_value=iter(())
        ) as post:
            list(provider.stream(
                [harness.Message(role="user", content="hi")],
                [_spec("scene.list_objects", "read")],
            ))
        _url, _headers, body = post.call_args.args
        self.assertEqual(len(body["tools"]), 1)
        self.assertEqual(body["tools"][0]["name"], "scene.list_objects")
        self.assertIn("input_schema", body["tools"][0])

    def test_tools_omitted_when_none_declared(self):
        provider = AnthropicProvider(
            model="claude-test", api_key="k", base_url="http://example.test", max_tokens=100,
        )
        with mock.patch(
            AnthropicProvider.__module__ + ".post_sse", return_value=iter(())
        ) as post:
            list(provider.stream(
                [harness.Message(role="user", content="hi")],
                [],
            ))
        _url, _headers, body = post.call_args.args
        self.assertNotIn("tools", body)

    def test_tool_results_become_tool_result_blocks(self):
        provider = AnthropicProvider(
            model="claude-test", api_key="k", base_url="http://example.test",
        )
        history = [
            harness.Message(role="user", content="run a tool"),
            harness.Message(
                role="assistant",
                content="I will list objects.",
                tool_calls=[harness.ToolCall(id="toolu_1", name="scene.list_objects", arguments={})],
            ),
            harness.Message(
                role="tool",
                tool_results=[harness.ToolResult(call_id="toolu_1", content="{\"objects\": []}")],
            ),
        ]
        with mock.patch(
            AnthropicProvider.__module__ + ".post_sse", return_value=iter(())
        ) as post:
            list(provider.stream(history, []))

        _url, _headers, body = post.call_args.args
        # User → assistant (text + tool_use) → user (tool_result)
        self.assertEqual([m["role"] for m in body["messages"]], ["user", "assistant", "user"])
        assistant_blocks = body["messages"][1]["content"]
        self.assertEqual(assistant_blocks[0]["type"], "text")
        self.assertEqual(assistant_blocks[1]["type"], "tool_use")
        self.assertEqual(assistant_blocks[1]["id"], "toolu_1")
        result_block = body["messages"][2]["content"][0]
        self.assertEqual(result_block["type"], "tool_result")
        self.assertEqual(result_block["tool_use_id"], "toolu_1")


class AnthropicSSEParsingTest(unittest.TestCase):
    def _events(self):
        return iter([
            ("content_block_start", {"index": 0, "content_block": {
                "type": "tool_use", "id": "toolu_42", "name": "mesh.add_primitive",
            }}),
            ("content_block_delta", {"index": 0, "delta": {
                "type": "input_json_delta", "partial_json": "{\"type\": ",
            }}),
            ("content_block_delta", {"index": 0, "delta": {
                "type": "input_json_delta", "partial_json": "\"CUBE\"}",
            }}),
            ("content_block_stop", {"index": 0}),
            ("content_block_start", {"index": 1, "content_block": {"type": "text"}}),
            ("content_block_delta", {"index": 1, "delta": {
                "type": "text_delta", "text": "Done.",
            }}),
            ("content_block_stop", {"index": 1}),
            ("message_delta", {"delta": {"stop_reason": "tool_use"}}),
            ("message_stop", {}),
        ])

    def test_assembles_tool_call_from_input_json_deltas(self):
        provider = AnthropicProvider(
            model="claude-test", api_key="k", base_url="http://example.test",
        )
        with mock.patch(
            AnthropicProvider.__module__ + ".post_sse", return_value=self._events()
        ):
            chunks = list(provider.stream(
                [harness.Message(role="user", content="make a cube")],
                [_spec("mesh.add_primitive")],
            ))
        tool_calls = [c.tool_call for c in chunks if c.tool_call is not None]
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0].id, "toolu_42")
        self.assertEqual(tool_calls[0].name, "mesh.add_primitive")
        self.assertEqual(tool_calls[0].arguments, {"type": "CUBE"})

        finishes = [c.finish_reason for c in chunks if c.finish_reason]
        self.assertEqual(finishes, ["tool_use"])

        text_chunks = [c.delta_text for c in chunks if c.delta_text]
        self.assertEqual("".join(text_chunks), "Done.")


class OpenAIRequestShapingTest(unittest.TestCase):
    def test_tools_are_forwarded_in_function_format(self):
        provider = OpenAIProvider(
            model="gpt-test", api_key="k", base_url="http://example.test/v1",
        )
        with mock.patch(
            OpenAIProvider.__module__ + ".post_sse", return_value=iter(())
        ) as post:
            list(provider.stream(
                [harness.Message(role="user", content="hi")],
                [_spec("scene.list_objects", "read")],
            ))
        _url, _headers, body = post.call_args.args
        self.assertEqual(body["tools"][0]["type"], "function")
        self.assertEqual(body["tools"][0]["function"]["name"], "scene.list_objects")
        self.assertIn("parameters", body["tools"][0]["function"])

    def test_tool_call_history_is_serialised_as_function_arguments(self):
        provider = OpenAIProvider(
            model="gpt-test", api_key="k", base_url="http://example.test/v1",
        )
        history = [
            harness.Message(role="user", content="run a tool"),
            harness.Message(
                role="assistant", content="",
                tool_calls=[harness.ToolCall(
                    id="call_42", name="mesh.add_primitive", arguments={"type": "CUBE"},
                )],
            ),
            harness.Message(
                role="tool",
                tool_results=[harness.ToolResult(call_id="call_42", content="{\"object\": \"Cube\"}")],
            ),
        ]
        with mock.patch(
            OpenAIProvider.__module__ + ".post_sse", return_value=iter(())
        ) as post:
            list(provider.stream(history, []))
        _url, _headers, body = post.call_args.args
        roles = [m["role"] for m in body["messages"]]
        self.assertEqual(roles, ["user", "assistant", "tool"])
        assistant = body["messages"][1]
        self.assertEqual(assistant["tool_calls"][0]["id"], "call_42")
        self.assertEqual(
            json.loads(assistant["tool_calls"][0]["function"]["arguments"]),
            {"type": "CUBE"},
        )
        tool_msg = body["messages"][2]
        self.assertEqual(tool_msg["tool_call_id"], "call_42")


class OpenAISSEParsingTest(unittest.TestCase):
    def _events(self):
        return iter([
            ("message", {"choices": [{"delta": {"content": "Sure, "}}]}),
            ("message", {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "scene.select", "arguments": "{\"name\":"}},
            ]}}]}),
            ("message", {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": " \"Cube\"}"}},
            ]}}]}),
            ("message", {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        ])

    def test_assembles_streaming_tool_call(self):
        provider = OpenAIProvider(
            model="gpt-test", api_key="k", base_url="http://example.test/v1",
        )
        with mock.patch(
            OpenAIProvider.__module__ + ".post_sse", return_value=self._events()
        ):
            chunks = list(provider.stream(
                [harness.Message(role="user", content="select cube")],
                [_spec("scene.select")],
            ))
        text_chunks = [c.delta_text for c in chunks if c.delta_text]
        self.assertEqual("".join(text_chunks), "Sure, ")

        tool_calls = [c.tool_call for c in chunks if c.tool_call is not None]
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0].id, "call_1")
        self.assertEqual(tool_calls[0].name, "scene.select")
        self.assertEqual(tool_calls[0].arguments, {"name": "Cube"})

        finishes = [c.finish_reason for c in chunks if c.finish_reason]
        self.assertEqual(finishes, ["tool_use"])


if __name__ == "__main__":
    unittest.main()
