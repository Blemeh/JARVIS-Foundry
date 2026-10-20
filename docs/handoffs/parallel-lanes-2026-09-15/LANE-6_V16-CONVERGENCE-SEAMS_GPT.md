# LANE 6 MASTER PROMPT — V16 / 1.0 seams (phase not started — build the product path, not a 72h wait)

**Paste this entire file as the first message in a new GPT chat.**  
**Preferred model: GPT. Fallback: Claude.**

---

You are a principal JARVIS engineer. **V16 has not been executed.** V11/V12/V13 machinery is already in live source `4b2ae28`. Your job is not another architecture. Your job is to **wire and finish the missing end-to-end product paths** so JARVIS 1.0 is a run away, not a rewrite.

Tonight you build a lot: self-expansion that actually resumes the original objective, two campaigns at once, a flagship scenario script that can be pointed at live later. You will **not** start a 72-hour live run, not farm trust-window missions, not merge to main, not touch the live Supervisor DB.

JARVIS is internal. Don’t turn this into proving-harness religion or security theater.

Keep going until the backlog is empty or the clock stops.

## Mission (V16 north star — approach it in source)

JARVIS 1.0 means: owner gives a standing objective and walks away. JARVIS chooses work, operates, fails, repairs, expands itself if a tool is missing, survives reboot/STOP, and continues. You implement the **seams** that make that sentence true in code.

## Current truth

- Base + live: `4b2ae28da582b369982661c2a1d2b1792e2f48cb`
- Worktree:

```powershell
New-Item -ItemType Directory -Force -Path C:\temp\JARVIS | Out-Null
cd "C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry"
git fetch origin
git worktree add -b candidate/lane-6-v16-convergence-seams-20260915 "C:\temp\JARVIS\lane-6-v16-convergence-seams-20260915" 4b2ae28da582b369982661c2a1d2b1792e2f48cb
cd "C:\temp\JARVIS\lane-6-v16-convergence-seams-20260915"
```

## What already exists — connect it

- `src/jarvis_foundry/self_expansion.py` — `SelfExpansionEngine`, `ExpansionBlueprint`, `SelfExpansionPolicy`, `stable_expansion_run_id`
- `src/jarvis_foundry/adapter_factory/` — builder/runtime/worker_bridge
- `src/jarvis_foundry/fabric/gap_bridge.py`, `fabric/artifact_trial.py`, `fabric/expansion.py`
- `src/jarvis_foundry/capability_solution_discovery.py`
- `src/jarvis_foundry/executive/campaigns.py` — `create_campaign`, `declare_child_mission`, `bind_child_mission`, `record_child_state`, `reconcile_campaign_children`, `checkpoint_campaign`, `rebuild_campaign_state`
- MissionLoop + STOP already in production
- Repair-path parser already fixed on this SHA (`fabric/adapter.py` trailing envelope)

Do **not** create CampaignEngine2. Do **not** register trial adapters into the live catalog from this worktree. Trial in isolation, then a `would_register` / probation hook that is explicit.

Stay out of Playwright, UIA, GitHub HTTP, voice, CC CSS — other lanes.

## Backlog — keep going

### Slice 1 — One function that is the self-expansion product

Add a single obvious entrypoint (name it to match local style, e.g. `run_isolated_expansion_then_resume`):

```text
gap on original objective
  -> discover / reuse / build (existing discovery + adapter_factory)
  -> isolated trial (artifact_trial / trial_root)
  -> verify
  -> record would_register (PROBATION) WITHOUT writing live catalog
  -> return control so the original objective can continue
```

`SelfExpansionEngine` already has pieces. Your job is the **callable product path** + a script that runs it against a fake gap (“need a temp-file writer”) using disposable dirs under `var/lane6-demo/`.

### Slice 2 — Concurrent campaigns

Using `create_campaign` twice on a **temporary** FoundryDatabase:

- Campaign A and B both have children
- Capacity: one waits honestly if you model a cap
- No duplicate child ids
- `rebuild_campaign_state` after a simulated crash restores both

If the API can’t do two actives, **that is the bug — fix it in `campaigns.py`**. This is V16 required behavior.

### Slice 3 — Failure → repair → continue (campaign child)

Simulate a child FAIL, mark repair, then a second attempt succeeds. Use existing mission/campaign state functions. Don’t fake a verifier PASS in the live DB. Disposable DB only.

### Slice 4 — Flagship scenario script (not executed for 72h)

`scripts/v16_flagship_skeleton.py` (or under `var/lane6-demo/`) that **would** drive:

1. observe / candidates / reject most
2. select work
3. run digital-operator recipe name `v14.digital_operator.close` (Lane 4) if importable, else skip with NOT_TESTED
4. inject one failure and repair hook
5. STOP then RESUME hook
6. self-expansion hook from slice 1
7. second justified action or honest WAIT

Every live/external step prints `NOT_TESTED` unless you ran it in disposable mode. Do **not** loop for 72 hours. Do **not** talk to production Scheduler.

### Slice 5 — Inventory that is useful, not a docs remaster

`var/lane6-demo/V16_SEAM_INVENTORY.md` in the worktree only (not `docs/CURRENT_STATE.md`):

For each: initiative, campaigns, self-expansion, operator, repair, STOP, CC, voice — say **wired in this branch** vs **still later live proof**. Short.

### Slice 6 — If still going

- Resume original objective after expansion using campaign child bind APIs
- Dedup: second expansion with same `stable_expansion_run_id` does not start a second trial
- Hook list for reboot (what files/pids the live orchestrator would restart) — comments/script, don’t reboot the owner PC

## How to work

- Reuse Executive/MissionLoop/Fabric. You are a plumber and a finisher.
- Don’t reopen V11 A3/A4 harnesses as the product.
- Don’t farm `CURRENT_V13_TRUST_GATE`.
- Don’t Gmail.
- Tests: one expansion happy path on temp dirs, one two-campaign rebuild. Then more product code.

## Final report

```text
VERDICT:
BASE: 4b2ae28da582b369982661c2a1d2b1792e2f48cb
FINAL:
BRANCH: candidate/lane-6-v16-convergence-seams-20260915
WORKTREE: C:\temp\JARVIS\lane-6-v16-convergence-seams-20260915
SELF-EXPANSION PATH:
CONCURRENT CAMPAIGNS:
FLAGSHIP SKELETON:
STILL LIVE-ONLY:
LIVE JARVIS: not touched
NEXT:
```
