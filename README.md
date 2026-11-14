# JARVIS

JARVIS turns a coding task for one of your git repos into a tested pull request.

You give it a task (`jarvis add`, or a GitHub issue labeled `jarvis`). It creates
an isolated git worktree and branch, runs a coding agent headlessly in it, and
then checks the work itself: it runs the repo's own test and lint commands,
confirms the diff is non-empty and confirms the diff stays inside the allowed
scope. It never trusts the agent's own claims. If verification fails it feeds
the failure output back to the agent (at most 2 repairs). If it passes, it
pushes the branch and opens a pull request with a summary, the changed files,
the verification commands and their output, the engine and model, and the cost
and duration.

JARVIS never merges, never pushes to a default branch and never deploys.
`jarvis stop` halts everything within seconds.

Status: private beta release candidate (`0.1.0rc2`). See [BETA.md](BETA.md) for what is verified and what is not.

## Requirements

- Python 3.11 or newer, with [uv](https://docs.astral.sh/uv/) (or pipx).
- git, with an identity configured:
  `git config --global user.name "Your Name"` and
  `git config --global user.email "you@example.com"`.
- One coding-engine CLI, installed and logged in:
  - **Claude Code** (default): `claude` must work in your terminal.
  - Codex CLI (`codex`) or Hermes Agent (`hermes`) are supported but not yet verified.
- For GitHub mode only: the [GitHub CLI](https://cli.github.com/) `gh`, logged in
  (`gh auth login`) with push access to the repo.

## Install

```bash
uv tool install git+https://github.com/Blemeh/JARVIS-Foundry@v0.1.0rc2
# or
pipx install git+https://github.com/Blemeh/JARVIS-Foundry@v0.1.0rc2
```

If `jarvis` is not found afterwards, run `uv tool update-shell` and open a new
terminal (or add `~/.local/bin` to your `PATH`).

## 5-minute quickstart

This uses a tiny demo repo with a failing test, in local mode (no GitHub
needed). Commands are for a POSIX shell (Linux, macOS, Git Bash on Windows).

```bash
uv tool install git+https://github.com/Blemeh/JARVIS-Foundry@v0.1.0rc2
export PATH="$HOME/.local/bin:$PATH"   # only needed if `jarvis` is not found
jarvis init

mkdir demo && cd demo
git init -b main
printf 'def add(a, b):\n    return a - b\n' > calc.py
printf 'import unittest\n\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n' > test_calc.py
git add . && git commit -m "demo"
cd ..

jarvis repo add demo
jarvis add --repo demo "Fix add() so the tests pass"
jarvis run
jarvis ls
```

`jarvis run` works the queue in the foreground. When it finishes, `jarvis ls`
shows the task as `DONE` with the path of the pull-request body (in local mode
the PR body is written to `~/.jarvis/prs/<id>.md` and the branch `jarvis/<id>`
stays in your repo). `jarvis show 1` prints the runs, costs and the log.

For a real repo on GitHub:

```bash
jarvis repo add ~/code/api --github you/api
jarvis add --repo api "Add rate limiting to POST /login"
jarvis run          # or: jarvis serve  (also picks up issues labeled "jarvis")
```

## Commands

| Command | What it does |
|---|---|
| `jarvis init` | Create `~/.jarvis` (or `$JARVIS_HOME`) with `config.toml` and the queue database |
| `jarvis repo add PATH [--name N] [--github owner/repo] [--base B] [--test CMD] [--lint CMD] [--setup CMD] [--build CMD] [--scope a,b] [--draft] [--branch-prefix P] [--label L] [--require-tests]` | Register a repo; prints the commands detected on its base branch |
| `jarvis repo ls` | List registered repos |
| `jarvis add --repo N "task" [--plan] [--scope a,b] [--body-file F]` | Queue a task (first line is the title) |
| `jarvis ls [--status S]` | List tasks with status, cost and PR link or failure reason, plus the merge rate of JARVIS's GitHub PRs |
| `jarvis show ID` | Task details, plan, runs (engine, model, cost, transcript path) and log |
| `jarvis run` | Process queued tasks in the foreground until the queue is empty |
| `jarvis serve [--concurrency N] [--no-intake] [--once]` | Background loop: polls GitHub issues labeled `jarvis` and the outcome of JARVIS's PRs, runs the queue. `--once`: one cycle, then exit |
| `jarvis stop [reason]` | STOP: kill running agents, halt the queue |
| `jarvis resume` | Clear STOP and re-queue stopped tasks |
| `jarvis approve ID [--reject] [--note TEXT]` | Approve (or reject) the plan of a large task; `--note` answers the plan's questions |
| `jarvis cancel ID` | Cancel a queued, waiting or stopped task |
| `jarvis retry ID` | Queue a failed, rejected, cancelled or stopped task again (its old branch is kept) |
| `jarvis notes REPO [--accept ID] [--reject ID]` | Show the repo's notes and proposed lessons |

Task states: `QUEUED`, `PLANNING`, `AWAITING_APPROVAL`, `RUNNING`, `DONE`,
`FAILED` (branch kept for inspection), `REJECTED` (edited outside its scope),
`STOPPED`.

## Config reference

`jarvis init` writes `~/.jarvis/config.toml`. Set `JARVIS_HOME` to use another
folder. Unknown keys are errors, so typos do not pass silently.

| Key | Default | Meaning |
|---|---|---|
| `[engine] name` | `"claude_code"` | `claude_code`, `codex`, `hermes` or `fake` |
| `[engine] model` | `""` | Model passed to the engine; empty uses the engine's default, which decides most of the cost per task (for example `"sonnet"` costs less than the largest models) |
| `[engine] command` | `""` | Engine executable; empty uses `claude` / `codex` / `hermes` |
| `[engine] args` | `[]` | Extra arguments appended to the engine command |
| `[engine] timeout_s` | `1800` | Timeout per agent call |
| `[engine] script` | `""` | Fake engine only: JSON script path |
| `[limits] max_repairs` | `2` | Repair attempts after a failed verification |
| `[limits] task_timeout_s` | `3600` | Wall-clock cap per task |
| `[limits] max_cost_usd` | `5.0` | Cost cap per task (`0` disables). Only engines that report cost can be capped; others show `NOT REPORTED` |
| `[limits] verify_timeout_s` | `900` | Timeout per verification command |
| `[limits] plan_over_words` | `150` | Tasks longer than this need an approved plan first (`0`: only with `--plan`) |
| `[limits] baseline` | `true` | Run the verification commands on the base branch before the agent; the agent sees what already fails and the PR shows before -> after |
| `[review] enabled` | `true` | A second, read-only agent call lists risks in the PR body (informs, never gates) |
| `[serve] concurrency` | `1` | Tasks run in parallel by `jarvis serve` |
| `[serve] poll_interval_s` | `120` | GitHub issue polling interval |
| `[webhook] url` | `""` | POSTs `{"event": "pr_ready" \| "failed" \| "plan_ready", "task_id", "repo", "title", "status", "pr_url", "reason"}` |

Per repo (`[repos.<name>]`, written by `jarvis repo add`, editable by hand):

| Key | Default | Meaning |
|---|---|---|
| `path` | required | The repo's local checkout |
| `github` | `""` | `owner/name`: push and open real PRs with `gh`. Empty: local mode |
| `base` | default branch | Branch the task branches start from and PRs target |
| `remote` | `"origin"` | Remote to fetch from and push task branches to |
| `setup`, `test`, `lint`, `build` | detected | Commands; `""` disables one. Detected from `package.json`, `pyproject.toml`/`uv.lock`, `requirements*.txt`, `setup.cfg`, `go.mod`, `Cargo.toml`, `Makefile` (see "Python projects" below) |
| `scope` | `[]` | Allowed paths (prefixes or globs; `*` also crosses folders). Empty: whole repo |
| `protected` | `[]` | Paths the agent may never touch, e.g. `[".github/"]` |
| `branch_prefix` | `"jarvis/"` | Task branches are `<prefix><task id>` |
| `draft` | `false` | Open PRs as drafts |
| `label` | `"jarvis"` | Issue label picked up by `jarvis serve` |
| `require_tests` | `false` | A change that adds or changes no test file fails verification (and is repaired) |

### Python projects

Each task gets a fresh worktree, so it also gets its own environment. With a
`uv.lock`, JARVIS uses `uv run`. Otherwise it creates `.venv` inside the task
worktree and installs what the project declares: the project itself in editable
mode with its `test`/`tests`/`testing`/`dev` extras, PEP 735 dependency groups
of those names, the usual `requirements*.txt` files, and pytest if the project
uses it. Tests then run with that interpreter, so they test the worktree's code.
This needs network access to your package index, and uses `uv` if it is
installed (`python -m venv` and pip otherwise). `jarvis repo add` prints the
exact commands; set `setup`/`test` yourself if your project needs something else.

### Repo notes and learning from reviews

Notes live in `~/.jarvis/notes/<repo>.md` (commands, conventions, lessons) and
go into every prompt for that repo. JARVIS never writes notes into your repo.
New lessons are only ever *proposed*; `jarvis notes <repo>` lists them for you to
accept or reject. They come from two places:

- lines the agent marks `NOTE:` at the end of a task;
- your reviews: while `jarvis serve` runs, it checks the outcome of every PR
  JARVIS opened. When you merge or close one that has review comments, a
  read-only agent call turns your comments into proposed lessons, so the next
  task on that repo avoids the same mistake.

`jarvis ls` shows `DONE/merged` and `DONE/closed`, and the merge rate of
JARVIS's GitHub PRs: the number that says whether JARVIS is useful.

## Safety model

- **Isolation.** Every task runs in its own git worktree on its own branch
  (`jarvis/<id>`), under `~/.jarvis/worktrees`. Your checkout is never touched.
- **Never merges, never touches default branches.** JARVIS only pushes the task
  branch, refuses to push to the base or default branch, never force-pushes,
  and only opens pull requests.
- **Independent verification.** Before the agent starts, JARVIS runs the
  repo's commands on the base branch, so the PR shows what failed before and
  passes after. Then it computes the diff itself (including
  untracked files), rejects empty diffs, rejects edits outside `scope` or
  inside `protected` (state `REJECTED`, no repair), and runs the repo's own
  commands. No configured or detected command means the task cannot pass.
- **Warnings you will see in the PR.** If test files lost tests or assertions,
  or if your own checkout changed while the agent ran (a sign it wrote outside
  its worktree), the PR body says so in a "Warning" section. JARVIS never
  reverts or deletes anything in your checkout.
- **Preflight and pausing.** `jarvis run` and `jarvis serve` check the engine
  CLI, the repos and (GitHub mode) the `gh` login before starting; if something
  is wrong, nothing starts and queued tasks stay queued. Two tasks in a row
  failing on an engine error (login, quota, outage) pause the queue.
- **Caps.** Time per agent call, time per task, cost per task and the number of
  repairs are all capped.
- **Read-only planning and review.** Plan and review calls run the agent with
  read-only tools, and JARVIS discards any change they make anyway.
- **Secrets.** Output that looks like a token or key is redacted before it is
  written to logs, transcripts or PR bodies.
- **No inherited MCP servers.** With Claude Code, JARVIS runs the agent with
  `--strict-mcp-config`, so it does not get the MCP servers from your own Claude
  setup (which could act outside the repo). Opt in with
  `[engine] args = ["--mcp-config", "/path/to/mcp.json"]`.
- **What the agent can do.** The agent runs as your user. With Claude Code,
  file edits are confined to the worktree and Bash is pre-approved so it can
  run tests; Bash commands themselves are not sandboxed. Run JARVIS in a
  container or VM if you do not trust the repo or the task.

## STOP and RESUME

`jarvis stop` writes a STOP file in `JARVIS_HOME`. Every running JARVIS process
checks it several times a second: it kills the agent's whole process tree,
marks the task `STOPPED`, removes its worktree and starts nothing new. The
command waits until no task is running and reports the result. No LLM and no
network are involved.

`jarvis resume` removes the STOP file and re-queues the stopped tasks. A running
`jarvis serve` continues on its own; otherwise run `jarvis run`.

Ctrl+C on `jarvis run` or `jarvis serve` is a graceful shutdown: the agent is
killed and its task goes back to `QUEUED`. If a JARVIS process crashes, the next
`jarvis run` or `jarvis serve` kills the orphaned agent and re-queues its task.

`jarvis serve` refuses to run from a source checkout (use an installed copy),
so switching branches in a development checkout never changes a running JARVIS.

## Development

```bash
uv run pytest
uv run ruff check
uv run python scripts/loc_budget.py      # src/jarvis must stay <= 5,000 lines
uv run python scripts/make_fixtures.py run /tmp/jarvis-fixtures   # real-engine suite
```

See [AGENTS.md](AGENTS.md) for the working rules and
[ARCHITECTURE.md](ARCHITECTURE.md) for the design.
