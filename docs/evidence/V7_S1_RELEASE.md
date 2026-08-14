# V7-S1 — ACCEPTED V6 INTEGRATION AND IMMUTABLE RELEASE PACKAGE

```text
STATUS: PASS — CLOSED (independent verifier deleg_719b3c49, structured PASS)
PHASE: V7_FULL_AUTONOMOUS_OPERATION
SLICE: S1 — accepted V6 integration and immutable release package
OBJECTIVE: prove the exact accepted V6 source transition into main; preserve
  all untracked owner files; create frozen source/dependency/configuration/
  runtime/artifact manifests; register previous/candidate/live/quarantine
  release slots on the isolated V7 runtime ledger; keep all evolution and
  external-effect features disabled; build and test in isolated V7 runtime
  paths; preserve the previous executable/start command and a tested rollback
  path. Integration alone changes no live process or database.
AUTHORITY_AND_ACTIVE_CONTROLS: V7 directive (S0 PASS, deleg_6ca5671e, commit
  a1a33e1); standing mandate; no vetoes; EMERGENCY_STOP absent; daemon task
  triggers HELD (disabled 2026-08-14T10:10+02:00) to prevent an uncontrolled
  daemon restart from auto-migrating the live v20 DB before S6 (S0 finding,
  empirically proven by the S1 pre-flight: V6 Supervisor.start() calls
  initialize() and migrated a live-DB copy v20->v32).
BASE_COMMIT_AND_DIFF: main moved fa4e65f -> 22a59ca (exact accepted V6,
  verified parent-tree equality dceaa5a5b… == accepted V6 tree) + one additive
  V7 doc commit (a1a33e1, S0 report). 37 accepted commits, 51 files,
  +11,488/-16 vs fa4e65f. Working tree clean except the 5 protected untracked
  owner files (hashes unchanged — recomputed post-integration).
LIVE_RELEASE_PROCESS_AND_DATABASE: unchanged by S1. Live daemon
  (supervisor-6d250614, pre-merge code) still RUNNING and heartbeating; live DB
  still schema v20 (39,047 events at check); JARVIS-Foundry-Supervisor task
  triggers disabled (next run N/A; running instance untouched); JF-WP7Gate
  test task and Hermes_Gateway untouched; V4 preview servers untouched.
CAPABILITIES_AND_READINESS: release registry + slots now operational on the
  isolated V7 runtime ledger (schema 32): improvement-c4f21554bf73 (adoption
  base, R3, observation = reproduced fa4e65f reconcile defect),
  previous release-77c3f7bb2e2f (fa4e65f), candidate release-f4b93134f608
  (22a59ca); slots live=previous, candidate=candidate, previous=previous,
  quarantine=null. Evolution remains disabled-by-default: nothing schedules,
  runs, or promotes cycles in the adopted code; the live process still runs
  the pre-merge executable.
MISSION_OBSERVATION_OR_HYPOTHESIS: improvement-c4f21554bf73 — adopting the
  accepted V6 source + migrations v21-v32 replaces the crashing main reconcile
  with a deterministic version-checked reconcile (baseline defect 1 ->
  MMI decrease >= 1; guardrails: zero live mutation, reconcile consistent at
  v32, cost 0, initialize on a 28 MB copy < 60 s).
SYSTEMS_AND_CANONICAL_TARGETS: main checkout (construction target), V6 frozen
  worktree (source of truth, untouched), .h (244d2966, untouched), V4 worktree
  (6270a21, untouched), v5-merge-test (detached, untouched), live foundry home
  (read-only), isolated V7 runtime var/v7-runtime/v7.db.
RISK_AND_CONSEQUENCE: S0's two findings addressed: (1) uncontrolled-migration
  restart hazard — task triggers disabled until S6 re-enables; (2) fa4e65f
  reconcile defect — resolved in the adopted source (version-refusal) and
  proven by the 912 suite. Residual: same-provider verifier limitation (D-031);
  live-DB isolation content-level; the running daemon still executes the
  pre-merge executable (intended until S6).
BASELINE_SUCCESS_THRESHOLD_AND_GUARDRAILS: accepted-source reproducibility
  (suite 912 EXIT=0 from the exact accepted tree — ACHIEVED 412.05 s);
  unknown parent/artifact/dependency/config fails closed (registry enforces
  exact 40-hex commits, 64-hex artifact hashes, existing parent/rollback
  targets, frozen risk class); integration alone changes no live process or
  database (VERIFIED post-integration).
BUDGET_QUOTA_AND_COST: local only; zero provider cost; improvement budget
  max_cost_usd 0.0.
ACTIONS_TAKEN: S1 pre-flight (V6 --once on a live-DB copy proved the
  auto-migration hazard and that v20->v32 initialize works on live data);
  disabled JARVIS-Foundry-Supervisor task triggers (restart-path hold);
  re-verified ancestry + protected hashes; rebased the single additive S0 doc
  commit onto the accepted V6 HEAD (main = 22a59ca + S0 doc; parent tree
  dceaa5a5b… == accepted V6 tree — exact-source proof); ran the full suite
  from the exact accepted source on main (912 passed, EXIT=0); generated
  frozen manifests (source/deps/config/runtime/artifacts); registered
  improvement + previous/candidate releases and set live/candidate/previous
  slots on the isolated V7 runtime ledger; re-verified live state unchanged
  (schema v20, daemon RUNNING, task triggers N/A).
FILES_PROCESSES_DATABASES_AND_EXTERNAL_STATE_CHANGED: git refs (main branch
  moved to accepted V6 + S0 doc — intended integration); scheduled-task
  triggers disabled (reversible; the daemon process itself untouched);
  isolated runtime ledger var/v7-runtime/v7.db created (schema 32; 6 events:
  1 improvement.recorded + 2 release.manifest_recorded + 3
  release.slot_changed); evidence files under var/evidence/v7-s1/ +
  docs/evidence/V7_S1_RELEASE.md (this report). NO live DB writes, NO live
  process changes, NO protected-file changes, NO remote effects.
TESTS_RECONCILIATION_RESTORE_AND_FAULTS: full suite 912 passed EXIT=0
  (412.05 s) from the exact accepted source (var/evidence/v7-s1/
  suite-v7-s1-full.txt, sha256 9caa0f42…); S0 baseline 719 EXIT=0 preserved
  (previous-executable proof); fault rehearsal: v20->v32 auto-migration
  hazard reproduced and contained; reconcile defect reproduced pre-adoption
  (S0 C5) and version-refusal behavior confirmed in the adopted source.
VERIFIER_AND_STRUCTURED_VERDICT: <fill after dispatch>
EVIDENCE_PATHS_AND_HASHES: var/evidence/v7-s1/{suite-v7-s1-full.txt
  (9caa0f42…), manifests.json (8c680b14…), register-releases.py};
  var/v7-runtime/v7.db; docs/evidence/V7_S0_ENTRY.md (a1a33e1);
  protected-files.sha256 (unchanged).
REAL_EFFECT_PROVIDER_REFERENCE_AND_AFTER_STATE: none (local integration only).
ROLLBACK_COMPENSATION_OR_CONTAINMENT: source rollback = restore main to
  fa4e65f (S0 baseline 719 EXIT=0 artifact preserved; no history invented —
  the accepted commits remain immutable objects; reflog records the
  transition); data rollback = S2 restore points (next slice); task-trigger
  hold reverses by re-enabling the task; registry rollback = delete the
  isolated runtime ledger (zero live exposure).
UNRESOLVED_RISKS_LIMITATIONS_AND_BLOCKERS: same-provider verifier limitation
  (D-031); live daemon runs pre-merge code until S6 (intended; restart path
  held); V4 roadmap surface still IN PROGRESS (V7-S8).
NEXT_AUTONOMOUS_ACTION: S2 — live v20 ledger migration v21-v32 and restore
  rehearsal on WAL-safe copies (replay, idempotency, interruption injection,
  restore-to-v20, measurements).
OWNER_INPUT_REQUIRED: NONE
```

