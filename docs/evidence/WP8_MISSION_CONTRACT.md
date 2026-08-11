# WP8 Proving Mission Contract — wfm-optimizer

Status: DRAFT — final registration happens atomically at clock start
Date: 2026-08-12
Gate: `docs/architecture/04_V1_ACCEPTANCE_GATE.md` §4 (proving mission
requirements); selection rationale: `docs/evidence/WP8_RESOURCE_SCORING.md`.

## 1. Mission Identity

- **mission_id:** `mission-wfm-optimizer` (registered in the live ledger at
  clock start via `mission.created`)
- **resource_id:** `resource-wfm-optimizer` (`project.registered` at clock
  start)
- **objective:** build a deterministic, tested CLI engine for the owner's
  contact-center break/lunch planning rules — the documented contract of
  the existing WorkforcePlanner.xlsm tool (2 × 10 min breaks + 1 × 40 min
  lunch per shift; phone vs digital staffing; operational coverage is
  priority; < ~200 ms execution) — as an inspectable, testable, reusable
  Python package that the Excel tool can later call into.
- **why now / user value:** the owner is an Intraday/Steering Coordinator;
  the existing optimizer is Excel/VBA with no test harness; a
  deterministic, property-tested engine extends it without touching his
  working tool or any business repo. Independence from the JARVIS kernel
  itself makes the gate evidence non-circular.

## 2. Resource And Worktree

- Repository path: `C:\Users\b\JARVIS\v1-proving\wfm-optimizer`
  (new directory, JARVIS-created; outside the Obsidian vault and outside
  every existing project).
- Baseline: the mission creates the baseline commit FIRST
  (`chore: establish wfm-optimizer baseline`) before any implementation —
  the tested rollback point (G8).
- Target paths: the repository root only.
- Non-target paths (resolved, recorded): `C:\Users\b\Blendi\` (vault),
  `C:\Users\b\AppData\Local\hermes\` (live Hermes + Foundry home), every
  other repository on the machine, `%TEMP%` writes only for tests.
- Branch: `main`; local commits only (push NOT_READY in V1).

## 3. Scope

### In scope
- Python package `wfm_optimizer` + CLI entry `wfm-optimize`.
- Roster CSV parsing (agent, shift start/end, channel split phone/digital).
- Break/lunch rule engine (2×10 + 1×40; configurable window margins with
  documented defaults) with per-interval coverage floors per channel.
- Deterministic scheduler (sorted assignment order, no RNG).
- Feasibility reporting: exact violated intervals/channels, never a fake
  schedule.
- JSON/CSV output + exit codes (0 ok / 2 infeasible / 3 usage error).
- Property tests + fixture matrix + performance benchmark (< 200 ms for
  50 agents × 1 day), README, LICENSE-owner note.

### Out of scope (FORBIDDEN)
- Touching the owner's Excel workbook or his vault notes.
- Any AI generation, telephony/Twilio integration, or live agent data.
- Anything beyond the documented rule contract; no new products.
- Pushes, deployments, publications, paid services.

## 4. Acceptance Criteria (independent, testable)

- **AC1 Rule conformance:** for every fixture in a 12-case matrix (varied
  shift overlaps, channel mixes, edge cases), the produced schedule
  satisfies ALL break/lunch rules AND all coverage floors; property tests
  assert this directly.
- **AC2 Determinism:** identical roster input produces byte-identical
  output across repeated runs (verified via hash of schedule.json).
- **AC3 Performance:** 50-agent, one-day instance schedules in < 200 ms
  (measured 5 runs, median recorded in evidence).
- **AC4 Honest infeasibility:** an infeasible fixture returns exit code 2
  with a precise violation report (agent/interval/channel), never a
  partial or fake schedule.
- **AC5 CLI contract:** documented flags, exit codes, and JSON schema are
  pinned by CLI tests (argv-level, subprocess).

## 5. Required Mission Behaviors (gate mapping)

- **G3 next-action selection:** after each PASS, the executive selects the
  next criterion from the remaining list (no owner prompting).
- **G6 repair loop:** one deliberate constraint bug is injected after AC1
  passes; the verifier returns FAIL; a fresh builder repairs it; full
  affected verification reruns; a fresh verifier independently PASSes.
- **G7 write-back:** each accepted step writes Obsidian V1 state (00–07 +
  Audit), evidence bundle, and ledger events.
- **G8 git integrity:** one coherent commit per accepted step after
  verifier PASS; rejected work never committed; rollback demonstrated
  (revert + worktree delete rehearsal in staging, recorded).
- **F1–F15:** executed per `04_V1_ACCEPTANCE_GATE.md` §7 with this mission
  as the active workload (kill supervisor idle/active; kill worker
  mid-step; network/provider outage at dispatch; duplicate events;
  injection; path escape; false-success report).

## 6. Budget And Resources

- Local compute only; zero external spend (all dispatches through
  configured local Hermes/opencode-go role sessions; provider cost
  attributed per run and recorded).
- Expected duration: 7 calendar days (PC-available time only), ≥ 10
  independently verified action cycles.

## 7. Rollback And Cleanup

- Per-step: `git revert` of the step commit in the worktree branch.
- Whole-mission: delete the worktree (registered path) + delete the branch
  + `project.revoked` event; baseline commit preserved in the ledger.
- Obsidian V1 area: snapshot + rollback pointers per note (WP6 machinery).
- On STOP: freeze (WP2 machinery), no automatic mass rollback.

## 8. Stop Conditions

- Active owner STOP or veto (sentinel/CLI/hotkey/tray).
- Secret exposure or a mutation outside the target paths.
- Budget/provider exhaustion with no fallback (honest BLOCKED).
- Any attempt to weaken the gate's criteria or manufacture PASS.

## 9. Verification Plan

- Builder: JARVIS executive/role sessions through the WP4 adapter (real
  dispatches, isolated HERMES_HOME provisioning at clock start).
- Verifier: fresh isolated sessions; provider-diverse when available,
  otherwise recorded same-provider limitation; read-only; JSON verdicts.
- Evidence: `var/evidence/wp8-gate/` freeze bundles per accepted step;
  reconciliation at gate end per `04` §9.

## 10. Recorded Assumptions (uncertainty made explicit)

- The WorkforcePlanner.xlsm file itself was not found on this machine
  (only its vault note `02 - Career/WorkforcePlanner.md`); the engine
  implements the DOCUMENTED rules and records any default as an
  assumption in the README, reviewable by the owner.
- Break-window margins (first/last 30 min of shift) and per-channel floor
  values are configurable parameters with documented defaults; the fixture
  matrix states them explicitly.
