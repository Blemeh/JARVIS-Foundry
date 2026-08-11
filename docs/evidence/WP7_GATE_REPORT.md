# WP7 Gate Report — V1 Integration And Activation

- **Date:** 2026-08-11 (continuation run)
- **Verdict:** **PASS**
- **Package:** WP7 (V1 integration and activation)
- **Previous state:** ACTIVE / NOT_TESTED (implementation complete, unverified)

## 1. Verdict Table (acceptance criteria F1–F7 from CURRENT_WORK_PACKAGE.yaml)

| ID | Criterion | Verdict | Evidence |
|----|-----------|---------|----------|
| F1 | Clean install → disabled state → activation → restart → STOP → resume → uninstall reproducible (gate) | **PASS** | Independent staging lifecycle on this host (round-2 verifier, 402-task host): install (schtasks registered, XML verified: LogonTrigger/Hidden/IgnoreNew/RestartOnFailure PT1M×3/absolute python/`--home`) → deactivate (Disabled) → activate (Ready) → daemon RUNNING → restart RUNNING → STOP sentinel FROZEN → resume RUNNING → CLI stop/resume → uninstall gone. Ledger exact (started=5, frozen=2), 0 leases left, temp XML cleaned |
| F2 | Installer touches ONLY its own task name and profile paths | **PASS** | Host task inventory diff: only the staging task appeared/disappeared; before == after (402 tasks); installer wrote only task + `foundry-profile.yaml` + temp XML (deleted) + own-DB events; user files survived uninstall |
| F3 | Installed Supervisor runs without Hermes; STOP/freeze independent | **PASS** | Import-graph check: no Hermes imports in the daemon path (grep hits are docstrings only); sentinel freeze + CLI stop/resume proven on a headless `--once` daemon with no Hermes running; `emergency_stop.frozen` events recorded |
| F4 | Restart after activation reconciles; no duplicate actions | **PASS** | Second daemon cycle RUNNING with 0 leases after exit (lease released), single canonical supervisor_state row, exact event counts, reconcile consistent, no duplicate-worker signal; daemon also runs against a `&`-containing home |
| F5 | Uninstall removes task + profile; idempotent; other files untouched | **PASS** (round 1 FAIL, repaired) | Round-1 crash (uninitialized Foundry home → `sqlite3.OperationalError: no such table: events`) reproduced and fixed: `Installer.uninstall()` self-heals via `database.initialize()`; CLI `jarvis uninstall --home <fresh>` exits 0; activate/deactivate on uninitialized DB refuse cleanly; double uninstall idempotent; profile removed, user files/tasks untouched |
| F6 | Real installation only after gate PASS | **PASS** | Real name `JARVIS-Foundry-Supervisor` refused with no verdict file / FAIL verdict / malformed JSON (API + CLI `install --v1`), real task never created; allowed only with `{"verdict":"PASS"}` |
| F7 | Gate runs in staging names first | **PASS** | Entire gate reproduced under staging names (JF-Verify-*, JF-WP7Gate-*, JF-R2*-*); real V1 name touched only in refusal probes; final schtasks query: zero jarvis/foundry/JF- tasks on host |

## 2. Independent Verification (two rounds)

- **Round 1 — deleg_9c19e04a** (fresh adversarial session, 44-check lifecycle
  script + live-daemon probes): 6/7 PASS, **FAIL on F5** — uninstall/activate/
  deactivate on an uninitialized Foundry home crashed with
  `sqlite3.OperationalError: no such table: events` (reproduced 3 ways:
  direct API, CLI, set_enabled). Plus hardening observations (XML escaping,
  DEGRADED silently ignored). Summary:
  `subagent-summary-0-20260811_230823_045734.txt`.
- **Round 2 — deleg_efb7bafc** (fresh adversarial session): **PASS 7/7** —
  all three round-1 defects reproduced **fixed** (F5 crash shape: no raise,
  clean status, event recorded; XML `&amp;` escaping with real registration;
  DEGRADED → exit 1, no lease), full lifecycle re-run, scope + guard
  re-verified, no new defects. Summary:
  `subagent-summary-0-20260811_231941_931961.txt`.
- Independence limitation (§14.3): both verifiers ran under the same provider
  family as the builder (opencode-go / deepseek-v4-flash); fresh isolated
  sessions, all evidence reproduced from scratch.

## 3. Findings And Repairs

| Finding (round 1) | Severity | Repair | Regression test |
|-------------------|----------|--------|-----------------|
| **F5** — uninstall/activate/deactivate on an uninitialized Foundry home crashed (`no such table: events`); shipped CLI path exit 1 | medium | `Installer._append` self-heals with `database.initialize()` | `test_uninstall_works_on_uninitialized_database` (direct API + shipped CLI) |
| Task XML unescaped — a path with `&`/`<`/`"` yields malformed or injectable XML (operator-controlled values only) | defense-in-depth | `xml.sax.saxutils.escape` on all path values | `test_task_xml_escapes_special_characters` (`&`-home: `&amp;` in XML + real task registers) |
| `supervisor.start()` DEGRADED result silently ignored (heartbeats + holds lease while inconsistent) | low | daemon exits 1 on DEGRADED, no heartbeat, no lease | `test_daemon_exits_nonzero_on_degraded` (corrupt projection → exit 1, DEGRADED printed) |

