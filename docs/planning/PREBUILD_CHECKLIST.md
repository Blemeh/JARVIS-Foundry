# Autonomous Build Launch Checklist

Status: WP0 PASS - WP1 ACTIVE - REMAINING ITEMS OWNED BY THEIR WORK PACKAGES

This checklist replaces assumptions with evidence. After `START BUILD`, JARVIS
completes it autonomously and advances when the gate passes. No item requests
routine owner permission.

## A. Launch And Control

- [x] `START BUILD` event is durably recorded.
- [ ] Standing autonomy mandate hash is recorded.
- [ ] Current owner goals, vetoes, constraints, and STOP state are loaded.
- [x] Deterministic STOP mechanisms are designed before runtime activation.
- [x] Build continuation is automatic after each PASS.
- [x] Reports inform the owner and do not become approval prompts.

## B. Repository And Source Reality

- [x] Exact Hermes path, commit, branch, remotes, and dirty state are observed.
- [x] Repository instructions are read.
- [x] Selected baseline tests run before Hermes changes.
- [x] Live Hermes is separated from candidate development.
- [x] JARVIS created the isolated `.h` development checkout without overwriting user changes.
- [x] Remote Git behavior is registered; `.h` push is disabled.

## C. Host Environment

- [x] Windows, CPU, memory, disk, and time zone are recorded.
- [x] Python, Node, Git, SQLite, and package tool versions are recorded.
- [x] `HERMES_HOME` and the existing Hermes startup mechanism are known.
- [ ] Junction, symlink, path case, lock, and atomic rename behavior are tested.
- [ ] Backup location and low-disk thresholds are established autonomously.

## D. Providers And Models

- [ ] Programmatic interfaces and authentication are verified from reality.
- [ ] Available models and role capabilities are enumerated dynamically.
- [x] ChatGPT Plus is not mistaken for API access.
- [ ] Vertex AI billing and quota are verified before a billable call.
- [ ] Initial Gemini accounting starts from the recorded USD 50 allocation.
- [ ] Builder and verifier diversity is used when technically available.
- [ ] Fake/offline adapters cover deterministic tests.

## E. Durable State

- [x] SQLite WAL behavior is verified on the staging filesystem.
- [ ] Supervisor is the single coordination writer.
- [x] Events and projections are transactional and rebuildable in WP1 staging.
- [x] Duplicate events and actions are idempotent in current tests.
- [x] Corrupt and incomplete writes have current test coverage; process-kill proof remains WP1 work.
- [ ] Mission, resource, capability, action, evidence, decision, veto, lease,
      event, briefing, and verdict schemas are reviewed.
- [x] Migration, backup, and restore exist for current staging state.

## F. Autonomous Action Integrity

- [x] ActionEnvelope schema uses goals, veto checks, readiness, risk, and
      recovery instead of owner grants.
- [ ] Resources are discovered and registered automatically.
- [x] Current target-resolution tests reject path escape.
- [ ] Capability states advance from DISCOVERED to READY through evidence.
- [ ] Credential broker avoids raw secret exposure.
- [ ] Retrieved content cannot forge owner directives or remove vetoes.
- [ ] Local, external, financial, and representational actions have
      consequence-scaled verification plans.
- [x] `NOT_READY` is a technical state, not a permission prompt.

## G. STOP And Recovery

- [x] Sentinel, CLI, hotkey/tray, and watchdog paths are defined.
- [ ] STOP works without Hermes, models, UI, or network.
- [ ] Freeze blocks dispatch, capabilities, promotion, and controllable workers.
- [x] Freeze is distinct from rollback; freeze occurs first.
- [ ] Restart reconstructs state deterministically.
- [ ] Database, Obsidian, project, external-effect, and release restoration are
      tested at the relevant capability stage.

## H. Verification

- [ ] Builder and final verifier use separate identities and sessions.
- [ ] Evidence artifacts have stable identities and hashes.
- [x] PASS, FAIL, BLOCKED, and NOT_TESTED are semantically fixed.
- [ ] G1-G9 and F1-F15 have executable ownership.
- [ ] Malicious instructions, concurrent edits, provider outage, expired auth,
      low disk, corrupt writes, path ambiguity, and false success are tested.
- [ ] Seven-day metrics and ten useful cycles are instrumented.
- [ ] PASS automatically activates continuation and queues V2.

## I. Automatic Go Decision

```text
START BUILD RECORDED                     YES
STANDING MANDATE ACTIVE                  YES
WP0 REALITY AND BASELINE                 PASS
WP1 DOMAIN AND EVENT STORE               ACTIVE / NOT_TESTED
WP2-WP8                                  INACTIVE / NOT_TESTED
V1 PROVING RESOURCE                      NOT_SELECTED
V1 PROVING MISSION                       NOT_SELECTED
BACKGROUND SUPERVISOR INSTALLED          NO
V1 TRUST                                 LAB
```

WP1 has started automatically after WP0 PASS. Unchecked items remain owned by
their specified work packages and cannot be inferred PASS from starter code.
FAIL triggers repair; a genuine unavailable dependency is BLOCKED. Neither
state becomes a routine permission request.
