# Post-V7 Skill Library. Quality Gate

## Per-procedure gate

An entry may be promoted only when all checks pass.

1. The definition is typed, hash-bound, and has an exact stable target identity.
2. Parameters, preconditions, capabilities, risk, reversibility, steps, oracle,
   evidence outputs, failure modes, repair policy, STOP/veto behavior, timeout,
   budget, version, and runner are present.
3. No hardcoded secret, owner-specific path, wildcard target, hidden retry, or
   unsupported capability appears in the definition.
4. A disposable sandbox run changes only its owned fixture root.
5. The success oracle checks observed state and evidence, not a worker claim.
6. At least one deliberate failure or ambiguity is reproduced and classified.
7. Repair or retry is bounded, explicit, and never crosses an irreversible
   boundary without a checkpoint and recovery option.
8. A fresh structured verifier recomputes the proof hash and attempts to reject
   false success, missing evidence, scope escape, and verifier self-certification.
9. Version and supersession metadata preserve in-flight history.

## Library gate

- At least 15 high-value procedures are `PROVEN_ONCE` or `TRUSTED`.
- Software engineering, research, diagnostics, repair, maintenance, failure/
  repair, versioning, quality ranking, and composite workflow are represented.
- Representative focused tests, the library factory, broader regression, and
  secret/hygiene scans are green.
- No live account, provider, credential, database, Scheduler, Supervisor,
  production, or canonical-main mutation occurred.
- Candidate branch is clean and its remote ref reconciles to local `HEAD`.

## Verdict vocabulary

`PASS`, `FAIL`, `BLOCKED`, and `NOT_TESTED` are the only verification verdicts.
Unavailable live/provider proof remains `NOT_TESTED` or `BLOCKED`; it never
becomes a catalog PASS from a fixture result.

## Run result (2026-08-24)

- Factory: `PASS` — 22/22 procedures completed in a fresh disposable root;
  22/22 independent verifiers passed; 22/22 entries promoted to
  `PROVEN_ONCE` v1.0.0; `TRUSTED=0` by policy.
- Focused catalog and V7B regression set: `PASS` — 30 tests, 0 failures,
  0 errors.
- Foundry suite: `PASS` — 1,978 tests, 0 failures, 0 errors, 1 skip;
  JUnit is `FOUNDRY_SUITE.junit.xml`.
- Convergence suite: `PASS` — 100 tests, 0 failures, 0 errors, 0 skips;
  JUnit is `CONVERGENCE_SUITE.junit.xml`.
- Combined final suite: `PASS` — 2,078 tests, 0 failures, 0 errors, 1 skip;
  JUnit is `FULL_SUITE.junit.xml`.
- Compile and whitespace checks: `PASS`.
- Source-only secret-marker scan: `PASS` (no matching credential or private-key
  markers in the new source, probe, or focused test files).
- Ruff: `NOT_TESTED` — the bundled environment has no `ruff` module.
- Live provider/account/runtime activation: `NOT_TESTED` by the lane boundary;
  no live effect was attempted.
