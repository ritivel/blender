# AI Assistant — Bundled Add-on (Step 1 scaffold)

This bundled add-on is the first step of the plan in
[`doc/guides/ai_assistant_plan.md`](../../../doc/guides/ai_assistant_plan.md).
It introduces the user-facing surface (chat panel + preferences) and the
internal harness skeleton (provider + tool interfaces) without making
any network calls.

## What ships in step 1

* **N-panel "AI"** in the 3D Viewport sidebar with a chat list, draft
  field, **Send**, **Clear**, and a collapsible **Quick Prompts** sub-panel.
* **Preferences** (Edit > Preferences > Add-ons > AI Assistant) for
  provider, model, API-key env var, system prompt, and tool-permission
  mode.
* **Harness skeleton** (`harness.py`) defining the `Provider`,
  `ToolSpec`, and `ToolRegistry` interfaces, plus an offline
  `EchoProvider` that echoes the last user message back. This is the
  contract that real providers (step 2) and real tools (step 3) plug
  into.
* **Default-enabled** on factory startup via the entry added in
  `BKE_blendfile_userdef_from_defaults`.

## What does **not** ship in step 1

* No real HTTP requests to Anthropic / OpenAI.
* No streaming.
* No tool execution against `bpy.ops` or generated Python.
* No background or parallel agents.
* No new editor space at the C level.

These are tracked in steps 2–8 of the plan.

## Trying it out

After building Blender from this branch (see "macOS .dmg" below):

1. Launch Blender.
2. In the 3D Viewport, press `N` to open the sidebar.
3. Click the **AI** tab.
4. Type a message and press **Send** — the offline echo provider replies
   immediately.

To configure the provider (for when step 2 lands), open
*Edit > Preferences > Add-ons > AI Assistant*.

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
├── __init__.py        bl_info, register / unregister
├── preferences.py     AddonPreferences (provider, model, key, prompt, perms)
├── properties.py      Per-scene chat session + message PropertyGroup
├── operators.py       Send / Clear / SetDraft operators
├── ui.py              N-panel "AI" + Quick Prompts sub-panel
├── harness.py         Provider, ToolSpec, ToolRegistry, EchoProvider
└── README.md          this file
```
