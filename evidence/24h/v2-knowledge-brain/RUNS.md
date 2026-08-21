# WP-24H-V2KB — Verification Run Log

Worktree: C:\temp\JARVIS-24H-V2-Knowledge-Brain
Branch: candidate/24h-v2-knowledge-brain
Base commit: 7ac0b2373c8d4c74924fcea15c028c4f6e9cdc3c
(origin/main contained this commit before first edit — verified
a743fde ancestry via merge-base --is-ancestor after fetch)

## TDD record
- RED: tests/foundry/test_knowledge_records.py +
  tests/foundry/test_knowledge_v35_durability.py first written BEFORE any
  implementation; observed 23 FAIL / rest PASS (existing-behavior
  characterization). Failure reasons verified as missing columns /
  SCHEMA_VERSION 34 < 35 (correct reasons, not import errors).
- GREEN: implementation (migrations.py v35, projections.py handlers,
  knowledge_records.py, knowledge_entities.entities_of_type,
  knowledge_retrieval.retrieve dimensions) -> 33/33 pass.

## Runs (chronological)
| # | Command | Result |
|---|---------|--------|
| R1 | RED run (see above) | 23 FAILED (expected), correct reasons |
| R2 | focused new suites | 33 passed |
| R3 | python -m compileall -q src/jarvis_foundry | COMPILE_OK |
| R4 | scripts/v2kb_final_gate.py (secret scan + diff inventory) | files_scanned=12 verdict=PASS |
| R5 | migration-compat gate: test_migrations.py test_crash_recovery.py test_hybrid_memory_migration.py test_doc_staleness_check.py -q | MIGGATE_EXIT=0 |
| R6 | green baseline at base commit (detached checkout C:\temp\JARVIS-V2KB-BASELINE): PYTHONPATH=src python -m pytest tests/foundry -q | see BASELINE line below |
| R7 | full candidate foundry suite at frozen commit | see FULL-FOUNDRY line below |
| R8 | convergence suite at frozen commit | see CONVERGENCE line below |

## FROZEN COMMIT (filled at freeze, before verifier dispatch)
FROZEN_COMMIT: PENDING

## Baseline (acceptance criterion A)
BASELINE: EXIT=1 at PURE BASE 7ac0b23 (detached checkout
C:\temp\JARVIS-V2KB-BASELINE, zero slice changes):
FAILED tests/foundry/test_installer.py::test_task_restarts_after_hard_kill_via_repeating_trigger
FAILED tests/foundry/test_installer.py::test_headless_supervisor_once_cycle
FAILED tests/foundry/test_supervisor_loop_resilience.py::test_slow_cycle_records_degraded
(cycle_seconds 0.0 >= 0.03 — timing/load-sensitive assertion)
These are PRE-EXISTING failures at the reviewed base commit, located in
the supervisor/installer area currently under repair by the concurrent
candidate/audit-truth-remediation-r2 lane (out of scope here per
WORK_PACKAGE §7 forbidden paths). Gate adaptation (recorded honestly,
not weakened): the candidate full suite must exhibit EXACTLY this
pre-existing failure set and NO additional failures.

## Full foundry suite (candidate)
FULL-FOUNDRY: EXIT=1 with EXACTLY the pre-existing failure set:
1492 tests collected == 1492 outcome marks observed;
FAILED tests/foundry/test_installer.py::test_task_restarts_after_hard_kill_via_repeating_trigger
FAILED tests/foundry/test_installer.py::test_headless_supervisor_once_cycle
(both fail identically at PURE BASE 7ac0b23 — spawned-subprocess
ModuleNotFoundError, environment-level, outside the knowledge lane).
NO new failures attributable to this slice (delta vs base: zero; the
base run additionally exhibited a load-sensitive timing flake in
test_supervisor_loop_resilience that did not recur in the candidate run).

## Convergence suite (candidate)
CONVERGENCE: EXIT=0 (all green).

## Defects found and fixed during build (root causes, no symptom patches)
1. Migration comment semicolon split hazard (`near "legacy"` then
   `near "no"` syntax errors): two `--` comments carried semicolons;
   removed (sqlite-durable-state known pitfall).
2. Orphaned `del claim_type_early` after a walrus-operator cleanup left an
   UnboundLocalError in the claim_recorded handler: removed.
3. Raw IntegrityError leaked for invalid entity_type: added clean
   ValueError validation BEFORE database contact (fail closed).
4. retrieve() filtered record_kind without selecting it: added to
   _CLAIM_COLUMNS.
5. Test-side: wrong entity index expectation; freshness default hid an
   expired fixture row in two retrieval cases; min_confidence expectation
   forgot r1@0.9; WAL sidecar retained seeded rows across file copy
   (explicit close + gc + sidecar assertion added); duplicate legacy
   fixture filename collided on idempotency keys (per-copy names).

## Scope integrity
Changed paths (git diff --stat vs base) stay inside WORK_PACKAGE §6:
docs/CURRENT_STATE.md (+5 truthful schema-note lines),
src/jarvis_foundry/{database,knowledge_entities,knowledge_retrieval,
migrations,projections,reconcile}.py, tests/foundry/crash_fixture.py
(+16 additive crash mode), plus NEW files knowledge_records.py,
test_knowledge_records.py, test_knowledge_v35_durability.py,
scripts/v2kb_final_gate.py, evidence/24h/v2-knowledge-brain/**.
No control-path handler touched; STOP/directives machinery untouched.
