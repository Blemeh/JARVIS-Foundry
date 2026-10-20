# LANE B — GAP BACKLOG (final, post verifier-routing repair)

Base `ddec34efc96b9a0154eb9821d6cc496ef76906a3` → lane branch (see HANDOFF.json for the exact head).

## Repaired in this lane (all were REAL_BUT_MANUAL / WIRED_BUT_UNPROVEN / broken behaviours)

| Domain | Was | Root gap | Repair | Proof |
| --- | --- | --- | --- | --- |
| B1 mission→capability binding | REAL_BUT_MANUAL | recipe id + hand-built params required; canonical intent resolver unused in the mission path | normalized MissionIntent + deterministic resolver + role binding (`mission/intent.py`, recipes declare `needs`/`bind`) | UNSCRIPTED_RUNTIME_PROOF.json 11/11 |
| B2 workflow.run | WIRED_BUT_UNPROVEN | only reachable with a harness-authored envelope | resolution produces the envelope; normal mission dispatch executes nested recipes | UNSCRIPTED_RUNTIME_PROOF.json, CAMPAIGN_CONTINUITY_PROOF.json |
| B3 operator selection seam | REAL_BUT_MANUAL | required a recipe id | needs-based selection with declared binding | UNSCRIPTED_RUNTIME_PROOF.json |
| B7 cross-system composition | REAL_BUT_MANUAL | chain required a developer-named recipe | runtime-selected workflow composes browser→artifact→native→git | UNSCRIPTED_RUNTIME_PROOF.json |
| B13 real gap detection | WIRED_BUT_UNPROVEN | gaps were created by drivers, not by a mission requirement | resolution GAP records a durable gap from the mission's own requirement | SELF_EXPANSION_PROOF.json |
| B18 Command Center truth | WIRED_BUT_UNPROVEN | projection over real runtime data unverified | snapshot built from real candidate DB; all required facts present, no simulated values | COMMAND_CENTER_PROOF.md |
| (readiness) registered resource eligibility | blocked by construction | registration never projected declared capability readiness | one-time `sync_registered_resource_capabilities` per cycle | focused tests + all proofs |
| (browser) session recovery | unrecoverable | reauth seam missing from Fabric dispatch | `browser_reauth_provider` plumbed through the adapter | FAILURE_REPAIR_PROOF.json |
| (receipt truth) post-recovery read | wrong URL | receipt described the pre-recovery redirect | re-read the requested target on the recovered session | FAILURE_REPAIR_PROOF.json |
| **B2/B10 verifier routing for deterministic local actions (R6)** | **WRONG_PRODUCT** | **the Fabric spine re-derived the effect itself (MATCH) but the mission's verifier step for that step was dispatched to a worker role; a model was asked to judge what the runtime can recompute, so a bounded local worker substitute recorded FAIL and the mission ended BLOCKED with no false success** | **the verifier session re-runs the canonical predicate over the recorded receipt; the re-derived predicate satisfies the acceptance rule; the effective answerer is recorded next to the routed provider** | VERIFIER_ROUTING_REPAIR_PROOF.json (A/B discrimination), SELF_EXPANSION_PROOF.json 9/9, verifier-routing tests 6/6 |
| **B16 restart durability under binding drift (R7)** | **broken (crash)** | **a pass resumed after the frozen binding drifted could not re-freeze under the same content-addressed verdict identity; the ledger's EventConflict escaped MissionLoop** | **the loop refuses honestly: BLOCKED verdict with the ledger's reason, mission BLOCKED, resume left to a fresh attempt** | VERIFIER_ROUTING_REPAIR_PROOF.json (before: unhandled EventConflict; after: BLOCKED) |

## Open (classified, with the reason they are allowed to remain)

1. **Native Windows toast** — environment/library absent (`winotify` not installed); in-app toast,
   dedupe, suppression proven. REMAINING_GAPS.md §1.
2. **Real GitHub writes / third-party authenticated sites** — owner-gated external targets absent;
   disposable local Git proven, runtime refuses honestly. §2.
3. **Gap-bridge wake ownership of intent-resolution gaps** — partial: the missing `cap.*` requirement
   is attached to the plan step so the bridge can own it, but the wake→replan path itself was not
   exercised end-to-end (the self-expansion proof resumed through the Executive's bounded retry). §3.
4. **Lane-A convergence** — normalized intent contract published; the parallel lane must deliver
   intents in that shape. §4.

Nothing in the owned core is left unclassified, and every source-fixable defect found in this lane —
including the two found during this session's continuation — was repaired rather than parked.
