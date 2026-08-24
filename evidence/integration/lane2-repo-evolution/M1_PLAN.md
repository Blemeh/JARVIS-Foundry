# LANE 2 — MISSION 1 PLAN (M1-LEADGEN-CONTRADICTORY-EVIDENCE)

Date: 2026-08-24
Lane branch (JARVIS): candidate/autonomous-repo-evolution-20260824 @ 079245ae (FROZEN_BASE = origin/main)
Lane worktree: C:\temp\JARVIS-Autonomous-Repo-Evolution

## PROJECT TRUTH

PROJECT_ID: LeadGen (LocalLead)
PATH (owner checkout, UNTOUCHED): C:\Users\b\Documents\LeadGen
ISOLATED CLONE (this lane only): C:\temp\JARVIS-L2\leadgen-baseline
REMOTE: https://github.com/Blemeh/LeadGen.git (private, owner-owned; gh auth = Blemeh with repo scope)
DEFAULT BRANCH: main
BEFORE HEAD: a7ccc12cc056d3a0aed0db3aa332e3904c2290cc (= origin/main at clone time)
OWNER CHECKOUT STATE: dirty (in-progress owner changes) -> isolated via separate clone; owner working tree never touched by this lane.
TEST BASELINE (committed HEAD, measured):
- ruff check .                          -> PASS (exit 0)
- pytest -q                             -> 344 passed, 119 skipped, 2 FAILED
- FAILING: tests/test_service_opportunities.py::test_contradictory_evidence_is_unknown_and_not_a_verified_gap
           tests/test_service_opportunities.py::test_contradictory_evidence_suppresses_gap_and_secondary_overlap
TEST RUNNER: C:\Users\b\Documents\LeadGen\backend\.venv\Scripts\python.exe (Python 3.12.13, pytest 9.1.1)
  used READ-ONLY against the lane clone cwd; owner venv/site-packages not modified.
ROLLBACK: delete branch jarvis/l2m1-fix-contradictory-evidence-suppression in lane clone; owner repo unaffected.
REMOTE PERMISSIONS: push allowed for non-main branches under Blemeh/LeadGen (repo scope). NO merge, NO force, NO default-branch writes.
DEPLOYMENT IMPLICATIONS: none (no deploy surfaces touched).

## PROBLEM

detect_service_opportunities() records per-check contradictions ("contradictory:{check}"
conflict + contradictory_evidence unknown signal) but then still scores the affected
check group from the contradicted "fail" observation, emitting a VERIFIED
contact_conversion_gap signal -> verified customer_conversion/lead_capture opportunities.

Consequence: fabricated "verified" sales opportunities are produced from ambiguous
crawl evidence, violating LocalLead's documented data boundary
("Missing provider fields remain unknown", README non-negotiable data boundaries).
Two committed contract tests fail on HEAD.

## RESEARCH EVIDENCE (R4)

1. Tests test_service_opportunities.py were authored WITH the engine at b5e8561
   (2026-07-27) as its behavioral contract; they have never been aspirational TDD stubs.
2. Commit 6d2a6ac ("AI", 2026-07-30, engine v2b->v3) deleted the contradiction guard
   in the check_groups loop while adding accessibility_gap:

   removed:
       statuses = {str(item.get("status") or "unknown") for item in observations}
       if "pass" in statuses and ("fail" in statuses or "warning" in statuses):
           continue

   => regression introduced here; both contract tests fail since.
3. The original guard was over-broad: it skipped the ENTIRE group when ANY pass/fail
   mix existed across DIFFERENT checks, suppressing legitimate independent evidence.
4. The booking_ordering block already implements precise contradiction handling:
   conflict + unknown signal, verified branch skipped. Precedent inside same module.

DECISION: IMPLEMENT (repair the regression with precise per-check semantics,
not a blind revert of the blunt guard).

## ACCEPTANCE CONTRACT

AC1: pytest backend/tests/test_service_opportunities.py -> all pass (incl. the two
     currently-failing contract tests).
AC2: full backend suite >= baseline: 0 failed (344 passed baseline + new tests).
AC3: ruff check . exit 0 (line-length 100).
AC4: mypy strict passes (project config).
AC5: diff confined to registered target paths (below). No other files changed.
AC6: default branches untouched; mission branch pushed to origin with readback
     SHA == local SHA; no merge; no force-push.

TARGET FILES (only these may change):
- backend/app/service_opportunities.py
- backend/tests/test_service_opportunities.py

NON-TARGET: everything else, especially owner checkout, alembic/, frontend/, docs/.

## IMPLEMENTATION DESIGN

In detect_service_opportunities():
1. First loop already computes per-check contradictions. Collect them into
   `contradicted_checks: set[str]` while appending conflicts/signals.
2. In the check_groups loop, exclude observations whose check_identifier is in
   contradicted_checks from problem_observations. Clean problem evidence from
   OTHER checks in the same group still supports a verified gap (precision
   improvement over the removed blunt guard).
3. If a group has no clean problem observations but DOES contain contradicted
   observations, emit the existing unknown-status signal shape referencing the
   contradicted observations ("evidence inconclusive"), matching CONFIDENCE_RULES
   semantics and the booking block precedent. Information is preserved instead of
   silently dropped.
4. Add regression tests pinning BOTH behaviors:
   - contradicted-check-only evidence yields NO verified contact_conversion_gap
     (already covered by the two repaired contract tests);
   - clean fail on one check + contradiction on another check in the same group
     still yields a verified signal supported ONLY by the clean observation
     (guards precision improvement against future regressions).

## RISK / REVERSIBILITY

Risk: LOW-MEDIUM (scoring semantics of one module; covered by 16 existing tests in
the target test file + full suite). Reversibility: single commit on disposable
branch; revert = delete branch. No data migration, no schema change, no deploy.

## VERIFICATION CONTRACT

Builder output (diff + command logs) -> adversarial critic (fresh context) ->
independent verifier (fresh context, reproduces AC1-AC5 itself) -> verdict.
R10 repair cycle will be exercised within this mission via the sanctioned proving-
fixture method if the first implementation verifies clean (documented transparently;
no fake defects in shipped code; planted expectation lives only in a disposable
proving commit that is then repaired).

## COST/TIME BUDGET

Estimated: ~45 min wall clock, $0 external spend (local compute + existing GH auth).
