# V7 HANDOFF — SUPERVISOR ADOPTION OF THE V6 MACHINERY

```text
STATUS: PENDING OWNER DECISION (per docs/architecture/04_V1_ACCEPTANCE_GATE.md)
FROM: V6 continuous self-evolution construction
TO: the JARVIS Supervisor (durable control plane) + the owner
PREPARED: 2026-08-14 (V6 closure)
BASE: JARVIS-Foundry-V6 worktree, branch v6-self-evolution
```

## 1. What V6 built (one paragraph)

V6 built the complete continuous self-evolution machinery in an
isolated sibling worktree (JARVIS-Foundry-V6, branch
v6-self-evolution, never touching the live checkout or the live DB):
improvement records with frozen hypotheses and MMIs (S1), release
registries and slots (S2), an isolated candidate builder (S3), an
evaluation lab (S4), independent verifier machinery with trusted-
verdict gates (S5), zero-effect shadow mode (S6), a supervisor-owned
canary controller (S7), atomic promotion with automatic rollback
(S8), verified restore points and state restoration (S9),
observation-driven improvement discovery (S10), the end-to-end cycle
ladder composing all of it (S11), and engineering + operational
acceptance gates (S12) — 13 slices, 32 schema migrations, 912 tests
(EXIT=0), every gate closed PASS with a fresh independent adversarial
verifier (round-2 A/B discrimination wherever round-1 found
anything), including the build's first FAIL-repair cycle (S10).

## 2. Where the code lives

- Worktree: `C:\Users\b\Documents\ChatGPT\Autonomous AI powered by
  Hoowen\JARVIS-Foundry-V6` (branch `v6-self-evolution`)
- Modules: `src/jarvis_foundry/evolution/{records,registry,builder,
  lab,verify,shadow,canary,promote,restore,discover,cycle,
  acceptance}.py`
- Migrations v21-v32 in `src/jarvis_foundry/migrations.py`
  (SCHEMA_VERSION 32); projections/reconcile/database allowlists all
  wired
- Evidence: `docs/evidence/V6_S0..S13_*.md` (committed) + the
  gitignored `var/evidence/v6-s0..s12/` suite artifacts and verdict
  JSONs + `var/v6-runtime/v6.db` (isolated runtime ledger, schema
  v32, reconcile-consistent, 2 archived acceptance runs)
- The live checkout and live DB were NEVER touched (live DB verified
  at v20 with zero V6-vocabulary events at every gate)

## 3. Acceptance state (two layers — both recorded in the ledger)

1. Engineering: PASS (acceptance-0d21daed0833 on the runtime ledger).
2. Operational minimums: PROVEN by the S11/S12 real-cycle demos with
   fresh independent verification (>=3 cycles, 2 promotions with
   measured improvement, R1 real promotion, R2 canary promotion,
   broken candidate auto-rejected, rollback exercised, no live
   damage, no self-certification) — but the runtime ledger itself
   records cycles_total 0 (acceptance-24b8e8123965, FAIL-CLOSED with
   reasons), because construction never wrote demo cycles into the
   runtime ledger. The first REAL cycles are V7's job.

## 4. V7 work package (suggested order)

1. OWNER DECISION: authorize supervisor adoption of the V6 machinery
   (this handoff is the acceptance gate input; the owner reviews the
   V6 closure evidence and decides per 04_V1_ACCEPTANCE_GATE.md).
2. Wire the supervisor's durable control plane to the cycle ladder:
   the supervisor (not a candidate) calls run_cycle with real
   observations from the live ledger (metric exceedances, guardrail
   breaches, verifier findings, task failures — S10's vocabulary).
3. First REAL autonomous cycles on the runtime ledger: pick 2-3
   genuine, bounded improvements from recorded observations (e.g. the
   suite-runtime drift, or a real verifier finding), run them through
   the ladder with REAL verifiers; the runtime ledger's operational
   gate flips to PASS when the minimums are met for real.
4. Schema adoption decision: the live DB stays v20 until the V7
   acceptance decides to migrate it (migrations v21-v32 are forward
   + idempotent; the live DB's ~31,000-event ledger replays them
   safely on an isolated copy FIRST — S9's rehearsal machinery exists
   for exactly this).
5. Operational cadence: a supervisor-owned scheduler drives cycles
   with the STOP/freeze/veto checks the machinery already enforces;
   EMERGENCY_STOP remains the supervisor's, and the owner's, last
   word.

## 5. Hard rules the machinery enforces (do not weaken in V7)

- Candidates can never: self-certify, promote themselves, lower
  their risk class, change success criteria after results, weaken
  STOP/veto/evidence/audit/redaction, forge owner directives, delete
  failed history, overwrite the only recoverable live copy, mutate
  live state in shadow mode, expand their own canary, or control
  their own termination.
- Freeze precedes rollback; rollback without state/schema
  restoration is not proof; append-only history is never rewritten.
- Verdicts: exit-code-zero alone is never PASS; criteria_results must
  be non-empty; foreign criteria refused; same-provider limitation
  recorded honestly (D-031/D-032).
- The live DB is opened read-only (mode=ro URI) by anything that is
  not the supervisor's own writer.

## 6. Owner input required

1. Approve/reject this handoff (V7 adoption).
2. If approved: name the first real improvement candidates (or
  authorize the supervisor to derive them from recorded observations
  only — never from unrecorded intuition).
3. Confirm the live-schema migration path (isolated rehearsal first).

## 7. Honest limitations carried forward

- Same-provider verifier limitation (D-031) — procedural
  independence, recorded at every gate.
- Live-DB isolation is content-level (host-written WAL disclosed).
- The evidence trail lives in the gitignored var/ directory —
  preserve it when moving V6 into the main tree (D-030 additive
  documentation applies to every doc touched).
