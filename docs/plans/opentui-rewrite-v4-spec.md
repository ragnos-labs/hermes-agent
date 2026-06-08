# Spec v4: Native OpenTUI engine — Solid + Effect-at-the-boundary, from scratch

**Status:** ACTIVE. Supersedes `opentui-native-rewrite-spec.md` (v3, React + path-import + shim
lineage) and the `ui-tui-opentui/` React build (reference-only; nothing carried forward).
**Date:** 2026-06-08
**Author:** Hermes (for glitch)
**Branch:** `feat/opentui-native-engine` (new package; Phase 0 may rebase onto a fresh branch off
`main` per glitch's call — see §12).

**Lineage / what changed from v3:**
- v3 chose **`@opentui/react`** + reuse-the-Ink-logic-via-path-import. v4 reverses both:
  **`@opentui/solid` + `solid-js`** (mirror opencode exactly) and a **pure-scratch** logic layer
  (no path-import of `ui-tui/src/**`; we re-author the small slice we need in Solid/TS).
- v3 left Effect out entirely. v4 puts **Effect 4.0-beta at the BOUNDARY only** — renderer
  lifecycle, the Python-gateway transport, Schema decoding (config + the `GatewayEvent` union),
  typed errors at those edges, and the runtime. The **logic layer (store/reducer/turn state/slash
  dispatch) is plain Solid** (`createStore` + reducers, à la opencode `sync-v2`). This reverses an
  earlier "Effect deep into the logic layer" idea: an Effect runtime threaded through the component
  tree fights Solid's fine-grained reactivity, so we keep it out.
- Everything load-bearing in v3 that is still TRUE is carried forward: the launcher cutover points
  (§4 v3 → §9 here), the distribution realities (Bun + per-arch native lib; §5 v3 → §10 here), and
  the hard-won render gotchas (§8 here).

> **Why this is not a port of `ui-tui-opentui/`.** That package (40 files, React) proved the render
> path, the prompt-deadlock fix, the launcher cutover, and the tool/markdown/resume gotchas — all
> valuable as *reference*. But its reactivity model (React re-render) and its logic-reuse strategy
> (path-import Ink) are both things v4 deliberately abandons. We mine it for **gotchas and the
> gateway event contract**, then build fresh. The agent may do a contained nuke/replace of
> `ui-tui-opentui/` once the new package supersedes it — **never touch `ui-tui/`.**

---

## 0. Settled decisions (do not relitigate)

| Decision | Value | Rationale |
|---|---|---|
| View binding | `@opentui/solid` + `solid-js@1.9.10` | mirror opencode; fine-grained reactivity fits streaming TUIs |
| Effect scope | BOUNDARY only (`effect@~4.0.0-beta.78`) | opencode's actual model; don't fight Solid |
| Logic layer | plain Solid (`createStore` + `apply(event)` reducer) | opencode `sync-v2`; idiomatic, reactive |
| Source | pure scratch package | no carry from `ui-tui-opentui/` |
| Transport | OUR Python `tui_gateway` via JSON-RPC `GatewayClient` | keep it; do NOT adopt opencode's HTTP/SSE-to-TS-server |
| Tests | plain `bun test` | NO vitest, NO `@effect/vitest` |
| `@opentui/*` versions | `core`+`keymap`+`solid` aligned (opencode pins 0.3.2) | lockstep |
| `@effect/*` | all lockstep with `effect@beta` | effect-ts skill rule |
| Design language | **our Ink TUI (for now)**; converge on opencode's good taste where clearly better | glitch's call — Ink-led look/layout/UX, opencode for METHOD/structure |
| Theming | **fully skinnable — NO hardcoded styles**; mirror Ink's skin→Theme contract so existing skins work unchanged | glitch's call (2026-06-08) |
| Branch | build on `feat/opentui-native-engine` (React pkg coexists, nuke at cutover) | glitch's call |
| New package dir | `ui-tui-opentui-v2/` during build → rename at Phase 8 cutover | glitch's call |
| Keymap | adopt `@opentui/keymap` host (mirror opencode) | glitch's call |
| Ink (`ui-tui/`) | UNTOUCHED, ships as default | dual-engine, possibly forever |

---

## 1. The boundary line (internalize this — it governs every file)

```
                       ┌─────────────────────────── EFFECT (the edges) ───────────────────────────┐
  launcher  ──argv──▶  entry: Effect.fn("Tui.run")                                                 │
  (caller                ├─ acquireRelease(createCliRenderer …) + finalizers + Deferred-on-destroy │
   provides              ├─ GatewayService  (transport layer: subscribe(handler) + request(m,p))   │
   layers,               ├─ Config          (Schema-decoded display config)                        │
   runMain)              ├─ GatewayEvent    Schema (decode unknown wire JSON ONCE)                  │
                         └─ typed errors at those edges; ManagedRuntime / BunRuntime.runMain        │
                       └───────────────────────────────────────────────────────────────────────────┘
                                     │  (1) ONE bridge:  render(() => <App/>, renderer)
                                     │  (2) GatewayService.subscribe(decodedEvent => store.apply(e))
                                     ▼
                       ┌─────────────────────────── SOLID (everything visible + stateful) ─────────┐
                         createStore session/message store  +  apply(event) reducer  (sync-v2 model)│
                         turn state · slash registry/dispatch · signals/memos · ALL renderables     │
                         (this is where reactivity lives — keep it idiomatic Solid, NOT Effect-ified)│
                       └───────────────────────────────────────────────────────────────────────────┘
```

**The ONLY two Effect↔Solid contact points:**
1. The single `render(() => <App/>, renderer)` bridge inside the entry Effect's scope.
2. `GatewayService` pushing *decoded* events into the Solid store via a `subscribe(callback)`.

**No Effect runtime in components. No Solid reactivity inside Effect boundary code.** If you find
yourself yielding an Effect inside a component or wrapping a `createStore` in a service, you've
crossed the line — stop.

### What is Effect, what is Solid (concrete file assignment)

| Concern | Side | Why |
|---|---|---|
| Renderer lifecycle (`createCliRenderer`, destroy, finalizers) | Effect | resource — `acquireRelease` |
| Python gateway transport (spawn, JSON-RPC, reconnect) | Effect (`GatewayService`) | external boundary + typed failures |
| Decoding wire JSON → `GatewayEvent` | Effect (Schema) | "decode unknown once at the boundary" |
| `display.*` config read | Effect (Schema) | decode unknown once |
| The runtime (`runMain` / `ManagedRuntime`) | Effect | edge only |
| Session/message **store** + `apply(event)` reducer | **Solid** | reactive state, opencode sync-v2 |
| Turn state (streaming flag, parts, busy) | **Solid** | reactive |
| Slash registry + dispatch ladder | **Solid** | plain data + functions |
| Every `.tsx` renderable, signals, memos | **Solid** | the view |
| Theme / SyntaxStyle | **Solid** (a `Theme` service only if it needs IO) | mostly pure data |

---

## 2. Package layout (pure scratch)

New sibling package. Working name **`ui-tui-opentui-v2/`** during the build so it coexists with the
superseded `ui-tui-opentui/` until cutover; **renamed to the canonical home at cutover** (likely
`ui-tui-opentui/` after the old one is nuked, or kept as a distinct name — glitch's call at §9
launcher repoint). Ink stays at `ui-tui/`, untouched.

```
ui-tui-opentui-v2/
  package.json            # type: module; deps below; scripts: check / type-check / lint / fmt / test
  tsconfig.json           # strict rails (§6); jsxImportSource @opentui/solid
  bunfig.toml             # preload = ["@opentui/solid/preload"]   (required by the Solid binding)
  eslint.config.mjs       # mirror ui-tui rule style (typescript-eslint, unused-imports, perfectionist)
  .prettierrc             # mirror ui-tui
  scripts/check.sh        # bun run check: type-check + lint + bun test + headless frame gate
  .repos/effect           # symlink → ~/github/effect-smol (4.0.0-beta.78) IF the effect-ts skill
                          #   wants it package-local; the worktree-root .repos/effect already exists
  src/
    boundary/             # ── EFFECT side ──
      runtime.ts          # AppLayer composition; ManagedRuntime / BunRuntime.runMain helper
      renderer.ts         # acquireRelease(createCliRenderer) + finalizers + Deferred-on-destroy
      gateway/
        GatewayService.ts # Context.Service<GatewayService, { subscribe; request }>; static layer
        liveGateway.ts    # Layer wrapping the real JSON-RPC client (spawns tui_gateway)
        client.ts         # the JSON-RPC transport itself (EventEmitter-free, Effect-native or a
                          #   thin wrapper over a re-authored client) — see §4
        python.ts         # principled python resolution (HERMES_PYTHON / *_SRC_ROOT → .venv → …)
      schema/
        GatewayEvent.ts   # Schema.toTaggedUnion("type") over the wire union (decode unknown once)
        Config.ts         # Schema for the display.* slice we consume
      errors.ts           # Schema-based tagged errors (wire) + Data.TaggedError (internal)
    logic/                # ── SOLID side (NOT Effect) ──
      store.ts            # createStore<{ messages, turn, … }> + apply(event) reducer (sync-v2 model)
      parts.ts            # ordered Part model (text/reasoning/tool) — see §7
      turn.ts             # turn/busy/streaming signals
      slash/
        registry.ts       # client-local command table + dispatch ladder (§ feature-map §1)
        commands/*.ts     # the 13 TUI-only client commands
      theme.ts            # theme data + SyntaxStyle.fromStyles (memoized)
    view/                 # ── SOLID renderables ──
      App.tsx             # the shell: header + transcript scrollbox + composer + overlay zones
      transcript.tsx      # ONE <scrollbox> (apply §8 gotchas) + <For> over messages
      messageLine.tsx     # part dispatch loop; tool render (compact); markdown→native <markdown>
      markdown.tsx        # thin wrapper over native <markdown> + SyntaxStyle
      composer.tsx        # <input>/<textarea>, clear-on-submit, flexShrink:0
      header.tsx          # model / cwd / context% / cost / update banner
      prompts/            # clarify / approval / sudo / secret / confirm overlays + cancel paths
      overlays/           # pager, completions dropdown, model picker, session switcher, skills hub,
                          #   agents dashboard
      chrome/             # todo panel, thinking trail, subagent tree, notifications, queued strip
    entry/
      main.tsx            # the launcher entry: build AppLayer, Effect.provide, runMain, render bridge
      fakeGateway.ts      # FakeGateway layer (test/dev) — emittable event source + spy request
    test/
      lib/effect.ts       # testEffect/testLayer over per-file ManagedRuntime + TestContext/TestClock
      lib/render.ts       # createTestRenderer + captureCharFrame helpers, mockInput/mockMouse
      lib/fakeTransport.ts# fake GatewayService layer (fake request + emittable events) for store tests
      *.test.ts(x)        # 4 layers (§5)
```

**Deps (pinned):**
```jsonc
{
  "dependencies": {
    "effect": "~4.0.0-beta.78",
    "@opentui/core": "0.3.2",
    "@opentui/solid": "0.3.2",
    "@opentui/keymap": "0.3.2",      // if keymap host is adopted (opencode uses it)
    "solid-js": "1.9.10"
  },
  "devDependencies": { "@types/bun": "latest", "eslint": "^9", "prettier": "^3", "typescript": "^5" }
}
```
All `@effect/*` (if any added — likely none needed; we use `effect` core + `effect/unstable/*`)
stay lockstep. **No `@effect/platform-*`** — on the 4.0 line platform modules live under
`effect/unstable/*` (effect-4-beta skill §2).

---

## 3. The Effect boundary (detailed)

### 3.1 Entry / runtime (mirror opencode `app.tsx:177` `run = Effect.fn("Tui.run")`)

```ts
// entry/main.tsx
export const run = Effect.fn("Tui.run")(function* (input: TuiInput) {
  yield* Effect.scoped(Effect.gen(function* () {
    const renderer = yield* Effect.acquireRelease(
      Effect.tryPromise(() => createCliRenderer({
        externalOutputMode: "passthrough",   // opencode app.tsx:184 — scrollbox clips, no scrollback corruption
        targetFps: 60,
        exitOnCtrlC: false,                   // prompts own Ctrl+C → deny/cancel (gotcha §8)
        useKittyKeyboard: {},
        useMouse: input.config.mouse,
      })),
      (r) => Effect.sync(() => destroyRenderer(r)),
    )
    const shutdown = yield* Deferred.make<void>()
    renderer.once("destroy", () => Deferred.doneUnsafe(shutdown, Effect.void))
    const gateway = yield* GatewayService            // the transport, already a layer
    yield* Effect.tryPromise(async () => {
      await render(() => <App gateway={gateway} />, renderer)  // THE one bridge line
    })
    yield* Deferred.await(shutdown)                  // block until renderer destroyed
  }))
})
```
Caller (the launcher, `entry/main.tsx` bottom) provides the layers and runs:
```ts
run(input).pipe(Effect.provide(AppLayer), BunRuntime.runMain)   // layers provided at the edge ONLY
```
`AppLayer = Layer.mergeAll(GatewayService.layer, Config.layer)` (+ Theme if it needs IO). Mirrors
opencode `cli/tui/layer.ts:6` (`Effect.provide(Global.defaultLayer)`) and `cli/cmd/tui.ts` runMain.

### 3.2 GatewayService (the transport, as a service/layer)

`class GatewayService extends Context.Service<GatewayService, Shape>()("@hermes-tui/GatewayService") {}`
(4.0 `Context.Service`, NOT `Context.Tag`/`Effect.Service` — effect-4-beta skill §2).

```ts
interface Shape {
  // push decoded events into the Solid store; returns an unsubscribe
  subscribe: (handler: (event: GatewayEvent) => void) => Effect.Effect<() => void>
  // typed request to the python gateway
  request: <A>(method: string, params: unknown) => Effect.Effect<A, GatewayError>
  sessionId: () => string | undefined          // for approval.respond {session_id}
}
static layer  // = liveGateway (spawns tui_gateway, wires JSON-RPC)
```
- `liveGateway.ts` wraps OUR JSON-RPC transport (see §4). It decodes each inbound wire frame with the
  `GatewayEvent` Schema **once**, then calls the subscriber with the typed value. Coalesce events on
  a **16ms debounce flushed inside Solid `batch()`** so a burst of deltas is one repaint (opencode
  `sdk.tsx:54-80`). The `batch()` call is the boundary handing off to Solid — fine.
- `request` returns a typed `Effect` that fails with `GatewayError` (timeout / transport-down /
  rpc-error), never throws. The `*.respond` replies (clarify/approval/sudo/secret) go through
  `request` too.
- **Tests swap `FakeGateway.layer`** — same `Shape`, fake `request`, an emittable event source.

### 3.3 Schema (`GatewayEvent`, `Config`) — decode unknown ONCE

The wire union is ~40 members (`ui-tui/src/gatewayTypes.ts:509-587`, discriminated on `type`).
Model it with `Schema.Class` members + `Schema.toTaggedUnion("type")` (effect-4-beta §2; verify the
exact symbol in `.repos/effect/packages/effect/src/Schema.ts` — it's `Schema.toTaggedUnion` /
`Schema.TaggedClass` on this line, NOT 3.x `Schema.TaggedErrorClass`). Decode with
`Schema.decodeUnknown*` at the transport boundary; **no `JSON.parse`, no `as`, no shape-probing.**
Types are **inferred** from the schema (`typeof Event.Type`). Unknown event types decode to a
catch-all/ignored member rather than throwing — the reducer simply has no case for them (matches the
v3 "ignored cosmetic/deferred" set).

**The union, grouped (drives the parity phases):**
- lifecycle: `gateway.ready`, `session.info`, `skin.changed`
- streaming text: `message.start`, `message.delta`, `message.complete`
- reasoning: `reasoning.delta`, `reasoning.available`, `thinking.delta`
- tools: `tool.start`, `tool.complete`, `tool.progress`, `tool.generating`
- 🔴 blocking prompts: `clarify.request`, `approval.request`, `sudo.request`, `secret.request`
- chrome/agent: `status.update`, `notification.show`, `notification.clear`, `voice.status`,
  `voice.transcript`, `browser.progress`, `background.complete`, `review.summary`,
  `subagent.*` (6)
- transport errors: `error`, `gateway.stderr`, `gateway.start_timeout`, `gateway.protocol_error`

### 3.4 Typed errors

- **Wire/serializable** (anything that crosses the RPC or gets persisted): Schema-based tagged
  errors — `Schema.Class`/`Schema.toTaggedUnion` or `Schema.TaggedError` (verify the 4.0 symbol in
  `.repos/effect`).
- **Internal** (renderer acquire failure, python-not-found, transport-down): `Data.TaggedError`.
- In boundary generators: `return yield* new FooError(...)` — **no `Effect.fail(...)` ladder, no
  throw / try-catch / Promise.catch / orDie** in boundary code.

---

## 4. Transport — keep OUR Python `tui_gateway` (do NOT adopt opencode's HTTP/SSE)

The contract (from Ink `ui-tui/src/gatewayClient.ts`):
- Spawn `python -m tui_gateway.entry` (`gatewayClient.ts:338`) with a **principled python
  resolution** that mirrors Ink's `resolvePython` (`gatewayClient.ts:45-64`) **1:1**:
  `HERMES_PYTHON`/`PYTHON` env → `$VIRTUAL_ENV/bin/python` (or `Scripts/python.exe`) →
  `<root>/.venv/bin/python(3)` → `<root>/venv/bin/python(3)` → bare `python3` (`python` on win32).
  **Never "probe any python".** `HERMES_PYTHON_SRC_ROOT` pins the source root. (Implemented in
  `boundary/gateway/python.ts`; the earlier draft's `~/.hermes/hermes-agent/venv` step is NOT in Ink
  and was dropped to keep the engines identical — add it later only if Ink gains it.)
- JSON-RPC over the child's stdio (newline-delimited frames); `request(method, params)` returns a
  promise resolved by id; an `'event'` stream pushes `GatewayEvent`s.
- Lifecycle: `new → start() → on('event') → drain`; reconnect with backoff (the Ink client buffers
  events + has a WebSocket attach mode — we only need the spawn+stdio path for the launcher).

**v4 wrapping decision:** re-author a **minimal Effect-native client** in `boundary/gateway/client.ts`
rather than path-importing Ink's `GatewayClient` (which extends `node:events.EventEmitter` and is
600+ LOC of attach-mode/buffering we don't need). The client:
- spawns the child (Bun `Bun.spawn`), frames stdio, resolves `request` ids;
- exposes an injectable interface the `liveGateway` layer adapts to `GatewayService.Shape`;
- exponential-backoff reconnect on child death (mirror opencode `sdk.tsx:112-116`).
The **wire contract** (method names, event shapes, the `*.respond` params) is copied verbatim from
`ui-tui/src/gatewayTypes.ts` + the Phase-4 reply contract (below) — that's the part that must not
drift, and the `GatewayEvent` Schema is the single source of truth for it.

**`*.respond` reply contract (verified against Ink `gatewayClient.ts` + v3 Phase 4):**
`clarify.respond {answer, request_id}` · `approval.respond {choice, session_id}` ·
`sudo.respond {password, request_id}` · `secret.respond {value, request_id}`. **Every cancel path
(Esc/Ctrl+C) MUST send the deny/empty reply** (approval→`deny`; sudo/secret→`''`; clarify→empty
answer; confirm→local `false`) so the python agent unblocks.

---

## 5. Test strategy — four layers + a per-phase agentic smoke (all on `bun test`)

### Layer 1 — Effect-boundary code (transport service, Schema, config, errors)
executor-style, on `bun test`. Write `test/lib/effect.ts` exposing `testEffect`/`testLayer` over a
**per-file `ManagedRuntime` + `TestContext`/`TestClock`** (recovers `it.effect` ergonomics WITHOUT
`@effect/vitest`). Inject `FakeGateway.layer`; assert decoded events / typed failures via
`Effect.flip`. Tests: `GatewayEvent` decode round-trips, unknown-type fall-through, `request`
timeout → typed `GatewayError`, the `*.respond` param shapes.

### Layer 2 — Renderables / components
opentui + opencode style: `createTestRenderer` (`@opentui/core/testing`) / `testRender` (Solid
binding) → `renderOnce`/flush → `captureCharFrame` → snapshot. Assert renderable **geometry**
(`scrollHeight`/`scrollTop`/viewport) for layout-sensitive bits (the scrollbox §8 gotchas);
`mockInput`/`mockMouse` for input; `resize()` for reflow. **Pass `exitOnCtrlC:false`** to the test
renderer (gotcha §8 — default `true` tears down on first simulated Ctrl+C).

### Layer 3 — TUI logic (the Solid store/reducer, slash dispatch)
opencode `sync-v2` style: mount the provider tree under a `TestContexts` fixture, inject a **fake
`GatewayService`** (fake `request` + emittable event source), a `Probe` captures context,
`wait(predicate)` polls. Test the `apply(event)` reducer as **pure data behavior** — streaming
concat, LRU id dedup, ordered-parts interleave (text→tool→text), hydrate-while-buffering on resume.

### Layer 4 — Hermes-specific contract
Mirror what Ink's vitest asserts (`ui-tui/src/**/*.test.ts` — messages/fuzzy/text/todo/stores) +
**gateway-contract tests**: `GatewayEvent` decode for every member, the resume tool-row
`{role:'tool', name, context}` shape (no `.text` — gotcha §8), the `*.respond` cancel paths, the
prefer-`text`-over-`rendered` rule.

### Per-phase agentic smoke (the living complement to the headless gate)
Maintain `docs/plans/opentui-smoke.md` — the canonical end-to-end drive (launch → type → submit →
streamed reply → run a tool → open a modal/slash popup → answer a prompt → resume → resize → quit),
each step with its expected on-screen observation. **Every phase:** an agent DRIVES the live TUI in
tmux (real TTY) through the scenario (new features + all prior still work), then APPENDS the new
steps. The smoke compounds into the full acceptance routine. **A phase is not complete until the
smoke doc is updated AND passes, and `bun run check` is green.**

`bun run check` gate = type-check + lint + `bun test` + headless frame verification. Green every
phase.

---

## 6. tsconfig / lint rails

```jsonc
// tsconfig.json compilerOptions
{
  "strict": true,
  "verbatimModuleSyntax": true,
  "exactOptionalPropertyTypes": true,
  "noUncheckedIndexedAccess": true,
  "moduleResolution": "bundler",
  "module": "preserve",
  "jsx": "preserve",
  "jsxImportSource": "@opentui/solid",
  "types": ["bun"],
  "skipLibCheck": true
}
```
**No `any`, no `as`, no escape hatches** in boundary code (effect-ts + executor rules). The Solid
view follows TS-strict but isn't bound by the Effect conventions (it's not Effect).

