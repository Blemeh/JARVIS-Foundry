# JARVIS V7 Lane B — Job Queue

Queue owner: `candidate/v7-broad-operations-20260823`
Frozen base: `6db467a2b0e6bdc37adbc4cbfac309418e33afec`

Each job requires focused tests, adversarial/failure cases, disposable proof,
secret/hygiene review, a scoped commit, and an evidence update. A green test
suite closes only the current job; the next safe job remains queued.

| Job | Scope | Initial state |
| --- | --- | --- |
| V7B-1 | Capability map across filesystem, Git, browser/desktop, HTTP, communication/calendar, cloud/deploy, account, finance | COMPLETE (source/runtime baseline recorded) |
| V7B-2 | Durable resumable procedure/transaction model | COMPLETE (focused tests pass) |
| V7B-3 | Deterministic application/window/document target identity | QUEUED |
| V7B-4 | Structural desktop/browser actions against synthetic apps | QUEUED |
| V7B-5 | Disposable inspect → edit → test → commit → push workflow | QUEUED |
| V7B-6 | Disposable build/stage/health/switch/rollback deployment | QUEUED |
| V7B-7 | Fake cloud/service resource contract and reconciliation | QUEUED |
| V7B-8 | Fake communication representation/send/reconcile workflow | QUEUED |
| V7B-9 | Fake calendar create/update/cancel/reconcile workflow | QUEUED |
| V7B-10 | Generic reversible transaction planner | QUEUED |
| V7B-11 | Timeout/partial-effect ambiguity resolution | QUEUED |
| V7B-12 | Multi-capability mission composition | QUEUED |
| V7B-13 | Verified procedure learning/reuse templates | QUEUED |
| V7B-14 | Adversarial external-content containment | QUEUED |
| V7B-15 | Disposable digital-office E2E lab across at least five capabilities | QUEUED |
| V7B-16+ | Next real broad-operation gap after the lab | QUEUED |

Protected controls: no real credentials, owner accounts, purchases,
financial actions, live messages, production/cloud mutation, canonical-main
edits, Supervisor restart, or Task Scheduler changes.
