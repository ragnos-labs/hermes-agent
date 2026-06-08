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

**Phase 2a — interactive shell (scrollbox + composer + header):** the read-only Phase-1 view
becomes interactive. New: a `<scrollbox>` transcript (§8 #2 gotchas — `minHeight:0` on wrapper +
scrollbox, NO `flexDirection` on the scrollbox root, `stickyScroll`/`stickyStart="bottom"`); a
`<textarea>` composer (`flexShrink:0`, focus-on-mount, Enter→submit via `keyBindings`, imperative
`.clear()` + `submitting` re-entrancy guard) that fires `prompt.submit` — now the PRIMARY input
(the `HERMES_TUI_PROMPT` stand-in stays for launch-with-prompt); a `header.tsx` skeleton. Steps 1–3
now run via the composer (no env prompt needed).

- *Drive:* live entry in tmux (real TTY, 100x28), no initial prompt; type into the composer.
- *Run log (2026-06-08, PASS):*
  - Headless gate `bun run check` → **green**: `tsc` + `eslint` clean; `bun test` **12/12** (4 files,
    31 expects). Frame test asserts header + the streamed message INSIDE the scrollbox + the composer
    placeholder; a re-skin test still re-themes the brand. Render helper now flushes 3 `renderOnce`
    passes — a `<scrollbox>` needs >1 pass to measure content + apply sticky before children paint
    (one pass left the transcript row blank).
  - **Live tmux:** header `ready`; composer placeholder showed the LIVE skin's welcome string
    ("Welcome to Hermes Agent! …" — proves the skin→theme path end to end). Typed
    `Reply with exactly three words` + Enter → composer cleared, and:
    ```
     Hermes Agent · opentui · ready
     ❯ Reply with exactly three words
     ⚕ Here are three words
     Welcome to Hermes Agent! Type your message or /help for commands.
    ```
  - **Teardown:** Ctrl+C quits cleanly EVEN with the textarea focused (renderer.keyInput sees Ctrl+C);
    my `bun` PID gone + its `tui_gateway` child reaped — no orphan (exact-PID checks).

**Phase 2b-i — ordered parts + inline tool render:** the flat `Message.text` is replaced (for
assistant turns) by an ordered `parts[]` (§7) dispatched by `<Switch>` in `messageLine.tsx` —
text/reasoning/tool interleave INLINE. Tools matched `start`↔`complete` by `tool_id`, updated IN
PLACE; result rendered inline (≤1 line) or as a capped left-bar block, with the `{output,exit_code}`
envelope stripped (`logic/toolOutput.ts`). Adds smoke step 4 (tool row renders inline).

- *Run log (2026-06-08, PASS):*
  - Headless gate `bun run check` → **green**: `tsc` + `eslint` clean; `bun test` **23/23** (5 files,
    64 expects). New: store ordered-parts tests (interleave text→tool→text, tool update-in-place,
    reasoning accumulate), a frame test asserting the tool renders inline between text + the envelope
    is stripped (`not.toContain('exit_code')`), and pure `toolOutput` unit tests.
  - **Live tmux:** prompt `Use your terminal tool to run … echo alpha; echo beta …`. The assistant
    turn rendered the tool INLINE between text blocks (not dumped below):
    ```
     ❯ Use your terminal tool to run the shell command: echo alpha; echo beta. …
     ⚕ (°ロ°) brainstorming... This seems straightforward.
       ⚡  terminal
           alpha
           beta
       (´･_･`) reflecting...
       It printed **2 lines**: …
    ```
    Multi-line output → left-bar block; envelope stripped (no `{output,exit_code}` wrapper shown).
    (Raw `**2 lines**`/``` fences are expected — native `<markdown>` is 2b-ii.)
  - **Teardown:** Ctrl+C → my `bun` + its `tui_gateway` child both gone, no orphan.

**Phase 2b-ii — native markdown:** text parts render via the native `<code filetype="markdown"
streaming>` (`CodeRenderable` — opencode's v2 text path; `<markdown>` + `internalBlockMode="top-level"`
deferred paint headlessly) with a theme-derived `SyntaxStyle.fromStyles` (cached per theme), `conceal`
(hide `**`/backtick markers), and `drawUnstyledText` (paint raw text immediately while highlighting
settles — also makes it headless-capturable). Completes smoke step 3 (markdown).

