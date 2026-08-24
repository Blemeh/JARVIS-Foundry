# JARVIS V7 Lane B — Capability and Operation Matrix

Readiness is source-plus-proof, not a configured string. `READY` below is
limited to the disposable scope stated; live/provider readiness is never
inferred from a fake or fixture.

| Family | Canonical seam at frozen base | Disposable lane target | Initial state | Boundary / proof needed |
| --- | --- | --- | --- | --- |
| Filesystem | `conn-fs`, `FsConnector`, effect journal | lane-owned scratch root | READY (disposable) | canonical path, hash read-back, rollback |
| Git/project | `v3_git.py`, registration, existing mission contracts | lane-owned repo + bare remote | READY (disposable) | exact worktree/branch/remote identity; `ls-remote` reconciliation; resume |
| Browser | OpenAdapt registry/executor and local oracle | synthetic local browser app | READY (disposable structural proof) | DOM/control identity, revision/modal/redirect gates, no coordinate authority |
| Native desktop/app | OpenAdapt desktop registration | synthetic local app/window | READY (synthetic structural proof) / live PROBING | accessibility/process identity; no live desktop claim |
| HTTP/service | no canonical general-purpose adapter at base | fake/local HTTP service | READY (fixture) / ABSENT live | exact URL, health oracle, timeout boundary |
| Deployment/release | `deployment.release`, CURRENT/PREVIOUS helpers | disposable release root/slot | READY (disposable) | artifact identity, health oracle, switch and restore |
| Cloud/service | no verified live provider | fake resource service | READY (fixture) / BLOCKED live | identity, idempotency, quota/cost unknown, timeout read-back, compensation |
| Communication | V5 owner-family policy only; no live adapter | fake provider | READY (fixture) / BLOCKED live | exact recipient, transparent JARVIS identity, receipt, duplicate suppression, withdraw |
| Calendar/schedule | V5 owner-family policy only; no live adapter | fake provider | READY (fixture) / BLOCKED live | exact calendar/attendees, read-back, cancel/duplicate prevention |
| Account/subscription | policy family only | no real account; fake record service | BLOCKED | no owner account use; identity and renewal guard contract only |
| Finance/purchase | broker explicitly denies financial classes | no target | BLOCKED | never spend real money; negative safety proof only |

Cross-cutting requirements for every lane operation: exact target and mission
identity, preconditions, capability state, ActionEnvelope/effect ticket,
idempotency, before-state, structural postcondition oracle, timeout freeze,
provider/system reconciliation, consequence record, and STOP/veto re-check.
