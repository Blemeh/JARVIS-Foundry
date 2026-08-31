# Build workflow

Use for ordinary source, UI, integration-adapter, documentation, or repair
work that does not activate a live release.

1. Inspect root/worktree/branch/HEAD/status and preserve unrelated work.
2. Read current state, active task contract, relevant code/tests, and history.
3. Start from the exact requested base in an isolated branch/worktree.
4. Inventory existing JARVIS and mature external solutions before designing.
5. Define owned surfaces, expected effect, verification, rollback, and explicit
   non-goals.
6. Implement the smallest coherent change using existing authority paths.
7. Run focused functional, integration, recovery, and performance checks
   proportional to the change.
8. Inspect actual outputs/diffs; commit a meaningful clean checkpoint.
9. Report candidate truth separately from canonical, installed, runtime, and
   production-trust truth.

Forbidden by default: live database/Scheduler/process/install/release-pointer
mutation, trust changes, merge to canonical main, push, or deployment.
