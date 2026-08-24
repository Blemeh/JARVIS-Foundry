# Post-V7 Skill Library. Catalog Seed

This file records the catalog contract and the intended high-value repertoire.
The proof harness writes the observed status, evidence hashes, and verifier
result after each bounded run. A source definition is never trusted because a
model wrote it.

## Required contract

Every entry has a stable `procedure_id`, name, domain, semantic version,
parameters, preconditions, capability requirements, risk and reversibility,
ordered typed steps, success oracle, evidence outputs, known failure modes,
repair/retry policy, STOP/veto behavior, timeout and budget, proof status,
verification date, supersession lineage, dependency list, and an executable
runner identity.

## Intended entries

| Procedure ID | Domain | Initial status | Runner |
|---|---|---|---|
| `jarvis.software-engineering` | software engineering | CANDIDATE | software-engineering |
| `jarvis.bug-triage` | bug triage/root cause | CANDIDATE | bug-triage |
| `jarvis.flake-diagnosis` | test-flake diagnosis | CANDIDATE | flake-diagnosis |
| `jarvis.repository-maintenance` | repository maintenance | CANDIDATE | repository-maintenance |
| `jarvis.dependency-update` | dependency updates | CANDIDATE | dependency-update |
| `jarvis.documentation-staleness` | documentation staleness | CANDIDATE | documentation-staleness |
| `jarvis.release-preparation` | release preparation | CANDIDATE | release-preparation |
| `jarvis.research-synthesis` | research/evidence synthesis | CANDIDATE | research-synthesis |
| `jarvis.knowledge-revalidation` | knowledge revalidation | CANDIDATE | knowledge-revalidation |
| `jarvis.worktree-archaeology` | branch/worktree archaeology | CANDIDATE | worktree-archaeology |
| `jarvis.system-diagnostics` | local diagnostics | CANDIDATE | system-diagnostics |
| `jarvis.system-repair` | bounded system repair | CANDIDATE | system-repair |
| `jarvis.incident-triage` | incident triage | CANDIDATE | incident-triage |
| `jarvis.project-health` | project health | CANDIDATE | project-health |
| `jarvis.safe-local-deployment` | safe local deployment | CANDIDATE | safe-local-deployment |
| `jarvis.communication-draft` | communication draft | CANDIDATE | communication-draft |
| `jarvis.calendar-planning` | calendar planning | CANDIDATE | calendar-planning |
| `jarvis.inbox-triage` | inbox triage | CANDIDATE | inbox-triage |
| `jarvis.research-build-verify` | composite workflow | CANDIDATE | research-build-verify |
| `jarvis.failure-repair-factory` | failure/repair factory | CANDIDATE | failure-repair-factory |
| `jarvis.versioning-supersession` | template versioning | CANDIDATE | versioning-supersession |
| `jarvis.quality-ranking` | quality ranking | CANDIDATE | quality-ranking |

`CANDIDATE` is the source state before the disposable factory runs. The
promotion operation can only move an entry to `PROVEN_ONCE` or `TRUSTED` when
the verifier has a fresh, hash-bound result. `REJECTED`, `PARTIAL`, and
`DUPLICATE` remain selectable only for audit and supersession history.

## Factory result

The fresh disposable run is recorded in
`LIBRARY_PROOF_REPORT.json`. It exercised 22/22 entries, produced 22/22
independent structured verifier `PASS` results, and promoted every entry to
`PROVEN_ONCE` at version `1.0.0`. No entry is called `TRUSTED` until a later
reconciled run supplies the stronger repeated-observation policy.

| Procedure | Domain | Proof | Version |
|---|---|---|---|
| `jarvis.software-engineering` | software engineering | PROVEN_ONCE | 1.0.0 |
| `jarvis.bug-triage` | bug triage/root cause | PROVEN_ONCE | 1.0.0 |
| `jarvis.flake-diagnosis` | test-flake diagnosis | PROVEN_ONCE | 1.0.0 |
| `jarvis.repository-maintenance` | repository maintenance | PROVEN_ONCE | 1.0.0 |
| `jarvis.dependency-update` | dependency updates | PROVEN_ONCE | 1.0.0 |
| `jarvis.documentation-staleness` | documentation staleness | PROVEN_ONCE | 1.0.0 |
| `jarvis.release-preparation` | release preparation | PROVEN_ONCE | 1.0.0 |
| `jarvis.research-synthesis` | research/evidence synthesis | PROVEN_ONCE | 1.0.0 |
| `jarvis.knowledge-revalidation` | knowledge revalidation | PROVEN_ONCE | 1.0.0 |
| `jarvis.worktree-archaeology` | branch/worktree archaeology | PROVEN_ONCE | 1.0.0 |
| `jarvis.system-diagnostics` | local diagnostics | PROVEN_ONCE | 1.0.0 |
| `jarvis.system-repair` | bounded system repair | PROVEN_ONCE | 1.0.0 |
| `jarvis.incident-triage` | incident triage | PROVEN_ONCE | 1.0.0 |
| `jarvis.project-health` | project health | PROVEN_ONCE | 1.0.0 |
| `jarvis.safe-local-deployment` | safe local deployment | PROVEN_ONCE | 1.0.0 |
| `jarvis.communication-draft` | communication draft | PROVEN_ONCE | 1.0.0 |
| `jarvis.calendar-planning` | calendar planning | PROVEN_ONCE | 1.0.0 |
| `jarvis.inbox-triage` | inbox triage | PROVEN_ONCE | 1.0.0 |
| `jarvis.research-build-verify` | composite research/build/verify | PROVEN_ONCE | 1.0.0 |
| `jarvis.failure-repair-factory` | failure and repair | PROVEN_ONCE | 1.0.0 |
| `jarvis.versioning-supersession` | versioning and supersession | PROVEN_ONCE | 1.0.0 |
| `jarvis.quality-ranking` | observed quality ranking | PROVEN_ONCE | 1.0.0 |
