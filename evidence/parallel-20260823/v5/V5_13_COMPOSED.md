# V5-13 Multi-Connector Composed Mission Harness

Verdict: PASS in the disposable three-connector environment.

## Mission path

```text
local HTTP observation (/state)
        -> candidate value
        -> owned local Git commit + bare-remote push
        -> structural browser action (/action)
        -> append-only lineage for all three connector requests
```

`ComposedMissionHarness` only sequences the existing HTTP, Git, browser, and
lineage contracts. It does not become a release controller or a second effect
journal. STOP and veto checks call the existing `emergency_stop_reason` and
`matching_veto_reason` gates before every external step.

## Disposable proof

- One mission completes across HTTP, Git, and browser and records three
  lineage ids under the same mission id.
- Re-running the same mission returns `REPLAYED`; the browser provider records
  one action, the remote ref has one new commit, and only one composed
  completion event exists.
- A lane STOP marker and an authenticated `VETO_ACTION` both block before
  connector mutation.
- Closing the HTTP fixture blocks the composed mission, while an unrelated
  lane-owned Git commit/push still completes against the same disposable bare
  remote.

## Verification

```text
python -m pytest -vv tests/foundry/test_v5_composed_mission.py
3 passed in 10.59s

python -m pytest -q tests/foundry/test_v5_composed_mission.py \
  tests/foundry/test_connector_safety.py tests/foundry/test_http_connector.py \
  tests/foundry/test_browser_connector.py tests/foundry/test_communication_contracts.py \
  tests/foundry/test_connector_operations.py tests/foundry/test_connector_lineage.py \
  tests/foundry/test_connector_resources.py tests/foundry/test_filesystem_connector.py \
  tests/foundry/test_git_connector.py tests/foundry/test_connector_contract.py \
  tests/foundry/test_connectors.py tests/foundry/test_effects.py \
  tests/foundry/test_correlation.py
exit 0
```

No production endpoint, repository, account, credential, scheduler, or live
provider was used.
