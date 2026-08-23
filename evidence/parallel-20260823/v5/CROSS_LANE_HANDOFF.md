# V5 Cross-Lane Handoff

## Canonical-main observation

At V5 lane orientation, the canonical main worktree was clean at frozen base
`01db32cbcaa409dfe26386ce66110429c2c0b0e0`. During this lane, a separate
full-reconciliation lane dirtied these four main-worktree paths:

- `evidence/integration/full-reconciliation/CANDIDATE_INVENTORY.json`
- `evidence/integration/full-reconciliation/CANDIDATE_INVENTORY.md`
- `evidence/integration/full-reconciliation/INTEGRATION_QUEUE.md`
- `scripts/full_reconciliation_inventory.py`

The canonical `main` ref and `origin/main` ref still resolve to
`b0aa96d07c65a6850e50873ffa99234bb669590d`, and no V5 command edited,
staged, reset, cleaned, or committed those paths. The V5 lane leaves the
worktree dirty so the owning lane can reconcile it. This is an external
working-tree condition, distinct from a V5 branch or main-ref mutation.

## V5 interfaces exposed

The candidate exposes reusable interfaces without coupling to V4/V6 branches:

- `ConnectorSpec` metadata for target kinds, idempotency, reconciliation,
  cancellation, cost, evidence, and stop policy.
- `connectors.resources` deterministic record-only discovery.
- `connectors.filesystem`, `git`, `http`, `browser`, and fake
  `communication`/`calendar` adapters.
- `connectors.lineage` and `connectors.evidence` append-only safe records.
- `connectors.composed.ComposedMissionHarness` disposable sequencing only.

No cross-lane promotion or production merge was performed.
