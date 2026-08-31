# JARVIS post-V9 to V16 roadmap handoff

Status: owner-supplied governing context for future development; imported as
repository memory on 2026-09-01. This document is not evidence that any live
mutation, promotion, merge, or V10 implementation occurred.

Canonicalization update (2026-09-01): the owner separately authorized landing
this roadmap plus the agent-control documentation to canonical `main`. That
authorization does not promote the `c41a3ad...` product candidate, start V10,
or permit live runtime/database/Scheduler/install/release/trust mutation.

Source: `C:\Users\b\Downloads\JARVIS_MASTER_HANDOFF_POST_V9_TO_V16_2026-08-31.txt`

Source SHA-256: `91105DC4D235DCAD4E698E495D1BDE4CE54C753E871ECD5F639F9A4462C44089`

Source baseline date: 2026-08-31

Repository base used for this import:
`c41a3ad8ef3debda6f84e6d4dac2e0ac525c0864`
(`candidate/v9-stabilization-20260831` at import time).

## Interpretation boundary

The source handoff contains instructions to future coding agents. The initial
2026-09-01 request authorized only reading, analysis, and memory preservation;
a later 2026-09-01 directive authorized documentation/control-plane
canonicalization. Neither directive by itself authorizes product-phase or live
operational instructions from the source handoff.

The handoff's Git, release, runtime, schema, process, trust, provider, reboot,
and promotion statements are point-in-time claims. Before any present-tense
claim or operational action, re-run the relevant read-only orientation and
live checks. In particular, never infer whether `CURRENT` is
`5167e2cbaa5df3c1ad00b660138a5a8b6b2bd1bd` or
`c41a3ad8ef3debda6f84e6d4dac2e0ac525c0864` from this document.

The stabilization product SHA is `c41a3ad...`. The separate post-V9 promotion
rehearsal tooling repair SHA is
`53d581392195e698e9d922e9fb65b95e82c8c4ef`; do not substitute the tooling SHA
for the product SHA.

Trust is not restored by this handoff. Preserve truthful states such as
`SUSPENDED`, `UNKNOWN`, `NOT_TESTED`, `PENDING`,
`BLOCKED_BY_PROVIDER_POLICY`, and `OWNER_APPROVAL_REQUIRED` until the required
live evidence exists. Never manually mark the system `TRUSTED`.

## Product doctrine

The post-V9 product loop is:

```text
UNDERSTANDING
  -> AGENCY
  -> ABILITY
  -> EVIDENCE / VERIFICATION
  -> WORLD MODEL UPDATE
  -> CONTINUE, WAIT, RETRY, REPAIR, ESCALATE, OR REPRIORITIZE
```

Preserve one production spine:

- World Intelligence understands and learns.
- Continuous Executive owns durable objectives, continuation, waiting,
  retry, escalation, and owner directives.
- Capability Fabric selects and composes verified abilities.
- The existing mission, worker, evidence, and verifier path records and proves
  effects.
- Supervisor remains the sole runtime authority.
- The event ledger remains the source of truth.
- Command Center remains a truthful projection, not another authority.

Do not introduce a competing mission loop, owner-control system, scheduler,
world model, capability registry, event authority, verifier, release
authority, or runtime owner unless the canonical implementation is proven
incapable of the requirement.

For each requested capability, use this order:

1. Reuse or extend what JARVIS already has.
2. Recover a still-valid implementation from accepted JARVIS history.
3. Integrate a mature compatible library, tool, API, SDK, MCP server, or
   external project.
4. Build a thin adapter around an existing solution.
5. Build custom functionality only when the earlier options are unsuitable.

Optimize for real capability gained per engineering hour, not custom code
volume, test count, document count, or architecture layers. Keep practical
owner-harm protections, STOP authority, effect controls, secret protection,
database integrity, duplicate-action prevention, and truthful verification;
do not turn the roadmap into generic enterprise security or compliance work.

## Inventory and proof law

Every major phase begins with a source/history/integration inventory and gap
map. Classify desired behavior as:

- `DONE`
- `PARTIAL`
- `PRESENT_BUT_NOT_WIRED`
- `PRESENT_BUT_UNPROVEN`
- `MISSING`
- `SUPERSEDED`
- `SHOULD_INTEGRATE_EXTERNAL_TOOL`

