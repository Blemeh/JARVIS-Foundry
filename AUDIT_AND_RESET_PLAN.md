# JARVIS Foundry: Audit and Reset Plan

Date: 2026-11-14. Scope: the whole repository at `main` (`228139f`), plus the
live-system facts in your own `docs/STATUS.md` and `docs/CURRENT_STATE.md`
(reconciled 2026-10-21). I did not touch your live machine. Live numbers below
come from those records. Repository numbers I measured myself.

---

## 1. Verdict

You set out to build a JARVIS for software development. What exists is a
219,000-line Python governance kernel for an "autonomous personal AI
organization". Its only code-writing component is a single subprocess call to
Hermes, pinned to one budget model with a $1.00 spending cap.

Across its documented life it produced a one-line unused-import removal (in one
of its own proof scripts), one GitHub issue and a few lint fixes. In the last
observed week it started zero missions while logging about 70 idle cycles a day.

You can't trim this codebase back into shape. 351 of 414 modules (203k of 219k
lines) are reachable from the entry points through imports. Everything is wired
to everything, so there's no small dead branch to cut off. Some ideas, and
roughly 1–2k lines of logic, are worth porting. The rest should be archived
behind a git tag and deleted.

The recovery plan: freeze Foundry, redefine the product on one page, and build a
3–5k-line tool that takes a coding task and returns a tested pull request. Add
autonomy only once that works.

---

## 2. The numbers

| Item | Value |
|---|---|
| Python source | 219,179 lines, 414 modules, 45+ subsystems |
| Python tests | 139,591 lines, ~3,850 test functions. Your 10-21 baseline: 27 files fail and 1 times out. On Linux, 2 files fail to import. |
| Command Center UI | ~39,000 lines of TS/TSX/CSS |
| Scripts | 96 files, 44,000 lines, almost all one-off proof drivers (`v8_canary.py`, `v11_a3_flagship.py`, `v13_*_proof.py`, …) |
| Docs | ~270 markdown files in `docs/` plus 12 at the root. `CURRENT_STATE.md` alone is 1,331 lines. |
| Evidence in git | 1,232 of 2,527 tracked files are evidence, demo or convergence artifacts (~50 MB) |
| Ledger event types | 400+ distinct event names |
| CI | Hard-gates the UI only. The 219k-line Python backend runs with `continue-on-error`. |
| Live ledger | 633 MB and 475k events, mostly idle-cycle snapshots |
| Live output, last 7 observed days | 0 missions started or completed |
| Live disk | 133 release folders, about 131 GB |
| Coding engine | `hermes_cli.main -z <prompt> --model muse-spark-1.3-contributor --yolo`: one pinned model on a contributor (trains-on-your-data) tier, $1.00 cap, all fallbacks disabled |
| How you give JARVIS a task | No CLI command exists. Tasks enter only as signed directives through the Command Center, then pass through delegation → objective → Executive → portfolio → mission → contract → worker. |

---

## 3. What went wrong

### 3.1 The goal changed before the first line of code

What you wanted: a JARVIS for software development.

What `IDEA.md` says: *"a persistent autonomous personal AI organization … an
always-active digital company, research laboratory, builder, operator, and
self-improving second brain."*

The North Star then says *"Completing one task is not a normal stopping
condition."* The Autonomy Mandate authorizes it to acquire services, communicate
on your behalf, and build and promote new versions of itself.

That describes general autonomous agency, not a developer tool. Every later
subsystem follows faithfully from that text: Gmail, Albanian voice, Windows UI
automation, office documents, screen perception, a world model, self-upgrading
releases. The drift didn't start at V9. It was written into `IDEA.md` from the
start.

### 3.2 You handed product ownership to AI agents

The repo was built by pasting mega-prompts into Codex, GPT, Grok, OpenCode and
Fable, running in parallel "lanes".

- `docs/planning/POST_V7_CODEX_SOL_REMASTER_GOAL.md` makes the agent *"principal
  engineer, product owner, systems architect, security engineer, … evidence
  custodian."*
- The frozen roadmap is *"authored by Fable 5 acting as principal architect /
  product strategist."*

