# JARVIS V7 Lane B — Capability and Operation Matrix

Readiness is source-plus-proof, not a configured string. `READY` below is
limited to the disposable scope stated; live/provider readiness is never
inferred from a fake or fixture.

| Family | Canonical seam at frozen base | Disposable lane target | Initial state | Boundary / proof needed |
| --- | --- | --- | --- | --- |
| Filesystem | `conn-fs`, `FsConnector`, effect journal | lane-owned scratch root | READY (disposable) | canonical path, hash read-back, rollback |
| Git/project | `v3_git.py`, registration, existing mission contracts | lane-owned repo + bare remote | PROBING | exact worktree/branch/remote identity; `ls-remote` reconciliation |
| Browser | OpenAdapt registry/executor and local oracle | synthetic local browser app | READY (qualified browser only) | structural identity, no coordinate authority, provider truth local-only |
| Native desktop/app | OpenAdapt desktop registration | synthetic local app/window | PROBING | accessibility/process identity; desktop remains unproven |
| HTTP/service | no canonical general-purpose adapter at base | fake/local HTTP service | ABSENT in canonical; lane fixture | idempotency, timeout ambiguity, read-back, outage isolation |
| Deployment/release | `deployment.release`, CURRENT/PREVIOUS helpers | disposable release root/slot | PROBING | artifact identity, health oracle, switch and restore |
| Cloud/service | no verified live provider | fake resource service | BLOCKED live / PROBING fixture | identity, idempotency, quota/cost unknown, compensation |
| Communication | V5 owner-family policy only; no live adapter | fake provider | BLOCKED live / PROBING fixture | exact recipient, transparent JARVIS identity, receipt, duplicate suppression |
| Calendar/schedule | V5 owner-family policy only; no live adapter | fake provider | BLOCKED live / PROBING fixture | exact calendar/attendees, read-back, cancel/duplicate prevention |
| Account/subscription | policy family only | no real account; fake record service | BLOCKED | no owner account use; identity and renewal guard contract only |
| Finance/purchase | broker explicitly denies financial classes | no target | BLOCKED | never spend real money; negative safety proof only |

Cross-cutting requirements for every lane operation: exact target and mission
identity, preconditions, capability state, ActionEnvelope/effect ticket,
idempotency, before-state, structural postcondition oracle, timeout freeze,
provider/system reconciliation, consequence record, and STOP/veto re-check.
