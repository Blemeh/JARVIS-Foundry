# JARVIS Remaster -> Canonical Convergence Matrix

Program: JARVIS FINAL REMASTER FORWARD-PORT & CANONICAL CONVERGENCE
Final convergence audit (2026-08-19): canonical runtime/source record `c60db92` with semantic source `93d23f5`; current published repository head `9c45694` contains documentation/evidence descendants only; current-source full suite `1260/0/0/0` is recorded at `CURRENT_SOURCE_FULL_SUITE_VENV_20260819.json`; exact current-release R3 proving is `WAITING_ELAPSED`; remaster certified head `6be9185` with dirty inventory `87/87` classified. Legacy evidence below is preserved and not relabelled.
Canonical runtime/source record: `c60db92` (main); current published repository head: `9c45694`; semantic source: `93d23f5`; reproducible suite snapshot: `f172147`; forward-port baseline: `2b3573606570f910e5569cf3306223354f93e4ac`; Remaster certified tip: `6be9185`; Merge-base: `93a3ddc`.
Method: SEMANTIC FORWARD-PORT. No wholesale merge, no blind cherry-pick. Newest canonical architecture is authoritative.

## Structural delta

Canonical `main` contains (newer architecture): `hybrid_memory/`, `workers/` (canonical Hybrid Workers + live runtime),
`runtime/`, `mission/loop.py` (autonomous mission loop: propose -> dispatch -> intake -> verify -> repair -> wait),
`evolution/` (V6 machinery: discover/builder/lab/shadow/canary/promote/restore/cycle/verify/acceptance/portfolio),
`capabilities/registry.py`, `connectors/activation.py`, `control/capability_broker.py`, `command_center.py`,
`briefings.py`, `voice.py`, `discovery_monitors.py`, `discovery_candidates.py`, `research_queue.py`, `knowledge*.py`,
`owner_controls.py`, `supervisor/estop.py`.

Remaster-only additive packages absent from main: `kernel/**`, `kernel/portfolio/**` (R7), `orchestration/**` (R8),
`capabilities_live/**` (R9), `command_center/**` (R10 package), `evolution_live/**` (R11), `fault_campaign/**` (R12),
`tests/remaster/**`, `scripts/remaster/r7..r12/**`.

## Disposition matrix

