# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Permission gating for AI Assistant tool calls (Step 4 of the plan).

The gate sits between the agent loop and the actual tool execution. It
mirrors the shape of Claude Code's tool-call confirmation popup:

* Each tool call produces a *decision* — :data:`ALLOW`, :data:`DENY`,
  or :data:`PROMPT`.
* :data:`PROMPT` decisions are resolved by a modal popup that offers
  four choices: allow once, allow for the session, allow for the
  project (persisted in the .blend file), or deny.

Decisions combine four inputs, in priority order:

1. **Permission mode** in the add-on preferences. ``deny`` short-
   circuits everything to :data:`DENY`. ``always`` short-circuits
   write/exec calls to :data:`ALLOW`.
2. **Tool permission class**. ``read`` tools are auto-allowed: they
   cannot mutate the .blend file by definition, so prompting for them
   would just be noise.
3. **Per-project trust**. The user can trust a tool for the duration
   of a .blend file. The trust list is stored in the per-scene
   ``ai_assistant.trusted_tools`` collection and persists with the
   file.
4. **Per-session trust**. In ``session`` mode, the popup's *Allow for
   session* button adds the tool to a process-local set that is
   forgotten on Blender restart.

The actual modal popup lives in :mod:`operators` so it can use Blender
operator infrastructure; this module is pure Python and is exercised
by ``tests/test_permissions.py`` without a Blender host.
"""

from __future__ import annotations


# Gate output (consumed by the agent loop).
ALLOW = "allow"
DENY = "deny"
PROMPT = "prompt"

GATE_OUTCOMES = (ALLOW, DENY, PROMPT)

# Popup output (consumed by :func:`apply_decision`).
DECISION_ONCE = "once"
DECISION_SESSION = "session"
DECISION_ALWAYS = "always"
DECISION_DENY = "deny"

DECISIONS = (DECISION_ONCE, DECISION_SESSION, DECISION_ALWAYS, DECISION_DENY)


# Process-local set of tool names trusted for the rest of the session.
# Mirrors Claude Code's "yes, and don't ask again this session" choice.
_session_trusted: set[str] = set()


def trust_for_session(tool_name: str) -> None:
    """Trust ``tool_name`` for the rest of this Blender session."""
    _session_trusted.add(tool_name)


def revoke_session_trust(tool_name: str) -> None:
    _session_trusted.discard(tool_name)


def is_trusted_for_session(tool_name: str) -> bool:
    return tool_name in _session_trusted


def session_trusted_tools() -> list[str]:
    return sorted(_session_trusted)


def clear_session() -> None:
    """Forget every session-trusted tool. Called on add-on reload."""
    _session_trusted.clear()


def trust_for_project(scene, tool_name: str) -> None:
    """Add ``tool_name`` to the per-project trusted set on ``scene``.

    The trust list lives on ``scene.ai_assistant.trusted_tools`` and
    persists with the .blend file. No-op if the session property is
    missing (e.g. add-on disabled).
    """
    session = getattr(scene, "ai_assistant", None)
    if session is None:
        return
    for entry in session.trusted_tools:
        if entry.name == tool_name:
            return
    entry = session.trusted_tools.add()
    entry.name = tool_name


def revoke_project_trust(scene, tool_name: str) -> None:
    session = getattr(scene, "ai_assistant", None)
    if session is None:
        return
    for i, entry in enumerate(session.trusted_tools):
        if entry.name == tool_name:
            session.trusted_tools.remove(i)
            return


def is_trusted_for_project(scene, tool_name: str) -> bool:
    session = getattr(scene, "ai_assistant", None)
    if session is None:
        return False
    return any(entry.name == tool_name for entry in session.trusted_tools)


def project_trusted_tools(scene) -> list[str]:
    session = getattr(scene, "ai_assistant", None)
    if session is None:
        return []
    return [entry.name for entry in session.trusted_tools]


def decide(
    permission_mode: str,
    scene,
    tool_name: str,
    tool_permission: str,
) -> str:
    """Resolve the gate to one of :data:`ALLOW`, :data:`DENY`, :data:`PROMPT`.

    ``permission_mode`` is the global mode from add-on preferences
    (``ask`` / ``session`` / ``always`` / ``deny``). ``tool_permission``
    is the tool's declared class (``read`` / ``write`` / ``exec``).
    """
    if permission_mode == "deny":
        return DENY
    if tool_permission == "read":
        return ALLOW
    if permission_mode == "always":
        return ALLOW
    if is_trusted_for_project(scene, tool_name):
        return ALLOW
    if permission_mode == "session" and is_trusted_for_session(tool_name):
        return ALLOW
    return PROMPT


def apply_decision(scene, tool_name: str, decision: str) -> str:
    """Persist a popup decision and return the resulting action.

    Returns :data:`ALLOW` if the call should run, :data:`DENY`
    otherwise. ``decision`` must be one of :data:`DECISIONS`; anything
    else is treated as :data:`DECISION_DENY` so a malformed popup
    response fails closed.
    """
    if decision == DECISION_SESSION:
        trust_for_session(tool_name)
        return ALLOW
    if decision == DECISION_ALWAYS:
        trust_for_project(scene, tool_name)
        return ALLOW
    if decision == DECISION_ONCE:
        return ALLOW
    return DENY


def denial_message(tool_name: str, permission_mode: str) -> str:
    """Render a short tool-result message describing why a call was denied.

    Sent back to the model so it can adapt instead of retrying the
    same call. Mirrors the "Permission denied" surface in Claude Code.
    """
    if permission_mode == "deny":
        return (
            "Tool {!r} was denied: the user has set Tool Permissions to 'Deny all'."
        ).format(tool_name)
    return (
        "Tool {!r} was denied by the user. Do not retry it; "
        "explain what you would have done instead, or ask for guidance."
    ).format(tool_name)
