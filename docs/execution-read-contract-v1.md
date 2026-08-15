# Hermes execution read contract v1

Status: Release 1 source contract. This document does not claim that the
contract is merged, packaged, deployed, or enabled in any running Hermes
installation.

Contract identity: `hermes.execution.read.v1`

HTTP API namespace: `/v1/execution-contract`

Store schema: SQLite `PRAGMA user_version=1`

## Purpose and authority

This contract is Hermes's provider-neutral public read surface for execution
lifecycle, pending decisions, ordered events, and authoritative terminal
effect receipts. It is profile scoped. The server returns the profile,
execution authority, and executor identities in every response; clients do
not infer authority from a URL, model name, provider, process, or config file.

The following are projections, not authoritative effect evidence:

- `/v1/runs` process-local state;
- chat output or session history;
- Kanban rows or summaries;
- tool progress events without an executor evidence record.

An execution that references an external effect can publish a receipt only
after an executor calls the closed internal evidence hook with the exact
execution, effect, decision when applicable, outcome, and SHA-256 subject,
evidence, and result digests. Generic completion without that evidence is
stored as `terminal_ambiguous` with `receipt_state=unproven`.

## Discovery and version negotiation

| Method | Route | Result |
| --- | --- | --- |
| `GET` | `/v1/execution-contract/capabilities` | Version, asserted authority, scopes, retention, limits, and features |
| `GET` | `/v1/execution-contract/schema` | Packaged closed JSON Schema |

Clients may send `Hermes-Execution-Contract-Version` or the
`contract_version` query parameter. Omitting both selects the only supported
version. Supplying two different values is `400`; an unsupported version is
`409`. Successful responses include
`Hermes-Execution-Contract-Version: hermes.execution.read.v1` and
`Cache-Control: no-store`.

## Read routes

All routes are also available under the existing multiplex prefix
`/p/<profile>/...`. The prefix enters that profile's home and credential
scope before the store or key is resolved.

| Method | Route | Filters |
| --- | --- | --- |
| `GET` | `/v1/execution-contract/executions` | `after`, `limit`, `lifecycle` |
| `GET` | `/v1/execution-contract/executions/{execution_id}` | none |
| `GET` | `/v1/execution-contract/decisions` | `after`, `limit`, `state` |
| `GET` | `/v1/execution-contract/decisions/{decision_id}` | none |
| `GET` | `/v1/execution-contract/events` | `after`, `limit`, `execution_id` |
| `GET` | `/v1/execution-contract/receipts` | `after`, `limit`, `outcome` |
| `GET` | `/v1/execution-contract/receipts/{receipt_id}` | none |

`limit` is 1 through 200. Collection order is immutable creation order. Event
order is the store-assigned monotonic `sequence`, not client or wall-clock
order.

Every collection returns:

- `cursor`: the last sequence/ordinal covered by the response;
- `high_water`: the newest durable sequence/ordinal at read time;
- `minimum_available`: the earliest retained position;
- `has_more`: whether another page exists within this snapshot;
- `completeness=complete`: returned only when the ledger is trustworthy.

The event page also returns `pruned_through` and
`retention_seconds=2592000`. Events form a transactional outbox and are kept
for 30 days by default. Pruning advances only across a contiguous global
prefix, so no retained event can be hidden behind the watermark. A cursor
older than retained history returns `410` with the new minimum and high-water;
a cursor ahead of high-water returns `409`.

## Execution lifecycle

Initial states are `accepted`, `queued`, or `running`. The closed lifecycle
set is:

`accepted`, `queued`, `running`, `awaiting_decision`,
`cancellation_requested`, `terminal_succeeded`, `terminal_failed`,
`terminal_cancelled`, `terminal_partial`, and `terminal_ambiguous`.

`execution_id` and its work, proposal, and effect references are immutable.
An `effect_id` requires both `work_ref` and `proposal_ref`. `revision` is the
optimistic concurrency token. `created_at`, `started_at`, `updated_at`, and
`terminal_at` are RFC 3339 UTC timestamps. Nonterminal data from a prior
runtime is reported `stale` until startup recovery closes it as
`terminal_ambiguous`; it is never projected as healthy or successful.

## Decisions

A decision has an immutable `decision_id` and exact execution, effect, and
proposal binding. It includes allowed choices, request digest, optional
candidate and policy digests, expiry, state, resolution evidence, timestamps,
and revision. States are `pending`, `resolved`, `expired`, and `superseded`.

One execution may have only one pending decision at a time. Resolution is
idempotent only for the exact same choice and evidence. Expiry terminates the
waiting execution as ambiguous. Any other terminal transition supersedes its
pending decision transactionally.

## Terminal receipts

Receipt outcomes are `succeeded`, `failed`, `cancelled`, `partial`, and
`ambiguous`. A receipt contains immutable receipt, execution, and effect IDs;
asserted profile, authority, and executor IDs; subject, evidence, and result
digests; terminal time; exact decision binding when applicable; optional
recovery/reconciliation references; and immutable `revision=1`.

Receipt insertion, terminal execution state, and their ordered events commit
in one `BEGIN IMMEDIATE` transaction. Duplicate identical evidence is
idempotent. A changed digest, effect, decision, profile, or outcome conflicts
and fails closed.

## Authentication and errors

`API_SERVER_KEY` retains full API authority. A distinct optional
`API_SERVER_READ_KEY` grants only `execution:read` for `/v1/capabilities` and
`GET /v1/execution-contract/*`. The read key receives `403` for run submission,
decision resolution, steering, stopping, chat/responses, unrelated reads, and
all other mutation paths. Both keys are profile scoped and must pass the same
minimum-strength guard. Reads never create or migrate the database.

Contract errors use a closed JSON shape and these statuses:

| Status | Meaning |
| --- | --- |
| `400` | malformed input or unknown closed value |
| `401` | missing or invalid bearer token |
| `403` | scope denial or cross-profile identifier |
| `404` | scoped object does not exist |
| `409` | version, revision, binding, lifecycle, or cursor conflict |
| `410` | cursor points into pruned history |
| `429` | ledger is busy; response includes `Retry-After` |
| `500` | persisted contract data is corrupt or an internal invariant failed |
| `503` | ledger is unavailable or the profile is degraded after a failed write |

Server error details are not returned to clients.

## Persistence and migration

Each profile owns `{get_hermes_home()}/execution_contract.sqlite3`. Schema
creation is an explicit, atomic migration performed before the HTTP listener
starts. Store metadata and every public row are bound to the exact asserted
profile, authority, and executor where applicable; a misplaced ledger fails
closed. Public reads open the existing file with SQLite `mode=ro` and
`query_only=ON`; a missing ledger returns an empty complete collection without
creating a file. Startup rejects an unknown store or contract version, recovers
orphaned nonterminal executions, expires decisions, and applies event
retention before accepting reads.

## Release 2 hold

Release 1 does not add proposal submission, general action dispatch,
WebAuthn/step-up authorization, public decision mutation, executor delegation,
or recovery mutation routes. The capability document advertises
`webauthn_decisions=false` and `action_dispatch=false`. Those features require
a separate accepted decision and action-contract release.
