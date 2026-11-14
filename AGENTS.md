# Working on JARVIS

Product: see IDEA.md. If a change does not help JARVIS turn a task into a
mergeable, verified pull request, do not make it. Design: see ARCHITECTURE.md.

## Rules
- Work on a branch. Keep PRs small (aim for under 400 changed lines).
- Replace, don't layer: if you write a new version of something, delete the old
  one in the same PR. One implementation per job.
- Core budget: `src/jarvis` stays at or under 5,000 non-blank, non-comment
  lines (`python scripts/loc_budget.py`). Raising it needs owner sign-off.
- No new top-level modules or packages without owner sign-off.
- Boring tools only: git, gh, subprocess, sqlite3, tomllib, argparse, pytest,
  ruff. No orchestration frameworks, no event sourcing, no ORM.
- Every feature is reachable from the CLI and covered by a test. No code that
  nothing calls.
- Docs are README, IDEA, ARCHITECTURE, AGENTS, BETA and CHANGELOG only. No
  evidence, report, handoff or checkpoint files. The PR and CI are the evidence.
- Tests use the fake engine, temp directories and `JARVIS_HOME`. They never
  touch the owner's real HOME, PATH, credentials or repositories.
- Never print or commit secrets.
- Never report a test or run you did not actually execute. A fake-engine test
  does not prove a real engine works.
- Product details that are unclear: pick the simplest behavior consistent with
  IDEA.md and add one line under "Decisions" in ARCHITECTURE.md.

## Commands
- `uv run pytest`
- `uv run ruff check`
- `uv run python scripts/loc_budget.py`
- `uv run jarvis --help`

## Done means
Tests and ruff pass, the line budget holds, the PR describes the change, and
you ran the change's acceptance check by hand.
