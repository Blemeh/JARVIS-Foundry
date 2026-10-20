# LANE 3 MASTER PROMPT — V14 GitHub + git effects (make writes actually usable)

**Paste this entire file as the first message in a new GPT chat.**  
**Preferred model: GPT. Fallback: OpenCode.**

---

You are a principal JARVIS engineer. Tonight you **finish the developer-effects path V12/V13 left as adapter-plus-fixtures**. JARVIS is the owner’s internal operator. This is not a public SaaS. Do not build a security/compliance program. Do not farm tests. Do **not** push to `JARVIS-Foundry` origin and do **not** open noisy PRs on random public repos. Make the write path **complete, idempotent, and ready** so a later live session can publish **one** authorized PR with a switch flip.

Keep secrets in env/existing key files, never in git. Do not touch live foundry DB. Isolated worktree.

## Mission

`GitHubWriteAdapter` exists. Default `dispatch_enabled=False`. Public WRITE is unproven. `git.push` is catalogued. Fixtures do not count as V14.

You will make:

1. Issue create + readback
2. PR create + publish + readback
3. `git.push` to a **local bare remote** (and the real GitHub transport ready)
4. Same payload twice → **one** external effect
5. Resume after STARTED does not double-fire

Gmail is owner-deferred. Do not touch `gmail_api.py`.

## Current truth

- Base + live: `4b2ae28da582b369982661c2a1d2b1792e2f48cb`
- Worktree:

```powershell
New-Item -ItemType Directory -Force -Path C:\temp\JARVIS | Out-Null
cd "C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry"
git fetch origin
git worktree add -b candidate/lane-3-v14-github-mcp-20260915 "C:\temp\JARVIS\lane-3-v14-github-mcp-20260915" 4b2ae28da582b369982661c2a1d2b1792e2f48cb
cd "C:\temp\JARVIS\lane-3-v14-github-mcp-20260915"
```

## What already exists

- `src/jarvis_foundry/connectors/github_write.py`
  - `GitHubWriteAdapter` — process-scoped pending/completed maps, `github_effect_key()`, `gh_api_write_transport`
  - `GitHubPullRequestPlan`, `GitHubPullRequestEffectDriver`
  - `dispatch_enabled` gate
- `src/jarvis_foundry/connectors/git.py` / `v3_git.py`
- `src/jarvis_foundry/connectors/github_research.py` — READ already real
- Fabric in `fabric/loop.py`: `github.issue.create`, `github.pull_request.create`, `github.pull_request.publish`, `git.push`, plus all `github.*.read`
- `effects.py` already classifies those as irreversible — **do not fork a second journal**. Use the existing effect keys.

Stay out of Playwright, Windows UIA, voice, CC UI, self-expansion.

## Product tonight

A driver script using a **fake or recorded GitHub HTTP** plus a **local git bare remote** that demonstrates the full lifecycle. Optionally, if `gh auth status` already works on this machine, a dry-run against the owner’s own throwaway repo **only if** you find an explicit owner-config list — otherwise stay local. Never use Foundry `origin`.

## Backlog — keep going

### Slice 1 — Turn the adapter into something missions can call

- Inspect how `fabric/loop.py` / `fabric/executors.py` dispatch `github.issue.create` and `github.pull_request.publish`. If they stop at `dispatch_enabled=False`, add a **simple owner config** (env or existing foundry yaml pattern) `JARVIS_GITHUB_DISPATCH=1` + optional `owner/repo` allow list. Default remains off so this worktree cannot nuke GitHub. The **code path** when on must be complete: transport → pending → readback MATCH → completed.
- Finish any `NOT_TESTED` readback branch by requiring `read_transport` in the enabled path.

### Slice 2 — Idempotency that is product, not a comment

- `github_effect_key()` already hashes payload. Enforce: second create with same key returns the first provider id, no second POST.
- STARTED then process crash then resume: readback first, then complete. No second PR.

### Slice 3 — PR publish is a first-class verb

- `github.pull_request.create` vs `publish` — inspect `GitHubPullRequestEffectDriver`. Make publish mean “the PR exists and is readable”, with MATCH readback.
- Plan object should carry head/base/title/body. Don’t silently rewrite owner text.

### Slice 4 — git.push

- Implement/finish push to a local bare repo created under `var/lane3-demo/remote.git`.
- Receipt: remote URL, sha, rejected vs accepted.
- Second push of the same sha is a no-op success, not a new effect.

### Slice 5 — MCP mutating call (optional extra)

- If `mcp_backend/` already has a mutating tool path, give **one** tool the same idempotency wrapper. If it is a swamp, skip and note it. Do not build a second MCP plane.

### Slice 6 — Compose contract for Lane 4

- Write `var/lane3-demo/VERBS.md` listing **exact** action_kind strings and payload fields Lane 4 should bind (`github.pull_request.publish`, `git.push`, …). Inspect catalog names; don’t invent `github.pr.send`.

## How to work

- This is internal. Don’t add OAuth device-flow, fine-grained permission matrices, or secret scanners as a lane.
- Don’t “fix” Gmail while you’re here.
- Don’t edit `effects.py` unless a field is actually missing for your receipt — prefer adapter-level completeness.
- A few tests around idempotency and local push: good. Do not clone the entire v12 proof campaign.

## Demo

`var/lane3-demo/demo.py` + `DEMO.md`. Show two identical publishes → one provider object.

## Final report

```text
VERDICT:
BASE: 4b2ae28da582b369982661c2a1d2b1792e2f48cb
FINAL:
BRANCH: candidate/lane-3-v14-github-mcp-20260915
WORKTREE: C:\temp\JARVIS\lane-3-v14-github-mcp-20260915
BUILT:
IDEMPOTENT PROOF:
LIVE GITHUB WRITE: no (unless owner throwaway explicitly used)
LIVE JARVIS: not touched
NEXT:
```
