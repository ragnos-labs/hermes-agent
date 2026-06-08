# OpenTUI Feature Map & Porting Roadmap

**What this is:** the complete inventory of every Hermes TUI feature — slash commands, overlays,
modals, prompts, chrome/HUD, and agent-level surfaces — with Ink (source of truth) vs the new
native OpenTUI engine (`ui-tui-opentui/`) port status. This is the master checklist that scopes
all remaining phases. Compiled 2026-06-08 from 3 parallel file:line-grounded recon passes.

**Source of truth = Ink TUI** (`ui-tui/src/`) + Python registry (`hermes_cli/commands.py`).
**Target = `ui-tui-opentui/`** (native OpenTUI on Bun; Ink stays default & untouched).
**Companion docs:** `opentui-native-rewrite-spec.md` (the spec), `opentui-migration-spec.md`
§11–14 (launcher/distribution review).

Legend: ✅ done in OpenTUI · ⚠️ partial · ❌ missing · 🔴 blocking (unhandled = agent deadlock).

> **Two builds — read this.** §0–§4 below are the original backlog for the **React** build
> `ui-tui-opentui/` (now SUPERSEDED, reference-only). The ground-up **Solid + Effect-at-boundary**
> rewrite `ui-tui-opentui-v2/` (spec `opentui-rewrite-v4-spec.md`) **starts from zero** — the React
> ✅s do NOT transfer. Its progress is tracked in the **v4 parity matrix** immediately below; §1–§4
> remain the authoritative Ink-side feature inventory each v4 phase ports from.

---

## v4 PARITY MATRIX — `ui-tui-opentui-v2` (Solid + Effect-at-boundary)

3-way: **Ink** (source of truth) ↔ **opencode** (method ref) ↔ **v2 build** (status + new file:line).
A row is ✅ only when it has a test (Layer 1–4) AND a smoke-doc check. The judge scores against this.

### Phase 0 — scaffold (foundation; commit `a47c6df`)
| Concern | opencode ref | v2 build | Status | Test · smoke |
|---|---|---|---|---|
| Effect runtime + `acquireRelease(createCliRenderer)` + one `render()` bridge | `app.tsx:177` | `boundary/renderer.ts`, `boundary/runtime.ts`, `entry/main.tsx` | ✅ | `render.test.tsx` · smoke P0 |
| FakeGateway layer (injectable transport seam) | `test/fixture/tui-sdk.ts` | `entry/fakeGateway.ts` | ✅ | `gateway.test.ts` · — |
| Headless frame gate (`captureCharFrame`) | `test/cli/tui/*.test.tsx` | `test/lib/render.ts` | ✅ | `render.test.tsx` · smoke P0 |
| `testEffect`/`testLayer` (ManagedRuntime + TestClock, no `@effect/vitest`) | executor | `test/lib/effect.ts` | ✅ | used by `gateway.test.ts` |

