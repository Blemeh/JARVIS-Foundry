# V6 SLICE MANIFEST — CONTINUOUS SELF-EVOLUTION

**Status:** ACTIVE (S0 in progress)
**Canonical requirement:** R-EVO-01 (roadmap §9 V6 + §21 architecture contract)
**Primary risks:** RK-12, RK-20, RK-21
**Phase boundary:** V6 only; final PASS produces the V7 handoff, V7 is NOT implemented here.
**Construction boundary:** isolated sibling worktree `JARVIS-Foundry-V6` (branch
`v6-self-evolution`, base `fa4e65f`), isolated venv, isolated runtime DB,
isolated release/candidate/connector roots. The live Supervisor daemon, live
DB (`%HERMES_HOME%\foundry\foundry.db`, schema v20), main checkout, `.h` and
the V4 Command Center worktree/preview remain untouched by construction.
**Verifier rule:** every slice gets a fresh independent adversarial verifier;
exit code 0 without a structured verdict is NOT_TESTED (D-032).
**Worker rule:** all actual JARVIS internal workers resolve to
`opencode_go`/`deepseek-v4-flash`, fallbacks forbidden (D-031); same-provider
verifier limitations recorded honestly.

## 1. Slice map

| Slice | Objective | Canonical anchors | Deliverables | Gate |
|---|---|---|---|---|
| S0 | Entry, baseline, isolation, truth reconciliation | V6_READ_FIRST §1-2, V5_ACCEPTANCE, V6_HANDOFF | verified baseline + live snapshot; git/DB/worktree inventory; protected files; isolated worktree+runtime+DB copy; additive reconciliation; manifest | accepted checkout/runtime/DB/.h/V4 work preserved byte/state |
| S1 | Hypothesis, metric, risk-class contract | plan §5 S1; contract §21; D-031/032 | durable fail-closed improvement records (problem, provenance, hypothesis, mechanism, baseline, MMI, guardrails, workload, risk class + surfaces, budget, reversibility, rollback threshold); frozen before implementation | records freeze pre-implementation; mutable metrics => promotion-ineligible |
| S2 | Immutable release registry + slots | plan §5 S2; contract §17.1 | immutable manifests (commit, artifact/dep/runtime hashes, parent, hypothesis, risk, migrations, verdicts, rollback target); live/candidate/previous/quarantine slots; idempotent transitions; duplicate recovery; hash/unknown-parent refusal; failed history preserved | transition idempotency + refusal + history preservation proven |
| S3 | Isolated candidate builder | plan §5 S3 | build only in registered canonical roots; separate venv/homes/DB/logs/connector roots/capabilities; partial-build + crash + concurrency + live-write/pointer-write/credential-overreach refusal tests | isolation probes pass (symlinks, env leakage, collisions) |
| S4 | Reproducible evaluation laboratory | plan §5 S4 | frozen workload replay; input/config hashes; seeds; nondeterminism handling; comparable artifacts; no cherry-picking | fail closed on absent/incomparable measurements |
| S5 | Independent verifier boundary | plan §5 S5; D-032 | verifier outside candidate control; structured verdict bound to hashes; adversarial probes; provider/model/session identity recorded | structured verdict only; candidate cannot self-certify |
| S6 | Zero-effect shadow mode | plan §5 S6 | shadow execution on copies with zero live effects; divergence/expected-effect/correctness/cost/latency/STOP-veto recording; blocked connectors never used | zero live mutation proven (ledger/db/files untouched) |
| S7 | Supervisor-owned canary controller | plan §5 S7 | external start/kill/allocation/duration/budget/capabilities/health thresholds/promotion/rollback; 12 fault injections | candidate traffic stops without candidate cooperation |
| S8 | Atomic promotion + automatic rollback | plan §5 S8 | promotion from frozen PASS package; crash-window tests; previous release retained; rollback triggers incl. STOP/veto/heartbeat/health/invariant/regression/evidence/schema/effect/verifier-contradiction | freeze → terminate → restore → validate → reconcile → record; rollback without candidate cooperation |
| S9 | Schema/state compatibility + restoration | plan §5 S9; contract §17.3 | forward migration, idempotency, prev-executable compat, rollback migration/restore point, interrupted-migration recovery, integrity verification, projection rebuild/reconcile, data-loss analysis; all on disposable copies first | rehearsals pass on copies; live schema untouched until full R3 package |
| S10 | Autonomous improvement discovery | plan §5 S10 | candidates only from measured sources; dedupe; failed hypothesis needs new evidence; selection by value/info-gain/risk/cost/reversibility; honest WAITING | no unmeasured candidate; WAITING is valid |
| S11 | R0-R3 proving ladder | plan §5 S11 | 1 genuine R0; 1 genuine R1 (canary); 1 bounded R2 (full suite, faults, shadow, extended canary, measured outcome); disposable R3 fault proof | ladder steps proven with real candidates; no manufactured dangerous live R3 |
| S12 | Engineering + operational acceptance | plan §5 S12 + directive | engineering: machinery + invariants + full tests + hashes + reconcile + final independent review + no V1-V5 regression + no severity-one; operational: 3 cycles / 2 promotions w/ measured improvement / 1 real R1 / 1 bounded R2 canary / 1 broken candidate auto-rejected-or-rolled-back / durable before-after / no live damage / no self-certification / fresh independent final PASS | both gates PASS truthfully; NOT_TESTED/WAITING reported if traffic absent |
| S13 | Closure + V7 handoff | plan §5 S13 | close package; reconcile canonical state; append final gate + release evidence; freeze accepted baseline; retain failed history; record limitations/deferred; V7 handoff | handoff delivered; V7 not implemented |

