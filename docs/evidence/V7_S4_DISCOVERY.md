# V7-S4 — REAL-OBSERVATION DISCOVERY AND SHADOW DECISIONS

```text
STATUS: PASS — CLOSED (independent verifier deleg_e52adaf2, structured PASS; 1 MEDIUM corrected additively)
PHASE: V7_FULL_AUTONOMOUS_OPERATION
SLICE: S4 — real-observation discovery and shadow decisions
OBJECTIVE: read real live observations without mutation; run discovery,
  deduplication, and candidate selection in shadow on the isolated V7 runtime
  ledger; every candidate cites a recorded observation/evidence reference and
  freezes metric, minimum measurable improvement, risk, guardrails, budget,
  canary, rollback, and expected value BEFORE results. Gate: at least three
  genuine observations yield correctly deduplicated candidates or honest
  WAITING decisions; no live mutation; no unrecorded intuition promoted as
  truth.
AUTHORITY_AND_ACTIVE_CONTROLS: V7 directive; S0-S3 closed PASS; standing
  mandate; no vetoes; live STOP sentinel absent; task triggers held.
BASE_COMMIT_AND_DIFF: base 91b2143 (S3 close); S4 adds no source changes —
  evidence scripts + report only (var/evidence/v7-s4/, docs/evidence/
  V7_S4_DISCOVERY.md).
LIVE_RELEASE_PROCESS_AND_DATABASE: untouched (verified: live schema v20,
  daemon RUNNING pre-merge code, task triggers N/A, all reads mode=ro).
CAPABILITIES_AND_READINESS: discovery machinery (S10) is now fed by REAL
  recorded observations; the V7 runtime ledger carries 3 improvements
  (adoption base + 2 discovery candidates) — all promotion-ineligible until
  cycles run (S5).
MISSION_OBSERVATION_OR_HYPOTHESIS: five genuine observations mined from the
  live ledger (evidence refs below); two became frozen candidates; three
  became recorded WAITING decisions with reasons.
SYSTEMS_AND_CANONICAL_TARGETS: live ledger (read-only), V7 runtime ledger
  (var/v7-runtime/v7.db — isolated writes), main src at 91b2143.
RISK_AND_CONSEQUENCE: none to live state (shadow only); the two candidates
  carry frozen R2/R3 risk classes; the WAITING decisions prevent premature
  live mutation (pre-adoption V4 machinery stays untouched).
BASELINE_SUCCESS_THRESHOLD_AND_GUARDRAILS: gate met: 5 genuine observations
  -> 2 candidates + 3 WAITING; dedup proven by identical re-run (same
  improvement ids, no duplicate rows); every candidate cites live-ledger
  evidence refs; live DB untouched (re-verified).
BUDGET_QUOTA_AND_COST: local only; zero provider cost; candidate budgets
  max_cost_usd 0.0.
ACTIONS_TAKEN: mined the live ledger read-only (agent_run.failed ×2,
  verification.failed ×13 + closure check, supervisor.degraded ×1, monitor/
  scoring timelines, knowledge staleness, candidate queue); ran
  var/evidence/v7-s4/discover-shadow.py on the V7 runtime ledger (2
  candidates recorded, 3 WAITING recorded, identical-rerun dedup proof);
  wrote this report.
FILES_PROCESSES_DATABASES_AND_EXTERNAL_STATE_CHANGED: V7 runtime ledger
  (isolated) gained discovery events + 2 improvement records + 1 discovery
  run; evidence files under var/evidence/v7-s4/; this report. NO live
  changes.
TESTS_RECONCILIATION_RESTORE_AND_FAULTS: discovery machinery re-exercised on
  real data (candidate_improvements validation, all-or-nothing recording,
  dedup); closure check for the 13 verifier FAILs (13/13 have later passes);
  no suite changes (S4 is evidence-only) — full suite remains 926 EXIT=0
  from S3.
VERIFIER_AND_STRUCTURED_VERDICT: <fill after dispatch>
EVIDENCE_PATHS_AND_HASHES: var/evidence/v7-s4/{discover-shadow.py,
  discovery-report.json}; var/v7-runtime/v7.db (isolated); live ledger
  evidence refs: events 10990, 11348, 14946, verification.failed ×13,
  discovery.monitor_polled (52), discovery.candidate_scored (237),
  knowledge_claims (1,071 stale).
REAL_EFFECT_PROVIDER_REFERENCE_AND_AFTER_STATE: none (shadow only).
ROLLBACK_COMPENSATION_OR_CONTAINMENT: revert the V7 runtime ledger to its S3
  state (the ledger is disposable/isolated); no live exposure.
UNRESOLVED_RISKS_LIMITATIONS_AND_BLOCKERS: same-provider verifier limitation
  (D-031); the two candidates' fixes will be built and measured in S5 (real
  cycles) against the isolated runtime; the three WAITING decisions bind
  their actions to the S6 adoption window.
NEXT_AUTONOMOUS_ACTION: S5 — first real durable self-evolution cycles on the
  V7 runtime ledger (build -> lab -> verifier -> shadow -> canary -> promote/
  reject -> measure) for the two S4 candidates.
OWNER_INPUT_REQUIRED: NONE
```

