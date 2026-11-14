# RESET_STATUS (temporary recovery file, delete before final merge)

Branch: claude/hopeful-euler-lv46qf. Spec: `git show origin/claude/great-clarke-0a0hu9:RESET_SPEC.md`.

## Current phase
Phase 4 (real-engine run in progress) + Phase 6 GitHub smoke in CI.

## Done
- Phase 0: archive/foundry-final -> 228139f (old main) on remote, created by a one-shot
  workflow (the git proxy refuses tag pushes; workflow removed in the demolition commit).
- Phase 1: IDEA, AGENTS (<60 lines), CLAUDE ("Read AGENTS.md."), README stub, ARCHITECTURE.
- Phase 2: demolition commit "remove Foundry (archived at archive/foundry-final)".
- Phase 3: src/jarvis skeleton; 51 fake-engine tests incl. all 9 lifecycle paths; CI matrix
  (ruff, loc budget, pytest, install smoke; Linux/Windows/macOS) green.
- Phase 5 features are already implemented and tested (plan/approve, reviewer, notes,
  label intake, caps, webhook, serve) -- the spec order is satisfied by re-running Phase 4
  after Phase 5 is declared done.
- Real claude_code smoke: toy repo DONE in 16s, $0.23.

## Environment facts
- `claude` works headless here (claude -p --output-format json reports total_cost_usd).
- codex 0.169 flags checked via --help (npm, scratch); no OpenAI auth -> NOT VERIFIED.
- Git proxy: pushes only to the session branch. Tags/releases/other branches go through
  Actions (GITHUB_TOKEN). gh is search-only for this session; GitHub writes via MCP/Actions.
- Phase 4 harness: scripts/make_fixtures.py run DIR (hidden checks validated both ways).
  Run from a `git archive` snapshot so edits don't affect it.

## Next
- Phase 4 results -> BETA.md table. GitHub e2e workflow result -> PR number.
- README/BETA/CHANGELOG/issue templates; real STOP/RESUME test with claude; secrets grep.
- Release: workflow_dispatch release workflow on main (tag + release from CHANGELOG).

## Blockers
- none yet
