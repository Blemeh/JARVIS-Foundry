# Changelog

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