## 1. Mined observations (all from the live ledger, read-only)

| # | Observation | Evidence ref | Decision |
|---|---|---|---|
| OBS-1 | 2 verify-role worker session failures (timeout 300s bound; heartbeat_timeout) | live-ledger:events:10990+11348 | CANDIDATE `improvement-023a0ec23c72` (R2) |
| OBS-2 | 1 supervisor heartbeat failure under DB lock (OperationalError, recovered) | live-ledger:events:14946 | CANDIDATE `improvement-9e684edeb0f7` (R3) |
| OBS-3 | 13 verification.failed — 13/13 closed by later verification.passed | live-ledger:verification.failed:13-closed | WAITING (all closed; no open finding) |
| OBS-4 | scoring stopped 08-12T23:59 (678 NEW unscored); monitors silent since 08-13T10:06 | live-ledger:candidates+monitors:snapshot | WAITING (pre-adoption V4 pipeline; action at S6) |
| OBS-5 | 1,071/1,071 live knowledge claims stale | live-ledger:knowledge_claims:fresh_until | WAITING (refresh loop is V2/V4 machinery; action at S6) |

## 2. Frozen candidates (metrics frozen BEFORE results)

- `improvement-023a0ec23c72` — verify-role worker session reliability:
  baseline 2 recorded failures; MMI decrease ≥1 failure; guardrails
  (correctness/safety/cost/latency); workload ref discovery + hash; budget
  0; rollback plan git revert; risk R2 (roles/mission surfaces).
- `improvement-9e684edeb0f7` — supervisor heartbeat resilience under DB lock
  contention: baseline 1 degraded event; MMI decrease ≥1; risk R3
  (supervisor/database surfaces).

## 3. Dedup proof

Identical re-run of `record_discovered_improvements` returned the SAME two
improvement ids with NO additional rows (improvement_count stayed 3 =
adoption base + 2 candidates); the discovery run recorded once.

## 4. Verifier — deleg_e52adaf2, PASS (fresh independent adversarial session)

Verdict JSON: `%TEMP%\v7s4-verdict-verifier.json` (early-skeleton protocol;
verdict PASS, criteria C1-C5 all PASS with per-criterion evidence).

- C1 PASS: every live-ledger claim re-derived from mode=ro reads (2
  agent_run.failed seq 10990/11348; 1 supervisor.degraded seq 14946
  'database is locked'; 13 verification.failed ALL closed by later
  verification.passed; scored 237 last 08-12T23:59:27Z; monitor_polled 52
  last 08-13T10:06:04Z; candidates 934 = 678 NEW + 232 SCORED + 19 REJECTED
  + 3 EXECUTED + 2 SUPERSEDED; knowledge_claims 1071/1071 stale).
- C2 PASS with F1: ledger holds exactly 3 improvement_records; the discovery
  machinery froze improvement-023a0ec23c72 at risk_class **R3** (surfaces
  classifier: roles + mission → R3), NOT R2 as the report claimed — the
  classifier was more conservative than the declared class. Additive
  correction below; the cycle-level records in S5 inherit R3 (fail-closed).
- C3 PASS: dedup re-proven on a backup-API copy — identical re-run returned
  the same 2 ids, 3→3 rows, 1→1 discovery runs.
- C4 PASS: 5 observations → 2 CANDIDATE + 3 WAITING with honest reasons;
  evidence refs match; live untouched (v20, daemons alive, task XML
  Enabled=false, no STOP, ports up).
- C5 PASS: git truth + protected hashes 5/5.
- Findings: F1 only (above). Scope violations: none.

## 5. Final status

**S4 CLOSED — PASS** with one additive correction: OBS-1's candidate
improvement-023a0ec23c72 is frozen at risk R3 (machinery-classified from
surfaces roles+mission), not R2 — fail-closed and consistent with the
adoption record's own R3. All S4 decisions stand unchanged; no live
mutation occurred; every observation carries an exact ledger evidence
reference.
