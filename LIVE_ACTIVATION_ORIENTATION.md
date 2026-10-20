# LIVE ACTIVATION ORIENTATION — 2026-08-25

**Mode:** GOD MODE / AUTONOMOUS UNTIL TERMINAL PREDICATE
**Purpose:** Phase A — orient before mutating. No mutations performed.

## 1. Required Canonical Source

| Field | Value | Status |
|-------|-------|--------|
| `local main SHA` | `8dfb981876985525621cf10b8b9b67e34468b450` | PASS — `git rev-parse HEAD` |
| `origin/main SHA` | `8dfb981876985525621cf10b8b9b67e34468b450` | PASS — `git rev-parse origin/main` |
| `required SHA` | `8dfb981876985525621cf10b8b9b67e34468b450` | MATCH |
| `observed Git tree` | `32932bf1d8ea025a6ec3706284505cc2ccebcbf9` | recorded |
| `dirty` | `?? .cursor/` only (untracked) | CLEAN — no tracked dirty files |
| `merge markers` | none found (scanned `*.py,*.md,*.yaml,*.json,*.toml`) | PASS |
| `canonical tree merge markers` | none | PASS |
| `source SCHEMA_VERSION` | `37` (`src/jarvis_foundry/migrations.py:8`) | PASS — `SCHEMA_VERSION = 37`, migration 37 `tier_c_project_aliases` present |
| `integration status` | PASS_CLOSED (commit `8dfb981` message confirms 4-lane integration + verifier PASS 12/12) | PASS |

## 2. Live Release Pointers (Filesystem Authority)

