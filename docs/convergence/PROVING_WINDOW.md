# Post-Promotion Proving Window

The exact current canonical executable/source record is `c60db92` (semantic
source `93d23f5`) and remains rollback-compatible with the previous runtime
source `f37f852` via `convergence-pre-runtime-c60-20260819`. The active R3
window is `post-canonical-c60db92-real-20260819-r3`; the earlier f37f852 R2
window is preserved as `SUSPENDED` history.
The proving window is intentionally
separate from the engineering gates: a green test suite or a heartbeat does
not compress seven days into a test.

`scripts/convergence/monitor_proving_window.py` is a read-only monitor.  It
requires an explicit release-bound manifest containing the real `started_at`
timestamp, reads the live SQLite database with `mode=ro` and `query_only=ON`,
and atomically writes one checkpoint.  It never appends events, invokes a
provider, changes capabilities, or claims voice, external connector, or host
fault proof.

Checkpoint statuses are deliberately honest:

- `WAITING_ELAPSED` — the runtime is healthy but the real duration has not yet
  elapsed;
- `WAITING_HEARTBEAT` — continuity evidence is insufficient or a gap exceeds
  the manifest tolerance;
- `WAITING_OWNER_STOP` — an active `STOP_EVERYTHING`, `PAUSE`,
  `VETO_ACTION`, or `STOP_MISSION` directive is visible; revoked directives
  never block;
- `WAITING_SUPERVISOR` — the singleton Supervisor is not RUNNING;
- `BLOCKED_INTEGRITY` — required tables or SQLite integrity are not healthy;
- `READY_TO_CLOSE` — duration and local continuity criteria are satisfied,
  pending independent adjudication.

The example manifest is a template only.  The current real manifest is
release-bound to c60db92 and lives under the runtime evidence directory. The
monitor's output belongs under the runtime evidence directory
(for example `%LOCALAPPDATA%\\hermes\\foundry\\evidence\\proving-window`) so
scheduled checkpoints do not dirty the source checkout.
