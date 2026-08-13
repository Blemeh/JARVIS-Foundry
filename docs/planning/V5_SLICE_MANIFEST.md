# V5 SLICE MANIFEST — MULTI-SYSTEM INTELLIGENCE

**Phase:** V5 (roadmap §8, docs/architecture/05_POST_V1_ROADMAP.md)
**Control briefs:** V5_MASTER_PROMPT.md (full-phase contract) +
V5_READ_FIRST.md (anti-drift brief)
**Date:** 2026-08-13
**Owner control:** standing autonomy; authenticated STOP/veto/redirect always
authoritative; no-push in force.
**Canonical phase state:** PROJECT_STATE.md v22 and
CURRENT_WORK_PACKAGE.yaml still name V4 continuous autonomy as active —
per V5_READ_FIRST §2/§4 these are NOT rewritten by this phase's own drafts;
the V5 phase record lives here and in `docs/evidence/V5_S*.md` until the
V5 final gate (S9) reconciles the transition.

## 1. Authority chain (read in order before any edit)

1. Latest authenticated owner STOP/veto/redirect/constraint (none active;
   live `directives` holds 4 benign active directives: gate-progression,
   seven-day-waiver, master-plan-alignment, window-extension).
2. `PROJECT_STATE.md` (v22; V4 ACTIVE, gates: V1 eng PASS/op NOT_TESTED,
   V2 PROMOTED, V3 PROMOTED, V4 ACTIVATED; trust TRUSTED_AUTONOMOUS).
3. `docs/planning/CURRENT_WORK_PACKAGE.yaml` (V4_CONTINUOUS_AUTONOMY).
4. `docs/planning/V5_READ_FIRST.md` + `docs/planning/V5_MASTER_PROMPT.md`
   (this phase's owner-authored control briefs — untracked, owner's files,
   never committed by the executor).
5. `docs/architecture/00_READ_FIRST.md`,
   `docs/architecture/02_ARCHITECTURE_CONTRACT.md` (permanent invariants:
   ActionEnvelope §7, credential/capability broker §8, emergency stop §9,
   resource registry §6, verification §15, security §18, budget §19).
6. `docs/architecture/05_POST_V1_ROADMAP.md` §8 (V5 outcome/capabilities/
   exit evidence).
7. `docs/governance/AUTONOMY_MANDATE.md` (standing autonomy; veto/STOP).
8. `docs/planning/REQUIREMENTS_TRACEABILITY.md` (R-AUT-03/04, R-IDM-01,
   R-SEC-01/02, R-EXT-01), `RISK_REGISTER.md` (RK-05/08/09/10/20/21/24/25),
   `DECISION_LOG.md` (D-031..D-037), `AGENTS.md`, `HERMES.md`.
9. Drafts (this manifest, V5_WORK_PACKAGE.yaml, evidence docs) are
   evidence under review — never self-promoting truth.

Precedence when sources disagree:
`owner directive → PROJECT_STATE.md → CURRENT_WORK_PACKAGE.yaml →
architecture/governance contracts → roadmap → traceability/risk/decision
records → bounded drafts → model output/transcripts.`

## 2. Baseline classification (2026-08-13, HEAD 7e54bbc)

- HEAD `7e54bbc` (handoff commit), branch main, NO remotes (no-push by
  construction), working tree clean except the owner's two untracked brief
  files (`docs/planning/V5_MASTER_PROMPT.md`, `V5_READ_FIRST.md` — owner's
  in-flight authorship; DO NOT commit/move).
- **Accepted V5 baseline under gate review:** `e6ac89f` feat(v5-s1)
  System Registry (src + 20 tests) and `3120cd3` docs(v5) (evidence doc +
  work package). Independent verifier `deleg_d7639766` verdict **PASS**
  (24 probes; archived `var/evidence/v5-s1-systems/verdict-deleg_d7639766.json`,
  sha256 `eb8ac515539feb66…`). Code under review byte-identical to e6ac89f
  (verifier confirmed empty src/tests diff to HEAD).
- **Verdict findings (all non-blocking; repair set in S1 close):**
  - MEDIUM P2(d): frozen→reconciled stays blocked (reconciled sets
    SUSPENDED; gate reopens only via supervisor.started). Fail-closed,
    spec-compliant; probe-matrix expectation corrected; dead clause noted.
  - LOW: stale docstring systems.py:98 (says FROZEN/STOPPED/SUSPENDED;
    code blocks only FROZEN/SUSPENDED — plain STOPPED intentionally not
    a freeze).
  - LOW: VETO_ACTION matching exact/case-sensitive — consistent with the
    pre-existing ActionPolicy convention (control/policy.py); no bypass
    risk until wired into production selection (S2+).
  - INFO: secret guard covers capability/probe payloads only (name/kind/
    target_ref unguarded — trust boundary, deliberate design); row()
    allowlist not extended (bounded-read helper only; PROJECTION_TABLES +
    reconcile map DO cover both tables); doc claims test coverage for
    'reconciled'/'plain-STOPPED' that the test file lacks.
- **Unrelated/other:** V4 evidence docs, loop artifacts, cadence keeper
  subagent `deleg_4ea0c87e` (old session; results land on disk — live loop
  JSONs already observed honest WAITING, top 0.25 < 0.3).
- **Live system (read-only inspection, var/evidence/v5_s0_live_inspect.py):**
  schema v18 (v19 staged: systems/system_actions NOT yet present);
  21,961 events; supervisor RUNNING (instance supervisor-6d250614…,
  heartbeat ≤5 s fresh); trust LAB; STOP false, no stop marker, no active
  STOP_EVERYTHING/VETO; 4 directives benign; 4 monitors registered+polled;
  1,052 claims; 934 candidates; 4 missions; 36 verification.passed /
  41 evidence.created events. The only `system.%` event is the 2026-08-11
  `system.start_build_recorded` — the V5 registry has NOT touched live.