| Field | Value |
|-------|-------|
| `install_root` | `C:\Users\b\AppData\Local\JARVIS` |
| `CURRENT pointer` | `C:\Users\b\AppData\Local\JARVIS\CURRENT` → `f7dfd7c1b370d8ab203b2e568f6e6d14e489a90b` |
| `PREVIOUS pointer` | `C:\Users\b\AppData\Local\JARVIS\PREVIOUS` → `0456d219a5567490e986697490f79433fe97e106` |
| `CURRENT release dir` | `C:\Users\b\AppData\Local\JARVIS\releases\f7dfd7c1b370d8ab203b2e568f6e6d14e489a90b\` — EXISTS, manifest valid |
| `PREVIOUS release dir` | `C:\Users\b\AppData\Local\JARVIS\releases\0456d219a5567490e986697490f79433fe97e106\` — EXISTS, manifest valid |
| `CURRENT manifest` | `release_id rel-f7dfd7c1b370-12745a88`, `git_commit f7dfd7c...`, `git_tree e2f7bcb...`, `schema 36` |
| `PREVIOUS manifest` | `release_id rel-0456d219a556-235c6e08`, `git_commit 0456d219...`, `schema 36` |
| `GENERATION.json` | EXISTS — `source_sha f7dfd7c1b370...`, `schema 36`, `release_id rel-f7dfd7c1b370-12745a88` |
| `target SHA release exists?` | `C:\Users\b\AppData\Local\JARVIS\releases\8dfb981876985525621cf10b8b9b67e34468b450` — **NOT EXISTS** (expected — needs build in Phase D) |
| `release authority report` | `FILESYSTEM_ONLY_AUTHORITY` (`database_current_recorded=false`, `database_release_records=3`) — deferred DB authority, not crash |
| `installer task launcher` | `C:\Users\b\AppData\Local\JARVIS\bin\jarvis-supervisor-headless.vbs` — EXISTS |
| `stable launcher cmd` | `C:\Users\b\AppData\Local\JARVIS\bin\jarvis-supervisor.cmd` — EXISTS |

**Interpretation:** Filesystem pointers are coherent. CURRENT is valid schema-36 release, PREVIOUS preserved for rollback. TARGET release not yet built — no blocker, Phase D will build via canonical `deployment.release.build_release`.

## 3. Live DB State

| Field | Value |
|-------|-------|
| `live DB path` | `C:\Users\b\AppData\Local\hermes\foundry\foundry.db` |
| `DB size` | ~195,858,432 bytes (~187 MB) + WAL ~4,400,192 bytes |
| `DB exists` | true |
| `journal_mode` | WAL (verified via `PRAGMA journal_mode` earlier) |
| `quick_check` | `ok` |
| `event_count` | 284,896 (doctor) / 284,867 (direct query at 09:08) — advancing |
| `max sequence` | 284,896 |
| `schema_migrations MAX` | `36` — LIVE is v36 |
| `source schema` | `37` — delta +1 migration pending |
| `tier_c_project_aliases table` | absent in live (expected — migration 37 adds it) |
| `reconciliation consistent` | `true` — `mismatches=[]`, `integrity_failures=[]` (doctor `reconciliation.consistent`) |
| `integrity status` | PASS — `PRAGMA quick_check=ok`, ledger verify consistent |

**WAL note:** WAL file present (`foundry.db-wal` ~4.4 MB). Phase C must `checkpoint WAL safely using canonical DB tooling` before backup (use `FoundryDatabase.backup_to` which handles WAL).

## 4. Supervisor / Runtime

| Field | Value |
|-------|-------|
| `supervisor_state.singleton` | `supervisor-afd96c9e-306f-4439-a578-81d9224b5b88`, `status RUNNING`, `trust_state LAB`, `last_heartbeat_at 2026-08-25T09:10:00.992Z` |
| `lease supervisor:singleton` | owner `supervisor-afd96c9e...`, `expires_at 2026-08-25T09:10:13Z`, `heartbeat_at 2026-08-25T09:08:13Z` — **alive, advancing** |
| `lease worker-loop:singleton` | owner `worker-loop-57f2b6e7734f`, `expires 09:10:13Z` |
| `lease worker:worker-loop` | owner `worker:worker-loop`, `expires 09:23:12Z` |
| `runtime_control` | `status RUNNING`, `desired_action START`, `request_id postv7-final-start-f7dfd7c`, `request_status APPLIED`, `pid 18572`, `creation_identity create_time 134320654730314141 exe C:\Python313\pythonw.exe` |
| `health` | `DEGRADED` — reason `release identity status is FILESYSTEM_ONLY_AUTHORITY` (expected for pre-activation; reconciliation is consistent, not a crash) |
| `doctor event_count` | 284,896, `heartbeat_count` 118,909 |
| `last supervisor.heartbeat seq` | 284,887 at 09:10:00, prior 284,876 at 09:09:04 — **heartbeat advancing ~60s** |
| `active_workers (doctor)` | `["worker-loop"]` |
| `supervisor PID (live)` | `18572` (pythonw) — confirmed via `doctor runtime_control.pid` and `wmic` `C:\...\f7dfd7c1b370...\pythonw.exe -m jarvis_foundry.deployment.headless_launcher ...` |
| `headless_launcher PIDs (wmic)` | `18572` (active lease holder) + `2084` (second wscript-launched pythonw with identical args) — **second process exists but does NOT hold lease**; logs show repeated `LEASE_UNAVAILABLE: lease supervisor:singleton is held by another live owner` for duplicate scheduler ticks (empirical IgnoreNew + 1-min repetition). **No duplicate authority** — duplicate claims fail closed. |
| `worker_loop --once PIDs` | `26496`, `34312` etc — ephemeral `--once` workers spawned by supervisor heartbeat, not duplicate supervisors |
| `stray test python processes` | `python.exe 29964` + `12488` running `--home C:\Users\b\AppData\Local\Temp\pytest-of-b\pytest-4331\test_task_restarts_after_hard_0\foundry-home` and `pythonw 35252` for `pytest-5038` — **isolated test foundry-homes**, not live DB; no live DB mutation risk but should be noted for cleanup |
| `Hermes gateway` | `python 31796/17752` — alive, but `hermes_required_for_supervisor=false` |
| `codex-router litellm` | `6128/24176` — auxiliary |

**Duplicate-authority verdict:** Single lease holder (18572). Repeating scheduler triggers create failed duplicate attempts that log `LEASE_UNAVAILABLE` and exit — fail-closed behavior, not a live invariant violation. Pre-activation file still reports `DEGRADED` only for filesystem-only authority.

## 5. Task Scheduler

| Field | Value |
|-------|-------|
| `Task Name` | `\JARVIS-Foundry-Supervisor` |
| `Status` | `Ready`, `Scheduled Task State: Enabled` |
| `Logon Mode` | `Interactive only`, `Run As User: b`, `Author: N/A` |
| `Next Run` | `2026-08-25 11:06:33 AM`, `Last Run 2026-08-25 11:05:34 AM`, `Last Result 0` |
| `Task To Run` | `C:\Windows\System32\wscript.exe //B //Nologo "C:\Users\b\AppData\Local\JARVIS\bin\jarvis-supervisor-headless.vbs" --home "C:\Users\b\AppData\Local\hermes\foundry" --spawn-worker-loop --worker-loop-config "C:\...\prompt2-worker-routing.yaml"` |
| `WorkingDirectory` | `C:\Users\b\AppData\Local\hermes\foundry` |
| `Triggers` | `LogonTrigger` (`DESKTOP-42E0DQE\b`) + `TimeTrigger` `StartBoundary 2026-08-24T19:16:33`, `Repetition PT1M / P30D` |
| `Settings` | `MultipleInstancesPolicy IgnoreNew`, `Hidden true`, `ExecutionTimeLimit PT0S`, `RestartOnFailure PT1M x3`, `StartWhenAvailable true` |
| `Worker-Loop task` | `\JARVIS-Foundry-Worker-Loop` — `Disabled` (expected — `LEGACY_DISABLED`, Supervisor spawns worker-loop) |
| `Proving-window monitor` | `\JARVIS-Foundry-Proving-Window-Monitor-Canonical-R3` — `Ready` (read-only monitor, not worker authority) |
| `Test tasks` | `\JF-WP7Gate-test_tas-8089` and `-94653` — `Running` — isolated test tasks in Temp homes; not live authority |
| `Install guard` | `deployment.guard.refuse_developer_checkout_on_live_home` correctly **refuses** developer checkout DB access when CURRENT exists — ensures immutable release path is used |
| `Scheduler health` | PASS — task enabled, last result 0, repetition advancing |

