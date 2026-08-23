# snapshot.discovery Payload Contract (pre-implementation)

Derived ONLY from evidenced canonical shapes at base ceb90d7:
- `v3_cycle.CycleReport.to_payload` (v3_cycle.py:39-53) + `discovery.cycle_completed` events
- `discovery.candidate_generated|scored|rejected|superseded` payloads (v3_scoring.py, projections.py)
- `candidate_scores`, `candidate_outcomes`, `scoring_policy_registry`, `discovery_portfolio` tables (migrations.py v36)
- `executive.decision_recorded` payload (executive/decisions.py:358-395), incl. `wait.wait_kind`,
  `rejected[{candidate_id, composite, reason}]`, `deferred[{candidate_id, reason}]`
- `executive.decision_materialized` / `executive.materialization_blocked` payloads
- `verification_verdicts` table (verdict/independence_json/builder_run_id/verifier_run_id/mission_id)
- `agent_runs` table (role/provider/status/mission_id/run_id)
- `WorkerRuntimeConfig.discovery_cycle_enabled` — passed in as explicit parameter
  (no ledger surface exists at base; absent → flag_state "UNREGISTERED")

## Top-level shape (additive key "discovery" in build_snapshot dict)

```jsonc
{
  "schema": 1,                       // projection schema version (int)
  "state": "FRESH" | "STALE" | "PARTIAL" | "EMPTY_LEDGER",
      // EMPTY_LEDGER: no discovery events AND no candidates rows
      // PARTIAL: >=1 candidate/cycle field degraded to unavailable
      // STALE: reserved for freshness policy; never set when data is current
  "flag_state": "ON" | "OFF" | "UNREGISTERED",   // from registered config param
  "policy_version": string | null,   // active scoring_policy_registry row; null = none registered
  "latest_cycle": { ... } | null,    // newest discovery.cycle_completed-derived cycle (see below)
  "cycles": [ ... ],                 // newest-first, bounded (default 10)
  "candidates_recent": [ ... ],      // newest-first by updated_at then id, bounded (default 25)
  "selected": { ... } | null,        // latest SELECT decision with lineage (see below)
  "portfolio_summary": {"eligible": int|null, "deferred": int|null, "rejected": int|null},
                                     // from newest portfolio.evaluated event; null fields honest
  "active_team": [ ... ],            // bounded agent_runs for the selected mission (see below)
  "verifier": { ... } | null,        // verifier truth for the selected mission (see below)
  "outcomes": [ ... ],               // bounded candidate_outcomes-derived (newest first)
  "maintenance_due": null,           // no durable maintenance-due surface exists at base -> always null (recorded reason below)
  "next_action": string | null       // derived continuation (see derivation rules)
}
```

UI mapper note: the adapter maps this to V3DiscoveryView. UI names
(policyVersion, candidatesRecent, verifierState, outcomeMeasurements,
activeTeam) are mapped from these backend names.

## latest_cycle / cycles[i] — derived from each discovery.cycle_completed event
(joinable to worker_loop.cycle_started via loop_id for startedAt)

```jsonc
{
  "loop_id": str,
  "started_at": ISO-str|null,        // matching worker_loop.cycle_started event occurred_at; null if absent
  "completed_at": str(event.recorded_at),
  "outcome": "COMPILED"|"WAITING"|"STOPPED"|"BLOCKED",  // as recorded
  "decision_id": str|null,
  "mission_id": str|null,
  "candidate_id": str|null,
  "observed": int, "candidates_created": int,
  "scored": int|null, "eligible": int|null, "rejected": int|null,
      // scored/eligible/rejected come from the SAME cycle's portfolio.evaluated
      // (loop_id join). For NO_ELIGIBLE_CANDIDATES waits eligible is 0 per
      // actual decisions; STOPPED cycles carry nulls (no scoring happened).
  "degraded_sources": [str],
  "reason": str|null,                // verbatim
  "wait_kind": "NO_ELIGIBLE_CANDIDATES"|"COMPARATIVE_WAIT"|null,
      // joined from executive.decision_recorded.wait.wait_kind via decision_id;
      // null when decision_id null or decision not WAIT
}
```

## candidates_recent[i]

```jsonc
{
  "candidate_id": str, "fingerprint": str|null, "type": str|null,
  "objective": str, "status": str,           // row status verbatim
  "created_from": str|null, "created_at": str|null,
  "updated_at": str, "expires_at": str|null,
  "evidence_ref_count": int,                  // len(evidence_json list of dicts); 0 on malformed
  "score": {
    "total": number,                          // candidate_scores.score
    "policy_version": str|null,               // candidates.scoring_policy_version or scored-event policy_version
    "dimensions": {...}|null, "weights": {...}|null,   // from candidate_scores.weights_json? NO -
        // dimensions come from the discovery.candidate_scored event trace (authoritative);
        // weights likewise. Malformed JSON -> null fields, candidate flagged partial.
    "calibration_adjust": number,             // trace.calibration_adjust default 0.0
    "thresholds": {...}|null                  // trace.thresholds
  } | null,
  "rejection": {"code": str, "detail": str} | null,
      // code from discovery.candidate_rejected reason_code; detail = reason text.
      // Non-null exactly when status REJECTED and a rejection event exists.
  "superseded_by": str|null, "deferred_until": str|null,
  "mission_id": str|null,                     // lineage via created_from=candidate:{id} -> missions.created_from scan
  "partial": bool                             // true when any sub-field degraded
}
```

