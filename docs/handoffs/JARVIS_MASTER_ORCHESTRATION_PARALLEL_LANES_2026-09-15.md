# JARVIS Master Orchestration — Parallel Session Program

**Date:** 2026-09-15  
**Authority:** owner request to plan maximum parallel work toward JARVIS 1.0  
**Orchestrator seat:** this Cursor chat (live serial + lock board + convergence)  
**Canonical base:** `4b2ae28da582b369982661c2a1d2b1792e2f48cb` (`main` == `origin/main`)  
**Live CURRENT (as of 2026-09-15 20:35Z):** same SHA, release `rel-4b2ae28da582-795339fb`, schema 40, trust `SUSPENDED`  
**Owner handoff:** `C:\Users\b\Downloads\JARVIS_MASTER_HANDOFF_2026-09-15.md`

Attach this file plus the owner handoff to every worker session.  
`docs/CURRENT_STATE.md` and `docs/roadmap/JARVIS_1_0_EXECUTION_STATE.yaml` are **stale** (still describe V10/V11). Ignore them as live truth.

---

## 0. What “100% JARVIS” actually means

JARVIS 1.0 is not green tests, not a trust-window counter, and not more modules.

It is this owner condition:

> The owner gives a high-level objective and walks away. JARVIS understands what matters, chooses and executes useful multi-surface work, verifies and repairs, survives interruption, learns, continues, talks naturally, and remains controllable by GOAL / veto / STOP.

That is **V16**. Tokens can build almost all of the missing **source**. Tokens cannot compress:

- one live Supervisor / one live DB / one CURRENT pointer;
- one real multi-surface owner mission;
- reboot / STOP / provider interruption on the real machine;
- ~72 hours of genuine owner-absent elapsed time.

Honest split:

| Layer | Can parallelize with many models | Must stay serial |
| --- | --- | --- |
| Source gaps (V14 operator, V15 interface, self-expansion polish) | Yes | File locks on spine files |
| Isolated tests / reviews / docs of a candidate | Yes | Never rewrite live truth |
| Live GOAL / promote / STOP / Scheduler / DB | No | One orchestrator session |
| V14 close scenario on the owner machine | No | After source convergence |
| V16 72h flagship | No | Elapsed time |

Target if this program is executed well: **source-complete V14+V15 candidates**, then **one live V14 close**, then **V15 live**, then **V16 proof**. That is the shortest path to 1.0. There is no V17.

---

## 0.1 Frozen V14 lanes (MAP-1 closed 2026-09-15)

Source inventory from the 2026-09-15 operator map, **corrected** against live identity:

- Live CURRENT is `4b2ae28` / `rel-4b2ae28da582-795339fb`, not V10 `faa861c`.
- V11–V13 machinery is in that live package. Do not classify it as uninstalled. Most of it is **PRESENT_BUT_UNPROVEN** at product-proof level, not missing.
- Internally superseded: `connectors/browser.py` / V5 `ComposedMissionHarness` (`conn-browser-v5`). Reuse Playwright + `fabric/compose.py` + `computer_operator`.
- Gmail remains **OWNER_DEFERRED** for live. Voice/Albanian live cutover is later; Lane 5 may still do V15 **source**.
- Do not restaff MAP-1. Do not reopen V11/V12/V13 from zero.

**Tonight: six master prompts** live in `docs/handoffs/parallel-lanes-2026-09-15/`. Staff those six chats. Do not restaff MAP-1/MAP-2 or the old P15-* split.

