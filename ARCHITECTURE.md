# Architecture

One process, one SQLite file, one worktree per task. Git and GitHub hold the
real history (branches, commits, pull requests); JARVIS only keeps a queue.

## Modules (`src/jarvis/`)

| Module | Job |
|---|---|
| `cli.py` | argparse commands: init, repo add/ls, add, ls, show, run, serve, stop, resume, approve, notes |
| `config.py` | `$JARVIS_HOME/config.toml` (default `~/.jarvis`): engine, limits, webhook, repos |
| `store.py` | SQLite tables `repos`, `tasks`, `runs`, `log`, `proposals`; numbered migrations |
| `worktree.py` | create and remove one worktree + branch per task |
| `agents/` | `AgentRunner` interface; `claude_code`, `codex`, `hermes`, `fake` |
| `proc.py` | run a subprocess with timeout and STOP polling; kill the whole process tree |
| `prompt.py` | task + repo notes + rules (+ plan, + previous failure) -> prompt |
| `repo_profile.py` | detect setup/test/lint/build commands from manifests |
| `verify.py` | changed files, scope check, run commands, verdict |
| `github.py` | push branch, open PR (gh or local file), read labeled issues, comment |
| `runner.py` | the task lifecycle and the queue loops (`run`, `serve`) |
| `notes.py` | per-repo notes file and proposed lessons |
| `stop.py` | the STOP sentinel file |
| `notify.py` | optional generic webhook |

## Task lifecycle

```
QUEUED ──claim──> RUNNING ──verify ok──> DONE (PR opened)
  │                 │  └─verify fail─> repair (max 2) ─> FAILED (branch kept)
  │                 ├─scope violation─> REJECTED (branch kept, no repair)
  │                 ├─agent timeout / cost cap / error─> FAILED
  │                 └─STOP─> STOPPED ──resume──> QUEUED
  └─large or --plan─> PLANNING ─> AWAITING_APPROVAL ──approve──> QUEUED
                                                  └─approve --reject─> FAILED
RUNNING left by a dead process ──next start──> QUEUED (worktree discarded)
```

## Decisions
- Repo settings live in `config.toml`; the `repos` table only holds runtime state (issue polling).
- Notes files live in `$JARVIS_HOME/notes/<repo>.md`, never in the target repo, so accepting a lesson never touches a default branch.
- The diff is always measured against the task's base commit and includes untracked files; tool caches are ignored.
- An out-of-scope edit is REJECTED at once, without repair: it is a safety breach, not a quality failure.
- An agent timeout fails the task without repair; a repair would most likely time out again.
- No verification command configured or detected means the task cannot pass.
- JARVIS commits the agent's work itself; failed attempts are committed to the task branch so they can be inspected.
- Local mode: the branch goes to the repo's `origin` remote if one exists (else stays local) and the PR body is written to `$JARVIS_HOME/prs/`.
- `serve` shutdown kills in-flight agent runs and re-queues their tasks; STOP marks them STOPPED instead.
- A crashed RUNNING task (owner process gone) has its orphaned agent killed (runs record the engine pid) and is re-queued from scratch on the next start.
- `serve` refuses to run from a source checkout unless `--allow-dev` is given.
- Cost is whatever the engine reports; otherwise it is recorded as NOT REPORTED and only the time cap applies.
- Plan and review calls run the agent read-only; JARVIS discards any change they make.
- If the agent commits on its own despite the rules, its commits are delivered like JARVIS's own (the diff is always measured from the base).
- Verification commands run with a private Python bytecode cache, so a same-size edit within one second can never run stale `.pyc` files.
- The webhook also sends `plan_ready`, because a plan waiting for approval needs the owner just like a failure.
- Scope globs use fnmatch: `*` also matches `/`, like gitignore patterns without a slash.

## Later (not in the beta)
- A small web dashboard (queue, runs, PRs, cost).
- Scheduled jobs: nightly red-CI fix, dependency bumps, flaky-test triage.
- A proposals queue: suggest tasks from issues, CI results and TODOs.
- Auto-merge for whitelisted categories with green CI.
- Choosing the model by task size.
- Learning notes from merged or rejected PRs automatically.
