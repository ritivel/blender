# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Offline echo provider, used when no real provider is configured."""

from __future__ import annotations

from typing import Iterable, Iterator

from .. import harness


class EchoProvider:
    """Streams a canned acknowledgement plus the user's last message.

    The provider exists so the chat UI is fully exercisable without
    network access. ``note`` lets :func:`providers.build` surface a
    configuration hint (e.g. missing API key) inline.
    """

    name = "echo"

    def __init__(self, note: str = ""):
        self._note = note

    def stream(
        self,
        messages: Iterable["harness.Message"],
        tools: Iterable["harness.ToolSpec"],
    ) -> Iterator["harness.StreamChunk"]:
        last_user = next(
            (m for m in reversed(list(messages)) if m.role == "user"),
            None,
        )
        text = "(echo provider — offline)\n"
        if self._note:
            text += self._note + "\n\n"
        text += "I received: {!r}\n".format(last_user.content if last_user else "")
        text += (
            "Configure a real provider in Edit > Preferences > Add-ons > AI Assistant "
            "to send this conversation to Anthropic or OpenAI."
        )
        # Stream word-by-word so the UI exercises the same code path as a real provider.
        for token in text.split(" "):
            yield harness.StreamChunk(delta_text=token + " ")