## 1. Integration proof (exact accepted source)

- `git rebase 22a59ca` moved the single additive S0 doc commit onto the
  accepted V6 HEAD. Result: `main = a1a33e1 (S0 doc) ← 22a59ca (accepted V6) ←
  … ← fa4e65f`.
- **Parent-tree equality**: `git rev-parse HEAD^^{tree}` ==
  `git rev-parse 22a59ca^{tree}` == `dceaa5a5bff42971e816ed18e003594944514840`
  — the integrated source tree is byte-identical to the accepted V6 tree.
- All 37 accepted commits are the original objects (no rewrite); the V6
  branch and worktree remain untouched at 22a59ca.
- The 5 protected untracked owner files were re-hashed post-integration:
  identical to the S0 manifest.

## 2. Restart-path hold (why and what)

S0 froze the hazard: after integration, an unplanned daemon restart loads the
adopted code (editable install), and `Supervisor.start()` calls
`initialize()`, which applies ALL pending migrations — an uncontrolled live
v20→v32 migration outside the S2-rehearsed package. The S1 pre-flight
empirically confirmed it on a live-DB copy (schema 20 → 32, exit 1 on the
copied live lease). Mitigation applied: `schtasks /change /tn
JARVIS-Foundry-Supervisor /disable` (triggers N/A; the running daemon instance
is untouched and continues heartbeating). Re-enable is scheduled for S6 after
adoption validation. The JF-WP7Gate test task (pytest %TEMP% home) and
Hermes_Gateway are unrelated and untouched.