## 6. Emergency STOP / Owner Veto / Directives

| Field | Value |
|-------|-------|
| `EMERGENCY_STOP` | `C:\Users\b\AppData\Local\hermes\foundry\EMERGENCY_STOP` — **NOT EXISTS (false)** |
| `directives active` | 20 rows — includes `DIR-2026-08-12-gate-progression` (redirect), `seven-day-waiver` (waive), `window-extension`, `master-plan-alignment` (align), plus 6 `CONSTRAINT`/`ADD_CONSTRAINT` and 8 `GOAL` (prompt3/prompt4/GLOBAL) |
| `VETO directive` | none — no `VETO` command_type active; owner STOP/veto path clear |
| `STOP active (doctor)` | `false` |
| `runtime_control` | `RUNNING` — not blocked |

## 7. Command Center / Diagnostics

| Field | Value |
|-------|-------|
| `doctor overall` | `reconciliation consistent=true`, `event_count 284k`, `runtime_control RUNNING`, `release_authority FILESYSTEM_ONLY_AUTHORITY` |
| `health.status` | `DEGRADED` solely for filesystem-only authority — **not a crash** |
| `Command Center API` | Not separately probed via HTTP in this orientation; `doctor` diagnostic snapshot covers ledger truth. Live port scan shows no unexpected port binding leakage; expected health is via `/api/command-center` on Hermes gateway (deferred to Phase I). |
| `Bridging` | `supervisor_state` RUNNING, heartbeat advancing, no stale activation locks reported in logs beyond lease-failed probes |

## 8. Runtime / Provider State

| Field | Value |
|-------|-------|
| `CURRENT release python` | `C:\Users\b\AppData\Local\JARVIS\releases\f7dfd7c1b370...\venv\Scripts\python.exe` — SCHEMA_VERSION 36 |
| `canonical src python` | `C:\Python313\python.exe` with `PYTHONPATH` to canonical `src` — SCHEMA_VERSION 37 |
| `guard` | Blocks developer checkout DB mutation — must use release python for live DB operations |
| `foundry-profile.yaml` | `task_name: JARVIS-Foundry-Supervisor`, `source_sha: f7dfd7c..., release_sha f7dfd7c, python_exe ...\f7dfd7..., spawn_worker_loop true` |
| `prompt2-worker-routing.yaml` | exists — `C:\Users\b\AppData\Local\hermes\foundry\prompt2-worker-routing.yaml` |
| `provider` | Not authenticated in this orientation (OpenCode/Vertex etc verified at runtime via worker config). No hard failure observed. |

## 9. CURRENT / PREVIOUS Pointer Targets — Coherence

- `CURRENT` → `f7dfd7c1b370d8ab203b2e568f6e6d14e489a90b` (released `rel-f7dfd7c1b370-12745a88`, built 2026-08-24, schema 36)
- `PREVIOUS` → `0456d219a5567490e986697490f79433fe97e106` (released `rel-0456d219a556-235c6e08`, schema 36)
- Both manifests valid, `previous_runtime` chain verified in manifest (`f7dfd7 previous_runtime == 0456d219 identity`)
- `GENERATION.json` matches CURRENT
- No pointer corruption, no missing release dir
- `8dfb981` not yet CURRENT/PREVIOUS — **this is the activation delta**

