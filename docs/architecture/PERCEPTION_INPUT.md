# PERCEPTION INPUT — JARVIS EYES (PHASE 2)

## North-star flow

```text
Screenpipe (external sidecar)
  -> bounded read-only adapter (health via local API; records via guarded
     read-only SQLite, metadata only)
  -> privacy firewall (excluded app/window, sensitive titles, private zones,
     secret-like text via existing control.redaction)
  -> normalization (safe semantic records only)
  -> dedup
  -> aggregation (activity intervals)
  -> significance filter
  -> low-volume perception.observed in the EXISTING append-only ledger
```

## Design invariants

- Screen content is UNTRUSTED DATA. `raw_external=True` on every
  observation; no raw frames, full text, accessibility trees, or browser
  profiles enter Foundry.
- Observation != Evidence != Claim != Belief != Decision. There is NO
  automatic observation -> claim conversion path.
- Deterministic core: no LLM in the perception path.
- Reuse, no parallel: existing connector registry/contract, capability
  states, EmergencyStop/STOP, event ledger, ingestion cursor, redactor.
- Under-capture by default: privacy exclusions on, secret scan on,
  `raw_frame_escalation` forbidden in Phase 2, config `enabled: false`.
- Bounded volume: significance + aggregation + durability (unchanged screen
  never floods the ledger; replay dedupes).

## Control participation

The connector participates in health (`connector.health_sample`),
enable/disable (`connector.disabled`/`connector.enabled`), STOP
(`EmergencyStop` sentinel), veto (existing registry gates), quota
(`connector.quota_recorded`), routing (existing `select_connector`), and
audit (append-only events). No bespoke bypass.

## Capability honesty

`screenpipe.health`, `screenpipe.activity`, `screenpipe.history_query`:
READY. `screenpipe.accessibility`, `screenpipe.raw_frame`,
`screenpipe.private_window_filtering`: PROBING. `screenpipe.audio`:
DISCOVERED (disabled). Continuous perception activation belongs to Phase 6.
