# V4/V5/V6 + Cinematic Candidate Matrix

Observed after `git fetch --all --prune` on 2026-08-23.

| Input | Local SHA | Remote SHA | Merge base with main | Unique commits | Unique files | Overlap | Status | Integration action |
|---|---|---|---|---:|---:|---|---|---|
| Cinematic UI, accepted current line | `55b2946b70ce002d3786dd80fa9ff2601ef7dad` (`candidate/jarvis-frontend-adaptation`, `integration/cinematic-command-center`) | no remote ref for `integration/cinematic-command-center`; `origin/candidate/jarvis-frontend-adaptation` is the same SHA | `55b2946b70ce002d3786dd80fa9ff2601ef7dad` | 0 | 0 | none against current main | `ALREADY_CANONICAL` | Do not merge. Use current cinematic frontend as the baseline. |
| V4 owner control + briefings | `20b954dc51ab1c3a2f6f259543b65c208dc20f33` | `20b954dc51ab1c3a2f6f259543b65c208dc20f33` | `01db32cbcaa409dfe26386ce66110429c2c0b0e0` | 13 | 47 | no changed-file overlap with V5 or V6; semantic UI/runtime review required | `INTEGRATED` | Merge commit `6b119c3`; UI product evidence refresh `4af8dbc`; retain the current cinematic baseline and continue cross-lane review. |
| V5 multi-system intelligence | `54210d81a77ea80fae55b71bddf77fcb76200f71` | `54210d81a77ea80fae55b71bddf77fcb76200f71` | `01db32cbcaa409dfe26386ce66110429c2c0b0e0` | 29 | 46 | no changed-file overlap with V4 or V6; effect/briefing semantics require review | `UNIQUE_ACCEPTED_WORK` | Merge after V4 and connect governed effect/reconciliation evidence to owner truth. |
| V6 continuous self-evolution | `0e3f93f35131b46273dc36a460ad003a238b35aa` | `0e3f93f35131b46273dc36a460ad003a238b35aa` | `01db32cbcaa409dfe26386ce66110429c2c0b0e0` | 22 | 36 | no changed-file overlap with V4 or V5; evidence-authority boundary requires review | `UNIQUE_ACCEPTED_WORK` | Merge after V5 and feed only durable governed evidence into evolution detection. |

## Topology checks

- `git merge-tree --write-tree main candidate/v4-owner-briefings-20260823`
  returned `372f421e104bfaea6d7545a5d61e84787cdf7c75` with no textual
  conflicts.
- The corresponding V5 and V6 merge-tree checks returned
  `5859fcebeb3b6b1332faabeb686939a7f88d5a50` and
  `bb86b916c35c59ec0e830ab48eea48e29810b474`, also without textual
  conflicts.
- Candidate local and remote refs match for V4, V5, and V6. No UNKNOWN
  candidate classification remains.
