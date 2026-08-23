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

## Focused verification

```text
python -m pytest tests/foundry/test_v7_resilience.py -q
7 passed

python -m pytest tests/foundry/test_v7_resilience.py \
  tests/foundry/test_capability_states.py \
  tests/foundry/test_effects.py \
  tests/foundry/test_runtime_diagnostics.py \
  tests/foundry/test_v7_scheduler.py \
  tests/foundry/test_supervisor_recovery.py -q
58 passed
```

The tests cover replay, idempotent scans, invalid transitions, STOP
precedence, capability isolation, effect freezing, dependency scope, and
synthetic resource/cost anomalies.

## Boundaries

- Automatic invocation from the Supervisor heartbeat is not yet integrated.
- Real providers, connectors, live release roots, live scheduler tasks, and
  production databases were not touched.
- `ruff` is NOT TESTED because the package is not installed in this lane.

