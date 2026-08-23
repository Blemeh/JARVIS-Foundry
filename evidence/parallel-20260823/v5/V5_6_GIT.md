# V5-6 Git / Repository Connector

Verdict: PASS for disposable local repositories and remotes.

## Implementation

`GitConnector` is a boundary adapter over the existing V3
`GitEffectAdapter`, `preflight_identity`, and `ls_remote_head`; it does not
create a second release controller. It binds local commits to an explicitly
owned worktree, rejects dirty/unowned paths, and delegates push journaling,
STOP/veto checks, duplicate delivery, and ambiguity handling to the canonical
effect machinery.

## Disposable proof

- A temporary bare remote and clone provide the only remote target. The
  connector inspects worktree/branch/remote identity, creates one named local
  commit, and pushes it.
- Completion is accepted only when `git ls-remote` reports the expected remote
  object id. Re-delivery of the same action id returns the recorded terminal
  result with `performed=False`.
- A push that lands and then loses its response raises
  `GitReconcilePending`; a blind retry remains refused. `reconcile` reads the
  remote ref, records provider truth, and the subsequent replay performs no
  second push.
- Dirty worktrees and worktrees outside the connector's owned root fail before
  `system.action_started`. Path traversal and `.git` writes are rejected.

## Verification

```text
python -m pytest -vv tests/foundry/test_git_connector.py
3 passed in 12.89s

python -m pytest -q tests/foundry/test_git_connector.py \
  tests/foundry/test_v3_git_reconcile.py tests/foundry/test_v3_fault_matrix.py
exit 0

python -m compileall -q src/jarvis_foundry/connectors/git.py \
  tests/foundry/test_git_connector.py
exit 0
```

All repositories/remotes were disposable lane fixtures. No production remote,
credential, account, deployment, or release slot was touched.
