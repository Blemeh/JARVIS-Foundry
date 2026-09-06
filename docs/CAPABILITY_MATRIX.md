# JARVIS Foundry Current Capability Matrix

Authoritative capability status for the canonical source candidate reconciled
on Sunday, September 6, 2026.

Allowed states:
`PROVEN_CURRENT`, `IMPLEMENTED_UNPROVEN`, `PARTIAL`, `PLANNED`,
`BLOCKED_EXTERNAL_DEPENDENCY`, `DEFERRED_BY_OWNER`, `HISTORICAL`,
`SUPERSEDED`, and `UNKNOWN`.

## Truth anchors

| Surface | Current truth |
|---|---|
| Promotion base | local `main` = `origin/main` = `2905c206fed0461bf3085bc0cb43773be913300a` before Prompt 2 promotion |
| Canonical promotion branch | `candidate/v13-canonical-source-promotion-20260906` |
| Frozen V13 final input | `d58debf04c6f53a11dcfb071f999b5349f01c9b1`, tree `97ff027f908202e84d2ccb11d8e3f73ffa5cefde` |
| Canonical product source | `1002e9919258d8ded33d125cd579bbdff7091f3a`, tree `3ff1757aabd778a25b1cabc7a96945b942ed0d34` |
| Candidate schema | v40, one additive migration chain |
| Live release | V10 `faa861c67399d5a3755d778224ff65513bd933a1`, release `rel-faa861c67399-5afeb024`, schema v38 |
| Production trust | `SUSPENDED`; source promotion is not live activation |
| Reconciliation | 0 stranded accepted source deltas; 0 material UNKNOWN items |

## Authority spine

| Capability | Canonical implementation | State | Boundary |
|---|---|---|---|
| Runtime ownership | one Supervisor, lease, Scheduler ownership, STOP and recovery controls | `PROVEN_CURRENT` | Prompt 2 does not retarget or restart live runtime |
| Owner authority | owner context, owner directives, pause/redirect/STOP | `PROVEN_CURRENT` | standing orders cannot dispatch outside the Executive |
| Continuous Executive | one objective/campaign prioritization and continuation authority | `PROVEN_CURRENT` | no alternate initiative or campaign engine |
| Mission execution | canonical `MissionLoop` and worker bridge | `PROVEN_CURRENT` | no second mission runtime |
| Capability readiness | Fabric Catalog | `PROVEN_CURRENT` | sole writable readiness authority |
| External effects | `effects.py`, Fabric execution, evidence and reconciliation | `PROVEN_CURRENT` | ambiguous dispatch reconciles before retry |
| Acceptance | independent verifier and AcceptanceGate | `PROVEN_CURRENT` | builders do not self-certify |
| Provider routing | canonical provider registry/router | `PROVEN_CURRENT` | real production provider proof remains blocked |
| Command Center | ledger-backed read projection and canonical control transport | `PROVEN_CURRENT` | UI is not an Executive, Catalog writer, or effect authority |

## Personal executive and work

