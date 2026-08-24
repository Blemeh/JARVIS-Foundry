# Serial Job Queue — Live GitHub + Internet Research Activation

Status legend: PENDING / IN_PROGRESS / DONE / BLOCKED / WAIT

| Job | Status | Proof location |
|---|---|---|
| G1 real GitHub identity + capability probe | DONE | `GITHUB_CAPABILITY_MATRIX.md` (identity Blemeh, scopes, rate limit, failure states) |
| G2 resource discovery / repository inventory | DONE | `PROJECT_INVENTORY.md` (3 repos, exact SHAs, local dedupe) |
| G3 internet/docs research contract | DONE | `RESEARCH_CONTRACT.md` + module + tests (23 focused) |
| G4 source quality + contradiction handling | DONE | `RESEARCH_CONTRACT.md` §G4; negation conflict, registry-vs-release disagreement, oversized-source honesty |
| G5 project change monitor | DONE | bounded `observe_repository` (drift/CI/stale/release/no-CI) + 4 live observations in `E2E_PROOF.json` |
| G6 software opportunity detector | DONE | typed opportunities w/ all G6 fields; only evidence-backed patterns emitted |
| G7 duplicate / already-solved rejection | DONE | 4 rejection layers in code+tests; campaign rejections recorded in `E2E_PROOF.json` |
| G8 existing V7A portfolio bridge | DONE | candidates+items via generate/score/register_work_item; no second scorer (`E2E_PROOF.json` → portfolio_feed) |
| G9 real research task proofs (>=5) | DONE | T1-T5 in `DISCOVERY_PROOF.md` (real Hoowen/Foundry/LeadGen/pytest data) |
| G10 real discovery campaign (>=3 repos, >=10 raw signals) | DONE | 3 repos, 4 observations + 5 research tasks + identity/rate probes; honest WAIT cases documented |
| G11 adversarial web/GitHub content | DONE | DATA_ONLY verdict, 5/5 probes flagged, zero control events (`E2E_PROOF.json` → adversarial) |
| G12 real remote reconciliation proof | DONE | push COMPLETED → ls-remote readback match → reconcile idempotent → replay performed=False → main untouched (`DISCOVERY_PROOF.md` §G12) |
| G13 project-discovery E2E | DONE | `E2E_PROOF.json` full chain; decision SELECT_CANDIDATES with top candidate cand-a72133166254 (0.487) |

## G12 evidence (live)

Governed push executed through `GitConnector`/`GitEffectAdapter` into a
disposable DB against the real origin remote:

```
action_key            v3-git:lane-g12-governed-push
local head            e9dc56dd37b4...
remote head           e9dc56dd37b4...   (git ls-remote readback == local)
reconcile             reconciled=True   (post-completion; no duplicate)
replay                performed=False   (exactly-once honored)
journal               system.action_started -> system.action_completed -> effect.reconciled
main mutated          NO
```
