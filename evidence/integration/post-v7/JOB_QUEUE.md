# Post-V7 integration job queue

| Job | Status | Exit evidence |
|---|---|---|
| Orientation and live baseline | PASS | `ORIENTATION.md` |
| Candidate ref and ancestry audit | PASS | `CANDIDATE_PROVENANCE.md` |
| Isolated branch/worktree | PASS | `integration/full-post-v7-20260824` at canonical base |
| Lane 1 account activation | READY | focused factory/tests plus cross-lane broker wiring |
| Lane 2 procedure library | READY | catalog/validator/factory tests plus V7A/V7B wiring |
| Lane 3 unattended harness | READY | focused runner/verdict/checkpoint tests |
| Cross-lane wiring | PENDING | account→capability, procedure→V7A/V7B, V7C/V6/V4, harness observer |
| Combined source gates | PENDING | affected suites, full suite, security, hygiene, docs |
| Canonical promotion | PENDING | normal push and local/origin equality |
| Immutable release and DB rollback | PENDING | manifest, schema, backup, restore, PREVIOUS validation |
| Live safe proof and restart | PENDING | procedure proof, QUICK profile, controlled restart, STOP/resume |
| Final adversarial verdict | PENDING | Critical=0, High=0, machine-readable final report |

The queue is autonomous after each passing gate. A missing credential or
unrun long profile changes only its own classification and does not block
independent source or local/disposable work.
