# JARVIS V12 Capability Solution Discovery — 2026-09-02

## VERDICT

`CAPABILITY_DISCOVERY_PROOF_COMPLETE` for the isolated candidate. This lane
discovers, normalizes, compares, and recommends solutions only. It does not
install, register, trial, authorize, mutate a provider, mutate live state, or
change the canonical checkout.

```text
BASE:                    candidate/v11-integration-base2-20260902
BASE SHA:                30998ef06d9be73adb30387c10a7fa72ef910d28
DEPENDENCY INPUT:        candidate/v11-capability-requirements-20260902
DEPENDENCY INPUT SHA:    aac5710248f2df9eda46c59694a0f43e1d71d6e5
BRANCH:                  candidate/v12-capability-discovery-20260902
WORKTREE:                C:\temp\JARVIS-V12-Capability-Discovery-20260902
FINAL SHA:               PENDING FINAL DOCS PIN
MAIN/ORIGIN MUTATED:    NO
LIVE MUTATED:            NO
```

The V11 dependency was ported semantically onto the exact base: the
`capability_requirements.py` implementation and tests came from
`54df76617f758d24206e65837ad8664f8a4961d7`; the dependency tip `aac5710...`
is a documentation reconciliation and was not wholesale merged over newer
base2 documentation.

## SOLUTION CONTRACT

`src/jarvis_foundry/capability_solution_discovery.py` defines versioned,
JSON-safe contracts:

- `CapabilityGapRequirement` — stable gap identity, requirement-set linkage,
  missing action/capability, bounded compatibility requirements, candidate
  identity, source, authority, and `DATA_ONLY` external-content policy.
- `SolutionCandidate` — frozen tier, locator/version, normalized coverage,
  compatibility, integration-cost estimate, maturity/maintenance evidence,
  provenance, disposition, and explicit rejection reasons. Mutation flags are
  hard-false and constructor validation rejects attempted install,
  registration, or execution authorization.
- `SearchReport` — stage, bounded inspected counts, candidate ids, evidence,
  errors, authority, and read-only state.
- `SolutionComparison` — deterministic ranked ids, component scores, reuse
  order, winner, and per-candidate rejection reasons.
- `SolutionRecommendation` — selected solution or honest deferral/no-solution,
  rationale, next step, and immutable discovery-only constraints.
- `DiscoveryRun` — durable requirement, all stage reports, normalized
  candidates, comparison, and recommendation.

Durability is event-sourced through `CapabilitySolutionStore` using additive
unknown-event-safe ledger records. Repeating identical inputs is idempotent;
changed requirements/candidates/comparisons/recommendations append a bounded
revision instead of overwriting history. Existing Fabric Catalog readiness is
the sole capability-readiness authority.

## FROZEN REUSE ORDER

The comparator enforces the requested order, with no custom implementation:

1. `FABRIC_CATALOG`
2. `JARVIS_SOURCE`
3. `JARVIS_GIT`
4. `EXTERNAL`
5. `CUSTOM_BUILD`

Coverage and compatibility are hard gates. A viable earlier tier wins even if
a later tier has a higher raw score; later candidates receive an explicit
`REUSE_ORDER_DEFERRED_TO:<winner>` reason. Custom is a comparison placeholder
only and is always marked last resort / no-trial-authorized in this lane.

## FABRIC SEARCH

`search_fabric_catalog()` reads `fabric.catalog.catalog_snapshot()` only. It
does not consult or write the legacy registry and does not register anything.
Exact action/capability matches are preferred; readiness must be `READY` and
unattended mode must be allowed. Verified readiness is represented as
`PROVEN`; unverified catalog presence remains `PRESENT_UNPROVEN` and cannot
win the comparison.

The disposable proof seeded the real built-in Fabric entries and recorded a
verified readback for `cap.artifact.pdf.copy`. The Fabric candidate was
selected before source/history/external options, proving tier-one closure.

## JARVIS SEARCH

`search_repository_source()` resolves the actual Git root, reads bounded
tracked `src`, `config`, and `pyproject.toml` files, hashes excerpts, and
returns current source-component candidates. Symlinks escaping the root,
oversized files, and untracked content are excluded. It is read-only and does
not execute source.

## GIT ARCHAEOLOGY

`search_git_archaeology()` is a real bounded implementation, separate from
`operations/reuse.py` (which is procedure-template reuse). It executes only
read commands against the repository:

```text
git rev-parse HEAD
git for-each-ref refs/heads refs/remotes
git worktree list --porcelain
git log --all --grep
git log --all -G
git show --name-only
git grep
```