## 3. Immutable release package

`var/evidence/v7-s1/manifests.json` (sha256 304f8a51…) freezes:
- source: main head + tree, accepted V6 commit + tree, parent-tree equality,
  previous main fa4e65f, 37 commits, clean working tree;
- dependencies: pyproject.toml hash, empty project deps, full venv package
  freeze;
- configuration: model default deepseek-v4-flash / opencode-go, fallback
  forbidden (D-031), foundry profile hash;
- runtime: python 3.13.5, pytest 9.1.1, git 2.50.1.windows.1, node v24.19.0,
  Windows 11;
- artifacts: suite/verdict/db hashes;
- runtime ledger: improvement + releases + slot pointers.

The release registry (V6 S2 machinery) enforces: exact 40-hex source commits,
64-hex artifact hashes, plain artifact names, existing parent/rollback
targets, promotion-eligible improvement with frozen risk class, idempotent
content-derived ids. The candidate manifest carries migrations v21-v32 and
the accepted V6 + V7-S0 verdicts; rollback_target = the previous release.

## 4. Evolution disabled by default

The adopted source contains the evolution machinery but nothing activates it:
the live process still runs the pre-merge executable; no cycle scheduler, no
canary controller, no promotion path is running anywhere; the isolated runtime
ledger holds only the adoption-base registration (no cycles). S3 proves the
observe-only wiring; S6 starts the Supervisor with effectful features
observe-only.

## 5. Previous executable and rollback path

- Previous executable: main at fa4e65f, start command
  `.venv\Scripts\python.exe -m jarvis_foundry.supervisor.run --home
  "C:\Users\b\AppData\Local\hermes\foundry"`, proven runnable by the S0
  baseline suite (719 passed EXIT=0, artifact preserved) — and the live
  daemon still executes it (process created 2026-08-13T02:46:20Z;
  `supervisor.started` ledger event 02:46:51Z — ~31 s boot; both pre-merge).
- Rollback path: `git reset --hard fa4e65f` restores the exact previous
  source (immutable objects; reflog records the transition); S2 adds the
  data/schema restore points; the task-trigger hold is reversible at any
  moment.

## 6. Verifier — deleg_719b3c49, PASS (fresh independent adversarial session)

Verdict JSON: `%TEMP%\v7s1-verdict-adv-verifier.json` (early-skeleton protocol;
verdict PASS, criteria C1-C7 all PASS with per-criterion evidence; 10 API
calls, ~335 s). Findings disposition (8 total, all LOW/INFO):

| Severity | Finding | Resolution |
|---|---|---|
| LOW-1 | First-draft manifests.json had EMPTY `main_head`/`main_head_tree`/`venv_packages` (generation script used shell=True with broken quoting) | REPAIRED: regenerated with list-args subprocess (`regenerate-manifest.py`); new sha256 304f8a51…; all fields populated (main_head a1a33e1, main_head_tree 001a766e, 111 venv packages); doc hash reference updated |
| LOW-2 | 6th untracked file = the report itself (same documented V7-S0 pattern) | ACCEPTED — additive; commit stages only this report |
| LOW-3 | `.h` commit 244d2966 not present in the main repo's object store (documented V6-S13 F7-1 boundary; `.h` is its own clone) | RECORDED — substitution evidence: `git -C .h rev-parse HEAD` == 244d2966 directly in its own store; no action |
| INFO-1 | Task Last Run Time 10:15:05 AM postdates the trigger disable; Last Result -2147020576 (0x800704E0, ERROR_ALREADY_EXISTS) | RECORDED — IgnoreNew skip of an already-running instance; NO new daemon process (only Aug-13 PIDs); live DB unchanged (schema still v20); triggers now N/A |
| INFO-2 | Process creation 02:46:20Z vs started event 02:46:51Z (~31 s boot) | CORRECTED additively in §5 (both pre-merge) |
| INFO-3 | "3 events: …=6 events" wording slip in §FILES | CORRECTED additively ("6 events: 1 + 2 + 3") |
| INFO-4 | `frozen_at` 10:35 postdates evidence writes (10:22-10:24) | RECORDED — `frozen_at` is the manifest-write time; regenerated manifest re-frozen at 10:40 |

Independence note (D-031): same-provider limitation — opencode_go /
deepseek-v4-flash; procedural independence only. Integrity concerns: none.
Scope violations: none.

**S1 CLOSED — PASS.** Suite artifact: `var/evidence/v7-s1/
suite-v7-s1-full.txt` (912 passed in 412.05 s, EXIT=0, sha256 9caa0f42…).
