# Architecture Decision Records

## 2026-08-22: Additive private action release identity

Status: Accepted for a bounded source candidate. This decision does not claim
merge, publication, tagging, deployment, activation, migration, or runtime use.

Context:
The private durable-idempotent Runs profile is implemented and reuses
`hermes.execution.read.v1` for status and terminal receipts. The existing
fork-owned GHCR workflow, however, attests only the read contract. Packaging
the action implementation under that read-only identity would not give a
downstream coordinator a machine-verifiable action dependency.

Decision:

- Add the distinct identity `hermes.execution.action.v1` and a packaged Draft
  2020-12 schema for the authenticated keyed submission envelope, accepted
  response, durable read-status binding, terminal-receipt binding, and action
  release receipt.
- Keep `hermes.execution.read.v1` unchanged. Do not rename its schema, labels,
  receipt, API routes, capability values, or consumers.
- Bind action contract, schema SHA-256, and exact source SHA at both child-image
  and immutable-index levels while retaining the existing read annotations.
- Run schema validation and the real authenticated keyed Runs API in release
  conformance. Exact replay must return the same accepted identity and durable
  status must validate through the read contract.
- Attach a separate authoritative
  `hermes.execution.action.release-receipt.v1` OCI artifact to the immutable
  index. The action receipt binds the exact index, source, workflow, action
  schema, and unchanged read dependency; the existing read receipt remains a
  separate artifact.

Consequences:

- ProgramOS can qualify the private action profile by exact contract, schema,
  source, index, and receipt rather than inferring action support from packaged
  source code.
- Existing read consumers retain identical wire identity and release evidence.
- `action_dispatch=false` remains accurate: this identity covers the existing
  private full-key Runs edge, not public proposal dispatch, WebAuthn, or public
  decision mutation.
- Publication still requires the existing protected manual workflow and
  operator gates; this source change performs no registry or runtime effect.

## 2026-08-22: Private durable-idempotent run submission

Status: Accepted for a bounded source candidate. This decision does not claim
merge, release, deployment, activation, executor evidence, or an outcome.

Context:
The Release 1 execution read contract already gives authenticated clients a
durable execution identifier, lifecycle, and evidence-gated terminal receipt.
The full-authority `POST /v1/runs` route already creates those execution rows,
but every request creates a fresh run. A PM coordinator retrying an ambiguous
HTTP response can therefore dispatch the same approved intent twice. The
existing generic API idempotency cache is process-local, expires quickly, and
does not reject a changed request under the same key.

Decision:
- Keep `POST /v1/runs` as the private full-key submission edge. A request with
  `Idempotency-Key` receives durable profile-scoped replay semantics; an
  unkeyed request preserves the existing runs behavior.
- Hash the key before persistence and retain only that hash, a canonical
  request digest, generated run and execution identifiers, authority binding,
  and creation time. Do not persist the request body, prompt, session key, or
  raw idempotency key in the execution ledger.
- Atomically reserve the keyed submission, create its queued execution, and
  append `execution.created`. An exact retry returns the original `202` body
  without launching another agent. Reusing the key for a changed request is a
  closed `409` conflict.
- Include the JSON request body and `X-Hermes-Session-Key` value in the
  canonical request digest because either can change execution semantics.
- Migrate the profile-local store from schema 1 to schema 2 before serving;
  the migration adds only the private submission table. Preserve the released
  `hermes.execution.read.v1` objects, schema artifact, routes, and wire identity.
- Retain the v1 read surface and action table across an offline prior-binary
  rollback. The v2 operator hook must create and validate a consistent private
  backup before lowering `user_version`; a paired v2 restore hook is the hard
  stop if the prior binary fails its read-only reopen canary. Both hooks require
  one explicit existing profile-home or ledger path and an independently known
  expected profile identity; default, absent, ambiguous, or mismatched targets
  fail before mutation.
- Continue to require `API_SERVER_KEY` for submission. `API_SERVER_READ_KEY`
  remains read-only and receives `403` on `POST /v1/runs`.
- Reuse `GET /v1/execution-contract/executions/{execution_id}` for durable
  status and `GET /v1/execution-contract/receipts/{receipt_id}` for terminal
  evidence. Do not add a second status or receipt authority.

