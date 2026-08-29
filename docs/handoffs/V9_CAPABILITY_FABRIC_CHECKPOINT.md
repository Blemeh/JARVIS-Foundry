# V9 Capability Fabric checkpoint

SHA: `9f8f8ffd96f643782dcb109ec5f681c718877109`
Branch: `candidate/v9-capability-fabric-20260830`
Worktree: `C:\temp\JARVIS-V9-Capability-Fabric-20260830`
Base: `84db5d338313d835b2540796dbed24489646ceae`

## Completed

- Event-sourced capability catalog. Registration is not readiness. UNKNOWN stays UNKNOWN. READY requires verified SUCCESS.
- INTENT and NEED loops with owned-root candidate generation.
- Provider routing: OpenCode contributor models are INTERACTIVE_REQUIRED without Hermes noninteractive consent.
- Real actions: filesystem, dir.list, git inspect/commit, GitHub read family, issue create via explicit transport, system diagnose, pytest, owned-root script, research artifact.
- `jarvis --home <disposable> fabric catalog` lists actual capabilities. Live home is refused.
- Ten required workflows plus CLI/dir/git-inspect proofs in `tests/foundry/test_v9_capability_fabric.py`.
- V7 `capability_readiness` reports missing records as UNKNOWN and not ready. Procedure eligibility fails closed. The procedure step gate still only refuses STOPPED/VETOED/DEGRADED.

## Decisions

- Compose existing connector registry, ActionEnvelope, and effect journal. Do not replace them.
- Catalog uses append-only fabric events. No schema bump.
- Generated tools never write into `src/`.
- Email/calendar remain UNKNOWN. Fake in-memory providers are not registered as READY.

## In progress

- Deeper GitHub write only through explicit transport.
- More owned-root local tools if they earn their place.

## Next gaps

- GitHub mutating actions against a disposable repo only when explicitly targeted.
- Compose fabric routing with `roles/router.route_provider_candidates` if that removes duplication without a second authority.
- Honest email/calendar if a real interface exists; otherwise leave UNKNOWN.

## Blockers

- None for the fabric core. Live production remains untouched.
