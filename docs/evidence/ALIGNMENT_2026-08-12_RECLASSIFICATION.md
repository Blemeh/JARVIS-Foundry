# Alignment 2026-08-12 — Version Reclassification (Two-Gate Model)

Status: **ACTIVE** — canonical version-gate record per
DIR-2026-08-12-master-plan-alignment (ledger seq 5486–5488).

Purpose: correct the gap between version labels and real operational
outcomes. This record RE-CLASSIFIES V1, V2 and V3 under the two-gate
model. It does NOT invalidate, delete or rewrite any prior record — the
WP8/WP9/WP10 gate reports remain historical engineering records. Where
this document and an older record disagree, this document is the
current truth and the older record is marked historical.

## Version model (per directive)

| Version | Engineering gate | Operational gate | Promotion state |
|---|---|---|---|
| V1 (WP0–WP8) | PASS | NOT_TESTED | HOLD (was PROMOTED under waiver) |
| V2 (WP9) | PASS | NOT_TESTED | HOLD |
| V3 (WP10) | IN PROGRESS (slice-1 PASS; slice-2 verified pending) | NOT_TESTED | HOLD |
| V4 | NOT_TESTED | NOT_TESTED | HOLD (inactive; activation rule below) |

Hold source: `control.changed:version-promotion-hold` (ledger seq 5487).
No version is promoted and V4 does not activate until BOTH gates PASS for
that version, unless a later authenticated owner directive changes one
named requirement.

## V1 — Autonomy Kernel (WP0–WP8)

```yaml
version: V1
engineering_gate: PASS
operational_gate: NOT_TESTED
promotion_state: HOLD
promotion_history: PROMOTED under authenticated duration waiver
  (DIR-2026-08-12-seven-day-waiver; ledger seq 1763 + 2143; owner
  message 16242) — accepted-under-waiver, NOT "seven-day reliability
  proven"
accepted_scope:
  - event store (append-only, correlated, idempotent)
  - Supervisor (independent of Hermes; 5s cadence; singleton lease)
  - deterministic STOP (sentinel + CLI; live latency ≤10s)
  - leases and recovery
  - action control (veto/STOP precedence)
  - mission and verification loops
  - governed Obsidian write-back (JARVIS/V1 area)
  - fault-injection foundation (F1–F15)
remaining_scope:
  - real multi-day unattended reliability (observation window OPEN —
    ledger seq 5488; evidence accumulates from heartbeat continuity,
    restarts, recoveries)
  - incomplete physical reboot evidence (F4 owner-resumable)
  - any previously NOT_TESTED reconciliation surface
evidence: docs/evidence/WP8_GATE_REPORT.md; docs/evidence/WP8_DAY1_REPORT.md;
  var/evidence/wp8-gate/ (private); ledger verification.passed:verdict-wp8-gate
  seq 2910
failures: WP8 gate round-1 FAIL (deleg_acbc8ce5) + round-2 FAIL
  (deleg_1e6e9421), all repaired with regressions; FIND-2026-08-12-01..-05
  recorded
repairs: STOP latency default 30s→5s; console-control fast durable stop;
  adapter provider canonicalization; installer UserId; exit-0 failure
  classification (all with regression coverage)
residual_risks: same-provider verifier limitation; heuristic failure-prefix
  whitelist; F8 simulation; staging-only audit model; duration waiver
  shortens the reliability proof (explicit — see observation window)
next_action: V1 reliability observation window continues (OPEN); no
  reliability claim beyond the evidence the window accumulates
```

## V2 — Knowledge Brain (WP9)

```yaml
version: V2
engineering_gate: PASS
operational_gate: NOT_TESTED
promotion_state: HOLD
accepted_scope (engineering):
  - epistemic claims and provenance (claim types, support status)
  - confidence, freshness and contradictions (deterministic negation
    detector)
  - SQLite retrieval (retrieve() with filters)
  - bounded context manifests (hints, STALE, conflicts, truncation)
  - entity resolution (registry + aliases + validated links)
  - deterministic ingestion primitives (source-hash dedup, idempotent
    re-ingestion)
  - snapshots and backup verification (record-after-backup, sha256 +
    quick_check verification)
  - semantic rollback mechanics (append-only status flags, deterministic
    idempotency)
  - migrations and projection reconciliation (v5–v9)
remaining_scope (operational):
  - real Obsidian indexing (live vault: C:\Users\b\Blendi)
  - whole-vault retrieval
  - continuous live ingestion
  - autonomous memory consolidation
  - stale-knowledge research queues
  - context delivery to real workers
  - meaningful live knowledge data (live knowledge_* tables are EMPTY)
  - integrated semantic rollback
evidence: docs/evidence/WP9_GATE_EVIDENCE.md + WP9_SLICE*.md;
  var/evidence/wp9-*/ (private); ledger verification.passed:verdict-wp9-gate
  seq 4846
failures: 8 recorded verifier FAIL rounds across five slices (all
  repaired with regression coverage); gate review round-1 FAIL
  (deleg_d0a4244d, G3 live-DB truth correction)
repairs: membership-scoped conflict queries; content-derived idempotency
  payloads; SCHEMA_VERSION constant pins; record-after-backup checksums;
  live-DB schema truth correction (additive)
residual_risks: no live knowledge data yet (the operational gap this
  alignment closes); same-provider verifier limitation
next_action: WP11 V2-OPERATIONAL integration (directive Phase 4, slices
  A–G), then the 16-criterion V2 operational gate with an independent
  verifier
```