---

## 7. Ordered-parts model (carried conceptually from the parts/markdown plan; re-authored in Solid)

Mirror opencode `sync-v2`: one assistant turn = **one ordered `parts[]`** of a discriminated union,
rendered by **one dispatch loop**. This is the structural fix for "tool calls dump below the message".

```ts
type Part =
  | { type: "text";      id: string; text: string }
  | { type: "reasoning"; id: string; text: string }
  | { type: "tool";      id: string; name: string; state: "running" | "complete";
      resultText?: string; summary?: string; error?: string; lineCount?: number }
```
Reducer (`logic/store.ts`, plain Solid `createStore` + `produce`):
- `message.start` → push `{ role:"assistant", parts:[], streaming:true }`.
- `message.delta` → if last part isn't an open `text`, push one; else append `payload.text`
  (**prefer `text` over `rendered`** — gotcha §8).
- `tool.start` → push `{ type:"tool", state:"running", name }`; remember id.
- `tool.complete` → update THAT part in place (`state:"complete"` + envelope-stripped fields);
  **do NOT push a separate `role:'tool'` row** (that was the dump-below bug).
- `reasoning.delta`/`thinking.delta` → positioned `reasoning` part (push-if-not-open, else append).
- `message.complete` → `streaming:false`; finalize last text part.

