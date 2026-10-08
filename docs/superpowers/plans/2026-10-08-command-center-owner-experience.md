# Command Center Owner Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rescue the live Command Center owner experience so Home communicates NOW/WHY/NEXT, Hoowen active work beats historical failures, and mission views are radically simpler — all from exact causal truth.

**Architecture:** Pure frontend rescue plus one additive read-only selector layer. New `src/lib/ownerWork.ts` owns all owner-priority joins (goal → child missions via `created_from` directive id + work-hierarchy links); routes consume it. No autonomy-kernel, provider, schema, or authority change.

**Tech Stack:** React 19 + TypeScript + react-router-dom 7 + Vite 7 + Vitest 4 + existing `product.css` calm layer.

**Spec:** `docs/superpowers/specs/2026-10-08-command-center-owner-experience-design.md`

## Global Constraints

- All owner-facing state derives from the loaded canonical snapshot + autonomy projection + work hierarchy; `UNKNOWN`/`UNAVAILABLE` stay valid outputs.
- Never borrow titles, verdicts, or next actions across missions; joins only by `mission_id`, `created_from` directive id, work-hierarchy links, decision-queue `mission_id` refs.
- No autonomy-kernel, provider-routing, self-evolution, schema, trust-state, or release-authority change.
- Preserve `useRouteQuery` + `keepPreviousWhileLoading` + `RouteQueryGate`/`DetailRouteQueryGate` + `routeResilience` + `PollingEventStream` reconnect behavior on every touched route.
- Monospace only for IDs/SHAs/evidence; primary copy is sans-serif sentence case.
- Frontend checks per touched area: `npx tsc -b --noEmit`, `npm run build`, `npx vitest run <touched>`.
- Base: `c64673f0ece00c4e89cbdbc58f776a3ad187cbf4` on branch `fix/prompt-to-outcome-20261005`; isolated rescue branch for this work; preserve pre-existing dirty `config/worker_routing.yaml`.

## Review Focus

- A BLOCKED Hoowen child borrows the old CI mission's next action or verdict — expect strict subgraph isolation.
- A historical mission with no linked decision record still shows an owner-action demand — expect "Nothing needed from you."
- HALT appears on a terminal BLOCKED mission in LIVE — expect it hidden unless currently executing.
- Trust SUSPENDED renders as system-down language — expect three separated indicators plus tooltip.
- A disconnected refresh drops last-known state into an infinite loader — expect retained state + stale marking + reconnect.

---

### Task 1: Owner-truth selectors

**Files:**
- Create: `ui/command-center/src/lib/ownerWork.ts`
- Test: `ui/command-center/src/lib/__tests__/ownerWork.test.ts`

**Interfaces:**
- Consumes: `Mission`, `WorkHierarchy`, `OwnerControlSurface`, `AutonomyProjection` types from `src/lib/contracts.ts`; snapshot mission rows carry `id`, `status`, `updated_at`/`startedAt`, `created_from`/`owner`, `objective`, `nextAction`.
- Produces: `rankOwnerGoals(...)`, `currentChildForGoal(...)`, `goalChildren(...)`, `isHistoricalMission(...)`, `servingGoalLabel(...)`, `ownerActionForMission(...)`, `plainSummary(...)` — all pure functions later tasks import.

- [ ] **Step 1: Write the failing test**

```ts
// ownerWork.test.ts — Hoowen DELEGATE goal outranks historical CI BLOCKED;
// current child = newest created_from match; historical check uses linkage,
// not recency alone; owner action is null without a linked open decision record.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/lib/__tests__/ownerWork.test.ts`
Expected: FAIL with "Cannot find module './ownerWork'" (or `../ownerWork` per placement).

- [ ] **Step 3: Implement pure selectors in `src/lib/ownerWork.ts`**

