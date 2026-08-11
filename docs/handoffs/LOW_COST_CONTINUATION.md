# Lower-Cost Model Continuation Contract

This is the canonical handoff after the final bounded high-reasoning review.
Continue the active package exactly as specified. Do not reinterpret the
product, restart planning, redesign the architecture, or request routine
approval.

## Read Order

1. `PROJECT_STATE.md`
2. `IDEA.md`
3. `docs/governance/AUTONOMY_MANDATE.md`
4. `AGENTS.md`
5. `docs/architecture/00_READ_FIRST.md`
6. `docs/architecture/02_ARCHITECTURE_CONTRACT.md`
7. `docs/architecture/03_V1_AUTONOMY_KERNEL_SPEC.md`
8. `docs/architecture/04_V1_ACCEPTANCE_GATE.md`
9. `docs/planning/V1_BUILD_SEQUENCE.md`
10. `docs/planning/CURRENT_WORK_PACKAGE.yaml`
11. `docs/evidence/WP0_BASELINE.md`
12. `docs/evidence/WP1_FOUNDATION.md`
13. `docs/evidence/FINAL_HIGH_REASONING_HANDOFF.md`

Repository truth beats chat summaries. Preserve all user and untracked work.

## Non-Negotiable Product Truth

- JARVIS is an always-active autonomous digital organization, not a chatbot.
- `START BUILD` has already been issued and is a standing mandate.
- Do not ask for per-task, per-package, per-version, or per-resource approval.
- Technical readiness gates are not permission gates.
- Capability readiness is not an authority or permission ladder. It records
  whether an effect can be performed and recovered with evidence.
- PASS advances automatically; FAIL triggers repair; unavailable dependencies
  create a precise resumable blocker while other useful work continues.
- Owner control is opt-out through directives, veto, redirect, pause, and STOP.
- Keep deterministic STOP, evidence, verification, secret brokering, recovery,
  and rollback. These protect autonomy; they do not reduce it.
- STOP detection and freeze must remain independent from Hermes, LLMs, network,
  desktop UI, and the future command center.
- Freeze dispatch and new effects before deciding what to roll back. Never
  start blind mass rollback as the first STOP action.
- Builders do not certify their own consequential work. Independent evidence
  gates determine PASS and progression.
- Existing code, passing unit tests, or a worker completion claim is not by
  itself evidence that a work-package gate passed.
- Do not weaken acceptance criteria to make progress appear green.
- Preserve unrelated user work and all pre-existing changes. Never reset,
  clean, overwrite, or silently absorb them.
- Do not invent work when no valuable action exists; record honest waiting.

## Frozen Architecture Decisions

- Independent Python Supervisor owns durable state, policy, STOP, and recovery.
- Hermes is an execution substrate behind a narrow adapter.
- Obsidian is the evolving knowledge projection, not the transaction database.
- SQLite WAL is the V1 durable store. Do not introduce distributed infrastructure.
- Events are append-only truth; projections are rebuildable and disposable.
- One event and its projection commit in the same transaction.
- Builder and final verifier are independent; prefer provider diversity.
- Agents receive capabilities, not raw credentials.
- `.h` is the canonical clean Hermes development clone on pinned commit
  `244d296646909aca1dd16c9759491da0ef4cd163`; never edit live Hermes directly.
- `/.h = isolated Hermes development checkout`. It is not automatically the V1
  proving project/resource/mission and convenience must not make it one.
- Select the eventual V1 proving mission/resource only by applying
  `04_V1_ACCEPTANCE_GATE.md` and recording the scoring evidence.
- `C:\Users\b\Blendi\JARVIS\V1` is the eventual V1 Obsidian write root.
- Do not install background startup until WP7 activation/recovery tests pass.
- Do not activate V2+ capabilities before V1 PASS queues them automatically.
- After a verified PASS, continue automatically to the next package or version
  unless an active owner veto or STOP conflicts.

## Canonical Package State

