# Durable Recovery Notes — Authoritative Resume Point

Updated: 2026-08-12T01:05+02:00 (continuation run)
Session: Hermes Desktop (visible executive), START BUILD active.

## READ FIRST

1. `PROJECT_STATE.md` — live state record (v12).
2. `docs/handoffs/CHECKPOINT_2026-08-12T0105.md` — newest durable checkpoint.
3. `docs/evidence/WP7_RECOVERY_2026-08-12.md` — 2026-08-12 incidents, repairs,
   verifier verdict, restore record.
4. `docs/evidence/WP8_ENTRY_PRECONDITIONS.md` — WP8 gate preconditions.
5. This file — milestone log + resume mechanics.

Repository truth beats chat summaries. Re-verify state at session start
(`git status`, `tasklist | grep -i python`, task state, DB quick_check).

## Current Truth (verified 2026-08-12)

- **Runtime restored and healthy:** task `JARVIS-Foundry-Supervisor`
  installed + enabled + RUNNING (daemon `supervisor-278af45b…`, heartbeats
  every 30 s, exactly one singleton lease, reconcile consistent, 13+
  events). Live STOP freeze → resume proven 2026-08-12.
- **WP0–WP7 PASS** (WP7 activated; recovery repairs verified by independent
  verifier deleg_56e7719d, C1–C6 PASS).
- **WP8 ACTIVE**: entry preconditions being recorded; seven-day clock NOT
  started. Proving resource selected (wfm-optimizer), mission + gate plan
  drafted.
- Live DB: `C:\Users\b\AppData\Local\hermes\foundry\foundry.db` (WAL;
  content-identical to the incident state; current sha256
  `1734df87…` — the older documented `9d4600f5…` reflects a WAL checkpoint
  change; content is the invariant).
- Full Foundry suite: 204 collected, exit 0, 0 FAILED/ERROR
  (`var/evidence/recovery-run/foundry-tests-final3.txt`). Hermes estop
  baseline: 24 passed.

## 2026-08-12 Incident Summary (details in WP7_RECOVERY_2026-08-12.md)

1. **0xC000013A** — live daemon died on a console control event without
   releasing its lease (stale RUNNING + expired lease). Repaired: console
   control handler performs a fast durable stop on every control event.
2. **Suite deleted the live task** — installer real-name tests' teardown
   was not scoped to test-created tasks. Repaired: host-state-agnostic
   tests + regression; task restored 00:55 local.
3. **RestartOnFailure never fires on this host** — compensated with a
   repeating time trigger (PT1M / 30-day) + IgnoreNew; live restart proven.
4. **Adapter provider names** — `opencode_go`/`vertex_ai` are not CLI
   aliases; canonicalized at the adapter boundary (opencode-go/vertex).
   Real role dispatch now resolvable; isolated HERMES_HOME provisioning is
   the next WP8 step.

## How To Resume (if this session is interrupted)

1. Read the READ FIRST list, then re-verify: `git status --short`,
   `tasklist | grep -i python`, `schtasks /query /tn JARVIS-Foundry-Supervisor`,
   live DB quick_check + reconcile (`jarvis doctor`).
2. If the Supervisor is not running: `schtasks /run /tn
   JARVIS-Foundry-Supervisor` (the repeating trigger also self-heals within
   ~2 min of any death). Never start a second daemon manually — the
   singleton lease prevents duplicates.
3. If WP8 clock has not started: continue the precondition record
   (`WP8_ENTRY_PRECONDITIONS.md`), then perform the clock-start sequence
   (START BUILD ledger append, resource + mission registration, isolated
   HERMES_HOME provisioning, V1 Obsidian root + snapshot), then execute the
   gate plan (`WP8_GATE_EXECUTION_PLAN.md`).
4. Never run duplicate builders. Never edit live Hermes or `.h` beyond
   read-only overlays. Never modify the canonical DBs under var/evidence.
   Freeze before rollback. No pushes during the V1 gate.

## Canonical Paths (NEVER mutate; use copies)

- `var/staging-foundation/foundry.db`, `var/cli-proof/foundry.db`,
  `var/final-proof/foundry.db`, `var/wp1-staging/foundry.db` — canonical
  gate evidence DBs (read-only, hashes in the wp1-gate bundle).
- `.h` — isolated Hermes development checkout (commit
  `244d296646909aca1dd16c9759491da0ef4cd163`).
- `var/staging-recovery/foundry.db` — live-DB copy used for the recovery
  rehearsal (content = incident state + one rehearsal cycle).
- Evidence bundles: `var/evidence/wp1-gate/` … `var/evidence/wp7-gate/`.

## Milestones Completed (this run, 2026-08-12)

- [x] Before-state bundle: git/process/task/DB/lease/events captured.
- [x] 0xC000013A diagnosed (console control event) + repair + 6 regression
      tests; real CTRL_BREAK end-to-end test.
- [x] Suite-reproduction incident (live task deleted) diagnosed + repair +
      regression; task restored and re-activated.
- [x] Restart mechanism probes (RestartOnFailure dead ×3; repeating
      trigger works) + installer XML + live restart regression test.
- [x] Recovery rehearsal on live-DB copy: PASS.
- [x] Independent verifier deleg_56e7719d: C1–C6 PASS (caveats recorded).
- [x] Post-verdict hardening: non-Windows import guard, adapter provider
      canonicalization; full suite 204 green; estop 24 green.
- [x] Live task restored; heartbeats proven; live STOP freeze → resume
      proven; reconcile consistent.
- [x] Docs reconciled in one transition (PROJECT_STATE v12,
      CURRENT_WORK_PACKAGE WP8, RECOVERY_NOTES, LOW_COST_CONTINUATION,
      marker).
- [ ] Freeze evidence bundle + checkpoint commit (next).
- [ ] WP8 clock start (precondition completion + atomic ledger records).

## Exact Next Actions (in order)

1. Freeze the recovery evidence (`freeze_verified_state.py` bundle) and
   create the coherent local checkpoint commit (no push).
2. WP8 clock start: provision the isolated HERMES_HOME (scoped
   credential_pool.opencode-go + OPENCODE_GO_API_KEY + config copy — never
   printed), verify a real oneshot role dispatch (bounded, cost recorded),
   append `system.start_build_recorded` + `project.registered` +
   `mission.created` atomically, create the mission repo baseline + V1
   Obsidian root snapshot, finalize WP8_ENTRY_PRECONDITIONS.md.
3. Execute the gate plan: G1–G9 matrix, F1–F15 injections, ≥10 verified
   cycles, reconciliation, independent final review.

## Running Commands

```bash
cd "C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry"
./.venv/Scripts/python.exe -m pytest tests/foundry -q --tb=short
./.venv/Scripts/python.exe -m jarvis_foundry.cli.main doctor
./.venv/Scripts/python.exe -m jarvis_foundry.cli.main status
schtasks /run /tn JARVIS-Foundry-Supervisor   # restart the daemon if needed
# Hermes baseline:
cd .h && HERMES_HOME="<root>\var\wp1-hermes-home" PYTHONPATH="$PWD;C:\Users\b\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages" ../.test-venv/Scripts/python.exe -m pytest tests/test_estop.py -q
```

## Honesty Rule

Nothing is PASS until reproduced with fresh commands. Verifier reports are
evidence to integrate, not claims to relay.
