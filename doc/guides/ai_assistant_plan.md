# Blender AI Assistant — Plan

This document captures the multi-step plan to turn Blender into an AI-agent
platform comparable to Claude Code, OpenAI Codex CLI, GitHub Copilot, and
Cursor — but designed natively for a 3D DCC environment instead of a code
editor.

The goal is for an end user to **open Blender, click an "AI" tab in the
sidebar (or, eventually, a dedicated AI editor space), chat with an
assistant, and have the assistant safely drive Blender via tools (bpy
operators, scene introspection, geometry nodes, generated Python) under a
permission model the user controls**.

Only **Step 1** of this plan is implemented in the commit that introduces
this document. Subsequent steps are intentionally deferred so each lands
as a small, reviewable change.

---

## Background — what we are imitating

These notes summarize what we learned about existing agent harnesses and
how that maps onto Blender:

- **Claude Code** (Anthropic CLI). The product lives in the *harness*, not
  the model. ~19 permission-gated tools (file I/O, shell exec, git, web),
  a primary system prompt plus tool-specific and sub-agent prompts, and
  modes like "kairos" (persistent background agent) and "dream" (idle
  reasoning). The harness is mostly a tight agent loop with strong
  permission UX and well-shaped tool schemas.
- **OpenAI Codex CLI** (open source, Rust). A formal *App Server* protocol
  separates the agent core from client surfaces (CLI, VS Code, web). The
  protocol is built around `Item` (atomic input/output unit), `Turn`
  (group of items for one unit of work), and `Thread` (durable session).
  Tools extend through the Model Context Protocol (MCP).
- **Cursor** (IDE). Composer-style workflow: explore → plan → execute,
  with up to 8 parallel cloud agents, async sub-agents, and an
  agent-management console as the primary UI in Cursor 3.
- **GitHub Copilot**. In-editor inline suggestions plus an agent mode
  using tool calls; recent versions bring sub-agents and MCP tools.

The recurring shape: **agent loop + typed tools + permission model +
durable sessions + provider abstraction + extension via MCP**.

---

## Mapping that to Blender

A 3D DCC has analogues for almost everything a code agent needs:

| Code agent concept | Blender analogue |
| --- | --- |
| Repository / files | `.blend` data-blocks (objects, meshes, materials, nodes, …) |
| Read file | Scene/datablock introspection via `bpy.data` |
| Write file / edit | `bpy.ops.*` operators + direct RNA writes |
| Run shell | Run Python in a sandboxed namespace |
| Diff / patch | Undo step, before/after datablock snapshot |
| Test | Render preview / geometry-validation script |
| Lint | RNA validation, operator poll() failures |
| MCP | Add-on contributed tools and asset-library bridges |

The agent's *tools* are operators, scripted Python, and read-only
introspection helpers — exactly what the existing Python API already
exposes. We mostly need the harness, the UI, and the permission model.

---

## Step plan

Each step is intended to be small and shippable on its own. Each later
step depends on the previous one but does not block it.

### Step 1 — Bundled add-on scaffold (this commit)

Add a new bundled add-on `scripts/addons_core/ai_assistant/` that
introduces:

- N-panel tab **"AI"** in the 3D Viewport sidebar with a chat history,
  input field, **Send** and **Clear** buttons.
- Per-scene chat storage (`Scene.ai_assistant_*` props + a `ChatMessage`
  PropertyGroup collection).
- Add-on **Preferences** for provider (Anthropic / OpenAI / Local),
  model id, base URL, API-key env var name, system prompt, and a global
  permission mode.
- A `harness.py` module that defines the *agent loop interface* and
  ships with a single in-process `EchoProvider` so the UI is testable
  without network access.
- A `tools/` package with a `ToolRegistry` skeleton — no real tools yet,
  just the type definitions and a `noop` tool for testing.
