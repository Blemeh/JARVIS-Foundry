# JARVIS

JARVIS is my software-development assistant. I give it a coding task for one of
my git repos, from the command line or by labeling a GitHub issue `jarvis`. It
works each task in an isolated git worktree and branch, using a strong coding
agent run headlessly. It then checks the work itself: it runs the repo's own
test and lint commands, confirms the diff is non-empty, and confirms the diff
stays inside the allowed scope. It never trusts the agent's own claims.

If verification fails, JARVIS feeds the failure output back to the agent, at
most twice. If it still fails, the task is marked FAILED with the reason and the
branch is kept for inspection. If it passes, JARVIS pushes the branch and opens
a pull request whose body shows a summary, the changed files, the verification
commands and their output, the engine and model, and the cost and duration.

JARVIS keeps a notes file per repo (commands, conventions, lessons). After a
task it proposes additions; I accept or reject them.

JARVIS never merges, never pushes to a default branch and never deploys.
`jarvis stop` halts all work within seconds, including the agent process.
`jarvis resume` continues the queue.

Success = useful pull requests that I merge, every week.

Out of scope until this file says otherwise: email, calendar, voice, chat
integrations other than one generic webhook, desktop or browser automation,
office documents, a world model, inventing its own goals, scheduled jobs,
self-updating, trust levels, a web dashboard.
