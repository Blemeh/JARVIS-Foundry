# V7-S2 — LIVE V20 LEDGER MIGRATION AND RESTORE REHEARSAL

```text
STATUS: PASS — CLOSED (independent verifier deleg_237a2435, structured PASS)
PHASE: V7_FULL_AUTONOMOUS_OPERATION
SLICE: S2 — live v20 ledger migration v21-v32 and restore rehearsal
OBJECTIVE: prove, on FRESH WAL-safe copies of the ACTUAL live database only,
  that the v20 -> v32 migration replays in order with repeat idempotency,
  schema/indexes/constraints/projections/event-integrity/reconcile all verify,
  interruption at every migration boundary recovers, compatibility assumptions
  with the previous executable hold, complete data/schema/config restore points
  restore to equivalent v20 truth, and time/disk/memory/WAL/downtime
  requirements are measured. The live database remains untouched.
AUTHORITY_AND_ACTIVE_CONTROLS: V7 directive; S0 + S1 closed PASS
  (deleg_6ca5671e, deleg_719b3c49); standing mandate; no vetoes; live STOP
  sentinel absent; daemon task triggers still held (S1 control, released at S6).
BASE_COMMIT_AND_DIFF: main 6557232 (S1 close). No source changes in S2 —
  rehearsal-only slice; all work on disposable copies under %TEMP% + evidence
  under var/evidence/v7-s2/.
LIVE_RELEASE_PROCESS_AND_DATABASE: untouched. Live daemon still RUNNING
  (pre-merge code), live DB still schema v20 (~39.2k events and growing),
  task triggers N/A, V4 previews untouched.
CAPABILITIES_AND_READINESS: migration path v20->v32 proven on real live data:
  DDL-only, 0.018 s, idempotent, interruption-safe at every boundary, with
  the correct sidecar-aware restore procedure. The governed restore machinery
  (create/verify/restore_from_point) proven at schema 32.
MISSION_OBSERVATION_OR_HYPOTHESIS: improvement-c4f21554bf73 (adoption base);
  this slice supplies the rehearsed migration/restore package S6 will execute
  on the live ledger.
SYSTEMS_AND_CANONICAL_TARGETS: live foundry home (mode=ro only), disposable
  copies, var/v7-runtime ledger, main src at 6557232.
RISK_AND_CONSEQUENCE: two real findings turned into procedure:
  (1) naive file-copy restore over a WAL-mode DB does NOT restore — stale
  -wal/-shm sidecars keep the migrated state (first rehearsal run: restored
  schema stayed 32); the correct procedure (close connections -> checkpoint ->
  delete sidecars -> replace main file) restores exact v20 truth (S5a PASS);
  (2) the previous (fa4e65f) executable on a migrated copy: initialize is a
  no-op and its reconcile currently returns consistent (0/0/0 mismatches)
  because no V6-vocabulary events exist yet — benign TODAY, but the moment
  the adopted Supervisor records evolution events, the old executable must
  NEVER run against the migrated ledger (its projection rebuild cannot
  understand v32 tables) — S6 therefore terminates the old daemon BEFORE the
  migration and rollback = v20 snapshot restore + old executable TOGETHER.
BASELINE_SUCCESS_THRESHOLD_AND_GUARDRAILS: repeated migration + full
  restoration PASS on actual-live copies (achieved); live DB untouched
  (re-verified post-rehearsal); every comparison against the copy's OWN
  snapshot count (the live DB grows continuously — heartbeats).
BUDGET_QUOTA_AND_COST: local only; zero provider cost.
ACTIONS_TAKEN: wrote and ran var/evidence/v7-s2/rehearse-migration.py
  (scenarios 1-6) and supplemental-probes.py (deep integrity, old-executable
  compatibility, config restore points); first rehearsal run exposed the WAL
  sidecar trap + a leaked-connection file lock (Windows: `with
  sqlite3.Connection` commits but does not close) — fixed, re-ran clean
  (EXIT=0); captured task XML + foundry-profile hashes.
FILES_PROCESSES_DATABASES_AND_EXTERNAL_STATE_CHANGED: none outside var/
  evidence + %TEMP% copies; live state untouched (verified).
TESTS_RECONCILIATION_RESTORE_AND_FAULTS: see rehearsal-report.json +
  supplemental-report.json (all PASS): full replay v20->32 (0.018 s, events
  preserved, v4 tables present, reconcile consistent, quick_check ok,
  integrity 39,245 events); re-initialize no-op (0.0018 s); 12/12 boundary
  interruptions recovered (partial schema k -> full 32, events preserved);
  mid-version interruption (migration 26) rolls back all-or-nothing; new
  executable on v20 refuses (version mismatch); old executable on v32 no-op
  (benign now / prohibited later); v20 snapshot verified (sha256 557a0909…)
  and restored exactly (schema 20, events == snapshot, quick_check ok);
  governed restore at v32 restores the exact mutated-slot state; deep
  integrity ok (51 tables, 77 indexes, foreign_key_check clean); config
  restore points hashed (foundry-profile 732d3040…, task XML byte-exact
  ea615b9d… — see F1 repair in §4).
VERIFIER_AND_STRUCTURED_VERDICT: <fill after dispatch>
EVIDENCE_PATHS_AND_HASHES: var/evidence/v7-s2/{rehearse-migration.py,
  rehearsal-run.txt, rehearsal-report.json, supplemental-probes.py,
  supplemental-report.json, task-JARVIS-Foundry-Supervisor.xml}; live DB
  untouched; V6 suite artifacts unchanged.
REAL_EFFECT_PROVIDER_REFERENCE_AND_AFTER_STATE: none (local rehearsal).
ROLLBACK_COMPENSATION_OR_CONTAINMENT: S2 changes nothing live; the rehearsed
  S6 procedure now includes: pre-migration v20 snapshot (WAL-safe, sha256),
  sidecar-aware restore, config restore files, and the rule that the old
  executable never runs against the migrated ledger.
UNRESOLVED_RISKS_LIMITATIONS_AND_BLOCKERS: same-provider verifier limitation
  (D-031); old-executable-on-migrated-schema must remain prohibited post-S6;
  memory estimate not directly measured (migration is DDL-only; peak RSS of
  the rehearsal process is the practical bound).
NEXT_AUTONOMOUS_ACTION: S3 — Supervisor adoption wiring in the isolated
  schema-32 runtime (observe-only; STOP/veto/idempotency/fault proofs).
OWNER_INPUT_REQUIRED: NONE
```

