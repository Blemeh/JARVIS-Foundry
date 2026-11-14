# JARVIS 0.1.0b1 beta

## Who this beta is for

Developers who want to hand small, well-described coding tasks (bug fixes, small
features, refactors, missing tests) to a coding agent and get back a pull
request they can review, with the repo's own tests already run. You need git,
Python 3.11+, and the Claude Code CLI (`claude`) logged in. For real pull
requests you also need `gh` logged in.

It is not for unattended work on repos you do not trust (the agent runs as your
user), and it is not a replacement for code review: JARVIS never merges.

## Known limitations

- Verification is only as good as the repo's own tests. A task whose effect no
  test checks can pass verification without being right; the reviewer pass
  lists risks but does not gate.
- One engine call per attempt; no plan-size heuristic beyond a word count.
- Notes proposals are de-duplicated by exact text only, so near-duplicates appear.
- A failed or stopped task is restarted from scratch, not resumed mid-way.
- In local mode without a remote, the task branch stays in your repo and the PR
  body is a file under `~/.jarvis/prs/`.
- The agent's Bash commands are not sandboxed (see README, Safety model).
- Windows: the code and test suite run on Windows in CI (and the installed CLI
  works there), but no real-engine run has been done on Windows yet.

## NOT VERIFIED

- **codex engine**: implemented against `codex exec` 0.169 flags (checked with
  `--help`), but never run with real credentials. No OpenAI credentials were
  available.
- **hermes engine**: ported from the Foundry adapter's subprocess call; Hermes
  was not installed or run.
- **Real GitHub issue intake** (`jarvis serve` polling issues labeled `jarvis`,
  PR linking, failure comments): covered by tests with a stubbed `gh`, not run
  against GitHub.
- **macOS real-engine runs**: CI runs the test suite and install smoke on macOS;
  no real-engine run was done there.

## Real-engine results (Phase 4)

10 tasks across 3 generated fixture repos (`scripts/make_fixtures.py`): a
Python/pytest project (uv), a Node project (`node --test`) and a Go project.
Engine `claude_code` (Claude Code 2.1.325, default model, reported as
`claude-opus-5-5`), local PR mode, reviewer pass on, run 2026-11-14 on Linux.

"Hidden check" is an acceptance test the agent never saw, applied to every DONE
branch afterwards (behavior tests, an AST check for the refactor, mutation
testing for the added tests). A DONE task whose hidden check fails would be a
false success. Task 9 is contradictory by design and must fail.

| # | Repo | Task | Outcome | Hidden check | Repairs | Duration | Cost |
|---|---|---|---|---|---|---|---|
| 1 | pyshop | bug fix: Fix apply_discount: percentage discounts are applied wrong | DONE | pass | 0 | 0m21s | $0.23 |
| 2 | pyshop | small feature: Add Cart.remove(sku, qty=None) to shop/cart.py | DONE | pass | 0 | 0m28s | $0.27 |
| 3 | pyshop | refactor: Refactor shop/inventory.py to remove duplicated SKU validation | DONE | pass | 0 | 0m16s | $0.21 |
| 4 | pyshop | add missing tests: Add tests for shop/money.py | DONE | pass (4/4 mutants killed) | 0 | 0m39s | $0.29 |
| 5 | nodecart | multi-file change: Rename calcTotal to computeTotal everywhere | DONE | pass | 0 | 0m20s | $0.25 |
| 6 | nodecart | bug fix: slugify leaves repeated and trailing dashes | DONE | pass | 0 | 0m24s | $0.26 |
| 7 | nodecart | small feature: Add a --json flag to bin/cli.js | DONE | pass | 0 | 0m38s | $0.33 |
| 8 | gotext | small feature: Add textutil.Truncate(s string, n int) string | DONE | pass | 0 | 0m22s | $0.23 |
| 9 | gotext | should fail cleanly: Report API version 2 | FAILED (expected: agent explained the contradiction, made no change) | - | 0 | 0m10s | $0.10 |
| 10 | gotext | bug fix: WordCount counts punctuation as part of words | DONE | pass | 0 | 0m41s | $0.30 |

Result: **9/10 succeeded with verified green tests, 0 false successes**, the
designed failure failed cleanly, and no fixture's `origin/main` moved.

## Re-run after Phase 5 (no regression)

The same 10 tasks on the final code (commit 538ebbb, all Phase 5 features on), run
2026-11-14 on Linux with `claude_code` (`claude-opus-5-5`), local PR mode:

