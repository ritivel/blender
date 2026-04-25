# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""HTTP transport helpers used by provider implementations.

Stays on Python's standard library so the add-on works inside Blender's
bundled interpreter without extra dependencies. SSE parsing follows the
WHATWG event-stream format closely enough for Anthropic and OpenAI.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Iterator

from .base import ProviderError


_KEY_FILE_SUBPATH = ("blender", "ai_assistant")


def resolve_api_key(env_var: str) -> str:
    """Return an API key looked up by environment variable, or via a key file.

    The key file is at ``$XDG_CONFIG_HOME/blender/ai_assistant/<env_var>``,
    or ``~/.config/blender/ai_assistant/<env_var>`` when ``XDG_CONFIG_HOME``
    is unset. The file's content is read verbatim and ``.strip()``-ed.
    Missing key returns the empty string; callers decide how to handle it.
    """
    if not env_var:
        return ""
    val = os.environ.get(env_var, "").strip()
    if val:
        return val

    cfg_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    path = os.path.join(cfg_home, *_KEY_FILE_SUBPATH, env_var)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _format_http_error(err: urllib.error.HTTPError) -> str:
    body_bytes = b""
    try:
        body_bytes = err.read() or b""
    except Exception:
        pass
    body = body_bytes.decode("utf-8", errors="replace").strip()
    detail = body
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            err_obj = parsed.get("error")
            if isinstance(err_obj, dict):
                detail = err_obj.get("message") or detail
            elif isinstance(err_obj, str):
                detail = err_obj
    except json.JSONDecodeError:
        pass
    return "HTTP {}: {}".format(err.code, detail or err.reason or "request failed")


def post_sse(
    url: str,
    headers: dict,
    body: dict,
    *,
    timeout: float = 120.0,
) -> Iterator[tuple[str, object]]:
    """POST JSON ``body`` and yield ``(event, data)`` pairs from the SSE stream.

    ``data`` is a parsed JSON object when the payload is valid JSON,
    otherwise the raw string. The ``[DONE]`` sentinel terminates the
    iterator. HTTP and network errors raise :class:`ProviderError`.
    """
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("accept", "text/event-stream")
    for key, value in headers.items():
        req.add_header(key, value)

    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as err:
        raise ProviderError(_format_http_error(err)) from err
    except urllib.error.URLError as err:
        raise ProviderError("Network error: {}".format(err.reason)) from err
    except OSError as err:
        raise ProviderError("Connection error: {}".format(err)) from err

    with resp:
        event = "message"
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                event = "message"
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event = line[6:].strip() or "message"
                continue
            if line.startswith("data:"):
                data = line[5:].lstrip()
                if data == "[DONE]":
                    return
                try:
                    yield event, json.loads(data)
                except json.JSONDecodeError:
                    yield event, data
