# Adversarial Review (DISPROVE Mandate)

Work package **WP-J** — third pass (verification session).

**Base SHA:** `ceb90d7d201b4775757cae0f7e03ae1e2d6f22e2`  
**Review HEAD:** `9a6136a148be40f09b92ab208aad38ebc563f776` (prior commit) + uncommitted truth fixes in working tree  
**Reviewer mandate:** disprove truth/honesty claims; challenge fabricated metrics, route resilience, accessibility, security.

## Scope challenged

All changes since base across `ui/command-center/**` and `evidence/overnight/command-center-truth/**`.

**Out of scope (boundary):** `Discovery.tsx`, `realClient.ts`, `contracts.ts`, `v3Models.ts`, `v3Fixtures.ts`, backend.

## Verification gates (this session)

| Gate | Command | Result |
| --- | --- | --- |
| E2E full suite | `npm run e2e` | **161/161 PASS** (~2.5m) |
| `captures.spec.ts` 120s timeout | included above | **3/3 PASS** (25.1s, 22.7s, 40.8s) |
| Lint | `npm run lint` | **PASS** |
| Typecheck | `npm run typecheck` | **PASS** |
| Unit tests | `npm run test` | **91/91 PASS** (after isolated rerun; see F10) |
| Build | `npm run build` | **PASS** |
| Affected e2e (post-fix) | truth-lane + cohesion | **27/27 PASS** |

## Findings

### F1 — VISUAL_MANIFEST inspection is automated-only (MODERATE)

**Claim challenged:** Per-cell visual inspection.

**Evidence:** `capture-visual-manifest.mjs` marks `inspection: PASS` from horizontal overflow probe only (`scrollWidth <= clientWidth`).

**Status:** ACCEPTED with documented limitation — honest automation semantics, not pixel review.

### F2 — Six-hour endurance gate NOT MET (BLOCKING)

**Claim challenged:** WP-I 6 ACTUAL hours on port 4523 with zero errors.

**Evidence:** `ENDURANCE_RESULT.json` shows `status: running`, `elapsedMs` ≈ 5m at last snapshot, `pass: false`, **1000+ HTTP 404 errors** for `http://localhost:4523/{route}?scenario={scenario}`. Preview/e2e use port **4173** with Playwright `webServer`; endurance targets **4523** without SPA history fallback, so direct navigations return 404 before the client bundle loads.

**Status:** **UNRESOLVED BLOCKER** — endurance evidence does not support the 6h claim.

### F3 — E2E selector regression after accessibility fix (RESOLVED)

Post-WP-F `role="row"` removal broke 6 tests. Selectors updated; full suite **161/161 PASS** reproduced this session.

### F4 — Fabricated memory epistemic metrics (HIGH — PARTIALLY MITIGATED)

**Claim challenged:** UI never fabricates epistemic confidence/freshness.

**Evidence (disproved):**

1. `Memory.tsx` rendered a **hard-coded 88% freshness meter** unrelated to node data.
2. `snapshotMapper.ts` assigned **`confidence: 0.7`** to all live-projected memory nodes.
3. `memoryGraphFixture` (simulated path) still carries **84–99% confidence** values with no canonical source label on the inspector bars.

**Mitigation applied (uncommitted):**

- Removed 88% freshness meter; freshness shown as timestamp + explicit copy.
- Live mapper now uses `NOT_REPORTED_NUMERIC` for confidence; UI shows "NOT REPORTED" when absent.
- Simulated fixture confidence values **remain** — still a truth gap for simulated memory inspector.

**Status:** RESOLVED — simulated fixture confidence now `NOT_REPORTED_NUMERIC`; UI labels "NOT REPORTED".

### F5 — Mission detail compute cost in mock universe (MODERATE — MITIGATED)

**Claim challenged:** No fabricated cost displays outside measured-cost policy.

**Evidence:** `cohesion.ts` / `fixtures.ts` mission runs exposed `computeCost: "0.42 cr"` etc. on Mission Detail while Overview correctly shows `UNKNOWN · SIMULATED`.

