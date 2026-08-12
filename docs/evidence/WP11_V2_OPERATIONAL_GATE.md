# WP11 — V2 OPERATIONAL GATE (directive Phase 4 gate)

Status: **CLOSED — PASS (16/16, round-2)** (2026-08-12). Round-1 FAIL
(14/16) → repairs committed → fresh artifact → round-2 independent
adversarial verifier **PASS 16/16** (`deleg_303dac65`, verdict sha256
38c64c72…, tree clean start→end). Ledger: verification.failed:
verdict-v2-operational:round1 → verification.passed:
verdict-v2-operational:round2 + evidence.created:v2-operational-gate.
PROJECT_STATE v20: V2 promotion_state **ELIGIBLE** (both gates PASS).
Canonical requirements: OWNER_DIRECTIVE_2026-08-12_ALIGNMENT.md "V2
OPERATIONAL GATE" criteria 1–16. Candidate HEAD: `92c31f3` (clean tree at
dispatch). Live DB: schema v16, ~9,400 events, 506 real claims, 8 indexed
vault files, reconcile consistent.

## Round-1 verdict → repairs (verification.failed:verdict-v2-operational:round1)

| Criterion | Round-1 finding | Repair | Round-2 evidence |
|---|---|---|---|
| C8 | real Hermes dispatch left NO agent_run.* ledger records (adapter used directly) | gate worker routed through RoleDispatcher (records agent_run.started/completed with manifest hash + provider + result) | `agent_run ledger records for run-gate-…: 1` in gate-run-20260812T202215.txt; agent_runs row + events on the live ledger |
| C9 | live note 06 - Lessons.md held 2 leftover probe lines (624B vs pristine 440B/70f9f8bd) — 'pristine' was compared to the pre-RUN state | note restored to pristine bytes via the first gate write's before-snapshot (governed write write-15fd3f865f9d, sha256 70f9f8bd48fa… verified); gate script now anchors on the vault index (aborts on leftover mutation; final restore must equal the anchor hash + index re-sync agrees) | `anchor: 06 - Lessons.md 70f9f8bd48fa (440 bytes…)`, `bytes restored to anchor: True`, `index agrees with pristine bytes: True` in the round-2 artifact |
| (new) | vault.file_indexed key carried mtime-variant payload under a content-only key (same-content re-observation conflicted) | key now `rel:digest:mtime` (handler already upserts) | suite green; live re-sync clean |

Round-1 verifier: `deleg_886d2cd4` (independent adversarial, 16/16
criteria, provider deepseek-v4-flash — same-provider limitation
recorded). Verdict archived at
var/evidence/v2-operational-gate/verdict-round1-deleg_886d2cd4.json.

## Criterion evidence (round-2 artifact: gate-run-20260812T202215.txt, sha256 f843c31e…)

| # | Criterion | Evidence | Verdict |
|---|---|---|---|
| 1 | real vault notes indexed with provenance | 8 vault_files rows (path, content sha256, size, mtime, status) + vault.file_indexed events; the live note 06 - Lessons.md verified at its pre-gate hash 70f9f8bd48fa | PASS |
| 2 | meaningful live knowledge data | 506 knowledge_claims from REAL sources (vault, handoffs, repo git-log, system events) | PASS |
| 3 | every claim supported or marked unsupported | support computed per claim in the envelope (SUPPORTED iff source maps to an ACTIVE vault file); live worker independently returned UNSUPPORTED verdicts without fabricating | PASS |
| 4 | contradictions remain visible | detection at record time + surfaced in envelopes/manifests; live data has zero pairs (surfacing test-verified) | PASS |
| 5 | bounded relevant whole-vault retrieval | filters (mission/entity/type/confidence/source), AND-token note matching, limits + truncated, provenance/stale/support/contradictions; measured precision 0.5 / recall 1.0 / leakage 0 on labeled live probes | PASS |
| 6 | safe continuous ingestion | live cycles all sources; per-line dedup (restart-safe — crashed run resumed with zero duplicates), savepoint isolation, per-source bounds, metrics recorded | PASS |
| 7 | stale knowledge creates research candidates | 63 live items → 60 evidence-backed candidates, 3 rejected (dedup) | PASS |
| 8 | real workers receive context manifests | worker manifest (hash+sources+truncation recorded) → REAL Hermes oneshot via RoleDispatcher → COMPLETED/PASS + agent_runs ledger row (round-2) | PASS |
| 9 | governed consolidation succeeds | live consolidate 06 - Lessons.md: VERIFIED → ACCEPTED (after d8e2520511c8) via ObsidianService | PASS |
| 10 | concurrent edits conflict instead of overwrite | expected-hash conflict → ObsidianConflictError, both versions preserved, rollback refuses on re-edit (tests + machinery probes) | PASS |
| 11 | rollback restores bytes and meaning | live chain: snapshot → mutation → degradation → freeze → restore → 5 claims ROLLED_BACK → rebuild → reconciled:true; anchor-verified byte restoration | PASS |
| 12 | restart creates no duplicate mutation | mid-flow resume = exactly one restore, mutation NOT re-run; completed re-run = noop (tests + live re-runs) | PASS |
| 13 | STOP and veto remain outside note content | live probe with STOP content → 0 control events; tests | PASS |
| 14 | resource use is measured | cycle metrics (items/bytes/elapsed) in ingestion_completed events + artifact | PASS |
| 15 | full regression suite passes | 378 tests EXIT=0 at candidate HEAD | PASS |
| 16 | independent verifier reproduces the important claims | **PASS** — round-2 verifier `deleg_303dac65`: 16/16 criteria reproduced (machinery re-probed on fresh temp DBs/vaults; live criteria re-derived read-only; C8/C9 repairs A/B-discriminated; reconcile consistent on copy + live twice; tree clean start→end at 4083fd9) | PASS |

## Live-DB impact record (G3 discipline)

- Documented initialize() v10 → v16 (2026-08-12T19:26:59+02:00,
  before/after in artifact); daemon untouched.
- Live note 06 - Lessons.md: gate series consolidated it twice and
  RESTORED it — now byte-identical to its pre-gate content (440B,
  70f9f8bd48fa, index-agreed). No other live file touched.
- Run-id scheme migration conflicts (round-1 era) remain recorded as
  ingestion errors — atomic rollbacks, zero corruption, ledger intact.

## Evidence manifest (Phase 5)

docs/evidence/EVIDENCE_MANIFEST_V2_OPERATIONAL.md — updated with
round-2 hashes (artifact f843c31e…, verdict round-2 <pending>).

## Residual risks / uncertainty

- Same-provider verifier limitation (recorded).
- Live data has zero contradiction pairs (surfacing test-verified).
- Worker support semantics: claim refs carry line hashes; a worker
  comparing file hashes correctly returns UNSUPPORTED — the
  authoritative support check is the vault-index cross-reference.
- 400+ live candidates from the research queue are real but voluminous;
  V3 loop scoring will select from them.

