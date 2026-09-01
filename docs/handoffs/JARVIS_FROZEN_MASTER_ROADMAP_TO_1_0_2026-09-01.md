# JARVIS FROZEN MASTER ROADMAP — V10 CONVERGENCE TO JARVIS 1.0

**Date:** 2026-09-01
**Status:** FROZEN. Do not redesign without new evidence. See "Not to be revisited" section.
**Authored by:** Fable 5 acting as principal architect / product strategist.
**Supersedes:** the V10→V16 six-phase rough roadmap structure. The old
V11–V16 phases are collapsed into V11–V14 as recorded in the decision log.
**Grounding:** module names reference the real V10 convergence tree
(`src/jarvis_foundry/`): `executive/` (continuous, scoring, decisions, goals,
missions, calibration), `world_model/` (observations, signals, gaps, patterns,
research_handoff, calibration), `fabric/` (catalog, expansion, spine),
`mission/` (loop, verification, verifier_binding, proposal), `connectors/`
(browser, web_research, github_research, github_write, gmail_api, git,
filesystem), `capabilities/registry.py`, `discovery_candidates.py`,
`discovery_monitors.py`, `research_queue.py`, `decision_queue.py`,
Tier-C hybrid memory, `operations/reuse.py`, `workers/` (hermes, openhands,
lab, mission_bridge).

---

# FINAL ARCHITECTURAL VERDICT

**The core authority spine is frozen. The initiative is not built.** JARVIS's
spine — World Intelligence → Continuous Executive → Capability Fabric →
MissionLoop/effects/verifier → World update — is correct, proven, and must not
be restructured between now and 1.0. Freezing the spine does not forbid
growth: additive records, projections, and subordinate modules remain allowed
whenever they serve and stay subordinate to that spine; what is forbidden is
any competing authority. What is missing is not another
subsystem; it is that today **every mission is still conceived by a human**.
The Executive can select, execute, verify, retry, wait, and recover — but it
selects from work it was handed. The single most valuable remaining build is
closing the loop from *observation* to *self-generated, self-ranked,
mostly-rejected candidates* to *autonomous objective creation*, using organs
that already exist (`discovery_monitors`, `discovery_candidates`,
`executive/scoring`, `decision_queue`, `world_model/gaps`, `fabric/expansion`).

The six remaining phases (V11–V16) collapse into **three build phases plus one
convergence**:

```text
V10 close (single release cycle, incl. bounded sensory completion)
  → V11  INITIATIVE        (autonomous discover→reject→choose→build→learn loop, live)
  → V12  ENDURANCE + SELF-EXPANSION  (multi-day campaigns survive everything;
                                      gaps get filled autonomously)
  → V13  PERSONAL OPERATOR (owner-context intelligence drives prioritization;
                            operator breadth pulled by real missions)
  → V14 = JARVIS 1.0       (convergence + 72-hour flagship acceptance + promotion)
```

## Rejected alternatives (do not reopen without new evidence)

1. **Keep V11–V16 as six sequential phases.** Rejected: V15 "human interface"
   duplicates the existing Command Center and voice is owner-deferred; V13
   "personal executive intelligence" cannot be a standalone phase because
   ranking without owner context produces junk candidates from day one — it
   must grow inside the loop that consumes it; V14 "full digital operator" as a
   breadth phase violates the reuse doctrine — operator capabilities should be
   pulled by concrete missions, not pushed by checklist.
2. **Self-expansion before autonomous discovery.** Rejected: self-expansion has
   no natural trigger until JARVIS is choosing its own work and hitting real
   gaps. Gap detection (`world_model/gaps.py`) firing during autonomous
   missions is the honest trigger; building the expansion pipeline first means
   testing it on synthetic gaps.
3. **Adopt an external agent framework (LangGraph/AutoGen/CrewAI) for the
   discovery loop.** Rejected: it would be a second executive. External agents
   remain bounded workers behind the existing worker contract
   (`workers/contracts.py`), exactly as OpenHands is today.
4. **A dedicated "memory platform" phase (embeddings/vector DB).** Rejected:
   Tier-C lexical memory with curation gates is proven; the reserved semantic
   hook stays reserved until a real recall failure justifies it.
5. **Two V10 release cycles (promote, then sweep, then promote again).**
   Rejected: the sensory sweep extends `connectors/browser.py` on the same
   schema with no migration or runtime-surface change; it lands inside the
   current V10 candidate line before ONE final exact-SHA convergence and ONE
   promotion. If the sweep uncovers instability, it slips into a V11 lane
   rather than forcing a second V10 promotion.

---

# CURRENT MATURITY

Scores are against "what 1.0 needs," not against generic agent systems.

| Dimension | Score | One-line justification |
|---|---|---|
| Architecture | 9/10 | Spine complete and proven; single authority chain; schema/ledger/effect discipline. Only missing: initiative wiring. |
| Autonomy | 6/10 | Selects/executes/verifies/waits/retries within a given portfolio; never originates its own work; honest WAIT/BLOCKED proven. |
| Capability breadth | 6/10 | Real web read plus bounded interactive/authenticated browser substrate, full document suite, Git/GitHub read + local write, disposable releases. Live provider effects and Windows control remain unproven/deferred. |
| Reliability | 8/10 | STOP/RESUME, restart recovery, effect reconciliation, rollback all proven live — but at mission scale, not campaign (multi-day) scale. |
| Real-world operation | 4/10 | Reads the world well; changes it barely (no authenticated sessions, no mail send, no public GitHub write exercised). |
| Self-improvement | 3/10 | Seams exist (`gaps.py`, `fabric/expansion.py`, `operations/reuse.py`, V6 shadow/canary machinery) but no end-to-end gap→integrate→register→resume path has ever run. |
| Personal intelligence | 2/10 | Tier-C memory infrastructure is strong, but no owner-goal/commitment/preference model feeds `executive/scoring`. |
| Human interface | 6/10 | Command Center projects ledger truth and is sufficient for 1.0; voice/Albanian intentionally post-1.0. |

## Capability matrix

