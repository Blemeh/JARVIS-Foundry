# WP3 Gate Report — Autonomous Action Control And Resource Registry

- **Date:** 2026-08-11 (gate closed by continuation run, cron session; amended 20:40 by continuation run 2 after verifier full-summary integration)
- **Verdict:** **PASS**
- **Package:** WP3 (autonomous action control and resource registry)
- **Previous state:** ACTIVE / NOT_TESTED (implementation done, verification in flight)

## 1. Verdict Table (acceptance criteria B1–B6 from CURRENT_WORK_PACKAGE.yaml)

| ID | Criterion | Verdict | Evidence |
|----|-----------|---------|----------|
| B1 | Traversing/symlink/junction/case-aliased/unresolvable targets denied deterministically (INVALID_TARGET) without mutation | **PASS** | Verifier scratch suite (TestB1PathBoundaries, real Windows junction escapes via `mklink /J`); repo suite `test_path_boundaries.py` (6 passed); independent probe: traversal → INVALID_TARGET, junction escape → INVALID_TARGET ("resolved target escapes registered root"), no mutation observed |
| B2 | Active owner vetoes outrank standing mandate and capability readiness | **PASS** | Repo `test_action_policy.py::test_active_owner_veto_wins_over_standing_mandate`; independent probe: VETOED with matched_veto_ids=('veto-1',) while mandate active and capability READY; precedence nuance documented and locked by `test_unready_capability_with_matching_veto_denies_not_ready` (see §3) |
| B3 | Unready capabilities produce NOT_READY decisions and no mutation authority | **PASS** | Repo `test_capability_states.py` + `test_action_policy.py::test_unready_capability_creates_no_mutation_authority`; independent probe: git_push (unregistered) → NOT_READY |
| B4 | Capability state changes are durable events; projections rebuild deterministically | **PASS** | Repo `test_capability_states.py` (durable + rebuildable transitions, idempotent per-transition events); verifier scratch suite capability state machine tests |
| B5 | Git resource registration records root/HEAD/branch/dirty/remotes/readiness; idempotent | **PASS** | Repo `test_registration.py` (6 tests incl. dirty→PROBING, idempotent re-registration, same-HEAD dirty-state update); verifier scratch TestB5Registration (dirty→clean re-register one row, duplicate delivery one row); one fail-loud finding repaired — see §3 |
| B6 | Every decision recorded with action id/reason/resolved target; duplicate deliveries produce one decision | **PASS** | Repo suite + verifier scratch suite; independent probe: duplicate delivery → exactly 1 action_decisions row; one fail-loud finding repaired — see §3 |

## 2. Independent Verification (deleg_d335614d)

- Dispatched 2026-08-11 19:58 (prior executive session), adversarial brief, fresh session.
- **Full battery:** repo suite reproduced (**94 passed** at 20:04), real junction probes on Windows (resolve() follows junctions; case-insensitivity confirmed), 65-test adversarial scratch suite covering B1–B6 **all passed at 20:15:08** (initial 8 failures were the verifier's own test bugs — event-count expectations, mklink-onto-existing-dir, case-variant filename — fixed in its scratch suite; no repo code changed), critical negative result (**no divergence between `open()` and `resolve()`** on junction/`..` paths, so the policy's resolve()-based boundary check has no lexical-escape divergence vector on this host), temp files cleaned.
- **Summary file:** `subagent-summary-0-20260811_201639_515049.txt` (landed 20:16:39) — verdict **PASS** on all six criteria, with two fail-loud integrity findings (B5, B6) and the B2 precedence nuance recorded.
- **Integration correction:** the first gate-close pass (20:23) recorded "no subagent-summary-* file" and closed with "no repo repairs required". The summary file did exist and carried two confirmed fail-loud findings. Per the run brief's honesty rule, this report is amended: the findings were repaired with regression tests in continuation run 2 (see §3) and the state was re-frozen (§5).
- Independence limitation (§14.3): verifier ran with the same provider (opencode-go / deepseek-v4-flash) as the builder — recorded, accepted for this gate. Provider diversity is preferred at future gates where available.

## 3. Findings And Repairs (verifier B5/B6 + B2 nuance)

| Finding | Repair | Regression test |
|---------|--------|-----------------|
| **B5** — same-HEAD re-registration with changed dirty status raised `EventConflict`; idempotency key `project.registered:{resource_id}:{head}` excluded `status_porcelain`, so a repo becoming dirty at an unchanged HEAD could not be re-registered (fail-loud, first registration stood, no corruption) | `registration_event()` idempotency key now covers the **full registration payload** (`project.registered:{resource_id}:{payload_hash(resource)}`): identical delivery still dedupes; any legitimate change (dirty status, remotes, capabilities, readiness) at unchanged HEAD produces a new event that the `project.registered` projection upserts onto the single resource row | `test_registration.py::test_same_head_re_registration_updates_dirty_state` (identical re-delivery dedupes with zero new events; same-HEAD dirty change updates `status_porcelain` + readiness to PROBING; exactly one resources row) |
| **B6** — same `action_id` + same decision + different reason raised `EventConflict`; key `action.decision:{action_id}:{decision}` excluded reason/target, so re-delivery after capability state changed failed loudly instead of recording | Decision idempotency key now covers the **full decision payload** (`action.decision:{action_id}:{decision}:{payload_hash(payload)}`): identical re-delivery dedupes; a changed payload records a new decision, which the `action_decisions` projection upserts (last decision wins) | `test_action_policy.py::test_decision_redelivery_dedupes_and_changed_reason_records` (identical re-delivery → one row, zero new events; capability DISCOVERED→PROBING → same NOT_READY decision with new reason recorded, no EventConflict, one row) |
| **B2 nuance** — capability-readiness check runs before veto matching, so an unready capability + matching veto yields NOT_READY (denial preserved; veto not surfaced as the decision). Safety property holds in every combination; this is intended precedence, now documented | No code change — documented behavior locked by test | `test_action_policy.py::test_unready_capability_with_matching_veto_denies_not_ready` (git_push DISCOVERED + active VETO_ACTION on the action → NOT_READY with capability reason) |

