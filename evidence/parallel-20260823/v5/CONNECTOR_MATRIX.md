# V5 Connector Matrix at Lane Start

The matrix separates source readiness from live/provider truth. A local fake or
fixture proves contract behavior only. It never upgrades a real provider to
READY.

| Connector | Identity and actions | Semantics | Readiness | Health/quota/cost | Credentials | Idempotency | Reconciliation | STOP/veto | Isolation | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|
| `conn-fs` | `local-fs`; file.read/write/append/delete | read/write/execute | READY on owned disposable root | HEALTHY probe; quota recorded only when sampled; cost unknown | none | effect key binds system, target, payload | content hash read-back; provider truth NOT_TESTED | broker ticket plus driver re-check | per-connector failures stay local | S8 source/tests; fresh focused baseline |
| Git local remote | worktree and lane-owned bare remote; inspect/push/pull in `v3_git.py` | read/write/external effect | PROBING as common connector; disposable adapter exists | no common registry health/cost surface yet | none for local remote | action id plus effect journal | `git ls-remote` provider-side ref truth | adapter checks STOP; common contract pending | local remote failure must not block other connectors | `test_v3_git_reconcile.py`, S6 evidence |
| HTTP/service | no owned adapter at lane start | absent | ABSENT | absent | absent | absent | absent | absent | not applicable | gap for V5-7 |
| Browser/procedure | bounded local browser substrate, not generic connector | execute/effect boundary only | PROBING and disabled by default | no generic health/quota/cost surface | no live credentials | procedure/effect keys where used | local oracle only; provider truth NOT_TESTED | existing ActionEnvelope/effect gates | connector boundary not yet normalized | Phase 4 evidence, S8 blocker |
| Email/messaging/calendar/cloud | no source adapter | communicate/external effect contract absent | BLOCKED | unknown | no credentials | contract not yet implemented | provider truth NOT_TESTED | no activation path | must remain isolated and unactivated | durable S8 blockers |

The immediate normalization slice must preserve these classifications while
making the shared contract executable for disposable adapters.
