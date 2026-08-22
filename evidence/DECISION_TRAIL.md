# V3 Command Center — Decision Trail

Candidate: candidate/v3-command-center @ C:\temp\JARVIS-V3-Command-Center
Base: origin/main 8d0d7892a66301f6237490d894c11549409a91b9
Backend contract reference (read-only, owner-frozen mid-run): candidate/v3-integration @ 701aa748a2db412e3b6e83ab91f7982408ea6573

## Decisions

1. **Data boundary = extend JarvisClient with getDiscovery().**
   Alternatives sketched: (B) parallel V3 store/context beside the client — rejected, splits the adapter story; (C) component-local fixture imports — rejected, violates the brief's adapter-isolation rule. Chosen shape matches contracts.ts's own stated design intent.

2. **Models mirror the frozen backend contract, not the design docs' aspirations.**
   Mid-task the owner froze V3 integration at 701aa74. I extracted field names from the actual modules (v3_cycle.CycleReport outcomes COMPILED|WAITING|STOPPED|BLOCKED + reason; v3_scoring DIMENSIONS order + BELOW_THRESHOLD reason_code; v3_outcomes predicted_value/actual_value distinct dicts with status RECORDED|UNKNOWN; v3_teams verifier fresh_session/independence INDEPENDENT|REDUCED; eligibility WAIT kinds) and rewrote my first-pass models to match. No invented fields.

3. **Live adapter honestly unavailable.** build_snapshot at 701aa74 has no discovery section, so RealJarvisRuntimeAdapter.getDiscovery returns { available: false, reason: "PROJECTION_NOT_AVAILABLE" }. The UI shows an explicit unavailable state; fixtures never leak into live mode. Mapping code lands when the §3.13 projection ships.

4. **Fixtures grounded in docs/v3/03 §10 worked examples** (A selected maintenance repair at 0.58; B cosmetic BELOW_THRESHOLD 0.18; C research override case; D honest WAIT; E STOP-blocked cycle). Candidate ids fixture-stable for tests. Lineage references a real mock mission (M-HYB-014).

5. **No utility-class layer exists in this codebase** — verified by exhaustive CSS scan (.flex/.gap-*/.mt-* etc. are dead strings in Executive.tsx too). The route uses scoped v3-* layout primitives (v3-row, v3-list, v3-mt-*, v3-note...) built on the existing token palette instead of adding a framework or copying dead classes. StatusBadge/badge markup reused and re-styled under .v3-flag-banner scope only.

6. **meter/meter-fill had no stylesheet definitions anywhere** (pre-existing usage renders unstyled). Dimension bars define their own properly-styled primitives under the v3 namespace rather than leaning on phantom classes.

7. **Units committed gate-first:** b721a01 (models+fixtures+adapter wiring; tsc/lint green before commit), fae1dd8 (route+nav+CSS+tests; all four gates green), 57dca2c (scoped layout classes + visual pass at 1600/834/390 via Playwright screenshots committed as evidence).

## Verification record

- Baseline before edits: lint clean, tsc clean, 40/40 tests, build OK.
- Final: lint clean (--max-warnings 0), tsc -b --noEmit clean, vitest 49/49 (11 files, includes 9 new discovery tests), production build OK.
- Visual: Playwright full-page captures at 1600x1000, 834x1112, 390x844 plus candidate-grid closeup; reviewed by vision pass; label-truncation defect found and fixed (dim label column widened to 126px).
- Dev server used for visual pass: vite on port 4519 (background process, not part of the deliverable).

## Scope discipline

`git diff --stat origin/main...HEAD` contains only ui/command-center/** paths. No push performed.
