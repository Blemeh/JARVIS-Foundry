# Lane 3 — Developer Effects Verb Contract (for Lane 4 composition)

All verbs are canonical Fabric actions dispatched through
`execute_action(database, ctx, intent, action_kind, payload)`. They reuse the
single effect journal (`system_actions` + `effect.*` events); no second
authority was introduced.

## 1. Action kinds, payloads, effects

| action_kind | payload | idempotency | recovery |
|---|---|---|---|
| `git.branch` | `{worktree, name}` | Fabric `effect:` from payload | none needed (local) |
| `git.commit` | `{worktree, message, changes}` or `{worktree, existing: true, paths, message}` | Fabric `effect:` | none needed (local) |
| `git.push` | `{worktree, remote, branch?, action_id?}` | V3 key `v3-git:{action_id}` | `git.push.reconcile` after `effect.frozen`/crash window |
| `git.push.reconcile` | `{action_id, remote, branch?, expected_head}` | read-only; appends only reconciliation events | n/a (is the recovery verb) |
| `git.read_ref` | `{remote, branch?}` | none (read) | n/a |
| `github.issue.create` | `{owner, repo, title, body?}` | Fabric `effect:` (input-hash bound) | `github.write.reconcile` with the exact original payload |
| `github.issue.comment.create` | `{owner, repo, number, body}` | Fabric `effect:` | `github.write.reconcile` |
| `github.pull_request.create` | `{owner, repo, title, head, base?, body?}` | Fabric `effect:` | `github.write.reconcile` |
| `github.pull_request.publish` | `{worktree, owner, repo, remote, base?, head_sha, title, body?, mission_id?, stop_path?}` | deterministic plan (branch effect + push effect + PR effect) | call `publish` again: it reconciles each stage before any re-dispatch |
| `github.write.reconcile` | `{original_effect_id, original_action?, request: <exact original Fabric payload>}` | read-only, not journaled | n/a (is the recovery verb) |
| `github.pull_request.read` / `github.issue.read` | `{action, endpoint}` | read | n/a |
| `github.identity.read` | `{}` | read | n/a |

## 2. Effect-key inputs (do not change)

* Fabric: `fabric_effect_id(system_id="github"|"git", target_ref,
  capability_id, capability_version, action_kind, input_payload)`.
  `input_payload` excludes runtime controls
  (`claim_ttl_s`, `stop_path`, `mission_id`, `budget`, `timeout_*`,
  `idempotency_key`, `stop_scope`). `github_write_input_hash` uses the same
  canonical filter, and recovery refuses a payload whose hash does not match
  the durable journal identity (`request_identity_mismatch`).
* Connector `system.action:` key: action kind + exact endpoint + sha256 of the
  provider payload (`{title, body}` / `{body}` / `{title, head, base, body}`).
  Owner-supplied text is never rewritten.
* Git push: `v3-git:{action_id}` — a repaired/renewed push is a NEW action id.

## 3. Receipt fields

`github.issue.create` / `github.issue.comment.create` / `github.pull_request.create`
result (and `system.action_completed.after_state`):

```
claimed, action_kind, endpoint, owner, repo, resource,
idempotency_key, payload_hash, response_hash, status,
number, provider_id, provider_ref, html_url,
readback_status, readback {status, matches, observed_head?},
reconciliation_required, reconciled, replayed, duplicate_prevented,
error_code, title, head, base
```

Verified only when: `claimed=COMPLETED`, `reconciled=true`,
`readback_status=MATCH`, `readback.matches=true`, `number` present.
Without a read transport the verifier stays `NOT_TESTED`/MISMATCH (honest).

`git.push` receipt: `{outcome, action_key, remote_head, performed,
already_satisfied?, provider_state, reason?}`; Fabric-level adds
`observed_remote_head` for the `remote_head == observed_remote_head` check.

`git.push.reconcile` receipt: `{claimed: COMPLETED, reconciled: true,
performed: false, action_key, action_id, remote, branch, expected_head,
remote_head, observed_remote_head, provider_state}` when the remote carries
the expected head; otherwise `{claimed: FAILED, error_code:
remote_branch_mismatch, observed_remote_head, ...}`. `effect_claim_active`
returns `{claimed: UNKNOWN, reconciliation_required: true}` while another
process' claim is unexpired.

