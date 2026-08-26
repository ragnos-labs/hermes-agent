# Hermes private keyed run submission contract v1

Status: bounded source candidate. This document does not claim merge, release,
deployment, activation, executor evidence, terminal success, or an outcome.

Contract identity: `hermes.execution.action.v1`

Canonical schema:
`hermes_cli/execution_contract_schemas/hermes.execution.action.v1.schema.json`

Schema SHA-256:
`d7ff28de7a04b015005e8ef39df78a06766db6426ae91792c3d5f39a61966870`

This private contract adds durable retry safety to the existing authenticated
Runs API. It deliberately reuses the released execution read contract for
status and receipts rather than creating a second authority surface.

## Authority and route

Submission uses `POST /v1/runs` and the profile-scoped `API_SERVER_KEY`.
`API_SERVER_READ_KEY` is never sufficient and receives `403`. Multiplexed
requests use `/p/{profile}/v1/runs` and that routed profile's full key, home,
ledger, and authority.

The request supplies the normal Runs API body plus:

```http
Idempotency-Key: one-stable-key-for-one-exact-intent
```

The trimmed key must be nonempty, no longer than 256 characters, and contain
no control characters. It is retry identity, not execution evidence or an
authorization credential.

For an external effect, `execution_context` remains closed:

```json
{
  "input": "Execute the already-approved bounded work.",
  "execution_context": {
    "work_ref": "work:synthetic",
    "proposal_ref": "proposal:synthetic",
    "effect_id": "effect:synthetic"
  }
}
```

`effect_id` requires both exact references. Provider, organization, and
persona fields do not belong in `execution_context`.

## Durable reservation

Hermes computes a canonical SHA-256 digest over the complete parsed JSON body
and the effective `X-Hermes-Session-Key`. It hashes the idempotency key under a
contract-specific domain separator. In one SQLite write transaction it:

1. binds the key hash to the request digest and active profile authority;
2. allocates one `run_id` and one execution-read `execution_id`;
3. creates the queued execution with the exact work, proposal, and effect
   references; and
4. appends the ordered `execution.created` event.

The ledger stores neither the raw key nor the request body, prompt, session
key, credentials, provider payloads, or private runtime evidence.

## Replay and conflict

The first accepted request returns `202`:

```json
{
  "run_id": "run_opaque",
  "execution_id": "exe_profile_opaque",
  "status": "started"
}
```

An exact retry under the same routed profile returns the identical status and
identifiers and does not create or launch another run. A changed body or
session key under the same idempotency key returns the execution contract's
closed `409 execution_contract_conflict` envelope.

Admission can reject a request before reservation, for example when the
concurrency limit is already reached. Such a response creates no submission;
the caller retries later with the same key. Hermes resolves an existing exact
durable replay before applying that new-work limit, so replay remains
available while capacity is saturated without launching another run.

If Hermes restarts after reservation, replay returns the original durable
identity. Existing startup recovery closes abandoned nonterminal work as
`terminal_ambiguous`; replay never re-executes it implicitly.

## Status and terminal receipt

Durable status is the existing read route:

```text
GET /v1/execution-contract/executions/{execution_id}
```

When the execution reports `receipt_state=published`, the immutable
`receipt_id` is read from:

```text
GET /v1/execution-contract/receipts/{receipt_id}
```

The process-local `GET /v1/runs/{run_id}` remains a convenience projection and
may expire. It is not durable execution authority.

## Evidence hard stop

A generic agent response, completed process-local run, session transcript,
Kanban row, or tool-progress event cannot mint an authoritative effect
receipt. An effect-bearing run publishes one only when a named executor calls
the closed evidence hook with the exact execution, effect, outcome,
subject/evidence/result digests, and latest decision binding when applicable.

Without that evidence, terminalization remains `terminal_ambiguous` with
`receipt_state=unproven`. This contract does not enable public action dispatch,
public decision mutation, WebAuthn step-up, or executor delegation.

## Additive release identity

The fork-owned GHCR workflow packages this private action profile beside the
existing `hermes.execution.read.v1` contract. It does not replace or rename the
read identity. Fork releases use `vYYYY.M.D[.N]-ragnos.N`; the prefix names the
exact NousResearch upstream release baseline and the required suffix prevents
the RAGnos artifact tag from colliding with that immutable upstream tag. A
conforming action release binds all of the following to the same immutable
multi-architecture index:

