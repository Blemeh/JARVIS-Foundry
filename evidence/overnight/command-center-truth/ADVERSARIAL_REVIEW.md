# Adversarial Review (DISPROVE Mandate)

Work package **WP-J** — first pass.

## Scope challenged

- `evidence/overnight/command-center-truth/*` matrices and result artifacts
- WP-F accessibility claims
- WP-G visual manifest (225 cells)
- WP-H performance/console/security measurements
- Route-state matrix and e2e gates
- Boundary: no `Discovery.tsx`, `realClient.ts`, `contracts.ts`, backend edits

## Findings

### F1 — VISUAL_MANIFEST inspection is automated-only (MODERATE)

**Claim challenged:** "INSPECT screenshots" per cell.

**Evidence:** `capture-visual-manifest.mjs` sets `inspection: PASS` from document-level horizontal overflow only. No pixel or layout heuristic beyond `scrollWidth <= clientWidth`.

**Mitigation:** Spot-checked mobile/tablet screenshots for missions, systems, evidence, timeline, executive. No clipping observed at viewport capture (`fullPage: false`). Manifest notes updated to `automatedOverflowProbe` semantics.

**Status after mitigation:** ACCEPTED with documented limitation.

### F2 — Endurance gate incomplete (BLOCKING for shift VERDICT)

**Claim challenged:** "6 ACTUAL hours on port 4523".

**Evidence:** First attempts aborted (port contention, stuck runner at `cycles: 0`). Runner hardened in `c9c63f4` (bundled Chromium, 30s heartbeat). Full 6h run restarted `2026-08-22T22:58:28.626Z` on port 4523; progress verified (`cycles` advancing, 0 errors).

**Status:** IN PROGRESS — verdict remains **DISPROVE PARTIAL** until 6h completes and checkpoints recorded.

### F3 — E2E regression from accessibility row-role fix (FIXED)

**Claim challenged:** Full e2e still green after removing `role="row"` from Systems/Evidence buttons.

**Evidence:** Initial full e2e run post-WP-F: 6 failures (`cohesion`, `smoke`, `truth-lane`) referencing `getByRole("row")` and deprecated `getByLabelText`.

**Fix:** Updated selectors to `getByRole("button", …)` and `getByLabel`. Re-run: **161/161 PASS**.

**Status:** RESOLVED.

### F4 — Manual accessibility keyboard checklist (LOW)

**Claim challenged:** Manual checklist marked PASS without recorded session capture.

**Evidence:** Checklist in `ACCESSIBILITY_RESULT.md` reflects keyboard probes also covered by `accessibility.spec.ts` (skip link, aria-current) plus code inspection of focusable controls.

**Status:** ACCEPTED — automated probes cover critical paths; manual table is operator guidance.

### F5 — Boundary compliance (NONE FOUND)

No edits to `Discovery.tsx`, `realClient.ts`, `contracts.ts`, `v3Models.ts`, `v3Fixtures.ts`, or backend paths in WP-F–H commits.

**Status:** PASS.

## Re-review (second pass)

After F3 fix and e2e 161/161 PASS:

| Area | Result |
| --- | --- |
| Matrices vs e2e route-state | CONSISTENT |
| Accessibility axe | 11/11 |
| Visual manifest automation | HONEST (overflow-only) |
| Performance/console/security | REPRODUCIBLE |
| Endurance 6h | **NOT MET** |
| Boundary | CLEAN |

## Verdict

**DISPROVE PARTIAL** — material gap remains on WP-I six-hour endurance. All other challenged claims survive scrutiny after F3 fix.