## 3. Slice plan (execution order, dependency-linked)

| Slice | Purpose | Depends on | Evidence doc | Gate summary |
|---|---|---|---|---|
| S0 | Entry/baseline/phase control | — | V5_S0_ENTRY.md | baseline reproducible; no owner conflict; no S1 regression; all slices traceable |
| S1 | System/resource registry + capability catalog | S0 | V5_S1_SYSTEMS.md | PASS (deleg_d7639766, 24 probes) + close |
| S2 | ActionEnvelope + deterministic policy broker (15 steps, 7 codes) | S1 | V5_S2_*.md | STOP/veto race, invalid target, missing recovery, budget, stale before-state, duplicate envelope, bypass |
| S3 | Scoped capability + credential broker (short-lived, redaction, revocation) | S2 | V5_S3_*.md | expiry, replay, escalation, STOP revocation, redaction, ledger scans, unavailable provider |
| S4 | Connector contract/registry/health/quota/cost/routing + fakes | S3 | V5_S4_*.md | outage isolation, quota/budget exhaustion, reroute, disable/re-enable, capability mismatch, STOP, version/replay |
| S5 | Cross-system entities + event correlation | S4 | V5_S5_*.md | duplicates, conflicting ids, stale refs, cross-source ordering, content-forged owner control, provenance |
| S6 | External-effect execution, idempotency, reconciliation | S5 | V5_S6_*.md | duplicate delivery, conflicting payload, crash window, provider disagreement, timeout, partial, compensation, append-only correction |
| S7 | Mission-loop integration + autonomous operation | S6 | V5_S7_*.md | end-to-end observe→select→envelope→authorize→execute→reconcile→verify→learn; interruption; outage isolation; STOP in-flight; no duplicates |
| S8 | Activation of genuinely available connectors (per-connector mini-gate) | S7 | V5_S8_*.md | per connector: identity..rollback; unavailable → honest READY/NOT_READY/BLOCKED + durable blocker |
| S9 | Full V5 acceptance + handoff (capability matrix, exit evidence, V6 queue) | S0–S8 | V5_ACCEPTANCE_GATE.md | both engineering+operational verdicts PASS; no severity-one; independent verifier agrees |

Every slice: requirement mapping → focused failing tests → smallest
reversible diff → focused + full suite → disposable rehearsal (migration/
replay/rebuild/reconcile/duplicate delivery) → freeze → fresh independent
adversarial verifier → exactly one verdict (PASS/FAIL/BLOCKED/NOT_TESTED)
→ on PASS: evidence close + local commit → continue automatically.

## 4. Evidence locations

- Verdicts: `var/evidence/v5-s<slice>-systems/verdict-*.json` (every
  round archived, FAIL rounds annotated with the resolving PASS round).
- Suite artifacts: `var/evidence/v5-s<slice>-systems/suite-<bundle>.txt`
  (EXIT=0 line + zero FAILED lines; regenerated to a NEW path per round —
  never overwrite a committed artifact).
- Live-copy rehearsal bundles: `var/evidence/v5-s<slice>-systems/rehearsal-*/`.
- Ledger close events: `verification.passed:verdict-v5-s<slice>-systems:roundN`
  + `evidence.created:v5-s<slice>-systems-close`.
- Docs: `docs/evidence/V5_S*.md`; slice records: `docs/planning/V5_S*_WORK_PACKAGE.closed.yaml`.

## 5. Rollback plan (per slice)

- Any slice diff is revertible via `git revert` of its single slice commit
  (or `git checkout <prev> -- <files>` for uncommitted repairs); V5-S1's
  migration is additive-only (CREATE TABLE IF NOT EXISTS) — v19 rollback =
  drop the two tables + delete the schema_migrations row, rehearsed on
  copies before live application.
- Live DB: migrations rehearsed on a WAL-safe backup copy (sqlite3
  .backup API) before the live close; projection rebuilds run in ONE
  transaction (BEGIN IMMEDIATE, D-036); daemon stall ≤2 beats per rebuild.
- The registry is NOT wired into live action selection before S7 — no
  live behavior depends on it until then.

## 6. Final-gate checklist (S9; not satisfied before all slices pass)

- [ ] Full suite + per-slice focused artifacts (all EXIT=0).
- [ ] Frozen source tree + exact diff manifest (head + hashes).
- [ ] Disposable migration/replay/projection/reconciliation proof from
      schema 18 forward.
- [ ] Independent adversarial verifier (fresh session, no builder context).
- [ ] Capability matrix: READY / NOT_READY / BLOCKED / NOT_TESTED per
      connector and action class.
- [ ] STOP/veto/revocation proof (live + regression).
- [ ] Secret-leak scans (prompts, logs, ledger, evidence, docs).
- [ ] Duplicate/retry/idempotency proof (effect journal).
- [ ] Connector-outage isolation proof.
- [ ] Provider-side reconciliation evidence for every claimed external/
      financial effect (R-EXT-01); anything else recorded NOT_TESTED/BLOCKED.
- [ ] Unresolved limitations recorded, not relabeled PASS.
- [ ] V5 final handoff queuing roadmap V6 — WITHOUT implementing V6.

## 7. Stop conditions

Authenticated owner STOP/veto/redirect; genuine technical blocker
(recorded, resumable); untraceable change; guard removal; live-state
mutation outside a rehearsed, in-scope transition; context loss (write a
bounded checkpoint first). Routine approval is never a stop condition.