Render (`view/messageLine.tsx`): `<For each={turn.parts}>` → `<Switch>`/`<Match>` on `part.type` →
inline flex siblings, correct interleave. **Stable `key`/`id` per part** stops the streaming
`<markdown>` above a new tool part from re-mounting/re-tokenizing. Resume: settled turns load as
single-text or standalone tool rows (`{name, context}`) — render directly, don't read `.text`.

**Markdown:** native `<markdown>` (`MarkdownRenderable`) with `streaming` + `internalBlockMode="top-level"`
+ one memoized `SyntaxStyle.fromStyles({...})` per theme (opencode patterns §5/§6) — **never hand-roll
a parser**. Reasoning bodies via `<code filetype="markdown" streaming>` (dimmer).

---

## 7.5. Theming / skins (fully skinnable — NO hardcoded styles)

**Requirement (glitch, 2026-06-08): the UI must be skinnable and honor EXISTING Hermes skins.** No
hardcoded colors/styles in components. The Phase-0 `App.tsx` hexes (`#8BD5CA`, …) are placeholders
to be removed the moment the theme layer lands (Phase 1).

**Contract to mirror (authoritative = Ink `ui-tui/src/theme.ts` + `ui-tui/src/gatewayTypes.ts`):**
- The gateway already emits the skin: `gateway.ready` carries `payload.skin?: GatewaySkin`, and
  `skin.changed` carries a `GatewaySkin` payload. `GatewaySkin = { colors?: Record<string,string>,
  branding?: Record<string,string>, banner_hero?, banner_logo?, help_header?, tool_prefix? }`.