Exact signatures: `rankOwnerGoals(goals, missions, hierarchy, autonomy)`, `currentChildForGoal(goalDirectiveId, missions)`, `goalChildren(...)`, `isHistoricalMission(mission, activeGoalIds, decisionQueue)`, `servingGoalLabel(mission, goals, hierarchy)`, `ownerActionForMission(mission, decisionQueue)`, `plainSummary(objective, maxSentences=3)`. Priority order per spec §2; historical rule per spec §2; no synthesis.

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/lib/__tests__/ownerWork.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui/command-center/src/lib/ownerWork.ts ui/command-center/src/lib/__tests__/ownerWork.test.ts
git commit -m "feat(command-center): owner-truth goal prioritization selectors"
```

### Task 2: JARVIS home NOW/WHY/NEXT

**Files:**
- Modify: `ui/command-center/src/routes/Overview.tsx`
- Test: `ui/command-center/src/routes/__tests__/overview.test.tsx` (extend), `src/lib/__tests__/ownerWork.test.ts` (reuse)

**Interfaces:**
- Consumes: Task 1 selectors + `loadOverviewData` `OverviewData` (missions, ownerSurface, autonomy, workers, evidence, timeline, ownerAttention).
- Produces: Home primary card with NOW/WHY/PROGRESS/LATEST/NEXT/NEEDS-YOU; Hoowen ACTIVE card while its directive is active.

- [ ] **Step 1: Write the failing test**

```tsx
// overview.test.tsx — with Hoowen active goal + blocked current child + one
// completed child: home shows Hoowen card, LATEST = completed child, NEEDS YOU
// = "Nothing needed from you." when no linked open decision record, no raw
// mission id in primary heading copy.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/routes/__tests__/overview.test.tsx`
Expected: FAIL (old first-BLOCKED-wins behavior surfaces wrong focus/needs-you).

- [ ] **Step 3: Rewire `OverviewContent` to Task 1 selectors**

Replace `activeMission`=first-ACTIVE and `blockedMission`=first-BLOCKED logic with ranked goals → current child → workers → latest completed → linked blocker. Keep lifecycle/today/secondary sections and all reliability wrappers untouched.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/routes/__tests__/overview.test.tsx src/lib/__tests__/ownerWork.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui/command-center/src/routes/Overview.tsx ui/command-center/src/routes/__tests__/overview.test.tsx
git commit -m "feat(command-center): home prioritizes active Hoowen goal with truthful next"
```

### Task 3: Work grouped by owner goal

**Files:**
- Modify: `ui/command-center/src/routes/Work.tsx`
- Test: `ui/command-center/src/routes/__tests__/work.test.tsx` (new or extend existing work coverage)

**Interfaces:**
- Consumes: Task 1 selectors + `WorkHierarchy` + `Mission[]`.
- Produces: Goal-first grouping with Hoowen ACTIVE first (current child, mission, worker, last result, next + child history); explicit unlinked section.

- [ ] **Step 1: Write the failing test**

```tsx
// work.test.tsx — Hoowen goal section renders first with current/blocked child,
// last COMPLETE result, next action, and child history; unlinked CI mission
// stays outside the goal section.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/routes/__tests__/work.test.tsx`
Expected: FAIL (flat objective list puts history first).

- [ ] **Step 3: Implement goal-first grouping in `Work.tsx`**

Goal sections ordered by Task 1 rank; within a goal keep objective → campaign → mission nesting; preserve filters + hierarchy-availability states + unlinked section.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/routes/__tests__/work.test.tsx src/lib/__tests__/ownerWork.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui/command-center/src/routes/Work.tsx ui/command-center/src/routes/__tests__/work.test.tsx
git commit -m "feat(command-center): work grouped around owner goals"
```

### Task 4: Mission page simplification

**Files:**
- Modify: `ui/command-center/src/routes/MissionDetail.tsx`
- Test: `ui/command-center/src/routes/__tests__/missionDetail.test.tsx` (extend)

**Interfaces:**
- Consumes: Task 1 `servingGoalLabel`, `ownerActionForMission`, `isHistoricalMission` + existing `MissionRun.lifecycle`, workers, evidence, related.
- Produces: Primary order title/status/serves/summary/current-result/next/owner-action + compact Goal→Plan→Build→Verify→Outcome tracker + disclosed technical sections + state-aware buttons + historical banner.

- [ ] **Step 1: Write the failing test**

```tsx
// missionDetail.test.tsx — historical BLOCKED mission shows historical banner
// + "Not affecting current Hoowen work" when unlinked, hides HALT in LIVE,
// hides RE-EVALUATE GATE in LIVE, shows compact tracker, keeps evidence behind disclosure.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/routes/__tests__/missionDetail.test.tsx`
Expected: FAIL (giant pipeline + unconditional buttons still present).

