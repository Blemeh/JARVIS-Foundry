# Operational Slice — Initial State Evidence

Lane: candidate/overnight-v3-operational-slice
Recorded at lane start (2026-08-22/23 overnight window).

## Anti-drift verification

| Item | Value | Verified by |
|---|---|---|
| origin/main after fetch --prune | `ceb90d7d201b4775757cae0f7e03ae1e2d6f22e2` | `git rev-parse origin/main` |
| Expected base matches prompt | YES (exact) | this file |
| Worktree path | `C:\temp\JARVIS-Overnight-V3-Operational-Slice` | created fresh; did not previously exist |
| Branch | `candidate/overnight-v3-operational-slice` | created fresh @ ceb90d7 |
| Local HEAD of worktree | `ceb90d7d201b4775757cae0f7e03ae1e2d6f22e2` | `git rev-parse HEAD` |
| Worktree status at creation | clean (0 modified paths) | `git status --porcelain=v1` |

Note: first `git worktree add` attempt used an MSYS-style path and git
created `C:/c/temp/JARVIS-Overnight-V3-Operational-Slice`. That misplaced
worktree was removed (`git worktree remove --force`) and the stray
directory deleted before recreating at the correct native path. No other
session's data touched.

## Environment

| Item | Value |
|---|---|
| Host OS | Windows 11 (git-bash shell) |
| Python (host) | 3.11.15 |
| uv | installed; lane-local venv `.venv` created in worktree |
| Lane venv install | `uv pip install -e .` → jarvis-foundry==0.1.0 + pyyaml |
| Node/UI | ui/command-center (Vite/vitest); UI commands run via pnpm/npm scripts in ui/command-center |
| Schema constant | `SCHEMA_VERSION = 36` (src/jarvis_foundry/migrations.py:8) |
| Feature flag default | `WorkerRuntimeConfig.discovery_cycle_enabled = False` (runtime/config.py:49); parsed from YAML `discovery.cycle_enabled` (config.py:104-105). NO durable ledger surface for the flag exists at base. |

## Current adapter behavior at base

- `ui/command-center/src/lib/realClient.ts:214-219`: `getDiscovery()` returns
  `{ available: false, reason: "PROJECTION_NOT_AVAILABLE" }` unconditionally,
  with a comment pointing at candidate/v3-integration @701aa74.
- Backend `build_snapshot` (command_center.py:271-459) has NO `discovery`
  section. Confirmed by reading full function.

## WP-B boundary check at base (source-verified)

- `runtime/worker_loop.py:491-498`: after terminal mission result:
  - PASSED → `_advisory_accepted_outcome_writeback` (Tier-C memory observation only)
  - PASSED/FAILED/BLOCKED → `_record_executive_outcome` (executive calibration only)
- NOTHING calls `v3_outcomes.record_outcome` anywhere in src/ (grep over
  `record_outcome|discovery.candidate_outcome_recorded`: only definitions +
  projections.py projection handler + discovery_candidates legacy variant).
- Conclusion: automatic post-dispatch outcome closure is MISSING at base.
  The only outcome recording is manual/lab-invoked. To be proven with a
  failing integration test before implementing (WP-B step 2).

## Live machine

Not accessed. No live DB reads or writes. Production untouched.
