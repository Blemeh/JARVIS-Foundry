# Agent workflow router

Use this directory to select task-specific operating guidance. Root
`../../AGENTS.md` defines the stable control tower; these files define workflow
boundaries without turning historical version plans into universal context.

| Task shape | Read | Default boundary |
| --- | --- | --- |
| Read-only explanation, audit, or diagnosis | `../../AGENTS.md`, `../CURRENT_STATE.md`, task-relevant evidence | Observe and report; do not implement or mutate external/live state. |
| Ordinary implementation or repair | `workflows/build.md` | Isolated candidate; no live mutation. |
| Reconcile multiple accepted lanes | `workflows/convergence.md` | Selective semantic integration; preserve one authority per concern. |
| Release or activate a candidate | `workflows/promotion.md` | Requires explicit active owner promotion/deployment goal. |
| Runtime/endurance/provider evidence | `workflows/proving.md` | Observation-only unless the active proving directive explicitly allows a bounded fault or recovery action. |
| Hermes substrate change | Relevant workflow plus `../../HERMES.md` | Isolated Hermes candidate; never edit the only live copy. |

Always recover current Git and task state before choosing a route. A document
that describes an old phase is evidence/history, not automatic activation of
that workflow.

If a task changes categories, stop at the boundary, record current state, and
apply the newly relevant workflow before acting. Build authorization does not
silently become promotion authorization; proving authorization does not become
repair authorization.
