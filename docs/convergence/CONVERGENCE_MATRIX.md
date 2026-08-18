# JARVIS Remaster -> Canonical Convergence Matrix

Program: JARVIS FINAL REMASTER FORWARD-PORT & CANONICAL CONVERGENCE
Canonical base: `2b3573606570f910e5569cf3306223354f93e4ac` (main), Remaster certified tip: `6be9185`, Merge-base: `93a3ddc`.
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
| command_center/** (package) | R10 | Command Center + EN/SQ briefings | command_center.py + briefings.py | PRESENT but NO Albanian/language/category support | FORWARD_PORT_ADAPTED | Port EN/SQ briefing semantics + category distinctions onto main command_center/briefings. |
| evolution_live/** | R11 | Controlled self-evolution + self-cert refusals | evolution/** + supervisor/evolution_control.py | PRESENT | SUPERSEDED_BY_NEWER_MAIN | Adapted gates (self-cert/promotion refusals, cycles, rollback/restore). |
| fault_campaign/** | R12 | Full fault campaign + freeze/restore | test_crash_recovery, integrated_rollback.py, V8 drill | PRESENT | FORWARD_PORT_ADAPTED | Port R12 fault matrix gate + freeze/restore proof adapted to main. |
| tests/remaster/r7..r12 + scripts | R7-R12 | Certified acceptance contracts/evidence | (main equivalents above) | - | KEEP_TEST_OR_GATE_ONLY / KEEP_EVIDENCE_ONLY | Port adapted gates into tests/convergence/ + scripts/convergence/. |
| R6 trees | R6 | Owner-deferred | - | - | HISTORICAL_ONLY | Never port, never reopen. Preserve in remaster worktree. |

## Port slices (ordered)

1. Slice A - R10 EN/SQ briefings + language toggle + briefing semantic categories onto main command_center/briefings (only genuine functional gap).
2. Slice B - R7-R12 acceptance gates adapted to main APIs (fill gaps found by failing gates minimally).
3. Slice C - R12 fault matrix + freeze/restore proof adapted to main (integrated_rollback).
4. Slice D - Docs reconciliation + remaster branch marked CERTIFIED HISTORICAL CANDIDATE / FORWARD-PORTED.
5. Verification: full suite + hybrid regressions + adapted gates + secret scan + independent convergence verifier.
6. Promotion via repository's proven process (no force push, no history rewrite).

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
condition. The candidate-bound canonical suite completes with 1222/1222
tests passing, and the adapted convergence suite completes with 76/76
passing. Independent R3 verifier closure is still pending in the durable state
record. Live provider research, voice/interface inspection, external
connector effects, and the required elapsed proving window remain honest
NOT_TESTED/WAITING boundaries.
