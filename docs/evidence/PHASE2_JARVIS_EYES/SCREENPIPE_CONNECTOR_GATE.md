# SCREENPIPE CONNECTOR GATE — JARVIS EYES PHASE 2

## Registration (existing connector contract/registry)

`register_perception_connector()` registers via the existing
`connectors.registry.register_connector` append-only stream
(`connector.registered`), with `ConnectorSpec` validation from
`connectors.contract`.

```text
connector_id: screenpipe-local
kind:         perception
version:      0.4.40
actions:      perception.health=read
              perception.activity=read
              perception.elements=read
              perception.frame=read
capabilities: screenpipe.health=READY
              screenpipe.activity=READY
              screenpipe.history_query=READY
              screenpipe.accessibility=PROBING
              screenpipe.raw_frame=PROBING
              screenpipe.audio=DISCOVERED
              screenpipe.private_window_filtering=PROBING
retry_policy: max_attempts=3 backoff_s=[1,3,9]
```

All actions declare `read` semantics (P2-F2 PASS). No write/execute/
communicate/external_effect actions exist. Registration is idempotent
(identical re-delivery dedupes at the ledger; one `connector.registered`
row for repeated registration).

## Participation in existing architecture

- health: `connector.health_sample` (HEALTHY/DEGRADED/DISABLED/UNKNOWN)
- enable/disable: `connector.disabled` / `connector.enabled` honored by
  the cycle (P2-F15/F16 PASS)
- STOP: `EmergencyStop` sentinel checked before any read (P2-F17 PASS)
- veto: existing `matching_veto_reason` gates in registry routing remain
  authoritative for connector selection (no bypass added)
- quota/budget: `connector.quota_recorded` per cycle
  (used=observations emitted, limit=max_observations_per_cycle)
- routing: `select_connector` unchanged; Eyes uses the same deterministic
  gates
- audit: every cycle action is an append-only event

## Source interface

Preferred source order honored: local API `/health` for health
(whitelisted/unauthenticated per Phase-1 evidence); guarded read-only
SQLite for bounded historical record reads (Phase-1 proved no dedicated
activity-summary endpoint; `/search` requires Bearer auth, 403 without
it). SQLite dependency is ISOLATED inside `perception/screenpipe.py`,
schema-guarded (`frames`/`ui_events` column set checked), fail-closed
(unknown schema -> `SourceFailure(UNKNOWN_SCHEMA)` -> DEGRADED).

## Capability honesty

Not everything is READY: raw frame stays PROBING, accessibility PARTIAL
-> PROBING, audio DISCOVERED/DISABLED, private-window filtering PROBING
per Phase-1 evidence. No new capability-state vocabulary introduced;
existing `CapabilityState` values used.
