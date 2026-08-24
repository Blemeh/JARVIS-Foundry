# LANE 2 — MISSION 2 PLAN (M2-WAN2GP-LONGCAT-BSA-SYNTAX)

Date: 2026-08-24

## PROJECT TRUTH

PROJECT_ID: Wan2GP (WanGP by deepbeepmeep) — third-party upstream
OWNER CHECKOUT (untouched): C:\Users\b\Documents\Wan2GP
ISOLATED CLONE: C:\temp\JARVIS-L2\wan2gp-baseline
UPSTREAM REMOTE: https://github.com/deepbeepmeep/Wan2GP.git (no write access expected)
BEFORE HEAD: 6e35b37e309ccebeed193ef53cdff66fb973b693 (= upstream main at clone)
DIRTY STATE OF OWNER CHECKOUT: only untracked env_venv/ -> clean committed state; isolated via clone regardless.
TEST/BUILD COMMANDS: project has NO test suite and NO CI. Acceptance uses
python -m py_compile on target + whole-tree compileall + diff confinement.
PYTHON: C:\Users\b\Documents\Wan2GP\env_venv\Scripts\python.exe used READ-ONLY.
ROLLBACK: delete mission branch in lane clone. Zero effect on owner checkout.
RISK: trivial (syntax-only repair of a vendored module).
VALUE: module currently cannot be imported or compiled by anyone; any code path
touching LongCat block-sparse attention crashes at parse time.

## PROBLEM

models/longcat/modules/block_sparse_attention/flash_attn_bsa_varlen_mask.py
(committed unchanged since 96e3438 "added experimental longcat") contains three
mis-indented statements:

- line 111: qk_scale *= 1.44269504  (8 spaces; body level is 4)
- line 140: acc = tl.dot(...)        (20 spaces; loop-body level is 12)
- line 143: l_i = l_i * alpha ...    (20 spaces; loop-body level is 12)

Whole-repo compileall fails: IndentationError line 111. Reproduced on pristine
clone at 6e35b37.

## RESEARCH EVIDENCE (R4)

- git log --follow: file untouched since introduction (96e3438); no local edits.
- Surrounding code is the standard triton flash-attn structure (compare sibling
  files in the same tree, e.g. flash_attn_no_pad.py): qk_scale scaling is plain
  function-body statement; acc/l_i updates belong to the innermost loop body.
- Sibling-file comparison confirms intended indentation levels.
DECISION: IMPLEMENT (mechanical syntax repair).

## ACCEPTANCE CONTRACT

AC1: py_compile succeeds on flash_attn_bsa_varlen_mask.py.
AC2: whole-tree compileall(force=True) over the lane clone returns COMPILE_OK
     (no other file in the repo fails to parse).
AC3: git diff confined to models/longcat/modules/block_sparse_attention/
     flash_attn_bsa_varlen_mask.py; changes are indentation-only on lines 111,
     140, 143 (no logic/token changes).
AC4: upstream default branch untouched; no force pushes; push attempt to
     upstream recorded honestly (expect BLOCKED_EXTERNAL without credentials).
TARGET FILES: only the one file above.

## VERIFICATION CONTRACT

builder(coordinator) -> adversarial critic(fresh) -> independent verifier(fresh)
-> commit. R10 already exercised in M1; this mission inherits the proven loop
and re-verifies with fresh contexts.

## COST/TIME BUDGET
~20 min, $0 external spend.
