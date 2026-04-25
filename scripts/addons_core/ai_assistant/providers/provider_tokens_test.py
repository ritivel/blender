#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

import importlib
import pathlib
import sys
import types
import unittest
from dataclasses import dataclass
from unittest import mock


if __package__:
    from . import AnthropicProvider, OpenAIProvider, build
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
    build = providers.build


@dataclass
class MockPreferences:
    provider: str
    model: str = "test-model"
    api_key_env: str = "TEST_API_KEY"
    base_url: str = ""
    max_tokens: int = 1234


def mock_api_key(value):
    return mock.patch(build.__module__ + ".transport.resolve_api_key", return_value=value)


class ProviderMaxTokensTest(unittest.TestCase):
    def test_build_forwards_max_tokens_to_anthropic_provider(self):
        prefs = MockPreferences(provider="anthropic", max_tokens=2048)

        with mock_api_key("test-key"):
            provider = build(prefs)

        self.assertIsInstance(provider, AnthropicProvider)
        self.assertEqual(provider.max_tokens, 2048)

    def test_build_forwards_max_tokens_to_openai_provider(self):
        prefs = MockPreferences(provider="openai", max_tokens=8192)

        with mock_api_key("test-key"):
            provider = build(prefs)

        self.assertIsInstance(provider, OpenAIProvider)
        self.assertEqual(provider.max_tokens, 8192)

    def test_build_forwards_max_tokens_to_custom_provider(self):
        prefs = MockPreferences(
            provider="custom",
            base_url="http://localhost:8080/v1",
            max_tokens=1024,
        )

        with mock_api_key(""):
            provider = build(prefs)

        self.assertIsInstance(provider, OpenAIProvider)
        self.assertEqual(provider.max_tokens, 1024)

    def test_openai_request_body_includes_max_tokens(self):
        provider = OpenAIProvider(
            model="gpt-test",
            api_key="test-key",
            base_url="http://example.test/v1",
            max_tokens=256,
        )
        message = harness.Message(role="user", content="hello")

        with mock.patch(OpenAIProvider.__module__ + ".post_sse", return_value=iter(())) as post_sse:
            list(provider.stream([message], []))

        _url, _headers, body = post_sse.call_args.args
        self.assertEqual(body["max_tokens"], 256)


if __name__ == "__main__":
    unittest.main()