- Ink maps a skin → a `Theme` via `fromSkin(colors, branding, bannerLogo, bannerHero, toolPrefix,
  helpHeader)`. `Theme = { color: ThemeColors (35 keys), brand: ThemeBrand (7 keys), bannerLogo,
  bannerHero }`. The skin `colors` use keys like `ui_primary`, `ui_accent`, `banner_title`,
  `banner_accent`, `ui_text`, `ui_border`, `ui_ok`/`ui_error`/`ui_warn`, `completion_menu_*`,
  `selection_bg`, `shell_dollar`, `prompt`, `session_label/border`, `banner_dim` — each with a
  documented fallback chain onto `DEFAULT_THEME`. Light/dark auto-detect (`detectLightMode` via
  `HERMES_TUI_LIGHT`/`THEME`/`BACKGROUND`/`COLORFGBG`/`TERM_PROGRAM`) + an Apple-Terminal ANSI-256
  normalization pass.

**v4 implementation (pure scratch, idiomatic Solid — mirror opencode `context/theme.tsx`):**
- **PORT `theme.ts` into the package** (`logic/theme.ts`) — it's pure TS, zero Ink dependency, so
  re-author 1:1: `Theme`/`ThemeColors`/`ThemeBrand` types, `DARK_THEME`/`LIGHT_THEME`,
  `detectLightMode`, the ANSI normalization, and `fromSkin`. This guarantees **existing skins work
  unchanged** because the mapping is identical.
