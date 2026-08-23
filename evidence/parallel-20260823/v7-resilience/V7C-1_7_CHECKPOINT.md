# V7C-1 through V7C-7 Source Checkpoint

Date: 2026-08-23
Verdict: PASS for the isolated source slice

## Delivered

- Event-derived incident lifecycle with explicit transition validation,
  immutable evidence, cause classification, effect ambiguity, action history,
  bounded repair attempts, and fresh-process reconstruction.
- Deterministic anomaly rules for heartbeat staleness, duplicate authority and
  lease churn, worker/provider/connector failures, unknown effects, database
  faults, canary degradation, repair loops, resource growth, cost spikes, and
  stale Command Center metrics.
- Capability-scoped containment through the existing capability state writer.
  Previous `READY` or `ACTIVE` state is restored only after observed health and
  owner STOP/veto checks. Unrelated capabilities remain usable.
- Small dependency graph and read-only diagnostic playbook above existing
  Supervisor, scheduler, release, reconciliation, and effect primitives.
- Bounded callback repair with explicit escalation and no blind retry of an
  ambiguous effect.
- Supervisor heartbeat integration is now an optional seam: the heartbeat
  records first, then invokes the coordinator; STOP returns before the hook;
  coordinator failures are bounded and recorded without crashing the loop.
- Independent review corrections: STOP is checked before every repair callback;
  failed diagnostics block mutation; capability recovery requires incident
  ownership and restores the exact prior state; effect mismatch requires
  evidence and remains non-repairable; capability health exposes per-capability
  status; and unresolved effects are derived from the complete action-event
  subset rather than a bounded recent sample.

## Focused verification

```text
python -m pytest tests/foundry/test_v7_resilience.py -q
14 passed

python -m pytest tests/foundry/test_v7_resilience.py \
  tests/foundry/test_capability_states.py \
  tests/foundry/test_effects.py \
  tests/foundry/test_runtime_diagnostics.py \
  tests/foundry/test_v7_scheduler.py \
  tests/foundry/test_supervisor_recovery.py -q
65 passed
```

The tests cover replay, idempotent scans, invalid transitions, STOP
precedence, capability isolation and ownership, diagnostic gating, effect
freezing and mismatch proof, dependency scope, capability health truth, and
synthetic resource/cost anomalies.

## Boundaries

- The heartbeat seam is source-integrated and disposable-tested; no live
  Supervisor was started or changed in this lane.
- Real providers, connectors, live release roots, live scheduler tasks, and
  production databases were not touched.
- `ruff` is NOT TESTED because the package is not installed in this lane.
