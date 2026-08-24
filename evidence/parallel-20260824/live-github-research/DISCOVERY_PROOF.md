# Discovery Proof — Real Campaign Results (G9/G10/G13)

Machine-readable truth: `E2E_PROOF.json` (same directory).
Campaign driver: bounded, serial, 21 GitHub API calls + 1 HTTPS docs read,
run through the NEW lane modules over EXISTING canonical machinery into a
DISPOSABLE database. No live runtime/DB touched.

## E2E chain (G13) — all steps through JARVIS code paths

```
real identity probe (Blemeh, rate 4994/5000 after campaign)
→ inventory of 3 accessible repos (exact SHAs recorded)
→ change observations (5 raw)
→ typed opportunities (3 accepted)
→ duplicate/quality rejection layers
→ generate_candidate (canonical dedupe) + score_candidate (canonical truth)
→ register_work_item (EXISTING V7A portfolio; classes mapped)
→ decision: SELECT_CANDIDATES (top: cand-a72133166254 score 0.487)
```

This lane does NOT implement any selected candidate.

## Raw observations (G5/G6)

| Type | Repo | Detail |
|---|---|---|
| CI_FAILED | JARVIS-Foundry | windows-ci.yml failed 2026-08-19 (ran on side branch `remaster/post-v7-purpose`) |
| NO_CI_ON_DEFAULT | LeadGen | no Actions runs on record (total_count=0) |
| NEW_ISSUE/PR | LeadGen | 1 open PR (#1: "fix: never derive verified gaps from contradictory audit evidence") — appeared BETWEEN campaign runs; live drift detection proof |
| CI_FAILED | Hoowen | workflow 'CI' failure at 2026-08-24T18:08:26Z |
| CI_FAILED | Hoowen | 3 recent failing runs — RECURRING pattern |

Monitor note (G5): the campaign ran twice this lane. The second run caught
LeadGen's newly opened PR that the first run's snapshot did not contain —
bounded re-observation surfaces estate changes without aggressive polling.

## Candidates fed to V7A (existing scorer's verdicts)

| Candidate | Repo | Kind→Portfolio class | Canonical score | Verdict |
|---|---|---|---|---|
| cand-7209962efb3c | Hoowen | CI_FAILURE→REPAIR | **0.487** | PASSES selection bar (≥0.35) → top recommendation for a later build lane |
| cand-8d66f291129a | JARVIS-Foundry | CI_FAILURE→REPAIR | 0.305 | below bar → honest WAIT (single non-recurring failure on side branch) |
| cand-9b10cb474443 | LeadGen | MISSING_TEST→MAINTENANCE | 0.239 | below bar → honest WAIT |

## Honest WAIT cases (G10)

- "LeadGen does not currently warrant work": zero issues, zero releases,
  zero CI history, active owner use — only generic gap remains (0.239).
- "JARVIS-Foundry CI_FAILURE waits": the sole red run targeted an old side
  branch; default branch is not currently red — fixing main CI is
  maintenance, not urgent repair (0.305).

## Duplicate / quality rejection (G7)

Layers proven in tests (`test_project_discovery.py`): canonicalized-objective
coverage vs existing candidates AND missions; open-issue overlap rejection;
already-fixed-on-default rejection; intra-batch duplicate keys.
Campaign rejections: none required beyond scoring (no open issues exist to
collide with; batch keys unique) — every layer is enforced in code and tests,
not merely claimed here. Weak/vague findings are structurally impossible:
`detect_opportunities` emits only evidence-backed typed patterns and the
canonical scorer rejects low-signal objectives.

## Real research tasks (G9) — five, tied to accessible projects

| ID | Question | Answer (evidence-backed) | Conflicts | Unknown kept |
|---|---|---|---|---|
| T1 | why does Hoowen CI fail? | run 32760688893: vitest unit tests, PostgreSQL integration, backend unit ALL failing; docker build OK | none | root cause inside suites needs local repro (UNKNOWN recorded w/ experiment) |
| T2 | is Foundry CI healthy on default? | windows-ci.yml ABSENT on default (contents API); single red run was side branch `remaster/post-v7-purpose` | none | — |
| T3 | does LeadGen have CI? | actions total_count=0 — never had any | none | — |
| T4 | current stable pytest? | registry JSON > read cap → NOT inferred from truncated data; upstream release tag 9.1.1 used as fallback evidence | oversized-source-vs-bounded-reader noted honestly | — |
| T5 | are Hoowen failures tracked? | issue search total_count=0 — untracked | none | — |

All five produced claims with provider/location/retrieved_at/hash provenance;
T1 additionally linked connector attempt hashes as append-only evidence.

## G12 — real remote reconciliation proof (this branch only)

```
branch            candidate/live-github-research-activation-20260824
local head        e9dc56dd37b4...
push outcome      COMPLETED        (GitEffectAdapter exactly-once journal)
remote readback   ls-remote == e9dc56dd37b4... (matches local exactly)
reconcile         reconciled=True  (post-completion reconcile adds nothing)
replay            performed=False  (duplicate push refused by design)
main mutated      NO               (origin/main unchanged across operation)
journal events    system.action_started → system.action_completed → effect.reconciled
```

Lost-response ambiguity path is covered by existing machinery tests
(`test_git_connector.py::test_ambiguous_push_requires_provider_reconciliation`)
and honored here via the same journal: while pending, retry raises until
ls-remote resolves; replay after completion never double-pushes.