- **Expose via a Solid `ThemeProvider` context** (`view/theme.tsx`) holding the current `Theme` as a
  signal/store. The default is `DEFAULT_THEME`; on `gateway.ready{skin}` / `skin.changed` the
  reducer calls `fromSkin(...)` and updates the theme signal → the whole view re-styles reactively
  (fine-grained Solid repaint).
- **Components read `theme.color.*` / `theme.brand.*` ONLY** — never literals. A lint guard
  (custom rule or a grep gate in `check.sh`) forbids raw hex in `view/**` to keep it honest.
- For the native `<markdown>`/`<code>` renderables, build the `SyntaxStyle.fromStyles({...})` from
  `theme.color.*` too (one memoized per theme), so fenced-code highlighting tracks the skin.

This is folded into **Phase 1** (theme module + provider + skin events wired) so no further view
code accretes hardcoded styles; Phase 2's transcript/markdown consume it.

---

## 8. Carry-forward render gotchas (verified in the React polish pass — re-apply in Solid)

1. **Rich text** = `<b>`/`<i>`/`<span>` children, never `attributes={{bold:true}}` (it's a
   `TextAttributes` BITMASK). Inline color is `<span style={{ fg }}>`; `<text>` takes `fg` directly.
2. **scrollbox:** `minHeight:0` on the scroll wrapper AND the scrollbox; do **NOT** set
   `flexDirection` on the `<scrollbox>` ROOT (internal viewport/content children — setting it there
   breaks content-height measurement → phantom scroll offset, clips the top, leaves a gap). Use
   `stickyScroll` + `stickyStart="bottom"`.
