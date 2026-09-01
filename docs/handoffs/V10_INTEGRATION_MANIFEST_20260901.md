# JARVIS V10 Selective Integration Manifest

Status: `SELECTIVELY_RECONCILED` for product convergence. The candidate starts
from `0cc0cbd9572db39f7754cb90de8a3977db829410` in
`C:\temp\JARVIS-V10-Full-Convergence-20260901` on
`candidate/v10-full-convergence-20260901`. Main, origin/main, and live
production were not mutated.

## Input adjudication

| Lane | Source branch and SHA | Decision | Candidate commit / note |
|---|---|---|---|
| A | `candidate/v10-a-web-research-20260901` at `a15bcc75bf5735063833cbe165b238d74a251273` | Select implementation and proof fixes; skip candidate state docs | `afcf3858432e40efb1b70c22a2a94492d743d411` -> `176470a`; `0336df6dd6d7967d773c7305474db865eafd9bf9` -> `39f0879`; `2ce932e88f42b8e0916fde64b2dfbb3cda82845f` -> `1ec9f66` |
| B | `candidate/v10-b-local-documents-20260901` at `a418c775a1e752dc2e2efd6aa42981511518699b` | Select the implementation tip through `dc3a3624c8ebfbeb97da24252b3880fba06f2aae`; supersede the one-line candidate state-doc tip | `b419ea32eedf49bd7a60fc3f8cfb119c1ca56655` -> `6d09150`; `dc3a3624c8ebfbeb97da24252b3880fba06f2aae` -> `7bee753` |
| C | `candidate/v10-c-developer-provider-20260901` at `6df9a850f07979fe2714f67c0c7007d1da2b7e80` | Select both provider/effect commits | `ac5b91f2fbc82fe64bd9b5c1df5617139260a0da` -> `890d021`; `6df9a850f07979fe2714f67c0c7007d1da2b7e80` -> `8a8d8c2` |
| D | Completed shared-contract/composition work was discovered on `candidate/v9-capability-fabric-20260830` | Reuse existing implementation; do not replay obsolete history | implementation checkpoint `6e770f1e78f3fcc5fc0ac6bce45d8d02de3ed995`; terminal audit tip `f21a8fe337089afb12483818bfd77777e1c695a3` |

## D resolution

The D composition commit `3177a6683a180176b7d38283a588afd78c2d6c3d` is
already represented on canonical main by cherry-pick `142ccdd530a3706e869227291c68334a4fd7f26f`.
The shared Fabric, effects, MissionLoop, verifier binding, and WorldModel files
were byte-identical between the D worktree and the canonical base at the
inspection point. No second scheduler, loop, broker, registry, or effect
journal was imported.

The following were inspected and intentionally skipped as non-source inputs:

- `candidate/v10-d-historical-reuse-20260901` at
  `96d776dddc039e4d9d3baab8c119f341d5f1679d` — archaeology/documentation only.
- `candidate/v10-c-workflow-audit-20260901` at
  `39edae74e7d2871b88b44facebf6c0b9ce3fafa9` — architecture audit/documentation only.
- A, B, and D candidate state-doc commits — superseded by the final V10 state
  and handoff pin.

## Convergence commits

The selected implementation history is linear and inspectable:

```text
176470a  A browser/research adapters
39f0879  A real journey/recovery tests
1ec9f66  A bounded capture-size fix
6d09150  B local artifact adapters
7bee753  B capability-fabric wiring
890d021  C developer/provider seams
8a8d8c2  C GitHub write lifecycle reuse
87ddd17  V10 contract/effect/composition convergence
1428613  documentation-authority allowlist for the V10 handoff surfaces
```

`uv.lock` was reconciled once after the lane lockfile overlap. The final lock
resolves 37 packages and contains only the accepted dependency set recorded in
the V10 handoff. No optional desktop/browser fleet was added.

## Boundaries preserved

- Fabric owns descriptor metadata, readiness, and capability selection.
- `effects.py` owns effect classes, stable effect identity, and reconciliation.
- MissionLoop/evidence/verifier remain the durable progression and acceptance
  authority; the V10 contract is a typed sidecar at that boundary.
- Owner STOP/veto remains authoritative. A worker/provider acknowledgement is
  never a PASS.
- Ambiguous external outcomes remain `UNKNOWN`/`FROZEN` until reconciliation.
- Only independently verified observations enter `$step.field` composition or
  WorldModel terminal writeback.
- All real artifacts and disposable Git operations stay outside live/main and
  origin/main.