No prompts/untrusted blobs in payload: objective/evidence summaries only carry
counts and ids, not evidence bodies.

## selected (latest executive.decision_recorded with decision=SELECT)

```jsonc
{
  "decision_id": str, "candidate_id": str,
  "total_score": number|null,            // selected_score.composite (executive composite)
  "comparative_rationale": null|string,  // wait.comparative_rationale is WAIT-only; SELECT carries reason
  "reason": str,                         // decision.reason verbatim
  "rejected_alternatives": [{"candidate_id": str, "total_score": number|null, "reason": str}],
  "deferred_alternatives": [{"candidate_id": str, "reason": str}],
  "mission_id": str|null,                // executive.decision_materialized join
  "block_reason": str|null               // executive.materialization_blocked join (latest for decision)
}
```

## active_team[i] (bounded to selected mission, else empty)

```jsonc
{ "run_id": str, "role": str, "provider": str|null, "model": str|null,
  "status": "RUNNING"|"COMPLETED"|"FAILED"|"CANCELLED", "mission_id": str }
```

## verifier (for selected mission; null when none)

```jsonc
{
  "mission_id": str,
  "verdict": "PASS"|"FAIL"|"BLOCKED"|"NOT_TESTED"|null,   // latest verdicts row
  "fresh_session": bool|null,     // independence_json.fresh_session when present else null
  "independence": "INDEPENDENT"|"REDUCED"|null,  // independence_json.independence else null
  "builder_run_id": str|null, "verifier_run_id": str|null,
  "repair_rounds_used": int,      // count of verification FAIL verdict rows for mission
  "repair_budget": 3              // v3_teams.DEFAULT_REPAIR_BUDGET constant
}
```

## outcomes[i] (bounded newest-first)

```jsonc
{
  "candidate_id": str, "mission_id": str,
  "window": "immediate"|"7d"|"30d",
  "predicted": {...},              // predicted_value_json; {} stays {}
  "actual": {...}|null,            // actual_value_json; status UNKNOWN -> null (honest UNKNOWN preserved)
  "status": "RECORDED"|"UNKNOWN",
  "confidence": number,
  "delta": number|null             // executive.calibration_recorded delta for candidate+mission; else null
}
```

## next_action derivation (honest, ledger-state only)

Priority order:
1. Any active STOP directive/veto evidence (controls vetoes present in same snapshot) -> "WAIT (STOP active)" — actually derived inside projection from directives query: veto rows exist -> next_action = "HOLD: owner veto/STOP active".
2. Latest cycle outcome COMPILED with mission still non-terminal -> "EXECUTE_MISSION:<id>".
3. Mission terminal PASSED + outcome recorded -> "RUN_NEXT_CYCLE" (continuation).
4. Mission terminal PASSED + outcome MISSING -> "RECONCILE_OUTCOME:<mission>" (closure debt made visible).
5. Latest cycle WAITING/BLOCKED/STOPPED -> "WAIT" (+ reason carried in cycle).
6. Nothing derivable -> null.

## cost_window

NOT EMITTED. Spec §3.13 names it but no canonical payload shape exists in any
event/table at base. Absent by design (prompt forbids fabricating it);
recorded here as the documented absence reason.

## maintenance_due

Always null at this schema: `_maintenance_candidates` produces candidates, not
a due-date surface; no durable "maintenance due at T" record exists. Absent
rather than invented.

## Bounds

cycles<=10, candidates_recent<=25, outcomes<=50, team rows<=20,
alternatives passthrough bounded by decision payload (already bounded upstream
by candidate_limit=20). All queries use indexed columns where available
(candidates PK, candidate_outcomes PK + candidate index, events_type_sequence
index for event-type scans). No unbounded scans: event reads restricted to
specific event types via SQL (not full-ledger Python iteration) wherever a
query can express it; _executive_projection-style bounded loops retained only
where the existing convention already scans (documented).

## Failure honesty rules

- Missing/malformed optional JSON -> null/{} + candidate["partial"]=true;
  never manufactured values.
- Legacy DB without v36 tables -> whole section degrades to
  {"available": false-shaped state:"UNSUPPORTED_SCHEMA"} equivalent:
  build_snapshot emits discovery.state="PARTIAL" with empty lists +
  "unsupported_schema": true marker... FINAL DECISION: emit
  `"state": "UNSUPPORTED_SCHEMA"` (additive enum value) with empty lists so
  the adapter can map honestly instead of crashing the whole snapshot.
