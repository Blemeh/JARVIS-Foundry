# JARVIS Full Product Audit and Recovery — 2026-08-25

## Verdict discipline

This audit separates canonical source, isolated execution, installed release,
and real-provider execution. A green test or Lab proof is not a live-provider
PASS. `SERVES_ORIGINAL_PURPOSE` is forbidden unless the installed system proves
the complete owner-to-outcome loop with a real configured worker provider.

## Reconstructed baseline

- Canonical `main` and `origin/main`: `8dfb981876985525621cf10b8b9b67e34468b450`.
- Installed `CURRENT`: the same source SHA; installed schema: 37.
- The canonical checkout contained unrelated active UI work and was preserved
  untouched. Recovery work used one clean candidate from the fetched main SHA.
- The installed Supervisor and Scheduler were alive, but the Scheduler launched
  only the Supervisor plus one-shot worker loop; it did not own Command Center.
- Installed routing enabled `lab_scripted_hermes` while real Hermes and
  OpenHands were disabled. The resulting completed missions were scripted Lab
  executions, not real provider work.
- Machine provider identity was `opencode_go` / `deepseek-v4-flash`, but no
  authorized API key was configured. A bounded real Hermes probe failed closed
  before provider dispatch. This is an external credential boundary.
- Port 8765 served the legacy Python projection from a developer process. The
  React `RealJarvisRuntimeAdapter` bundle was not the installed UI.
- The owner command endpoint returned 503 because owner control was not
  configured. Therefore the real owner-facing product path failed before
  mission admission.

## Root-cause call graph

The broken path was:

```text
Command Center POST /api/owner-command
→ owner control unavailable (503)

or, in source with a secret configured:

authenticated GOAL directive
→ durable goal projection
→ no owner-goal work item
→ no mission
→ executive WAIT
```

Additional breaks occurred after admission:

- Supervisor did not own the HTTP/UI lifecycle, so runtime and UI could drift.
- The Supervisor recovery pass could race a live builder-to-verifier handoff,
  mistake the lease gap for a crash, and corrupt mission transitions.
- Hybrid routing advertised fallbacks but did not execute them.
- Verifier FAIL findings were not included in automatic repair context.
- STOP during `ACCEPTING` used an undefined run identity and returned no outcome.
- Nested untracked Git files collapsed to directories, producing false evidence
  mismatch.
- Portfolio outcomes were recorded after every passed step rather than only the
  terminal mission outcome.
- The React client silently defaulted to simulated mode, invented timeline
  events, treated non-fail lineage as PASS, cached stale snapshots, hid errors,
  and exposed notification/gate mutations that had no real backend authority.
- Tracked worker routing claimed stale machine-specific `ACTIVE`/`READY` paths.

## Recovery design

The repair extends the existing authorities rather than introducing a second
mission, executive, STOP, or ledger plane:

```text
Supervisor-owned Command Center
→ authenticated owner GOAL
→ canonical portfolio OWNER_GOAL item
→ canonical selection and mission materialization
→ ordered durable plan and execute contracts
→ Supervisor-owned one-shot worker loop
→ RoleDispatcher and HybridWorkerRoleAdapter
→ evidence reconciliation
→ independent verifier
→ FAIL findings in automatic repair context
→ fresh repair and fresh verifier
→ terminal outcome recording
→ useful WAIT
```

The immutable release builder now compiles the tracked React Command Center at
the exact release commit and records/verifies a bundle digest. The Supervisor
serves that bundle and API from one localhost process with a stable machine-local
owner secret. Missing projections, routing config failures, and health defects
are durable or visible rather than silently rendered as healthy.

## Capability matrix

The `candidate` column describes this recovery tree before live promotion. The
installed column must be refreshed after promotion and live verification.

