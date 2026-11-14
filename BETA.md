# JARVIS private beta (0.1.0rc1)

## Who this beta is for

Developers who want to hand small, well-described coding tasks (bug fixes, small
features, refactors, missing tests) to a coding agent and get back a pull
request they can review, with the repo's own tests already run. You need git,
Python 3.11+, and the Claude Code CLI (`claude`) logged in. For real pull
requests you also need `gh` logged in.

It is not for unattended work on repos you do not trust (the agent runs as your
user), and it is not a replacement for code review: JARVIS never merges.

Supported in this beta: Linux and macOS; the `claude_code` engine; local and
GitHub modes; Python (pytest or unittest, dependencies from extras, dependency
groups or requirements files), Node (npm, pnpm, yarn) and Go repos.
Experimental: the `codex` and `hermes` engines, and Windows.

## Known limitations

- Verification is only as good as the repo's own tests. A task whose effect no
  test checks can pass verification without being right; the reviewer pass
  lists risks but does not gate.
- One engine call per attempt; no plan-size heuristic beyond a word count.
- The repair loop has not been exercised by the real engine yet: in every real
  run so far, Claude passed verification on its first attempt. It is covered by
  fake-engine tests only.
- Lessons from PR reviews are learned only while `jarvis serve` runs (or with
  `jarvis serve --once`).
- A failed or stopped task is restarted from scratch, not resumed mid-way.
- In local mode without a remote, the task branch stays in your repo and the PR
  body is a file under `~/.jarvis/prs/`.
- The agent's Bash commands are not sandboxed (see README, Safety model).
- Windows: the code and test suite run on Windows in CI (and the installed CLI
  works there), but no real-engine run has been done on Windows yet.
- Python environments are built per task (a few seconds with uv and a warm
  cache) and need network access to your package index. Layouts other than
  extras, dependency groups or requirements files (for example Poetry dev
  groups) need `setup`/`test` set in the repo config.
- Large tasks (over 150 words by default) wait for `jarvis approve`; in a fully
  unattended `serve` they stay waiting until you approve them.

## NOT VERIFIED

- **codex engine**: implemented against `codex exec` 0.169 flags (checked with
  `--help`), but never run with real credentials. No OpenAI credentials were
  available.
- **hermes engine**: ported from the Foundry adapter's subprocess call; Hermes
  was not installed or run.
- **Failure comments on GitHub issues**: when an issue's task fails, JARVIS
  comments on the issue. Covered by tests with a stubbed `gh`; the real smoke
  test only covers issues whose task succeeds.
- **macOS real-engine runs**: CI runs the test suite and install smoke on macOS;
  no real-engine run was done there.

## Independent suite on unfamiliar projects (0.1.0rc1)

The strongest evidence in this file. An independent author, who did not see
JARVIS's code, wrote three realistic projects and ten user-style tasks, each
with a hidden acceptance check:

- **The projects:**
  - `invoicer`: a Python app, requirements files, about 960 lines, 98 tests;
  - `geokit`: a src-layout library with attrs and `[test]` extras, about 940 lines, 71 tests;
  - `taskboard`: a Node CLI with jest, about 850 lines, 76 tests.
- **How the checks were proven:** each one fails on the base commit and passes
  on a reference fix. Cross-checks show no check passes on an unrelated change.
- **Bug-fix tasks:** the bugs were injected with no test coverage, so the reports
  only describe symptoms.
- **Harder tasks:** the add-tests task is judged by mutation testing, and the
  performance task by a timed 60k-task run plus a differential test.

Run with `python scripts/run_suite.py SUITE WORK` on commit 69a2f8f, 2026-11-14,
Linux, with `claude_code` (`claude-opus-5-5`):

| # | Repo | Task | Outcome | Hidden check | Repairs | Duration | Cost |
|---|---|---|---|---|---|---|---|
| 1 | invoicer | bugfix: Invoice amounts sometimes one cent lower than our bookkeeping software | DONE | pass | 0 | 0m58s | $0.59 |
| 2 | invoicer | feature: Support an invoice-level discount | DONE (after `jarvis approve`) | pass | 0 | plan + run | $1.14 |
| 3 | invoicer | multi-file: Rename `qty` to `quantity` everywhere | DONE | pass | 0 | 1m13s | $0.64 |
| 4 | invoicer | impossible: Subtotal must always match both the printed lines and the exact sum | FAILED (expected) | - | 0 | 0m26s | $0.21 |
| 5 | geokit | bugfix: parse_coordinate puts southern/western points in the wrong hemisphere | DONE | pass | 0 | 0m41s | $0.40 |
| 6 | geokit | refactor: Port Point and BoundingBox to attrs | DONE | pass | 0 | 1m27s | $0.55 |
| 7 | geokit | add-tests: Add tests for the geohash module | DONE | pass (12/12 mutants killed) | 0 | 2m23s | $0.85 |
| 8 | taskboard | bugfix: WIP limit lets one extra card in | DONE | pass | 0 | 0m36s | $0.41 |
| 9 | taskboard | feature: Allow excluding things in list queries | DONE | pass | 0 | 1m26s | $0.54 |
| 10 | taskboard | performance: Everything is slow on big boards | DONE | pass | 0 | 1m18s | $0.63 |

