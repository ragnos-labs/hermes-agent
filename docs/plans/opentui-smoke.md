# OpenTUI engine — agentic smoke test (living end-to-end scenario)

**What this is:** the canonical end-to-end drive of the native OpenTUI (Solid + Effect-at-boundary)
engine. An agent runs this in **tmux on a real TTY** after every phase: it confirms the new phase's
features work AND that everything from prior phases still works. Each phase APPENDS its new steps
(with expected on-screen observations) so the scenario compounds into the full acceptance routine.

**Companion:** the headless `bun run check` gate (type-check + lint + `bun test` + headless frame
verification) is the non-interactive complement — run BOTH every phase. A phase is not complete
until this doc is updated AND the live drive passes AND `bun run check` is green.

**Rules of the drive:**
- Real TTY only (OpenTUI core is Bun/FFI; the dev shell is non-TTY → use tmux).
- ALWAYS press Enter after `tmux send-keys` (a known driving pitfall).
- Capture a frame (`tmux capture-pane -p`) at each "observe" checkpoint; paste the relevant lines
  into the phase's run log below.
- If a step regresses a prior phase, the phase is NOT done — fix before appending.

---

## The full target scenario (the end state we build toward)

Each phase implements the slice it owns; by Phase 5e this entire sequence runs clean:

1. **Launch** — `HERMES_TUI_ENGINE=opentui hermes --tui` (or the dev `bun src/entry/main.tsx`).
   → *Observe:* header paints (engine/model/cwd), empty transcript, composer with placeholder.
2. **Type + submit** — type a prompt, press Enter.
   → *Observe:* composer clears; the user message lands in the transcript; busy indicator starts.
3. **Streamed reply** — the assistant streams text.
   → *Observe:* text appears incrementally; markdown renders (bold/headings/fenced code/table);
     no raw `**`/escape leakage; sticky-bottom keeps the latest line visible.