- [ ] **Step 3: Rebuild `MissionDetail` primary per spec §5**

Keep all data fetching/mapping identical; restructure presentation only. Buttons: HALT only if executing + mutationsEnabled; gate button hidden in LIVE. Historical banner gated on `isHistoricalMission` + Hoowen-active check.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/routes/__tests__/missionDetail.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui/command-center/src/routes/MissionDetail.tsx ui/command-center/src/routes/__tests__/missionDetail.test.tsx
git commit -m "feat(command-center): simplified mission view with historical truth"
```

### Task 5: Missions list prioritization

**Files:**
- Modify: `ui/command-center/src/routes/Missions.tsx`
- Test: `ui/command-center/src/routes/__tests__/missions.test.tsx` (extend)

**Interfaces:**
- Consumes: Task 1 `isHistoricalMission` + mission `status`/`updated_at`.
- Produces: Default sort active-executing > recent completed > attention > historical; historical badge on aged/unlinked BLOCKED rows.

- [ ] **Step 1: Write the failing test**

```tsx
// missions.test.tsx — default order puts executing/recent above historical
// CI BLOCKED; historical row carries an explicit badge.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/routes/__tests__/missions.test.tsx`
Expected: FAIL

- [ ] **Step 3: Implement priority-aware default sort + badge in `Missions.tsx`**

Preserve search/filter/pagination/sort-override behavior.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/routes/__tests__/missions.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui/command-center/src/routes/Missions.tsx ui/command-center/src/routes/__tests__/missions.test.tsx
git commit -m "feat(command-center): missions prioritize current work over history"
```

### Task 6: Health / trust separation

**Files:**
- Modify: `ui/command-center/src/components/TopStatusBar.tsx`, `ui/command-center/src/components/TrustDialog.tsx`
- Test: `ui/command-center/src/components/__tests__/topStatusBar.test.tsx` (extend)

**Interfaces:**
- Consumes: `connection`, `trustSummary`, autonomy executive state, STOP sentinel via `useCommandCenter`.
- Produces: Three separated indicators (System / Autonomy / Trust) + SUSPENDED tooltip copy per spec §6.

- [ ] **Step 1: Write the failing test**

```tsx
// topStatusBar.test.tsx — system, autonomy, and trust render as separate
// indicators; trust SUSPENDED carries the permitted-work explanation.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/__tests__/topStatusBar.test.tsx`
Expected: FAIL

- [ ] **Step 3: Implement separated indicators + tooltip**

No change to STOP semantics or trust derivation (`snapshotMapper.mapTrustSummary` untouched).

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/components/__tests__/topStatusBar.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui/command-center/src/components/TopStatusBar.tsx ui/command-center/src/components/TrustDialog.tsx ui/command-center/src/components/__tests__/topStatusBar.test.tsx
git commit -m "feat(command-center): separated system autonomy trust indicators"
```

### Task 7: Owner command dock

**Files:**
- Modify: `ui/command-center/src/components/CommandBar.tsx`, `ui/command-center/src/components/OwnerCommandSurface.tsx`
- Test: `ui/command-center/src/components/__tests__/commandBar.test.tsx` (new) or extend existing command coverage

**Interfaces:**
- Consumes: `client.submitOwnerGoal`, `submitVeto`, `pauseOperations`, `activateStop`, `mutationsEnabled`, `stopActive`.
- Produces: Visible GOAL (primary) / ASK / VETO tabs + separate unmistakable STOP; durable feedback.

- [ ] **Step 1: Write the failing test**

```tsx
// commandBar.test.tsx — GOAL is the default primary tab, ASK and VETO are
// one click away, STOP is visually separate from the submit path.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/__tests__/commandBar.test.tsx`
Expected: FAIL (module or tab structure missing).

