# Command Center Owner-Experience Acceptance (Task 9)

**Date (UTC):** 2026-10-08
**Branch:** `owner-experience-rescue-20261008`
**Base:** `0d79ec6d` (Tasks 1–8 complete)
**Verdict: CONDITIONAL — browser-pixel journeys UNRESOLVED (environmental blocker, no fabrication).**
All live discipline rules held: no Supervisor / worker-loop / scheduler start, no
owner-command POSTs (dock LIVE mutation paths unclicked), no database writes, no
CURRENT/PREVIOUS/release-pointer/trust/scheduler touches.

## 0. Live backend provenance (read-only)

Reads served ONLY via `scripts/serve_command_center_api.py` (bounded run, stopped afterwards).

| Field | Value |
|---|---|
| `--port` | `18765` (`8765` bind denied: `PermissionError WinError 10013`; see §8) |
| Release id | `rel-4cac1948ce11-c18ad1c1` |
| Release current | `4cac1948ce115d75225fdfab929ae57aaecbbbb5` |
| Release previous | `0bc96b7047705bd3f8c2c17839d1192316022d1f` |
| Schema | live `41` / package `40` (mismatch → `/api/health` `DEGRADED`, `ready: false`) |
| Trust | `SUSPENDED` (missing acceptance evidence; permitted work still allowed) |
| Supervisor | `RUNNING` (pid 29796, pre-existing; never started/stopped here) |
| DB | `C:\Users\b\AppData\Local\hermes\foundry\foundry.db` (`mode=ro` + GET only) |
| Snapshot | `GET /api/command-center` → 6.7 MB, 66 missions, `generated_at` 2026-10-08T11:06Z and 11:18Z |

Live truth served (both fetches identical for owner-relevant rows; ledger head
advanced 402439 → 402494 from the running Supervisor — ambient appends, not this task):

| Record | State (served) |
|---|---|
| `owner-delegation-643042f8f84795e7de8b3d79a4b7142c` (Hoowen DELEGATE) | `active = True` |
| `mission-08a548a685ae` | COMPLETE 2026-10-06T14:00 |
| `mission-62250d335ab6` | COMPLETE 2026-10-06T15:34 (follow-up 2) |
| `mission-fe81cb0d1e37` | BLOCKED 2026-10-06T15:50 (follow-up 3) |
| `mission-4646c7023b05` | BLOCKED 2026-10-04 (CI_FAILURE, historical) |

## 1. Gate summary (each command attempted EXACTLY as mandated)

| Check | Result | Evidence (one line) |
|---|---|---|
| `npx tsc -b --noEmit` | **PASS** | exit 0, strict + `noUnusedLocals`, covers `src` (not `e2e/`/`scripts/`) |
| `npm run build` | **BLOCKED** | `tsc -b` half passes; `vite build` dies `Error: spawn EPERM` from `esbuild/lib/main.js`, 0 bundles |
| `npx vitest run` | **BLOCKED** | dies loading `vite.config.ts` with `spawn EPERM`, 0 tests ran |
| `npm run verify:product` | **BLOCKED** | exit 1 `Preview server not ready at http://localhost:4524`, 0 checks ran (Vite spawn blocked) |
| `node scripts/capture-remaster.mjs` | **BLOCKED** | `browserType.launch: spawn EPERM` (chromium_headless_shell present on disk, spawn denied) |
| `npm run e2e` | **BLOCKED** | `Playwright server did not become ready on port 62253` |
| `npx eslint e2e/live-autonomy-proof.spec.ts scripts/product-verify.mjs` | **PASS** | exit 0, `--max-warnings 0` (covers the 2 Task 9 edits `tsc` cannot see) |
| `node --check scripts/product-verify.mjs` | **PASS** | syntax OK |
| Interim harness (below) | **201/202** | real components + real fixtures under jsdom, plain-node runner (no esbuild) |

No escalation was available (approver-less session: approval prompts disabled), so
the EPERM retry path fails closed. Zero screenshots fabricated: `dist/` is a
2026-10-06 stale bundle WITHOUT the Task 4 tracker (no `md-compact-tracker` in its
assets), so it cannot stand in for this branch's UI.

## 2. Interim evidence (honest, reproducible): 201/202 PASS

`vitest` cannot load its config here, so the COMMITTED suites were executed with a
plain-node ESM harness kept OUTSIDE the repo (temp dir, nothing committed):
typescript-compiler transpile only (no esbuild), real `expect` from the installed
`vitest` dist, real `@testing-library/react` + `user-event` + `jest-dom` under
jsdom, REAL components + REAL committed fixtures. 202 unique tests, 201 pass:

