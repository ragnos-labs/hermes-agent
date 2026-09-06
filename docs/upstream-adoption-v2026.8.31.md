# Shared-fork reconciliation: v2026.8.31

Recorded 2026-09-06. This is source delivery. No runtime release, tag, image,
installation, configuration change or service activation is part of this update.

The reconciliation joins private fork source
`e6d81aaf6999a255fd96049f67945f3fa08640bb` with upstream stable source
`29112bef099274229cadff79cdff7bf7b99c4b77` (the peeled `v2026.8.31` tag).
It includes 911 upstream commits absent from that fork base.

## Compatibility decisions

- Retain upstream's extracted Runs implementation, request profile scope,
  principal ownership, hosted-room grants and session normalization.
- Preserve the private execution contract's permanent hashed-key admission and
  identical start replay. Room-grant Runs keep upstream's scoped admission.
  A private keyed request does not also reserve a second raw-key Runs ledger.
- Preserve durable decisions, effect evidence, terminal receipts, recovery and
  maintenance hooks. A stop request does not erase a real late completion;
  effect-bearing success without evidence remains ambiguous. Approval events
  retain the exact request identity used by the HTTP response.
- Retain the fork's Photon attachment acknowledgement and upstream read receipt.
  Use upstream's interpreter-aware Linux desktop launcher repair.
- Preserve contributor identities whose email filenames differ only by case
  in a nested mapping directory. Attribution consumers read both locations.

Execution action/read schema bytes and their existing release identifiers are
unchanged. This source update makes no claim about an existing image's contents
and does not move an installed fleet's runtime pin.

## Validation and adoption limits

Local validation covers the isolated Python suite, repository JavaScript
workspace checks, Photon tests, documentation build, blocking Python lint,
Windows static checks and contributor attribution. Final counts, repaired
failures and independent exact-head review are recorded in the delivery PR.
The Mac validation environment uses a current isolated uv version for the new
upstream lock format. It is not a Linux fleet runtime qualification.

Dependency audit findings are inherited: the root npm audit reports six
findings (two moderate, four high), and the website install reports five
(three moderate, two high). Root dependency versions are unchanged from the
fork base; the website lockfile is unchanged. Neither audit is claimed green.
Artifact release and deployment require their own dependency assessment,
provenance, target-platform checks and workflow qualification.

Keep product profiles, operational state and provider custody in their owning
fleet and deployment. A fleet may propose an independent source-pin adoption
after this shared change lands. Installation, activation, migration compatibility
and rollback evidence remain separate from that source proposal.
