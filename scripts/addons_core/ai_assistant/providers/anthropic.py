# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Anthropic Messages API streaming provider.

Implements the subset needed for chat plus tool use:

* Text deltas via ``content_block_delta`` (``type == "text_delta"``).
* Tool calls via ``content_block_start`` (``type == "tool_use"``) plus
  ``content_block_delta`` (``type == "input_json_delta"``); the JSON
  fragments are buffered and parsed at ``content_block_stop``.
* ``message_delta`` carries the final ``stop_reason`` which becomes our
  ``finish_reason`` (``end_turn`` → ``stop``, ``tool_use`` → ``tool_use``,
  ``max_tokens`` → ``length``).
* Tool results are sent as ``user`` messages with a list of
  ``tool_result`` content blocks, matching Anthropic's expected shape.
"""

from __future__ import annotations

import json
from typing import Iterable, Iterator

from .. import harness
from .base import ProviderError
from .transport import post_sse


_DEFAULT_BASE_URL = "https://api.anthropic.com"
_DEFAULT_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = 4096


_STOP_REASON_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_use",
    "max_tokens": "length",
}


def _to_anthropic_messages(messages: Iterable["harness.Message"]) -> tuple[str, list[dict]]:
    """Convert harness messages into Anthropic's wire format.

    Returns ``(system_text, chat)``. ``system`` content is concatenated
    with blank-line separators because Anthropic accepts only one
    ``system`` field per request.
    """
    system_parts: list[str] = []
    chat: list[dict] = []
    for m in messages:
        if m.role == "system":
            if m.content:
                system_parts.append(m.content)
            continue
        if m.role == "user":
            if m.content:
                chat.append({"role": "user", "content": m.content})
            continue
        if m.role == "assistant":
            blocks: list[dict] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for call in m.tool_calls:
                blocks.append({
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": call.arguments or {},
                })
            if blocks:
                chat.append({"role": "assistant", "content": blocks})
            continue
        if m.role == "tool":
            # Anthropic expects tool results as a user message containing
            # one tool_result block per tool call we are answering.
            blocks = []
            for r in m.tool_results:
                block = {
                    "type": "tool_result",
                    "tool_use_id": r.call_id,
                    "content": r.content,
                }
                if r.is_error:
                    block["is_error"] = True
                blocks.append(block)
            if blocks:
                chat.append({"role": "user", "content": blocks})
            continue
    return "\n\n".join(system_parts), chat


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
        system_text, chat = _to_anthropic_messages(msgs)
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

        tool_list = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters or {"type": "object"},
            }
            for t in tools
        ]
        if tool_list:
            body["tools"] = tool_list

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.api_version,
        }
        url = self.base_url + "/v1/messages"

        # Per-content-block scratch state. ``index`` is the block index
        # Anthropic uses to disambiguate concurrent text and tool_use blocks
        # within a single assistant turn.
        blocks: dict[int, dict] = {}
        finish_reason: str | None = None

        try:
            for event, data in post_sse(url, headers, body):
                if not isinstance(data, dict):
                    continue
                if event == "content_block_start":
                    index = data.get("index")
                    block = data.get("content_block") or {}
                    blocks[index] = {
                        "type": block.get("type"),
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "input_buf": "",
                    }
                elif event == "content_block_delta":
                    index = data.get("index")
                    delta = data.get("delta") or {}
                    state = blocks.get(index)
                    if state is None:
                        continue
                    if delta.get("type") == "text_delta":
                        text = delta.get("text") or ""
                        if text:
                            yield harness.StreamChunk(delta_text=text)
                    elif delta.get("type") == "input_json_delta":
                        state["input_buf"] += delta.get("partial_json") or ""
                elif event == "content_block_stop":
                    index = data.get("index")
                    state = blocks.pop(index, None)
                    if state is None or state.get("type") != "tool_use":
                        continue
                    raw = state.get("input_buf") or ""
                    try:
                        arguments = json.loads(raw) if raw else {}
                    except json.JSONDecodeError:
                        arguments = {}
                    yield harness.StreamChunk(
                        tool_call=harness.ToolCall(
                            id=state.get("id") or "",
                            name=state.get("name") or "",
                            arguments=arguments if isinstance(arguments, dict) else {},
                        )
                    )
                elif event == "message_delta":
                    delta = data.get("delta") or {}
                    stop_reason = delta.get("stop_reason")
                    if stop_reason:
                        finish_reason = _STOP_REASON_MAP.get(stop_reason, "stop")
                elif event == "message_stop":
                    yield harness.StreamChunk(
                        finish_reason=finish_reason or "stop",
                    )
                    return
                elif event == "error":
                    err = data.get("error") or {}
                    yield harness.StreamChunk(
                        error=err.get("message") or "Anthropic returned an error event."
                    )
                    return
        except ProviderError as err:
            yield harness.StreamChunk(error=str(err))