```yaml
WP0: PASS
WP1: ACTIVE / NOT_TESTED
WP2: INACTIVE / NOT_TESTED
WP3: INACTIVE / NOT_TESTED
WP4: INACTIVE / NOT_TESTED
WP5: INACTIVE / NOT_TESTED
WP6: INACTIVE / NOT_TESTED
WP7: INACTIVE / NOT_TESTED
WP8: INACTIVE / NOT_TESTED
v1_proving_resource: NOT_SELECTED
v1_proving_mission: NOT_SELECTED
trust_state: LAB
```

Supporting code exists for parts of WP2 and WP3. That code does not activate
those packages and does not constitute their gate evidence.

Use verdicts exactly:

- `PASS`: every mandatory criterion has reproduced evidence.
- `FAIL`: implementation evidence contradicts a mandatory criterion; repair it.
- `BLOCKED`: a genuine unavailable dependency prevents proof; persist the exact resume condition.
- `NOT_TESTED`: the complete gate has not run; make no trust claim.

## Active Work

Active package: WP1.

Continue in this order:

1. Add schema version 3 with durable `agent_runs`, `evidence_bundles`,
   `verification_verdicts`, and `budgets` projections.
2. Add migration-forward and old-database compatibility tests.
3. Add a subprocess crash fixture that dies between transaction statements;
   reopen the database and prove no partial event or projection exists.
4. Add event/projection reconciliation diagnostics with exact mismatch output.
5. Add atomic evidence artifact hashing and storage below project-local `var`.
6. Run the full Foundry tests and selected Hermes baseline tests.
7. Perform an independent review against the architecture contract.
8. Repair every finding, record WP1 PASS evidence, update `PROJECT_STATE.md`,
   and activate WP2 automatically.

WP2 already has tested starter code for Supervisor, leases, STOP, and doctor.
Do not mark WP2 PASS until process termination, worker control, restart,
reconciliation, tray, and hotkey requirements are proven.

Do not perform WP2, WP3, WP7 installation, WP8 proving-project selection, or
V2+ activation while WP1 remains ACTIVE, except for narrowly necessary test
fixtures that cannot be mistaken for package activation.

## Commands

```powershell
Set-Location 'C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry'
$env:PYTHONPATH = (Join-Path $PWD 'src')
& '.venv\Scripts\python.exe' -m pytest tests\foundry -q --tb=short
& '.venv\Scripts\python.exe' -m jarvis_foundry.cli.main --home var\dev doctor
git status --short
```

Hermes baseline overlay:

```powershell
Set-Location '.h'
$env:HERMES_HOME = '..\var\wp0-hermes-home'
$env:PYTHONPATH = $PWD.Path + ';C:\Users\b\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages'
& '..\.test-venv\Scripts\python.exe' -m pytest tests\test_estop.py -q
```

## Current Evidence

- Foundry tests: 21 passed.
- Hermes baseline: 49 passed, 8 skipped.
- Real staging Supervisor and heartbeat: PASS.
- Real staging registered Hermes read action: ALLOWED.
- Staging SQLite quick check: `ok`.
- Background service installation: not yet technically ready.
- Live Hermes and normal Obsidian notes: unchanged by this pass.
- `.h` role: isolated Hermes development checkout only.
- V1 proving mission/resource: not selected.
- WP2-WP8 verdicts: NOT_TESTED.

## Anti-Drift Completion Rule

Never report only “code implemented.” Every package update must state exact
tests, real artifacts, remaining uncertainty, verdict, next autonomous action,
and whether live state changed. A green model claim without reproduced evidence
is not PASS.

## Exact Next Action

After completing the mandatory read order, continue only WP1. Reconcile the
WP1 domain list in `03_V1_AUTONOMY_KERNEL_SPEC.md` against the current schema,
then implement schema version 3 for the missing `agent_runs`,
`evidence_bundles`, `verification_verdicts`, and `budgets` projections with
forward-migration and old-database compatibility tests. Do not start WP2 and
do not redesign the architecture.
