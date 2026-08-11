# WP8 Proving Resource Scoring And Selection

Date: 2026-08-12 (continuation run)
Gate authority: `docs/architecture/04_V1_ACCEPTANCE_GATE.md` §3–§4 and
`docs/architecture/03_V1_AUTONOMY_KERNEL_SPEC.md` §3.1, §11.
Rule: the orchestrator scores accessible projects, preserves dirty user work,
chooses the highest-value reversible proving mission, registers it, and
records why it is suitable. It does not ask the owner to choose; an owner
only enters when an owner-only fact makes selection technically impossible.

## 1. Candidate Inventory (inspected 2026-08-12, read-only)

| Candidate | Location | Baseline | Verdict | Reason |
|---|---|---|---|---|
| LeadGen | `C:\Users\b\Documents\LeadGen` | main `1f5f9c0`, dirty (10+ modified backend files, in-flight AI work) | **NOT_SELECTED** | Its own repository contract (`docs/LEADGEN_NEXT_PHASES.md` Implementation Gate: "not approved for implementation until explicitly started by the user"; AGENTS.md: do-not-commit/reset) gates all defined next work behind an owner start. That is an owner-authored constraint; JARVIS never routes around an owner constraint. Parallel work in a worktree would also collide with the owner's in-flight session (same stack: Postgres/Redis/Celery/Vertex). |
| OperatorOS | `C:\Users\b\Documents\OperatorOS` | main `5c292a1`, a few untracked files | **NOT_SELECTED** | Phase 4 engineering packages P4-A..P4-G are complete (gate PASS 2026-08-10); the remaining Phase-4 gate is the BUSINESS evidence track (E2 FAIL immutable, E3–E7 BLOCKED on real-world inputs) awaiting the sponsor's GO/PIVOT/STOP decision. Its AGENTS.md phase control ("one application implementation phase active at a time", anti-drift rule 4/16: implement only active-phase work, never invent) leaves no in-scope engineering work a proving mission could lawfully claim. Its own autonomous program (master-architect + Hermes builders) also forbids a second builder. |
| QueueTwin | `C:\Users\b\Documents\Queuetwin` | **no commits** (unborn main; every file untracked) | **NOT_SELECTED** | No git baseline → no recorded rollback path; the gate requires a tested rollback path and worktree isolation from a clean baseline. |
| Hoowen | — | no local source checkout found (only logos/slides in Downloads) | **NOT_SELECTED** | No accessible repository to register. |
| `.h` (Hermes dev checkout) | repo `.h` | pinned `244d2966…` | **NOT_SELECTED** | Explicitly excluded by the owner handoff and HERMES.md: must not become the proving resource merely because it is convenient. |
| JARVIS-Foundry (self) | this repo | main `c2770ef` (frozen gate baseline) | **CONDITIONAL** | Circularity risk if the mission edits the kernel under gate. Rejected as the mission TARGET; the gate's frozen main branch stays untouched. |

## 2. Selection

**Selected mission shape: a NEW bounded standalone repository serving the
owner's professional WFM domain (intraday steering), with a committed
baseline created by the mission itself before any implementation.**

Rationale (recorded honestly):

1. **No accessible external repo is lawfully selectable** (table above):
   LeadGen is owner-gated, OperatorOS has no in-scope work, QueueTwin has
   no baseline, Hoowen has no checkout. These are repository-truth facts,
   not convenience.
2. The gate requires a mission that is *real, user-valued, isolated,
   code-bearing, reversible, and completable with existing services*. A
   new utility repo in the owner's own WFM discipline (his day job:
   Intraday/Steering Coordinator; existing related artifacts:
   WorkforcePlanner.xlsm, Agent Manipulation Detector) is real value,
   zero contamination of business repos, full reversibility (directory +
   branch deletion), and satisfies every §4 criterion.
3. The mission creates its own baseline commit FIRST (clean rollback
   point), then implements bounded useful functionality with ≥3
   independent acceptance criteria, a deliberate reversible failure
   injection (G6), and a tested rollback path (G8).
4. The chosen utility MUST first be checked against the owner's existing
   tools (WorkforcePlanner.xlsm / Agent Manipulation Detector, located on
   this machine) so the mission extends rather than duplicates — the
   inspection itself is part of the mission's required uncertainty work.

Mission contract, worktree paths, acceptance criteria, and rollback plan:
`WP8_MISSION_CONTRACT.md` (written before clock start).

## 3. Why Not Ask The Owner

No owner-only fact blocks selection: every disqualification is readable
repo state or the owner's own standing written constraint. Asking would
convert a technical selection into a permission request, which the mandate
forbids. The owner's veto remains available at any time.

## 4. Registration

Before the clock starts, the selected resource is registered in the live
Foundry ledger via a `project.registered` event (resource record: absolute
path, baseline commit, worktree path, capabilities, readiness evidence),
and the mission via `mission.created` — the WP8 preconditions document
tracks these.

## 5. Anti-Drift Note

The mission must not mutate any file outside its registered worktree root
(G4), must not touch the frozen main branches of JARVIS-Foundry or any
other repo, and must not perform push/public/paid actions (V1 staging).
