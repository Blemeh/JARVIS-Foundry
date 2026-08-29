# V9 Capability Fabric checkpoint

SHA: (update after commit)
Branch: `candidate/v9-capability-fabric-20260830`
Worktree: `C:\temp\JARVIS-V9-Capability-Fabric-20260830`
Base: `84db5d338313d835b2540796dbed24489646ceae`

## Completed

- Event-sourced capability catalog. Registration is not readiness. UNKNOWN stays UNKNOWN. READY requires verified SUCCESS.
- INTENT loop: select → execute via existing connectors/tools → verify actual effect → remember outcome.
- NEED loop: detect missing action, specify gap, write candidate under owned root, focused test, reject or register, retry.
- Provider routing: OpenCode contributor models are INTERACTIVE_REQUIRED without Hermes noninteractive consent. No silent LLM substitution.
- Real actions: filesystem, git commit, GitHub read/identity/issue-create (injected write), system diagnose, pytest, owned-root script, research→artifact workflow.
- Ten required workflows proven in `tests/foundry/test_v9_capability_fabric.py`.

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
- Wire `github.search.read` / check / release through the same research executor.
- Honest email/calendar if a real interface exists; otherwise leave UNKNOWN.

## Blockers

- None for the fabric core. Live production remains untouched.
