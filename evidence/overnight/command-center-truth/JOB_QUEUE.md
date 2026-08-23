# Command Center Truth — Serial Job Queue

**Branch:** `candidate/overnight-command-center-truth`  
**Worktree:** `C:\temp\JARVIS-Overnight-Command-Center-Truth`  
**Canonical base:** `ceb90d7d201b4775757cae0f7e03ae1e2d6f22e2`  
**Queue verified at:** `4642337`

Evidence is marked **DONE** only after executable verification at the committing HEAD.

## Job 1 — Route resilience layer

**Status:** DONE @ `25d7b47`

| Requirement | Evidence |
| --- | --- |
| `RouteQueryGate` / `DetailRouteQueryGate` on all non-Discovery list/detail routes | `src/components/RouteQueryGate.tsx`, `DetailRouteQueryGate.tsx`, `ROUTE_RESILIENCE_MATRIX.md` |
| Abort/unmount cleanup in `useRouteQuery` | `src/hooks/useRouteQuery.ts` (`cancelled` flag) |
| Disconnected/unknown honest errors | `src/lib/routeResilience.ts`, unit + e2e matrix |
| Stream disconnected visible (not silent) | `TopStatusBar` `role="status"` chip; `?scenario=STREAM_DISCONNECTED` |
| Memory empty graph / no selection blank screen | `Memory.tsx` `EmptyState` instead of `return null` |
| Route/state matrix tests | `e2e/route-state-matrix.spec.ts` (77 matrix + 2 extended scenario + nav cases) |

**Gate commands (session):**

```text
npm run test   → 94/94 PASS
npx playwright test e2e/route-state-matrix.spec.ts → PASS
```

## Job 2 — Mapper and fixture truth semantics

**Status:** DONE @ `9a17f70`+ (re-verified session)

| Requirement | Evidence |
| --- | --- |
| Table-driven truth cases | `src/lib/__tests__/snapshotTruthCases.ts` (14 boundaries) |
| Absent/null/zero/UNKNOWN/malformed/stale/Unicode | `snapshotMapper.test.ts`, `truthFormat.test.ts` |
| No fabricated fallback in mapper | `FIXTURE_MAPPER_RESULT.md` |

**Gate:** `npm run test` → snapshot + truthFormat suites PASS

## Job 3 — Owner workflow and controls

**Status:** DONE @ `8fd0d27`+ (re-verified session)

| Requirement | Evidence |
| --- | --- |
| Nav, deep links, back/forward, active nav | `e2e/owner-workflow.spec.ts` |
| Disabled controls with reasons | `disabledControlsAudit.test.tsx` |
| Source/candidate identity distinct labels | `TopStatusBar`, `Overview` footer, `data-source-pill` |

**Gate:** `npx playwright test e2e/owner-workflow.spec.ts` → PASS

## Job 4 — All-route state harness

**Status:** DONE @ `159a31e`+ (re-verified session)

| Requirement | Evidence |
| --- | --- |
| Deterministic route × scenario matrix | `e2e/route-state-matrix.spec.ts` |
| Console/pageerror guard | matrix `attachConsoleGuard` |
| Rapid nav + refresh cases | matrix tail tests |
| Forbidden fabricated telemetry | `$0.38`, `99.8` banned in matrix |

**Gate:** `npx playwright test e2e/route-state-matrix.spec.ts` → PASS

## Job 5 — Accessibility source repair

**Status:** DONE @ `8fd0d27` (re-verified session)

| Requirement | Evidence |
| --- | --- |
| axe automated checks | `e2e/accessibility.spec.ts` (11 tests) |
| Skip link, landmarks, page titles | `AppShell`, `useDocumentTitle` |
| Results | `ACCESSIBILITY_RESULT.md` |

**Gate:** `npx playwright test e2e/accessibility.spec.ts` → PASS

## Job 6 — Responsive and visual functional repair

**Status:** DONE @ `0a9c046` (re-verified session)

| Requirement | Evidence |
| --- | --- |
| 3 viewports × routes × scenarios | `VISUAL_MANIFEST.json` (225 cells) |
| Overflow probe per cell | `capture-visual-manifest.mjs` |

**Gate:** manifest `inspection: PASS` on overflow probe (not pixel review)

## Job 7 — Reliability, performance, security

**Status:** DONE @ `159a31e` (re-verified session)

| Requirement | Evidence |
| --- | --- |
| Build chunk measurement | `PERFORMANCE_CONSOLE_SECURITY.md` |
| Security static scan | `scripts/frontend-security-scan.mjs` |
| vitest timeout flake mitigation | `vite.config.ts` `testTimeout: 10_000` |

**Gate:** `npm run build`, `npm run verify:security` → PASS

## Job 8 — Local product verification harness

**Status:** DONE @ `e6ba131`

| Requirement | Evidence |
| --- | --- |
| Production preview on lane port 4524 (4523 reserved for endurance/visuals) | `scripts/product-verify.mjs` (`VERIFY_PORT`) |
| Routes × scenarios × viewports + nav/refresh | 212 checks |
| Structured failure capture | `PRODUCT_VERIFY_RESULT.json` |
| Endurance preview wrapper | `scripts/run-endurance-with-preview.mjs` |
| npm scripts | `verify:product`, `verify:endurance`, `verify:console`, `verify:security` |

**Gate:** `npm run verify:product` → **212/212 PASS**

## Job 9 — Next real UI gap

**Status:** DONE @ `d3019a9`

| Gap | Fix |
| --- | --- |
| STREAM_DISCONNECTED had no owner recovery control | `reconnectStream` in context + TopStatusBar reconnect button (simulated only) |

## Job 10 — WP-I six-hour endurance

**Status:** IN_PROGRESS (out of band; not blocking source commits)

| Requirement | Evidence |
| --- | --- |
| 6h on port 4523 with preview SPA | `ENDURANCE_RESULT.json` (`running`, 0 errors at t+15m checkpoint) |
| Wrapper ensures preview lifecycle | `run-endurance-with-preview.mjs` |

**Note:** Prior 404 storm fixed by dedicated preview + 500ms throttle. `product-verify` uses port **4524** so it cannot kill the endurance preview on **4523**. Full 6h gate not complete in this session.

## Final validation checklist

```bash
cd ui/command-center
npm run lint          # 0 warnings
npm run typecheck
npm run test          # unit
npm run e2e           # full playwright
npm run build
npm run verify:product
npm run verify:security
```

## Out of scope (untouched)

`Discovery.tsx`, `realClient.ts`, `contracts.ts`, `v3Models.ts`, `v3Fixtures.ts`, backend Python, migrations, Audit R2, production config, `main`, `origin/main`.