- *Run log (2026-06-08, PASS):*
  - Headless gate `bun run check` → **green** (23 tests / 5 files). Render helper now `flush()`es
    (Tree-sitter markdown tokenizes async) and `captureFrame` can wait for content via `until`
    (`waitForFrame`); the hello + inline-tool frame tests pass with text rendered through the
    markdown renderable.
  - **Live tmux:** prompt asking for a level-2 heading + a bold word + a 2-item bullet list rendered:
    ```
     ⚕ (´･_･`) contemplating...
       Demo
       This word is bold
       - apples
       - oranges
    ```
    The `**bold**` markers are CONCEALED — `grep -c '**'` over the pane = **0** (no raw markup leak).
  - **Teardown:** Ctrl+C → my `bun` + child both reaped, no orphan.

**Phase 2 is complete** (2a shell + 2b-i ordered parts/tool render + 2b-ii markdown). Smoke steps
1–4 run live; step 5+ (modals/overlays), step 6 (blocking prompts), step 7 (resume) are later phases.

### Phase 3 — blocking prompts (🔴 deadlock-critical)

The 4 gateway `*.request` events now drive a blocking-prompt overlay that REPLACES the composer
(`store.state.prompt` → App `<Show>` swap), answered via the matching `*.respond` RPC; Esc/Ctrl+C
sends deny/empty so the agent unblocks. The global Ctrl+C-quit is gated on `!blocked`
(`renderer.ts` `isBlocked`). Native paradigm (per glitch's steer): native `<select>` for
approval/clarify choices, native `<input>` for clarify free-text, masked-buffer (`useKeyboard`) for
sudo/secret (native `<input>` has no mask). Adds smoke step 6.

- *Run log (2026-06-08, PASS):*
  - Headless gate `bun run check` → **green** (28 tests / 5 files). New: store reducer tests (all 4
    `*.request` set `store.state.prompt`; `clearPrompt` clears; clarify null-choices→free-text) + a
    frame test (approval overlay renders the command + all 4 options as a bordered modal AND the
    composer placeholder is GONE while blocked).
  - **Live tmux (real TTY):** `Use your terminal tool to run: rm -rf /tmp/hermes-approval-probe` →
    the gateway emitted `approval.request` and the overlay rendered inline below the running
    `⚡terminal` row:
    ```
     ┌─ ⚠ Approval required ───────────────────────────────────────┐
     │ rm -rf /tmp/hermes-approval-probe                           │
     │ delete in root path                                         │
     │  ▶ Approve once  / Approve for session / Always / Deny      │
     │ ↑↓ select · Enter confirm · Esc/Ctrl+C deny                 │
     └─────────────────────────────────────────────────────────────┘
    ```
    - **Approve once (Enter):** agent UNBLOCKED — command ran (exit 0), prompt cleared, composer
      returned, assistant continued.
    - **Deny (Esc):** agent UNBLOCKED — tool result `[error] BLOCKED: Command denied by user`, then
      the assistant continued. No deadlock.
    - **Ctrl+C WHILE BLOCKED:** process stayed ALIVE (did NOT quit — `isBlocked` gate) and the prompt
      cancelled (→ deny) + composer returned. **Ctrl+C when NOT blocked:** clean quit, child reaped,
      no orphan.
  - Coverage note: approval was the live-driven representative; clarify/sudo/secret share the
    identical overlay-swap + `useKeyboard` cancel + `*.respond` wiring (reducer + render tested).
    `confirm` is local (non-gateway) and lands with the slash commands that trigger it (Phase 4).

### Phase 4 — session lifecycle + slash system

**Phase 4a — slash command system + confirm:** the composer routes `/command` through the dispatch
ladder (`logic/slash.ts`): client-local command → `slash.exec {command, session_id}` (output →
system line) → on reject `command.dispatch {arg, name, session_id}` (exec/plugin→system ·
alias→re-dispatch · skill/send→submit a turn · prefill→notice). Client commands: help/quit/exit/
clear/new/logs. `/clear`,`/new` open a LOCAL Y/N confirm (`ConfirmPrompt`, non-gateway). `/help`
renders the live `commands.catalog`. Adds smoke step 5 (slash) partial.

- *Run log (2026-06-08, PASS):*
  - Headless gate `bun run check` → **green** (36 tests / 6 files). New `slash.test.ts`: parse + the
    full ladder (client cmds; unknown→slash.exec; reject→command.dispatch send/exec) against a fake
    `SlashContext`.
  - **Live tmux:**
    - `/help` → the full gateway catalog rendered (18+ `/command — desc` lines incl. skill commands;
      `commands.catalog` `pairs` parsed).
    - `/version` → ran through `slash.exec`; output shown as a system line ("Hermes Agent v0.16.0 …").
    - `/clear` → LOCAL confirm dialog ("Clear the transcript? y/Enter · n/Esc") → `y` cleared the
      transcript; composer returned and accepted input.
    - `/quit` → clean quit, gateway child reaped.
  - **Keystroke-leak fix:** the key that answers a prompt no longer bleeds into the freshly-focused
    composer (`/clear`→`y`→`hi` shows `hi`, not `yhi`). PromptOverlay now defers the prompt-clear
    (composer remount) past the current keystroke (`setTimeout 0`) — this also hardens the Phase 3
    prompts (approve/deny Enter, masked Enter, clarify submit).

**Phase 4b — session resume (step 7):** the entry bootstrap resumes instead of creating when
`HERMES_TUI_RESUME=<id|recent>` is set: `session.most_recent` (for recent) → `session.resume
{cols, session_id}` → `store.commitSnapshot(mapResumeHistory(messages))`, buffering live events
across the RPC (`beginBuffer`/`commitSnapshot`). `mapResumeHistory` (`logic/resume.ts`) folds the
resumed `{role:'tool', name, context}` rows into the preceding assistant turn's ordered parts so
they render inline (state:'complete', summary=context) — the §8 #5 gotcha.

- *Run log (2026-06-08, PASS):*
  - Headless gate `bun run check` → **green** (40 tests / 7 files): `resume.test.ts` (map user/
    assistant + fold tool rows; standalone holder; ignore junk) + a store test (beginBuffer/
    commitSnapshot replays events buffered across the resume).
  - **Live tmux (two launches):** Launch A (initial prompt `… run echo resume-marker-42 …`) created a
    session with a `⚡terminal` tool call + assistant reply, then quit. Launch B
    (`HERMES_TUI_RESUME=recent`) → `session resumed {count:3}` and the transcript hydrated:
    ```
     ❯ Use your terminal tool to run exactly: echo resume-marker-42 …
     ⚕
       ⚡terminal  echo resume-marker-42        ← TOOL ROW hydrated (name + command context)
     ⚕ The output was resume-marker-42.
    ```
    User message ✓, assistant text ✓, **tool row ✓** (the `{name,context}` row rendered inline, not
    blank). `/quit` clean, child reaped.
  - **Stress test + profile (real 303-line / 103-message session `20260503_163205_0443f04e` from
    `~/.hermes/sessions`):** resumed clean. Profile (logged via the `rpc_ms`/`hydrate_ms` instrument):
    - **client hydration = 76 ms** for 103 messages (`mapResumeHistory` + `commitSnapshot` + the Solid
      store write — ~0.7ms/msg, fast); server `session.resume` RPC = 1578 ms (the gateway loading the
      session from disk — server-side, scales with raw message count, outside the TUI's code).
    - **bun RSS = 214 MB, STABLE over 6s (no leak)**; gateway child (Python) = 157 MB.
    - Render: the transcript bottom-pinned correctly, multiple `⚡terminal` rows hydrated inline with
      their command context, no clipping; **PageUp scrolls** into older history.
    - Note: message rows + their native markdown/code renderables are instantiated for the whole
      history (the `<scrollbox>` `viewportCulling` skips offscreen *render* calls but not
      instantiation), so RSS grows ~linearly with turn count — fine at hundreds; list virtualization
      is the lever if multi-thousand-turn sessions become a target.

**Phase 4c (TODO):** remaining TUI-only client commands (mouse/redraw/compact/details/sessions/
replay/setup/heapdump/mem), completions dropdown (step 5), pager routing for long slash output.

### Phase 5a — pager (step 5, partial)

A full-height scrollable overlay (`view/overlays/pager.tsx`) replaces the transcript+composer while
open (`store.state.pager`); scrolling via `useKeyboard`→`scrollBy`/`scrollTo` (no focus reliance),
Esc/q close (deferred so the key can't leak into the remounting composer). Long slash output
(>180 chars or >2 lines, Ink parity) routes here instead of a system line; `/logs` always pages.
Unlocks `/status`,`/logs`,`/history`,`/tools` output at once.

- *Run log (2026-06-08, PASS):*
  - Headless gate `bun run check` → **green** (41 tests / 7 files): slash `present()` routing
    (short→system, long→pager, titled by command; `/logs`→pager) + a pager frame test (title +
    content render, transcript/composer replaced).
  - **Live tmux:** `/logs` → bordered pager titled "Logs" with the ring lines + footer "Esc/q close";
    PageDown scrolled; Esc closed → composer returned AND refocused (typed "after-pager" appeared —
    no key-leak). `/version` (5-line output) → routed to the pager titled "Version".

### Phase 5c — session switcher (step 5; first-class picker)

`/sessions` (alias `/resume`,`/switch`,`/session`) → `session.list` → a native `<select>` overlay
(`view/overlays/sessionSwitcher.tsx`) replacing the composer; Enter resumes the chosen session via
the SAME `resumeInto` hydrate path as launch (so tool rows hydrate), Esc closes. Reuses Phase 4b.

- *Run log (2026-06-08, PASS):*
  - Headless gate `bun run check` → **green** (43 tests / 7 files): slash `/sessions`/`/resume` →
    `listSessions`+`openSwitcher` + a switcher frame test (rows render, composer replaced).
  - **Live tmux:** `/sessions` → switcher listing real sessions with titles + msg counts + previews
    ("Terminal Echo Command Output · 4 msgs · …", "[IMPORTANT: …cron…] · 21 msgs", …). ↓ + Enter on
    "Terminal Echo Command Output" → `session resumed {count:3, hydrate_ms:8}` and the transcript
    hydrated (user prompt + `⚡terminal echo resume-marker-42` + assistant reply); switcher closed,
    composer returned; `/quit` clean.

**Phase 5a (completions) / 5b / 5d / 5e (TODO):** completions dropdown (typing `/` → `complete.slash`);
chrome (header model/cwd/context%/cost from `session.info`+`Usage`); model picker + skills hub;
agent features (reasoning trail, todos, notifications, voice); subagents tree + agents dashboard.

### Phase 8 — launcher
_(append: launch via the real `HERMES_TUI_ENGINE=opentui hermes --tui`; dashboard PTY path)_
