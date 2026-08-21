# Lane Review Procedure (24h Independent Red-Team)

Reviewer constitution: acceptance-gate-review v2.0.0. Verdicts limited to
PASS / FAIL / BLOCKED / NOT_TESTED. Builder artifacts are claims, never proof.
Model lock: x-preview-f-free only, no subagents unless model proven.

## Trigger
`lane_watch.jsonl` shows a lane ref SHA different from base
7ac0b2373c8d4c74924fcea15c028c4f6e9cdc3c AND/OR a non-empty
`final_added_by_lane` containing a NEW FINAL.json committed by the lane.

## Ordered steps (per lane)

1. PIN: record lane HEAD SHA + full diff vs base (`git diff --stat base..lane`
   then full patch saved to scratch). If FINAL.json names its own reviewed SHA,
   verify it matches the branch HEAD; mismatch = stale/replayed-evidence attack,
   record and treat the branch tip as authoritative target of review.
2. SCOPE AUDIT: classify every changed file (source / tests / migrations /
   docs / packaging / evidence). Any touch outside declared lane scope is a
   finding. Hidden live dependencies: grep diff for absolute paths
   (`hermes\\foundry`, `%LOCALAPPDATA%`, `AppData`), network endpoints, and
   provider/model identifiers violating the model lock.
3. SOURCE INSPECT: read the actual changed source (not the FINAL summary).
   Migrations: verify additive-only or documented; check schema_migrations
   interplay; interrupted-migration behavior from code paths.
4. TESTS: enumerate test files added/changed; run FOCUSED new tests first via
   review venv against a disposable copy home (never live). Then FULL suite
   `tests/foundry -q` bare (no pipes), exit code recorded, counts taken from
   the summary line only. Compare against base control log
   `C:/temp/JARVIS-24H-scratch/base_suite_7ac0b23.log`.
5. ATTACK MATRIX (disposable copies only): self-certification (does the lane
   grade its own output?); false-success (planted wrong-artifact reconciliation);
   duplicate events; restart/resume mid-write; STOP/veto precedence over the
   lane feature; path escape in any new file-writing surface; concurrent edits;
   previous-executable compatibility (PREVIOUS release content still loads);
   rollback assessment (can the lane change be reverted without orphaning state).
6. EVIDENCE INTEGRITY: every artifact the lane cites - confirm exists, hash if
   claimed, timestamp consistent with chronology, produced by commands that
   appear reproducible. Re-derive counts independently.
7. POST-VERDICT MUTATION GUARD: before writing the verdict, re-resolve the lane
   SHA; if it moved since step 1, invalidate all intermediate conclusions and
   restart at step 1 on the new SHA.
8. VERDICT: per-criterion states, overall derived per skill section 18.
   Material uncertainty is never PASS. Record required repair + integration
   safety explicitly.

## Outputs
`evidence/24h/review/independent-verifier-<sha>.json` /
`evidence/24h/review/v2-knowledge-brain-<sha>.json`, committed locally only.
