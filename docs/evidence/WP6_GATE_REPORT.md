# WP6 Gate Report — Governed V1 Obsidian Write-Back

- **Date:** 2026-08-11 (continuation run)
- **Verdict:** **PASS**
- **Package:** WP6 (governed V1 Obsidian write-back)
- **Previous state:** ACTIVE / NOT_TESTED (implementation complete, unverified)

## 1. Verdict Table (acceptance criteria E1–E7 from CURRENT_WORK_PACKAGE.yaml)

| ID | Criterion | Verdict | Evidence |
|----|-----------|---------|----------|
| E1 | Concurrent edit and rollback tests preserve both meaning and bytes (gate) | **PASS** | Verifier's own gate scenario (35/35 scratch checks): write → concurrent edit → conflict → BOTH versions byte-exact preserved → rollback → byte-exact restore; clobber guard refuses rollback over newer edits |
| E2 | V1 note set (00-07 + Audit/) with the exact frontmatter contract | **PASS** | Note-set creation test; frontmatter matrix 37/37 (all 4 keys exact values required; extra keys/CRLF/quoted/colon-values accepted; missing/wrong/unterminated rejected with zero mutation) |
| E3 | Every write follows the protocol and records obsidian.write_started + write_completed with expected/after hashes | **PASS** | Transactional protocol test; verifier payload-field alignment checks; idempotent re-delivery dedupes |
| E4 | Concurrent edit → obsidian.conflict, both versions preserved, note never corrupted | **PASS** | Conflict raises ObsidianConflictError (write_id + preserved_path); note on disk keeps the concurrent edit; incoming bytes under Audit/conflicts/; CONFLICT row + event sequence verified |
| E5 | Rollback restores pre-write bytes exactly; rebuild reproduces audit state | **PASS** | Byte-exact restore + rolled_back event + rollback pointer; rebuild_projections + reconcile consistent after write+conflict+rollback chains |
| E6 | Writes confined to the V1 write root; rest of vault read-only | **PASS** | Boundary matrix 58/58 (traversal/absolute/drive/UNC/audit-path/note-name/junction/symlink/read-path escapes all refused before any mutation); snapshots confined; restore containment hardened |
| E7 | Real vault never written by tests; all fixtures temp vaults | **PASS** | Verifier confirmed `C:\Users\b\Blendi` never referenced or touched; zero scope violations |

## 2. Independent Verification

- **deleg_d32753dd** (fresh adversarial session): 156-check scratch suite
  across all 10 mandated areas + a TOCTOU probe; full suite reproduced twice
  (179 passed at review time, exit 0); schema v4 verified (migrations
  1..4, obsidian_writes columns + status CHECK); **PASS on E1–E7**, zero
  gate-failing findings. Summary:
  `subagent-summary-0-20260811_223955_463451.txt`.
- Integrity note: the WP7 planning doc appeared mid-review (docs only —
  planning for the next package); 81-file sha256 baseline otherwise
  identical; source tree untouched.
- Independence limitation (§14.3): verifier ran under the same provider
  family as the builder (opencode-go / deepseek-v4-flash); fresh isolated
  session, all evidence reproduced from scratch.

## 3. Findings And Hardening

**Verifier observations, all documented; two hardened before the freeze:**

| Finding | Severity | Disposition |
|---------|----------|-------------|
| TOCTOU between the expected-hash check and `os.replace` — an edit landing inside the window is overwritten and not snapshotted | residual | Inherent to check-then-replace (no OS-level CAS on Windows); the realistic between-write concurrent edit is fully handled. Documented as residual risk (04 gate F11 still applies: the conflict test uses the between-read-and-write window, which is handled) |
| `SnapshotStore.restore()` had no target confinement at module level | defense-in-depth | **Hardened**: restore now refuses targets outside the V1 write root (regression test `test_restore_refuses_target_outside_write_root`) |
| Conflict-preserve file write was non-atomic | minor | **Hardened**: preserved copies now use the atomic writer (never partial) |
| Leftover `.tmp` files after a crash are not swept | cosmetic | Target is never partial (temp + os.replace); documented |
| Parser leniency: `--- trailing` accepted; BOM rejected; leading-slash reads normalized | documented | Verified contained; no action |
| `expected_hash=None` = documented unconditional overwrite | documented | Clobber guard still protects rollback |
| `ensure_v1_root` has no product caller — note set created by fixtures | WP7 scope | Bootstrap wiring is WP7 integration work (recorded in WP7 spec) |

## 4. Executive Re-Verification (fresh commands, final frozen tree)

- `./.venv/Scripts/python.exe -m pytest tests/foundry -q --tb=short` →
  **180 passed** (exit 0) — after hardening and inside the freeze battery.
- Hermes baseline (isolated HERMES_HOME): `tests/test_estop.py` → **24 passed**.
- Canonical DBs: all 4 re-hashed → unchanged (full recheck at freeze).
- Schema v4 confirmed on fresh DBs (schema_migrations 1..4).
- No stray processes; no JARVIS/Foundry scheduled tasks; tree settled.

## 5. Frozen-State Snapshot (var/evidence/wp6-gate/, frozen AFTER hardening)

| Artifact | sha256 |
|----------|--------|
| verified-state-snapshot.json | `8227654afa663eb3e9ec97be0b532b4a55fae5ad8188f0789f98ce8466b40150` |
| foundry-tests-final.txt | `bb1d70473b2cc157244fa8b60a863e44e1ca7e41de8fa7341df9bcee4ef27429` |
| hermes-baseline-estop-final.txt | `ad1bbd3d25019ff33ee6f9dbd6d05f930b2bc82fe358380a8fbe2a35c2677946` |
| canonical-databases-recheck.json | `399ab6bb23147887712990bae0b1625ef70f1e951882df479c3b715763dc0710` (identical across all gates) |
| canonical-databases-full-recheck.json | `ad7ab899de42c13b532df024b0c85d178b0c1c4cc79ead2131cd8cbaa26a22da` |

Summary artifact: `verification-summary.json` sha256
`77501e1d79fbb8d59f5facaa88ec16f7ea083f55de041cd0e1574422d68ceffc`

## 6. Residual Risks (recorded, not gate-blocking)

- TOCTOU window between hash check and atomic replace (inherent; no OS-level
  CAS on Windows) — documented; conflict detection covers the realistic
  between-read-and-write concurrent edit.
- `.tmp` files from a crash mid-write are not swept (cosmetic; target never
  partial).
- `ensure_v1_root` bootstrap wiring is WP7 integration scope.
- Same-provider independence limitation (§14.3).
- Repo has no git commits (untracked); frozen-state = sha256 manifests.

## 7. Conclusion

All seven gate criteria have reproduced evidence of PASS from an independent
adversarial verifier (156-check scratch suite, nothing broken) and fresh
executive re-verification. Two defense-in-depth observations were hardened
before the freeze. The governed write protocol — expected hash, before
snapshot, atomic replace, parse check, after hash, audit event, rollback
pointer — is implemented, conflict-safe, byte-exact on rollback, and confined
to the V1 projection area. WP6 declared **PASS**; next package
**WP7 (V1 integration and activation)** activated automatically per gate
rules.