## 10. Disk Space

- Free: `~1,193,176,977,408` bytes (~1.11 TB) on `C:`/`JARVIS`/`hermes` — **ample**
- No disk-pressure blocker

## 11. Active JARVIS Processes (wmic filtered)

```
pythonw 2084  — C:\...\JARVIS\releases\f7dfd7c...\pythonw.exe -m jarvis_foundry.deployment.headless_launcher --home ... --spawn-worker-loop
pythonw 18572 — same (PID 18572) — LEASE HOLDER
pythonw 26496 — C:\...\f7dfd7...\pythonw.exe -m jarvis_foundry.runtime.worker_loop --once --home ... (ephemeral)
pythonw 34312 — same --once (ephemeral; two concurrent due to close dispatch cadence)
pythonw 35252 — pytest temp foundry-home isolate (not live)
python  29964 — pytest temp foundry-home isolate (not live)
python  12488 — same
python  31796/17752 — Hermes gateway
python  6128/24176 — codex-router litellm
```

**Single Supervisor authority confirmed:** lease `supervisor:singleton` owned by 18572, others fail with `LEASE_UNAVAILABLE`.

## 12. Dirty Canonical Worktrees

- `git status --porcelain` → `?? .cursor/` only (untracked, allowed)
- No tracked dirty files → `build_release` will pass dirty check
- Worktrees list shows canonical `C:/Users/b/Documents/ChatGPT/Autonomous AI powered by Hoowen/JARVIS-Foundry` on `8dfb981 [main]` — clean
- 100+ other worktrees are PRUNABLE historical candidates — not dirty, not blocking; no active merge in canonical

## 13. Release-Management Blockers

| Blocker | Status |
|---------|--------|
| Dirty tracked tree | NONE — clean |
| Merge markers | NONE |
| Olive schema mismatch | Expected delta 36→37 — to be handled in Phase F via canonical migration |
| Missing PREVIOUS | NONE — PREVIOUS exists and validates |
| Missing CURRENT | NONE — CURRENT exists |
| Duplicate live Supervisor authority | NONE — single lease holder; duplicate launches fail closed |
| EMERGENCY_STOP active | NONE — false |
| Disk space | NONE — 1.1 TB free |
| Task Scheduler disabled | NONE — enabled, last result 0 |
| Guard blocking release python | NONE — release python can access live DB; developer checkout guard is correct |
| WAL not checkpointed | PENDING — Phase C must checkpoint via `FoundryDatabase.backup_to` |
| `8dfb981` release built | NOT YET — Phase D builds it |
| `GENERATION.json` stale | No — matches CURRENT |
| Secret exposure | No credentials in orientation artifacts |

## 14. Pre-Activation Invariants

- Integration `PASS_CLOSED`, four lanes merged at `8dfb981`, verifier 12/12 PASS
- Source HEAD matches required SHA; origin/main matches
- Live release remains previous production (deliberately not yet mutated)
- Live DB remains at v36, schema v37 source ready
- CURRENT/PREVIOUS untouched, Supervisor not restarted, Task Scheduler not modified — **as required at integration closure**

## 15. Evidence Artifacts for This Phase

- `git rev-parse HEAD` = `8dfb981876985525621cf10b8b9b67e34468b450`
- `git rev-parse origin/main` = same
- `git rev-parse HEAD^{tree}` = `32932bf1d8ea025a6ec3706284505cc2ccebcbf9` (cross-checks `RELEASE.json git_tree` for built releases)
- `src/jarvis_foundry/migrations.py SCHEMA_VERSION=37` verified via `C:\Python313\python.exe -c sys.path.insert`
- `doctor` output at `2026-08-25T09:10:00Z` — reconciliation consistent, 284,896 events, 118,909 heartbeats
- `C:\Users\b\AppData\Local\JARVIS\releases\f7dfd7c1b370...\RELEASE.json` manifest captured
- `C:\Users\b\AppData\Local\JARVIS\CURRENT` / `PREVIOUS` contents captured
- `C:\Users\b\AppData\Local\JARVIS\logs\supervisor-20260825.log` tail — lease-fail-closed evidence
- Task Scheduler XML export captured
- `wmic` process lists captured

---

**ORIENTATION RESULT: PASS — no mutation, ready for Phase B pre-activation gate.**

Next: Phase B must re-verify `local==origin==8dfb981`, schema source v37, migrations contain v37, no drift, previous release rollback-available, and that deployment factory can build this SHA before any packaging.
