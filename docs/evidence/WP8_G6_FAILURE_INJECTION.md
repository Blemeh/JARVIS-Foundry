# WP8 G6 — Deliberate Failure Injection Evidence (step-3)

Status: **PASS (full repair lineage)** — 2026-08-12
Gate: `04_V1_ACCEPTANCE_GATE.md` G6 — the mission loop must survive a
deliberately broken implementation criterion: verifier FAIL, prioritized
repair, regression tests, re-verification PASS, lineage visible.

## 1. The Injection (recorded at step-3 creation)

- Ledger: `step.created:mission-wfm-optimizer:step-3` carries
  `injection_note: "DELIBERATE G6 INJECTION: a subtle constraint regression
  is planted in the scheduler; the step verifier must catch it (FAIL) and
  the repair lineage must be recorded"`.
- Planted defect: `src/wfm_optimizer/scheduler.py` line 161 — selection
  comparison flipped `score < best_score` (documented: earliest candidate
  with LOWEST coverage impact) to `score > best_score` (latest candidate).
- Why this shape: a suite-invisible semantic regression — all rules,
  floors, determinism, and performance still hold; only the documented
  selection semantics change. The mission suite (28 tests) stayed green
  under the injection, so the detection burden fell on the independent
  verifier, as G6 requires.
- First attempt (dropped candidate window) was rejected because the
  suite's own case-08 fixture (a tight-fit 120-min shift) caught it — the
  injection must be verifier-detectable, not suite-detectable, to prove
  the independent-review leg of the gate.

## 2. The FAIL (independent verifier deleg_20994c80, 592 s)

- Verdict: **FAIL** — "the step-3 claim is disproven."
- Exact finding: `scheduler.py:161` — `score < best_score` → `score >
  best_score` inverts the documented selection semantics: every block
  lands on the LATEST candidate start.
- Evidence: isolated A/B harness (step-2 HEAD vs step-3 worktree differing
  only by the comparison): step-2 honors the contract on 38/38 placements;
  step-3 violates it on 38/38. Smoking gun: solo lunch at 950 (15:50)
  instead of the earliest 570 (9:30); breaks 930/940 instead of 610/620.
- All other documented behaviors verified PASS (rules, floors, determinism
  across 3 processes/hash seeds, infeasibility edges, tight-fit margins,
  CLI contract, scope, evidence).
- Independence: probes authored from the contract and source alone;
  same-provider limitation (opencode-go family) recorded.
- Transcript: `C:\Users\b\AppData\Local\hermes\cache\delegation\live\deleg_20994c80\task-0.log`; summary `subagent-summary-0-20260812_014702_064323.txt`.

## 3. The Repair (executive, same builder role — G6 allows the repair
   builder to be a fresh builder OR the executive; recorded here)

1. Restored `score < best_score` (byte-identical to the committed step-2
   contract — git forensics by the re-verifier confirmed the flip never
   existed in any commit).
2. Added `tests/test_g6_regressions.py` (3 tests):
   - `test_selection_earliest_lowest_impact` — pins the earliest-candidate
     clause (510 chosen; the injected flip produces 890 → caught).
   - `test_tight_fit_120_minute_shift_schedules` — 120-min shift must
     schedule (guards dropped-candidate/window-math regressions).
   - `test_119_minute_shift_is_honest_infeasibility` — 119-min must fail
     precisely with reason "no candidate window", placements empty.
3. CLI help fix: `--floor-factor` documented as `<=0 disables floors`
   (post-verifier hardening note from step-2's INFO finding).
4. Suite: 31 passed, AC3 72.5 ms.

## 4. The Re-Verification (fresh verifier deleg_041a903e, 362 s)

- Verdict: **PASS** — "The exact G6 finding is genuinely repaired."
- Independent greedy re-implementation from scratch matched every
  placement on all 8 valid fixtures; case-06 reproduces the FAIL report's
  step-2 reference values exactly (lunch 570, breaks 610/620).
- Flip probe (TEMP copy with `score > best_score`): the semantics
  regression test FAILS (890 vs 510) — the exact finding is caught by the
  new test; control module passes 3/3.
- Honest nuance documented: 2 of 3 new tests are genuine behavioral pins
  (120/119 boundaries) but are not sensitive to a pure comparison
  inversion — the flip is caught by test 1 and the suite.
- Git forensics: `git log -S 'score > best_score' --all` empty; reflog
  shows no rewrite — repair = restore to committed contract.
- Scope clean on both repos; step-2 evidence baseline intact.
- Transcript: `...\delegation\live\deleg_041a903e\task-0.log`; summary `subagent-summary-0-20260812_015625_...txt`.

## 5. Ledger Lineage

- `step.created` step-3 (injection_note) → `verification.started`
  verdict-step-3 → `verification.failed` (exact finding + repair
  requirements) → `verification.passed` (re-verify evidence) →
  `git.local_commit_created` 5b01665 → Obsidian write-back (02/07).
- Mission repo commits: e9aa450 (baseline) → 893eec8 (step-1) → 508ca5f
  (step-2) → 5b01665 (step-3 G6 repair).

## 6. Evidence Artifacts

- `var/evidence/wp8-gate/step-3/` (bundle pending: mission-tests.txt 31
  passed, manifest.json).
- Verifier summaries + live transcripts (paths above).
- This report.

## 7. Verdict

G6: **PASS** — a deliberate defect was planted, independently FAILed with
an exact finding, repaired with regression tests, re-verified PASS by a
fresh session, and the full lineage is visible in the ledger, docs, and
repositories. No gate was weakened; the FAIL was genuine and unscripted.