The inventory determines 2-4 non-overlapping build lanes. Use isolated
worktrees, record exact base and final SHAs, preserve dirty/protected work,
and converge by semantics rather than blind branch merges. Normal build lanes
must not mutate live production. Merge, push, promotion, trust changes, live
database changes, Scheduler changes, or release-pointer changes require a
separate active authorization and proof context.

Testing should answer whether the capability works, creates the intended
effect, verifies that effect, survives failure, resumes/retries/repairs,
preserves durable state, integrates with the canonical spine, avoids material
regressions, and performs acceptably. Focused lane tests are appropriate
during development; broad regression belongs at meaningful convergence and
promotion boundaries. Unit tests or fixture success alone do not prove real
end-to-end behavior.

Live UI and reports must expose backend truth. `UNKNOWN`, `UNAVAILABLE`,
`NOT_TESTED`, `SUSPENDED`, and `BLOCKED` are valid and preferable to fabricated
health, progress, missions, memory, verifier state, or telemetry.

## Fixed roadmap

| Version | Mission | Required proof shape |
| --- | --- | --- |
| V9 | Core autonomous loop: Understanding + Agency + Ability | Existing V9 spine and effect-learning loop remain canonical. |
| V10 | Real-world capability expansion | One practical objective composes browser/research, files/docs/repos/apps, code or application actions, a real effect, verification, evidence, world update, and continuation/reporting. |
| V11 | Long-horizon autonomy | Multiple objectives include immediate work, waiting, a deadline, a dependency, an injected failure, and a changed condition; JARVIS resumes, reprioritizes, repairs, and interrupts the owner only when necessary. |
| V12 | Self-expansion / self-improvement | A missing ability is detected, discovered or integrated (preferably externally), boundedly tested, independently verified, registered, and used to finish the original objective. |
| V13 | Personal executive intelligence | From real projects, commitments, deadlines, and events, JARVIS identifies what matters, explains why, begins authorized preparation, and avoids irrelevant interruption. |
| V14 | Full computer / digital operator | One complex objective crosses applications/services without prompt shuffling, verifies important effects, recovers from an interruption, and completes or truthfully escalates. |
| V15 | Final human interface | Natural voice/text interaction in Albanian and English, interruption, proactive briefings/notifications, truthful Command Center inspection, and polished UX. |
| V16 | Final convergence / JARVIS 1.0 | No major new feature program: reconcile V9-V15, remove duplicates, prove realistic end-to-end scenarios, restart/reboot/provider recovery, long-duration operation, concurrency, rollback, packaging, and production release. |

Do not rename, reorder, or invent versions beyond this roadmap without a new
owner direction. Voice and bilingual UX are intentionally deferred to V15.

## Phase emphasis

- V10: inventory filesystem, shell/system, Git/GitHub, HTTP/search/browser,
  documents/spreadsheets/presentations/PDF/images, code/test/package tooling,
  Windows apps, APIs, email/calendar/messaging, research/scraping/deployment,
  databases, notifications, MCP, external ecosystems, and current procedures.
  Likely lanes are browser/web/research; computer/files/local apps;
  developer/repo/build/deploy; and typed multi-tool workflows with effect
  verification. These are proposals until the inventory proves the gaps.
- V11: finish existing durable foundations for project lifecycle, time and
  event resumption, portfolio/concurrency, follow-up, replanning, and bounded
  owner interruption; do not rebuild the executive.
- V12: use the existing V6/V9 gap and self-evolution machinery for discovery,
  acquisition, thin adaptation, verification, registration, and strategy
  learning; self-expansion is not permission for endless self-rewrite.
- V13: extend the world model, memory, directives, objectives, predictions,
  salience, ranking, and briefings into a durable owner/project/commitment
  model and proactive executive behavior; do not create generic chatbot
  memory.
- V14: compose V10 abilities across browser, Windows applications,
  communications/services, and cross-app workflows with practical authority,
  reversibility, duplicate-action prevention, and postcondition checks.
- V15: add voice, Albanian/English, notifications, concise briefings, and final
  interaction design only after the operator foundation is strong.
- V16: inventory all accepted outputs, remove stranded or superseded work,
  reconcile one product, clean up performance, and prove actual operation.

## Immediate future entry gate

Before V10 implementation:

