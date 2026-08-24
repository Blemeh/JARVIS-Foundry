# MEMORY FAILURE MATRIX (Phase P — proven by tests)

| Failure mode | Test | Required behavior observed |
|---|---|---|
| Tier C unavailable at compile time | test_adversarial_matrix.py::TestOutageDegradation::test_tier_c_unavailable_degrades_not_breaks (+ test_software_context degrade) | governance section survives; degradation recorded in notes; no exception escapes to control plane |
| Obsidian unavailable entirely | TestOutageDegradation::test_obsidian_unavailable_does_not_touch_ledger | ledger writebacks proceed; event count advances; verdicts recorded |
| Obsidian note corrupted / malformed frontmatter | test_obsidian_mirror.py::test_malformed_frontmatter_refused_before_any_mutation + canonical test_obsidian_write contract suite | refusal BEFORE any file or ledger mutation; note not created |
| Concurrent/stale edit conflict | test_obsidian_mirror.py::test_update_conflict_preserves_both_versions; canonical writer protocol | incoming bytes preserved under Audit/conflicts/; disk keeps concurrent edit; both versions survive |
| Rollback safety | test_obsidian_mirror.py::test_rollback_restores_byte_exact; canonical rollback refusal of newer-edit clobber | byte-exact restore; refuses when note changed again since write |
| Memory git divergence | canonical hybrid_memory versioning suite (unchanged, green) | DivergenceError raised; explicit decision required |
| Secret/poisoned memory candidate | TestPoisonInjection (4 payloads, parametrized) + WritebackRejected paths | refused before ANY durable write; nothing in claims or tier_c_memories |
| Oversized memory/context | test_software_context.py::test_budget_law_governance_survives_starvation; factory s13 | governance kept within 500-char budget; omissions listed in dropped[] |
| Contradictory claims | s1 research contradiction + canonical negation-pair detection | contradiction pair projected; research cue created; both claims visible |
| Stale procedure | TestProcedureExperience::test_reality_change_marks_stale | state STALE; confidence reduced; not silently reused as fresh guidance |
| Missing provenance | canonical curation validate() finding missing_provenance | validation fails closed; accept refused without stamp |
| Malformed metadata | canonical frontmatter parser + identity alias validation | deterministic ObsidianPolicyError/ProjectIdentityError refusals |
| Context compiler interruption | s10 Tier-C outage monkeypatch | degrades honestly with notes; governance intact |
| Process restart between write and index | s15 rebuild/reconcile + identity alias rebuild test | projections rebuilt identical from events; recall finds survivor |

Control-plane invariants re-verified: STOP/directive sections are compiled
first from the ledger and never sourced from memory; no memory surface can
emit a PASS; all verdicts flow only through mission.verification events.
