# Lane 6 orientation

Date: 2026-09-16

## Source boundary

- Base: `4b2ae28da582b369982661c2a1d2b1792e2f48cb`
- Branch: `candidate/max-lane-6-v16-autonomy-expansion-20260915`
- Worktree: `C:\temp\JARVIS\max-lane-6-v16-autonomy-expansion-20260915`
- Schema: 40
- Classification: isolated source/build lane; the explicit owner goal is a
  build-ahead redirect and does not change the frozen roadmap execution pointer.
- Protected: live database/runtime/install, Scheduler, Supervisor, trust ledger,
  provider configuration, release pointers, `main`, and origin.

## Existing canonical seams

### Campaigns

`jarvis_foundry.executive.campaigns` is the only campaign aggregate. It already
provides stable campaign/child identity, child-to-MissionLoop binding, ALL/ANY
joins, bounded per-campaign parallel children, deadlines and replanning,
provider/time/dependency/resource waits through the existing Executive wake
authority, retry reservation, terminal child repair, checkpointing, startup
rebuild, effect reconciliation, STOP/resume, and objective truth mirroring.

### Self-expansion

`SelfExpansionEngine` is the existing bounded expansion port. It already uses
the canonical reuse-first discovery order, Adapter Factory or the sole
`FabricMcpBackend`, isolated trials, independent verification, Fabric Catalog
READY/PROBATION adoption, deterministic `stable_expansion_run_id`, failed
candidate rejection/fallback, restart reconciliation, rollback/disable, and
STOP/veto gates. `fabric.gap_bridge` owns durable gap, WAIT, attempt, wake,
replan, and exact-objective resume semantics.

### MCP

`jarvis_foundry.mcp_backend` is the sole MCP runtime. It supports stdio and
Streamable HTTP, tool enumeration, stable Fabric identities, schema-bounded
calls, DATA_ONLY remote content, readiness degradation, disable/removal, and
canonical effect/evidence/reconciliation for mutation. Existing disposable
proofs cover read-only calls, bounded filesystem mutation, timeout-after-effect
reconciliation, restart restoration, and no generic-worker fall-through.

### Capability authority

`fabric.catalog` remains the only capability/readiness authority. Trial and MCP
adoption may create READY/PROBATION state only in the disposable Lane 6 database.
No live registration is authorized.

## Product gaps selected for this lane

1. The end-to-end expansion path currently requires callers to manually compose
   `bridge_missing_plan_capabilities`, `start_expansion_attempt`, the expansion
   port, and ready-gap reconciliation. Add one callable product entrypoint that
   performs this composition and returns exact gap/attempt/resume lineage while
   leaving every authority with its current owner.
2. Campaign admission enforces parallelism and isolation only within each
   campaign. Multiple active campaigns can expose READY children with the same
   isolation/resource key simultaneously. Add durable cross-campaign resource
   contention using existing campaign child state plus `WAIT_FOR_RESOURCE`
   wakes, then release exactly once when the running owner leaves the resource.
3. Build one source-level continuity runner under `var/lane6-demo/` over these
   canonical APIs: two campaigns, contention/wake, real capability gap, local
   MCP probation trial, exact objective resume, child failure/repair, checkpoint,
   fresh-database rebuild, and truthful completion/WAIT output.

## Baseline proof

Using Python 3.13 and the prior V12 candidate's installed dev/web environment,
with `PYTHONPATH` bound to this worktree's `src`:

```text
pytest -q tests/foundry/test_v12_self_expansion_convergence.py
          tests/foundry/test_v12_campaign_runtime.py
          tests/foundry/test_v12_mcp_backend.py
exit 0
```

No source or live state was mutated by the baseline.

