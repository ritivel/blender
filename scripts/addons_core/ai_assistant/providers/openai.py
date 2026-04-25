# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""OpenAI / OpenAI-compatible Chat Completions streaming provider.

Speaks the ``/chat/completions`` SSE protocol. The same client serves
self-hosted endpoints (vLLM, llama.cpp server, LM Studio, …) when the
``custom`` provider is selected with a non-default ``base_url``.
"""

from __future__ import annotations

from typing import Iterable, Iterator

from .. import harness
from .base import ProviderError
from .transport import post_sse


_DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        max_tokens: int = 4096,
    ):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens

    def stream(
        self,
        messages: Iterable["harness.Message"],
        tools: Iterable["harness.ToolSpec"],
    ) -> Iterator["harness.StreamChunk"]:
        chat = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("system", "user", "assistant") and m.content
        ]
        if not chat:
            yield harness.StreamChunk(error="No user message to send.")
            return

        body = {
            "model": self.model,
            "messages": chat,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        headers = {"authorization": "Bearer " + self.api_key}
        url = self.base_url + "/chat/completions"

        try:
            for _event, data in post_sse(url, headers, body):
                if not isinstance(data, dict):
                    continue
                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if text:
                    yield harness.StreamChunk(delta_text=text)
                if choices[0].get("finish_reason"):
                    return
        except ProviderError as err:
            yield harness.StreamChunk(error=str(err))
