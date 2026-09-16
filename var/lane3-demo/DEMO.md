# Lane 3 Demo — Developer Effects on Real Local Substrate

Run everything:

```powershell
python var/lane3-demo/demo.py all
```

The demo creates its own disposable workspace under `var/lane3-demo/work/`
(ignored by Git), executes five scenarios against real code paths, and writes
machine-readable receipts to `var/lane3-demo/receipts/`. It never touches live
JARVIS: no live database, release pointer, trust window, Scheduler entry,
runtime state, or provider credential is read or written, and the
JARVIS-Foundry `origin` is never used.

## Scenario 1 — Git branch / commit / push (`receipts/git.json`)

Real substrate: a local bare remote `work/git/remote.git`, a clone under the
connector's owned root, real `git` subprocesses, a real SQLite effect journal.

Observed on this run:

* `create_branch` → `lane3/feature`; deterministic commit
  `c10813a17e835c084a1c4e1ed09f7cadf60b478a`.
* First push: `outcome=COMPLETED`, `performed=true`, `remote_head` equals the
  commit; `remote_branch_state` → `exists=true`, `matches_expected=true`,
  `push_required=false`, `source=git ls-remote`.
* Second push of the same commit under a new action id:
  `already_satisfied=true`, `performed=false`, `duplicate_material_effects=0`.
  The transport was never invoked (provider truth already carried the SHA).
* A divergent local commit pushed to the advanced branch returned a truthful
  `outcome=FAILED` with the real `! [rejected] ... (fetch first)` reason; the
  remote kept the upstream head the other clone pushed.

## Scenario 2 — GitHub issue dedupe (`receipts/github-issue.json`)

Fake file-backed provider (honest identity: monotonic numbers, GET returns
stored documents). Fabric dispatch through `execute_action` with
`github_writes_enabled=true` and `owner/demo` allow-listed.

Observed: first intent `claimed=COMPLETED`, `verified=MATCH`,
`reconciled=true`, `readback_status=MATCH`; second identical intent
`replayed=true`, `performed=false`; provider `writes=1`, `issues=1`.

## Scenario 3 — Pull request publish (`receipts/github-pr.json`)

Deterministic branch creation + push to the local bare remote + PR create via
`GitHubPullRequestEffectDriver` with the file-backed provider.

Observed: first publish `claimed=COMPLETED`, `stage=complete`, `accepted=true`,
`terminal_truth=true`, provider `pulls=1`, `writes=1`; second publish
`replayed=true`, `performed=false`, still `writes=1`. The pushed branch head on
the bare remote was read back with `git ls-remote`.

## Scenario 4 — Crash recovery (`receipts/crash-recovery.json`)

The strongest proof: a real child process (`python demo.py crash-child ...`)
dispatches `github.issue.create` through the canonical Fabric path. Its
transport POSTs the issue to the file-backed provider, prints the write
count, and calls `os._exit(9)` before any journal completion. The durable
journal is therefore left `STARTED`.

A fresh parent process then:

1. observes exactly one `system.action_started` (kind `github.issue.create`);
2. waits for the claim TTL (0.01 s) to expire;
3. dispatches `github.write.reconcile` with the exact original intent;
4. the reconciler freezes the stale claim (`effect.frozen`,
   reason `interrupted GitHub write requires provider readback`), reads
   provider truth, finds exactly one matching object, and finalizes the
   ORIGINAL effect through `finalize_verified_effect`;
5. the same original intent replayed returns the recorded outcome.

Observed on this run: `child_exit_code=9`, provider `writes=1`, final state
`status=FROZEN` (the crash reap is preserved) with `terminal_outcome=COMPLETED`,
`accepted=true`, `reconciled=true`. No second POST existed to issue.

## Scenario 5 — Git push crash recovery (`receipts/git-crash-recovery.json`)

A child process pushes a deterministic commit to the local bare remote and then
dies with `os._exit(9)` before the V3 push journal completes, leaving the push
identity `STARTED`. A fresh parent process runs the read-only
`git.push.reconcile` action:

* the stale claim is frozen (`interrupted git push requires ls-remote
  reconciliation`), never re-executed;
* `git ls-remote` confirms the remote carries the expected SHA;
* `effect.reconciled` is appended to the ORIGINAL action key;
* a later push of the same action id returns
  `outcome=COMPLETED, performed=false, reconciled=true` with zero additional
  transport calls.

Observed: `child_exit_code=9`, before=`STARTED`, reconcile
`claimed=COMPLETED verified=MATCH`, replay `performed=false reconciled=true`,
remote head equals the expected head.

## Why FROZEN + accepted is correct

The journal is monotonic: a crash reap is never rewritten as COMPLETED. The
provider-matched acceptance is appended as separate durable truth
(`effect.provider_receipt`, `effect.readback_observed`, `effect.reconciled`,
`effect.acceptance_recorded`, `effect.terminal_truth`). Replays read that
truth and never dispatch again.

## Focused test suite

```powershell
python -m pytest tests/foundry/test_v14_lane3_developer_effects.py -q
```

Covers: duplicate-intent single POST, crash-after-POST recovery via the
Fabric action, exact-intent binding refusal, provider object finalization,
reconcile-allowed-under-STOP, PR-create recovery, issue-comment dedupe +
recovery, env settings seam (default off / malformed refusal), git push no-op
and divergence, and `git.branch` independent verification.
