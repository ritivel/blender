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

**Status:** Steps 1 (scaffold), 2 (real streaming providers), and 3
(typed tool harness with the first tool set) are implemented.
Subsequent steps are intentionally deferred so each lands as a small,
reviewable change.

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

### Step 2 — Real provider clients + streaming  *(landed)*

A new `providers/` subpackage adds:

* `AnthropicProvider` — Messages API SSE; parses `content_block_delta`
  text deltas and stops on `message_stop`.
* `OpenAIProvider` — `/chat/completions` SSE; assembles `choices[0].delta.content`
  until `finish_reason` arrives. The same client serves the
  `OpenAI-compatible` provider (custom `base_url`).
* `EchoProvider` — offline fallback that streams a canned reply word by
  word; also used when a real provider is selected but no API key is
  configured (the reason is surfaced inline).
* `transport.post_sse` — stdlib-only SSE POST. HTTP errors return a
  `ProviderError` with the API's own error message extracted from the
  response body.
* `transport.resolve_api_key` — `os.environ[<env-var>]` then
  `$XDG_CONFIG_HOME/blender/ai_assistant/<env-var>` fallback.

The send operator is now non-blocking: a worker thread iterates
`provider.stream(...)` and pushes `StreamChunk` objects onto a queue;
a `bpy.app.timers` callback drains the queue on the main thread,
appends deltas to the placeholder assistant message, and calls
`area.tag_redraw()`. A **Stop** operator sets a `threading.Event` the
worker checks between chunks.

Tested without a Blender host via a mock SSE server: Anthropic and
OpenAI streams assemble correctly end-to-end, HTTP errors surface as
chunked errors, and the missing-key path falls back to `EchoProvider`
with a configuration hint.

### Step 3 — Tool harness ("Atelier") *(landed)*

The harness now defines a typed tool interface (`name`, JSON-schema
`parameters`, `permission` class, `run` callable) and ships the first
tool set under `scripts/addons_core/ai_assistant/tools/`:

- `scene.list_objects`, `scene.get_object`, `scene.select`
- `mesh.add_primitive`, `transform.translate/rotate/scale`
- `bpy.run_operator` (gated, allowlisted to a fixed namespace set —
  no `wm.*`, no `preferences.*`, no add-on installation)
- `python.eval_in_sandbox` (gated, restricted namespace: only
  curated builtins, only `bpy.data` and `bpy.context` access, no
  imports, no dunders)
- `viewport.screenshot` so the model can "see" — writes a PNG to a
  fresh path under `tempfile.gettempdir()`

The send operator now drives a *multi-step* agent loop:

1. Worker thread streams provider output (text deltas + tool-call
   chunks) onto a queue.
2. A `bpy.app.timers` callback drains the queue on the main thread.
3. When the provider emits ``finish_reason == "tool_use"``, the timer
   executes each pending tool **on the main thread** (so it can
   safely touch `bpy.data` / `bpy.context`), appends a tool log
   entry to the chat, and spawns a fresh worker that resumes the
   conversation with the tool results.
4. Loop until the model emits ``finish_reason == "stop"`` or the
   per-turn step cap is reached.

Permission gating is provisional: this step honours `deny` (no tools
exposed), `always` (all tools), and treats `ask` / `session` as
*read-only* (only tools with `permission == "read"` are forwarded to
the model). The full modal popup arrives in step 4.

Both providers were extended:

* **Anthropic** — declares `tools` in the request body, parses
  `content_block_start`/`input_json_delta`/`content_block_stop` for
  tool-use blocks, and round-trips `tool_use` + `tool_result` content
  blocks across turns.
* **OpenAI / OpenAI-compatible** — declares OpenAI-style
  `function`-typed tools, assembles streamed `tool_calls[].function.arguments`
  fragments, emits one `StreamChunk` per call, and round-trips
  `tool_calls` / `role: "tool"` messages.

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
