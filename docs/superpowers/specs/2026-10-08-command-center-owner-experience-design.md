# Command Center Owner Experience — Design Spec

**Date:** 2026-10-08
**Scope:** Owner-experience rescue of the live Command Center. No autonomy-kernel redesign.
**Authority order:** Authenticated owner mission (this rescue) > `docs/CURRENT_STATE.md` >
frozen roadmap + `docs/roadmap/JARVIS_1_0_EXECUTION_STATE.yaml` (V11_INITIATIVE / A3_A4
BLOCKED_BY_PROVIDER) > task contract > `JARVIS-Foundry/AGENTS.md` > judgment.
**Live truth observed 2026-10-08 (hermes foundry.db, read-only):**
- Standing Hoowen directive `owner-delegation-643042f8f84795e7de8b3d79a4b7142c`
  (DELEGATE, active=1): "Turn Hoowen into a commercially credible workforce-management product…"
- Hoowen missions: `mission-08a548a685ae` COMPLETE (2026-10-06T14:00),
  `mission-62250d335ab6` COMPLETE (2026-10-06T15:34, follow-up 2),
  `mission-fe81cb0d1e37` BLOCKED (2026-10-06T15:50, follow-up 3; builder FAILED,
  verifier PASS 15:46 — repair attempt failed after verification).
- Historical CI mission `mission-4646c7023b05` BLOCKED (2026-10-04, CI_FAILURE
  2026-08-19, `created_from=portfolio:pw-dupkey-…`). No shared worker, evidence,
  or decision-queue linkage with Hoowen work. No ACTIVE missions in the recent window.
- Snapshot orders missions by `updated_at DESC`; directives active=1 by `updated_at DESC`.

## 1. Product principle

The Command Center is an owner summary first, a forensic tool second.
Within ~5 seconds the owner answers: what is JARVIS doing, why, what changed,
is anything blocked, do I need to act, what happens next.
Technical evidence stays one click away, never in the primary scan path.

## 2. Owner prioritization (binding order)

Every owner surface (JARVIS home, Work) ranks:

1. Active standing owner goals (`directives.active=1`, any command_type incl. DELEGATE).
2. Executing missions (`ACTIVE` / `IN_PROGRESS` / `INTERVENTION`, `updated_at DESC`).
3. Current workers (`agent_runs.status=RUNNING` linked to the surfaced mission).
4. Recent completed outcomes (`COMPLETE`, `updated_at DESC`, max 3).
5. Genuine unresolved owner blockers (open `decision_queue` records + BLOCKED missions
   linked by `mission_id` to an active goal).