Consequences:
- A coordinator can safely retry the same accepted submission across process
  restarts without duplicate dispatch.
- Admission failures before durable reservation, including concurrency
  rejection, remain retryable with the same key.
- Exact durable replay is resolved before new-work concurrency admission and
  therefore neither consumes nor bypasses capacity for a new execution.
- A crash after reservation but before or during execution is recovered by the
  existing startup rule as terminal ambiguity; replay never silently restarts
  it or projects success.
- Generic run completion still cannot publish an authoritative effect receipt.
  A named executor must call the closed evidence hook with exact bindings and
  digests. Public proposal dispatch, public decision mutation, and WebAuthn
  step-up remain held, so `action_dispatch=false` stays accurate.

Detailed contract: [execution-action-contract-v1.md](execution-action-contract-v1.md)

## 2026-08-15: Durable, profile-scoped public execution read contract

Status: Accepted for the Release 1 source candidate. This status records the
architecture decision; it does not claim merge, package release, deployment,
or runtime enablement.

Context:
Hermes exposes useful process-local run, approval, session, Kanban, and UI
state, but none of those surfaces is a stable durable authority contract for
an external controller. Process dictionaries disappear on restart, chat and
session history do not prove an external effect, and operator projections may
lag or omit lifecycle transitions. A downstream reader needs versioned,
profile-scoped objects, detectable replay gaps, and terminal receipts that
cannot diverge from terminal state across a crash.

Decision:
- Add the provider-neutral `hermes.execution.read.v1` contract and closed
  packaged JSON Schema for executions, decisions, ordered events, receipts,
  capability negotiation, and errors.
- Store each profile's ledger at
  `{get_hermes_home()}/execution_contract.sqlite3`. Use explicit schema
  migration and startup recovery; use read-only SQLite connections for every
  public GET.
- Treat the ordered event table as a transactional outbox. Terminal state,
  evidence-backed receipt, and publication events commit in one transaction.
- Derive profile-instance, authority, and executor identity from an atomically
  created, owner-only, versioned random anchor inside the active scoped Hermes
  home, never from the home path, a URL label, or a literal default. Embed an
  anchor-derived key in public IDs so a cross-profile identifier fails with
  `403` instead of becoming an ambiguous `404`. Bind store metadata and every
  persisted public row to that same authority so a database copied without
  its complete profile home cannot be projected under a different profile.
- Require an explicit executor evidence hook before publishing an external
  effect receipt. Generic chat, session, Kanban, tool-progress, or process-run
  completion terminates an effect-bearing execution as ambiguous and
  unproven.
- Add an optional profile-scoped `API_SERVER_READ_KEY` with only
  `execution:read`. Keep run submission, decision resolution, steering,
  stopping, and all other API authority behind the full `API_SERVER_KEY`.
  Refuse startup if the read and full keys are equal in any served profile.
- Enforce the execution transition graph for decision creation and closure;
  cancellation and restart recovery close pending decisions transactionally.
  Bind effect evidence to the latest resolved decision so an older resolution
  cannot authorize work after a newer decision exists. Serialize decision and
  evidence creation under the same immediate transaction, and reject every new
  decision after evidence or a receipt exists.
- Pin an explicit snapshot high-water in the same read transaction as every
  collection query and require clients to carry it across pages. Prove the
  unfiltered global event interval is contiguous before declaring any event
  page complete, including filtered feeds.
- Allow late evidence only through a dedicated atomic reconciliation hook
  that can publish an authoritative ambiguous receipt without upgrading the
  execution to success. Include the effective recovery reference in immutable
  retry comparison.
- Deep-validate persisted identifiers, references, digests, bindings, states,
  revisions, and timestamp ordering before projecting any stored object.
- Retain ordered events for 30 days by default. Prune only a contiguous global
  prefix and return `410` with an explicit minimum cursor when replay history
  is gone.
- Normalize authenticated router-level `404` and `405` failures in the default
  and multiplex contract namespaces into the same closed error envelope.

Consequences:
- External controllers can read durable state across restart without using
  Hermes internals or mutation credentials.
- Existing `/v1/runs` remains a convenience projection and may carry a durable
  `execution_id`, but it is not the authority store.