**No other FAIL findings.** The verifier's 8 initial scratch-suite failures were its own test bugs (fixed in the scratch suite; repo untouched). All other integrity concerns were recorded as accepted risks (see §7).

## 4. Executive Re-Verification (this run, fresh commands)

- `./.venv/Scripts/python.exe -m pytest tests/foundry -q --tb=short` → **133 passed** (94 + 3 WP3 regression tests + 36 WP4 role tests present in tree; fresh reproduction, exit 0).
- Focused WP3 suites: test_registration (6), test_action_policy (9), test_capability_states (5), test_path_boundaries (6) — all green.
- Frozen-state battery (var/evidence/freeze_verified_state.py wp3-gate): snapshot 50 files, foundry suite exit 0, Hermes estop baseline **24 passed**, canonical DB recheck unchanged.

## 5. Frozen-State Snapshot (var/evidence/wp3-gate/, re-frozen after repairs)

| Artifact | sha256 |
|----------|--------|
| verified-state-snapshot.json | `425b9f92603ce3783cf5ea2f5288ea046a6335614bd9d83ad1dc7d6c4fd3fa22` |
| foundry-tests-final.txt | `ebddf18a0eedb29d0c4cd882766440bf2e774f60e7d0c828e7cab90bf1968825` |
| hermes-baseline-estop-final.txt | `284fe7016720a6440f18069e211722d2c3dce869487c5d857c5cd07095bd07a2` |
| canonical-databases-recheck.json | `399ab6bb23147887712990bae0b1625ef70f1e951882df479c3b715763dc0710` (identical to first pass — canonical DBs byte-unchanged) |
| canonical-databases-full-recheck.json | fresh manifest: all 4 canonical DBs match recorded state (integrity ok, schema/event counts per checkpoint §2); wp2-staging home present |

Summary artifact: `verification-summary.json` sha256 `e06cf22ba54034d1ce244d8c4b4410ba1e0c5c3cafee13187ecddbe6d5c5c466`

Note: the snapshot covers the whole src+tests tree at gate close, which now includes the in-progress WP4 role adapter modules (src/jarvis_foundry/roles/, test_role_*.py). Their presence in the snapshot is not WP4 gate evidence — WP4 is verified and frozen separately at its own gate.

## 6. Canonical Database Integrity

- var/staging-foundation/foundry.db: schema 2, 11 events, integrity ok (matches recorded).
- var/cli-proof/foundry.db: schema 1, 0 events, integrity ok (checkpoint's "(1)" = schema version).
- var/final-proof/foundry.db: schema 2, 0 events, integrity ok (checkpoint's "(2)" = schema version).
- var/wp1-staging/foundry.db: schema 3, 0 events, sha256 `a524c85a…` unchanged vs wp1-gate record.
- var/wp2-staging: staging home present; not canonical.

## 7. Residual Risks (recorded, not gate-blocking)

- Same-provider independence limitation (§14.3); mitigated by fresh-session adversarial brief and independent re-verification.
- Junction semantics verified on this host (Windows 10, Python 3.13.5); behavior may differ on other OSes — policy tests skip gracefully off-Windows.
- TOCTOU between policy evaluation and actual file open is inherent to path-based checks; no divergence found on this host, but a hostile process with filesystem-write access to the registered root could still race. Out of scope for WP3 (documented).
- Verifier B1 note: `junction/../x` is ALLOWED because Windows/CPython collapse `..` lexically (GetFullPathName) before reparse traversal; empirically verified `open()` writes the inside file, so no escape. Boundary is point-in-time (a junction created between evaluate and execute would need re-evaluation — deterministic per state).
- `policy.evaluate()` would raise TypeError on an empty system_state table (fetchone()[0]); unreachable for migrated DBs (migration 2 inserts the singleton row).
- Repo has no git commits (untracked); frozen-state = sha256 manifests, per package contracts.

## 8. Conclusion

All six gate criteria have reproduced evidence of PASS. Two fail-loud verifier findings (B5, B6) were repaired with full-payload idempotency keys and locked by regression tests; the B2 precedence nuance is documented and tested. State re-frozen at wp3-gate bundle (new digest `425b9f92…`). WP3 declared **PASS**; next package **WP4 (Hermes role adapter)** activated automatically per gate rules.
