# WP8 Entry Preconditions — V1 Seven-Day Acceptance Gate

Status: INVENTORY IN PROGRESS (values verified 2026-08-12, continuation run)
Gate: `docs/architecture/04_V1_ACCEPTANCE_GATE.md` §3 (entry preconditions)
Rule: the seven-day clock does NOT start until every precondition is
recorded and reproducible. This document is the precondition record.

## 1. Mandate And Specifications

| Precondition | Value | Verified |
|---|---|---|
| Original START BUILD event | Owner directive 2026-08-11T17:33:05+02:00 (00_READ_FIRST §9, PROJECT_STATE) | recorded; live-ledger append pending (see §10) |
| AUTONOMY_MANDATE.md sha256 | `918e9909040ea8d83845d1988cdb82297a7de4ad8e06d9921c7e87da68405ecb` | ✓ |
| 02_ARCHITECTURE_CONTRACT.md sha256 | `955ab4ee577a40f715f173401a7f425ba88eb57950f1223d1b733b73e876529a` | ✓ |
| 03_V1_AUTONOMY_KERNEL_SPEC.md sha256 | `0d1a5a7abffb56ba39ee47e9f5855ec50a6011fca42b2672bd724623c207e586` | ✓ |
| 04_V1_ACCEPTANCE_GATE.md sha256 | `91cac4b603fbb6172e5d2603fd5534f9601b7c8131e934cd438df280b3f0425f` | ✓ |

## 2. Source And Runtime Inventory

| Precondition | Value | Verified |
|---|---|---|
| Hermes base (foundation) commit | live install commit `244d296646909aca1dd16c9759491da0ef4cd163` (recorded in HERMES.md; `.h` pinned to it) | ✓ |
| Foundry repo HEAD | `c2770ef` (recovery checkpoint commit pending after verification) | ✓ |
| Foundry working tree | dirty by design: WP7 repairs + this run's recovery fixes (see git status) | ✓ |
| Python (Foundry venv) | 3.13.5 (`.venv\Scripts\python.exe`) | ✓ |
| Python (system) | 3.11.15 | ✓ |
| Node | v24.19.0 | ✓ |
| Windows | 10.0.26200.8875 (build 26200) | ✓ |
| Git | 2.50.1.windows.1 | ✓ |
| Hermes runtime | Hermes Desktop (this session) + gateway; adapter uses `.h` CLI surface | ✓ |
| Provider mapping | foundry-profile.yaml `model_roles: default`; role defaults per contract §14.2 (verified at WP4 gate; runtime recheck at first dispatch) | partial |
| Resolved HERMES_HOME | `C:\Users\b\AppData\Local\hermes` (profile) | ✓ |
| Foundry DB path | `C:\Users\b\AppData\Local\hermes\foundry\foundry.db` (WAL; sha256 `9d4600f5…` at handoff, changes as the runtime lives) | ✓ |
| Schema version | migrations 1–4 applied (v4) | ✓ |
| Supervisor scheduled task | `JARVIS-Foundry-Supervisor` (logon trigger, hidden, IgnoreNew, RestartOnFailure PT1M×3, absolute python + `--home`); see §5 | ✓ |

## 3. Runtime Health Preconditions

| Precondition | Value | Verified |
|---|---|---|
| Supervisor task installed + enabled | yes (restored 2026-08-12; see WP7_RECOVERY_2026-08-12.md) | ✓ |
| Daemon running + heartbeating | verified at restore (≥2 heartbeats 30 s apart, singleton lease held) | ✓ |
| Singleton lease | exactly one `supervisor:singleton` owner at all times | ✓ |
| Restart recovery | expired-lease reclaim proven (unit + staging rehearsal on live-DB copy) | ✓ |
| STOP (sentinel/CLI) on the real runtime | sentinel freeze → FROZEN → resume proven on staging; live-task probe pending | pending |
| STOP independence from Hermes | WP2/WP7 gate evidence (daemon import graph, no Hermes dependency) | ✓ (gate) |

## 4. Emergency-Stop Mechanisms (staging-proven, WP7 gate)

Sentinel file `%HERMES_HOME%\foundry\EMERGENCY_STOP`; CLI `jarvis stop` /
`jarvis resume`; tray + hotkey controls (WP2 controls module). All four
mechanisms produce the same deterministic freeze (WP2/WP7 evidence).
Maximum detection latency ≤10 s: WP2 gate evidence.

## 5. Supervisor Scheduled-Task Definition (restored)

```text
Task: JARVIS-Foundry-Supervisor (enabled)
Run:  <repo>\.venv\Scripts\python.exe -m jarvis_foundry.supervisor.run
      --home "C:\Users\b\AppData\Local\hermes\foundry"
Trigger: logon (+ repeating time trigger added by the recovery fix — see §10)
Settings: Hidden, IgnoreNew, StartWhenAvailable, RestartOnFailure PT1M×3,
          ExecutionTimeLimit PT0S, interactive logon, least privilege
```

## 6. Proving Resource And Mission (selection)

| Precondition | Value | Verified |
|---|---|---|
| Candidate scoring | WP8_RESOURCE_SCORING.md (scored: LeadGen, QueueTwin, Hoowen, `.h`-excluded) | pending |
| Selected resource record | registered in live DB (`project.registered` event) | pending |
| Isolated worktree | created under the selected resource's repo | pending |
| Mission objective + ≥3 acceptance criteria | WP8_MISSION_CONTRACT.md | pending |
| Target / non-target paths | resolved and recorded | pending |
| Build/test commands | discovered | pending |
| Rollback + cleanup plan | recorded | pending |

## 7. V1 Obsidian Root

| Precondition | Value | Verified |
|---|---|---|
| V1 write root | `C:\Users\b\Blendi\JARVIS\V1\` (00–07 + Audit/) | pending |
| Initial snapshot | byte snapshot + expected hashes | pending |
| Write confinement | WP6 gate evidence (V1-root confinement, whole-vault read only) | ✓ (gate) |

## 8. Gate Execution Plan

- Reviewer identity/independence: fresh isolated session, provider-diverse
  when available; same-provider limitation recorded per contract §14.3.
- F1–F15 injection schedule + G1–G9 mapping: WP8_GATE_EXECUTION_PLAN.md.
- F4 Windows-restart: planned within the run (safety + before-state +
  recovery + rollback prerequisites proven first).

## 9. Residual Items Recorded But Not Blocking

- Task Scheduler RestartOnFailure empirically does NOT fire on this host
  for this task shape (three probe scenarios, transcripts in
  var/evidence/recovery-run/); compensated by the repeating-time-trigger
  restart mechanism + console-event clean-stop handler (see
  WP7_RECOVERY_2026-08-12.md §3) — recorded deviation.
- Same-provider verifier limitation (§14.3).
- The repo venv python path in the task is absolute; a repo move requires
  reinstall (recorded WP7 residual risk).

## 10. Verification Trails

- Recovery probes: var/evidence/recovery-run/ (suite outputs, probe
  transcripts, staging DB copy hashes).
- Live DB lineage: hash at handoff `9d4600f5…`; every live mutation
  (START BUILD event, mission registration, gate events) appended as
  events with recorded hashes.