- The API server refuses startup when the ledger cannot migrate or recover;
  a failed durable write degrades the profile's read contract instead of
  serving a healthy projection.
- Release 2 proposal/action dispatch, WebAuthn step-up, and public decision
  mutation remain separately gated. This decision does not authorize them.

Detailed contract: [execution-read-contract-v1.md](execution-read-contract-v1.md)

## 2026-07-13: Scope plugin manager state by Hermes home/profile (keyed cache)

Status: Accepted

Context:
Hermes supports multiple profiles via different Hermes home directories.
Homes are switched two ways in a running process: the `HERMES_HOME`
environment variable (single-profile CLI/gateway processes), and the
context-local `set_hermes_home_override()` (`hermes_constants.py`), which
the multiplexed gateway worker (`gateway/run.py`'s `_profile_scope`) and
subagent/embedded callers use to serve several profiles from one
long-lived process. The override is a `ContextVar` and deliberately does
**not** mutate `os.environ`, since that would leak one profile's home
into every other concurrent task in the same process.

The plugin manager was a process-global single-slot singleton
(`_plugin_manager`). User-installed plugins are discovered from
`get_hermes_home() / "plugins"`, and context-engine plugins (e.g.
`hermes-lcm`) capture profile-scoped state — such as the LCM database
path — at registration time. A single-slot cache meant:

1. Switching homes via `set_hermes_home_override()` was invisible to a
   naive "did `HERMES_HOME` change" check, so the singleton silently kept
   serving the first profile's manager to every other profile in the
   process.
2. Even when a fresh `PluginManager` *was* created for a new home, plugin
   modules are imported into `sys.modules` as `hermes_plugins.<slug>` by
   `_load_directory_module`, and only that top-level module was ever
   replaced. A same-slug plugin's *relative* imports
   (`from . import state`) are cached separately under
   `hermes_plugins.<slug>.<submodule>`, and Python's import machinery
   resolves those from `sys.modules` first — so a profile switch could
   silently keep serving a previous profile's already-imported submodule
   code/state instead of re-executing the new profile's plugin.

Decision:
- Replace the single-slot singleton with a cache keyed on the *resolved*
  Hermes home path (`_plugin_managers_by_home: Dict[Path, PluginManager]`).
  `get_plugin_manager()` resolves the current home via `get_hermes_home()`
  (which itself already consults `get_hermes_home_override()` before
  `os.environ`), so both the env-var and context-local override paths are
  covered uniformly.
- `_plugin_manager` (the old single-slot name) is kept as a thin "last
  manager returned" pointer purely for backward compatibility with
  existing test code that does
  `monkeypatch.setattr(plugins_mod, "_plugin_manager", some_manager)`.
  When that name is monkeypatched to a manager the keyed cache doesn't
  know about, `get_plugin_manager()` treats it as an explicit injection
  and adopts it into the cache under the *current* resolved home, rather
  than discarding it.
- Both `PluginManager._load_directory_module` (initial/`force=True`
  reload within the same home) and the shared `_clear_plugin_submodules`
  helper (profile switch / test teardown) evict `sys.modules[module_name]`
  **and every name prefixed with `module_name + "."`** before a plugin
  slug is (re-)imported, so relative-import submodules can never survive
  a reload or a home switch.
- Test isolation (`tests/conftest.py`'s `_hermetic_environment` fixture)
  calls a new `_reset_plugin_managers_for_tests()` helper that drops the
  entire keyed cache and purges every plugin submodule from `sys.modules`
  between tests, instead of only resetting the single-slot pointer.

Consequences:
- Per-profile LCM instances (and any other context-engine plugin) use
  their own `{home}/lcm.db` regardless of whether the profile switch went
  through `HERMES_HOME` or `set_hermes_home_override()`.
- Plugin discovery remains cached within a profile for normal
  performance, and re-entering a previously-seen profile reuses its
  cached manager instead of rebuilding from scratch.
- Sequential *and* interleaved profile switching — in tests, the gateway
  multiplexer worker, or embedded callers using the context-local
  override — no longer leaks context-engine state, plugin module state,
  or stale relative-import submodules across profiles.
- Regression coverage exercises the real production path
  (`set_hermes_home_override()`) rather than only the env-var path, and
  includes a dedicated relative-import leak test.