`github.pull_request.publish` receipt: `{claimed, stage, accepted,
terminal_truth, provider_id, provider_ref, number, push_effect,
pull_request_effect, external_effects, branch, head_sha, intent_id}`.
`external_effects` is 0/1/2 (branch is local, push and PR are external).

`github.write.reconcile` receipt: `{claimed: COMPLETED, accepted: true,
reconciled: true, original_effect_id, provider_id, provider_ref, number,
readback.status: MATCH}`; or `{claimed: UNKNOWN, reconciliation_required:
true, error_code: not_observed|duplicate_provider_matches|readback_not_configured|
effect_claim_active}`.

## 4. Safe retry vs reconcile

* A repeated identical intent is a replay: `execute_fabric_effect` returns the
  recorded outcome with `replayed=true, performed=false`. Never dispatch a
  "second attempt" by minting a new payload (e.g. editing the title) — that is
  a new effect by definition.
* `FAILED` is terminal for that identity: repair the cause and use a new
  intent/action id; do not re-run the same identity expecting a different
  result.
* `FROZEN`/`UNKNOWN` after a timeout, 5xx, or crash: DO NOT re-dispatch.
  Call `github.write.reconcile` (or re-invoke `github.pull_request.publish`
  for the publish path) so provider truth decides.
* `github.write.reconcile` is allowed while STOP is active
  (`reconciliation_capable`) and while write dispatch is disabled; it performs
  zero provider writes. It requires a read transport: set `ctx.github_api`
  (production fallback: the same `gh api` session used by `github.*.read`).
* If exactly one provider object matches the intent → the ORIGINAL effect is
  finalized with `finalize_verified_effect` (durable receipt + acceptance).
  Zero matches → `not_observed`; multiple matches → `duplicate_provider_matches`
  (both stay UNKNOWN; no guessing). A scan that exhausts the bounded page
  limit without a match reports `scan_truncated`, never a false `not_observed`.
* Git push after a crash window: a `STARTED` V3 push claim is frozen by the
  next `push` attempt (never re-executed) and must be resolved with
  `git.push.reconcile`. Matching remote head → `effect.reconciled` on the
  original key; a mismatch records provider truth and reports
  `remote_branch_mismatch`. `git.push.reconcile` is allowed under STOP and
  appends reconciliation events at most once per outcome.

## 5. Remote / provider identity

* Git remote: absolute path of the bare remote (demo) or an authorized remote
  URL. `GitConnector.remote_branch_state(remote, branch, expected_head=...)`
  returns `{exists, head_sha, expected_head, matches_expected, push_required,
  source: "git ls-remote"}` — use it before retrying any push.
* GitHub: exact `owner/repo`; PR identity additionally `head` branch and
  `base`; comments additionally the issue/PR `number`. Provider ids are
  `github:{issues|pulls}:{owner}/{repo}#{number}` and
  `github:comment:{owner}/{repo}#{id}`.

## 6. Enabling live transport (owner-only, default OFF)

Environment (read by `fabric/adapter.py` via `github_write_dispatch_settings`):

* `JARVIS_GITHUB_WRITES_ENABLED=1` — enables write dispatch;
* `JARVIS_GITHUB_AUTHORIZED_REPOSITORIES=owner/repo[;owner/repo2]` — exact
  allow-list (required for any write; a repo not listed is
  `repository_not_authorized`);
* `JARVIS_GITHUB_AUTHORIZATION_FILE=<path.json>` — optional owner grant for
  `github.pull_request.publish`:
  `{authorization_id, repository, remote, allowed_base_branches,
  owner_authorized, dispatch_enabled, capability_trust}`. The grant repo must
  also appear in the allow-list.

Malformed configuration raises `GitHubWriteError` (never silently enables).
Credentials stay in the authenticated `gh` session; no OAuth or account
management exists in this lane. Publish remains additionally gated by
`GitHubRepositoryAuthorization.refusal` (owner_authorized, dispatch_enabled,
PROBATION refusal) and by `ctx.github_repository_authorization` being set.

## 7. Live-proof-only (not proven in this branch)

* Real `gh` POST against a real throwaway repository (no owner-provided
  throwaway target or explicit authorization existed; nothing was invented).
* Real PR publish to github.com (same reason).
* Production worker path with the environment seam enabled end-to-end
  (the seam is wired to `fabric/adapter.py`, but live enablement is owner
  action and was not exercised).
