# WP8 F8 — Disk Almost Full (Bounded Quota Simulation)

Status: **PASS** (2026-08-12; live staged experiment)
Gate: `docs/architecture/04_V1_ACCEPTANCE_GATE.md` §7 F8

## 1. Criterion

> F8 — Disk almost full. Use an isolated bounded volume or quota
> simulation. Do not fill the owner's real system drive. Pass condition:
> new builds stop, state commits safely, no broad cleanup occurs, and the
> exact condition is reported.

## 2. Mechanism (honest environment facts)

- A real VHD volume requires admin rights: verified **NO_ADMIN** on this
  host (`net session` fails) — recorded limitation.
- SQLite's internal full-disk pragmas (`max_page_count`,
  `journal_size_limit`, `wal_autocheckpoint`) were empirically probed and
  do NOT fire on this host's forced-WAL configuration (the WAL absorbs
  writes; a failed autocheckpoint is ignored) — recorded environment
  finding.
- The gate's allowed "quota simulation" arm is therefore delivered at the
  storage boundary of the ISOLATED staging home: `QuotaShimDB`, a
  `FoundryDatabase` whose connections use a subclassed
  `sqlite3.Connection` enforcing a zero-byte write budget, raising the
  exact SQLITE_FULL error a full disk produces (`database or disk is
  full`). Reads/PRAGMAs stay functional; the real transaction machinery
  rolls back atomically.

## 3. Injection and Results

`var/evidence/wp8-gate/f8/run_f8_experiment.py` — snapshot copy of the
live DB (live DB/daemon never touched):

| Phase | Injection | Actual | Verdict |
|---|---|---|---|
| A | control, healthy | append ok; real dispatch COMPLETED (8.4 s) | PASS |
| B | quota = 0 bytes | append **raises** `sqlite3.OperationalError: database or disk is full`; events unchanged (no partial row); quick_check ok; **dispatch blocked** (0 started/0 completed rows, 0 leases) — new builds stop; **zero file deletions** (no broad cleanup); REAL `Supervisor.start()` blocked by the same error (the daemon entry has no try around start → non-zero exit with the condition reported → F1/F4-proven bounded restart machinery) | PASS |
| C | quota lifted | append ok; fresh real dispatch COMPLETED (8.4 s) — builds resume | PASS |

While-RUNNING disk-full behavior is covered by the exception-isolated
heartbeat loop (FIND-01 machinery; `_record_degraded` never raises; when
even the degraded event cannot append, `DEGRADED_RECORD_FAILED` goes to
stderr — the exact condition is always reported).

## 4. Live-System Protection

Live DB read-only (snapshot); live daemon untouched (task + heartbeat
verified after the experiment); real drive never filled; staging home
isolated.

## 5. Residual Risk

- Real OS-volume behavior (VHD) NOT_TESTED — admin dependency recorded;
  the SQLITE_FULL write path is byte-identical to what a full disk
  produces at the SQLite layer.
- Evidence: `var/evidence/wp8-gate/f8/` (script + summary + staging).