Description (directive-mandated phrasing): **"V2 engineering core
accepted; operational acceptance pending."**

## V3 — Discovery and Building (WP10)

```yaml
version: V3
engineering_gate: IN PROGRESS
  - slice-1 (candidate pipeline, schema v10): CLOSED — PASS
    (deleg_d70f796a; ledger seq 5100–5102; round-1 P3 timing finding
    repaired and re-verified)
  - slice-2 (monitors + mission-execution wiring, schema v11):
    IMPLEMENTED, 310-test suite EXIT=0, independent verifier PENDING
operational_gate: NOT_TESTED
  - no real monitored sources on the live system (monitors table not yet
    on the live schema)
  - no real autonomous loops completed
promotion_state: HOLD
accepted_scope (so far): discovery candidate pipeline (generation,
  dedup, calibrated scoring, mission creation, outcome tracking);
  monitored-source registry and mission-completion outcome wiring
remaining_scope: real monitored sources → multiple complete loops
  (observe → … → next action) on a real registered project; FAIL→repair→
  fresh-PASS cycle; interrupted-build recovery; remote-Git
  reconciliation; safe deployment-target reconciliation; maintenance
  work; next-mission selection (directive Phase 7)
evidence: docs/evidence/WP10_SLICE1_CANDIDATES.md +
  WP10_SLICE2_MONITORS.md; var/evidence/wp10-*/ (private); ledger seq
  5100–5102
failures: slice-1 round-1 verifier P3 (artifact timing — resolved,
  artifact regenerated at pinned HEAD); slice-2 full-suite draft-bug
  (mission.created handler pre-v11 schema adaptation, commit ac65025)
repairs: both with regression coverage and committed evidence
residual_risks: no operational loops yet; live schema still v10 (v11
  deploys when monitors go live, documented)
next_action: finish slice-2 verification (freeze → fresh adversarial
  verifier → ledger close), then V2-OP (Phase 4), then V3 real loops
```

## V1 reliability observation window

Per directive Phase 2 ("Create a durable V1 reliability observation
window without stopping unrelated safe work"):

- Window id: `v1-reliability-window` (ledger seq 5488, state OPEN).
- Purpose: accumulate honest multi-day unattended-reliability evidence
  for V1 — heartbeat continuity (5s cadence), restart behavior,
  recovery events, STOP latency, lease health.
- Method: passive observation of the live ledger + supervisor state;
  no cron jobs (owner paused all cron 2026-08-12), no popping windows;
  periodic snapshots recorded whenever a checkpoint runs.
- Close criterion: 7 consecutive days of continuous healthy operation
  (no unrecovered failure), or owner STOP, or owner waiver.
- Explicit: this window does NOT block other work and other work does
  not manufacture reliability evidence.

## V4 activation rule (unchanged destination)

V4 (command center / full organization) remains inactive until: V2
engineering PASS ✓, V2 operational PASS, V3 engineering PASS, V3
operational PASS, state files reconciled, evidence durable, no
severity-one defect, Supervisor and STOP healthy.

## Completion-condition tracker (directive COMPLETION CONDITION)

| # | Condition | Status |
|---|---|---|
| 1 | project state internally consistent | IN PROGRESS (this alignment commit) |
| 2 | engineering and operational gates separate | DONE (this document) |
| 3 | V1's shortened reliability proof explicit | DONE (waiver + observation window) |
| 4 | missing V2 live integration implemented | PENDING (WP11 V2-OP) |
| 5 | V2 independent operational PASS | PENDING (16-criterion gate) |
| 6 | durable privacy-safe evidence manifests | PENDING (directive Phase 5) |
| 7 | moving-target verification eliminated | DONE (freeze discipline, this commit onward) |
| 8 | V3 completes multiple real loops | PENDING (directive Phase 7) |
| 9 | interruption/repair/Git/deploy reconciliation proven | PENDING |
| 10 | V3 independent operational PASS | PENDING |
| 11 | V4 activates only then | PENDING |
| 12 | original complete JARVIS vision unchanged | HOLDING (no design change made) |
