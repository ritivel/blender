# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Bundled tool implementations for the AI Assistant.

Each submodule defines a ``register(registry)`` function that adds its
tools to the given :class:`harness.ToolRegistry`. The umbrella
:func:`register_default_tools` registers all of them at once and is what
:func:`harness.default_registry` calls on first use.

Tools are intentionally split by concern so future steps can extend
individual surfaces without churn:

* ``scene``     — read-only scene introspection.
* ``mesh``      — reversible mesh creation primitives.
* ``transform`` — translate / rotate / scale on objects.
* ``system``    — gated escape hatches: raw operator and Python eval.
* ``viewport``  — render the active 3D view to disk so the model can see.
"""

from __future__ import annotations

from . import scene as _scene
from . import mesh as _mesh
from . import transform as _transform
from . import system as _system
from . import viewport as _viewport


def register_default_tools(registry) -> None:
    _scene.register(registry)
    _mesh.register(registry)
    _transform.register(registry)
    _system.register(registry)
    _viewport.register(registry)


__all__ = ("register_default_tools",)
