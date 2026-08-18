# PERCEPTION EVENT GATE — JARVIS EYES PHASE 2

## Event types added (minimal, justified)

| Event | Meaning |
|---|---|
| `perception.observed` | one bounded semantic observation in the append-only ledger |
| `perception.redacted` | a cycle suppressed/redacted material (counts only) |
| `perception.source_degraded` | source unavailable/corrupt/unknown-schema |
| `perception.source_recovered` | source readable again |

`ingestion.cursor_updated` (EXISTING event, existing projection) is reused
for the durable cursor — no new cursor mechanism.

No other speculative event types were added. Corrections are new events
(cursor advances are monotonic; re-advance dedupes, never rewrites).

## Observation payload (frozen contract)

`perception.observed` payload carries ONLY: observation_id, source
connector, source pointer (external ids + record count), captured_at /
interval_end / recorded_at, application, resource, activity_class,
bounded semantic summary, transitions, content hash, privacy class,
redaction state, confidence, raw_external=True, source_version,
retention class. No raw frames, no full text, no accessibility trees, no
browser profile data (P2-F3/F14 PASS; payload test asserts absence).

## Epistemic boundary

P2-F8 PASS: a perception event alone produces ZERO
`knowledge.claim_recorded` events. P2-F9 PASS: zero candidates/missions.
P2-F7 PASS: screen prompt injection cannot create owner directives,
missions, claims, or actions.
