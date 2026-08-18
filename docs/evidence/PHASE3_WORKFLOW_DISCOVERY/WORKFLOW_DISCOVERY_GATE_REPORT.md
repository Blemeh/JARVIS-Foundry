# PHASE 3 WORKFLOW DISCOVERY GATE REPORT

## Deterministic core

| Surface | Implementation | Proof |
|---|---|---|
| observation input | existing Phase-2 `PerceptionObservation`; payload rehydration validates the frozen contract | `test_phase2_payload_contract_is_reused` |
| sessionization | capture-time ordering, duplicate-id suppression, idle/max-duration/source-restart boundaries; explicit late markers preserved | sessionizer tests in `test_workflow_discovery.py` |
| safe tokens | allowlisted structural labels, URL/path/value classification, existing secret detector reused, safe parameter keys only | `test_safe_parameter_variation_keeps_structure`, `test_secret_and_raw_text_never_reach_tokens_or_evidence` |
| fingerprint | SHA-256 of normalized structural sequence + algorithm version + configuration hash | `test_mining_same_history_is_deterministic` |
| similarity | normalized Levenshtein similarity, matching endpoints, threshold 0.80, bounded 256 comparisons per bucket | `test_shared_prefix_does_not_merge`, `test_bounded_exact_history_scales_without_pairwise_rescan` |
| repetition evidence | occurrence/session/observation refs, timestamps, sequence, similarity, frequency, median duration, variability, freshness, privacy, versions, evidence ref | evidence persistence and lineage tests |
| storage | existing append-only event ledger (`workflow.pattern_evidence`); no schema migration | `test_evidence_persistence_is_idempotent_and_rebuildable`, `test_no_schema_migration_or_second_engine` |

## Existing governance bridge

- Source is exactly `workflow_discovery`.
- Evidence references are safe `workflow-pattern:<pattern-id>` references plus
  the ledger event id; raw observation payloads are not copied into candidates.
- `covered_objective` is checked before `generate_candidate`, then the
  existing duplicate detector and `score_candidate` are reused.
- Repeated bridge calls produce one selectable candidate; low-value evidence
  can be rejected through the existing `reject_candidate` path.
- Candidate approval and mission provenance are demonstrated only by calling
  the existing `approve_candidate`; workflow discovery itself emits no
  `mission.created` event.

## Controlled probe

The bounded probe in `scripts/phase3_workflow_discovery_probe.py` used only
constructed semantic observations, no Screenpipe DB, no frames, and no
computer execution. It found one controlled pattern with three occurrences;
see `var/phase3/probe-report.json` (generated, not committed).

## Current gate result

Focused Phase-3 tests: PASS (36/36 at this revision), including resource
switch structure, YAML fail-closed default, and duplicate late-marker order
independence.

Full candidate suite: PASS, 1,202 collected, EXIT 0 when run with an absolute
candidate `PYTHONPATH` so child-process regressions import the candidate code.
The final run is recorded in `var/phase3/full-suite-run-final.txt` (generated,
not committed).
