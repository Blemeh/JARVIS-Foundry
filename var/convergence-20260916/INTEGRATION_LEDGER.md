# V14/V15/V16 semantic integration ledger

Base: `4b2ae28da582b369982661c2a1d2b1792e2f48cb`  
Branch: `candidate/v14-v16-max-semantic-convergence-20260916`  
Worktree: `C:\temp\JARVIS\v14-v16-max-semantic-convergence-20260916`  
Boundary: source candidate only; live JARVIS, main, origin/main, release pointers, Scheduler, providers, and trust are not touched.

All six supplied branch tips exist locally, exactly match the owner-supplied heads, and descend from the exact base. Their committed handoffs and full base-to-head changed-file surfaces were inspected before porting.

## Owner directive versus historical roadmap

The repository execution pointer still names V11 A3/A4 and frozen-roadmap decision D10 puts voice/Albanian after 1.0. The current authenticated owner goal explicitly directs isolated V14/V15/V16 source convergence and states that Voice/Albanian/English are part of V15 completion. That new directive governs this candidate. It does not authorize a gate transition, promotion, installation, or live activation, so the historical execution-state file remains untouched.

## Intended combined central semantics

- `fabric/loop.py`: retain one Catalog/MissionLoop; combine browser grant-gated inspect/execute, Git/GitHub reconciliation actions, `workflow.run`, strict nested receipt verification, and all prior capabilities. STOP may allow only explicitly reconciliation-only verbs.
- `fabric/executors.py`: retain one `ExecutorContext` and one handler map. It must carry browser authorization/reauth, GitHub read/write transport, nested workflow execution, desktop/general-work state, and prior context simultaneously. Missing authorization stays blocked.
- `fabric/adapter.py`: retain canonical context construction and default-off GitHub dispatch. Browser authorization will be injected from an existing owner/config/activation seam, never fabricated by mission payload.
- `fabric/model.py` and `fabric/__init__.py`: add Lane 4 recipe/failure-policy API without changing canonical effect identity or evidence authority.
- `command_center.py`: remain a projection and canonical owner-control transport. Voice text, preferences, and toast delivery adapt into `OwnerControlService` and the existing decision queue; Emergency STOP remains independent.
- `runtime/worker_loop.py` and `executive/*`: campaign capacity and workflow invocation are additive recovery/dispatch behavior inside the existing Executive/MissionLoop; no second scheduler or campaign authority.

## Classification summary before port

The machine-readable ledger is authoritative for every row. Counts: 15 `PORT_EXACT`, 5 `PORT_SEMANTIC`, 1 `ALREADY_REPRESENTED`, 3 `SUPERSEDED_BY_COMBINED_IMPLEMENTATION`, 3 `EVIDENCE_ONLY`, 3 `DEFER_LIVE_ONLY`, 1 `REJECT_WITH_REASON`, and 1 `BLOCKED`.

No lane is accepted by commit count. Source behavior, effect identity, owner authority, verifier truth, STOP semantics, and recovery durability determine acceptance.
