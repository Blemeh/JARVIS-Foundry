# Changelog

## 0.1.0rc2 (private beta release candidate)

- Claude Code runs no longer inherit your MCP servers (`--strict-mcp-config`);
  opt in through `[engine] args`. Safer for unattended work, and a smaller prompt.
- Plan, review and lessons calls load only the read-only tools, which cut the
  context of a review call from about 37k to about 7k tokens in a measured run.

## 0.1.0rc1 (private beta release candidate)

- Python repos without `uv.lock` now work: each task gets a `.venv` in its
  worktree with the project's declared dependencies (extras, dependency groups,
  requirements files) and runs tests with it. Before, verification used the
  system Python and failed on almost every real project.
- `jarvis run`/`serve` check the engine CLI, repos and `gh` login first and
  start nothing if something is wrong; queued tasks stay queued.
- Two tasks in a row failing on an engine error pause the queue.
- PR warnings when tests or assertions disappear, or when your own checkout
  changed during the agent run.
- `jarvis cancel` and `jarvis retry`.
- `jarvis serve` survives a failing poll step.
- `jarvis approve ID --note "..."` answers a plan's questions; the note goes into
  the plan the agent follows.
- `jarvis run` now reports the tasks it actually processed.
- Detection fixes: requirements-only projects are Python; `go.mod` and
  `Cargo.toml` win over a stray `tests/` folder; ruff is only used when it can run.

## 0.1.0b2 (beta)

- Baseline: before the agent works, JARVIS runs the verification commands on
  the base branch. The agent sees what already fails, and the PR body shows
  before -> after for every command (`[limits] baseline`, on by default).
- The PR body says which test files changed. Repos can set `require_tests`
  (`jarvis repo add --require-tests`) so a change without a test fails
  verification and is repaired.
- Learning from your reviews: `jarvis serve` tracks whether JARVIS's PRs are
  merged or closed, and turns review comments into proposed repo notes.
  `jarvis ls` shows `DONE/merged`, `DONE/closed` and the merge rate.
- `jarvis serve --once`: poll issues and PR outcomes once, work the queue, exit.
- Notes proposals skip near-duplicates and lessons you already rejected.
- `jarvis repo add` detects commands from the base branch rather than the
  current checkout, and takes `--label` and `--require-tests`.
- Command output in PR bodies is capped at 40 lines.
- Real GitHub issue intake is now covered by the GitHub smoke test.

## 0.1.0b1 (beta)

First release of the rebuilt JARVIS: a small command-line tool that turns a
coding task into a tested pull request. It replaces the "JARVIS Foundry"
autonomy kernel, which is archived at the `archive/foundry-final` tag.

- `jarvis init`, `repo add/ls`, `add`, `ls`, `show`, `run`, `serve`, `stop`,
  `resume`, `approve`, `notes`.
- One git worktree and branch per task; never merges, never pushes to a default
  or base branch, never force-pushes.
- Independent verification: non-empty diff, scope and protected paths, the
  repo's own setup/lint/build/test commands (detected for Node, Python, Go,
  Rust and Make; overridable).
- Up to 2 repairs with the failure output fed back; FAILED keeps the branch.
- Pull request body: summary, changed files, verification commands and output,
  reviewer notes, engine and model, repairs, cost and duration.
- Engines: `claude_code` (default, verified), `codex` and `hermes` (not yet
  verified), `fake` (tests). Engine and model come from config.
- Plan-first for large tasks with `jarvis approve`; read-only reviewer pass;
  per-repo notes with accept/reject of proposed lessons; GitHub label intake;
  per-task time and cost caps (`NOT REPORTED` when an engine reports no cost);
  generic webhook (`pr_ready`, `failed`, `plan_ready`).
- STOP kills the agent's process tree within seconds; RESUME re-queues;
  crashed tasks and orphaned agents are recovered on the next start.
- `jarvis serve` with configurable concurrency and graceful shutdown; refuses to
  run from a source checkout.
- Zero runtime dependencies; Python 3.11+; Linux, macOS and Windows.