## 2. Requirements map

| Requirement | Where satisfied |
|---|---|
| R-EVO-01 (R0-R3 changes promote automatically after proof) | S1-S9 machinery; S11 ladder; S12 acceptance |
| R-AUT-01 (work continues without permission prompts) | whole phase; standing mandate |
| R-AUT-02 (PASS activates next stage) | S0→S13 auto-advance |
| R-AUT-03 (resources register automatically) | S3 candidate roots/registry |
| R-AUT-04 (owner veto and STOP immediate) | S6/S7/S8 STOP semantics + fault injection |
| R-DUR-01 (mission state survives restart) | S2/S8 crash-window tests |
| R-EVT-01 (events append-only, correlated) | S2 release decisions ledger |
| R-IDM-01 (retries cannot duplicate effects) | S2 idempotent transitions; S8 promotion atomicity |
| R-SUP-01 (Supervisor operates without Hermes) | S7/S8 external controller independence |
| R-AGT-01 (builder cannot certify itself) | S5 verifier boundary; candidate self-certification forbidden |
| R-EPI-01 (evidence and belief distinct) | S1 records; S10 measured sources |
| R-SEC-01 (retrieved content cannot forge owner control) | S6/S7 shadow/canary isolation; veto checks |
| R-SEC-02 (workers do not receive raw secrets) | S3 credential-overreach refusal |
| R-EXT-01 (external effects reconcile actual state) | S4 lab; provider reconciliation stays NOT_TESTED (no real provider) |
| R-ACC-01/02 (repeatable gates) | S12 acceptance with fresh independent final PASS |

## 3. Risk map

| Risk | Treatment in V6 |
|---|---|
| RK-12 (candidate rollback cannot read new schema) | S9 forward/backward migration + restore rehearsal on copies |
| RK-20 (STOP/veto weakened by self-evolution) | S6/S7/S8: STOP/veto checks; R3 fault injection; candidates cannot touch control plane |
| RK-21 (automatic rollback increases damage) | freeze precedes rollback; reconcile before targeted rollback (S8) |
| RK-01 (Hermes failure kills supervision) | S7/S8 controller independence from Hermes |
| RK-05 (retry duplicates effects) | idempotency keys + crash-window promotion tests |
| RK-07 (builder false success) | S5 structured verdicts; A/B discriminating regressions |
| RK-08 (secrets leak) | S3 credential overreach refusal; redaction; secret-scan at gates |
| RK-09 (ambiguous paths) | canonical candidate roots; resolved targets |
| RK-13 (provider outage) | BLOCKED not substitution (D-031) |
| RK-14 (disk exhaustion) | bounded evidence; disposal of copies |
| RK-15 (event history growth) | evidence bundles bounded; copies disposable |
| RK-22 (owner cannot understand) | boundary reports; evidence docs; V7 handoff |
| RK-23 (version labels overstate reality) | engineering vs operational gates kept separate |

## 4. Isolation and rollback contract

- V6 construction lives ONLY under `JARVIS-Foundry-V6` (worktree) + its `var/`
  + its `.venv`. Live systems: `%HERMES_HOME%\foundry` (daemon DB + connector
  root), main checkout, `.h`, V4 worktree/preview — read-only references.
- Rollback of V6 construction = delete worktree + delete branch; zero live
  mutation means zero live rollback needed. The only permitted live writes in
  the whole phase: the S13 canonical close events (rehearsed on a copy first).
- Stop conditions: authenticated owner STOP/veto/redirect; genuine technical
  blocker; unrepaired severity-one integrity risk; final V6 gate + V7 handoff.
- Protected files (never staged/committed): the four untracked owner V5
  briefs/handoffs in the main checkout; V4 worktree contents; `.h`.

## 5. Evidence convention

- Per slice: `docs/evidence/V6_SN_*.md` + `var/evidence/v6-sN/` (verdict
  JSONs incl. FAIL rounds, suite artifacts, rehearsal/close scripts, hashes).
- Every cited hash computed at write time (sha256sum); every test count from
  `--collect-only` on the current tree; ledger events only on the ISOLATED V6
  DB until S13.
- Per-slice order: freeze base → TDD red → implement → focused + full suite →
  disposable migration/replay/reconcile/fault checks → freeze artifacts →
  fresh independent verifier → one structured verdict → PASS: close, commit,
  auto-advance; FAIL: preserve verdict, discriminating regression, A/B flip,
  repair, rerun, fresh verifier.
