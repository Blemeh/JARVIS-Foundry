# Cross-Lane Findings (defects proved OUTSIDE this lane's ownership)

Recorded per the ownership boundary: smallest deterministic reproduction,
no out-of-scope repairs. For the hard-gate / Command Center truth lanes.

## CX-1 — `candidate_scores` projection lacks `scoring_policy_version` column

- Area: migrations/projections (V3 hard-gate lane).
- Observation: the discovery projection prefers
  `candidates.scoring_policy_version` because the v36 `candidate_scores`
  table has no policy column; the scored-event trace is used instead.
  Not a defect in this slice — recorded so the schema lane can consider an
  additive column if per-policy score history is ever needed.
- Repro: `PRAGMA table_info(candidate_scores)` at schema v36.
- Severity: LOW (informational).

## CX-2 — `_executive_projection` iterates the full ledger

- Area: command_center.py general telemetry (Command Center truth lane).
- Observation: `_executive_projection(database, limit=25)` walks ALL events
  via `database.events()` in Python on every snapshot build. On long-lived
  ledgers this dominates snapshot latency. The new discovery section uses a
  bounded indexed event scan (`events_type_sequence`, LIMIT 400) — the same
  pattern could bound the executive path without changing its output shape.
- Repro: read `_executive_projection`; benchmark with >10k unrelated events.
- Severity: MED for production scale; no functional incorrectness found.

## CX-3 — CORS headers absent on GET snapshot routes

- Area: command_center HTTP handler (release/supervisor lane).
- Observation: `_json_response` emits CORS headers but the `/api/command-center`
  GET path writes its body directly, so cross-origin browser clients get no
  `Access-Control-Allow-Origin`. Same-origin deployments are unaffected;
  browser proofs must use the vite preview proxy (as documented in
  WP_C_BROWSER_PROOF.md). Recorded so the truth lane can decide whether the
  header belongs on GETs too.
- Repro: `curl -s -D - http://HOST/api/command-center -H "Origin: http://x" | grep -i access-control` → empty.
- Severity: LOW.

## CX-4 — Windows read-only git object files block naive rmtree

- Area: packaging/ops.
- Observation: disposable workspaces containing git repos need read-only-flag
  clearing before `shutil.rmtree` on Windows (harness handles this itself;
  see scripts/v3_operational_e2e.py `_onerror`). Any lane copying that cleanup
  pattern should reuse it rather than learning it again.
- Severity: LOW (ops note).
