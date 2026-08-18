# PERCEPTION STOP GATE — JARVIS EYES PHASE 2

## STOP integration

`run_cycle` checks the EXISTING `EmergencyStop` sentinel
(`<home>/EMERGENCY_STOP`) before any registration/read/append. When
active, the cycle records no `perception.observed` events and returns
`{"stopped": true}`.

## Resume

After the sentinel is cleared, the next cycle re-orients from the durable
cursor (health sample -> read from cursor), so resume reconciles without
duplicate storms.

## Evidence

- P2-F17 STOP blocks ingestion PASS (sentinel active -> zero observations)
- P2-F18 resume reconciles PASS (sentinel cleared -> cycle appends and
  advances cursor)
- Unit tests: STOP blocks; connector disable blocks; re-enable resumes;
  degraded source only affects Eyes.