**Round-1 non-blocking observations (documented, no code change):**
lookalike task-name guard bypass (CLI only passes `--v1` or explicit names);
English-only `Status:` parsing (locale-dependent); hard-kill lease window up
to 90 s with 3× PT1M retry self-heal (designed lease semantics); `schtasks
/query /xml` UTF-16 oddity; CLI raw tracebacks on refusal (functional).

**Spec deviations (documented):** Task Scheduler XML schema has no
EnvironmentVariables node → the deterministic environment is the explicit
absolute `--home` argument (HERMES_HOME recorded in the profile); restart
backoff is a uniform PT1M × 3 (Task Scheduler cannot express the spec's
1/5/15 escalating intervals).

## 4. Executive Re-Verification (fresh commands, final frozen tree)

- `./.venv/Scripts/python.exe -m pytest tests/foundry -q --tb=short` →
  **193 passed** (exit 0), after repairs and inside the freeze battery.
- Hermes baseline (isolated HERMES_HOME): `tests/test_estop.py` → **24 passed**.
- Canonical DBs: all 4 re-hashed → unchanged (full recheck at freeze).
- Host state: zero JARVIS/Foundry scheduled tasks after the gate (all staging
  tasks removed by the tests and both verifiers).
- Coordination note: an initial git commit (`c2770ef`, 23:00:37,
  "checkpoint: WP7 integration activation in progress") appeared during
  round-1's window, authored with the machine's git identity; not created by
  this session and not authorized — harmless snapshot of the then-current
  tree, no effect on file-hash freeze manifests; recorded for the owner.

## 5. Frozen-State Snapshot (var/evidence/wp7-gate/, frozen AFTER all repairs)

| Artifact | sha256 |
|----------|--------|
| verified-state-snapshot.json | `c1b29c22fbfe2994ee0c3c33862d3b5afacd3fa7f6c5b3e5c2c581757fb50ee5` (68 files; refreshed after the CLI fix) |
| foundry-tests-final.txt | `f843372a7767b057ea66a117271b80809e487d3f360520176f7831df96e01f4d` |
| hermes-baseline-estop-final.txt | `8bd86bdbdf2ee615ca8ce407e88003870e82ccfae7cd3d7d1ddca9020dbc80d7` |
| canonical-databases-recheck.json | `399ab6bb23147887712990bae0b1625ef70f1e951882df479c3b715763dc0710` (identical across all gates) |
| canonical-databases-full-recheck.json | `ab638838c9ef36459a09da1355e955b8588174e6a30285c3912e3215dbbaea0c` |

Summary artifact: `verification-summary.json` sha256
`5064d2aa05a7b1c4690d616642fcb7b229cfbd5554aacefae1635826e69f1e07`

Post-freeze note: the activation path exposed one more pre-existing CLI bug
(`--home` SUPPRESS → bare `jarvis <cmd>` crashed with AttributeError); fixed
with a regression test and the freeze was refreshed (194 tests). The
GATE_VERDICT.json latch was written after the refresh and used by the
activation step below.

## 8. Activation Record (after gate PASS)

Per the START BUILD authorization, the real V1 Supervisor was installed and
activated at 2026-08-11 ~23:26:

- `var/evidence/wp7-gate/GATE_VERDICT.json` written (verdict PASS latch).
- `jarvis install --v1` → task `JARVIS-Foundry-Supervisor` registered
  (Ready), profile at `C:\Users\b\AppData\Local\hermes\foundry\foundry-profile.yaml`.
- `jarvis activate --v1` → enabled.
- `schtasks /run` → started; verified Running; daemon holds the
  `supervisor:singleton` lease, heartbeats every 30 s, `supervisor_state`
  RUNNING, 3 durable events (started + heartbeats).
- The task runs `python -m jarvis_foundry.supervisor.run --home
  "C:\Users\b\AppData\Local\hermes\foundry"` with the repo venv python
  (absolute path; a repo move requires reinstall — recorded residual risk).

## 6. Residual Risks (recorded, not gate-blocking)

- Lookalike task-name guard bypass; locale-dependent status parsing; 90 s
  lease window after hard-kill; raw CLI tracebacks — all documented above.
- The scheduled task runs `python -m jarvis_foundry.supervisor.run` with the
  repo venv's python (absolute path). A repo move breaks the task until
  reinstall — recorded; the uninstaller always works (schtasks-based).
- Same-provider independence limitation (§14.3).
- Repo has exactly one git commit (c2770ef, unowned snapshot); frozen-state
  = sha256 manifests, unaffected by git metadata.

## 7. Conclusion

The gate failed once, honestly: round-1 adversarial verification found a real
shipped-path crash (F5) plus two hardening gaps. All three repaired with
regression tests and re-verified by a fresh adversarial session (round 2,
PASS 7/7, no new defects). The full lifecycle — install, disabled state,
activation, restart, STOP, resume, uninstall — is reproducible on this host,
scoped to the installer's own task and profile, Hermes-independent, and the
real V1 task name stays guarded by the gate verdict. WP7 declared **PASS**;
per the START BUILD authorization, the activation step follows: the real V1
Supervisor task is installed and enabled, and WP8 (seven-day acceptance) is
activated.
