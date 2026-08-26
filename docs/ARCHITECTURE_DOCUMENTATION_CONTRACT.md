# JARVIS FOUNDRY — DOCUMENTATION MAINTENANCE CONTRACT (anti-staleness)

Why this exists: documentation went stale because current operational truth and
planning prose were coupled manually (e.g. the 2026-08-11 architecture set said
"BUILD NOT STARTED" long after a running canonical runtime existed).

## Classification vocabulary

Every material statement in repository documentation belongs to exactly one class:

- `PROVEN_CURRENT` — matches code, git, runtime state, or committed evidence.
- `IMPLEMENTED_UNPROVEN` — code exists; proof is missing.
- `PARTIAL` — subset implemented.
- `PLANNED` — future intent.
- `SUPERSEDED` — a newer decision replaced it.
- `HISTORICAL` — was true when written; no longer current truth.
- `UNKNOWN` — not evidenced.

## Permanent surfaces (change only through legitimate architecture evolution)

- `docs/architecture/01_NORTH_STAR.md` (end state)
- `docs/architecture/02_ARCHITECTURE_CONTRACT.md` (binding invariants)
- `docs/governance/AUTONOMY_MANDATE.md` + `CONSTITUTION.md` (authority)
- STOP / owner-veto / evidence-integrity / verifier-independence invariants

## Current-truth surfaces (MUST be updated by every promotion/remaster mission)

At minimum, any mission that changes any of the following MUST update
`docs/CURRENT_STATE.md` (and the matrix if capability state changed):

1. current canonical HEAD;
2. schema version;
3. full-suite count/result;
4. supervisor/STOP/trust state;
5. capability state (matrix);
6. migration/recovery state;
7. evidence roots;
8. known limitations;
9. roadmap status;
10. superseded assumptions (mark old docs HISTORICAL with a status header).

## Historical surfaces

Old version plans/specs (`03_V1_AUTONOMY_KERNEL_SPEC.md`, `V*_MASTER_PROMPT.md`,
slice manifests, checkpoints) are preserved for auditability. They carry a
`CURRENT STATUS` header pointing at the matrix; their old status lines are
HISTORICAL and must never be read as current truth.

## Mechanical enforcement

`scripts/doc_staleness_check.py` verifies:

1. `docs/CURRENT_STATE.md` pins the immediate parent of the current
   documentation-only commit in `DOCUMENTATION STATE BASE SHA`; a current
   commit that changes source or tests fails closed (a document cannot contain
   its own commit SHA without a self-reference);
2. the current/candidate schema claim == `jarvis_foundry.migrations.SCHEMA_VERSION`;
3. no forbidden stale phrase in current-status surfaces
   (CURRENT_STATE.md, 00_READ_FIRST.md, 05_POST_V1_ROADMAP.md);
4. referenced evidence paths exist.

Run before closing any gate; the test `tests/foundry/test_doc_staleness_check.py`
wraps the script. Fixes to documentation counts/hashes follow evidence discipline:
regenerate evidence, never edit claims to match prose.

## Verification discipline

A documentation-only change is still verified independently (fresh session,
adversarial brief, strict JSON verdict) before it may be called reconciled.