| Capability | Status | Note |
|---|---|---|
| Web research (read) | CURRENT | Playwright + Trafilatura, provenance, DATA_ONLY |
| Interactive browser | IMPLEMENTED / DISPOSABLE_PROVED → LIVE_AUTH_PROVED BLOCKED | Click/type/session/auth on existing substrate; safe live owner session unavailable |
| Documents/data | CURRENT | PDF/DOCX/XLSX/CSV/PPTX/images, readback checks |
| Coding | CURRENT | Local repo change/test/commit, disposable releases |
| Git/GitHub | PARTIAL | Read + write adapter exist; public write unexercised |
| Windows control | DEFERRED → V13, mission-pulled | Phase-4 OpenAdapt bundle exists as recoverable prior art |
| Providers (mail/calendar) | PARTIAL | Gmail adapter/seam exists; no real credentials; no send |
| Long-horizon autonomy | PARTIAL | Mission-scale proven; campaign-scale (days, reboot, outage) unproven |
| Self-expansion | MISSING (seams exist) | `gaps.py`, `expansion.py`, `reuse.py`, V6 trial machinery unwired |
| Personal intelligence | MISSING | Tier-C substrate ready; no owner-context model |
| Cross-app operation | MISSING → V13 | Pulled by missions, never checklist-built |
| Voice/language (Albanian/English) | POST-1.0 (product decision) | See resolution 3 below; do not revisit |

---

# CRITICAL GAPS

## CRITICAL — 1.0 is impossible without these

1. **Observation → candidate wiring.** `discovery_monitors.py` /
   `discovery_candidates.py` are not fed by scheduled real-world observation
   (GitHub repos, watched web sources, project/filesystem drift, world-model
   signals).
2. **Rejection-first ranking governor.** A scoring pass over candidates
   (extend `executive/scoring.py` + `v3_scoring.py`) that rejects most ideas
   with durable, reasoned rejection records — plus dedup against prior
   candidates and rejections.
3. **Autonomous initiation authority.** A bounded policy under which the
   Continuous Executive may convert a top candidate into a real objective
   without an owner prompt (budget, WIP cap, reversibility class).
4. **Outcome → prioritization learning.** Mission outcomes must adjust scoring
   priors (`world_model/calibration.py` ↔ `executive/calibration.py` exist on
   both sides; unwired).
5. **Interactive + authenticated browser live proof.** The bounded
   click/type/login/session-persistence implementation is complete in the
   V10_EXIT candidate; a safe owner-authorized live session is still required
   for the promotion gate.
6. **Campaign-scale durability proof.** Restart/reboot/outage/wait survival
   exists at mission scale; must be proven across a multi-day, multi-mission
   objective.

## IMPORTANT — needed before 1.0, not before V11

- End-to-end self-expansion (gap → search → trial → register → resume),
  including rollback/disable.
- Owner-context model (goals, commitments, deadlines, preferences,
  rejected-idea history) feeding scoring.
- Real external effects: Gmail send, calendar write, public GitHub write —
  each behind reversibility policy.
- Gmail live read (personal signal stream; optional before V13, hard entry
  requirement for V13).
- MCP client connector (one adapter unlocks a large tool ecosystem under the
  existing worker/effect contract).
