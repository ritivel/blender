# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Anthropic Messages API streaming provider.

Implements the subset needed for chat completion: text deltas in
``content_block_delta`` events and ``message_stop`` to finish. Tool use
is wired up in step 3.
"""

from __future__ import annotations

from typing import Iterable, Iterator

from .. import harness
from .base import ProviderError
from .transport import post_sse


_DEFAULT_BASE_URL = "https://api.anthropic.com"
_DEFAULT_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = 4096


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        api_version: str = _DEFAULT_VERSION,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version
        self.max_tokens = max_tokens

    def stream(
        self,
        messages: Iterable["harness.Message"],
        tools: Iterable["harness.ToolSpec"],
    ) -> Iterator["harness.StreamChunk"]:
        msgs = list(messages)
        system_text = "\n\n".join(m.content for m in msgs if m.role == "system" and m.content)
        chat = [
            {"role": m.role, "content": m.content}
            for m in msgs
            if m.role in ("user", "assistant") and m.content
        ]
        if not chat:
            yield harness.StreamChunk(error="No user message to send.")
            return

        body: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "stream": True,
            "messages": chat,
        }
        if system_text:
            body["system"] = system_text

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.api_version,
        }
        url = self.base_url + "/v1/messages"

        try:
            for event, data in post_sse(url, headers, body):
                if event == "content_block_delta" and isinstance(data, dict):
                    delta = data.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text = delta.get("text") or ""
                        if text:
                            yield harness.StreamChunk(delta_text=text)
                elif event == "message_stop":
                    return
                elif event == "error" and isinstance(data, dict):
                    err = data.get("error") or {}
                    yield harness.StreamChunk(
                        error=err.get("message") or "Anthropic returned an error event."
                    )
                    return
        except ProviderError as err:
            yield harness.StreamChunk(error=str(err))