### Phase 1 — transport + store + theming (this phase)
| Concern | Ink ref | opencode ref | v2 build | Status | Test · smoke |
|---|---|---|---|---|---|
| Live transport: spawn `tui_gateway`, JSON-RPC stdio framing, async-result map, exit→reject-all | `gatewayClient.ts` | `context/sdk.tsx` | `boundary/gateway/{client,liveGateway,python}.ts` | ✅ | `liveGateway.smoke.ts` (live) + `gateway.test.ts` · smoke P1 |
| Principled python resolution (HERMES_PYTHON/PYTHON → $VIRTUAL_ENV → `<root>/.venv`→`venv` → bare) | `gatewayClient.ts:45` | — | `boundary/gateway/python.ts` | ✅ | (mirrors Ink 1:1) |
| `GatewayEvent` Schema: decode-unknown ONCE, skip-unknown/malformed (`Option.none`), ~35 members | `gatewayTypes.ts:509` | `context/sdk.tsx` | `boundary/schema/GatewayEvent.ts` | ✅ | `schema.test.ts` · — |
| 16ms event coalescing → Solid `batch()` (one repaint per burst) | — | `sdk.tsx:54` | `boundary/gateway/liveGateway.ts` | ✅ | (live smoke) |
| Store reducer: streaming text concat (`message.start/delta/complete`, prefer `text`) | `cgeh.ts` | `context/sync-v2.tsx` | `logic/store.ts` | ✅ | `store.test.ts` + `render.test.tsx` · smoke P1 (`⚕ pong`) |
| Lifecycle `gateway.ready` → ready flag | `cgeh.ts` | `sync-v2.tsx` | `logic/store.ts` | ✅ | smoke P1 (header `ready`) |
| LRU id-dedup + hydrate-while-buffering (resume scaffold) | — | `sync-v2.tsx` | `logic/store.ts` | ✅ | `store.test.ts` |
| Theming/skins: `fromSkin` + light/dark + ANSI-256 norm, 1:1 Ink port; `ThemeProvider`; NO hardcoded styles | `theme.ts`, `gatewayTypes.ts` | `context/theme.tsx` | `logic/theme.ts`, `view/theme.tsx`, `view/App.tsx` | ✅ | `store.test.ts` + `render.test.tsx` (re-skin) · — |
| `gateway.ready{skin}` / `skin.changed` → reactive re-theme | `cgeh.ts` | `theme.tsx` | `logic/store.ts` | ✅ | `store.test.ts` |
| Ctrl+C graceful quit + no-orphan child (renderer destroy → finalizers → `client.stop()`) | — | `app.tsx` | `boundary/renderer.ts` | ✅ | smoke P1 (PID teardown) |
| Initial-prompt bootstrap (`session.create`→`prompt.submit`; Phase-2 composer stand-in) | `useSessionLifecycle.ts`, `useSubmission.ts` | — | `entry/main.tsx` | ✅ | smoke P1 |
| Typed errors at the boundary (`RendererError`/`GatewayError`/`PythonResolutionError`) | — | — | `boundary/errors.ts` | ✅ | (compile + `gateway.test.ts`) |
| Diagnostics log (ring + NDJSON file; console-safe) | — | `util/log.ts` | `boundary/log.ts` | ✅ | (live smoke tail) |

### Phase 2a — interactive shell (scrollbox + composer + header)
| Concern | Ink ref | opencode ref | v2 build | Status | Test · smoke |
|---|---|---|---|---|---|
| Transcript `<scrollbox>` — one full-height, sticky-bottom, §8 #2 gotchas (minHeight:0 wrapper+box, no flexDirection on root) | `appLayout.tsx` | `routes/session/index.tsx:1169` | `view/transcript.tsx` | ✅ | `render.test.tsx` · smoke P2a |
| MessageLine — role gutter + streaming `▍`, themed (flat text in 2a → ordered parts in 2b-i) | `transcript.*` | `session-v2.tsx:305` | `view/messageLine.tsx` | ✅ | `render.test.tsx` · smoke P2a/P2b |
| Composer `<textarea>` — flexShrink:0, focus-on-mount, Enter→submit, `.clear()` + re-entrancy guard → `prompt.submit` | `textInput.tsx`, `useSubmission.ts` | `component/prompt/index.tsx:141` | `view/composer.tsx` + `entry/main.tsx` submit | ✅ | smoke P2a (typed→cleared→reply) |
| Header skeleton (brand · engine · ready) | `appChrome.tsx`/`branding.tsx` | — | `view/header.tsx` | ⚠️ skeleton | `render.test.tsx` · smoke P2a |
| Frame-snapshot helper settles a scrollbox (3× renderOnce) | — | `test/cli/tui/*.test.tsx` | `test/lib/render.ts` | ✅ | (all frame tests) |

### Phase 2b-i — ordered parts + inline tool render
| Concern | Ink ref | opencode ref | v2 build | Status | Test · smoke |
|---|---|---|---|---|---|
| Ordered `parts[]` (text/reasoning/tool) per assistant turn + `<Switch>` dispatch (inline interleave §7) | `cgeh.ts`/`thinking.tsx` | `context/sync-v2.tsx`, `session-v2.tsx:305` | `logic/store.ts`, `view/messageLine.tsx` | ✅ | `store.test.ts` + `render.test.tsx` · smoke P2b |
| Tool render: inline (≤1 line) / capped left-bar block; tool `start`↔`complete` matched by `tool_id`, updated in place | `messageLine.tsx`/`thinking.tsx:689` | `session-v2.tsx:564-700` | `view/toolPart.tsx`, `logic/store.ts` | ✅ | `store.test.ts` + `render.test.tsx` · smoke P2b (`⚡ terminal`) |
| `{output,exit_code}` envelope strip + line/char collapse | — | `util/collapse-tool-output.ts` | `logic/toolOutput.ts` | ✅ | `toolOutput.test.ts` + `render.test.tsx` (`not.toContain exit_code`) |
| Reasoning part accumulation (render is dim text; rich trail = Phase 5d) | `thinking.tsx:621` | — | `logic/store.ts`, `view/messageLine.tsx` | ⚠️ basic | `store.test.ts` |