- MissionDetail 17/17 — primary order, 5-stage tracker render, historical banner
  for `mission-4646c7023b05`, HALT hidden for terminal historical / shown for
  executing in LIVE, halt transport accept/reject.
- Overview 10/10 — Hoowen goal card, NOW/LATEST/NEXT/NEEDS-YOU, no-goal fallback.
- Work 8/8 — Hoowen-first grouping, unlinked CI isolation, goal/campaign drill-downs.
- Missions 10/10 — owner-priority sort, Historical badge, recency-gated lending.
- Trust 2/2, remaster IA 4/4, route gates/resilience 8/8, autonomy 8/8, disabled-controls audit 7/7.
- Dock 6/7, topbar 3/3, STOP 3/3, discovery 9/9, clock/nucleus/nav/signal/sidebar/locale/voice/boundary/lib suites remainder green.
- 1 FAIL (committed-test over-specificity, out of Task 9 scope, filed P1 §9):
  `commandBar … preserves the keyboard shortcut` uses `getByText("Hoowen beta")`
  which matches BOTH the combobox value and the result option.

## 3. Journey evidence (no browser pixels available — each journey UNRESOLVED with interim proof)

### HOME (Journey A — 5-second read) — UNRESOLVED (pixels) / interim PASS
Overview suite 10/10 renders the Hoowen card (goal, NOW, latest result, next,
literal "Nothing needed from you") against fixtures; live snapshot confirms the
underlying directive active + children states.

### HOOWEN (Journey B — Work grouping) — UNRESOLVED (pixels) / interim PASS
Work suite 8/8: Hoowen ACTIVE goal first with current child/worker/last-result/
next + child history; unlinked CI mission isolated.

### CURRENT WORK — UNRESOLVED (pixels) / interim PASS
Owner-priority sort + recency-gated lending (missions 10/10) keep current Hoowen
work above history; `ownerWork` lib tests 10/10 pin directive-recency ranking.

### OLD BLOCKED MISSION (`mission-4646c7023b05`) — UNRESOLVED (pixels) / interim PASS
MissionDetail historical tests render the exact banner
"Historical mission · Blocked on CI verification · Not affecting current Hoowen work",
HALT hidden; served snapshot confirms the mission is BLOCKED/CI-unlinked.

### MISSION VIEW (`mission-fe81cb0d1e37` lineage) — UNRESOLVED (pixels) / interim PASS
Primary-order tests pin standing-goal → child → worker → verifier/outcome order;
served snapshot confirms the mission row (BLOCKED follow-up 3).

### OWNER COMMAND — UNRESOLVED (pixels) / interim PASS (SIMULATED only)
Dock suites 12/13: GOAL primary + ASK/VETO tabs, STOP separate, IDs as secondary
confirmation. No LIVE submission ever attempted (discipline). 1 P1 test-only fail (§9).

### HEALTH-TRUST — UNRESOLVED (pixels) / interim PASS
Topbar + trust suites 5/5: System/Autonomy/Trust separate; SUSPENDED carries the
permitted-work explanation. Served `/api/health` matches: DEGRADED system
(schema 41/40), trust SUSPENDED.

### LOADING-RECONNECT (Journeys E/F) — UNRESOLVED (pixels) / interim PARTIAL
- E (refresh sameness): served snapshot fetched twice 12 min apart — owner rows
  byte-identical; `RouteQueryGate`/`keepPreviousWhileLoading` retention covered by
  route-gate tests 4/4 (SIMULATED transport).
- F (disconnect/reconnect): chip source-verified (`TopStatusBar.tsx:151-161`
  renders `.topbar-stream-reconnect` "Reconnect" iff `!streamConnected`);
  `sourceMode` lib tests 6/6 pin LIVE/DISCONNECTED/UNKNOWN semantics. No live
  disconnect exercised (would need a browser + owned server control).

### 1920×1080 — UNRESOLVED (no pixels)
CSS unchanged since Task 8's 87/87 overflow harness over identical product code.

### LAPTOP 1366×768 — UNRESOLVED (no pixels)
Same as 1920: identical code to Task 8's verified pass.

## 4. Verifier-side maintenance (2 files, stale-selector drift, zero product change)

