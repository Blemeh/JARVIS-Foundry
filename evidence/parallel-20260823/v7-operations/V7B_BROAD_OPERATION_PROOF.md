# V7 Lane B — Broad-Operation Proof

Date: 2026-08-23
Frozen base: `6db467a2b0e6bdc37adbc4cbfac309418e33afec`
Candidate: `candidate/v7-broad-operations-20260823`
Worktree: `C:\temp\JARVIS-V7-Broad-Operations`

## Source additions

- `operations/procedure.py`: hash-bound, event-ledger-backed procedure state;
  ordered steps, effect tickets, before/after evidence, postcondition oracles,
  compensation, restart freeze, provider reconciliation, and terminal outcome.
- `operations/targets.py`: exact process/executable/window/document identity;
  structural desktop/browser controls; duplicate-label, hidden-control, modal,
  stale-revision, delayed-load, and redirect fail-closed behavior.
- `operations/project.py`: disposable issue/spec → edit → test → commit →
  local bare remote push → `ls-remote` reconciliation with resume identity.
- `operations/deployment.py`: immutable disposable artifacts with staged
  health checks and `CURRENT`/`PREVIOUS` switch/rollback semantics.
- `operations/services.py`: offline HTTP, resource, communication, and
  calendar contracts with exact identities, idempotency, quota/429, cost
  unknown, timeout-after-commit, read-back, duplicate suppression, withdraw,
  update, and cancel behavior.
- `operations/planning.py`: before-state, intended effects, irreversible
  boundary, checkpoints, rollback options, evidence, abort/STOP gate.
- `operations/composition.py`: one mission/procedure/correlation lineage and a
  disposable STOP/veto gate.
- `operations/reuse.py`: reusable procedure templates only from a completed,
  evidenced procedure; explicit parameters and version/hash binding.
- `operations/adversarial.py`: external content remains `DATA_ONLY` and never
  becomes owner authority, a directive, a path, a command, a payment, a goal,
  or a STOP override.
- `operations/lab.py`: one disposable digital-office mission spanning
  filesystem, project, Git, HTTP, cloud, deployment, browser, communication,
  and calendar.

## Focused disposable proof

Focused V7B suites pass (`29 passed`):

- procedure lifecycle, restart freeze, reconciliation, compensation/reopen;
- process/window/document and accessibility target identity;
- structural browser and synthetic desktop actions;
- disposable project and local bare remote, including resume;
- deployment health failure and rollback;
- service timeout/quota/idempotency;
- communication transparent identity, timeout read-back, duplicate suppression,
  and withdraw;
- calendar exact identity, availability, duplicate prevention, update/cancel;
- reversible planner checkpoint/STOP/irreversible boundary;
- mission lineage, completed-only procedure reuse, hostile content;
- digital-office end-to-end lab.

The repository `tests/foundry` regression run completed at `100%` with exit
code 0 after the V7B additions. No duplicate full-suite run was started.

## Digital-office lab result

The lab's report is produced from a temporary SQLite ledger and disposable
filesystem/Git/fake-provider roots. It records:

- a recoverable failed test edit followed by explicit compensation and retry;
- a cloud mutation whose response was lost, frozen as UNKNOWN, and resolved by
  provider read-back;
- a deployment STOP/veto check before the switch boundary;
- structural browser actions followed by restart freeze and read-back;
- a communication post-send ambiguity resolved by fake-provider read-back;
- local Git push/read-back, disposable deployment, fake HTTP health, exact fake
  calendar, and transparent fake communication;
- hostile inbound content preserved as data and a reusable template registered
  only after terminal completion.

## Boundary

All proofs are source plus disposable fixtures. This lane used no real
credentials, owner accounts, live messages, purchases, financial actions,
cloud/production mutations, Task Scheduler edits, Supervisor restart, or live
database writes. The live runtime observations in `INITIAL_STATE.md` remain
read-only and are not promoted to capability proof.
