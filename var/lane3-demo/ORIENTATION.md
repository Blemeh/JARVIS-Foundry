# Lane 3 — Developer Effects Orientation (Git / GitHub write path)

Base: `4b2ae28da582b369982661c2a1d2b1792e2f48cb` (schema line 40).
Worktree: `C:\temp\JARVIS\max-lane-3-v14-effects-20260915`.
This map reflects the code as inspected at lane start plus the lane's own
changes; every claim below was verified against the checked-out source.

## Action kinds and where they live

| action_kind | semantic | executor (HANDLERS) | connector | journal scope |
|---|---|---|---|---|
| `git.inspect` / `git.diff` / `git.log` / `git.read_ref` | READ | `fabric/executors.py` git handlers | `GitConnector` (`conn-git-v5`) | none (read) |
| `git.branch` | WRITE | `git_branch` | `GitConnector.create_branch` | Fabric `execute_fabric_effect` (`effect:...`) |
| `git.commit` | WRITE | `git_commit` | `GitConnector.create_commit` / `commit_existing` | Fabric `execute_fabric_effect` |
| `git.push` | EXTERNAL_EFFECT | `git_push` | `GitConnector.push` → `GitEffectAdapter` | specialized: V3 journal key `v3-git:{action_id}` |
| `github.*.read` (repo/issue/pull_request/commit/check/release/search) | READ | `github_research` | `GitHubResearchConnector` (`conn-github-research-v1`), live `gh api` by default | none |
| `github.identity.read` | READ | `github_identity` | research connector probe | none |
| `github.issue.create` | EXTERNAL_EFFECT | `github_issue_create` → `_github_write` | `GitHubWriteAdapter` (`conn-github-write-v1`) | Fabric `execute_fabric_effect` |
| `github.issue.comment.create` | EXTERNAL_EFFECT | `github_issue_comment_create` → `_github_write` | same | Fabric `execute_fabric_effect` |
| `github.pull_request.create` | EXTERNAL_EFFECT | `github_pull_request_create` → `_github_write` | same | Fabric `execute_fabric_effect` |
| `github.pull_request.publish` | EXTERNAL_EFFECT (irreversible) | `github_pull_request_publish` | `GitHubPullRequestEffectDriver` (branch + push + PR) | specialized: three canonical effects |
| `github.write.reconcile` (NEW, this lane) | READ | `github_write_reconcile` | `GitHubWriteAdapter.observe_request` | read-only recovery of one original effect |

## Payload schemas (Fabric-facing)

* `github.issue.create`: `{owner, repo, title, body?}` →
  `POST /repos/{owner}/{repo}/issues`, provider body `{title, body}`.
* `github.issue.comment.create`: `{owner, repo, number, body}` →
  `POST /repos/{owner}/{repo}/issues/{number}/comments`, provider body
  `{body}`. GitHub comments are issue comments; a PR number works natively.
* `github.pull_request.create`: `{owner, repo, title, head, base?, body?}` →
  `POST /repos/{owner}/{repo}/pulls`.
* `github.pull_request.publish`: `{worktree, owner, repo, remote, base?,
  head_sha, title, body?}` (executor reads `_adapter_request.effect_id` and
  requires `ctx.github_repository_authorization`).
* `github.write.reconcile`: `{original_effect_id, original_action?,
  request: <exact original Fabric payload>}`.

## Effect identity / idempotency

* Fabric effect id: `fabric_effect_id(system_id, target_ref, capability_id,
  capability_version, action_kind, input_payload)` → `effect:<sha256>`.
  Durable claims live in `system_actions` via `execute_fabric_effect`.
* Connector replay key (adapter-local): `external_action_key("github",
  f"{action_kind}:{endpoint}", sha256(canonical provider payload))` →
  `system.action:<24 hex>`. In-memory `_completed`/`_pending` maps are caches
  only; durable truth is the Fabric journal.
* Git push: V3 key `v3-git:{action_id}`; ambiguity freezes via
  `effect.frozen` and must be resolved by `reconcile_pending` (ls-remote).

## Provider identity and readback fields

* Issue/PR: provider id `github:{issues|pulls}:{owner}/{repo}#{number}`,
  provider ref `html_url` (fallback constructed), item readback
  `GET {endpoint}/{number}`, collection readback
  `GET {endpoint}?state=all&per_page=100`.
* Comment: provider id `github:comment:{owner}/{repo}#{id}`, ref `html_url`
  (fallback `.../issues/{n}#issuecomment-{id}`), item readback
  `GET /repos/{o}/{r}/issues/comments/{id}`, collection readback
  `GET /repos/{o}/{r}/issues/{n}/comments?per_page=100`.
* Match rule: exact identity + exact `title` (and `body` when present) for
  issues/PRs; exact `body` for comments; head/base refs for PRs.
* Every write result carries `readback: {status: MATCH|MISMATCH|UNKNOWN|
  NOT_TESTED, matches}`; `reconciled` only becomes true after an independent
  read.

## Irreversible classification (`effects.infer_effect_class`)

* `git.push`, `github.pull_request.create`, `github.pull_request.publish` →
  `IRREVERSIBLE_OR_HIGH_COST`.
* `github.issue.create`, `github.issue.comment.create` →
  `REVERSIBLE_EXTERNAL` (still canonical-effect journaled).
* `.read` → `READ_ONLY`.

## Local Git push behavior (before this lane)

`GitConnector.push` delegated to `GitEffectAdapter.push`: preflight identity
(reject dirty), journal `v3-git:{action_id}`, execute `git push`, verify with
`git ls-remote`. A replay of the same action id returned `performed=False`;
a second push under a new action id executed the transport again even when
the remote already carried the commit. No structured remote-state API existed.

## Real code vs fixtures

* Real transport: `gh_api_write_transport` (`gh api` POST), research reads
  (`gh api` GET), `git` CLI push via subprocess.
* Fixtures: `tests/foundry/test_v12_irreversible_github.py::FakeGitHub`,
  `tests/foundry/test_v14_lane3_developer_effects.py::FakeIssueProvider`,
  `var/lane3-demo/fake_github_provider.py` (file-backed, cross-process).
* `scripts/v12_external_effects_proof.py` is the disposable proof harness
  (constructor-injected `owner/disposable`).
* No production runtime constructor enabled GitHub writes before this lane;
  `fabric/adapter.py` now reads the explicit environment opt-in
  (`github_write_dispatch_settings`, default off).

## Lane gaps found and closed

1. A terminal-but-unaccepted GitHub write effect (crash/timeout/5xx after
   the provider accepted) had no provider-reconciliation path: the generic
   Fabric replay returned `terminal_effect_recorded` forever. Closed with
   `reconcile_github_write_effect` + the `github.write.reconcile` action.
2. `github.issue.comment.create` did not exist. Added bounded comment
   support with the same identity/readback/dedupe contract.
3. A second identical push under a new action id re-invoked the transport.
   `GitConnector.push` now checks provider truth first and reports
   `already_satisfied`, `performed=False`.
4. `git.branch` had no independent verifier predicate (always UNKNOWN).
   Added worktree re-observation.
5. GitHub write dispatch had no owner-visible configuration seam. Added the
   default-off environment opt-in plus an optional JSON owner grant.