| Lane | Gap (only this) | Reuse | Worker |
| --- | --- | --- | --- |
| A Browser | Owner-authorized authenticated site, session restore across restart, unattended `browser.web.execute` (today `INTERACTIVE_REQUIRED`) | `web_research.py` Playwright, `browser.web.*`, `storage_state`, `effects.py` | **Lane 1** |
| B Windows | One real allow-listed app: HWND/control identity, mutation + readback, interrupt/FROZEN. Tests today use fake UIA. | `fabric/computer_operator.py`, `windows_operator.py`, `desktop.action.reconcile` | **Lane 2** |
| C Services | One real owner-authorized GitHub (or MCP mutating) effect with readback and non-duplication. Fixtures do not count. No Gmail. | `github_write.py`, `git.push`, MCP backend, irreversible effect class | **Lane 3** |
| D Compose | One MissionLoop journey binding A+B+C with independent verify and STOP/crash reconcile. No second orchestrator. | MissionLoop, `fabric/compose.py`, catalog, `effects.py`, campaigns, CC projection | **Lane 4** |

**V14 close scenario (frozen, MAP-2 default):** public GitHub/docs page via Playwright → local XLSX/DOCX via General Work → open that artifact in one allow-listed Windows app and assert window/control → publish **one** authorized PR → kill or STOP mid-effect → resume with no duplicate effects. CC/ledger reconstructs receipts. Harmless, reversible, no Gmail, no voice. Live run is Wave 5 only.

---

## 1. Operating model

```text
YOU (owner)
  -> this orchestrator chat (locks, live, merge order, promotion)
       -> many worker sessions (isolated worktrees, source only)
            -> independent reviewer sessions (diff-only, different model)
                 -> one convergence session
                      -> one live-serial proof session
```

Workers **never**:

- touch live home `C:\Users\b\AppData\Local\hermes\foundry`;
- run `jarvis release activate`, STOP, resume, Scheduler, or owner GOAL against live;
- merge to `main` or push unless the orchestrator packet says so;
- edit a locked spine file without a written lock grant;
- create a second Executive / MissionLoop / Fabric catalog / effect journal / owner-control plane;
- touch Gmail;
- farm trust-window missions;
- reopen V11/V12/V13 from zero;
- merge P3-D3 (`repair/heartbeat-idempotency-conflict-20260914`) unless the orchestrator explicitly pulls it.

Live already has an in-flight owner GOAL `owner-046acf1169abf2810aaef8c9` against the disposable worktree `C:\temp\JARVIS-Goal1-OwnerWork-20260915`. Workers must not submit another live GOAL.

---

## 2. Worktree factory (every BUILD session)

From the clean repo:

```text
git fetch origin
git worktree add -b <BRANCH> "<WORKTREE>" 4b2ae28da582b369982661c2a1d2b1792e2f48cb
```

Convention:

```text
BRANCH    candidate/<lane-id>-20260915
WORKTREE  C:\temp\JARVIS\<lane-id>-20260915
```

If `C:\temp\JARVIS` does not exist, create it. Do not work on `main`. Do not share a worktree across two models.

---

## 3. File locks (one writer)

Request a lock in the orchestrator chat before touching these:

1. `src/jarvis_foundry/fabric/loop.py`
2. `src/jarvis_foundry/fabric/executors.py`
3. `src/jarvis_foundry/fabric/catalog.py`
4. `src/jarvis_foundry/fabric/adapter.py`
5. `src/jarvis_foundry/effects.py`
6. `src/jarvis_foundry/mission/loop.py`
7. `src/jarvis_foundry/control/broker.py`
8. `src/jarvis_foundry/owner_controls.py`
9. `src/jarvis_foundry/command_center.py`
10. `src/jarvis_foundry/migrations.py`
11. `ui/command-center/src/lib/contracts.ts`

Lock request format:

```text
LOCK REQUEST
LANE: P14-A
FILE: src/jarvis_foundry/fabric/executors.py
WHY: register one existing-capability verb, no new catalog authority
UNTIL: candidate commit
```

Default: **work around the lock**. Add tests, connector methods, UI, recipes that call **existing** verbs. Only the convergence session edits spine files, unless a lock is granted.

---

## 4. Waves

### Wave 0 — Live serial (orchestrator only)

**Do not staff this to GPT/Grok/OpenCode in parallel.**

