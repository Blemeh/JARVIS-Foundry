# Evidence Manifest — V3 OPERATIONAL GATE (Phase 5, sanitized)

Committed 2026-08-12 · candidate HEAD `b433ae6` (tree clean at freeze
and start→end of verification). Private/local artifacts stay under
`var/evidence/` (gitignored) and are referenced by sha256.

## Candidate

- HEAD: `b433ae6` (code candidate `a18ea14` + gate docs)
- Worktree: clean at freeze; verifier confirmed clean start→end
- Suite: 388 tests, EXIT=0 (`./.venv/Scripts/python.exe -m pytest
  tests/foundry -q --tb=short`; collect-count 388)

## Verifier identity

- `deleg_894de0e1` — independent adversarial, 6/6 requirements PASS
  (round-1), provider deepseek-v4-flash via Hermes subagent
  (same-provider limitation recorded).
- Verdict: `var/evidence/v3-loops/verdict-round1-deleg_894de0e1.json` →
  sha256 `88b858bb2632a5d7f14c740aeecc91557ec3d956de352c92ed72228f02fe4ec8`

## Artifact hashes (sha256)

- `var/evidence/v3-loops/live-loops-20260812T211355.json`,
  `…T212237.json`, `…T215117.json`, `…T220638.json` — live loop reports
  (8 honest-WAITING loops total across runs; 3 complete missions).
- Deliverables (in the registered worktree, untracked):
  - `v3-loops/loop-live-1-211504/report.md` — final verified content
    1382 B, sha256 prefix `b83a5733…` (pre-resume 1200 B `5255d1fc…`)
  - `v3-loops/loop-live-1-221141/report.md` — 1166 B, `f4264a58…`
  - `v3-loops/loop-live-2-221706/report.md` — 1434 B, `3f9194e9…`
- Staging deployment-target probe: `var/staging-v3-check/staging.db`
  (copy of the live DB; initialize() → v16; reconcile consistent;
  11,959 events).
- Live ledger: verification.passed:verdict-v3-operational:round1 +
  evidence.created:v3-operational-gate (mission-wfm-optimizer).

## PASS/FAIL lineage (V3 operational)

- Live loop 1: BLOCKED (verifier 300s bound) → reaper session_lost
  reclaim → resumed → 3 PASS verdicts → mission COMPLETE.
- Live loops 2-3: PASSED first-try (maintenance candidates; the second
  produced the 894-unsupported root-cause finding).
- Repairs with regressions: mission.completed gating; _top_scorable
  newest-first (×2 starvation findings); state-machine walk; SQL
  cleanup. All in commits 53a4aaa → f4a9ad4 → 81243a5 → a18ea14.
- Earlier gates: V2 op gate round-1 FAIL (C8/C9) → repairs 41eb9c6 →
  round-2 PASS 16/16 (38c64c72…); WP10 slice-2 FAIL→repair→PASS.

## Remaining uncertainty

- Support-semantics gap (the loop's own finding): 894/920 claims are
  marked UNSUPPORTED because the support test only matches ACTIVE
  vault_files sources — the follow-up repair mission is queued as the
  next valuable action (V4's first mission).
- Deliverable hashes recorded in driver logs are not git-tracked (the
  worktree is untracked for v3-loops/) — drift is detectable by
  comparing logs vs disk (recorded in the gate doc).
- Same-provider verifier limitation.
- No remotes configured on the Foundry repo — local-only (no-push
  guaranteed by construction); .h upstream fetch flaky (recorded).

## Private evidence references (not committed)

- `var/evidence/v3-loops/` — driver logs (driver-run2..5.log), live
  loop artifacts, verdict JSON, resume logs.
- `var/evidence/staging-v3-check/` — staging DB probe.
- Live DB `C:\Users\b\AppData\Local\hermes\foundry\foundry.db`
  (~12,000 events at close).