- `hermes.execution.action.v1` and the exact action-schema SHA-256 above;
- the exact source commit in `org.opencontainers.image.revision` and
  `io.ragnos.hermes.execution.action-source-sha`;
- action contract, schema, and source labels on each child image and matching
  annotations on the immutable index;
- conformance that validates the action schema and exercises authenticated,
  keyed `POST /v1/runs`, exact replay, and the durable execution-read status
  binding; and
- a distinct
  `hermes.execution.action.release-receipt.v1` OCI artifact attached to the
  immutable index and validated against the packaged action schema.

The action receipt also binds the unchanged read-contract identity and schema
digest because durable status and terminal receipts remain owned by
`hermes.execution.read.v1`. The existing read labels, schema, release receipt,
and consumers remain intact. The source candidate does not claim that an image,
receipt, tag, release, deployment, or runtime activation exists.

## Store migration

The profile-local `execution_contract.sqlite3` store uses
`PRAGMA user_version=2`. Migration from version 1 adds the private
`action_submissions` table atomically without changing any
`hermes.execution.read.v1` public object or schema artifact. Unknown versions
continue to fail closed.

### Prior-binary rollback

Rollback is an offline operator action. First obtain the expected `profile_id`
from an authenticated read canary or the private release record. Stop the
gateway and every process that can write that profile's execution ledger.
Set one explicit existing profile target and a private backup destination in
an existing operator-controlled directory:

```bash
export HERMES_EXECUTION_PROFILE_HOME=/exact/existing/profile
export HERMES_EXPECTED_PROFILE_ID=hermes-profile-instance:expected
export HERMES_EXECUTION_ROLLBACK_BACKUP=/operator-private/execution-contract-v2.sqlite3
python -c 'import json, os; from pathlib import Path; from hermes_cli.execution_contract import ExecutionContractStore; store=ExecutionContractStore(profile_home=Path(os.environ["HERMES_EXECUTION_PROFILE_HOME"])); print(json.dumps(store.describe_v1_binary_rollback_target(expected_profile_id=os.environ["HERMES_EXPECTED_PROFILE_ID"]), indent=2))'
```

Record that JSON in the private change record and verify its resolved
`profile_home`, `database_path`, `profile_id`, and `schema_version=2`. The
inspection is read-only and fails if the target ledger is absent or its path,
anchor, stored authority, or expected profile identity disagrees. An explicit
`database_path=Path(...)` may be used instead of `profile_home`; never pass
both and never rely on the default Hermes home.

After that target check, run the v2 code once to make a consistent SQLite
backup and expose the unchanged v1 read surface:

```bash
python -c 'import os; from pathlib import Path; from hermes_cli.execution_contract import ExecutionContractStore; store=ExecutionContractStore(profile_home=Path(os.environ["HERMES_EXECUTION_PROFILE_HOME"])); store.prepare_v1_binary_rollback(backup_path=Path(os.environ["HERMES_EXECUTION_ROLLBACK_BACKUP"]), expected_profile_id=os.environ["HERMES_EXPECTED_PROFILE_ID"])'
```

The mutation refuses to overwrite a backup, repeats the exact target and
identity validation, validates the complete canonical v2 schema, holds the
ledger's SQLite writer reservation while snapshotting, and only then changes
`user_version` to `1`. It retains the validated `action_submissions` table.
Start the prior v1 binary and perform a read-only execution-contract canary
before accepting new work.

If the prior binary cannot reopen the store, stop it, return to the v2 code,
and restore the validated snapshot:

```bash
python -c 'import os; from pathlib import Path; from hermes_cli.execution_contract import ExecutionContractStore; store=ExecutionContractStore(profile_home=Path(os.environ["HERMES_EXECUTION_PROFILE_HOME"])); store.restore_v2_rollback_backup(backup_path=Path(os.environ["HERMES_EXECUTION_ROLLBACK_BACKUP"]), expected_profile_id=os.environ["HERMES_EXPECTED_PROFILE_ID"])'
```

Then reopen v2 and repeat the read canary. If the prior binary works, a later
v2 reopen validates the retained action table and advances the store to v2 in
place, preserving durable replay identity. Never copy only the live main
SQLite file, never run either command with ledger writers active, and never
accept new work before the selected binary passes its read canary. Keep the
backup private; it contains execution-ledger data.