3. **Composer:** `flexShrink:0` so it never collapses onto its rule under a full transcript. Clear
   imperatively on submit (uncontrolled ref → `.clear()`/`value=""`); double-Enter re-entrancy guard.
4. **message.delta/complete:** prefer `payload.text` over `payload.rendered` (rendered is
   incremental Rich-ANSI; appending injects raw escapes).
5. **Resume tool rows** arrive as `{role:'tool', name, context}` with **no text** — render them
   (don't read `.text` → blank).
6. **Blocking prompts deadlock the agent if unhandled** — each needs a prompt UI + the matching
   `*.respond` RPC + a cancel path (Esc/Ctrl+C → deny/empty). `exitOnCtrlC:false` on the renderer so
   the prompt owns Ctrl+C; gate the global Ctrl+C-quit on `!blocked`.
7. **Test renderer** defaults `exitOnCtrlC:true` — pass `false` in any Ctrl+C-bearing test.
8. **OpenTUI core is Bun/FFI-only** — run everything via `bun`, never node.

---

## 9. Launcher integration (carried from v3 §4; STILL the plan — re-verify anchors at Phase 8)

`HERMES_TUI_ENGINE=opentui` already branches in `hermes_cli/main.py` `_make_opentui_argv` (~1613 per
the goal; v3 recorded `_make_tui_argv` @ main.py:1530 as the single argv cutover). **Phase 8 work:**
repoint `_make_opentui_argv` from the OLD React entry (`ui-tui-opentui/src/entry.real.tsx`) to the
NEW Solid entry (`<new-pkg>/src/entry/main.tsx`); keep the engine gate + the dashboard PTY path
(`web_server.py` `_resolve_chat_argv`) consistent. Re-verify all line numbers against the tree at
that point (they drift). Carry-forward facts that are still true:
- BOTH spawn sites route argv through the shared helper; the V8 heap-cap env is only in `_launch_tui`
  and must stay gated on `engine=="ink"` (Bun is JSC — `--max-old-space-size`/`--expose-gc` are V8).
- `_resolve_tui_engine()` refuses `opentui` on Windows/Termux → falls back to Ink.
- Auto-fallback to Ink on OpenTUI launch failure.

## 10. Distribution (carried from v3 §5 — unchanged realities)

OpenTUI has **no build step** (Bun runs `.tsx` directly) but loads a **per-arch native Zig lib**
(`@opentui/core-linux-x64`, `-darwin-arm64`, …) that **cannot be inlined**. So: pip wheel stays
`py3-none-any`, OpenTUI is **runtime-provisioned** (Bun + lazy-install `@opentui/*`); Docker
**prebuilds at image build, never lazy-installs in container**; Termux/Windows = Ink only. A perf
bench vs Ink is the final justification gate.

---

## 11. Parity matrix plan — every feature-map row → a phase

`docs/plans/opentui-feature-map.md` is the **master backlog**; we EXTEND it into a living **3-way
matrix**: Ink (source-of-truth) ↔ opencode (method ref, if any) ↔ new build (status ❌/⚠️/✅ +
new file:line). **Every phase updates the rows it lands.** The judge verifies completion against the
matrix — a feature is done only when its row is ✅ with a test and a smoke-doc check. The build
starts from **zero** (the new package has nothing yet — the React `ui-tui-opentui/` ✅s do NOT
transfer).

### Phase map (each independently demoable + tested + smoked; commit per phase)

| Phase | Scope | Feature-map rows landed |
|---|---|---|
| **0 — scaffold** | new branch/pkg; deps; `.repos/effect`; tsconfig/lint/test rails + `test/lib/effect.ts`; the Effect runtime boundary + `acquireRelease(createCliRenderer)` + the one-line Solid `render` bridge; `FakeGateway.layer`; render "hello" + assert a captured frame in `bun test`; seed `opentui-smoke.md` + first agentic smoke | (foundation — no feature rows) |
| **1 — transport + store** | `GatewayService` layer wrapping `tui_gateway`; `GatewayEvent` Schema; the sync-v2 Solid reducer store (streaming concat + LRU dedup + hydrate-while-buffering). Behavioral tests + smoke | streaming text events; lifecycle (`gateway.ready`) |
| **2 — core transcript** | `<scrollbox>` (§8 gotchas), `messageLine` (ordered parts §7; compact tool render — one-line default / capped left-bar block / strip `{output,exit_code}` envelope), markdown→native `<markdown>`, composer (clear-on-submit), header skeleton. Frame-snapshot tests + smoke | §3 chrome: response separator; §1/§3 transcript; tool result (compact) ⚠️→ ; reasoning render |
| **3 — blocking prompts** 🔴 | clarify / approval / sudo / secret / confirm overlays + cancel paths + `*.respond` RPCs. **Deadlock-critical — makes real sessions usable** | feature-map §2a (all 4 + confirm) |
| **4 — session lifecycle + slash system** | create/resume (incl. tool-call render); slash-command SYSTEM = `commands.catalog` RPC + the dispatch ladder (`slash.exec` → `command.dispatch`) + the **13 TUI-only client commands** (mouse/redraw/compact/details/logs/sessions/replay/setup/heapdump/mem…) | §1 (full registry + dispatch + TUI-only); resume tool rows |
| **5a — pager + completions** | the `FloatBox` pager (porting it unlocks `/status /logs /history /tools` at once) + the completions dropdown | §2b pager, completions; §1 autocomplete |
| **5b — header/chrome** | model / cwd-branch / context%+token bar / cost / compressions-duration / update banner / profile / MCP panel / busy face-verb-elapsed / queued strip / sticky-prompt / draggable scrollbar / response sep / banner+SessionPanel | §3 chrome gaps (mostly trivial once `session.info`+`Usage` are wired) |
| **5c — pickers** | model picker → session switcher (Ctrl+X / `/resume`) → skills hub | §2b model picker, session switcher, skills hub |
| **5d — agent features** | reasoning/thinking trail; tool trail (live spinner+args+timing+collapse, inline diffs); todos panel; notifications sticky/ttl; voice listening/transcribing; browser progress; background-complete count | §3 agent-feature gaps |
| **5e — subagents/agents dashboard** | subagent tree (`subagent.*`) + agents dashboard (tree + Gantt + accordions) + delegation HUD (`SpawnHud`). **Hardest; last** | §2b agents dashboard; §3 subagents/delegation/activity feed |
| **8 — launcher + distribution** | repoint `_make_opentui_argv` to the Solid entry; engine gate + dashboard PTY consistency; Bun + per-arch native lib distribution; perf bench vs Ink | §9/§10 |

> Phase numbering keeps the goal's "Phase 5+" lumped surfaces split into 5a–5e for trackability; the
> launcher is "Phase 8" per the goal's EXECUTION PLAN item 8. The interactive surfaces (slash
> system, modals/overlays/popups, blocking prompts, floating chrome) are **first-class acceptance
> items** — explicitly assigned above so nothing silently drops.