1. `ui/command-center/e2e/live-autonomy-proof.spec.ts` — mission-detail block
   retargeted from removed `.md-pipeline`/"Execution Pipeline"/10-stage strip to
   `.md-compact-progress` → "Progress" heading → `.md-compact-tracker` with
   Goal/Plan/Build/Verify/Outcome + Outcome `Complete`
   (`compactMissionTracker` forces COMPLETE for a COMPLETE mission; `StatusBadge`
   still renders raw `COMPLETE`/`FAILED` so the adjacent page-level asserts stand).
2. `ui/command-center/scripts/product-verify.mjs` — `STREAM_DISCONNECTED` check
   retargeted from never-rendered `.topbar-disconnect`/"Updates paused" to the
   calm-layer `.topbar-stream-reconnect`/"Reconnect" chip.

Residual drift OUT of scope (not touched): `e2e/route-state-matrix.spec.ts:217`
and `e2e/smoke.spec.ts:43` assert the same removed markup; `live-autonomy-proof`
lines 141/431 still reference `.topbar-disconnect` (connected-count-0 holds
vacuously; line 431 needs the same chip retarget when that live spec is next
serviced).

## 5. Provenance: `ui/command-center/src/components/ObsidianOrbitCore.tsx`

- `git log --all -- <path>`: EMPTY (never committed on any branch).
- Importers in `src`: NONE (only self-references inside the file).
- Content: 89-line Obsidian vault orbit visual importing from
  `../lib/obsidianVault` (a COMMITTED lib; `obsidianVault.test.ts` 3/3 green here).
- Left untouched and uncommitted per instruction. Harmless to `tsc` (exit 0).

## 6. Dirt preserved (untouched, uncommitted)

`config/worker_routing.yaml` (M) and all untracked session files
(`.cursor/`, `Execute_*`, `WAVE1B_*`, `LIVE_ACTIVATION_*`, `LIVE_DB_PRE_V37_BACKUP.json`,
`docs/handoffs/parallel-lanes-*`, `ui/command-center/remaster-shots/`,
`ObsidianOrbitCore.tsx`). No stash/clean/reset performed (foreign `stash@{0}` intact).

## 7. How to complete the gate outside this sandbox

1. `npm run build`, `npx vitest run`, `npm run verify:product` (canonical PASS).
2. Serve the FRESH bundle + `serve_command_center_api.py --port 8765` (or the
   `8765→4173` proxy pair), run journeys A–F in a real browser at 1920×1080 and
   1366×768, save screenshots alongside this file.
3. Fix P1 test-only `getByText` ambiguity in `commandBar.test.tsx:139`
   (out of Task 9 scope).

## 8. Attempt log (exact commands, honest outputs)

- `npx tsc -b --noEmit` → exit 0 (×1 post-change; Task 8 recorded ×3 pre-change).
- `npm run build` → `tsc -b` half passes; `vite build` → `Error: spawn EPERM` at `esbuild/lib/main.js:2272` (0 bundles).
- `npx vitest run` → config-load `Error: spawn EPERM`, 0 tests.
- `npm run verify:product` → `Preview server not ready at http://localhost:4524`, 0 checks.
- `node scripts/capture-remaster.mjs` → `browserType.launch: spawn EPERM`.
- `npm run e2e` → `Playwright server did not become ready on port 62253`.
- `python scripts/serve_command_center_api.py --port 8765` → `PermissionError WinError 10013` on bind; `--port 18765` → served (stopped after).
- Interim harness → 201/202 (method §2; 1 P1 test-only fail).

## 9. UNRESOLVED lists

P0 (blocks acceptance, all environmental):
- P0-1 browser-pixel proof for journeys A–F (no browser spawn: EPERM).
- P0-2 1920×1080 + 1366×768 screenshots (no buildable/servable current UI here).
- P0-3 canonical `vitest` + `verify:product` + `vite build` PASS outside sandbox.

P1 (non-blocking, follow-up):
- P1-1 `commandBar.test.tsx:139` `getByText("Hoowen beta")` ambiguity (test-only).
- P1-2 residual `.topbar-disconnect` refs: `route-state-matrix.spec.ts:217`, `live-autonomy-proof.spec.ts:141/431` (431 needs chip retarget).
- P1-3 residual `.md-pipeline`/"Execution Pipeline" ref: `smoke.spec.ts:43`.
- P1-4 jsdom `requestAnimationFrame` stderr noise in dock tests (setup polyfill candidate).
- P1-5 `dist/` is a stale 2026-10-06 bundle (pre-branch, no tracker) — rebuild on next build PASS; never mistake it for this branch's UI.
