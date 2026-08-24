# V1 Acceptance Harness Contract

This directory will eventually contain executable acceptance tests for
`docs/architecture/04_V1_ACCEPTANCE_GATE.md`.

## Required Suites

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

## Evidence Rule

Every test must produce structured evidence containing:

- test identifier and version;
- setup and initial state;
- fault applied;
- expected behavior;
- actual observations;
- relevant events and artifact hashes;
- cleanup or restore result;
- PASS, FAIL, BLOCKED, or NOT_TESTED;
- verifier identity and independence limitation.

The reusable unattended acceptance runner lives in
`scripts/unattended_acceptance.py`; focused contract and negative-control tests
are in `test_unattended_harness.py`. Fresh disposable proofs are recorded under
`evidence/parallel-20260824/unattended-harness/runs/`.