### Acceptance (how the judge scores)
For each feature-map row: ✅ requires (a) the new file:line, (b) a test (Layer 1–4 as appropriate),
(c) a smoke-doc step that observes it live. `bun run check` green + the agentic smoke passing are the
two gates run every phase.

---

## 12. Resolved decisions (glitch, 2026-06-08)

1. **Branch — RESOLVED: keep `feat/opentui-native-engine`.** Build the new Solid package on this
   branch; the React `ui-tui-opentui/` coexists as reference until the Solid one supersedes it, then
   a contained nuke/replace at Phase 8 cutover. Docs/feature-map already live here.
2. **Package dir — RESOLVED: `ui-tui-opentui-v2/`** during the build (coexists with the old React
   pkg), renamed to the canonical home at Phase 8 cutover.
3. **Design language — RESOLVED: our Ink TUI is the design language *for now*** (look, layout, UX
   map FROM Ink), but we're free to **converge on opencode's good design taste where it's clearly
   better**. METHOD/structure still follows opencode wherever Ink lacks a clean native pattern
   (Solid reactivity, the boundary, scrollbox/parts/markdown idioms). So: Ink-led visuals, opencode-led
   architecture; not a pixel mandate either way — use judgment when opencode's UX is the stronger choice.
4. **Keymap — RESOLVED: adopt `@opentui/keymap`.** Use the keymap host (mirror opencode's
   `createDefaultOpenTuiKeymap` + a custom keymap layer) rather than hand-rolling `useKeyboard` per
   surface. `@opentui/keymap@0.3.2` is in the dep set.

