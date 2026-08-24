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
| V7B-3 | Deterministic application/window/document target identity | COMPLETE (focused proof) |
| V7B-4 | Structural desktop/browser actions against synthetic apps | COMPLETE (focused proof) |
| V7B-5 | Disposable inspect → edit → test → commit → push workflow | COMPLETE (local bare remote + resume proof) |
| V7B-6 | Disposable build/stage/health/switch/rollback deployment | COMPLETE (health failure + rollback proof) |
| V7B-7 | Fake cloud/service resource contract and reconciliation | COMPLETE (timeout/quota/idempotency proof) |
| V7B-8 | Fake communication representation/send/reconcile workflow | COMPLETE (transparent identity + withdraw proof) |
| V7B-9 | Fake calendar create/update/cancel/reconcile workflow | COMPLETE (exact identity + duplicate proof) |
| V7B-10 | Generic reversible transaction planner | COMPLETE (checkpoint/STOP/irreversible proof) |
| V7B-11 | Timeout/partial-effect ambiguity resolution | COMPLETE (freeze/read-back, no blind retry) |
| V7B-12 | Multi-capability mission composition | COMPLETE (shared mission lineage) |
| V7B-13 | Verified procedure learning/reuse templates | COMPLETE (completed-only registration) |
| V7B-14 | Adversarial external-content containment | COMPLETE (data-only hostile payload proof) |
| V7B-15 | Disposable digital-office E2E lab across at least five capabilities | COMPLETE (local PASS) |
| V7B-16+ | Next real broad-operation gap after the lab | QUEUED |

Protected controls: no real credentials, owner accounts, purchases,
financial actions, live messages, production/cloud mutation, canonical-main
edits, Supervisor restart, or Task Scheduler changes.