6. Historical blocked/failed missions (BLOCKED/HALTED/CANCELLED not linked to an active
   goal, or `created_from` CI_FAILURE/portfolio dup-key, or `updated_at` older than the
   active goal's latest child).

A historical mission never dominates Home/Work by recency or by being the first
BLOCKED row. History lives under filters, search, and an explicit history section.

`mission-4646c7023b05` is historical. Its page states truthfully:
"Historical mission · Blocked on CI verification · Not affecting current Hoowen work"
iff linkage check holds (no shared `mission_id` refs in decision queue, workers,
evidence, or work hierarchy between the two subgraphs — verified at render from the
loaded snapshot, not from memory).

## 3. JARVIS home (`/`)

Primary card (no raw IDs in primary text; IDs in `.tech-id` secondary only):

- NOW — current focus title: executing mission title, else autonomy
  `current_work.candidate.title`, else active Hoowen goal short label.
- WHY — serving goal exact text (short label) + executive `latest_decision.reason`
  or `wait_reason` fallback. Never a bare event ID.
- PROGRESS — child objective / mission / worker state line
  (e.g. "Follow-up 3 · Blocked after builder failure · verifier passed 15:46").
- LATEST RESULT — most recent meaningful completed outcome (Hoowen follow-up 2
  COMPLETE with its outcome summary, linked).
- NEXT — next intended action from exact lineage (mission `next_action` of the
  current child, else executive `next_wake` / `wait_reason`).
- NEEDS YOU — only when a genuine owner action is required (open decision-queue
  record or BLOCKED mission linked to the active goal that names an owner boundary).
  Otherwise the literal sentence: "Nothing needed from you."

Secondary (below the fold): lifecycle strip, today strip, recently completed (3),
discovered, next sequence. All existing reliability behavior preserved
(`useRouteQuery` + `keepPreviousWhileLoading`, `RouteQueryGate`, stale marking).

## 4. Hoowen product-goal card

Rendered on Home and atop Work while the DELEGATE stays active:

- "Hoowen — Active strategic goal"
- Current work: latest child mission title for the directive (by `updated_at DESC`
  within `created_from` containing the directive id).
- Status: exact mission state in plain words (Executing / Blocked / Complete).
  Current truth is Blocked — the card says Blocked, not Executing.
- Latest completed: plain-English outcome of the newest COMPLETE child.
- Next: next intended action from the current child's `next_action` or executive wait.
- Needs you: "Nothing" unless a linked open decision record exists.
- Drill-down link to the goal-filtered Work view and to the current mission.

No portfolio IDs, ledger seqs, hashes, wake IDs, or verifier event names in primary.
`created_from` prefix matching is the allowed causal join (durable lineage, not a guess).
Cross-mission leakage is forbidden: never borrow a title, verdict, or next action from
a mission outside the directive's subgraph.

## 5. Mission page (`/missions/:missionId`)

Primary order:

1. Title (plain language = mission objective, sentence case).
2. Status pill (Executing / Complete / Blocked / Failed + historical badge when applicable).
3. Serves (parent owner goal short label, linked to goal-filtered Work).
4. Summary (2–4 sentences, verbatim leading sentences of the objective + state line).
5. Current / Result (what is happening or what happened, incl. builder/verifier states).
6. Next (exact `next_action` or "No next action recorded").
7. Owner action (only when genuinely required per §3; otherwise "Nothing needed from you").
8. Compact stage tracker: Goal → Plan → Build → Verify → Outcome (from the
   mission lifecycle records `owner_goal/plan/worker+action_artifact/evidence+verifier/
   owner_briefing+final outcome`).

Progressive disclosure below: Technical details (IDs, event counts, hashes, run IDs,
bundle IDs, repair lineage), Evidence, Timeline. The current horizontal
`md-stages` pipeline with full prose columns is removed from primary; the compact
tracker replaces it.

Buttons reflect state:
- HALT EXECUTION appears only when the mission is currently executing
  (`ACTIVE`/`IN_PROGRESS`/`INTERVENTION`) AND mutations are enabled. Historical
  terminal BLOCKED missions never show it.
- RE-EVALUATE GATE appears only when a fresh verifier evaluation is actionable.
  The live client reports gate re-evaluation UNAVAILABLE, so it is hidden in LIVE
  and retained in SIMULATED.

## 6. Health / trust clarity

Three separate indicators, never conflated:

- System: Healthy / Degraded / Down (from `snapshot.health.supervisor_status` + degraded flag).
- Autonomy: Running / Waiting / Stopped (from autonomy executive state + STOP sentinel).
- Trust: Proven / Suspended / Unknown + short human reason (e.g. "Suspended —
  missing live-observation evidence. Permitted work can still run within constraints.").

Trust SUSPENDED must not read as "JARVIS is dead" when permitted work exists.
A tooltip carries the one-line explanation.

## 7. Owner command experience

A first-class command dock (not a bottom terminal strip):

- Primary modes as visible tabs: GOAL (primary — "tell JARVIS what outcome is wanted"),
  ASK (advice / briefing, maps to Tell-me), VETO (hold an action).
- STOP stays visually separate and unmistakable (existing EmergencyStop control).
- The dock sits where the owner looks (Home top + persistent shell surface), with
  plain placeholders and durable accepted/rejected feedback incl. directive/event IDs
  as secondary confirmation only.

## 8. Visual language

Apple / Linear / Raycast-quality dark product UI with JARVIS mission-control character.
Sans-serif primary, sentence case, clear hierarchy, larger body, generous spacing,
layered surfaces, subtle borders, strong status hierarchy, plain language.
Monospace only for IDs, SHAs, technical evidence. No cyberpunk clutter, no global glow.
Build on the existing `product.css` calm layer (commit c64673f0), extend it — do not fork it.

## 9. Information architecture

Keep the existing owner navigation (already correct):
JARVIS (`/`) · Work (`/work`) · World (`/world`) · Capabilities (`/capabilities`) ·
You (`/you`) · Activity (`/activity`) · Evidence (`/evidence`) · Settings (`/settings`).
JARVIS = executive summary. Work = goals + current missions + recent outcomes.
Activity = chronological stream. Evidence = forensic proof. No forensic data on Home.

## 10. Work page (`/work`)

Grouped by OWNER GOAL first (goal → objectives → campaigns → missions), not a flat
mission list. The Hoowen ACTIVE goal renders first while its directive is active,
with: current child, current mission, worker state, last result, next. Child history
(COMPLETE follow-ups) sits beneath it. Unlinked missions (no goal join) stay in an
explicit "Unlinked mission records" section — never silently merged into a goal.
Filters (active/waiting/attention/completed/all) and hierarchy-availability states
are preserved.

## 11. Live truth (non-negotiable)

All owner-facing state derives from the loaded canonical snapshot + autonomy
projection + work hierarchy. Exact causal lineage only (`mission_id`,
`created_from` directive id, `mission_to_objective` / `mission_to_campaign`,
decision-queue `mission_id` refs, worker/evidence/verdict `mission_id` joins).
No nearest-event, newest-unrelated-directive, cross-mission, or truncated-window
guessing. `UNKNOWN` / `UNAVAILABLE` / `NOT REPORTED` remain valid outputs.

## 12. Loading / reconnect (no regression)

Preserve: bounded requests (existing limits), `keepPreviousWhileLoading`,
last-known state retained when disconnected, stale marking, automatic reconnect,
refresh preserves state, Supervisor restart recovery. Every route keeps its
`RouteQueryGate`/`DetailRouteQueryGate` + `routeResilience` behavior.

## 13. Responsive

Usable at 1920×1080 and 1366×768 laptop. No horizontal information explosion, no
tiny unreadable pipeline, no critical owner action below an unusable fold.
The compact tracker and card layout must overflow-free at both widths
(existing `capture-remaster.mjs` + `product-verify.mjs` discipline continues).

## 14. Non-goals (this session)

Autonomy algorithms, provider routing, self-evolution, broad security work,
unrelated backend refactors, voice, localization, new integrations.
Backend projection/API changes only where required for truthful, usable
owner-facing state — and then as additive, read-only, bounded projections.
