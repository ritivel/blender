# AI Assistant — Bundled Add-on

This bundled add-on implements the user-facing AI surface for Blender,
following the plan in
[`doc/guides/ai_assistant_plan.md`](../../../doc/guides/ai_assistant_plan.md).

Steps **1** (scaffold), **2** (real streaming providers), **3**
(typed tool harness with the first tool set), and **4** (per-call
permission gate with session and per-project trust) have landed.

## What ships now

* **N-panel "AI"** in the 3D Viewport sidebar — chat list, draft input,
  **Send** / **Stop** / **Clear**, plus a collapsible **Quick Prompts**
  sub-panel.
* **Preferences** (Edit > Preferences > Add-ons > AI Assistant) — provider,
  model, base URL, API-key env var, max tokens, system prompt, and a
  global tool-permission mode.
* **Streaming providers** (step 2):
    * **Anthropic** — Messages API SSE (`content_block_delta` text deltas
      plus `tool_use` content blocks; round-trips `tool_use` + `tool_result`
      blocks across turns).
    * **OpenAI** — `/chat/completions` SSE with streamed `tool_calls`
      assembled per index.
    * **OpenAI-compatible** — same client, custom `base_url` for self-hosted
      endpoints (vLLM, llama.cpp server, LM Studio, …).
    * **Echo** — offline fallback used when no key is configured; also
      surfaces *why* the real provider was skipped.
* **Tool harness** (step 3):
    * Typed `ToolSpec` (name, JSON-schema parameters, permission class,
      callable). Read / write / exec permission classes.
    * Bundled tool set in `tools/`:
      `scene.list_objects`, `scene.get_object`, `scene.select`,
      `mesh.add_primitive`, `transform.translate/rotate/scale`,
      `bpy.run_operator` (allowlisted), `python.eval_in_sandbox`
      (restricted namespace), `viewport.screenshot`.
    * Multi-step agent loop: provider streams text + tool calls,
      tools run on the main thread, results flow back into the
      conversation, loop continues until the model finishes or a
      hard-stop limit is reached.
* **Per-call permission gate** (step 4):
    * Every non-`read` tool call goes through
      `permissions.decide(...)` before it runs.
    * `Ask each time` mode opens the modal **AI Assistant — Confirm
      Tool** popup with four options (mirrors Claude Code):
      *Allow once*, *Allow for this session*, *Always for this
      project*, *Deny*.
    * **Session trust** is process-local; **project trust** is saved
      with the .blend file on `Scene.ai_assistant.trusted_tools`.
    * Pressing **Stop** while a popup is open resolves it as a
      denial so the loop can unwind.
    * The full tool catalogue is now advertised to the model in any
      non-`Deny all` mode (gating is per-call, not list-filter).
* **Threaded request loop** — provider calls run on a worker thread; chunks
  are drained on the main thread by a `bpy.app.timers` callback that updates
  the chat message in place and calls `area.tag_redraw()`. Pressing **Stop**
  signals the worker via a `threading.Event`.
* **API-key resolution** — `os.environ[<env-var>]` first, then a key file at
  `$XDG_CONFIG_HOME/blender/ai_assistant/<env-var>` (defaults to
  `~/.config/blender/ai_assistant/<env-var>`). Keys are never written to the
  `.blend` file.
* **Default-enabled** on factory startup via the entry added in
  `BKE_blendfile_userdef_from_defaults`.

## What does **not** ship yet

* No background / parallel agents (step 5).
* No MCP bridge (step 6).
* No dedicated AI editor space at the C level (step 7).

## Trying it out

After building Blender from this branch:

1. Set an API key, e.g. `export ANTHROPIC_API_KEY=sk-...` before launching
   Blender, or write the key to
   `~/.config/blender/ai_assistant/ANTHROPIC_API_KEY`.
2. Launch Blender.
3. *Edit > Preferences > Add-ons > AI Assistant* — choose **Anthropic** (or
   **OpenAI** / **OpenAI-compatible**) and set the model id (e.g.
   `claude-sonnet-4-6`, `gpt-4.1`).
4. In the 3D Viewport, press `N` and click the **AI** tab.
5. Type a message and press **Send**. The reply streams in token-by-token;
   press **Stop** at any time to cancel.

If no key is configured, the offline **Echo** provider runs and the reply
spells out which env var / key file path it looked for.

## macOS `.dmg`

A macOS host with Xcode is required. From a Mac:

```bash
git clone <fork-url> blender
cd blender
git checkout claude/blender-ai-assistant-TBGJH
git submodule update --init --recursive
make update                                    # fetches precompiled libs
make release                                   # release build
cmake --build ../build_darwin --target install --config Release
./build_files/utils/make_bundle_apple.sh ../build_darwin
```

The resulting `.dmg` lives at
`../build_darwin/blender-<version>-darwin-<arch>.dmg`.

This add-on requires no extra build flags — the existing `addons_core`
install rule copies it into `Blender.app/Contents/Resources/<ver>/scripts/
addons_core/ai_assistant/`, and the entry in
`BKE_blendfile_userdef_from_defaults` enables it on first launch.

## Layout

```
ai_assistant/
├── __init__.py            bl_info, register / unregister
├── preferences.py         AddonPreferences (provider, model, key, max-tokens, prompt, perms)
├── properties.py          Per-scene chat session + message PropertyGroup (user/assistant/system/tool roles)
├── operators.py           Send / Stop / Clear / SetDraft / PermissionPrompt / RevokeTrust + multi-step gated agent loop
├── ui.py                  N-panel "AI" + Quick Prompts + Trusted Tools sub-panels
├── harness.py             Provider / StreamChunk / ToolSpec / ToolCall / ToolRegistry
├── permissions.py         Per-call decision gate + session/project trust
├── providers/
│   ├── __init__.py        build(prefs) factory + exports
│   ├── base.py            ProviderError
│   ├── transport.py       SSE POST + key-file fallback (stdlib only)
│   ├── echo.py            Offline EchoProvider
│   ├── anthropic.py       AnthropicProvider (Messages API SSE + tool_use)
│   └── openai.py          OpenAIProvider (chat/completions SSE + tool_calls)
├── tools/
│   ├── __init__.py        Bundles the default tool set into a registry
│   ├── _common.py         Shared helpers (lazy-bpy, vec3 coercion, JSON)
│   ├── scene.py           list_objects / get_object / select
│   ├── mesh.py            add_primitive
│   ├── transform.py       translate / rotate / scale
│   ├── system.py          bpy.run_operator (allowlisted) + python.eval_in_sandbox
│   └── viewport.py        screenshot to a temp PNG path
└── README.md              this file
```

## Implementation notes

* The transport layer uses only the Python standard library
  (`urllib.request`, `json`) so the add-on works inside Blender's bundled
  interpreter without extra wheels.
* The threading model writes only via the main-thread timer callback. The
  worker thread never touches `bpy` data, which keeps Blender's data model
  safe under streaming updates.
* `make_provider()` falls back to `EchoProvider` (with an inline note) when
  the selected real provider has no API key, so the UI is always usable —
  the user just sees the configuration hint as the reply.