### Phase 2b-ii — native markdown
| Concern | Ink ref | opencode ref | v2 build | Status | Test · smoke |
|---|---|---|---|---|---|
| Assistant text via native `<code filetype="markdown" streaming conceal>` + theme-derived `SyntaxStyle.fromStyles` (cached per theme); `drawUnstyledText` | `markdown.tsx` | `session-v2.tsx:358` AssistantText | `view/markdown.tsx`, `view/messageLine.tsx` | ✅ | `render.test.tsx` · smoke P2b (no raw `**`) |
| Frame helper settles async markdown (`flush` + `waitForFrame`/`until`) | — | `test/cli/tui/*` | `test/lib/render.ts` | ✅ | (all frame tests) |

**Phase 2 complete** — smoke steps 1–4 run live (launch → type/submit → streamed markdown reply →
inline tool render).

### Phase 3 — blocking prompts 🔴 (deadlock-critical)
Native paradigm (glitch's steer): native `<select>` (approval/clarify choices) + native `<input>`
(clarify free-text) + masked-buffer via `useKeyboard` (sudo/secret — `<input>` has no native mask).
Composer↔overlay swap on `store.state.prompt`; global Ctrl+C-quit gated on `!blocked`.
| Concern | Ink ref | opencode ref | v2 build | Status | Test · smoke |
|---|---|---|---|---|---|
| Prompt state + composer↔overlay swap; Ctrl+C-quit gated on `!blocked` | `overlayStore.ts`, `appLayout.tsx:273` | `permission.tsx` | `logic/store.ts`, `view/App.tsx`, `boundary/renderer.ts` | ✅ | `store.test.ts` + `render.test.tsx` · smoke P3 |
| `approval.request` → `<select>` (once/session/always/deny) → `approval.respond {choice, session_id}`; Esc/Ctrl+C→deny | `cgeh.ts:722` | `permission.tsx` | `view/prompts/approvalPrompt.tsx` | ✅ | `render.test.tsx` · **smoke P3 (live: approve + deny + Ctrl+C-cancel, no deadlock)** |
| `clarify.request` → `<select>` choices + "✎ Other…"→`<input>` free-text → `clarify.respond {answer, request_id}`; Esc→empty | `cgeh.ts:2225` | — | `view/prompts/clarifyPrompt.tsx` | ✅ | `store.test.ts` · smoke P3 (shared infra) |
| `sudo.request`/`secret.request` → masked buffer → `sudo/secret.respond {password/value, request_id}`; Esc/Ctrl+C→'' | `cgeh.ts` | — | `view/prompts/maskedPrompt.tsx` | ✅ | `store.test.ts` · smoke P3 (shared infra) |
| `confirm` (local, non-gateway Y/N) | `prompts.tsx` ConfirmPrompt | — | (Phase 4 — triggered by `/new`,`/clear`) | ❌ | — |

### Phase 4a — slash command system + confirm
| Concern | Ink ref | v2 build | Status | Test · smoke |
|---|---|---|---|---|
| Parse + dispatch ladder (client → `slash.exec` → `command.dispatch` w/ exec/alias/skill/send/prefill) | `createSlashHandler.ts`, `domain/slash.ts` | `logic/slash.ts` | ✅ | `slash.test.ts` · smoke P4 (`/version` via slash.exec) |
| Composer routes `/command` vs prompt; server output → system line | `useSubmission.ts` | `entry/main.tsx` | ✅ | smoke P4 |
| Client commands (help/quit/exit/clear/new/logs) | `slash/commands/*.ts` | `logic/slash.ts` | ⚠️ 6 of 13 | `slash.test.ts` · smoke P4 |
| `commands.catalog` → `/help` | `useConfigSync.ts` | `logic/slash.ts` | ✅ | smoke P4 (full catalog) |
| Local `confirm` Y/N dialog (`/clear`,`/new`) | `prompts.tsx` ConfirmPrompt | `view/prompts/confirmPrompt.tsx` + store `setConfirm` | ✅ | smoke P4 (clear→y) |
| Keystroke-leak fix: defer prompt-clear past the answering key (hardens all prompts) | — | `view/prompts/promptOverlay.tsx` | ✅ | smoke P4 (`/clear`→y→hi) |
| Remaining TUI-only commands (mouse/redraw/compact/details/sessions/replay/setup/heapdump/mem) | `slash/commands/*.ts` | — | ❌ (4b) | — |
| Completions dropdown; pager routing for long output | `useCompletion.ts`; `FloatBox` | — | ❌ (4c/5a) | — |
| Session RESUME (`session.resume`/`session.most_recent` + hydrate incl. tool rows `{name,context}` folded into assistant parts) | `useSessionLifecycle.ts` | `logic/resume.ts`, `logic/store.ts` (beginBuffer/commitSnapshot), `entry/main.tsx` | ✅ | `resume.test.ts` + `store.test.ts` · **smoke P4 (live + 103-msg stress: 76ms hydrate, 214MB stable)** |

### Phase 5a — pager (§2b)
| Concern | Ink ref | opencode ref | v2 build | Status | Test · smoke |
|---|---|---|---|---|---|
| Pager overlay — full-height scrollbox, title+footer, Esc/q close, scroll via scrollBy/scrollTo; replaces transcript+composer | `appOverlays.tsx:177` (`FloatBox`) | `routes/session` pager | `view/overlays/pager.tsx`, `store` openPager/closePager, `view/App.tsx` | ✅ | `render.test.tsx` · smoke P5a |
| Long slash output → pager (Ink: >180 chars / >2 lines), titled by command; `/logs`→pager | `createSlashHandler.ts` `page()` | — | `logic/slash.ts` `present()` | ✅ | `slash.test.ts` · smoke P5a (`/version`,`/logs`) |
| Unlocks `/status`,`/logs`,`/history`,`/tools` output | `appOverlays.tsx` | — | (via `present()`) | ✅ | smoke P5a |

### Phase 5c — session switcher (§2b; first-class picker)
| Concern | Ink ref | v2 build | Status | Test · smoke |
|---|---|---|---|---|
| Session switcher — `/sessions`/`/resume`/`/switch`/`/session` → `session.list` → `<select>` → resume (reuses Phase 4b hydrate) | `activeSessionSwitcher.tsx` (`appOverlays.tsx:145`) | `view/overlays/sessionSwitcher.tsx`, `logic/slash.ts`, `logic/resume.ts` `mapSessionList`, `entry` `resumeInto`/`onResume` | ✅ | `slash.test.ts` + `render.test.tsx` · **smoke P5c (live: list → pick → hydrate w/ tool row)** |

_Remaining first-class surfaces: completions dropdown, model picker, skills hub, agents dashboard;
plus chrome (5b) + agent features (5d). The §1–§4 Ink inventory below is the per-phase source._

---

## 0. Current OpenTUI engine state (what exists today)

Renders: static header line, transcript scrollbox w/ role gutters, markdown→spans, **compact
tool-result render** (one-line by default; capped left-bar block w/ JSON-envelope strip +
click-to-expand — polish 2026-06-08), streaming `▍` cursor, single-line `<input>` composer that
**clears on submit** and never collapses (flexShrink:0), basic status text. Resize reflows live via
`useTerminalDimensions()` (no manual re-mount). Session **resume** wired (`HERMES_TUI_RESUME` →
`session.resume`).
`src/gateway/eventAdapter.ts` handles: `gateway.ready`, `message.start/delta/complete`,
`thinking/reasoning.delta` (stored on `Msg.thinking` but **not rendered**), `tool.start` (label
only), `tool.complete`, `status.update`, `error`, `gateway.stderr/start_timeout/protocol_error`,
**and (Phase 4 ✅) the 4 interactive `*.request` events** (clarify/approval/sudo/secret) via a
native prompt overlay + `*.respond` RPCs — the deadlock is fixed.
**Explicitly drops** (eventAdapter `default:` branch): `notification.*`, `voice.*`,
`browser.progress`, `background.complete`, `subagent.*`, `tool.progress/generating`,
`reasoning.available`.

---

## 1. SLASH COMMANDS

**Canonical registry:** `hermes_cli/commands.py:64` `COMMAND_REGISTRY` — **70 `CommandDef` entries**,
5 categories. The OpenTUI engine should consume the `commands.catalog` RPC (not hardcode), exactly
like Ink. **Status: ❌ the OpenTUI app has NO slash command handling yet.**

### Dispatch ladder to reproduce (`ui-tui/src/app/createSlashHandler.ts:10`)
1. Parse (`domain/slash.ts:6`) → 2. client-local handler (`app/slash/registry.ts:20`, aggregates
`commands/{core,session,ops,setup,debug}.ts`) → 3. catalog alias/prefix (`catalog.canon`) →
4. `gw.request('slash.exec', …)` (Python `_SlashWorker` subprocess) → 5. `command.dispatch`
fallback (quick_commands / plugins / **skills** / pending-input).
- Forced to `command.dispatch` (slash.exec rejects): `_PENDING_INPUT_COMMANDS` =
  {retry, queue, q, steer, goal, undo} (`tui_gateway/server.py:6461`); `_WORKER_BLOCKED` =
  {snapshot, snap} (`:6473`).
- Skills (not in registry) route via `command.dispatch` → `{type:"skill", message}` → submitted as
  a user turn. `{type:"alias"}` re-dispatches; `{type:"prefill"}` fills the composer.
- TUI catalog hides `_TUI_HIDDEN`={sethome,commands,approve,deny} and adds `_TUI_EXTRA`=
  {compact,details,logs,mouse} (`server.py:6437,6447`).

### Commands that OPEN a UI surface (port priority — need a component)
| Command(s) | Opens | Ink component |
|---|---|---|
| `/model` (bare) | model picker | `modelPicker.tsx` (`appOverlays.tsx:161`) |
| `/sessions` `/resume` `/switch` `/session` | session switcher | `activeSessionSwitcher.tsx` (`appOverlays.tsx:145`) |
| `/skills` (bare) | skills hub | `skillsHub.tsx` (`appOverlays.tsx:173`) |
| `/agents` `/tasks` `/replay` `/replay-diff` | agents dashboard | `agentsOverlay.tsx` (`appLayout.tsx:409`) |
| `/new` `/clear` | confirm dialog | `prompts.tsx` ConfirmPrompt (`appOverlays.tsx:49`) |
| `/status` `/usage` `/history` `/logs` `/tools` `rollback diff`, long `/skills` | pager | `FloatBox` pager (`appOverlays.tsx:177`) |
| `/help` | inline panel (not overlay) | `transcript.panel()` (`core.ts:108`) |

### TUI-only client commands (13, NOT in COMMAND_REGISTRY — must reimplement)
`mouse/scroll`, `redraw`, `compact`, `details`, `fortune`, `terminal-setup`, `logs`(→pager),
`sessions`(→switcher), `replay`/`replay-diff`(→agents), `setup`(suspend+shell), `heapdump`, `mem`.
Defined in `ui-tui/src/app/slash/commands/{core,session,ops,setup,debug}.ts`.

### Full 70-command registry (abridged — full table in recon, all in `commands.py:64-225`)
- **Session (29):** start, new/reset, topic, clear, redraw, history, save, retry, undo, title,
  handoff, branch/fork, compress, rollback, snapshot/snap, stop, approve, deny, background/bg/btw,
  agents/tasks, queue/q, steer, goal, subgoal, status, sethome, resume, sessions, restart.
- **Configuration (15):** config, model, codex-runtime, personality, statusbar/sb, verbose, footer,
  yolo, reasoning, fast, skin, indicator, voice, busy.
- **Tools & Skills (12):** tools, toolsets, skills, bundles, cron, curator, kanban, reload,
  reload-mcp, reload-skills, browser, plugins.
- **Info (13):** whoami, profile, gquota, commands, help, usage, insights, platforms/gateway,
  platform, copy, paste, image, update, version/v, debug.
- **Exit (1):** quit/exit.
Subcommand completion declared for: footer, reasoning, fast, voice, busy, indicator, skills, cron,
curator, kanban.

### Autocomplete (`ui-tui/src/hooks/useCompletion.ts:41`)
`looksLikeSlashCommand` → `complete.slash` RPC (server builds `SlashCommandCompleter` from registry
+ skills/bundles + TUI extras, caps 30); else `complete.path`. `/model …` returns null → uses the
picker instead. Dropdown rendered in `FloatingOverlays` (`appOverlays.tsx:203`).

---

## 2. OVERLAYS / MODALS / POPUPS / PROMPTS

State: single atom `$overlayState` (`ui-tui/src/app/overlayStore.ts:19`, 11 slots); computed
`$isBlocked` hides the composer when any slot is set (`appLayout.tsx:273`). Two render zones:
**PromptZone** (inline blocking prompts, priority approval→confirm→clarify→sudo→secret) and
**FloatingOverlays** (dropdowns above composer). Agents overlay replaces the transcript pane.
Lifecycle: `resetFlowOverlays()` clears prompts/pager at turn-end but **preserves** user overlays
(agents/modelPicker/sessions/skillsHub).

### 2a. ✅ BLOCKING gateway prompts — DONE (Phase 4; was 🔴 unhandled = deadlock)
Dispatched in `createGatewayEventHandler.ts:722-747` (Ink); in the OpenTUI engine handled by
`src/gateway/eventAdapter.ts` → prompt channel → `src/components/prompts/promptOverlay.tsx`,
replied via the `*.respond` RPCs. Verified by `bun src/demo.prompts.tsx` (45/45 green).

| Event | Payload | Component | Responds | RPC reply | Port |
|---|---|---|---|---|---|
| `clarify.request` | `{choices[]\|null, question, request_id}` | `prompts/clarifyPrompt.tsx` (`<select>`+Other→free-text) | ↑↓/1-N/Enter, "Other"→free-text, Esc | `clarify.respond {answer, request_id}` | ✅ |
| `approval.request` | `{command, description}` | `prompts/approvalPrompt.tsx` (`<select>`) | ↑↓/1-4 once/session/always/deny, Esc/Ctrl+C→deny | `approval.respond {choice, session_id}` | ✅ |
| `sudo.request` | `{request_id}` | `prompts/maskedPrompt.tsx` 🔐 | masked pw, Enter, Esc/Ctrl+C→'' | `sudo.respond {password, request_id}` | ✅ |
| `secret.request` | `{env_var, prompt, request_id}` | `prompts/maskedPrompt.tsx` 🔑 | masked input, Enter, Esc/Ctrl+C→'' | `secret.respond {value, request_id}` | ✅ |

Cancel paths (Ctrl+C/Esc) send the deny/cancel RPC so the agent unblocks. **`confirm`** is a local
(non-gateway) blocking dialog (`prompts/confirmPrompt.tsx`, Y/N/Esc) driven by a local callback
(`gw.onLocalConfirm`), not an RPC — ✅ included.

### 2b. Floating overlays / pickers
| Name | Trigger | Component | Port |
|---|---|---|---|
| Model picker | `/model`, embedded in switcher | `modelPicker.tsx` | hard (multi-stage + fuzzy + key entry) |
| Session switcher | `/resume`, **Ctrl+X**, click count | `activeSessionSwitcher.tsx` | hard (merged list + embeds model picker + close/delete RPCs) |
| Skills hub | `/skills` | `skillsHub.tsx` | hard (3-stage + install) |
| Agents dashboard | `/agents`, `/replay*` | `agentsOverlay.tsx` | hard (tree + Gantt + accordions + draggable scrollbar; largest single port) |
| Pager | `transcript.page()` — many `/cmd`s | `appOverlays.tsx:177` | moderate (porting it unlocks `/status /logs /history /tools` at once) |
| Completions dropdown | typing `/` or path | `appOverlays.tsx:203` | moderate |

### 2c. Passive / inline (not overlay slots, don't block)
Help hint (`?` card, `helpHint.tsx`), queued-messages strip (`queuedMessages.tsx`), todo panel
(`todoPanel.tsx`), thinking/reasoning + subagent tree (`thinking.tsx`, inline transcript), `/help`
panel (`transcript.panel()`), FPS overlay (`fpsOverlay.tsx`).

---

## 3. CHROME (persistent UI) + AGENT FEATURES — the gap list

Ink chrome composed in `appLayout.tsx`; the **status rule** (`appChrome.tsx:390`) is one
progressively-disclosed line. Live turn state in `turnStore.ts` (`TurnState`); UI state in
`uiStore.ts`. (Note: there is **no `turnController.ts` file** — `turnController.*` is an object
invoked from `createGatewayEventHandler.ts`.)

### Chrome gaps
| Feature | Ink | OpenTUI | Port | Drives from |
|---|---|---|---|---|
| Model in header | `appChrome.tsx:547` | ❌ | trivial | `SessionInfo.model/reasoning_effort/fast` |
| Session id | `branding.tsx:296` | ❌ | trivial | session.info |
| cwd / branch label | `appChrome.tsx:614` | ❌ | trivial | `SessionInfo.cwd` |
| Context % + token bar | `appChrome.tsx:551` | ❌ | moderate | `Usage.context_*` |
| Cost read-out | `appChrome.tsx:596` | ❌ | moderate | `Usage.cost_usd` |
| Compressions/duration/dev-credits | `appChrome.tsx:564-607` | ❌ | moderate | Usage/session |
| Update-available banner | `branding.tsx:397` | ❌ | trivial | `SessionInfo.update_behind/command` |
| Profile in prompt | `appLayout.tsx:179` | ❌ | trivial | `SessionInfo.profile_name` |
| MCP servers panel | `branding.tsx:246` | ❌ | moderate | `SessionInfo.mcp_servers[]` |
| Banner / SessionPanel intro | `branding.tsx:85/160` | ❌ | moderate | theme/session |
| Response separator `───` | `appLayout.tsx:108` | ❌ | trivial | history roles |
| Draggable scrollbar | `appChrome.tsx:653` | ⚠️ (auto only) | moderate | scroll state |
| Sticky-prompt line | `appLayout.tsx:245` | ❌ | moderate | viewport scroll |
| FPS overlay / help hint / GoodVibesHeart | various | ❌ | trivial | cosmetic |
| Busy face/verb/elapsed ticker | `appChrome.tsx:119` | ⚠️ (text only) | moderate | turn timing |
| Queued messages | `queuedMessages.tsx` | ❌ | moderate | composer queue |
| Multiline input / paste / history | `textInput.tsx` | ❌ | moderate | replaces `<input>` |

### Agent-feature gaps (each: gateway event → turn field → renderer)
| Feature | Ink renderer | OpenTUI | Port |
|---|---|---|---|
| Reasoning/thinking display | `thinking.tsx:621` (`reasoning.delta/available`) | ❌ (data captured on `Msg.thinking`, never rendered) | moderate |
| Tool trail (live spinner+args+timing+collapse) | `thinking.tsx:689` (`tool.start/generating/progress`) | ⚠️ flat labels only | moderate |
| Tool result (inline diffs) | inline-diff path `cgeh:698` | ⚠️ compact block (capped+strip+expand; no inline diffs yet) | moderate |
| Subagents/delegation tree | `thinking.tsx:281` + `agentsOverlay` (`subagent.*`) | ❌ (dropped) | **hard** (biggest) |
| Delegation HUD (SpawnHud) | `appChrome.tsx:270` (`$delegationState`) | ❌ | hard |
| Todos panel | `todoPanel.tsx` (`payload.todos`) | ❌ | moderate |
| Activity feed | `thinking.tsx:878` (status/stderr) | ❌ | hard (coupled to `/details` section visibility) |
| Notifications sticky/ttl | `appChrome.tsx:533` (`notification.show/clear`) | ❌ | moderate |
| Voice listening/transcribing | `appChrome.tsx:578` (`voice.status/transcript`) | ❌ | moderate |
| Browser progress | system line (`browser.progress`) | ❌ | trivial |
| Background-task completion + count | `cgeh:752`, count `:590` (`background.complete`) | ❌ | trivial |

---

## 4. RECOMMENDED PORT ORDER (consolidated)

1. **Phase 4 — 🔴 blocking prompts + confirm** (§2a). Deadlock-critical; sudo/secret trivial,
   clarify/approval moderate. Makes any non-trivial session actually usable. **DO THIS FIRST.**
2. **Wire `session.info` + `Usage` into the adapter** → unlocks most trivial chrome (model, cwd,
   context%, cost, update banner, profile) in one stroke.
3. **Reasoning render + tool trail** (data largely already captured) + todos panel.
4. **Pager + completions dropdown** → unlocks many `/commands` + slash autocomplete.
5. **Slash command system** (catalog RPC + dispatch ladder + the 13 TUI-only cmds).
6. **Pickers:** model → session switcher → skills hub.
7. **Subagents tree + agents dashboard + SpawnHud** (hardest; last).
8. **Polish:** banner/SessionPanel/MCP panel, sticky-prompt, draggable scrollbar, queued msgs,
   multiline input, notifications, voice, FPS/help-hint.

This map IS the backlog. Each row is an independently portable unit with its Ink reference.
