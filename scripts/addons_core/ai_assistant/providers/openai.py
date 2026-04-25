# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""OpenAI / OpenAI-compatible Chat Completions streaming provider.

Speaks the ``/chat/completions`` SSE protocol. The same client serves
self-hosted endpoints (vLLM, llama.cpp server, LM Studio, …) when the
``custom`` provider is selected with a non-default ``base_url``.

Tool-call streaming follows OpenAI's incremental ``tool_calls`` deltas:
each delta carries an ``index`` plus optional ``id``, ``function.name``,
and ``function.arguments`` (JSON string fragment). We buffer per-index,
parse the final JSON when ``finish_reason == "tool_calls"`` arrives,
and emit one :class:`harness.StreamChunk` per tool call.
"""

from __future__ import annotations

import json
from typing import Iterable, Iterator

from .. import harness
from .base import ProviderError
from .transport import post_sse


_DEFAULT_BASE_URL = "https://api.openai.com/v1"


_FINISH_REASON_MAP = {
    "stop": "stop",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "length",
    "content_filter": "stop",
}


def _to_openai_messages(messages: Iterable["harness.Message"]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        if m.role == "system" and m.content:
            out.append({"role": "system", "content": m.content})
        elif m.role == "user" and m.content:
            out.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            entry: dict = {"role": "assistant", "content": m.content or ""}
            if m.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {
                            "name": c.name,
                            "arguments": json.dumps(c.arguments or {}),
                        },
                    }
                    for c in m.tool_calls
                ]
            out.append(entry)
        elif m.role == "tool":
            for r in m.tool_results:
                out.append({
                    "role": "tool",
                    "tool_call_id": r.call_id,
                    "content": r.content,
                })
    return out


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
        chat = _to_openai_messages(messages)
        if not chat:
            yield harness.StreamChunk(error="No user message to send.")
            return

        body: dict = {
            "model": self.model,
            "messages": chat,
            "max_tokens": self.max_tokens,
            "stream": True,
        }

        tool_list = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters or {"type": "object"},
                },
            }
            for t in tools
        ]
        if tool_list:
            body["tools"] = tool_list

        headers = {"authorization": "Bearer " + self.api_key}
        url = self.base_url + "/chat/completions"

        # Tool-call buffer. Indexed by the integer ``index`` field of
        # each tool_calls delta; OpenAI may interleave deltas for
        # multiple parallel calls, so we cannot reduce to a single buffer.
        tool_buf: dict[int, dict] = {}

        def _emit_buffered_tool_calls() -> Iterator["harness.StreamChunk"]:
            for idx in sorted(tool_buf.keys()):
                state = tool_buf[idx]
                raw = state.get("arguments") or ""
                try:
                    arguments = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    arguments = {}
                yield harness.StreamChunk(tool_call=harness.ToolCall(
                    id=state.get("id") or "",
                    name=state.get("name") or "",
                    arguments=arguments if isinstance(arguments, dict) else {},
                ))

        try:
            for _event, data in post_sse(url, headers, body):
                if not isinstance(data, dict):
                    continue
                choices = data.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}

                text = delta.get("content")
                if text:
                    yield harness.StreamChunk(delta_text=text)

                for call_delta in delta.get("tool_calls") or []:
                    if not isinstance(call_delta, dict):
                        continue
                    idx = call_delta.get("index", 0)
                    state = tool_buf.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if call_delta.get("id"):
                        state["id"] = call_delta["id"]
                    function = call_delta.get("function") or {}
                    if function.get("name"):
                        state["name"] = function["name"]
                    if function.get("arguments"):
                        state["arguments"] += function["arguments"]

                finish_reason = choice.get("finish_reason")
                if finish_reason:
                    if tool_buf:
                        for chunk in _emit_buffered_tool_calls():
                            yield chunk
                        tool_buf.clear()
                    yield harness.StreamChunk(
                        finish_reason=_FINISH_REASON_MAP.get(finish_reason, "stop"),
                    )
                    return
        except ProviderError as err:
            yield harness.StreamChunk(error=str(err))