- Observe the open GOAL; do not stampede.
- If it produces FAIL → repair dispatch (not `invalid_workforce_scope`) → fresh independent PASS, Phase 0 repair closure is done.
- If it PASSes without repair, that is honest; do not fake a FAIL.
- Do not close the trust window as PASSED. 2026-09-14 degraded, 2026-09-15 STOP/freeze, `slow_cycle` seq 245605. Counters stay truthful.
- P3-D3 stays out.

### Wave 1 — Read / freeze (start immediately, 2 sessions)

No code. Output is a V14 lane freeze and one close-scenario.

### Wave 2 — V14 source (start immediately, 4 worktrees)

Browser, Windows, GitHub/dev effects, compose recipes. No live mutation.

### Wave 2b — V15 source (start immediately, overlapping)

Voice adapter, Command Center i18n, notifications, CC polish. Same owner-control spine. No live voice cutover.

### Wave 2c — Self-expansion polish (optional overlap)

Only `self_expansion.py` / `adapter_factory/` / tests. **Do not** edit `fabric/catalog.py`.

### Wave 3 — Independent review

One reviewer per landed candidate. Different model than the author. Diff + tests + “did they fork the spine?”

### Wave 4 — Convergence (one session)

Semantic integration into `candidate/v14-v15-convergence-20260915`. One authority per concern. Then focused tests. No live activate.

### Wave 5 — Live V14 close (orchestrator + owner)

One real multi-surface owner objective. This **closes V14**, not a feature checklist.

### Wave 6 — Live V15

Voice + Albanian/English + interruption + notifications on the real interface, still HMAC/STOP underneath.

### Wave 7 — V16 / 1.0

No new architecture. Concurrent projects, self-expansion live proof, repair, reboot/STOP, human interface, then ~72h owner-absent. Elapsed time is the gate.

---

## 5. Session catalog

Staff **these 6 lanes tonight**. Packets: `docs/handoffs/parallel-lanes-2026-09-15/`. Do not staff live Wave 0/5/6/7 to random models.

| ID | Covers | Type | Model | Branch | Worktree |
| --- | --- | --- | --- | --- | --- |
| Lane 1 | V14 browser auth / unattended web | BUILD | OpenCode (fb Grok) | `candidate/lane-1-v14-browser-auth-20260915` | `C:\temp\JARVIS\lane-1-v14-browser-auth-20260915` |
| Lane 2 | V14 Windows operator | BUILD | Grok (fb OpenCode) | `candidate/lane-2-v14-windows-operator-20260915` | `C:\temp\JARVIS\lane-2-v14-windows-operator-20260915` |
| Lane 3 | V14 GitHub / MCP effects | BUILD | GPT (fb OpenCode) | `candidate/lane-3-v14-github-mcp-20260915` | `C:\temp\JARVIS\lane-3-v14-github-mcp-20260915` |
| Lane 4 | V14 MissionLoop compose | BUILD | GPT (fb Claude) | `candidate/lane-4-v14-compose-20260915` | `C:\temp\JARVIS\lane-4-v14-compose-20260915` |
| Lane 5 | V15 voice + EN/SQ + notify + CC | BUILD | Grok (fb GPT) | `candidate/lane-5-v15-human-interface-20260915` | `C:\temp\JARVIS\lane-5-v15-human-interface-20260915` |
| Lane 6 | V16 self-expansion / campaigns / 72h seams | BUILD | GPT (fb Claude) | `candidate/lane-6-v16-convergence-seams-20260915` | `C:\temp\JARVIS\lane-6-v16-convergence-seams-20260915` |
| CONV-1 | Semantic merge of the six | CONVERGE | this orchestrator | later | later |
| LIVE-* | V14 close, V15 live, V16 72h | LIVE | this orchestrator only | n/a | live runtime |

### Model bias (not religion)

