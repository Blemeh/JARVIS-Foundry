# Unattended Scenario Contract

The harness consumes JSON or YAML that parses into one immutable
`ScenarioSpec`. The schema is declarative. It describes what to observe and
which faults to schedule. It does not grant the harness authority to mutate
canonical JARVIS state.

## Required shape

```json
{
  "scenario_id": "FULL_12H",
  "duration_seconds": 43200,
  "clock_mode": "REAL_TIME",
  "seed": 12024,
  "initial_state": {
    "stop_available": true,
    "portfolio_enabled": true,
    "provider": "fake-local"
  },
  "required_capabilities": ["supervisor", "portfolio", "effects", "database"],
  "scheduled_faults": [
    {"fault_id": "worker-crash", "at_seconds": 1800, "kind": "PROCESS_CRASH", "target": "owned-worker"},
    {"fault_id": "provider-outage", "at_seconds": 3600, "kind": "PROVIDER_OUTAGE", "target": "fake-local"}
  ],
  "expected_invariants": ["STOP_AVAILABLE", "SINGLE_SUPERVISOR", "NO_DUPLICATE_EFFECT"],
  "resource_envelope": {
    "max_event_count": 100000,
    "max_db_bytes": 67108864,
    "max_retries": 10000,
    "max_provider_cost": 5.0
  },
  "owner_spam_threshold": 8,
  "busywork_threshold": 2,
  "restart_checkpoints": ["PRE_RESTART", "POST_RESTART"],
  "required_end_state": "CONTINUABLE",
  "rollback_requirement": "HEALTHY_AFTER_ROLLBACK",
  "evidence_output": "evidence/runs/FULL_12H"
}
```

## Clock modes

`REAL_TIME` uses wall-clock elapsed time. Wall-time invariants such as process
liveness and scheduled process faults are marked as wall-time evidence.
`ACCELERATED_DISPOSABLE` advances a deterministic logical clock and may prove
ordering, replay, thresholds, and state-machine behavior. It must be labeled
`ACCELERATED_EQUIVALENT_ONLY` and can never be reported as real endurance.

## Fault schedule

Faults are ordered by `(at_seconds, fault_id)` after validation. The seed is
recorded even when a schedule is explicit. A fault fires at most once per
`fault_id` and is recorded with target, phase, logical time, wall time, and
whether it was injected or organic. Resume loads the fired set before the next
dispatch, so completed faults are not replayed.

## Threshold semantics

Thresholds are explicit and scenario-local. Missing optional provider metrics
are `NOT_TESTED` unless the scenario requires them. `MANUAL_CONTINUATION_REQUIRED`
must equal zero for an unattended PASS. A timeout, interruption, or absent
required artifact produces `INCOMPLETE`, `ABORTED`, or `BLOCKED_EXTERNAL`, never
PASS.