It records current head, refs, worktrees, commit subjects/dates, changed paths,
selected branch tips, matched lines, bounded limits, and
`operations_reuse_used: false`. Historical candidates are clearly
`HISTORICAL`/`UNKNOWN` for current readiness and can never impersonate a
registered capability.

## EXTERNAL SEARCH

`search_external_solutions()` uses the existing read-only
`GitHubResearchConnector` and its configured `github.search.read` /
`github.repo.read` actions. No new provider client, credential path, or
provider write was added. Repository metadata and descriptions are evidence
only; every external candidate is forced to authority `DATA_ONLY`, and
research claims contain a bounded observational statement rather than remote
instructions.

For the MCP gap the live bounded search returned five repositories. The
highest-ranked observed candidate was `github:modelcontextprotocol/python-sdk`
(MIT, Python, 24,189 stars, 3,862 forks, recent activity); metadata is
point-in-time research evidence and still requires an owner-authorized trial
and independent verification before any integration.

Provider failure, rate limiting, malformed data, or empty results are returned
as `BLOCKED`/`EMPTY`; they never become a fabricated candidate or a PASS.

## COMPARISON

Each candidate receives component scores for tier priority, capability
coverage, compatibility, maturity, maintenance, integration cost, and evidence
confidence. Hard rejection reasons include missing/partial coverage,
unproven/incompatible runtime, dependency/file limits, immature/unknown
maturity, non-`DATA_ONLY` external material, and the custom-build lane guard.

The proof produced these recommendations:

| Gap proof | Earlier-tier result | Recommendation |
| --- | --- | --- |
| `artifact.pdf.copy` | Verified Fabric Catalog entry | Existing Fabric capability (`RECOMMENDED`) |
| `mcp.client.invoke` | No current Fabric/source/history solution passed gates | `github:modelcontextprotocol/python-sdk` (`RECOMMENDED`, external `DATA_ONLY`) |
| `windows.uia.click` | Current source/history candidates remain unproven | Historical evidence only; no execution recommendation |

The MCP comparison explicitly deferred the custom placeholder and weaker
external repositories, preserving the frozen order and recording reasons.

## REAL GAP PROOFS

Evidence file:
`evidence/v12-capability-discovery/proof.json`

Evidence SHA-256 at capture:
`13eeab0c8acdf2f059f8d6a333ccb92a4d926038eb56ba68cc166a9467e4a7d5`

The disposable proof used three concrete gaps and the real checkout head
`b6b7d7920f1cbe6037a116ae1a2c6a4106cbfb60`:

- Fabric PDF: real built-in `cap.artifact.pdf.copy`, disposable verified
  readback, Fabric recommendation, no registration event.
- MCP client: no Fabric mapping, existing source/history search, live
  read-only GitHub research, five external repositories, official Python SDK
  recommendation, all external rows `DATA_ONLY`.
- Windows UIA: bounded archaeology observed 32 refs, 32 worktrees, 32 commits,
  32 branch heads, and 24 file matches through real Git commands; no current
  readiness or install claim was made.

Assertions in the proof are all true: Fabric selection, external provider
completion, external candidate observation, Git refs/worktrees/commands,
`DATA_ONLY` external content, no install/register/execution authorization, and
no provider writes.

## TESTS

The focused suite covers durable requirement round-trip/idempotency and
revision replay, requirement-set gap projection, Fabric-first selection,
bounded source and real Git archaeology, injected GitHub research through the
existing connector, external `DATA_ONLY` normalization, explicit rejection
reasons, service persistence/replay, and mutation guards.

```text
python -m py_compile src/jarvis_foundry/capability_solution_discovery.py scripts/v12_capability_discovery_proof.py   PASS
uv run pytest tests/foundry/test_v11_capability_requirements.py tests/foundry/test_v12_capability_solution_discovery.py -q   PASS
uv run pytest tests/foundry/test_v12_capability_solution_discovery.py -q   PASS (8 tests)
python scripts/v12_capability_discovery_proof.py   CAPABILITY_DISCOVERY_PROOF_COMPLETE
```

The broad Foundry suite and documentation staleness check remain to be run
after this implementation checkpoint. No test result here is a production
trust or live-runtime claim.

## BOUNDARIES / NEXT

This candidate does not install packages, create worktrees, register Fabric
capabilities, call provider writes, authorize effects, or mutate live/main/
origin. External freshness, license approval, adapter behavior, and
independent verification remain open gates. The next authorized action is an
owner-approved trial in a separate lane; this discovery lane itself grants no
trial or promotion authority.