4. **Run a tool** — prompt that triggers a tool call mid-reply (e.g. "explain, then ls, then
   summarize").
   → *Observe:* the tool row renders INLINE between the two text blocks (not dumped at the bottom);
     compact one-line by default; multiline output capped in a left-bar block with click-to-expand;
     no `{output,exit_code}` JSON envelope visible.
5. **Open a modal / slash popup** — `/model` (picker), `/sessions` or Ctrl+X (switcher), `/skills`
   (hub), `/status` (pager), `/` (completions dropdown).
   → *Observe:* the overlay opens above the composer (or replaces the transcript for agents);
     arrow-key nav works; Esc closes; selection takes effect.
6. **Answer a blocking prompt** — trigger a tool approval / clarifying question / sudo / secret.
   → *Observe:* the prompt overlay blocks the composer; ↑↓/1-N selects; Enter answers; the agent
     UNBLOCKS and continues; Esc/Ctrl+C sends deny/empty and the agent still unblocks (no deadlock).
7. **Resume** — relaunch with resume; the prior session's transcript reloads.
   → *Observe:* historical user/assistant/tool rows render (tool rows show `name (context)`, not
     blank); the latest is pinned; a new turn streams on top correctly.
8. **Resize** — shrink/grow the terminal.
   → *Observe:* transcript + composer reflow/rewrap to the new width; no clipped top, no gap.
9. **Quit** — Ctrl+C at the composer (no prompt up) / `/quit`.
   → *Observe:* clean teardown (renderer finalizers run), terminal restored, no orphan python child.

---

## Phase run logs (appended per phase)

### Phase 0 — scaffold
**New steps to add:** step 1 (launch) — but minimal: render a static "hello" frame.
- *Drive:* `bun src/entry/main.tsx` in tmux.
- *Expect:* a single frame with "hello"-class content paints and stays; Ctrl+C tears down cleanly
  (renderer `acquireRelease` finalizer runs); `bun test` captures the same frame headlessly.
- *Run log (2026-06-08, PASS):*
  - Package: `ui-tui-opentui-v2/` on `feat/opentui-native-engine`. Deps installed: `effect@4.0.0-beta.78`,
    `@opentui/{core,solid,keymap}@0.3.2`, `solid-js@1.9.10` (peer wants 1.9.12 — harmless patch mismatch,
    same as opencode). Native lib `@opentui/core-linux-x64` loaded; bun 1.3.13.
  - Headless gate `bun run check` → **green**: `tsc --noEmit` 0 errors, `eslint .` 0 errors,
    `bun test` **5/5 pass** across 3 layers (boundary/Effect via FakeGateway, store reducer, App frame).
  - Headless frame gate (`src/test/render.test.tsx`): App mounted via Solid `testRender` →
    `renderOnce()` → `captureCharFrame()` contains `hermes`, `ready`, `Hi there, glitch!`.
  - **Live tmux (real TTY, 100x28):** `bun src/entry/main.tsx` painted:
    ```
     hermes · opentui · ready
     ✦ Hi there, glitch!
    ```
    (FakeGateway scripted stream: `gateway.ready` → `message.start` → 3 deltas → `message.complete`.)
  - Teardown: Ctrl+C → process exited, **no orphan** `bun` process left (verified `pgrep`). NOTE:
    `exitOnCtrlC:false` is set on the renderer (gotcha §8 #6/#7) and Phase 0 has no in-app keyboard
    quit handler yet, so Ctrl+C currently exits via SIGINT→bun (OS cleanup), not an in-app
    Deferred-driven quit. The `acquireRelease` finalizer is wired; a signal/keymap-driven graceful
    quit lands with the `@opentui/keymap` host in a later phase.
  - API facts pinned this phase (verified against effect@4.0.0-beta.78 `.d.ts`, NOT 3.x docs):
    `Context.Service<Self,Shape>()("id")`; `Deferred.make` + `Deferred.doneUnsafe(self, Effect.void)`
    + `Deferred.await`; no `TestContext` — use `TestClock.layer()` from `effect/testing`;
    `ManagedRuntime.make(layer)` + `.runPromise` + `.dispose`. Renderable: inline color is
    `<span style={{ fg }}>` (NOT `fg=` — that's `<text>` only); `createTestRenderer` returns
    `{renderer,renderOnce,captureCharFrame,resize,mockInput,mockMouse}` and Solid renders async so
    you MUST `renderOnce()` before capturing.

### Phase 1 — transport + store
**New steps to add:** steps 1–3 against the REAL `tui_gateway` (connect → `gateway.ready` → submit
a trivial prompt → watch a streamed reply land), plus a clean Ctrl+C quit that reaps the gateway
child (newly wired this phase). The composer is Phase 2, so the prompt is driven via the
`HERMES_TUI_PROMPT` initial-prompt bootstrap (`session.create` → `prompt.submit`).

- *Drive:* live entry in tmux (real TTY, 100x28). The worktree `.venv` lacks `jsonrpcserver`, so the
  drive uses the installed interpreter while running the worktree's `tui_gateway` via the source root:
  ```
  HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python \
  HERMES_PYTHON_SRC_ROOT=<worktree> \
  HERMES_TUI_PROMPT='Respond with only the single word: pong' \
  bun src/entry/main.tsx
  ```
  (default backend = live `liveGatewayLayer`; `HERMES_TUI_FAKE=1` selects the scripted hello instead.)
- *Expect:* header flips to `ready` on `gateway.ready`; the user prompt lands (`❯ …`); the assistant
  reply streams in (`⚕ …`); Ctrl+C tears down cleanly with no orphan `bun` or `tui_gateway` child.

- *Run log (2026-06-08, PASS):*
  - Headless gate `bun run check` → **green**: `tsc --noEmit` 0 errors, `eslint .` 0 errors,
    `bun test` **12/12 pass** across 4 files (boundary FakeGateway · GatewayEvent decode · store
    reducer skin/dedup/hydrate · themed App frame + reactive re-skin).
  - Headless live-transport contract (`bun src/test/liveGateway.smoke.ts`, installed venv + worktree
    srcRoot) → `PASS — gateway.ready seen, session.create ok (sid=…)`. Decode-once boundary +
    handshake verified against the REAL server (skips gracefully without a venv/model).
  - **Live tmux (real TTY, 100x28):** the frame painted, end to end through the live gateway:
    ```
     Hermes Agent · opentui · ready
     ❯ Respond with only the single word: pong
     ⚕ pong
    ```
    Log (`~/.hermes/logs/opentui-v2.log`, NDJSON ring+file sink) confirmed `bootstrap: session
    created {sid:4e3ff31d}`. Theme is the default (no skin emitted by this gateway); `store.test`
    + `render.test` cover the `gateway.ready{skin}` / `skin.changed` → `fromSkin` re-theme path.
  - **Teardown:** Ctrl+C → my `bun` PID gone (graceful quit: renderer `destroy` → `shutdown` Deferred
    → scope finalizers) AND its `tui_gateway` child gone (gateway layer release → `client.stop()` →
    stdin EOF → child exits). Verified by exact-PID checks — **no orphan**. (`exitOnCtrlC:false` hands
    Ctrl+C to an in-app key handler now; the `!blocked` gating for prompts lands in Phase 3.)
  - DRIVING PITFALL recorded: never `pkill -f tui_gateway.entry` — it also kills the children of the
    user's live Ink sessions (which auto-respawn). Track the spawned `bun` PID and kill only that;
    its gateway child is reaped by the graceful-quit finalizer.

### Phase 2 — core transcript
_(append: markdown rendering, inline ordered-parts tool render — steps 3–4 full)_

### Phase 3 — blocking prompts
_(append: step 6 — all 4 prompts + confirm + cancel paths; verify no deadlock)_

### Phase 4 — session lifecycle + slash system
_(append: step 7 resume; slash dispatch + the 13 TUI-only commands)_

### Phase 5a–5e
_(append: step 5 modals/overlays/pager/completions/pickers; chrome; agent features; subagents)_

### Phase 8 — launcher
_(append: launch via the real `HERMES_TUI_ENGINE=opentui hermes --tui`; dashboard PTY path)_
