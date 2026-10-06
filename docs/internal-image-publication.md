# Internal execution image publication

The fork-owned `GHCR Execution Read Release` workflow builds the selected native
Linux platform once, defaulting to `linux/amd64`; select `linux/arm64` for an
ARM64 target. It publishes the exact OCI digest and source-bound fork tags.
Dispatch the workflow on protected main with the exact source SHA, existing
fork tag ref, release tag, and platform under the task's existing publication
authorization. Publication does not grant runtime execution permissions.

PRs run execution read/action conformance with read-only permissions. Publication
checks the trusted workflow revision against current main, revalidates the
source ref, proves packaged contracts, and uses the repository-bound GHCR token.
The global lock serializes cooperating publication runs. Package writer controls
remain the owner's responsibility; arbitrary external clients are not fenced
by an Actions concurrency group.

The workflow retains the exact OCI archive before registry writes and records
the pending or published digest. ORAS transfers those bytes without rebuilding.
Authenticated unknown/error responses stop the operation; a lost push or tag
response is reconciled by exact manifest readback. Existing matching tags are
reused. Conflicting source/release tags are never overwritten. An unresolved
push holds with its exact digest and archive available for bounded recovery;
resume that artifact rather than rebuilding uncertain bytes. Do not retry
with a new platform behind an existing source tag.

One target image, its source/digest, actual validation and recoverable publication
are sufficient for internal use. A second platform, repeated build, typed
confirmation, separate approval receipts, maintenance launcher, signing,
SBOM/provenance or distribution certification is not an ordinary prerequisite.
Public repackaging adds only an explicitly named requirement when it is needed.
Deployment remains the owning runtime's backup, swap, health/user-path check
and compatible rollback. This workflow performs no deployment or activation.

The pinned ORAS copy command supports an OCI archive source; see the
[ORAS 1.3 command contract](https://oras.land/docs/commands/oras_cp/).