Result: **all 9 doable tasks done and passing their hidden checks, the impossible
task failed cleanly with an exact explanation, 0 false successes**, default
branches untouched, 0 false warnings, about $5.96 in total.

- **Task 2:** its description was over the plan threshold, so JARVIS stopped with
  a plan and three open questions. After `jarvis approve` it finished and passed.
- **Dependencies:** all three projects got their dependencies installed per task
  with no configuration. Before 0.1.0rc1, the two Python projects would have
  failed verification on every task.

## Fixture suite on 0.1.0rc1 (no regression)

| # | Repo | Task | Outcome | Hidden check | Repairs | Duration | Cost |
|---|---|---|---|---|---|---|---|
| 1 | pyshop | bug fix: Fix apply_discount: percentage discounts are applied wrong | DONE | pass | 0 | 0m23s | $0.24 |
| 2 | pyshop | small feature: Add Cart.remove(sku, qty=None) to shop/cart.py | DONE | pass | 0 | 0m27s | $0.27 |
| 3 | pyshop | refactor: Refactor shop/inventory.py to remove duplicated SKU validation | DONE | pass | 0 | 0m20s | $0.23 |
| 4 | pyshop | add missing tests: Add tests for shop/money.py | DONE | pass | 0 | 0m57s | $0.52 |
| 5 | nodecart | multi-file change: Rename calcTotal to computeTotal everywhere | DONE | pass | 0 | 0m17s | $0.24 |
| 6 | nodecart | bug fix: slugify leaves repeated and trailing dashes | DONE | pass | 0 | 0m24s | $0.25 |
| 7 | nodecart | small feature: Add a --json flag to bin/cli.js | DONE | pass | 0 | 0m37s | $0.30 |
| 8 | gotext | small feature: Add textutil.Truncate(s string, n int) string | DONE | pass | 0 | 0m25s | $0.23 |
| 9 | gotext | should fail cleanly: Report API version 2 | FAILED (expected) | - | 0 | 0m11s | $0.10 |
| 10 | gotext | bug fix: WordCount counts punctuation as part of words | DONE | pass | 0 | 0m33s | $0.30 |

9/10 with verified tests, 0 false successes, 0 false warnings.

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

## Re-run on 0.1.0b2 (no regression)

The same 10 tasks after adding the baseline run, test-change check and PR-outcome
learning (commit 1bb02d0), run 2026-11-14 on Linux with `claude_code`
(`claude-opus-5-5`), local PR mode:

| # | Repo | Task | Outcome | Hidden check | Repairs | Duration | Cost |
|---|---|---|---|---|---|---|---|
| 1 | pyshop | bug fix: Fix apply_discount: percentage discounts are applied wrong | DONE | pass | 0 | 0m22s | $0.24 |
| 2 | pyshop | small feature: Add Cart.remove(sku, qty=None) to shop/cart.py | DONE | pass | 0 | 0m29s | $0.28 |
| 3 | pyshop | refactor: Refactor shop/inventory.py to remove duplicated SKU validation | DONE | pass | 0 | 0m16s | $0.21 |
| 4 | pyshop | add missing tests: Add tests for shop/money.py | DONE | pass (4/4 mutants killed) | 0 | 0m45s | $0.31 |
| 5 | nodecart | multi-file change: Rename calcTotal to computeTotal everywhere | DONE | pass | 0 | 0m17s | $0.23 |
| 6 | nodecart | bug fix: slugify leaves repeated and trailing dashes | DONE | pass | 0 | 0m20s | $0.24 |
| 7 | nodecart | small feature: Add a --json flag to bin/cli.js | DONE | pass | 0 | 0m32s | $0.29 |
| 8 | gotext | small feature: Add textutil.Truncate(s string, n int) string | DONE | pass | 0 | 0m24s | $0.23 |
| 9 | gotext | should fail cleanly: Report API version 2 | FAILED (expected) | - | 0 | 0m12s | $0.11 |
| 10 | gotext | bug fix: WordCount counts punctuation as part of words | DONE | pass | 0 | 0m33s | $0.28 |

Result: **9/10 succeeded with verified green tests, 0 false successes**; task 9
failed cleanly; no fixture's `origin/main` moved. Total cost $2.42.

Review lessons with the real engine: given two review comments on a closed PR
("keep shop/ stdlib-only", "use plain pytest functions"), the lessons call
proposed exactly those two durable notes ($0.09, 5 s).

## STOP and RESUME with the real engine

Re-run on 0.1.0rc1 (installed copy, 2026-11-14): STOPPED **0.81 s** after
`jarvis stop`, agent process group gone, `serve` kept running and started
nothing, and both tasks were DONE after `jarvis resume`. The original b1 run
follows.

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

0.1.0b2 extends the same workflow (run 43631259031, 2026-11-14). One
`jarvis serve --once` took a `jarvis add` task and **real issue #9** labeled
`jarvis-e2e`, and opened **draft PRs #10 and #11**. PR #11's body says
`Closes #9`, and both show the baseline (`before the change: exit 1`). After a
review comment on #10 and closing both, a second `jarvis serve --once` recorded
them as `DONE/closed`, `jarvis ls` reported "2 opened, 0 merged (0%)", and the
review comment became a proposed note. PRs, issue and branches were cleaned up.

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
