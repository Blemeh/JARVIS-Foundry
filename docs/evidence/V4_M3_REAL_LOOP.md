# V4-M3 — Real Live Loop + Mandatory Routing Proof (DIR routing item 8)

**Date:** 2026-08-13T01:59+02:00
**Status:** EXECUTED — one real live loop on the live DB via
`var/evidence/v3_loops/live_loop_driver.py` (loops=1, the V3-proven
driver; `enforce_directive_routing()` runs at driver construction AND
as step 0 of `run_one_loop` before any mutation).

## What the loop did (ledger-verified)

- **Observed real state**: repo 24 new events, system 20 new events,
  vault/handoffs 0 (no new claims) — `run_ingestion_cycle` on the live
  sources.
- **Monitors registered on the LIVE DB (first time ever)**: 4 rows —
  vault, handoffs, repo, system/live-ledger (`monitors` table was empty
  since the 2026-08-12 audit; R6 closed operationally).
- **Polled through the registry**: 4 `discovery.monitor_polled` events;
  the vault poll found 1 genuinely-new research item → 1 candidate
  created (`candidates_found: 1`; handoffs/repo/system 0 — dedup).
- **Scored 8 candidates** (S6 context formula); top 0.25.
- **Outcome: WAITING — honest** ("no candidate scored >= 0.3 (top 0.25)
  — honest waiting, no invented work"). The maintenance signal:
  unsupported 1 (< 5 bar), stale 0, open 0 → no maintenance candidate.
- No mission dispatched: the live backlog currently contains NO
  bar-clearing candidate (the 233 old-formula SCORED rows peak at
  0.247; the 696 NEW rows predate the newest-first score window and
  age out by design).

## Mandatory-routing proof status — HONEST

The directive requires ledger rows showing the first real builder,
verifier and recovery worker on deepseek-v4-flash. **No real worker has
dispatched since the routing fix because no candidate cleared the bar**
— manufacturing a mission would violate "do not lower thresholds to
manufacture work". The proof currently rests on:

1. `RoleRouter.enforce_directive_routing()` — rejects ANY configuration
   that is not opencode_go + deepseek-v4-flash with no fallbacks; runs
   as step 0 of `run_one_loop` BEFORE any mutation (v3_loop.py) and at
   the live drivers' construction — a real dispatch under a
   non-compliant router is structurally impossible (26 routing tests,
   test_directive_model_routing.py).
2. The dispatcher's chain: `builder`/`verifier`/`repair`/`researcher`/
   `premium_reviewer` all resolve to deepseek-v4-flash; provider
   outage → BLOCKED, never substitution (tested).
3. The historical kimi-k2.7-code ledger rows (run-13ec6f846520,
   run-446d64ab721b) are pre-fix history — impossible under the
   current router (probed by the MR-1 verifier, deleg_debc2612 PASS).

**Pending**: the first REAL deepseek-v4-flash dispatch happens when a
bar-clearing candidate appears (fresh repo/vault observations, a
maintenance signal ≥ threshold, or a BLOCKED mission to resume). The
next live loop cycles re-check every ~15-30 min; the enforcement makes
the ledger proof a WHEN, not an IF.

## Artifacts

- `var/evidence/v3-loops/live-loops-20260813T015932.json` (full report)
- Ledger: 4× monitor_registered, 4× monitor_polled, candidate_generated
  + candidate_scored for the vault-poll candidate; schema 18 intact;
  daemon RUNNING (heartbeats continuous); reconcile consistent.
