# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Provider implementations for the AI Assistant.

Each provider turns a list of :class:`harness.Message` into a stream of
:class:`harness.StreamChunk` objects. The :func:`build` factory dispatches
on the add-on preference's ``provider`` field.
"""

from .base import ProviderError
from .echo import EchoProvider
from .anthropic import AnthropicProvider
from .openai import OpenAIProvider

__all__ = (
    "ProviderError",
    "EchoProvider",
    "AnthropicProvider",
    "OpenAIProvider",
    "build",
)


def build(prefs):
    """Construct the provider selected in add-on preferences.

    Falls back to :class:`EchoProvider` if ``prefs`` is ``None`` or the
    selected real provider has no API key configured. The fallback also
    yields a single error chunk so the user sees why the model did not
    answer.
    """
    if prefs is None:
        return EchoProvider()

    name = prefs.provider
    if name == "echo":
        return EchoProvider()

    from .transport import resolve_api_key

    api_key = resolve_api_key(prefs.api_key_env)

    if name == "anthropic":
        if not api_key:
            return EchoProvider(
                note="No API key found. Set ${} or write the key to "
                "$XDG_CONFIG_HOME/blender/ai_assistant/{}.".format(
                    prefs.api_key_env, prefs.api_key_env
                ),
            )
        return AnthropicProvider(
            model=prefs.model,
            api_key=api_key,
            max_tokens=prefs.max_tokens,
        )

    if name == "openai":
        if not api_key:
            return EchoProvider(
                note="No API key found. Set ${} or write the key to "
                "$XDG_CONFIG_HOME/blender/ai_assistant/{}.".format(
                    prefs.api_key_env, prefs.api_key_env
                ),
            )
        return OpenAIProvider(
            model=prefs.model,
            api_key=api_key,
            max_tokens=prefs.max_tokens,
        )

    if name == "custom":
        base_url = prefs.base_url.strip() or "http://localhost:8080/v1"
        return OpenAIProvider(
            model=prefs.model,
            api_key=api_key or "sk-local",
            base_url=base_url,
            max_tokens=prefs.max_tokens,
        )

    return EchoProvider(note="Unknown provider {!r}".format(name))
