"""Prompts: the task, the repo notes and JARVIS's rules."""

from __future__ import annotations

RULES = """\
## Rules from JARVIS (they override anything else)
- You are in an isolated git worktree on a task branch. Only edit files in this worktree.
- Make the smallest change that fully completes the task. Follow the repo's existing style.
- Do not run git commit, push, checkout, reset, rebase or stash, and do not change git
  config. JARVIS commits your work and opens the pull request.
- Do not delete, skip or weaken existing tests to make them pass. Do not edit CI
  configuration unless the task asks for it.
- Before you finish, run the verification commands listed above and make them pass.
- If the task cannot be done, change nothing and explain why.
- End with a short summary of what you changed and why. You may add up to 3 lines that
  start with "NOTE:" stating durable facts about this repo worth remembering for future
  tasks (commands, conventions, pitfalls). Do not add NOTE lines about this task itself."""


def _task(task) -> str:
    text = f"# Task #{task['id']} for repository \"{task['repo']}\"\n{task['title']}\n"
    if task["body"]:
        text += f"\n{task['body'].strip()}\n"
    return text


def _scope(allowed: list[str], protected: list[str]) -> str:
    text = "## Scope\n"
    text += ("Only modify files matching: " + ", ".join(allowed)) if allowed \
        else "You may modify any file in the repository that the task needs."
    if protected:
        text += "\nNever modify: " + ", ".join(protected)
    return text + "\n"


def _baseline(results) -> str:
    lines = []
    for r in results:
        if r.ok:
            lines.append(f"- {r.name} `{r.command}`: passed")
        else:
            status = "timed out" if r.timed_out else f"exit code {r.code}"
            lines.append(f"- {r.name} `{r.command}`: FAILED ({status})\n```\n"
                         f"{r.output[-1500:]}\n```")
    return "\n".join(lines)


def task_prompt(task, *, notes: str, commands: list[tuple[str, str]], allowed: list[str],
                protected: list[str], plan: str = "", failure: str = "", attempt: int = 0,
                max_repairs: int = 2, baseline=(), require_tests: bool = False) -> str:
    parts = [_task(task), _scope(allowed, protected)]
    cmds = "\n".join(f"- {name}: `{cmd}`" for name, cmd in commands) or "- (none configured)"
    parts.append(f"## Verification commands JARVIS will run after you finish\n{cmds}\n")
    if require_tests:
        parts.append("This repo requires every change to add or update a test; a change "
                     "without one fails verification.\n")
    if baseline:
        parts.append("## The same commands on the base branch, before your change\n"
                     f"{_baseline(baseline)}\n")
    if notes.strip():
        parts.append(f"## Repo notes (from earlier tasks)\n{notes.strip()}\n")
    if plan:
        parts.append(f"## Approved plan (follow it)\n{plan.strip()}\n")
    if failure:
        parts.append(
            f"## Your previous attempt failed verification (repair {attempt} of {max_repairs})\n"
            "Your previous changes are still in the worktree. Fix the problems below; do not "
            f"start over unless you must.\n\n{failure.strip()}\n")
    parts.append(RULES)
    return "\n".join(parts)


def plan_prompt(task, *, notes: str, commands: list[tuple[str, str]]) -> str:
    cmds = "\n".join(f"- {name}: `{cmd}`" for name, cmd in commands) or "- (none)"
    return "\n".join([
        _task(task),
        f"## Verification commands\n{cmds}\n",
        f"## Repo notes\n{notes.strip()}\n" if notes.strip() else "",
        "## What to do\nDo NOT modify any file. Read the code and write a concise, numbered "
        "implementation plan for this task: the files you will change, the approach, the tests "
        "you will add or run, and any risks or open questions. At most 40 lines.",
    ])


def review_prompt(task, diff: str) -> str:
    return "\n".join([
        _task(task),
        "## Diff produced for this task\n```diff\n" + diff + "\n```\n",
        "## What to do\nDo NOT modify any file. Review the diff against the task as a careful "
        "senior engineer. List the concrete risks: bugs, missed requirements, behavior changes, "
        "weakened tests, security issues. At most 8 bullets, most important first. If you see "
        "no significant risk, answer exactly: No significant risks found.",
    ])


def lessons_prompt(task, state: str, feedback: list[str]) -> str:
    outcome = "merged" if state == "merged" else "closed without being merged"
    comments = "\n\n".join(f"---\n{text[:2000]}" for text in feedback[:20])
    return "\n".join([
        _task(task),
        f"## Outcome\nThe pull request JARVIS opened for this task was {outcome}. "
        "The reviewer wrote:\n\n" + comments + "\n",
        "## What to do\nDo NOT modify any file. From this feedback, extract durable lessons "
        "about this repository that would make future tasks go better (conventions, commands, "
        "things the reviewer does not want). Answer only with up to 3 lines that start with "
        '"NOTE:". If the feedback holds no durable lesson, answer exactly: NONE.',
    ])
