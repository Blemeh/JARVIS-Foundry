# Cross-Lane Conflict and Semantic Review Register

Status: SOURCE_CLOSED_LIVE_OPEN (semantic adaptation and canonical source gate
complete; runtime reconciliation and live promotion remain)

Textual merge-tree checks are clean. Semantic review remains mandatory because
the candidates were built from the older common base and current main contains
the accepted cinematic frontend and later runtime/source reconciliations.

| Area | Evidence of risk | Required resolution |
|---|---|---|
| Cinematic UI + V4 | V4 changes `command_center.py`, snapshot contracts, frontend mapper/types, and adds `OwnerManagement.tsx`; the current main already owns the newer cinematic AppShell and route architecture | V4 is merged at `6b119c3`. Focused source/UI gates pass and `/owner` is an additive route in the existing shell. Recheck the combined V5/V6 snapshot and live-source semantics before final closure. |
| V4 + V5 | V4 briefing and decision selection must understand connector effects without treating every attempt as notification-worthy | `briefing_items.py` now classifies terminal/ambiguous V5 attempts and lineage as `EFFECT`, preserves evidence IDs, and exposes bounded `effect_truth` in the snapshot/packet. Unknown/failed/frozen effects require owner attention; completed effects inform only. |
| V5 + V6 | V6 detector and feedback must not treat raw external connector content as authority | V6 is integrated at `9290b3f`; its records require evidence references and independent verdicts, while raw connector payloads remain non-authoritative. `evolution_truth` exposes only governed evidence/gate records and labels UI authority `NONE`. |
| V4/V5/V6 + STOP | Owner control is shared across directives, connector action envelopes, evolution eligibility, and UI controls | Verify each material path checks the same durable STOP/veto state and that UI never becomes authority. |
| Release/runtime | Current live release is schema 34 and lacks `git_commit`/`git_tree`; source expects schema 36 and filesystem release authority is explicit | Build from exact final main, rehearse migration on a copy, capture previous identity and backup, and do not call deployment PASS until supported promotion and restart proof exist. |
| Documentation | Historical candidate reports retain pre-stage checkpoints | Canonical `CURRENT_STATE.md` and `CAPABILITY_MATRIX.md` now identify pushed `08dad95`; historical candidate reports remain provenance, not current status. |

## Prohibited resolutions

- No global `ours` or `theirs` conflict resolution.
- No deletion of V4 behavior to silence UI or test conflicts.
- No reset, rebase, force push, blind squash, or candidate-history rewrite.
- No fabricated credentials, provider-side reconciliation, live readiness, or
  simulated data presented as live.