| Capability | Baseline installed | Recovery candidate | Root truth |
|---|---|---|---|
| Supervisor | PROVEN_LIVE | PROVEN_ISOLATED_ONLY | Watchdog lived; execution/CC ownership was incomplete. |
| Owner goals/directives | BROKEN | PROVEN_ISOLATED_ONLY | 503 live; source directive previously stopped before work admission. |
| Mission admission/persistence | PARTIAL | PROVEN_ISOLATED_ONLY | Durable mission authority reused; owner-goal bridge added. |
| Next-action selection | PARTIAL | PROVEN_ISOLATED_ONLY | Canonical portfolio now compares owner goals and emits useful WAIT. |
| Worker dispatch | MOCK_SIMULATED | PROVEN_ISOLATED_ONLY | Lab spine proven; real provider remains credential-blocked. |
| Hermes | BLOCKED_EXTERNAL_DEPENDENCY | IMPLEMENTED_UNPROVEN | Safe isolated root exists; provider credential missing. |
| OpenHands | DISABLED | IMPLEMENTED_UNPROVEN | No current installed executable proof. |
| Evidence | PARTIAL | PROVEN_ISOLATED_ONLY | Nested-file and direct-file hashing corrected. |
| Independent verifier | MOCK_SIMULATED | PROVEN_ISOLATED_ONLY | Fresh verifier identity proven in Lab only. |
| FAIL to repair | BROKEN | PROVEN_ISOLATED_ONLY | Findings now drive repair context and repair routing. |
| PASS to continuation | PARTIAL | PROVEN_ISOLATED_ONLY | Ordered contracts continue and complete without prompts. |
| STOP/Resume | PARTIAL | PROVEN_ISOLATED_ONLY | ACCEPTING race fixed; Supervisor-owned proof passes. |
| Supervisor restart | PARTIAL | PROVEN_ISOLATED_ONLY | Durable mission resumes with no duplicate mission. |
| Worker crash recovery | PARTIAL | PROVEN_ISOLATED_ONLY | Owned child kill after partial write repairs and verifies. |
| Executive Intelligence | PARTIAL | PROVEN_ISOLATED_ONLY | Existing portfolio retained; owner goals are first-class items. |
| Discovery | DISABLED | IMPLEMENTED_UNPROVEN | Existing cycle retained; live activation still requires proof. |
| Outcome learning | PARTIAL | PROVEN_ISOLATED_ONLY | Outcome recorded once at terminal mission state. |
| Hybrid Memory / Obsidian | PARTIAL | IMPLEMENTED_UNPROVEN | Existing compiler/projections found; no fresh installed-process proof yet. |
| GitHub/internet research | PARTIAL | BLOCKED_EXTERNAL_DEPENDENCY | Source paths exist; credentials/provider/remote proof absent. |
| Autonomous software engineering | PARTIAL | BLOCKED_EXTERNAL_DEPENDENCY | Isolated worktree path exists; real provider/remote proof absent. |
| Eyes | DISABLED | DISABLED | No installed live activation proof. |
| Workflow Discovery | DISABLED | DISABLED | No installed live activation proof. |
| OpenAdapt | DISABLED | DISABLED | No installed live activation proof. |
| Connectors/external effects | IMPLEMENTED_UNPROVEN | IMPLEMENTED_UNPROVEN | Fake/disposable proofs do not prove real accounts. |
| Self-evolution | PARTIAL | IMPLEMENTED_UNPROVEN | Governed components exist; installed autonomous loop not proven. |
| Command Center live integration | BROKEN | PROVEN_ISOLATED_ONLY | Real bundle/API lifecycle now Supervisor-owned; promotion pending. |

## Reproducible isolated proof

Run with the release Python and a new, nonexistent disposable home:

```text
python scripts/full_product_isolated_spine_proof.py <new-disposable-home>
```

The harness labels itself `LAB` / `PROVEN_ISOLATED_ONLY`. It submits through
the real HTTP endpoint and uses the production Supervisor/worker-loop process
shape. It proves restart continuation, false-success rejection, automatic
repair, STOP/Resume, owned worker kill and recovery, durable UI reconstruction,
no duplicate missions, and useful WAIT. It does not prove a real provider.

## Remaining live boundary

Promotion must replace `CURRENT`, preserve `PREVIOUS`, update the Scheduler to
let Supervisor own Command Center, and rerun the proof from the immutable
release. The installed normal routing configuration must disable Lab and fail
closed at the missing provider credential. Until a real authorized provider is
configured and the full installed mission completes, the maximum truthful final
verdict is `BLOCKED_EXTERNAL_DEPENDENCY`.