## 1. Rehearsal results (all PASS)

| Scenario | Result |
|---|---|
| 1 full replay v20→v32 | 0.018 s; 39,245 events preserved; 32 migration rows; v4 tables present; quick_check ok; reconcile consistent |
| 2 repeat idempotency | schema/rows/events stable; second initialize 0.0018 s |
| 3 interruption at every boundary (20..31) | 12/12: partial schema k → full recovery to 32, events preserved |
| 3b mid-version interruption (migration 26) | rolled back to 25; partial table absent; full recovery; events preserved |
| 4a new executable on v20 | version refusal (fail-closed, no crash) |
| 4b old executable on v32 | initialize no-op; schema/events unchanged; quick_check ok |
| 5a v20 restore (sidecar-aware) | snapshot verified (sha256 557a0909…); restored schema 20, events == snapshot, quick_check ok, integrity 39,251 |
| 5b governed restore at v32 | point verified; mutated slot restored exactly; 39,251 events returned |
| 6 measurements | migration 0.018 s; downtime ≈ backup + reconcile (~seconds); live 28.3 MB → migrated 28.5 MB; WAL sidecars present |

## 2. Findings turned into procedure (the value of this slice)

1. **WAL sidecar restore trap** — a file-copy restore over a WAL-mode DB
   leaves stale `-wal`/`-shm` sidecars that win over the restored main file
   (first rehearsal: restored schema stayed 32). The correct procedure —
   close all connections, `PRAGMA wal_checkpoint(TRUNCATE)`, delete sidecars,
   replace the main file — restored exact v20 truth. S6 uses this procedure
   for the pre-adoption rollback path.
2. **Leaked-connection file lock** — Python's `with sqlite3.Connection`
   commits but does not close; on Windows the leaked WAL connection holds the
   `-wal` file lock (PermissionError). Rehearsal code now closes explicitly.
3. **Old-executable compatibility** — benign today (its reconcile passes on a
   migrated copy because no V6-vocabulary events exist), prohibited after the
   first evolution event. S6 terminates the old daemon before migrating; the
   only rollback is snapshot restore + old executable together.

## 3. What S6 will execute (the rehearsed package)

1. Freeze mission dispatch and external effects.
2. Capture live state (executable hash, DB, WAL, config, task XML, leases,
   health).
3. Create + independently verify: v20 snapshot (WAL-safe), config restore
   files, task XML.
4. Terminate the old daemon (console control handler → durable stop).
5. Migrate the live ledger through initialize() (the S2-rehearsed path).
6. Start the adopted Supervisor (evolution observe-only).
7. Validate STOP/veto, leases, heartbeats, schema 32, event integrity,
   reconcile, health; verify the previous-release restore path.
8. Re-enable the scheduled task; resume previously safe operation.

## 4. Verifier — deleg_237a2435, PASS (fresh independent adversarial session)

Verdict JSON: `%TEMP%\v7s2-verdict-verifier.json` (early-skeleton protocol;
verdict PASS, criteria C1-C6 all PASS with per-criterion evidence; 11 API
calls, ~353 s). Findings disposition:

| Severity | Finding | Resolution |
|---|---|---|
| LOW-MED (F1) | task XML hash `8a1f45ca…` bound only the LF-normalized in-memory string; the archived file hashed `5316c816…` (CRLF write translation) and the live output differed again; file self-declared UTF-16 while being UTF-8 | REPAIRED: the task XML is now archived BYTE-EXACT from `schtasks /xml` raw output (tool emits UTF-8 bytes with a self-declared UTF-16 header — archived verbatim, 1745 bytes); hash `ea615b9d…` binds the exact bytes (re-read verified); supplemental-report.json updated (new hash 08792854…); S6 restores these exact bytes |
| INFO (F2) | port 8765 has two listeners (PIDs 9268 + 3720) | RECORDED — matches S0 §4 (two preview processes on 8765); untouched |
| INFO (F3) | artifact event counts 39231 < 39245 < 39291 consistent with live growth + timing order | RECORDED — per-copy baselines make the comparisons valid |

Independence note (D-031): same-provider limitation — opencode_go /
deepseek-v4-flash; procedural independence only. Integrity concerns: F1 was
the only artifact-level hash-binding defect; repaired. Scope violations: none.

**S2 CLOSED — PASS.** The live database remains untouched (schema v20, daemon
RUNNING, task triggers held). The rehearsed S6 migration/restore package is
frozen in this slice's artifacts.