- **GPT:** MAP, scenario design, independent review, compose contracts.
- **Grok / Cursor Agent:** Windows operator, UI, voice adapter, notifications.
- **OpenCode / worker models:** browser receipts, GitHub adapter tests, bounded connectors.
- **Claude:** MissionLoop compose, self-expansion seams, convergence.
- **This orchestrator:** live, locks, activate, STOP, GOAL, promotion.

If two models are equally fine, pick the cheaper one for BUILD and the stronger one for REVIEW.

---

## 6. Copy-paste packets

Every worker packet starts with the common preamble, then the lane block.

### Common preamble (paste first)

```text
You are a JARVIS Foundry worker, not the orchestrator.

BASE SHA (required): 4b2ae28da582b369982661c2a1d2b1792e2f48cb
Read: AGENTS.md, docs/agent/README.md, docs/agent/workflows/build.md,
and docs/handoffs/JARVIS_MASTER_ORCHESTRATION_PARALLEL_LANES_2026-09-15.md
Owner product intent: C:\Users\b\Downloads\JARVIS_MASTER_HANDOFF_2026-09-15.md

Create an isolated git worktree on the branch named in your lane. Do not work on main.

REUSE FIRST. Inspect current modules. Do not create a second Executive, MissionLoop,
Fabric catalog, effect journal, owner-control plane, or MCP authority.

FORBIDDEN:
- live home C:\Users\b\AppData\Local\hermes\foundry
- Scheduler, STOP, resume, jarvis release activate, owner GOAL against live
- Gmail
- P3-D3 branch
- trust-window farming / fake PASS
- editing locked spine files listed in the orchestration doc unless the packet
  explicitly grants a lock
- rewriting docs/CURRENT_STATE.md or the execution-state YAML
- restarting V11/V12/V13 from zero

Commit on the candidate branch if the owner asked this session to commit.
Do not merge or push unless the packet says so.

Final report MUST use:
VERDICT / BASE / FINAL / BRANCH / WORKTREE / REUSED / CHANGED /
REAL PROOF / TESTS / KNOWN LIMITATIONS / LIVE (must be NOT_TESTED) / NEXT
```

### MAP-1 — Capability accounting

```text
SUPERSEDED. MAP-1 is frozen in section 0.1 of this file (2026-09-15).
Do not restaff. If you opened this packet by mistake, stop.
```

### MAP-2 — V14 close scenario design

```text
SUPERSEDED. The V14 close scenario is frozen in section 0.1.
Do not restaff MAP-2. P14-A/B/C/D implement toward that scenario only.
```

### P14-A — Browser / authenticated web

```text
LANE: P14-A
ALLOWED: src/jarvis_foundry/connectors/web_research.py
         tests that target that module
         scripts that prove that module in isolation
FORBIDDEN LOCKS: fabric/loop.py, fabric/executors.py, fabric/catalog.py,
                 mission/loop.py, live Chromium against owner accounts
                 unless using a disposable test profile in the worktree
Do not revive src/jarvis_foundry/connectors/browser.py or the V5
ComposedMissionHarness. That path is superseded.

Reuse Playwright already in the product. Do not build a new browser engine.
Close real gaps only: session expiry recovery, download/upload robustness,
form/dialog, postcondition receipts. Authenticated-site work must fail closed
without owner-authorized credentials.

Acceptance: focused tests + one isolated script showing recover-from-expiry
or upload/download receipt against a local disposable page. LIVE=NOT_TESTED.
```

### P14-B — Windows / desktop operator

```text
LANE: P14-B
ALLOWED: src/jarvis_foundry/connectors/windows_operator.py
         src/jarvis_foundry/fabric/computer_operator.py
         src/jarvis_foundry/connectors/personal_operator.py
         tests/foundry/test_v13_windows_operator.py and new focused tests
FORBIDDEN LOCKS: mission/loop.py, fabric/executors.py, fabric/catalog.py
Do not build a generic RPA platform. Do not make OpenAdapt the operator.

Close real gaps: file-dialog, multi-app handoff helpers, app restart /
re-identification, readback/postcondition. Fail closed on identity ambiguity.

Acceptance: focused tests for identity-ambiguity fail-closed and one
readback/postcondition helper. LIVE=NOT_TESTED.
```

