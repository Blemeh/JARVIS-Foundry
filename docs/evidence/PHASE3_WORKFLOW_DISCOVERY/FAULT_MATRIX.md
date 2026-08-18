# PHASE 3 ADVERSARIAL FAULT MATRIX

The expected and actual results below are backed by
`tests/foundry/test_workflow_discovery.py`; no row is inferred from the
absence of a failure.

| ID | EXPECTED | ACTUAL | EVIDENCE | VERDICT |
|---|---|---|---|---|
| F1 | empty history creates no pattern | no sessions/patterns | `test_no_observations_produce_no_patterns` | PASS |
| F2 | one session is not repeated | no pattern | `test_one_session_is_never_repetition` | PASS |
| F3 | exact sequence becomes one pattern | one pattern, two occurrences | `test_exact_repeated_sequence_is_one_pattern` | PASS |
| F4 | duration variation preserves structure | one pattern, nonzero duration variability | `test_duration_variation_keeps_structure` | PASS |
| F5 | safe parameter variation preserves structure | one pattern; only `field` key retained | `test_safe_parameter_variation_keeps_structure` | PASS |
| F6 | materially different sequences separate | two patterns | `test_materially_different_sequences_are_separate` | PASS |
| F7 | shared prefix is not equivalence | endpoint guard keeps two patterns | `test_shared_prefix_does_not_merge` | PASS |
| F8 | large idle gap splits sessions | two sessions with idle boundary | `test_large_idle_gap_splits_sessions` | PASS |
| F9 | duplicate observation ids do not inflate count | one session, no repetition | `test_duplicate_observations_do_not_inflate_occurrences` | PASS |
| F10 | reordered batch does not fabricate order | sorted result is deterministic | `test_reordered_input_is_deterministic_and_marks_late_events` | PASS |
| F11 | stale evidence retains lineage | stale flag plus observation refs | `test_stale_pattern_preserves_lineage` | PASS |
| F12 | source restart creates boundary | two sessions | `test_source_restart_is_a_boundary` | PASS |
| F13 | secret-like values never survive | fake values absent from token/evidence JSON | `test_secret_and_raw_text_never_reach_tokens_or_evidence` | PASS |
| F14 | prompt injection has no control effect | no directive or mission event | `test_prompt_injection_is_data_only_and_has_zero_control_effects` | PASS |
| F15 | same history is idempotent | pure result and evidence event stable | `test_mining_same_history_is_deterministic`, `test_evidence_persistence_is_idempotent_and_rebuildable` | PASS |
| F16 | crash before candidate can retry | evidence-only state retries once | `test_crash_before_candidate_retries_once` | PASS |
| F17 | crash after candidate does not duplicate | second bridge is duplicate | `test_crash_before_candidate_retries_once` | PASS |
| F18 | new occurrence evolves lineage | second immutable snapshot supersedes first | `test_new_occurrence_adds_lineage_snapshot` | PASS |
| F19 | stale policy applies | old pattern marked stale | `test_stale_pattern_preserves_lineage` | PASS |
| F20 | low-value repetition may be rejected | existing candidate becomes REJECTED | `test_low_value_pattern_can_be_rejected_by_existing_system` | PASS |
| F21 | controlled valuable pattern reaches candidate flow | existing generator and scorer used | `test_candidate_bridge_reuses_existing_scoring_and_no_mission` | PASS |
| F22 | candidate-to-mission provenance is existing | `created_from=candidate:<id>`; no replay | `test_candidate_to_mission_provenance_uses_existing_api` | PASS |
| F23 | STOP blocks candidate-producing writes | no evidence/candidate while stopped | `test_stop_blocks_evidence_and_candidate_then_resume` | PASS |
| F24 | resume reconciles idempotently | one candidate after resume and retry | `test_stop_blocks_evidence_and_candidate_then_resume` | PASS |
| F25 | miner failure/isolation does not break Eyes | pure miner leaves DB and perception events untouched | `test_miner_is_pure_and_eyes_boundary_remains_intact` plus Phase-2 suite | PASS |

No unresolved CRITICAL or HIGH findings were observed in the focused gate.