| Remaster component | R-stage | Original purpose | Current main equivalent | Main status | Disposition | Port method |
|---|---|---|---|---|---|---|
| kernel/** | R4 | Clean trusted kernel | main flat modules | PRESENT | SUPERSEDED_BY_NEWER_MAIN | Do NOT port kernel wholesale. Use main surfaces. |
| kernel/portfolio/** | R7 | Goals/portfolio/selection/WAITING | evolution/portfolio.py | PRESENT | ALREADY_PRESENT | Keep main. Port R7 gates/tests adapted to main portfolio API. |
| discovery/research/monitor semantics | R7 | Evidence-only discovery, monitored research | discovery_candidates.py, discovery_monitors.py, research_queue.py, knowledge*.py | PRESENT | ALREADY_PRESENT | Keep main. Adapted gates/tests. |
| mission candidate lineage | R7 | Candidate->decision->mission lineage | mission/loop.py, discovery_monitors, worker_context | PRESENT | ALREADY_PRESENT | Keep main. Adapted lineage tests. |
| orchestration/** | R8 | Autonomous production organization | workers/** + runtime/** + mission/loop.py | PRESENT (newer Hybrid Worker) | SUPERSEDED_BY_NEWER_MAIN | Do NOT port remaster worker pipeline. Prove R8 semantics against main worker/mission path. |
| capabilities_live/** | R9 | Capability lifecycle + brokerage | capabilities/registry.py, connectors, capability_broker | PRESENT | ALREADY_PRESENT | Keep main. Adapted gates. |
| command_center/** (package) | R10 | Command Center + EN/SQ briefings | command_center.py + briefings.py + durable directive consumer | PRESENT with adapted EN/SQ categories and nonce-bound controls | FORWARD_PORT_ADAPTED | Keep canonical command-center path; verify local controls and preserve live voice/provider boundaries as NOT_TESTED. |
| evolution_live/** | R11 | Controlled self-evolution + self-cert refusals | evolution/** + supervisor/evolution_control.py | PRESENT | SUPERSEDED_BY_NEWER_MAIN | Adapted gates (self-cert/promotion refusals, cycles, rollback/restore). |
| fault_campaign/** | R12 | Full fault campaign + freeze/restore | test_crash_recovery, integrated_rollback.py, V8 drill | PRESENT | FORWARD_PORT_ADAPTED | Port R12 fault matrix gate + freeze/restore proof adapted to main. |
| tests/remaster/r7..r12 + scripts | R7-R12 | Certified acceptance contracts/evidence | (main equivalents above) | - | KEEP_TEST_OR_GATE_ONLY / KEEP_EVIDENCE_ONLY | Port adapted gates into tests/convergence/ + scripts/convergence/. |
| R6 trees | R6 | Owner-deferred | - | - | HISTORICAL_ONLY | Never port, never reopen. Preserve in remaster worktree. |

## Port slices (ordered)

1. Slice A - R10 EN/SQ briefings, language toggle, semantic categories, and nonce-bound durable controls: PASS_WITH_BOUNDARY.
2. Slice B - R7-R12 acceptance gates adapted to canonical main APIs: focused gates PASS; external/provider/elapsed boundaries remain explicit.
3. Slice C - R12 fault matrix plus freeze/restore proof adapted to main: bounded campaign PASS; physical/provider rows remain NOT_TESTED/BLOCKED.
4. Slice D - Documentation, dirty-remaster inventory, current-source replay repair, and historical-branch reconciliation: PASS.
5. Verification - current-source full suite 1260/0/0/0 at reproducible snapshot f172147, focused R10 replay repair 36 passed, JSON and documentation checks PASS, fresh Luna audit PASS_WITH_BOUNDARY.
6. Promotion - non-force fast-forward main and rollback tag pushed; runtime proving release remains frozen separately.

## Current convergence evidence

The dirty remaster checkout is preserved unchanged. The exact current
non-ignored inventory contains 87 files and is hashed in
`REMASTER_CURRENT_DIRTY_INVENTORY_R3.json`: 74 are historical R6 material,
4 are convergence evidence, and 9 are post-certification semantic deltas.
The remaster's committed tip is still `6be9185`; no R6 source or evidence is
being reopened or ported.

Adapted gates currently present on the candidate are:

| Slice | Candidate gate/evidence | State |
|---|---|---|
| R7 | `test_r7_portfolio_gate.py`, `test_r7_research_lineage_gate.py`, `R7_RESEARCH_GATE.md` | PASS focused; live provider research/proving window NOT_TESTED |
| R8 | `test_r8_production_gate.py`, `R8_GATE.md` | PASS focused; subprocess-backed multi-input health asset and maintenance repair are independently verified; live external provider readiness NOT_TESTED |
| R9 | `test_r9_capability_gate.py` | PASS focused; financial namespace/descendant boundary is fail-closed and independently verified; real external connector/provider effects NOT_TESTED |
| R10 | `test_r10_command_center_gate.py`, `R10_COMMAND_CENTER_GATE.md`, `briefings_l10n.py` | PASS focused; real provider voice and visual/a11y inspection NOT_TESTED |
| R11 | `test_r11_evolution_gate.py`, `test_r11_authority_regression.py` | PASS focused; exact Supervisor guards cover the full low-level evolution/control closure; real elapsed promotion cycles NOT_TESTED |
| R12 | `test_r12_fault_restore_gate.py`, `R12_FAULT_MATRIX.md`, `R12_FAULT_CAMPAIGN.json` | PASS bounded campaign; 47 rows execute with durable incidents/reconciliation; physical host and unavailable provider rows remain NOT_TESTED/BLOCKED |

The R8 and R9 gates use canonical main's newer worker and capability
architectures; no remaster `orchestration`, `capabilities_live`, or duplicate
runtime was introduced. R12 external/physical failures are explicitly
boundary-only where a safe candidate cannot induce the real provider or host
 condition. Historical candidate-bound suites remain preserved in their
 original evidence. The reproducible current-source snapshot `f172147` has a
 fresh deterministic `1260/0/0/0` full-suite artifact (the earlier 1248-test
 semantic-source artifact remains historical), and the current R10 replay
 repair has an independent 36-test PASS_WITH_BOUNDARY verifier. Live provider
 research, voice/interface inspection, external connector effects, and the
 required elapsed proving window remain honest NOT_TESTED/WAITING boundaries.
 The runtime proving release is still `c60db92`; the previous `f37f852` window
 remains suspended history, so repository promotion has not been silently
 represented as a runtime deployment. The disposable worker-loop deployment
 and restore rehearsal is recorded at
 `WORKER_LOOP_DEPLOYMENT_CANARY_REHEARSAL_20260819.json`; it does not upgrade
 the live schema, worker task, provider, or proving-window claims.