### P14-C — GitHub / developer effects (not Gmail)

```text
LANE: P14-C
ALLOWED: src/jarvis_foundry/connectors/github_write.py
         src/jarvis_foundry/connectors/git.py
         src/jarvis_foundry/connectors/github_research.py
         related tests / isolated proofs
FORBIDDEN: Gmail, live push to JARVIS-Foundry origin, fabric/executors.py
           without a lock grant, effects.py without a lock grant

Reuse existing write adapter. Make PR/issue/commit paths fail closed and
idempotent. Do not perform a real public write unless the packet later
grants it (this packet does not).

Acceptance: tests proving idempotent issue/PR adapter behavior against
fakes/recorded contracts. LIVE write = NOT_TESTED.
```

### P14-D — Cross-app compose (no second orchestrator)

```text
LANE: P14-D
ALLOWED: src/jarvis_foundry/fabric/compose.py
         new workflow recipe data under an existing recipes/workflows path
         tests for compose bindings
FORBIDDEN: new Executive, new MissionLoop, workers/orchestrator.py rewrite,
           mission/loop.py, fabric/loop.py, fabric/catalog.py

MissionLoop already sequences plan → dispatch → verify → repair.
Your job is reusable $step workflows: browser → file → desktop/git
using EXISTING verbs. If a verb is missing, document it as a lock request
for CONV-1; do not invent a catalog.

Acceptance: unit/integration tests that a named workflow binds outputs of
step N as inputs of step N+1 and fails closed on missing evidence.
LIVE=NOT_TESTED.
```

### P15-A — Voice gateway (source only)

```text
LANE: P15-A
ALLOWED: src/jarvis_foundry/voice.py and its tests
OPTIONAL: a thin STT/TTS adapter module next to voice.py
FORBIDDEN LOCKS: owner_controls.py, control/broker.py, command_center.py
                 unless lock granted. No second authority plane.
Voice commands must map into existing HMAC owner-control types
(GOAL/STOP/PAUSE/RESUME/VETO). STOP must work even if STT is dead.
Fixture STT/TTS may remain for tests; add a real-provider seam but do
not require live mic/speakers for acceptance.

Acceptance: tests that spoken STOP (fixture transcript) hits the same
command types as text STOP, replay-safe. LIVE mic = NOT_TESTED.
Albanian speech can land as a locale seam; do not fake fluency.
```

### P15-B — Command Center i18n (EN/SQ)

```text
LANE: P15-B
ALLOWED: ui/command-center/src/** (UI strings, locale provider, tests)
FORBIDDEN LOCKS: command_center.py, contracts.ts unless lock granted
Reuse briefing l10n in src/jarvis_foundry/convergence/briefings_l10n.py
as the string authority where possible. English default. Albanian toggle.
Ledger numbers/SHAs stay untranslated.

Acceptance: UI tests that locale toggle changes chrome strings and does
not invent mission state. LIVE CC = NOT_TESTED.
```

### P15-C — Notifications

```text
LANE: P15-C
ALLOWED: briefing/decision_queue/chief_of_staff notification projection,
         a thin Windows toast adapter, ui notifications route tests
FORBIDDEN: new attention authority, Gmail, push services that need new
           owner accounts, command_center.py without lock

Reuse ledger-derived attention. Add local OS toast for already-projected
owner-attention items. Fail closed if toast backend missing.

Acceptance: unit tests that a projected attention item can emit one toast
idempotently. LIVE toast = NOT_TESTED.
```

### P15-D — Command Center polish