1. Verify the actual live stabilization state read-only: `CURRENT`, `PREVIOUS`,
   schema, runtime SHA, release authority, Supervisor singleton, health,
   parity, and reconciliation.
2. If the stabilized V9 product is live, confirm basic post-promotion health
   and performance; otherwise do not infer permission to promote it from this
   handoff alone.
3. Keep production proving separate from isolated future development. A real
   source defect found during proving must be addressed; elapsed-time-only
   proving should not prevent safe isolated inventory work.
4. Run one V10 inventory and gap-map session before generating or starting
   implementation lanes.
5. The gap map must identify existing/ready/unwired/partial capabilities,
   reusable accepted history, mature external integrations, what truly must be
   built, the cleanest non-overlapping lanes, and one end-to-end V10 proof
   scenario.

## Durable lane handoff contract

Each major lane reports: verdict; owned scope; branch/worktree; exact base and
final SHAs; existing components reused; external integrations; changes;
real-world proof; focused and relevant regression results; limitations; what
was intentionally untouched; and the next convergence action. A compact,
truthful report is preferred to a large documentation pack.

## Review notes and recommended refinements

These are analysis recommendations, not owner-approved changes to the fixed
roadmap. They can be accepted, modified, or rejected without renaming or
reordering V10-V16.

1. Define 2-3 "golden journeys" before broad V10 expansion. A capability
   inventory can otherwise become a catalog-building exercise. Choose a small
   set of owner-valued workflows that force browser/research, local artifacts,
   code or application work, external effects, verification, and continuation;
   use them as the recurring regression spine through V16.
2. Make the V10/V14 boundary explicit. V10 should prove individual abilities,
   typed composition, receipts, and postconditions in bounded workflows. V14
   should prove durable cross-application operation under changing UI state,
   interruptions, partial completion, and recovery. This prevents duplicate
   browser/desktop implementations in both phases.
3. Add measurable phase budgets. Each phase should declare latency, task
   success, verified-effect rate, duplicate-effect rate, recovery time,
   unattended duration, cost/resource, and owner-interruption budgets. The
   exact targets come from inventory baselines; the important addition is that
   exit decisions become measurable rather than narrative.
4. Add a versioned capability lifecycle. Every internal or external capability
   should have provenance, version, license/terms note, install source and
   integrity record, permissions/effect class, credential needs, health,
   verification history, rollback/uninstall path, and replacement/retirement
   status. This is practical reliability for the integration-first doctrine,
   not enterprise compliance.
5. Resolve the autonomy/irreversible-effect policy before V14. Repository
   standing autonomy and the handoff's owner-authorization/approval-boundary
   language can diverge for messages, purchases, publication, deletion,
   account changes, and other consequential effects. Define deterministic
   effect tiers for reversible execution, prepare-and-present, explicit
   confirmation, and prohibited actions while preserving immediate STOP.
6. Make interruption and compensation first-class. Cross-app workflows need
   durable checkpoints, idempotency keys, duplicate-effect reconciliation,
   partial-effect detection, and compensating actions where reversal is
   possible. "Retry" alone is unsafe once an external effect may already have
   happened.
7. Add degraded/offline behavior as a product feature. JARVIS should state
   which abilities remain usable without a provider, browser session, network,
   or third-party API; queue resumable work without busy-looping; and preserve
   local owner control and truthful status.
8. Give V13 an explicit memory correction and forgetting path. The personal
   executive model should distinguish owner assertions, observations,
   inferences, and predictions; support corrections, expiry, supersession,
   project scoping, and selective deletion/export; and prevent a low-confidence
   inference from silently becoming a durable owner fact.
9. Build a frozen scenario/evaluation corpus. Retain sanitized inputs, expected
   invariants, effect receipts, verifier contracts, and failure injections for
   the golden journeys. This provides comparable evidence across versions
   without mistaking mocks for live proof.
10. Treat backup, export, and machine recovery as product abilities. Before
    JARVIS 1.0, prove that the owner's world model, objectives, capability
    metadata, evidence, configuration, and secrets references can be backed
    up, restored, and moved without corrupting authority or leaking secrets.

The highest-value immediate refinement is to pair the V10 inventory with
golden journeys and measurable budgets. That converts a very broad capability
phase into a decision-driven build while preserving the document's
integration-first intent.
