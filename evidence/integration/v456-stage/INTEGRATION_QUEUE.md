# V4/V5/V6 + Cinematic Integration Queue

Status: IN_PROGRESS
Lane: `integration/v456-cinematic-stage-20260823`

| Order | Unit | Status | Exit evidence | Next action |
|---|---|---|---|---|
| 0 | Current Git, worktree, UI, candidate, and live ownership orientation | PASS | `ORIENTATION.md`, exact refs, doctor and production-status snapshots | Preserve the snapshot and begin source integration |
| 1 | Cinematic frontend discovery | PASS | `CINEMATIC_UI_STATUS=ALREADY_CANONICAL`; commits are ancestors of main | Use current cinematic AppShell and routes as the UI baseline |
| 2 | V4 owner control and briefings | PASS | Merge commit `6b119c3`; 41 V4 Python tests; UI typecheck, lint, 115 unit tests, build, product 212/212, and security PASS; product evidence refresh `4af8dbc` | Keep the V4 projection under review while V5/V6 are added |
| 3 | V5 multi-system intelligence | PASS | Merge commit `7e38aae`; focused connector/effect suite passes, including filesystem, Git, HTTP, browser synthetic, communication contracts, safety, lineage, and composed mission | Keep effect/reconciliation semantics under combined V4/V6 review |
| 4 | V6 continuous self-evolution | IN_PROGRESS | V6 ref, merge base, file/commit inventory, focused suite | Merge after the V5 checkpoint and keep verifier, STOP, veto, and promotion boundaries intact |
| 5 | Cross-lane semantic adaptation | PENDING | V4/V5/V6 lineage, briefing selection, evolution evidence boundaries | Inspect and repair shared runtime, snapshot, mapper, and UI contracts |
| 6 | Combined source gate and adversarial review | PENDING | Fresh Python, runtime, V3, V4, V5, V6, UI, security, migration, fault evidence | Run focused checks first, then the strongest practical repository gate |
| 7 | Canonicalization and push | PENDING | Clean integration head, normal main update, local/origin equality | Update main and push only after source gate PASS |
| 8 | Immutable release and offline validation | PENDING | Exact Git commit/tree, manifest, schema/provenance, previous identity, disposable proof | Build from final canonical main |
| 9 | Live ownership, backup, promotion, and runtime proof | PENDING | Ownership arbitration, backup readability, supported promotion path, live proofs | Re-read live state and resolve the irreversible deployment boundary |
| 10 | Final report | PENDING | `STAGE_GATE.json`, `NO_MISSING_ACCEPTED_WORK.json`, `FINAL_SOURCE_REPORT.md`, live report | Report PASS, FAIL, ROLLED_BACK, or BLOCKED without upgrading missing evidence |

## Unit discipline

Each source unit is merged, checked, repaired if evidence requires it, and
committed before the next unit. Historical PASS claims are provenance only.
No candidate is rebased, reset, globally taken as ours/theirs, or merged into
another candidate worktree.

## Cross-lane data shapes to preserve

- Owner truth: directive -> goal/priority/constraint/veto -> control state.
- Effect truth: mission -> connector -> effect request -> attempt -> external
  object/system -> reconciliation -> terminal effect -> evidence -> outcome.
- Evolution truth: governed evidence -> weakness -> hypothesis -> measurement ->
  experiment -> candidate -> shadow -> benchmark -> independent verdict ->
  eligibility -> learning.
- UI truth: backend snapshot -> source mode -> route mapper -> cinematic view,
  with live, simulated, blocked, degraded, and unknown states distinct.