- Command Center "autonomy briefing" projection (the "found 12, rejected 8,
  built 1" report).

## LATER — pull-based only

- Windows UI Automation (minimal, per-mission; recover Phase-4/5 prior art
  first).
- OCR/media, additional providers, semantic recall.
- Voice/Albanian (post-1.0).

## UNNECESSARY — never on the 1.0 path

- Enterprise security/compliance frameworks, multi-tenant anything,
  distributed multi-machine operation, a second orchestrator/executive,
  generic desktop RPA platform, custom browser stack.

---

# FINALIZATION RESOLUTIONS (2026-09-01, binding)

## Resolution 1 — V10 sequencing: ONE release cycle

```text
current V10 convergence (lanes A–D merge, suite green)
→ V10 sensory completion inside the same candidate line
   (interactive browser actions + authenticated session persistence;
    Gmail read only if credentials are already in hand)
→ ONE final V10 convergence / exact-SHA gate
→ ONE rehearsal → promotion → live validation
→ V11 begins on the promoted base
```

No second V10 promotion. If the sweep uncovers instability it slips to a V11
lane.

## Resolution 2 — Gmail must never become an artificial blocker

- **Truly required for V11:** interactive/authenticated browser, GitHub
  research connector, web watchlist, local project/repo state, filesystem
  drift, existing world-model signals. These alone feed A2/A3/A4.
- **Gmail read:** OPTIONAL for V10/V11/V12. If credentials are unavailable at
  sweep time, record `GMAIL: BLOCKED/DEFERRED (credentials)` and proceed
  without pausing anything.
- **Hard deadline:** Gmail read is a HARD ENTRY REQUIREMENT for V13 — the
  owner-context model and A7 need at least one live personal signal stream.
  Gmail send stays a V13 exit-path capability under reversibility policy.

## Resolution 3 — Voice / Albanian: POST-1.0 (product decision B)

JARVIS 1.0 ships with the Command Center (text) as its only human interface.
Voice and Albanian/English localization are the first post-1.0 program — not a
1.0 criterion, not a late-1.0 stretch goal. The A8 flagship is judged entirely
on the ledger and briefing. Core autonomy never waits for voice.

---

# AUTONOMOUS LOOP DESIGN

No new executive. Every step is owned by an existing component; new code is
wiring, policy, and records.

| Step | Owner (existing module) | Durable record |
|---|---|---|
| Observe | World Intelligence: `world_model/observations.py` + `signals.py`, fed by scheduled Fabric capability runs (web watch, GitHub watch, project scan, Gmail read when available) | Observation events + provenance hashes (existing ledger pattern) |
| Generate candidates | `discovery_monitors.py` → `discovery_candidates.py`, plus `world_model/gaps.py` for capability-gap candidates | Candidate record: source observations, claim, fingerprint |
| Deduplicate | Candidate ingest, reusing Workflow-Discovery versioned fingerprints + Tier-C dup gates | Fingerprint index; `DUPLICATE_OF` links |
| Research | `research_queue.py`/`research_contract.py` → `world_model/research_handoff.py` → web-research capability via MissionLoop (bounded, read-only, budgeted) | Research artifacts, DATA_ONLY, hashed |
| Score | `executive/scoring.py` extended; inputs below | Score record with per-factor breakdown |
| Reject | Same scorer, rejection-first | **Rejection record with reason code** — mandatory, feeds dedup and learning |
| Choose | Continuous Executive portfolio selection (`executive/continuous.py` + `decisions.py` + `decision_queue.py`) — the *same* selector that handles owner-given work; autonomous candidates enter the same queue at policy-bounded priority | Decision record (existing) |
| Objective / plan | `executive/goals.py` + `missions.py` + `operations/planning.py` | Objective + plan records; base SHA pinned |
| Execute | MissionLoop (`mission/loop.py`) → Fabric → connectors/workers in isolated worktrees | ActionEnvelopes, effect tickets (existing) |
| Verify | Independent verifier (`mission/verification.py`, `verifier_binding.py`) — never the building worker | Verifier records (existing) |
| Repair | Existing retry/escalation/replan in Continuous Executive | Retry/escalation events (existing) |
| Learn | World Model writeback + `world_model/patterns.py`/`calibration.py` → scoring priors; Tier-C experiential memory for procedures | Outcome record linked to candidate + score |
| Continue | Continuous Executive wait/wake — unchanged | Wake conditions (existing) |

**Ranking inputs** (each a named factor in the score record): owner-goal
relevance (from owner-context memory), project relevance (project identity
model), expected benefit, urgency/deadline proximity, confidence (source trust
+ world-model calibration), effort estimate, capability availability (Fabric
catalog lookup — a missing capability *discounts* score and may spawn a gap
candidate instead), dependency readiness, duplication penalty, opportunity
cost (must beat the current portfolio's marginal item *and* a "do nothing,
keep watching" baseline).

**When a candidate is rejected:** fails relevance threshold; duplicates a
live/completed/rejected candidate (unless materially new evidence); effort
exceeds autonomous budget class; requires a capability whose acquisition costs
more than the candidate's value; confidence below floor after one research
pass; or the portfolio is at WIP cap and it doesn't beat the weakest active
item. **Default outcome is rejection.** Rejections are durable and reasoned so
JARVIS can report them and avoid re-litigating them.

**When JARVIS acts autonomously vs. asks the owner:** autonomous when effects
are local, reversible, in-worktree, or within already-granted provider scopes
(candidate commits, research, drafts, disposable releases). **Owner input
required** for: irreversible or externally visible effects (public GitHub
push, sending email to third parties, spending money, deleting non-disposable
data), new credentials/scopes, entering a goal domain the owner never
expressed, and live promotion (release authority is unchanged). This is a
*reversibility policy on effects*, enforced where effects already flow — not a
new approval plane.

**How endless self-generated work is prevented:** (1) hard WIP cap on
autonomous objectives (start: 1, grow to 2–3); (2) daily/weekly autonomous
compute+action budget metered in the ledger; (3) candidate TTL and decay —
stale candidates expire rather than accumulate; (4) research is budgeted per
candidate (one bounded pass before scoring, more only if the score is
near-threshold); (5) opportunity-cost baseline means "no action" wins by
default; (6) discovery itself is a scheduled bounded cycle, not a hot loop;
(7) STOP freezes everything, as today.

**How it decides what to do next:** the existing Continuous Executive decision
cycle, unchanged — one queue containing owner directives (always dominant),
active objective continuations, wake-condition firings, and autonomous
candidates in ascending priority. Owner work always preempts self-generated
work.

---

# PHASE-BY-PHASE PLAN

Capability classes used throughout:
**HARD ENTRY** (phase cannot start without it) ·
**EXIT REQUIREMENT** (phase cannot close without it) ·
**MISSION-PULLED / OPTIONAL** (built only when a real mission demands it) ·
**POST-1.0** (out of the program).

## V10 — CLOSE (convergence + sensory completion, one promotion)

- **Objective:** close V10 convergence, land the sensory completion inside the
  same candidate line, run ONE final exact-SHA convergence, then ONE
  rehearsal → promotion → live validation.
- **Owner-visible gain:** JARVIS can log into and operate real websites; if
  credentials exist, it can also see the owner's real inbox (read-only).
- **Extend:** `connectors/browser.py`, `connectors/web_research.py`,
  `connectors/gmail_api.py`, effect receipts in `connectors/evidence.py`.
  **New modules: none.**
- **External:** Playwright (already in), Google API client (already seamed).
- **Lanes (2):** L1 interactive browser + authenticated session persistence;
  L2 Gmail live read activation (only if credentials already in hand).

| Capability | Class |
|---|---|
| Lanes A–D semantic convergence, suite, exact-SHA | EXIT REQUIREMENT |
| Interactive browser actions + auth session persistence | EXIT REQUIREMENT — IMPLEMENTED / DISPOSABLE_PROVED in V10_EXIT candidate |
| Gmail live read | MISSION-PULLED / OPTIONAL (`BLOCKED/DEFERRED` if no credentials) |
| Mail send, calendar, Windows UIA, OCR, MCP | explicitly out (later phases) |

- **Flagship proof:** JARVIS logs into a real authenticated site, performs a
  bounded interaction, extracts data with provenance; (if Gmail active) reads
  real inbox and produces a hashed observation artifact.
- **Failure/recovery proof:** session expiry mid-interaction → detected,
  re-auth or honest BLOCKED; STOP mid-browser-action → no orphaned Chromium,
  effect reconciled.
- **Exit gate:** converged head promoted and live-validated; live
  authenticated-site interaction proof with receipts; STOP-mid-browser-action
  reconciliation proof; suite green; no ambiguous partial browser effects
  possible (every interaction has a receipt).
- **NOT in phase:** mail send, calendar, Windows UIA, MCP, OCR, any discovery
  logic.

### V10_EXIT status — 2026-09-01

The single V10_EXIT candidate is `V10_EXIT_READY_FOR_PROMOTION` for the next
authorized rehearsal → promotion → live-validation sequence. Its product
predecessor is `71d71da3f0998538a579a807a152c4bd9d3282d0`; the exact V10
source/test tip is `5e466b103b73065b292fd41883fb9fd3f6fc751d` (authority/truth
fix `03e6f64bd045a8ca560896d5d5dbf8c552eb9767`, built from implementation tip
`4a645c0b939cf1f4d09e37e1cdfd04dba564e179`); the
roadmap-authority commit is
`e9b2155bd669887c4778db0b16a4ef7861d2c9ff`; and the candidate worktree is
`C:\temp\JARVIS-V10-Exit-20260901` on branch
`candidate/v10-exit-20260901`.

Bounded Playwright interaction, authenticated storage-state persistence and
restore, expiry detection, STOP behavior, effect identity/replay, readback,
and disposable authenticated proof are implemented and proven. No safe
owner-authorized live authenticated site was available, so
`LIVE_AUTH_PROVED` remains `BLOCKED/NOT_TESTED`; Gmail remains
`BLOCKED/DEFERRED` without credentials. The execution state remains
`current_phase=V10_CLOSE`, `current_gate=V10_EXIT`, `gate_status=NOT_PASSED`;
V11 is not started.

The 2026-09-02 independent authority/truth audit is closed on that tip:
Fabric/Executive uses `fabric/catalog.py` for Fabric readiness, mutating Fabric
actions use the existing canonical effect journal and independent readback,
STOP surfaces converge through `control/broker.py`, and raw
`verification.passed` cannot become final mission, WorldModel, learning, or
briefing truth before the exact `verification.accepted` receipt. The legacy
capability registry remains only a gated compatibility seam for non-Fabric
callers. No phase or roadmap scope changed.

## V11 — INITIATIVE (the decisive phase)

- **Objective:** the full autonomous discover → dedupe → research → score →
  reject → choose → build → verify → learn loop, live, under the existing
  Executive.
- **Owner-visible gain:** the mature report becomes real at small scale:
  *"Watched N sources, generated 12 candidates, rejected 8 with reasons,
  researched 4, chose 1, built it in a worktree, verified it, learned from
  it."* Command Center shows it without being asked.
- **Extend:** `discovery_monitors.py`, `discovery_candidates.py`,
  `research_queue.py`, `world_model/signals.py`/`research_handoff.py`,
  `executive/scoring.py`/`continuous.py`/`decision_queue.py`,
  `mission/proposal.py`, Command Center projection.
  **New modules:** at most a `discovery/sources` package for watch-source
  definitions and the rejection-record schema migration (next additive schema
  revision; the actual migration number is determined from the exact promoted
  predecessor at phase start).
- **External integrations:** none new — GitHub research connector, web
  research, and the existing worker fleet are the sensors and hands.
- **Lanes (3):**
  - **L1 Observation & candidates:** scheduled watch cycles over (a) owner's
    GitHub repos/issues, (b) a curated web watchlist, (c) local
    project/filesystem drift, (d) existing world-model signals (plus Gmail
    signals if available); candidate generation + fingerprint dedup.
  - **L2 Judgment:** scoring factors, rejection-first governor,
    budgets/WIP caps/TTL, minimal owner-context block in Tier C (owner goals
    + active projects + standing rejections — hand-seeded, machine-read)
    feeding relevance factors.
  - **L3 Execution bridge & truth:** candidate→objective→worktree mission
    path hardening; outcome→calibration writeback; Command Center autonomy
    briefing.
- **Convergence dependency:** L1+L2 merge before L3's end-to-end run.

| Capability | Class |
|---|---|
| Promoted V10 base + interactive browser | HARD ENTRY |
| Observation cycles (GitHub, web watchlist, project/FS drift, world-model signals) → candidates + fingerprint dedup | EXIT REQUIREMENT |
| Rejection-first scoring + governor (WIP=1, budgets, TTL, reversibility classes), next additive schema revision | EXIT REQUIREMENT |
| Candidate→objective initiation via existing Executive; outcome→calibration writeback; autonomy briefing | EXIT REQUIREMENT |
| Hand-seeded owner-context block (goals, projects, standing rejections) | EXIT REQUIREMENT (seed only; full model is V13) |
| Gmail as an observation source | MISSION-PULLED / OPTIONAL |
| Self-expansion execution, external irreversible effects | explicitly out (gaps only generate candidates) |

- **Flagship proof (Ladder A3):** from a cold scheduled cycle with zero
  prompts, JARVIS produces ≥10 candidates from real observation, rejects the
  majority with recorded reasons, researches finalists, autonomously selects
  one *small real improvement to one of the owner's actual repos*, builds it
  in an isolated worktree, tests, has it independently verified, records the
  outcome, and returns to WAIT.
- **Failure/recovery proof (Ladder A4):** kill the process mid-loop at three
  points (post-candidate, post-selection, mid-build) → restart recovers exact
  state, no duplicate candidates, no duplicate missions; STOP honored at every
  stage; forced bad selection (poisoned candidate) → build fails, repair
  attempts, honest close with negative outcome recorded and prior adjusted.
- **Exit gate:** A2 first (unattended cycle: real candidates, reasoned
  rejections, zero execution), then A3 + A4 over ≥48h unattended: three
  consecutive cycles producing honest candidate/rejection/outcome ledgers,
  ≥1 verified autonomous build, zero duplicate effects, zero unbounded loops.
  Then V11 promotion. Elapsed-time proving must not idle engineering: once the
  functional candidate is frozen for the 48h window, V12 planning and isolated
  development may proceed in parallel; V12 live promotion remains blocked
  until this gate passes.
- **NOT in phase:** self-expansion execution, external irreversible effects,
  multi-day single campaigns, provider writes, Windows.

## V12 — ENDURANCE + SELF-EXPANSION

- **Objective:** autonomous work survives everything, and missing capabilities
  stop being blockers.
- **Owner-visible gain:** JARVIS runs a campaign for days across reboots and
  outages; when it lacks a tool, it acquires one and resumes.

### Long-horizon design (extends existing machinery, no new scheduler)

- **Campaign = objective with child missions** in the existing goal/mission
  model (`executive/goals.py`, `missions.py`) — checkpointed progress records
  per objective (next additive schema revision, numbered from the exact
  promoted predecessor at phase start), so recovery replays state from the ledger, not
  memory. This generalizes the proven restart recovery from mission scale to
  objective scale.
- **Reboot survival:** the existing Supervisor scheduled-task adoption +
  ledger recovery already covers process death; add an objective-resume pass
  on startup (recover objectives in `EXECUTING/WAITING`, re-derive wake
  conditions, reconcile in-flight effect tickets before any new action).
- **Waits/deadlines:** existing wake-condition machinery, extended with
  absolute deadlines that trigger *replan-or-escalate* decisions rather than
  silent expiry.
- **Provider outage:** existing V7C resilience coordinator classifies outage →
  affected objectives transition to honest WAIT with provider-recovery wake
  conditions; no retry storms (budgeted retries already exist).
- **Parallel subtasks:** existing worker orchestrator + isolated worktrees;
  sibling missions under one objective with a join condition; partial-effect
  reconciliation (already typed) runs per-subtask before the join.

### Self-expansion design (all seams exist; this wires them)

```text
gap detected (world_model/gaps.py, fired by planning/capability-catalog miss)
→ candidate of class CAPABILITY_GAP into the SAME V11 loop
→ solution search: current JARVIS (fabric/catalog.py) → historical JARVIS
  (operations/reuse.py over the worktree/branch archaeology — V10-D lane
  already built this muscle) → mature OSS/API/MCP (research capability)
→ comparison record (same scoring factors + integration-cost estimate)
→ isolated trial: disposable worktree + V6 lab/shadow machinery
  (workers/lab.py, evolution shadow bridge) — zero live effects
→ integrate as thin adapter behind the standard connector/worker contract
→ verify: focused tests + independent verifier on the trial evidence
→ register in the Capability Fabric Catalog (fabric/catalog.py) — the SINGLE
  canonical capability/readiness authority — with trust state PROBATION
  (new capabilities can't perform irreversible effects until promoted).
  capabilities/registry.py may persist only as a read-only compatibility
  projection of the catalog if still required; never create two writable
  capability authorities
→ rollback/disable: catalog entries are toggleable; adapter removal restores
  prior catalog state; failed trials close with a rejection record
→ resume: the original objective's wake condition was "capability X registered" —
  it fires, planning re-runs, the mission continues
```

- **Lanes (3):** L1 campaign durability + reboot/outage proofs; L2
  self-expansion pipeline; L3 first irreversible-effect class under policy
  (public GitHub PR to owner's repo; email to owner's own address).
- **External:** MCP client is the preferred first expansion target (one
  adapter, many tools — the highest-leverage expansion and the best proof of
  the pipeline itself). It is MISSION-PULLED, not a gate.

| Capability | Class |
|---|---|
| A3/A4 passed on promoted V11 base | HARD ENTRY |
| Campaign checkpoint/resume (objective-scale reboot/outage/wait/deadline survival), next additive schema revision | EXIT REQUIREMENT |
| Self-expansion pipeline live on a REAL gap (gap→search→trial→register PROBATION→resume), rollback/disable proven | EXIT REQUIREMENT |
| First irreversible-effect class under policy (GitHub PR to owner repo; email to owner's own address) | EXIT REQUIREMENT |
| MCP client connector | MISSION-PULLED / OPTIONAL (preferred first expansion target) |
| Gmail read | MISSION-PULLED / OPTIONAL |
| Windows UIA, calendar, OCR | explicitly out |

- **Flagship proof (Ladder A5/A6):** a 3-day campaign: JARVIS discovers a real
  improvement requiring a capability it lacks, acquires it via the pipeline
  above, resumes, builds, verifies, opens a real PR on the owner's GitHub —
  surviving one forced reboot and one simulated provider outage mid-campaign.
- **Failure/recovery proof:** trial capability fails verification → disabled,
  objective replans or closes honestly; reboot during the expansion trial →
  trial worktree recovered or discarded cleanly, no registry corruption;
  ambiguous partial external effect (PR half-created) → reconciliation
  resolves via effect identity before any retry.
- **Exit gate:** A5 + A6 live; a capability registered by JARVIS itself is in
  the catalog with honest trust state; zero duplicate external effects across
  all forced faults. Then V12 promotion.
- **NOT in phase:** Windows UIA, calendar, owner-context modeling beyond V11's
  seed, voice.

## V13 — PERSONAL OPERATOR

- **Objective:** prioritization driven by a real owner model; operator breadth
  added only where live missions demand it.
- **Owner-visible gain:** JARVIS chooses work the owner would have chosen,
  explains why in owner terms, and operates across mail/calendar/web
  apps/(minimal) Windows to finish it.

### Personal executive intelligence design

- **What is remembered** (Tier-C blocks + world-model entities, all through
  existing curation gates; next additive schema revision): owner goals with horizons; active
  projects and their states; commitments/deadlines (sourced from
  Gmail/calendar observation); standing decisions and vetoes ("never X",
  "prefer Y"); rejected-idea history with reasons; recurring problems;
  observed value patterns (which past autonomous work the owner kept vs.
  discarded).
- **What is deliberately NOT remembered:** raw browsing/content dumps,
  unconsolidated observation streams, or inferred preferences without evidence
  links — every memory entry carries provenance and survives the existing
  dup/contradiction/defense gates, which is how noise is prevented.
  Consolidation summarizes; TTL expires stale context; contradiction gates
  force reconciliation when the owner changes direction.
- **How it changes prioritization:** these records are *inputs to existing
  scoring factors* (relevance, urgency, veto filters) — not a parallel
  decision system. A veto is a hard reject; a goal-linkage raises relevance; a
  past-discard pattern lowers expected benefit for similar candidates.

### Digital operator scope (minimum for real end-to-end work)

Authenticated web-app workflows via the interactive browser (live since V10);
Gmail send + calendar read/write under reversibility policy; minimal Windows
UIA verbs (launch/focus/read-window/click-by-automation-id) recovered from
Phase-4/5 prior art (`procedural_learning`,
`candidate/learned-procedure-phase5`) behind the standard connector contract —
added *only when a selected mission needs a desktop app*, and each verb ships
with its own effect receipt and postcondition. No generic RPA framework.

- **Lanes (3):** L1 owner-context model + scoring integration + "why I chose
  this" briefing; L2 provider effects (calendar, mail workflows) as durable
  campaign steps; L3 mission-pulled operator verbs (Windows/cross-app),
  strictly demand-driven.

| Capability | Class |
|---|---|
| Gmail live read (real personal signal stream) | HARD ENTRY |
| Owner-context model in Tier C feeding scoring factors; "why I chose this" briefing | EXIT REQUIREMENT |
| Gmail send + calendar under reversibility policy | EXIT REQUIREMENT |
| Windows UIA verbs, cross-app workflows, OCR/media, further providers | MISSION-PULLED / OPTIONAL (verb-by-verb, receipts each) |
| Voice / Albanian | POST-1.0 |

- **Flagship proof (Ladder A7):** JARVIS observes a real commitment arriving
  by email, generates and ranks candidates against real owner goals,
  autonomously executes a multi-step cross-surface task (research → document →
  email to owner / calendar entry / repo change), and its briefing explains
  the choice in terms of the owner model.
- **Failure/recovery proof:** owner redirect mid-campaign honored instantly
  and recorded as a standing decision; a wrong-priority selection corrected by
  veto → veto becomes durable and provably prevents recurrence.
- **Exit gate:** two weeks of unattended operation where ≥80% of autonomous
  selections are accepted (not vetoed) by the owner in retrospect review;
  memory stays bounded (curation metrics flat, not growing). Then V13
  promotion. Elapsed-time proving must not idle engineering: once the
  functional candidate is frozen for the two-week window, V14 planning and
  isolated development may proceed in parallel; V14 live promotion remains
  blocked until this gate passes.
- **NOT in phase:** voice, Albanian, OCR/media unless a mission pulls them,
  additional providers beyond Gmail/calendar.

## V14 = 1.0 — FINAL CONVERGENCE

- **Objective:** converge all lanes, run the flagship acceptance (below),
  promote to the installed live release under existing release authority.
- **Lanes (2):** L1 semantic convergence + broad suite + drift reconciliation
  of `CURRENT_STATE.md`; L2 flagship rehearsal → live run → promotion with
  exact-SHA proof.
- **Exit gate:** the FINAL 1.0 PROOF (A8) passes live, on the installed
  release, with the runtime surviving the full window.

---

# FINAL 1.0 PROOF — THE 72-HOUR OWNER-ABSENT CAMPAIGN (A8)

Owner writes one standing goal statement, grants scopes, then does not prompt
again for 72 hours (except two scripted interventions).

1. **Hour 0:** JARVIS begins scheduled observation of the owner's repos,
   watchlist, inbox, projects.
2. **Day 1:** it generates ≥10 candidates, rejects the majority with reasons,
   researches finalists, ranks them, and autonomously selects one substantial
   improvement — chosen to require a capability it does not yet have.
3. It creates the objective, plans, hits the gap, runs the self-expansion
   pipeline (search → compare → isolated trial → integrate → verify →
   register), and resumes the original objective.
4. **Day 2, scripted fault #1:** forced machine reboot mid-build. JARVIS must
   recover the campaign, reconcile in-flight effects, and continue without
   duplication. A simulated provider outage follows; affected work reaches
   honest WAIT and resumes on recovery.
5. It builds, tests, encounters at least one real failure, repairs, and passes
   **independent** verification.
6. It performs a real external effect (e.g., PR on the owner's repo, or a
   document emailed to the owner) exactly once, with effect identity proving
   non-duplication.
7. **Day 3, scripted fault #2:** owner issues STOP mid-action. STOP
   immediately prevents any new dispatch or continuation; an already in-flight
   external effect may land as UNKNOWN/FROZEN and must be reconciled through
   effect identity before any further action — STOP does not imply the
   provider-side effect was undone. RESUME → continuation from the exact
   reconciled state.
8. It records outcomes, updates the world model and scoring priors, selects a
   *second, smaller* justified action, completes it, and returns to WAIT with
   live wake conditions.
9. **Acceptance:** the Command Center briefing alone — with the ledger behind
   it — must let the owner reconstruct the entire 72 hours: every candidate,
   every rejection reason, every effect receipt, both fault recoveries, and
   the learning writeback. Zero fabricated states; every UNKNOWN honest.

## Autonomy acceptance ladder

| Rung | Mission | Proves | Phase |
|---|---|---|---|
| A1 | Given objective → research → verified artifact | Execution spine | done (V9/V10) |
| A2 | Unprompted observation cycle → candidates + reasoned rejections, no execution | Discovery + judgment, safely inert | V11 early |
| A3 | Full loop: discover→choose→build→verify→learn, one small real repo improvement | Initiative | V11 exit |
| A4 | A3 with kill/restart at three points + STOP/RESUME | Loop durability | V11 exit |
| A5 | Mission blocked by real gap → self-expansion → resume → complete | Self-expansion | V12 |
| A6 | 3-day campaign, reboot + outage + real external effect (PR/email) | Endurance + effects | V12 exit |
| A7 | Owner-context-driven cross-surface task with "why" briefing, 2-week unattended window | Personal operator | V13 exit |
| A8 | 72-hour Owner-Absent Campaign | JARVIS 1.0 | V14 |

The ladder rungs A2–A8 are the ONLY phase gates. Feature count proves nothing.

---

# WHAT TO REMOVE / AVOID

Top 10 things NOT to build:

1. **A second executive/orchestrator** (LangGraph, AutoGen, CrewAI, custom
   "discovery agent"). The Continuous Executive is the only chooser. External
   agents = bounded workers, period.
2. **Vector database / embedding memory platform.** Lexical Tier-C with
   curation gates is proven; the hook stays reserved until a documented recall
   failure.
3. **Enterprise security theater** — OWASP sweeps, RBAC, multi-tenant
   isolation, compliance docs. Keep only the practical safeguards already in
   doctrine (destructive effects, secret leakage, wrong process termination,
   duplicate actions, corrupted data, ambiguous partial effects).
4. **Voice + Albanian/English interface before 1.0.** Post-1.0 by product
   decision. Any session spent here before A8 passes is waste.
5. **Generic Windows RPA framework.** Individual UIA verbs pulled by real
   missions, each with receipts. No screen-recording-replay platform, no full
   OpenAdapt activation as infrastructure.
6. **Command Center rewrite / cinematic expansion.** One additive
   autonomy-briefing projection is the only UI work on the critical path.
7. **Checklist provider adapters** (Slack, Notion, Discord, Twitter/X,
   Drive…) built preemptively. Each provider enters only when a scored
   candidate needs it — ideally *via* the self-expansion pipeline, which turns
   adapter-building into proof of V12.
8. **OCR/media pipeline as a phase.** Mission-pulled, thin wrapper over mature
   OSS (e.g., RapidOCR/Tesseract) when needed.
9. **Distributed/multi-machine operation.** Single host until well past 1.0.
10. **Custom browser stack or anti-bot evasion engineering.** Playwright +
    persistent sessions is the ceiling; sites that defeat it get honest
    BLOCKED records.

Two subtler traps: **test-count maximization at lane level** (prove behavior;
growth must be justified per capability) and **re-litigating rejected
candidates** (the rejection ledger exists precisely so future cycles and
future agents do not redo analysis without new evidence).

---

# FASTEST SAFE PATH TO JARVIS 1.0

## Critical path (strict order; everything else is parallel)

```text
V10 lane convergence → sensory completion → final exact-SHA
→ rehearsal/promotion/live validation
→ V11 L1+L2 (parallel) → A2 → L3 → A3/A4 → V11 promotion
→ V12 L1 ∥ L2 ∥ L3 → A5/A6 → V12 promotion
→ V13 L1 ∥ L2 ∥ L3 → A7 → V13 promotion
→ V14 convergence → A8 flagship → 1.0 promotion
```

Hard dependencies: schema migrations serialize (candidates/rejections →
campaigns → owner-context, each an additive revision whose actual number is
determined from the exact promoted predecessor at phase start); nothing touching live
runtime/schema/release pointers runs in parallel; each phase starts from the
previous promoted base. Gmail read must be live before V13 entry (and no
earlier requirement exists).

## Parallelization map

- **V10 close:** browser lane ∥ Gmail-read lane (fully independent; Gmail may
  be BLOCKED/DEFERRED without holding the phase).
- **V11:** observation (L1) ∥ judgment (L2) build simultaneously against the
  shared candidate schema; execution bridge (L3) starts against fixtures,
  converges last.
- **V12:** endurance (L1) ∥ self-expansion (L2) ∥ external-effects policy
  (L3) — three genuinely independent surfaces; converge before A6.
- **V13:** owner-context (L1) ∥ provider effects (L2) ∥ mission-pulled
  operator verbs (L3).
- **Never parallel:** anything touching live runtime/schema/release pointers.

## Top 10 highest-leverage remaining builds (capability per engineering hour)

1. **Wire observation → `discovery_candidates`** — sensors and candidate store
   both exist; pure wiring, unlocks everything.
2. **Rejection records + fingerprint dedup** — reuses Workflow-Discovery
   fingerprints + Tier-C dup gates; makes autonomy honest and bounded.
3. **Autonomy governor** (WIP cap, budgets, TTL, reversibility classes) —
   small policy code; it is the safety case for everything after.
4. **Interactive browser actions** — extends a proven substrate; doubles the
   observable/operable web.
5. **Gmail live read** — adapter exists; credentials + probe; first real
   personal signal (whenever credentials arrive).
6. **Outcome→prior calibration wiring** — both `calibration.py` files exist;
   closing them makes JARVIS *learn*.
7. **Self-expansion wiring** (`gaps` → `reuse` → `expansion` → catalog
   trial) — seams all exist; converts blockers into work.
8. **Campaign checkpoint/resume records** — generalizes proven restart
   recovery to objectives.
9. **MCP client connector** — one adapter, an ecosystem of tools under
   existing contracts.
10. **Command Center autonomy briefing** — one projection over existing ledger
    data; the owner's window into everything above.

## Final architecture diagram (1.0 — unchanged spine, initiative wired)

```text
                    OWNER (goals / scopes / STOP / veto / redirect)
                                      │ always dominant
┌─────────────────────────────────────▼─────────────────────────────────────┐
│ WORLD INTELLIGENCE / WORLD MODEL                                          │
│  observations ◄── scheduled watch cycles (web·GitHub·Gmail·projects·FS)   │
│  signals → discovery_monitors → candidates → fingerprint dedup            │
│  gaps.py → CAPABILITY_GAP candidates      patterns/calibration ◄─ outcomes│
└─────────────┬─────────────────────────────────────────────▲───────────────┘
              │ candidates + research + owner-context        │ learning
┌─────────────▼─────────────────────────────────────────────┴───────────────┐
│ CONTINUOUS EXECUTIVE (the only chooser)                                   │
│  scoring (reject-first, owner-model factors) → decision_queue             │
│  governor: WIP caps · budgets · TTL · reversibility policy                │
│  goals/missions · waits/wakes/deadlines · retries/escalation · campaigns  │
└─────────────┬──────────────────────────────────────────────────────────────┘
              │ objectives / plans
┌─────────────▼──────────────────────────────────────────────────────────────┐
│ CAPABILITY FABRIC  (catalog · trust states · expansion pipeline)           │
│  connectors: browser(interactive+auth) · web · GitHub r/w · git · files    │
│              docs/data · Gmail r/w · calendar · MCP client · UIA verbs     │
│  workers: Hermes · OpenHands · bounded external agents (contract-isolated) │
└─────────────┬──────────────────────────────────────────────────────────────┘
              │ ActionEnvelopes
┌─────────────▼──────────────────────────────────────────────────────────────┐
│ MISSIONLOOP → typed effects/receipts/idempotency → evidence →              │
│ INDEPENDENT VERIFIER → reconciliation of partial/ambiguous effects         │
└─────────────┬──────────────────────────────────────────────────────────────┘
              │ world update → continue / retry / wait / replan / close
              ▼
   Append-only LEDGER ══ single truth ══► COMMAND CENTER (autonomy briefing)
   Release authority · Supervisor singleton · STOP sentinel  (all unchanged)
```

## Execution strategy

Per phase, exactly the structure proven since V7: **1 planning/orientation
session** (inventory, base SHA, lane specs, acceptance gates) → **2–3 parallel
build lanes** in isolated worktrees → **1 semantic convergence** (broad suite
+ independent verification) → **1 combined rehearsal/promotion/live-validation
session**. For the V10 close, compress further: the sensory completion is
small enough that planning + both lanes fit one session and convergence +
promotion another. No review-only or documentation-only sessions;
documentation reconciliation rides the convergence session (anti-staleness
contract already enforces it).

---

# DECISION LOG

| # | Decision | Reason |
|---|---|---|
| D1 | Collapse V11–V16 into V11–V14 | Initiative is the bottleneck; interface phase was redundant; operator breadth is pull-based |
| D2 | V10 closes with ONE release cycle; the 2-item sensory completion (browser interaction + auth sessions) lands inside the current candidate line before the single final exact-SHA convergence and promotion | Sweep items are V11 prerequisites; no schema/runtime change justifies a second promotion; instability slips to a V11 lane |
| D3 | Autonomous candidates enter the existing Executive queue | Prevents a second executive; owner work dominance falls out for free |
| D4 | Rejection records are first-class and mandatory | Bounded autonomy, honest reporting, dedup, and learning all depend on them |
| D5 | Self-expansion is triggered by real gaps inside the live loop (V12, not before) | Synthetic-gap testing proves nothing; seams (`gaps`/`expansion`/`reuse`) already exist |
| D6 | Owner-context is scoring input, not a parallel decision system | Keeps one authority spine; memory noise controlled by existing Tier-C gates |
| D7 | Reversibility policy governs autonomy boundary, enforced at the effect layer | Reuses effect model; avoids a new approval plane |
| D8 | MCP client is the flagship expansion target, mission-pulled, not a gate | One adapter buys an ecosystem and doubles as the V12 pipeline proof |
| D9 | Windows UIA recovered from Phase-4/5 prior art, mission-pulled, verb-by-verb | Reuse-first; avoids RPA platform trap |
| D10 | Voice/Albanian is POST-1.0 by explicit product decision; Command Center (text) is the only 1.0 interface | Owner deferral formalized; core autonomy never waits for voice |
| D11 | Acceptance ladder (A2–A8) gates every phase | Proves autonomy, not feature count |
| D12 | Gmail read is OPTIONAL until V13, where it becomes a HARD ENTRY requirement; unavailable credentials are recorded `BLOCKED/DEFERRED` and never pause the program | V11 initiative runs on GitHub/web/project/FS/world-model signals alone; A7 needs one real personal stream |

---

# NOT TO BE REVISITED WITHOUT NEW EVIDENCE

- One Executive; autonomous candidates enter its existing queue (no second
  orchestrator, no agent framework as orchestrator).
- Rejection-first ranking with mandatory reasoned rejection records; the
  anti-runaway governor.
- Reversibility policy at the effect layer as the autonomy boundary (no new
  approval plane).
- Self-expansion triggered only by real gaps inside the live loop; PROBATION
  trust for self-registered capabilities.
- Owner-context as scoring input only, gated by Tier-C curation.
- Mission-pulled operator breadth (no checklist adapters, no RPA platform, no
  custom browser stack).
- Acceptance ladder A2–A8 as the only phase gates;
  capability-gain-per-engineering-hour doctrine.
- Voice/Albanian post-1.0; no embeddings/vector platform, no enterprise
  compliance, no multi-machine before 1.0.
- The 72-hour Owner-Absent Campaign as the sole 1.0 acceptance test.
- Single V10 release cycle (sensory completion inside V10, no second
  promotion).

---

# NEXT 3 ENGINEERING JOBS

1. **V10_EXIT rehearsal and live validation** — use the exact clean candidate
   above for the single rehearsal → promotion → live-validation sequence.
   Re-read provider credentials, release, database, Scheduler, reboot, and
   endurance boundaries before any operational action; Gmail remains
   `BLOCKED/DEFERRED` unless credentials are already available.
2. **V11 Lanes 1+2 (parallel worktrees)** — observation cycles (owner GitHub,
   web watchlist, project/filesystem drift, world-model signals) into
   `discovery_monitors` → `discovery_candidates` with fingerprint dedup
   (next additive schema revision) ∥ rejection-first scoring in `executive/scoring.py` with the
   governor (WIP cap = 1, budgets, TTL, reversibility classes) and the
   hand-seeded owner-context block in Tier C. Exit on **A2**: an unattended
   cycle producing real candidates and reasoned rejections while executing
   nothing.
3. **V11 Lane 3 + convergence** — candidate→objective initiation through the
   existing Executive (`mission/proposal.py` → objective → isolated worktree
   build → independent verification), outcome→calibration writeback, Command
   Center autonomy briefing. Exit on **A3 + A4**: one autonomous verified
   improvement to an owner repo; loop survives kill/restart at three points
   plus STOP/RESUME. Then V11 promotion.

---

# FROZEN ROADMAP VERDICT

V10 closes with one release cycle (convergence → sensory completion →
exact-SHA → rehearsal/promotion/live validation). Then V11 Initiative
(A2→A3/A4), V12 Endurance + Self-Expansion (A5/A6), V13 Personal Operator
(A7, Gmail read as hard entry), V14 = 1.0 via the 72-hour Owner-Absent
Campaign (A8). Gmail is never a blocker before V13; voice/Albanian is post-1.0
by product decision; all listed architectural decisions are frozen absent new
evidence.

# IMMEDIATE NEXT ACTION AFTER V10_EXIT CANDIDATE

Run the single exact-SHA V10 rehearsal → promotion → live-validation session
after fresh read-only boundary checks. If and only if that live gate passes,
open V11 Lanes 1+2 from the promoted base. Do not start V11 from this
candidate while `V10_EXIT` remains `NOT_PASSED`.
