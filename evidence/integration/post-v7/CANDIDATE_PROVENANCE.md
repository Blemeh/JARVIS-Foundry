# Post-V7 candidate provenance

All candidate refs were checked after `git fetch --all --prune`. Local and
remote refs resolve to the accepted SHAs below. All three candidates share the
frozen base `107827d81eaee514edc66eab98390438b63c1650`; their merge-base with
the verified canonical `main` is also that frozen base. Three-way merge-tree
checks reported no conflicts or changed-in-both markers.

| Lane | Ref | Accepted SHA | Commits after frozen base | Candidate-owned paths |
|---|---|---|---:|---:|
| 1 Account activation | `candidate/postv7-account-activation-20260824` | `573bf2a20b4213eb830b10f552e8b46d901c8d92` | 2 | 7 |
| 2 Procedure library | `candidate/postv7-skill-library-20260824` | `44488776dc4ebf1b475e608758c973c70c6be460` | 2 | 19 |
| 3 Unattended harness | `candidate/postv7-unattended-harness-20260824` | `6c61210802df23d522a5cd8b0e2d3665e4e3caac` | 3 | 90 |

## Lane 1

The source adds `src/jarvis_foundry/connectors/account_activation.py`, its
factory script and focused tests. It defines opaque credential references,
explicit account identity, scope discovery, read-only readiness probes,
activation/deactivation, reconciliation and account events. Its evidence and
factory report Gmail, Calendar, GitHub, and Browser as source-ready while Drive
is deferred. The module consumes canonical database/event and connector
registry surfaces, but it does not yet call the canonical capability broker or
procedure eligibility path. That is a mandatory cross-lane wiring item.

## Lane 2

The source adds the `procedure_library` package, a probe, and focused tests.
It represents 22 reusable procedures with parameters, preconditions, oracles,
evidence, validation, repair, supersession, and maturity. Candidate evidence
reports 22 `PROVEN_ONCE` procedures and `TRUSTED=0`. The package uses canonical
events and disposable operation/sandbox seams but does not itself register
with V7A portfolio selection or V7B execution. Integration must preserve
`PROVEN_ONCE != TRUSTED` and wire selection through the existing authority.

## Lane 3

The source adds the typed acceptance harness, scenario/profile configuration,
runner script, tests, and machine-readable evidence. It supports real-time and
accelerated clocks, checkpoint/resume, invariant sampling, bounded faults,
false-PASS checks, resources, incidents, and verdict bundles. It is explicitly
an observer and test orchestrator, not runtime authority. Integrated proof must
observe canonical portfolio, procedure, account, V7B/V7C, effect, briefing,
WAIT/wake, and continuation facts. Accelerated logical hours are never genuine
endurance.

## Overlap and interface impact

The candidate-owned base-to-tip path sets are disjoint. The harness changes
`pyproject.toml` and `scripts/README.md`; the account and procedure lanes do
not edit those files. The current canonical tree advanced after the frozen
base in V7 command-center, executive-portfolio, and portfolio-bridge paths.
Those are semantic interface overlaps, not same-side Git conflicts. The plan
therefore integrates the source packages first, then adds adapters at the
canonical portfolio/capability/effect/verifier/incident seams.

## Required provenance preservation

Candidate evidence remains historical input. It does not certify the
integrated tree or the live release. Every integrated checkpoint will record
the exact source SHA, tree, focused test result, independent verdict, and
remaining boundary. No candidate branch is rebased or rewritten.
