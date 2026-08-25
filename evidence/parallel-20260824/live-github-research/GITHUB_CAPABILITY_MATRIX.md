# GitHub Capability Matrix — Live Probes (G1)

Date: 2026-08-24
Probe method: bounded real reads through `connectors.github_research.GitHubResearchConnector`
(default transport = already configured `gh` CLI; token stays in OS keyring,
never enters process/logs/evidence).

## Authenticated identity

| Item | Observed value |
|---|---|
| Authenticated login | `Blemeh` |
| gh CLI | v2.92.0, git protocol https, token via keyring (value never read by JARVIS) |
| Token scopes (as reported by `gh auth status`) | `gist`, `read:org`, `repo`, `workflow` |
| Core rate limit at probe | 5000 limit / 4994 remaining after campaign (reset epoch recorded) |
| Campaign API cost | 21 calls total (bounded; well under quota) |

## Read capabilities (proven live)

| Capability | Action | Evidence |
|---|---|---|
| Repo metadata read | `github.repo.read` | `/repos/{3 repos}` COMPLETED, redaction-normalized |
| Commit/head read | `github.commit.read` | exact default-branch SHAs captured for 3 repos |
| Issue/PR read | `github.issue.read` | open-issue lists retrieved (all empty) |
| CI/check read | `github.check.read` | workflow runs + failing jobs/jobs-steps for Hoowen & Foundry |
| Release read | `github.release.read` | empty release lists + upstream pytest latest tag 9.1.1 |
| Search read | `github.search.read` | issue-tracker search total_count=0 for Hoowen CI |
| HTTPS docs read | `http.doc.read` | pypi.org JSON fetched; oversized source truncated honestly at 256 KiB cap |

Failure states proven: `http_404`, `timeout`/transport errors → FAILED attempt
with error_code (never silently swallowed); malformed JSON → `malformed_response`;
secret-like documents → canonical `redact_payload` normalization with
`redacted: true` evidence flag (raw values never persisted).

## Branch-write capability (proven on THIS lane's branch only)

See `DISCOVERY_PROOF.md` § G12. Summary:

```
push outcome            COMPLETED   (GitEffectAdapter exactly-once journal)
local head              e9dc56dd37b4...
remote head (ls-remote) e9dc56dd37b4...   == local head
reconcile after done    reconciled=True (no duplicate push)
replay same action_id   performed=False
main mutated            NO
```

## Refusals honored

- No push to main, no force push, no repo settings/protection changes.
- No public posting performed to prove API access.
- No token values printed or persisted anywhere in this lane's evidence.