| # | Repo | Task | Outcome | Hidden check | Repairs | Duration | Cost |
|---|---|---|---|---|---|---|---|
| 1 | pyshop | bug fix: Fix apply_discount: percentage discounts are applied wrong | DONE | pass | 0 | 0m18s | $0.21 |
| 2 | pyshop | small feature: Add Cart.remove(sku, qty=None) to shop/cart.py | DONE | pass | 0 | 0m30s | $0.26 |
| 3 | pyshop | refactor: Refactor shop/inventory.py to remove duplicated SKU validation | DONE | pass | 0 | 0m21s | $0.20 |
| 4 | pyshop | add missing tests: Add tests for shop/money.py | DONE | pass (4/4 mutants killed) | 0 | 0m41s | $0.30 |
| 5 | nodecart | multi-file change: Rename calcTotal to computeTotal everywhere | DONE | pass | 0 | 0m17s | $0.21 |
| 6 | nodecart | bug fix: slugify leaves repeated and trailing dashes | DONE | pass | 0 | 0m22s | $0.24 |
| 7 | nodecart | small feature: Add a --json flag to bin/cli.js | DONE | pass | 0 | 0m32s | $0.28 |
| 8 | gotext | small feature: Add textutil.Truncate(s string, n int) string | DONE | pass | 0 | 0m20s | $0.22 |
| 9 | gotext | should fail cleanly: Report API version 2 | FAILED (expected) | - | 0 | 0m12s | $0.10 |
| 10 | gotext | bug fix: WordCount counts punctuation as part of words | DONE | pass | 0 | 0m32s | $0.28 |

Result: **9/10 succeeded with verified green tests, 0 false successes**; task 9
failed cleanly again; no fixture's `origin/main` moved. Total cost $2.30.

## STOP and RESUME with the real engine

`python scripts/make_fixtures.py stop DIR --jarvis <installed jarvis>`, run
2026-11-14 on Linux with `claude_code`: an installed `jarvis serve` worked task 1
(`pyshop`, add tests) with task 2 queued. 8 seconds into the agent's work,
`jarvis stop` ran. Results: task 1 was `STOPPED` **0.81 s** after the command
started; the agent process and its whole process group were gone; the run was
recorded as `stopped`; `serve` stayed up and started nothing (task 2 still
`QUEUED` 3 s later). After `jarvis resume`, `serve` re-ran task 1 and then ran
task 2: both `DONE`. SIGTERM then shut `serve` down cleanly (exit 0).

## Plan-first with the real engine

`jarvis add --plan` on the Go fixture (add `Truncate`), 2026-11-14: the read-only
plan call produced a numbered plan ($0.14, 16 s) and left no branch and no
change behind; `jarvis approve 1` and `jarvis run` then produced a DONE task
(code $0.16, review $0.10).

## Real GitHub smoke test

`.github/workflows/github-e2e.yml`, run 43614354257 (attempt 2), 2026-11-14:
JARVIS, installed from this repo, took a task against a throwaway orphan branch
`jarvis-e2e-base` in this repository and opened **draft PR #3** from
`jarvis-e2e/1` into `jarvis-e2e-base`. The job checked the PR was a draft with
the expected base, head and body sections, and that `main` did not move. It then
closed the PR and deleted both branches (0 `jarvis-e2e*` branches left). The
coding engine in this test is the fake engine: it proves the GitHub path, not an
LLM.

## Owner actions

None are required for this beta.

- **Old Foundry install:** the reset audit (section 7, Phase 0) has a checklist
  for shutting down a live Foundry on Windows: a `JARVIS-Foundry-Supervisor`
  scheduled task, a foundry home under `%LOCALAPPDATA%\hermes\foundry\` and
  about 131 GB of releases under `%LOCALAPPDATA%\JARVIS\releases`. The owner has
  no such install, so it does not apply. If one ever turns up, the checklist is
  in `AUDIT_AND_RESET_PLAN.md` at tag `archive/branches/claude/great-clarke-0a0hu9`.
- **Archives:** the old `main` is at tag `archive/foundry-final`; the 39 old
  branches are at tags `archive/branches/<name>`.
- **Optional:** to verify the `codex` engine, provide an OpenAI API key and run
  `python scripts/make_fixtures.py run DIR --engine codex`.

## How to report bugs

Open an issue with the **Bug report** template. Include `jarvis --version`, your
OS, the engine, the output of `jarvis show <id>` (it redacts token-like strings,
but check before posting), and the run transcript path it prints. Use the
**Feedback** template for anything else. Do not paste API keys or tokens.