Agents optimize for whatever acceptance criteria they're given. Yours were: no
fabrication, independent verification, fail-closed behavior, a single
authority, durable evidence, and a STOP that always works. So they built proof
machinery, authority layers and evidence files, and built them well. None of
the criteria said "the owner's real repo got a useful merged change this week."

### 3.3 "Reuse-first" plus a "frozen spine" meant permanent accretion

`AGENTS.md` tells every agent to reuse and extend existing JARVIS code first, and
forbids restructuring the "authority spine". Applied to your own code, that
means nothing ever gets replaced and each version adds a layer on top.

The docstrings show the agents arguing against the obvious objection: "This is
not a second Executive", "deliberately subordinate", "not another authority",
"Thin workforce binding" (in a 3,761-line file).

The result is that the same job is implemented many times:

| Job | Implementations |
|---|---|
| Decide what to do next | `executive/` (decisions, continuous, autonomy, campaigns, intelligence, portfolio_bridge), `evolution/executive_portfolio`, `evolution/portfolio`, `chief_of_staff/`, `delegation/`, `initiative_a2/a3`, `resilience/`. About 56k lines. Live outcome: **WAIT every cycle.** |
| Run a unit of work | `roles/dispatcher`, `workers/orchestrator`, `workers/mission_bridge`, `workforce/gateway`, `fabric/adapter+executors+loop`, `execution/pipeline`, `operations/procedure`, `general_work/`, `v3_loop`/`v3_cycle` |
| Find work | `discovery_candidates`, `discovery_monitors`, `discovery_sources`, `project_discovery`, `capability_solution_discovery`, `workflow_discovery`, `evolution/discover`+`detector`, `world_model/` |
| Remember things | `knowledge_*`, `vault_*`, `hybrid_memory/`, `obsidian/`, `owner_context/`, `software_intelligence/`, `research_*`, `consolidation`, `live_ingestion`, `worker_context` |
| Ship itself | `deployment/` (`rollback_controller.py` alone is 4,581 lines), `evolution/` (canary, shadow, promote, restore), `trust.py`, `proving.py`, `integrated_rollback.py`, `installer.py` |

There are also version-named leftovers from V3 through V14: `v3_*.py`,
`v4_schema.py`, `postv7/`, `initiative_a2.py`, `initiative_a3.py`, and others.

### 3.4 The value is inverted

All the code writing happens in `workers/adapters/hermes.py`:

```
python -m hermes_cli.main -z "<prompt>" --provider opencode-go \
  --model muse-spark-1.3-contributor --in <worktree> --yolo --cli
```

That call uses one pinned model (`AUTHORIZED_MODEL`, with every fallback
disabled), on a training-data tier, under a $1.00 cap for the whole run. All the
real software work happens inside it: understanding the task, editing files,
fixing tests. The other 219k lines are bookkeeping about that one call.

That's why the output is lint fixes. The part that writes code is starved, and
everything around it is huge.

### 3.5 Proof became the product

- Half the tracked files are evidence.
- 96 scripts are one-time proof drivers.
- `CAPABILITY_MATRIX.md` marks rows `LIVE_COMPLETE` with justifications like
  "no current objective warrants a plan". In other words, a row counts as
  complete because nothing exercises it.
- Many sessions ended in a docs-only commit pinning a SHA, just so
  `doc_staleness_check.py` would pass.

Weeks of agent time went into proving and re-proving the system instead of
using it.

### 3.6 The anti-drift rules locked the drift in

`AGENTS.md` contains rules like "frozen roadmap", "out-of-phase discoveries are
DEFERRED, never implemented", and "only the session that closes a gate may
update the execution-state file".

That execution-state file has been stale since 2026-09-03. `STATUS.md` says work
"stays in hygiene/verification" until someone picks a gate. The process built to
keep you on track is now what blocks you from getting back on track.

### 3.7 It caused real damage on your machine

All from your own `STATUS.md`:

- One blocked delegation was logged 3,301 times.
- About 4,100 toast popups fired in 12 hours.
- 83 test-leaked entries ended up in your user PATH.
- Tests wrote to your real Hermes profile.
- Old releases take 131 GB.
- The live worker read its config from the development working tree, so
  switching branches changed production.
- The Command Center takes about 20 seconds to build one snapshot.

