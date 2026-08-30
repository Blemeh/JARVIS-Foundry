# V9 Capability Fabric checkpoint

SHA: `6e770f1e78f3fcc5fc0ac6bce45d8d02de3ed995`
Branch: `candidate/v9-capability-fabric-20260830`
Worktree: `C:\temp\JARVIS-V9-Capability-Fabric-20260830`
Base: `84db5d338313d835b2540796dbed24489646ceae`

## Completed

- Event-sourced capability catalog. Registration is not readiness. UNKNOWN stays UNKNOWN. READY requires verified SUCCESS.
- INTENT and NEED loops with owned-root candidate generation.
- Provider routing: OpenCode contributor models are INTERACTIVE_REQUIRED without Hermes noninteractive consent.
- Real actions: filesystem, dir.list, file.replace, git inspect/diff/branch/commit/push, GitHub read family, issue create via explicit transport, system diagnose, pytest, owned-root script, research artifact.
- Generic workflow composition: `$step.field` binding, stop or continue on failure. Combinations are data, not hardcoded paths.
- Mission spine adapter `execute_for_spine` journals `fabric.spine.result` evidence.
- Selection ranks by readiness then verified success count.
- `jarvis --home <disposable> fabric catalog` lists actual capabilities. Live home is refused.
- Workflows: issue→inspect→branch→commit→diff→test→receipt; diagnose→write→replace→run→list; commit→push to owned remote.
- V7 `capability_readiness` reports missing records as UNKNOWN and not ready.

## Decisions

- Compose existing connector registry, ActionEnvelope, and effect journal. Do not replace them.
- Catalog uses append-only fabric events. No schema bump.
- Generated tools never write into `src/`.
- Email/calendar remain UNKNOWN. Fake in-memory providers are not registered as READY.
- File write claims follow observed filesystem text, not Windows newline hash mismatches.

## In progress

- GitHub PR/write only through explicit transport.
- Deeper MissionLoop wiring without replacing the worker adapter.

## Next gaps

- GitHub mutating actions against a disposable repo only when explicitly targeted.
- Compose fabric routing with `roles/router.route_provider_candidates` if that removes duplication without a second authority.
- Honest email/calendar if a real interface exists; otherwise leave UNKNOWN.
- Browser/read remains disconnected until a real method exists.

## Blockers

- None for the fabric core. Live production remains untouched.