- Add-on registered automatically on factory defaults via
  `BKE_blendfile_userdef_from_defaults` so a fresh install shows the
  AI tab out of the box.

What this **does not** do yet (intentional):

- No real network calls.
- No bpy-operator-as-tool execution.
- No streaming.
- No background / parallel agents.
- No new C-level editor space.

### Step 2 — Real provider clients + streaming

Implement HTTP providers (Anthropic Messages API, OpenAI Responses /
Chat Completions API, optional OpenAI-compatible local URL). Run requests
on a worker thread; surface incremental output via `bpy.app.timers`
into the chat panel. Honour the `XDG_*` / `~/.config/blender` paths for
API-key files. Map provider errors to user-visible messages.

### Step 3 — Tool harness ("Atelier")

Define the typed tool interface (name, JSON-schema params, permission
class, callable). Ship the first tool set:

- `scene.list_objects`, `scene.get_object`, `scene.select`
- `mesh.add_primitive`, `transform.translate/rotate/scale`
- `bpy.run_operator` (gated, allowlisted)
- `python.eval_in_sandbox` (gated)
- `viewport.screenshot` for the model to "see"

Wire the agent loop to call tools, append tool results to the
conversation, and loop until the model emits a final answer or a
hard-stop is reached.

### Step 4 — Permission model

Per-tool permission classes: `read`, `write`, `exec`. Modes:
`ask-each-time`, `allow-for-session`, `allow-always`, `deny`. Modal
permission popup matches Claude Code's tool-call gate. Per-project
"trusted scopes" stored alongside the .blend file.

### Step 5 — Background / parallel agents

Persistent `kairos`-style background worker (idle-time tasks: "rename
inconsistent objects", "suggest material cleanups"). Allow N parallel
chat threads, each with its own Thread (Codex-style durable session)
serialized to a per-blend-file JSON in the scene's text data-block.

### Step 6 — MCP-style extensibility

Allow other Blender add-ons to *contribute tools and prompts* via a
documented Python entry-point (`ai_assistant.register_tool(...)`).
Optional out-of-process MCP server bridge so existing MCP servers
(filesystem, fetch, github, …) can be reused.

### Step 7 — First-class AI editor space

Promote the N-panel into a dedicated `SPACE_AI` editor at the C level
(new entry in `eSpace_Type`, file under `source/blender/editors/space_ai/`
mirroring the `space_text` layout). The N-panel stays as a quick-access
surface; the editor space is the full agent console (Cursor 3 style).

### Step 8 — Distribution

- Update `release/darwin/` packaging notes; the add-on lives under
  `Blender.app/Contents/Resources/<ver>/scripts/addons_core/ai_assistant`
  and is included automatically by the existing CMake install rules.
- Add a CI knob to the Mac buildbot that signs/notarizes the resulting
  `.dmg` (no change required for the add-on itself — packaging
  inherits the existing pipeline).

---

## Building a macOS `.dmg` from this branch

A macOS host with Xcode is required (the `.dmg` is produced by the
Apple toolchain; cross-builds are not supported). On a Mac:

```bash
git clone <fork-url> blender
cd blender
git checkout claude/blender-ai-assistant-TBGJH
git submodule update --init --recursive
make update                    # fetch precompiled libs into ../lib/
make release                   # full release build
# bundle + dmg:
cmake --build ../build_darwin --target install --config Release
./build_files/utils/make_bundle_apple.sh ../build_darwin
# result: ../build_darwin/blender-<version>-darwin-<arch>.dmg
```

The bundled add-on requires no extra build flags — once the source tree
contains `scripts/addons_core/ai_assistant/`, CMake's existing
`scripts/addons_core` install glob copies it into the `.app` bundle and
the new entry in `BKE_blendfile_userdef_from_defaults` ensures it is
enabled on first launch.

For an unsigned development `.dmg`, omit notarization. For a
distributable build, follow `release/darwin/README.md` for codesign
identities and `notarytool` invocation.