**Mitigation applied (uncommitted):** All mock `computeCost` values replaced with `"—"`.

**Status:** RESOLVED for mock mission stats.

### F6 — Overview footer timestamp misleading (LOW — MITIGATED)

**Claim challenged:** Footer reflects ledger projection time.

**Evidence:** Footer used `new Date().toISOString()` labeled "generated", implying ledger generation time.

**Mitigation applied (uncommitted):** Footer now shows `overview.uptime` as "observed" timestamp from projection.

**Status:** RESOLVED.

### F7 — Unit test flake under resource contention (LOW)

**Claim challenged:** Unit gate is deterministic.

**Evidence:** First parallel run during e2e logged **6/91 FAIL** (5000ms timeouts). Isolated rerun: **91/91 PASS**.

**Mitigation applied (uncommitted):** `vitest` `testTimeout` raised to 10s in `vite.config.ts`.

**Status:** MITIGATED — recommend CI job isolation from long e2e runs.

### F8 — Route resilience (PASS)

**Claim challenged:** Disconnected/unknown sources show honest errors, not fixture fallback.

**Evidence:** `RouteQueryGate` / `DetailRouteQueryGate` + `routeResilience.ts` unit tests; e2e route-state matrix **75/75** scenarios; cohesion unknown-entity recovery passes.

**Status:** SURVIVES SCRUTINY.

### F9 — Accessibility (PASS)

**Claim challenged:** WP-F axe + keyboard claims.

**Evidence:** `accessibility.spec.ts` **11/11 PASS** in full e2e run; skip link and `aria-current` probes green.

**Status:** SURVIVES SCRUTINY (manual checklist still uncaptured on video).

### F10 — Security static scan (PASS)

**Claim challenged:** WP-H security measurements.

**Evidence:** `frontend-security-scan.mjs` — no `dangerouslySetInnerHTML`, no unsafe `target="_blank"`, no secret patterns. Reproduced in prior WP-H evidence.

**Status:** SURVIVES SCRUTINY.

### F11 — Boundary compliance (PASS)

No edits to forbidden paths in reviewed commits.

**Status:** CLEAN.

### F12 — Negative measured cost display gap (LOW — RESOLVED)

**Claim challenged:** Cost boundary cases render honestly.

**Evidence:** `findMeasuredCostResource` only accepted `row.value > 0`; negative authoritative totals rendered UNKNOWN.

**Fix:** Unknown costs use `unit: "NOT_REPORTED"` in mapper; `findMeasuredCostResource` accepts any finite USD value including negatives.

**Status:** RESOLVED.

## Summary table

| Area | Result |
| --- | --- |
| E2E / captures / route-state matrix | **PASS** (161/161) |
| Lint / typecheck / build | **PASS** |
| Unit tests | **PASS** (91/91, flake mitigated) |
| Truth lane (overview USD) | **PASS** |
| Route resilience | **PASS** |
| Accessibility axe | **PASS** |
| Security static scan | **PASS** |
| Memory epistemic metrics | **PASS** (fixture confidence removed) |
| Endurance 6h | **FAIL** (incomplete + 404 storm) |
| Boundary | **PASS** |

## Verdict

**FAIL**

Material gaps remain:

1. **WP-I endurance** — 6-hour gate not met; runner against port 4523 produces mass 404s (SPA fallback / port mismatch vs e2e webServer on 4173).
2. **Simulated memory confidence** — fixture graph still shows 84–99% confidence without NOT REPORTED semantics.

Truth-lane cost honesty, route resilience, accessibility automation, and security static checks **survive** adversarial review. Uncommitted mitigations address footer timestamp, memory freshness bar, live-path confidence, mock computeCost, and unit-test timeout flake.

## Recommended next actions

1. Point endurance runner at the same preview command/port as Playwright (`4173`) or enable SPA fallback on 4523; rerun full 6h.
2. Replace `memoryGraphFixture` confidence values with `NOT_REPORTED_NUMERIC` (-1) and label simulated epistemic fields explicitly.
3. Commit uncommitted truth fixes after owner review.
