# JARVIS Foundry — Duty-Window Final Report & GPT-5.6 Sol Audit Handoff

**Window:** DIR-2026-08-12 12-hour autonomous duty (2026-08-12T23:15 →
2026-08-13T11:15+02:00). **Report status:** ACCUMULATING (final
numbers at window end; structural content complete).
**Audience:** the owner and the GPT-5.6 Sol audit session.

## 1. Mandate and boundaries (recap)

Owner away; per-task and per-phase approval NOT required; silence =
continue; STOP/veto live. Repository: JARVIS-Foundry (branch main, NO
remotes, no push). Live daemon home `C:\Users\b\AppData\Local\hermes\
foundry` — write access bounded to append-only ledger events, rehearsed
migrations, atomic projection rebuilds; nothing else.

## 2. What was delivered (all independently verified)

| Slice | Fix | Verifier(s) | Suite impact |
|---|---|---|---|
| MR-1 | mandatory routing (opencode_go+deepseek-v4-flash, no fallbacks; enforcement before mutation) | deleg_debc2612 PASS (10) | 414 |
| S2 | structured verdicts; exit-0 never PASS; artifact-hash binding | deleg_81eea974 PASS (10) | 429 |
| S3 | heartbeat lease renewal; terminal-monotonic projections | deleg_18d9f7b0 PASS (9) | 438 |
| S4 | exactly-once verifier resume (fresh ids, dangling-verdict correction) | deleg_a3e54630 PASS (10) | 441 |
| S1 | FIRST MISSION: source-kind-aware support + run_id normalization + v17 | deleg_12682a02 PASS (17) | 462 |
| S5 | durable monitor registry; poll integrity | R1 FAIL deleg_71efdc9e (15) → repair → R2 PASS deleg_9decc685 (16) | 468→485 |
| S6 | bounded fair scoring context; frozen recorded scores | deleg_66e30870 PASS (12 groups) | 485 |
| GATE | V4 acceptance review | deleg_a241797f PASS 3/3 (15) | — |

Ledger: every close recorded (`verification.passed` + `evidence.created`,
hash-anchored). Suite artifacts: 414→429→438→441→462→468→485, all
EXIT=0 (two documented contention flakes in supervisor-recovery tests,
pass in isolation, regenerated artifacts clean).

## 3. Live truth after the window's work

- Schema v18 (v17+v18 rehearsed on copies first); events ~16,150+;
  claims 1,003+ (998+ supported, 1 genuinely unsupported — the S1
  "894→1" claim holds exactly); candidates 678 NEW / 232 SCORED /
  3 EXECUTED / 2 SUPERSEDED / 19 REJECTED-duplicates (never selectable).
- **Reconcile: consistent, 0 mismatches** (gate verifier re-ran
  read-only at 16,059 events: 0 integrity failures). Two atomic
  projection rebuilds converged 780 + 45 drift rows — derived-state
  corrections; event payloads never rewritten.
- Supervisor RUNNING, 5s beats, 1 lease, STOP false, no veto; daemon
  survived both rebuild windows (≤2 missed beats).
- Monitors: 4 real sources registered + polled every loop cycle
  (dedup live-proven). Live loops: 3 today (01:59, 02:05, 02:26) —
  all honest WAITING; 8 V3-era WAITING loops prior.

## 4. Defect list / honest limitations for the Sol audit

1. **First real post-fix worker dispatch PENDING** (no bar-clearing
   candidate; enforcement makes non-compliant dispatch structurally
   impossible — the ledger proof is a WHEN). Historical kimi runs are
   pre-fix history.
2. **Verifier independence is same-provider-family** (DeepSeek-only
   owner constraint). Mitigations: separate sessions, frozen HEADs,
   adversarial probe matrices, hash-anchored verdicts; provider
   diversity unavailable by directive.
3. **Supervisor-recovery tests flake under pytest contention** (2
   documented instances; pass in isolation).
4. **S5 R2 close label** `verdict-v4-s5-monitors:round1` on a round-2
   event (close script key) — preserved additively (D-037); verifier
   identity + hash correct.
5. **S5 R2 evidence.created references the pre-fix suite artifact**
   (suite-v4-s5.txt, 468) — the 485 EXIT=0 is corroborated by
   suite-v4-s5-r1.txt and suite-v4-s6.txt.
6. **Ledger doc-hash drift**: R8 edits after closes changed on-disk
   docs; all verdict/rescan/backup hashes still match the ledger
   (verifier evidence integrity intact).
7. **Freshness age is ledger-relative** (latest recorded_at) — spec-
   compliant; live loop keeps 'now' real-time.
8. **Gate doc initial snapshot totals were stale** (S1/V3 snapshots
   not 02:10 re-measures) — corrected additively with the gate
   verifier's re-measured numbers.

## 5. Evidence index (for the audit's re-verification)

docs/evidence/: V4_MR1_MODEL_ROUTING.md, V4_S2_VERIFIER_TRUTH.md,
V4_S3_LEASE_MONOTONIC.md, V4_S4_EXACTLY_ONCE.md,
V4_S1_SUPPORT_SEMANTICS.md, V4_S5_MONITORS.md, V4_S6_SCORING.md,
V4_M3_REAL_LOOP.md, V4_ACCEPTANCE_GATE.md.
var/evidence/v4-{mr1-routing,s2-verifier-truth,s3-lease-monotonic,
s4-exactly-once,s1-support,s5-monitors,s6-scoring,acceptance-gate}/
verdict-*.json + suite-*.txt + backups. Checkpoints under
var/evidence/checkpoints/. Planning: docs/planning/V4_TRUTH_SLICES.md,
CURRENT_WORK_PACKAGE.yaml, DECISION_LOG (D-031..D-037),
RISK_REGISTER (RK-26..RK-29), PROJECT_STATE v22, RECOVERY_NOTES.

## 6. Next justified phase (evidence-gated)

V5 (roadmap next entry) activates only through its own engineering +
operational gates; in the interim the daemon + loop cadence continue on
the live backlog. Owner STOP/veto/redirect fully live.

## 7. Handoff instructions to Sol

1. `git log --oneline -30` + `git status` (expect clean at the final
   commit).
2. Re-verify any slice: read its evidence doc → check its verdict JSON
   sha against the ledger event (`verification.passed` payload
   verdict_hash) → run the named focused tests → re-run the verifier's
   probe matrix from the doc's lineage if needed.
3. Re-run reconcile on the live DB (read-only URI or a backup copy;
   schema must be 18).
4. Confirm the pending-routing-proof item before marking the window
   complete.
