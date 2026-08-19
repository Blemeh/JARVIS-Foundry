# Current Canonical Semantic Convergence Matrix

Program: JARVIS FINAL REMASTER FORWARD-PORT & CANONICAL CONVERGENCE.
Current canonical repository head: `e99269562dfa63be3075e9e2a60842ea020d5a83` (docs-only promoted descendant).
Tested source base: `84a22b4186aa0c4ba98bfaeed177993cfcc85576`.
Certified remaster tip: `6be9185ea34a64a84737dbd9ec323e25776c4558`.
Merge-base: `93a3ddc0965dfaf3e4cb9dc0952467bd5250520a`.

This is a semantic forward-port. Canonical `main` is authoritative. No
wholesale remaster merge and no blind cherry-pick was performed.

## Architectural Dispositions

| Remaster capability | Stage | Current canonical surface | Disposition | Proof obligation |
|---|---|---|---|---|
| `kernel/**`, `kernel/portfolio/**` | R4/R7 | `database.py`, `evolution/portfolio.py`, discovery, mission loop | `SUPERSEDED_BY_NEWER_MAIN` | R7 gates and architecture invariants |
| portfolio, scoring, selection, honest `WAITING` | R7 | `evolution/portfolio.py`, `mission/loop.py` | `ALREADY_PRESENT` | `test_r7_portfolio_gate.py` |
| research, provenance, contradiction, deduplication, monitoring | R7 | discovery, research queue, knowledge, monitor surfaces | `ALREADY_PRESENT` | `test_r7_research_lineage_gate.py` |
| `orchestration/**` production organization | R8 | Hybrid Workers, `runtime/worker_loop.py`, mission loop, deployment canary | `SUPERSEDED_BY_NEWER_MAIN` | `test_r8_production_gate.py` plus disposable canary |
| capability lifecycle and digital effects | R9 | capabilities, connectors, capability broker | `ALREADY_PRESENT` | `test_r9_capability_gate.py` |
| `capabilities_live/**` | R9 | canonical capability/connector surfaces | `SUPERSEDED_BY_NEWER_MAIN` | fail-closed and effect-boundary gates |
| Command Center, durable controls, EN/SQ categories | R10 | `command_center.py`, briefings, `convergence/briefings_l10n.py`, nonce-bound directives | `FORWARD_PORT_ADAPTED` / already present | R10 gates and local UI/control evidence |
| `command_center/**` remaster package | R10 | canonical command-center module and durable directive system | `SUPERSEDED_BY_NEWER_MAIN` | no duplicate UI/control path |
| `evolution_live/**` self-evolution lifecycle | R11 | `evolution/**`, Supervisor authority and evolution control | `SUPERSEDED_BY_NEWER_MAIN` | authority regression and evolution gates |
| `fault_campaign/**` and freeze/restore | R12 | `integrated_rollback.py`, rollback drills, adapted fault gate | `FORWARD_PORT_ADAPTED` | bounded campaign and restore proof |
| remaster R7-R12 tests/scripts/evidence | R7-R12 | `tests/convergence/**`, `docs/convergence/**` | `KEEP_TEST_OR_GATE_ONLY` / `KEEP_EVIDENCE_ONLY` | fresh current-head reruns required |
| R6 trees and evidence | R6 | none; preserved in remaster | `HISTORICAL_ONLY` | owner redirect remains binding |

## Dirty Remaster Dispositions

The verified dirty inventory contains 87 files: 74 historical R6 files, four
convergence evidence/gate files, and nine post-certification semantic deltas.
All 87 current hashes match the recorded inventory. The nine post-certification
source edits are superseded by newer canonical Supervisor authority and
financial-family guards already present on `main`; their tests remain proof
obligations, not source to copy. No dirty remaster file is promoted wholesale.

| Dirty group | Count | Disposition |
|---|---:|---|
| `docs/remaster/evidence/r6/**`, `scripts/remaster/r6/**`, `tests/remaster/r6/**`, R6 work package | 74 | `HISTORICAL_ONLY`; untouched |
| `docs/convergence/R8_GATE.md`, convergence R8/R9/R11 tests | 4 | `KEEP_TEST_OR_GATE_ONLY`; rerun against current main |
| `control/broker.py` | 1 | `SUPERSEDED_BY_NEWER_MAIN`; current financial namespace guard is stronger/different |
| `evolution/builder.py`, `lab.py`, `registry.py` | 3 | `SUPERSEDED_BY_NEWER_MAIN`; current shared Supervisor authority is canonical |
| five `tests/foundry/test_evolution_*.py` edits | 5 | `KEEP_TEST_OR_GATE_ONLY`; current authority/evolution suites cover the invariant |

## Invariants Preserved

One mission/runtime truth, one Hybrid Worker execution architecture, one
durable STOP/control path, one Supervisor-owned evolution authority, financial
effects denied, evidence and rollback retained, owner work preserved, Eyes
untouched, and `OWNER_REDIRECT_R6_DEFERRED` unchanged.

## Current Boundary

The current source can prove bounded local semantics and disposable recovery.
Live schema migration, worker-loop activation, external provider/connectors,
live voice/provider and production-interface inspection, and real elapsed proving remain honest
`NOT_TESTED`, `WAITING`, or `BLOCKED` boundaries until independently reproduced
against the exact promoted release.