```text
LANE: P15-D
ALLOWED: ui/command-center/src/routes, components, CSS/tokens
FORBIDDEN: new mock-data as if live, cinematic fake activity,
           command_center.py, contracts.ts

Polish clarity/latency/empty/error/STOP banner. Must remain
ledger-truth-driven via existing realClient/snapshotMapper.

Acceptance: existing UI unit tests + e2e still pass; no fake live claims.
```

### P12-X — Self-expansion seams (optional)

```text
LANE: P12-X
ALLOWED: src/jarvis_foundry/self_expansion.py
         src/jarvis_foundry/adapter_factory/**
         src/jarvis_foundry/fabric/gap_bridge.py
         src/jarvis_foundry/fabric/artifact_trial.py
         tests for those
FORBIDDEN: fabric/catalog.py, fabric/loop.py, live capability install

Make the documented path testable in isolation:
gap -> discover/reuse/build -> isolated trial -> verify -> (would register)
Do not register into the live catalog. Do not claim V16 proof.

Acceptance: focused test of the isolated trial+verify seam.
LIVE self-expansion = NOT_TESTED.
```

### REV-* — Independent review

```text
You did not author this branch. Read-only review.
Check: spine fork? lock violations? Gmail? live mutation? fake PASS?
Reuse vs rewrite? tests prove the claim? LIVE wrongly claimed?
Return: ACCEPT / ACCEPT_WITH_FIXES / REJECT plus exact file notes.
```

### CONV-1 — Convergence (later, one session)

```text
Workflow: docs/agent/workflows/convergence.md
Inputs: accepted P14-* and P15-* SHAs only.
Semantic integration. One authority per concern.
Grant yourself spine-file edits only for real verb registration.
No live activate. Freeze final SHA and omitted inputs.
```

---

## 7. What not to staff (token sink)

- Trust-window day/mission counter work
- Full A3/A4 / 48h / historical campaign reruns “because SHA changed”
- P3-D3 heartbeat unless live impact pulls it
- Gmail
- Duplicate MCP/tool-governance plane
- Docs remaster of CURRENT_STATE as a lane
- Security/compliance theater
- New agent frameworks as a second brain
- PowerPoint-for-its-own-sake (capability exists; pull it with a real owner task later)

---

## 8. How the owner runs a night of parallel work

1. Keep **this orchestrator chat** open. It owns live + locks.
2. Open 8–10 other chats (GPT / Grok / OpenCode / Cursor). Paste preamble + one lane packet each.
3. Each worker creates its own worktree and stays inside ALLOWED paths.
4. When a worker needs a spine file, they paste a LOCK REQUEST here. You (or this orchestrator) grant or deny.
5. When a worker reports VERDICT=PASS, start a REV-* session on that worktree with a **different** model.
6. After 2+ ACCEPTed lanes, run CONV-1. Do not merge five dirty branches blindly.
7. Only then schedule LIVE-14 with an explicit owner go.

If two workers collide on an ALLOWED file (example: both P15-B and P15-D edit the same route), stop the later one and give the file to the earlier lane. Orchestrator decides; workers do not negotiate in the repo.

---

## 9. Immediate live caution

Live is already on `4b2ae28` with one Supervisor. A disposable GOAL is in the ledger. Until Wave 0 is observed:

- do not promote again;
- do not disable Scheduler;
- do not submit more GOALs;
- do not merge P3-D3 to “save” the trust window.

The trust window will not PASS this week if we stay honest. That is correct. Product work continues anyway.

---

## 10. Success metrics from now on

Count these, not test volume:

- real owner objectives completed without prompt-shuffling;
- distinct surfaces used in one mission (browser / file / desktop / git);
- independent verifier PASS after a real FAIL/repair;
- STOP/RESUME during material work;
- honest WAIT vs fake activity;
- owner interruptions required (should fall).

---

## 11. Orchestrator standing orders

This chat remains Master Orchestrator until the owner names a replacement.

On every owner message, classify: lock request, worker report, live question, or redirect.  
Do not implement worker-lane code here while workers are in flight, unless a lock requires a spine patch.  
Do not let a worker session become the live operator.
