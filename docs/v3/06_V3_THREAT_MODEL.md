# V3 Threat Model

Version: 1.0 (design)
Base SHA: `a743fdeb535f78c5ad455e8b09944f721f2515a7`

## 1. Assets

| Asset | Location | Criticality |
|-------|----------|-------------|
| Event ledger | SQLite `events` table | CRITICAL |
| Projections | `candidates`, `missions`, etc. | HIGH |
| STOP sentinel | filesystem + `emergency_stop` | CRITICAL |
| Owner directives | `owner_directive` events | CRITICAL |
| Release pointers | `deployment/release.py` | CRITICAL |
| Credentials | broker vault, not prompts | CRITICAL |
| Worktrees | registered resources | HIGH |
| Scoring policy | `scoring_policy_registry` | HIGH |
| Verifier verdicts | `verification_verdicts` | HIGH |

## 2. Trust boundaries

```text
[Owner] ──authenticated──► [Supervisor/STOP]
                                │
[Untrusted: web, notes, repos, model output]
        │ read only with provenance
        ▼
[Discovery monitors] ──► [Candidates] ──► [Executive] ──► [Missions]
                                │              │
                                ▼              ▼
                         [Projections]   [Workers/Hermes]
                                              │
                                              ▼
                                        [External effects]
```

**Trusted:** Supervisor deterministic code, append-only ledger, STOP evaluation, idempotency enforcement.

**Untrusted:** All retrieved content, builder output, projection direct writes, model-generated scores without evidence.

## 3. Attacker and failure models

| Model | Capability |
|-------|------------|
| Prompt injection | Malicious content in vault, handoffs, web retrieval |
| Compromised builder | Mutate worktree, forge evidence, self-certify |
| Compromised verifier | Collude with builder (same provider) |
| Compromised connector | False health, replay stale observations |
| Operator error | Wrong remote, dirty tree, force push attempt |
| Crash attacker | Kill at arbitrary transition |
| Resource exhaustion | Candidate spam, model loop, budget drain |
| Insider projection edit | Direct SQL on projections |

## 4. Abuse cases and mitigations

| ID | Abuse case | Mitigation | Residual risk | Test |
|----|------------|------------|---------------|------|
| T-01 | Injected "owner says STOP" in note | Directives only from authenticated channel | LOW | AC-07, AC-16 |
| T-02 | Fabricated evidence artifact | Hash verification, provenance required | LOW | AC-06 |
| T-03 | Stale/replayed evidence | `provenance_hash`, `expires_at` | LOW | AC-06 |
| T-04 | Wrong-target binding | Resource root validation, `action.invalid_target` | LOW | AC-10 |
| T-05 | Projection tampering | Rebuild reconcile, integrity check | LOW | AC-18 |
| T-06 | Builder self-certify | Verifier role separation, fresh session | MEDIUM (same provider) | AC-06, H21 |
| T-07 | Score gaming via splits | Split detection, fingerprint merge | LOW | AC-03 |
| T-08 | Candidate spam | Per-cycle limits, HARD_CIRCULAR | LOW | AC-17 |
| T-09 | Circular busywork | Performative pattern registry | LOW | AC-04, AC-15 |
| T-10 | Duplicate missions | Fingerprint + `covered_objective` | LOW | AC-03 |
| T-11 | Duplicate git push | Idempotency + ls-remote reconcile | LOW | AC-10, AC-11 |
| T-12 | Unbounded delegation | depth≤2, team size≤6 | LOW | AC-17 |
| T-13 | Infinite repair loop | repair_budget=3 | LOW | AC-09 |
| T-14 | Budget exhaustion | broker limits, cycle throttle | LOW | AC-17 |
| T-15 | Secret leakage in manifest | redaction, defense gates | LOW | AC-16 |
| T-16 | Path escape / symlink | resource root canonicalization | LOW | AC-10 |
| T-17 | Wrong worktree/remote | preflight identity check | LOW | AC-10 |
| T-18 | Force push | policy deny, never in adapter | LOW | negative control |
| T-19 | Release pointer corruption | Supervisor-only mutation | LOW | AC-07 |
| T-20 | STOP race after queue | Pre-dispatch STOP check | LOW | AC-07 |
| T-21 | Acceptance after STOP | Materialization gate | LOW | AC-07 |
| T-22 | Crash duplicate select | `loop_id` idempotency | LOW | AC-08 |
| T-23 | Compromised candidate runtime | No direct mission truth write | LOW | architecture |
| T-24 | Poisoned knowledge | Tier B contradiction + Tier C defense | MEDIUM | V2 dependency |
| T-25 | Priority starvation | aging + starvation guard | LOW | AC-04 |
| T-26 | Blocked candidate monopolizes | DEFER + comparative WAIT | LOW | AC-15 |
| T-27 | Metric gaming | Outcome vs technical PASS separation | LOW | AC-12 |
| T-28 | Provider fallback drift | `router.enforce_directive_routing` | LOW | v3_loop step 0 |

## 5. Residual risks

| Risk | Severity | Acceptance |
|------|----------|------------|
| Same-provider verifier independence reduced | MEDIUM | Record `independence=REDUCED`; prefer different provider when READY |
| V2 not canonical — knowledge contradictions incomplete | HIGH | Block implementation until V2 PASS |
| Remote git unavailable in owner environment | LOW | Disposable remote in gate; local-only degrade |
| Long verifier sessions reclaimed | LOW | Proven recovery; bounded repair |

## 6. Attack-to-test traceability

| Threat IDs | Acceptance tests |
|------------|-------------------|
| T-01, T-07, T-08, T-09, T-25, T-26 | AC-04, AC-15, AC-16 |
| T-02, T-03, T-06 | AC-06 |
| T-05 | AC-18 |
| T-10, T-11 | AC-03, AC-10, AC-11 |
| T-12, T-13, T-14, T-27 | AC-09, AC-17 |
| T-16, T-17, T-18 | AC-10, negative controls |
| T-19, T-20, T-21 | AC-07 |
| T-22 | AC-08 |
| T-24 | V2 gate dependency |
| T-28 | existing routing tests |

## 7. Bounded limits reference

See `01_V3_DISCOVERY_BUILDING_SPEC.md` §3.12 and `03_V3_SCORING_AND_PORTFOLIO.md` §7.