- [ ] **Step 3: Implement tabbed dock with GOAL primary**

Keep all mutation paths (`submitOwnerGoal` HANDLE/ASK mapping, veto, stop) identical; restructure presentation + placement only.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/components/__tests__/commandBar.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui/command-center/src/components/CommandBar.tsx ui/command-center/src/components/OwnerCommandSurface.tsx ui/command-center/src/components/__tests__/commandBar.test.tsx
git commit -m "feat(command-center): obvious goal ask veto command dock"
```

### Task 8: Visual + responsive pass

**Files:**
- Modify: `ui/command-center/src/styles/product.css` (extend only), touched routes as needed for overflow
- Test: `ui/command-center/scripts/product-verify.mjs` + `capture-remaster.mjs` (existing discipline)

**Interfaces:**
- Consumes: All Task 2–7 markup class names.
- Produces: Overflow-free 1920×1080 + 1366×768, no horizontal pipeline explosion, critical actions above the fold.

- [ ] **Step 1: Write the failing check**

Run: `node scripts/product-verify.mjs` (or `npm run verify:product`)
Expected: FAIL on overflow / pipeline width / tiny-type violations for the new markup.

- [ ] **Step 2: Extend `product.css` + markup for the two viewports**

Sans-first, sentence case, layered surfaces; monospace only for `.tech-id`/hashes.

- [ ] **Step 3: Re-run verification**

Run: `npm run verify:product` + `npx tsc -b --noEmit` + `npm run build`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add ui/command-center/src/styles/product.css ui/command-center/src/routes/Overview.tsx ui/command-center/src/routes/Work.tsx ui/command-center/src/routes/MissionDetail.tsx ui/command-center/src/routes/Missions.tsx ui/command-center/src/components/CommandBar.tsx ui/command-center/src/components/TopStatusBar.tsx
git commit -m "style(command-center): owner-first visual language at 1080p and laptop"
```

### Task 9: Live browser acceptance

**Files:**
- No source change (evidence only): screenshots + `var/owner-experience-20261008/ACCEPTANCE.md`
- Test: live journeys A–F against `http://localhost:8767` (Supervisor-served static root) or the documented `8765→4173` proxy pair when the Supervisor static root is absent.

**Interfaces:**
- Consumes: Canonical release authority + Supervisor Command Center (port from `service.py` config); read-only live DB.
- Produces: Journey proof (Home 5-second read, Work Hoowen grouping, old-mission historical banner, current-Hoowen lineage, refresh sameness, disconnect/reconnect retention) + 1920 + laptop screenshots.

- [ ] **Step 1: Start the canonical API + UI without mutating live state**

Run: Supervisor-owned Command Center (preferred) or `scripts/serve_command_center_api.py --port 8765` + `npm run dev` proxied; record exact SHAs, release id, port pair.

- [ ] **Step 2: Execute journeys A–F in a real browser and capture evidence**

Record: HOME / HOOWEN / CURRENT WORK / OLD BLOCKED MISSION / MISSION VIEW / OWNER COMMAND / HEALTH-TRUST / LOADING-RECONNECT / 1920 / LAPTOP.

- [ ] **Step 3: Run the full frontend gate**

Run: `npx tsc -b --noEmit`, `npm run build`, `npx vitest run`
Expected: PASS with zero P0/P1 owner-facing defects, or file them as UNRESOLVED P0/P1.

- [ ] **Step 4: Commit evidence**

```bash
git add var/owner-experience-20261008/ACCEPTANCE.md
git commit -m "test(command-center): live owner-experience acceptance evidence"
```