---

## 13. Build status

- **Phase 0 — scaffold: ✅** (commit `a47c6df`). Effect runtime + `acquireRelease(createCliRenderer)`
  + the one `render()` bridge + `FakeGateway.layer` + headless frame gate; spec + `opentui-smoke.md`
  reviewed by glitch (see §12).
- **Phase 1 — transport + store + theming: ✅** (this commit). `GatewayService`/`liveGateway` over
  the real `tui_gateway` (JSON-RPC stdio, 16ms→`batch()` coalesce, typed errors, decode-once
  `GatewayEvent` Schema); the Solid `sync-v2`-style store (streaming concat + skin→theme + LRU dedup
  + hydrate-while-buffering); the 1:1 `theme.ts` port + `ThemeProvider` (no hardcoded styles). Live
  drive + headless gate logged in `opentui-smoke.md` (P1). The v4 parity matrix in
  `opentui-feature-map.md` tracks each ✅ row (test + smoke). **Two items pulled forward from later
  phases:** (a) a minimal **Ctrl+C graceful quit** (`boundary/renderer.ts`) so the live engine reaps
  its own gateway child — the full keymap host + `!blocked` gating still land with prompts (Phase 3);
  (b) an **initial-prompt bootstrap** (`HERMES_TUI_PROMPT` → `session.create`→`prompt.submit`) as the
  Phase-2-composer stand-in so a streamed reply can be driven live now.
- **Phase 2a — interactive shell: ✅** (this commit). The `<scrollbox>` transcript (§8 #2 gotchas),
  the real `<textarea>` composer (clear-on-submit + re-entrancy guard → `prompt.submit`, now the
  primary input), and a `header.tsx` skeleton. Live drive + gate logged in `opentui-smoke.md` (P2a);
  parity-matrix rows ✅/⚠️ in `opentui-feature-map.md`.
- **Phase 2b-i — ordered parts + inline tool render: ✅** (this commit). Assistant turns are an
  ordered `parts[]` (text/reasoning/tool) dispatched by `<Switch>` in `messageLine.tsx` so tools
  interleave inline (§7); `view/toolPart.tsx` does the inline/capped-block render; `logic/toolOutput.ts`
  strips the `{output,exit_code}` envelope + collapses. Live drive shows a `⚡ terminal` row between
  text blocks (smoke P2b); 23 tests green.
- **Phase 2b-ii — native markdown: ✅** (this commit). Text parts render via `<code
  filetype="markdown" streaming conceal>` (`CodeRenderable`, opencode's v2 path — `<markdown>` +
  `internalBlockMode="top-level"` deferred paint headlessly) + a theme-derived `SyntaxStyle.fromStyles`
  (`view/markdown.tsx`). Live: a markdown reply renders with `**` concealed (smoke P2b). **Phase 2 is
  complete** — smoke steps 1–4 run live.
- **Phase 3 — blocking prompts: ✅** (this commit) 🔴 deadlock-critical. The 4 gateway `*.request`
  events drive an overlay that REPLACES the composer (`store.state.prompt`), answered via the matching
  `*.respond` RPC; Esc/Ctrl+C → deny/empty; global Ctrl+C-quit gated on `!blocked` (`renderer.ts`).
  Native paradigm (glitch's steer): native `<select>` for approval/clarify choices + native `<input>`
  for clarify free-text + a masked `useKeyboard` buffer for sudo/secret (native `<input>` has no mask).
  Live-verified end to end: approval → approve/deny/Ctrl+C-cancel all UNBLOCK the agent, no deadlock
  (smoke P3). `confirm` is local (non-gateway) — lands with the slash commands that trigger it (P4).
- **Next — Phase 4 — session lifecycle + slash system:** create/resume (incl. resumed tool rows
  `{name,context}`) + the slash-command SYSTEM (`commands.catalog` RPC + the dispatch ladder +
  the 13 TUI-only client commands) + the local `confirm` dialog for `/new`,`/clear`.
