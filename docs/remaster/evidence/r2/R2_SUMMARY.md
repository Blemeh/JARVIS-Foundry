# R2 Forensic Summary - Post-V7 JARVIS Remaster

Baseline: `93a3ddc0965dfaf3e4cb9dc0952467bd5250520a` (tree `2e1df6190b0d032372ae46e6a59bef22ddac57e3`), main checkout, read-only audit.
Scope: 99 source modules, 88 test files, 397 tracked files.
Findings: 30 total (9 S1, 12 S2, 9 S3). Status: forensic inventory only; no repair performed.

## Repository shape

- Source: 19573 lines (17037 code) in 99 modules.
- Tests: 21747 lines (17454 code) in 88 files; 920 test functions.
- Functions: 1,860 total; 18 functions are 81-160 lines and 6 exceed 160 lines. The largest is `projections.apply_event` at 1,669 lines.
- Exception handling: 123 handlers; 29 `except Exception`, 9 `except BaseException`, 0 bare `except`.
- Typing: 1,440 of 1,701 parameterized functions are fully annotated; 401 are untyped; no mypy/lint/coverage/mutation gate exists.
- Markers: 0 TODO/FIXME/XXX/HACK occurrences.

## Top structural risks

1. **Unreachable executive** (`R2-F014`, S1): the installed path ends at the Supervisor heartbeat watchdog
   (`cli/main.py:48-113`, `installer.py:73-74`, `supervisor/service.py:34-65` start and `67-90` heartbeat, `supervisor/run.py:121-186`).
2. **Parallel authority/state writers** (`R2-F007`, S1): three mission/execution paths, two promotion controllers
   (`evolution/registry.py:214-277` vs `evolution/promote.py:168-222`), and three capability registries.
3. **Replay loses state** (`R2-F012`, S1): 41 of 112 created event types have no projection, and
   `rebuild_projections` (`database.py:104-112`) silently skips them.
4. **No immutable ledger** (`R2-F011`, S1): zero foreign keys/triggers and no event-header hash chain
   (`migrations.py:13-31`, `events.py:73-82`).
5. **Editable live source** (`R2-F009`, S1): the scheduled task imports mutable main `src` through the editable
   `.pth`; no immutable release pointer exists.

## Mandated resolutions

- Projection dispatcher: `src/jarvis_foundry/projections.py` is 1,740 lines; `apply_event` spans 58-1726;
  unknown events fall through without error (`R2-F001`).
- Top-20 functions by size: recorded in `R2_METRICS.json` and `R2_FORENSIC_FINDINGS.json` (`R2-F002`).
- Broad exception handling: all sites with paths/lines in `R2-F003`.
- Quality gates: absent; `pyproject.toml` has pytest only and no CI config exists (`R2-F004`).
- Fixture-as-live surface: voice fixture providers, `local-test-sink` delivery, and dead `FakeConnector` (`R2-F005`, `R2-F020`).
- Never-invoked code: 17 executive entry points with no caller reachable from the installed watchdog path;
   the only references are tests (311 word-boundary / 344 substring test-side references measured by the frozen script scripts/remaster/r2/measure_test_references.py (evidence: R2_TEST_REFERENCE_COUNTS.json)) and call sites inside other
   unreachable executive modules (`R2-F006`).
- Duplicated transitions: mission, promotion, and capability authority enumerated (`R2-F007`).
- Runtime/live switch: env-ordered `FoundryPaths` with no identity pin; LAB-vs-TRUSTED contradictions (`R2-F008`, `R2-F010`).
- Editable source drift: `.venv` `.pth` -> mutable main `src` (`R2-F009`).
- Planning contradictions: `PROJECT_STATE.md`/V7-S13 claims vs live LAB truth (`R2-F010`).
- State/schema/migration/replay: no FK/triggers/header chain; 32 additive migrations with destructive rebuilds (`R2-F011`, `R2-F023`).
- Secrets/target identity: credential flow ends at identifiers; caller-supplied worker/worktree not bound (`R2-F013`).
- Test independence: tmp_path-heavy but env/subprocess/sleep/Task-Scheduler-coupled; R1-F003/F004 regressions preserved (`R2-F024`).
- Operational reachability: every point where the live path dead-ends is listed in `R2-F006`/`R2-F014`.

## Disposition summary

The R0 disposition map was applied per module in `R2_MODULE_INVENTORY.json` (KEEP/REFACTOR/REWRITE/DELETE/
NOT_IMPLEMENTED/UNSAFE). Ledger, projections, mission/execution, capability, scheduler, portfolio, and evolution
release controllers are REWRITE/UNSAFE; watchdog STOP and control primitives are KEEP+HARDEN or REFACTOR; tests are
KEEP with reclassification; fixture adapters are NOT_IMPLEMENTED/BLOCKED_EXTERNAL.

## Outputs

- `docs/remaster/evidence/r2/R2_FORENSIC_FINDINGS.json`
- `docs/remaster/evidence/r2/R2_MODULE_INVENTORY.json`
- `docs/remaster/evidence/r2/R2_METRICS.json`
- `docs/remaster/evidence/r2/R2_SUMMARY.md`