That's the cost of an always-on system nobody can fully reason about.

### 3.8 GitHub was already most of what you rebuilt

For a software JARVIS, Git and GitHub already provide:

- the ledger (commits)
- the evidence store (PR diffs and CI logs)
- the independent verifier (CI)
- the owner approval queue (PR review)
- the task queue (issues)
- rollback (`git revert`)

Foundry rebuilt all six in SQLite, with 400+ event types and a 20-second
dashboard on top.

---

## 4. What was right (keep the ideas)

- Run every task in an isolated git worktree and branch. Never touch `main` directly.
- Don't trust the agent's own claim. Check the actual diff and run the tests
  yourself (`workers/evidence.py`).
- Refuse the commit if the agent touched files outside the task's scope. The
  "rogue.py" refusal in `mission/commit.py` is a good example.
- Have a STOP switch that works without the LLM (`supervisor/estop.py`, 75 lines).
- Cap spend and time per run.
- Report honest states (FAILED, BLOCKED, UNKNOWN) instead of faking success.
- Use machine-level hygiene tools in the style of `scripts/doctor.py` and `scripts/verify.py`.
- The Command Center visual design (`DESIGN.md`) shows good taste. It just came
  too early.

In a sane design, all of that is about 1–2k lines.

---

## 5. Target product

**JARVIS takes a software task, from me or from my issue tracker, and works it
in an isolated branch using the best coding agent available. It checks the work
with the repo's own tests and hands me a pull request with evidence. It
remembers my repos and conventions. It never merges or deploys without me.**

These user stories are the acceptance tests for the reset:

1. I run `jarvis add --repo api "Add rate limiting to POST /login"`. Within 30
   minutes a PR exists, the tests are green, and the PR body explains the
   change and shows the test output.
