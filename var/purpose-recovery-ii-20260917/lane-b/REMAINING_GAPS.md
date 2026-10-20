# LANE B — REMAINING GAPS (honest, post-repair)

Scope rule from the lane order: a remaining gap is allowed only when it is an
owner credential, an unavailable external service/hardware, an explicit
owner-deferred feature, a Goal-A-owned integration point, or genuinely
nonblocking polish. Everything below is classified, and anything that was
source-fixable in an owned domain was fixed instead of parked here.

## 1. Native Windows toast (OPEN — environment limitation, nonblocking)

- Domain: B19. `WindowsToastAdapter` imports `winotify` lazily; the optional
  backend is not installed in this venv, so the native toast path stays
  unexercised. The in-app toast path, dedupe, suppression and preference
  handling are proven; a failed OS toast does not corrupt state (the router
  records EMITTED only after the adapter call and isolates provider errors).
- Repair direction: install the optional backend and emit exactly one real
  owner toast; no source change is required.

## 2. Real GitHub writes / third-party authenticated sites (OPEN — owner-gated external)

- Domain: B9/B4. Disposable local Git (commit + push to a bare remote with SHA
  equality) and the authenticated localhost site are proven. Real GitHub API
  writes and third-party authenticated accounts require an owner-authorized
  target/config that does not exist in this lane; the runtime refuses
  honestly instead of fabricating one. Gmail stays owner-deferred.

## 3. Gap-bridge ownership of intent-resolution gaps (PARTIAL)

- Domain: B13/B14. Intent-resolution gaps are recorded durably
  (`fabric.gap.recorded` with the needed action, plus
  `portfolio.intent_unresolved`) and the missing `cap.*` requirement is
  attached to the plan step, so the canonical gap/WAIT/expansion bridge can
  own them on the next cycle. The bridge path itself (`bridge_missing_plan_capabilities`
  → wake → replan) was not exercised end-to-end; the resume in the
  self-expansion proof ran through the Executive's bounded retry path, which
  is a canonical resume mechanism but not the bridge wake.
- Repair direction: run one proof where the gap is opened by the bridge, the
  wake fires exactly once, and the objective resumes from the bridge's resume
  target.

## 4. Lane-A convergence items

- The normalized intent contract is implemented and documented in
  `INTEGRATION_CONTRACT.json`; the parallel lane must deliver mission intents
  in that shape (or confirm an equivalent public call) before the two lanes
  converge. Nothing in this lane blocks on it.

## Closed in the continuation session (previously listed here as OPEN)

- **Verifier routing for deterministic local actions** — repaired as R6. The
  Fabric spine already re-derived the effect, but the mission's verifier step
  for such a step was dispatched to a worker role, so a model was asked to
  judge what the runtime can recompute; the mission ended BLOCKED with no
  false success. The verifier session now re-runs the canonical JARVIS
  predicate over the step's recorded receipt, that re-derived predicate
  satisfies the acceptance rule that demands a JARVIS-owned artifact
  predicate, and `VERIFIER_ROUTING_REPAIR_PROOF.json` records the A/B
  discrimination (seam patched out = BLOCKED with the predicate refusal;
  seam enabled = PASSED with the canonical predicate).
  The repair direction recorded here previously ("reuse
  `_verify_with_postconditions` on the recorded receipt instead of an LLM
  verifier; keep LLM verification for judgment steps") is what was
  implemented, via `fabric/loop.canonical_predicate_verdict` +
  `fabric/adapter.canonical_verifier_dispatch`.

- **Binding drift on a resumed verification pass** — repaired as R7. It was
  found by the regression work for R6, not by reading a report: a pass resumed
  after the frozen binding drifted raised the ledger's `EventConflict` out of
  `MissionLoop`. `verifier_binding.py` is byte-identical to the lane base
  `ddec34e`, so this was a pre-existing defect, not a regression. The loop now
  refuses honestly (`BLOCKED` with the ledger's own reason) and leaves the
  resume to a fresh attempt.

## Explicitly NOT a gap (verified)

- Duplicate consequential effects: 0 in every proof (effect starts dedupe by
  identity; two mission runs produced distinct effects for distinct targets).
- False successes: 0 (BLOCKED/FAIL paths never emitted a completion, and a
  PASS now additionally requires the JARVIS-owned predicate evidence).
- Owner-visible native Word verification, artifact creation, authenticated
  browser read, local Git commit/push: proven from a resolved mission intent.