| Capability | Current implementation | State | Boundary |
|---|---|---|---|
| Owner Context | durable profile, references, standing orders, projection | `PROVEN_CURRENT` | data and policy only; execution remains subordinate |
| Delegation | intent admission, calibration, inference, projection, service | `PROVEN_CURRENT` | Executive owns objectives and plans |
| AI Workforce | bounded workers, recovery, steering, suitability, worktrees | `PROVEN_CURRENT` | one worker authority under MissionLoop/Fabric |
| General Work | files, ledger, materialization, planning, verification, Fabric bridge | `PROVEN_CURRENT` | composition does not become a second campaign authority |
| Personal Operator | governed local personal-work capabilities behind Fabric | `PROVEN_CURRENT` | no direct unjournaled effects |
| Computer Operator | browser/Windows operation with effect identity and ambiguity handling | `PROVEN_CURRENT` | no blind retry after ambiguous dispatch |
| World Intelligence | research, dossier, decision support, handoff | `PROVEN_CURRENT` | advisory only |
| Chief of Staff | project and briefing projection | `PROVEN_CURRENT` | advisory only; strongest repaired implementation selected |
| DOCX/PDF/XLSX/CSV artifacts | bounded creation, update, verification and readback | `PROVEN_CURRENT` | disposable/source-backed proof only |
| PPTX artifacts | create, update, copy and readback code retained | `IMPLEMENTED_UNPROVEN` | V13 PowerPoint proof was omitted by owner direction; capability was not removed |
| Gmail read/send | connector code retained | `DEFERRED_BY_OWNER` | not authenticated, probed, enabled, or required |
| Gmail observation | disabled | `DEFERRED_BY_OWNER` | no observation authority |
| Voice and EN/SQ localization | partial historical surfaces only | `DEFERRED_BY_OWNER` | no end-to-end current proof |

## Capability expansion and operations

| Capability | Current implementation | State | Boundary |
|---|---|---|---|
| Capability discovery | reuse-first gap, search, comparison and recommendation records | `PROVEN_CURRENT` | discovery does not install or activate |
| Artifact trial/adoption | canonical artifact trial, independent verification, probation, disable/restore | `PROVEN_CURRENT` | old `trial.py`/`installed_adapter.py` authority is superseded |
| Adapter factory | typed adapter model, builder, runtime and worker bridge | `PROVEN_CURRENT` | Catalog remains readiness authority |
| MCP backend | one `FabricMcpBackend` with durable adoption and cleanup | `PROVEN_CURRENT` | no adapter-private MCP runtime |
| Campaign fault/recovery | bounded children, joins, waits, provider fallback, STOP/restart | `PROVEN_CURRENT` | build-ahead proof is not production trust |
| Irreversible effects | stop-before zero effect; ambiguous-after reconcile | `PROVEN_CURRENT` | public consequential writes remain unproved unless separately authorized |
| Disposable migrations | v34 through v40 additive chain, reopen/no-op and integrity checks | `PROVEN_CURRENT` | live database remains v38 and untouched |
| Immutable release packaging | source-bound wheel/sdist and isolated install path | `PROVEN_CURRENT` | Prompt 2 verifies source packaging, not a live release |
| V13 live activation | deployment mechanisms exist | `IMPLEMENTED_UNPROVEN` | not authorized or exercised |
| V11 real A3 provider gate | real provider path fails closed | `BLOCKED_EXTERNAL_DEPENDENCY` | provider HTTP 500 evidence is not converted to success |
| V11 48-hour proving gate | guarded proving runner exists | `PLANNED` | elapsed run is `NOT_STARTED` and cannot precede provider recovery |
| Production trust restoration | owner-governed trust gates | `BLOCKED_EXTERNAL_DEPENDENCY` | remains `SUSPENDED` |
| Distributed multi-machine operation | no current implementation | `PLANNED` | later roadmap work |

## Historical and superseded

| Item | State | Disposition |
|---|---|---|
| V10 live release and rollback evidence | `HISTORICAL` | remains the current live boundary until separately authorized activation |
| Frozen V13 proof/evidence tip | `HISTORICAL` | retained unchanged as source-bound evidence |
| Old V12 capability-trial prototype authority | `SUPERSEDED` | canonical artifact trial/adoption is stronger |
| Alternate V13 General Work branches | `SUPERSEDED` | selected Executive-bound implementation is canonical |
| Original remaster parallel control plane | `SUPERSEDED` | useful behavior was absorbed into the single authority spine |
| Failed pre-repair V13 package verifier run | `HISTORICAL` | exact blob predicate was preserved and the package was rebuilt correctly |

No material accepted capability is `UNKNOWN` at this reconciliation. Historical
evidence is not rewritten, and no dirty worktree is consumed as canonical
source.
