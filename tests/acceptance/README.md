# JARVIS V1 acceptance harness

The acceptance contract is executable. The primary runner is
`scripts/unattended_acceptance.py`; the source-level acceptance tests live in
this directory and the Foundry/convergence suites exercise the same durable
contracts.

The candidate-neutral V11 A3/A4 proving runner is covered by
`test_v11_a3a4_proving.py`. Its operator contract and guarded 48-hour commands
are documented in `docs/handoffs/V11_A3A4_PROVING_HARNESS_PREP_20260902.md`.

## Executed profiles

| Profile | Result | Wall-clock truth |
| --- | --- | --- |
| `ACCELERATED_12H_EQUIVALENT` | PASS | `ACCELERATED_EQUIVALENT_ONLY`; 714 cycles, 21.189 s wall time, 43,200 s logical time |
| `QUICK_30M` | PASS | `REAL_TIME_DISPOSABLE`; 28 cycles, 1,811.370 s wall time |
| `FULL_12H` | `NOT_RUN` | deterministic automation is available; no long-duration PASS claimed |
| `FULL_24H` | `NOT_RUN` | deterministic automation is available; no long-duration PASS claimed |
| `REBOOT_ASSISTED_24H` | `NOT_RUN` | requires an owner-scheduled reboot window |

Both executed profiles cover interruption/resume, crash/process replacement,
provider/network faults, resource pressure, release degradation, restart,
owner controls, effect idempotency, and structured evidence. The independent
false-PASS factory reports 8/8 adversarial cases rejected as expected.

## Required suites

```text
functional/
  G1 durable supervisor and restart
  G2 event-first wake and heartbeat
  G3 bounded role dispatch
  G4 idempotent mission execution
  G5 independent verification and repair
  G6 deterministic STOP
  G7 standing mandate, owner veto, and resource readiness
  G8 governed knowledge write-back
  G9 continued useful action

faults/
  F1 supervisor process death
  F2 Hermes gateway termination
  F3 active worker termination
  F4 provider outage
  F5 network loss
  F6 expired authentication
  F7 disk almost full
  F8 database lock/contention
  F9 incomplete or corrupt event write
  F10 malicious retrieved prompt injection
  F11 concurrent Obsidian edit
  F12 secret exfiltration attempt
  F13 ambiguous target and path escape
  F14 deliberately broken release
  F15 false-success builder report
```

## Evidence rule

Every run produces structured evidence containing the test identifier and
version, setup and initial state, fault applied, expected behavior, actual
observations, relevant events and artifact hashes, cleanup/restore result,
verdict (`PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED`), verifier identity, and
the session/provider independence limitation.

The retained disposable evidence roots are:

```text
C:\temp\JARVIS-closure-evidence-37764ac\accelerated
C:\temp\JARVIS-closure-evidence-37764ac\false-pass
C:\temp\JARVIS-closure-evidence-37764ac\quick-30m
```

These profiles did not modify canonical `main`, the live database, the live
Scheduler task, or the production Hermes home. Longer profiles remain
`NOT_RUN` until their real wall-clock evidence is complete.