2. I label a GitHub issue `jarvis`. A linked PR appears.
3. A task fails (tests won't pass after retries). Its status becomes FAILED with
   the reason, the branch is kept for inspection, and I get notified.
4. `jarvis ls` shows queued, running and done tasks, with PR links and cost.
5. `jarvis stop` halts all work within seconds.
6. As I merge or reject PRs, JARVIS updates that repo's notes so the next
   attempt goes better.

**Not in scope until Phase 4 is done:** email, calendar, voice, Albanian,
Windows UI automation, office documents, screen perception, a world model,
self-upgrading releases, inventing its own goals, multi-day campaigns, trust
levels, and any kind of "Executive".

**The one metric:** merged JARVIS PRs per week, and the percentage of JARVIS PRs
that get merged. Not test counts, not gates, not feature counts.

---

## 6. Keep / Port / Delete

### 6.1 Port (rewrite small, using the old file as a reference)

| New module | Port from | Old size | Target size |
|---|---|---|---|
| `store.py`: SQLite tables for repos, tasks, runs and log | `database.py`, `events.py` (drop `migrations.py` and schema v41) | 831 + 1,671 | ~250 |
| `stop.py` | `supervisor/estop.py` | 75 | ~40 |
| `worktree.py` | `workforce/worktrees.py` | 1,623 | ~150 |
| `agents/hermes.py`, only if you keep Hermes as one engine | `workers/adapters/hermes.py`, without the spend gateway, model pinning or profile rewriting | 704 | ~150 |
| `verify.py`: scope check plus test run | `workers/evidence.py`, plus the changed-paths and scope refusal in `mission/commit.py` | 531 + 627 | ~250 |
| `repo_profile.py`: detect test, lint and build commands | `project_context/inspector.py` | 1,600 | ~150 |
| `github.py`: push, open PR, read labeled issues | Replace `connectors/github_write.py` with the `gh` CLI | 1,953 | ~200 |
| `scripts/doctor.py` | Keep the idea and rewrite it for the new layout | — | ~150 |

### 6.2 Delete (do not port)

| Area | Paths | Lines | Why |
|---|---|---|---|
| Decision layers | `executive/`, `evolution/`, `delegation/`, `chief_of_staff/`, `owner_context/`, `resilience/`, `initiative_a2.py`, `initiative_a3.py`, `autonomy_projection.py`, `operational_intelligence.py`, `self_expansion.py`, `capability_requirements.py`, `capability_solution_discovery.py`, `decision_queue.py`, `briefing*.py` | ~56k | A task queue plus you replaces all of it |
| Execution stacks | `roles/`, `workforce/`, `fabric/`, `mission/`, `workers/` (except the ported pieces), `execution/`, `operations/`, `general_work/`, `runtime/`, `v3_*.py`, `control/`, `procedure_library/`, `adapter_factory/`, `mcp_backend/` | ~80k | One runner replaces six. Coding agents bring their own tools and MCP support. |
| Non-software scope | `connectors/` (keep only the git/GitHub ideas), `perception/`, `procedural_learning/`, `workflow_discovery/`, `voice*.py`, `windows_toast.py`, `postv7/`, `delivery/`, `convergence/` | ~29k | Outside the product |
| Discovery, world model, research | `world_model/`, `discovery_*.py`, `project_discovery.py`, `research_*.py` | ~12k | Phase 4 "suggest tasks" is ~300 lines over issues, CI results and TODOs |
| Memory stacks | `hybrid_memory/`, `obsidian/`, `knowledge*.py`, `vault_*.py`, `consolidation.py`, `live_ingestion.py`, `worker_context.py`, `software_intelligence/`, `project_context/` (after porting) | ~8k | A notes file per repo plus task history is enough |
| Self-deployment, trust, proving | `deployment/`, `trust.py`, `trust_states.py`, `proving.py`, `integrated_rollback.py`, `installer.py`, `acceptance_harness/`, `gates/`, `migrations.py`, `supervisor/` (except the STOP logic) | ~18k | Install with `uv tool install`. Roll back with `git revert`. |
| Projections and Command Center backend | `command_center*.py`, `projections.py`, `result_traceability.py`, `owner_controls.py`, `owner_directives.py`, `owner_ui.py`, `reconcile.py`, `reporting/`, `correlation/`, `systems.py`, `domain.py`, `v4_schema.py` | ~10k | The CLI plus GitHub is the UI for now |
| UI | `ui/command-center/` | ~39k | Rebuild in 1–2k lines in Phase 4 if you still want it. Keep `DESIGN.md`. |
| Tests | all of `tests/` | ~140k | They test code that's being deleted. Start a new suite. |
| Scripts | everything except `doctor.py` (rewrite it) | ~44k | One-off proof drivers |
| Evidence and state | `evidence/`, `var/`, `docs/convergence/`, `docs/evidence/`, `ui/command-center/evidence/`, `.impeccable/review/` | ~50 MB | The PR and CI are the evidence |
| Docs | `docs/handoffs/`, `docs/planning/`, `docs/roadmap/`, `docs/superpowers/`, `docs/v3/`, `docs/runtime/`, `docs/architecture/`, `docs/governance/`, `docs/agent/`, `docs/*.md`, `PROJECT_STATE.md`, `RECOVERY_BASELINE.md`, `LIVE_ACTIVATION_*.md`, `LANE_CHECKPOINT.md`, `HERMES.md`, `PRODUCT.md`, `CLAUDE.md` (rewrite it as a 3-line pointer) | ~280 files | Replaced by `IDEA.md`, `ARCHITECTURE.md` and `AGENTS.md`, each one page or less |
| Config and packaging | `config/*`, `packaging/*.vbs`, `packaging/*.cmd`, `schemas/*` | — | Old routing and policy YAML, Windows task launchers, schemas for deleted contracts |
| Dependencies | `mcp`, `Pillow`, `openpyxl`, `pypdf`, `python-docx`, `python-pptx`, `reportlab`, `uiautomation`, `playwright`, `trafilatura`, `edge-tts`, `winotify` | — | Not needed for the product |

Nothing is lost. All of it stays in git history behind the archive tag.

---

## 7. Phases

Durations are rough estimates for one person working with an AI coding agent.

### Phase 0: Shut it down and archive (1 day)

On your Windows machine:

1. Stop the live system:
   ```
   schtasks /End /TN "JARVIS-Foundry-Supervisor"
   schtasks /Change /TN "JARVIS-Foundry-Supervisor" /DISABLE
   ```
   Also disable `Hermes_Gateway`, unless you use Hermes messaging for
   something else. Check Task Manager and confirm no JARVIS
   `python`/`pythonw` processes are left.
2. Delete the 8 disabled leftover tasks listed in `docs/STATUS.md`, using
   `schtasks /Delete /TN "<name>" /F`.
3. Zip the foundry home (`%LOCALAPPDATA%\hermes\foundry\`, which holds the ledger
   DB) to external storage. That's your full history. You probably won't open
   it again.
4. Once the zip exists, delete `%LOCALAPPDATA%\JARVIS\releases` (~131 GB).
   Remove `%LOCALAPPDATA%\JARVIS\bin` from your user PATH.
5. Tag and push the archive:
   ```
   git tag archive/foundry-final main
   git push origin archive/foundry-final
   ```
   The 96 `archive/*` lane tags aren't on GitHub. Push them with
   `git push origin --tags` if you want them, or accept that they're gone.
6. Clean up worktrees. Run `git worktree list`, then `git worktree remove <path>`
   for each old one (the `C:\temp\...` folders), then `git worktree prune`.
7. Make a decision about the contributor tier. Don't point a tier that trains on
   your data at private repos unless you're fine with that.

**Exit:** nothing JARVIS-related is running, the disk space is back, and the
archive tag is on GitHub.

### Phase 1: Redefine the product (1–2 days, done by you, not an agent)

1. Replace `IDEA.md` with Appendix A, edited until it's yours.
2. Replace `AGENTS.md` with Appendix B. Reduce `CLAUDE.md` to "Read AGENTS.md."
3. Choose the coding engine. Spend half a day running 2–3 candidates on one real
   task in one of your repos: Claude Code headless (`claude -p`), Codex CLI
   (`codex exec`), OpenHands headless, Hermes Agent, Aider. Choose by result
   quality, not price. Also try a GitHub-native option (Claude Code GitHub
   Action, Copilot coding agent, OpenHands resolver). If one of them already
   gets you 80% of "issue → PR", Phase 2 becomes mostly configuration plus the
   parts it doesn't cover. That's reuse-first applied properly.
4. Pick 2–3 of your real repos and write down 10 tasks you actually want done.
   They're the Phase 2 exam.

**Exit:** a one-page `IDEA.md`, a chosen engine, and a written list of 10 tasks.

### Phase 2: Walking skeleton, task → PR (1–2 weeks)

On a `reset` branch, delete everything listed in section 6.2, then create:

```
src/jarvis/
  cli.py          jarvis add | ls | show | run | stop
  config.py       ~/.jarvis/config.toml (repos, engine, limits)
  store.py        SQLite: repos, tasks, runs, log
  worktree.py     one worktree + branch per task; cleanup
  agents/base.py  AgentRunner.run(prompt, cwd, timeout) -> AgentResult(transcript, cost, exit_code)
  agents/<engine>.py
  prompt.py       task + repo notes + rules -> prompt
  verify.py       run the repo's test/lint commands; scope check; verdict
  github.py       push branch; open PR via gh; PR body = summary + test output
  runner.py       claim task -> worktree -> agent -> verify -> one retry with failures -> PR -> DONE/FAILED
  stop.py         STOP sentinel, checked between steps and enforced on the agent subprocess
tests/            fake-agent unit tests + 1 real end-to-end test on a toy repo
```

Constraints for this phase:

- At most 3,000 lines of Python, not counting tests.
- It runs in the foreground with `jarvis run`.
- No daemon, scheduler or UI.

**Exit:** run the 10 tasks. At least 6 of them should produce a PR you'd merge
as-is or with small edits. If you fall short, fix the prompt, the engine or the
verify step. More architecture won't help.

### Phase 3: Make it good (2–4 weeks)

Add these in order, and only when Phase 2 results show you need them:

1. **Plan first for larger tasks.** The agent writes a plan, and you approve it
   (or it's auto-approved below a size threshold).
2. **Repair loop.** Feed failing test output back to the agent, with at most 2
   retries.
3. **Reviewer pass.** A second model reviews the diff against the task and flags
   risks in the PR body.
4. **Repo memory.** Each repo gets a `JARVIS.md` (or uses its existing
   `AGENTS.md`/`CLAUDE.md`) with commands, conventions and lessons. After each
   merged or rejected PR, JARVIS proposes additions and you approve them.
5. **GitHub issue intake by label.**
6. **Notifications** through Telegram, Discord or a webhook ("PR ready",
   "failed, needs you"). Hermes' gateway is fine for this.
7. **Per-task cost and time caps**, with the model chosen by task size.
8. **Background service** (`jarvis serve`), installed with `uv tool install`.
   It never runs from your working tree.

**Exit:** you use it most days, at least 70% of its PRs get merged, and the
core is at most 5,000 lines.

### Phase 4: Autonomy on top of a working core (ongoing)

This is where the original JARVIS feel comes back, now built on something that
demonstrably works:

- Scheduled jobs: check CI on `main` nightly and open a fix PR if it's red;
  dependency updates; flaky-test triage; a TODO/FIXME sweep.
- A proposals queue: JARVIS suggests tasks from issues, CI results and TODOs,
  and you approve them with one tap.
- Parallel tasks across repos.
- A small web dashboard (queue, runs, PRs, cost) in 1–2k lines, reusing
  `DESIGN.md`.
- Auto-merge only for categories you whitelist, for example patch-level
  dependency bumps with green CI.

### Phase 5: Optional, one at a time, only on real demand

Obsidian as a knowledge base, voice, deploy and ops tasks, non-code work. Each
one has to pass "I would use this every week", and its line budget is written
down before anyone builds it.

---

## 8. Rules so this doesn't happen again

1. **You own the product.** Agents implement tickets you write. No agent gets
   the role of "product owner" or "principal architect", and no more `/goal`
   mega-prompts.
2. **A ticket is at most one day of work.** It has an acceptance check you can
   see as a user, and it lists what it deletes.
3. **Replace, don't layer.** If a PR builds a new version of something, the old
   one is deleted in the same PR.
4. **Line budget:** 3k lines for Phase 2, 5k for Phase 3. Raising it requires a
   written decision.
5. **No evidence, proof, handoff or checkpoint files in git.** The PR and CI are
   the evidence.
6. **No parallel agent lanes** until Phase 3 is complete.
7. **CI gates the Python core,** not just the UI.
8. **The running JARVIS never reads from your development checkout.**
9. **Weekly check: how many JARVIS PRs were merged this week?** If the answer is
   zero for two weeks running, stop building features and find out why.
10. **Off-the-shelf first** for anything external: agents, `gh`, git, SQLite,
    schedulers. "Reuse-first" doesn't mean "reuse our own old code".

---

## Appendix A: Draft `IDEA.md`

```markdown
# JARVIS

JARVIS is my software-development assistant. I give it coding tasks, or it
picks them up from my issue tracker. It works each one in an isolated branch
with a strong coding agent, runs the repo's own tests, and opens a pull request
with a clear summary and the test results. It learns each repo's commands and
conventions from the PRs I merge and reject.

JARVIS never merges, deploys or touches main without me. STOP halts it
immediately.

Success = useful PRs I merge, every week.

Out of scope until this file says otherwise: email, calendar, voice, desktop
automation, office documents, self-upgrading, inventing its own goals.
```

## Appendix B: Draft `AGENTS.md`

```markdown
# Working on JARVIS

Product: see IDEA.md. If a change doesn't help JARVIS turn a task into a
mergeable PR, don't make it.

Rules
- Work on a branch. Keep PRs small (aim for under 400 changed lines).
- Replace, don't layer: if you write a new version of something, delete the old
  one in the same PR.
- Python core budget: 5,000 lines, not counting tests. Don't exceed it without
  owner sign-off.
- Don't add new top-level modules or packages without owner sign-off.
- Use existing tools (git, gh, the agent CLIs, SQLite) instead of building your
  own.
- Docs are README, IDEA, ARCHITECTURE and AGENTS only. No evidence, report,
  handoff or checkpoint files.
- Test behavior with a fake agent, plus one end-to-end test on a toy repo.
  Don't write tests for code paths nothing calls.
- Tests never touch the owner's real repos, credentials, PATH or home
  directory.

Commands
- uv run pytest
- uv run ruff check
- uv run jarvis --help

Done means: tests pass, the PR describes the change, and you ran the ticket's
acceptance check by hand.
```
