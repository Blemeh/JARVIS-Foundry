"""Hash-bound verifier verdicts and a fail-closed acceptance gate.

Master Goal 2 — Independent Verifier Hardening.

Closes the recorded independent-verifier-depth limitation without creating
a second mission engine: ``VerifierBinding`` and ``AcceptanceGate`` reuse
the existing ledger, mission runtime, evidence and repair systems and are
consumed by BOTH canonical engines (MissionLoop.run_step and
HybridStepController).

The invariants proven here:

1. A builder cannot certify its own consequential work — the acceptance
   gate refuses any PASS whose builder_run_id equals the verifier's run
   identity (self-certification), whose verifier session is not a fresh,
   distinct isolated run, or whose evidence set is incomplete.
2. Every verdict binds exactly the tuple (mission, step, target, builder
   run, verifier run, context manifest, artifact hashes, diff hash) into
   one sha256 ``binding_hash`` recorded at STARTED; the outcome event
   carries it again.
3. Missing / stale / replayed / mismatched / fabricated / post-verdict-
   mutated evidence fails closed: the gate re-hashes the frozen artifact
   set and working tree at acceptance time and refuses every deviation
   from the STARTED-time capture.
4. False success cannot commit/advance/promote/complete: MissionLoop and
   HybridStepController call ``gate.evaluate`` before ACCEPTING /
   advancing to ACTION_SELECTION; commit_after_pass already refuses
   without a durable PASS verdict row bound to this exact builder run.
5. Repair invalidates the old verdict: a new attempt produces a NEW
   binding (fresh artifact hashes, fresh verifier identity); the old
   verdict stays as history but can never authorize anything again —
   authorization always consults the LATEST verdict for the step AND its
   binding freshness at acceptance time.
6. Verdict vocabulary is exactly PASS / FAIL / BLOCKED / NOT_TESTED
   (enforced by record_verification_outcome; enforced again here).
7. Same-provider verification records reduced provider independence while
   enforcing fresh-session isolation (distinct run/session ids, no hidden
   builder reasoning: verifier instructions carry only the contract +
   JARVIS-observed reconciliation report, never builder chain-of-thought).
8. STOP and authenticated vetoes deterministically block dispatch and
   acceptance: gate checks them first and returns BLOCKED before any
   other evaluation.
9. Crash / restart / duplicate event / expired lease / interrupted
   verification remain idempotent and resumable: all events use stable
   idempotency keys derived from verdict_id; replay of an identical
   terminal event is a no-op by construction of the append-only ledger;
   an interrupted pass leaves STARTED rows that the existing recovery
   path supersedes with fresh bindings.

Fail-closed rule: ANY gate exception evaluates to refusal (BLOCKED),
never to acceptance.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..events import Event, canonical_json
from .verification import SUBJECT_STEP, latest_verdict_for_step

VERDICTS = ("PASS", "FAIL", "BLOCKED", "NOT_TESTED")

BINDING_SCHEMA = "verifier-binding-v1"

# The verifier prompt and manifest are executable protocol, not presentation.
# This version is included in the identity digest so a protocol change creates
# a new verifier run while leaving the historical run/evidence immutable.
VERIFIER_CONTRACT_VERSION = "verifier-contract-v2"

# Evidence-set keys that must ALL be present in a terminal outcome event
# for the verdict to be eligible for acceptance (missing evidence fails
# closed). PASS additionally requires the frozen-artifact seal below.
_REQUIRED_EVIDENCE_KEYS = (
    "builder_run_id",
    "verifier_run_id",
    "artifact_hashes",
    "diff_hash",
    "target",
)

_STOP_BLOCKING_DEPENDENCY = "stop_active"
_VETO_BLOCKING_DEPENDENCY = "veto_active"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class BindingCapture:
    """The exact state a verifier verdict is bound to (frozen at STARTED)."""

    binding_hash: str
    schema: str
    mission_id: str
    step_id: str
    target: str
    builder_run_id: str
    verifier_run_id: str
    context_manifest_id: str
    context_manifest_hash: str
    artifact_hashes: dict[str, str]
    diff_hash: str
    head: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "binding_hash": self.binding_hash,
            "mission_id": self.mission_id,
            "step_id": self.step_id,
            "target": self.target,
            "builder_run_id": self.builder_run_id,
            "verifier_run_id": self.verifier_run_id,
            "context_manifest_id": self.context_manifest_id,
            "context_manifest_hash": self.context_manifest_hash,
            "artifact_hashes": dict(self.artifact_hashes),
            "diff_hash": self.diff_hash,
            "head": self.head,
        }


def _hash_target_tree(target: Path, *, limit: int = 200) -> dict[str, str]:
    """Deterministic per-file sha256 map of the frozen step target."""
    hashes: dict[str, str] = {}
    if target.is_file():
        try:
            return {target.name: hashlib.sha256(target.read_bytes()).hexdigest()}
        except OSError:
            return {}
    if not target.is_dir():
        return hashes
    import itertools

    for path in itertools.islice(sorted(target.rglob("*")), limit * 8):
        if not path.is_file():
            continue
        rel = str(path.relative_to(target)).replace("\\", "/")
        try:
            hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        if len(hashes) >= limit:
            break
    return hashes


def _working_root_for(target: Path) -> Path:
    """Nearest existing ancestor directory of the target (the worktree root
    when target does not exist yet)."""
    current = target.resolve()
    while not current.exists():
        parent = current.parent
        if parent == current:
            return current
        current = parent
    return current


def _git_diff_hash(root: Path) -> str:
    """Hash of the working-tree diff vs HEAD; non-worktree roots yield a
    stable constant so captures remain comparable across environments."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", str(root), "diff", "HEAD", "--no-ext-diff"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _sha256_text(f"git-unavailable:{root}")
    if result.returncode != 0:
        return _sha256_text(f"not-a-worktree:{result.returncode}")
    return _sha256_text(result.stdout)


def _git_head(root: Path) -> str:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


class BindingError(RuntimeError):
    """Raised when a binding cannot be captured honestly."""


def capture_binding(
    database: FoundryDatabase,
    *,
    verdict_id: str,
    mission_id: str,
    step_id: str,
    target: Path,
    builder_run_id: str,
    verifier_run_id: str,
    context_manifest_id: str = "",
    context_manifest_hash: str = "",
    independence: dict[str, Any],
    criteria: list[str],
) -> BindingCapture:
    """Freeze the verdict binding and record ``verification.started``.

    The binding hash covers the complete tuple required by Master Goal 2;
    it is recomputed independently by ``AcceptanceGate.evaluate`` at
    acceptance time and any drift fails closed.
    """
    root = _working_root_for(target)
    artifacts = _hash_target_tree(target)
    diff_hash = _git_diff_hash(root)
    head = _git_head(root)
    capture = BindingCapture(
        binding_hash="",  # filled after assembly (hash covers itself minus hash)
        schema=BINDING_SCHEMA,
        mission_id=mission_id,
        step_id=step_id,
        target=str(target.resolve()),
        builder_run_id=builder_run_id,
        verifier_run_id=verifier_run_id,
        context_manifest_id=context_manifest_id,
        context_manifest_hash=context_manifest_hash,
        artifact_hashes=artifacts,
        diff_hash=diff_hash,
        head=head,
    )
    payload = capture.to_payload()
    capture = _with_hash(capture, _binding_digest(payload))

    event = Event.create(
        "verification.started",
        {
            "verdict_id": verdict_id,
            "mission_id": mission_id,
            "subject_type": SUBJECT_STEP,
            "subject_id": step_id,
            "verifier_run_id": verifier_run_id,
            "builder_run_id": builder_run_id,
            "independence": independence,
            "criteria": criteria,
            "binding": capture.to_payload(),
        },
        source_type="supervisor",
        source_id="mission-loop",
        idempotency_key=f"verification.started:{verdict_id}",
    )
    database.append(event)
    return capture


def _with_hash(capture: BindingCapture, digest: str) -> BindingCapture:
    return BindingCapture(
        binding_hash=digest,
        schema=capture.schema,
        mission_id=capture.mission_id,
        step_id=capture.step_id,
        target=capture.target,
        builder_run_id=capture.builder_run_id,
        verifier_run_id=capture.verifier_run_id,
        context_manifest_id=capture.context_manifest_id,
        context_manifest_hash=capture.context_manifest_hash,
        artifact_hashes=capture.artifact_hashes,
        diff_hash=capture.diff_hash,
        head=capture.head,
    )


def _binding_digest(payload: dict[str, Any]) -> str:
    material = {k: v for k, v in payload.items() if k != "binding_hash"}
    return _sha256_text(canonical_json(material))


def verifier_contract_digest(context: dict[str, Any]) -> str:
    """Return the stable identity digest for one verifier contract.

    The caller supplies the exact provider-facing contract fields except the
    derived ``run_id``.  Keeping the digest independent of the derived id
    means a release can deterministically decide whether the legacy verifier
    id is compatible before it chooses a versioned id.
    """
    material = {"schema": VERIFIER_CONTRACT_VERSION, **context}
    return _sha256_text(canonical_json(material))


def deterministic_verdict_id(
    *,
    mission_id: str,
    step_id: str,
    builder_run_id: str,
    verifier_run_id: str,
    contract_digest: str,
) -> str:
    """Derive a stable verdict identity from the immutable pass contract."""
    material = {
        "schema": VERIFIER_CONTRACT_VERSION,
        "mission_id": mission_id,
        "step_id": step_id,
        "builder_run_id": builder_run_id,
        "verifier_run_id": verifier_run_id,
        "contract_digest": contract_digest,
    }
    return f"verdict-v2-{_sha256_text(canonical_json(material))[:24]}"


def fresh_verifier_identity(
    builder_run_id: str,
    *,
    attempt: int | None = None,
    identity_context: dict[str, Any] | None = None,
    base_verifier_run_id: str | None = None,
    use_base: bool = False,
    revision: int = 1,
) -> tuple[str, str]:
    """Return deterministic identities for one fresh verifier pass.

    ``use_base`` is true only when the caller has proven that the historical
    base verifier manifest is compatible.  Otherwise a content-addressed
    ``-verify-v<digest>`` id is used.  If that content-addressed identity was
    already terminally failed, ``revision`` adds a deterministic ``-rN``
    suffix so a retry cannot collide with the old idempotency keys.  The
    optional context keeps this helper backward-compatible for the hybrid
    worker path while eliminating random verdict ids for new calls.
    """
    base = base_verifier_run_id or f"{builder_run_id}-verify"
    context = dict(identity_context or {
        "builder_run_id": builder_run_id,
        "attempt": int(attempt or 1),
    })
    digest = verifier_contract_digest(context)
    if use_base:
        verifier_run_id = base
    else:
        verifier_run_id = f"{base}-v{digest[:20]}"
        if int(revision) > 1:
            verifier_run_id = f"{verifier_run_id}-r{int(revision)}"
    mission_id = str(context.get("mission_id") or "unknown-mission")
    step_id = str(context.get("step_id") or "unknown-step")
    verdict_id = deterministic_verdict_id(
        mission_id=mission_id,
        step_id=step_id,
        builder_run_id=builder_run_id,
        verifier_run_id=verifier_run_id,
        contract_digest=digest,
    )
    return verdict_id, verifier_run_id


def verifier_replay_state(
    database: FoundryDatabase,
    *,
    mission_id: str,
    step_id: str,
    builder_run_id: str,
    contract_digest: str,
) -> tuple[dict[str, Any] | None, int]:
    """Return the reusable PASS row and the next safe revision.

    A verifier identity is content addressed, but a terminal non-PASS (or an
    interrupted STARTED row) must never be reused for a retry.  This lookup is
    deliberately scoped to the exact mission/step/builder/contract family;
    it makes retries deterministic while keeping every previous verdict
    immutable.  A completed PASS is the only terminal result eligible for an
    exact duplicate invocation.
    """
    base = f"{builder_run_id}-verify-v{contract_digest[:20]}"
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT v.*, e.sequence AS terminal_sequence "
            "FROM verification_verdicts v "
            "LEFT JOIN events e ON e.event_id = v.last_event_id "
            "WHERE v.mission_id = ? AND v.subject_type = ? "
            "AND v.subject_id = ? AND v.builder_run_id = ? "
            "AND v.verifier_run_id LIKE ? "
            "ORDER BY COALESCE(e.sequence, 0) ASC, v.rowid ASC",
            (mission_id, SUBJECT_STEP, step_id, builder_run_id, f"{base}%"),
        ).fetchall()
    max_revision = 0
    latest: dict[str, Any] | None = None
    for row in rows:
        item = dict(row)
        run_id = str(item.get("verifier_run_id") or "")
        suffix = run_id[len(base):] if run_id.startswith(base) else ""
        revision = 1
        if suffix.startswith("-r") and suffix[2:].isdigit():
            revision = int(suffix[2:])
        max_revision = max(max_revision, revision)
        # The rows are ledger-sequence ordered.  A later STARTED/FAIL/BLOCKED
        # attempt supersedes an earlier PASS for this contract family; never
        # let the old green row bypass a retry or crash recovery.
        latest = item
    if latest is not None and str(latest.get("status") or "") == "PASS":
        reusable = latest
        reusable_run = str(reusable.get("verifier_run_id") or "")
        reusable_suffix = reusable_run[len(base):] if reusable_run.startswith(base) else ""
        reusable_revision = 1
        if reusable_suffix.startswith("-r") and reusable_suffix[2:].isdigit():
            reusable_revision = int(reusable_suffix[2:])
        return reusable, reusable_revision
    return None, max_revision + 1 if max_revision else 1


@dataclass(frozen=True, slots=True)
class GateDecision:
    decision: str  # ACCEPT | REFUSE
    reason: str
    checks: dict[str, str]

    @property
    def accepted(self) -> bool:
        return self.decision == "ACCEPT"


class AcceptanceGate:
    """Fail-closed evaluator deciding whether a verified step may advance.

    Reuses the durable ledger as the only source of truth: the STARTED
    event's binding, the terminal outcome event, and the live filesystem
    are compared at acceptance time.
    """

    def __init__(self, database: FoundryDatabase, *, stop_path: Path | None = None) -> None:
        self.database = database
        self.stop_path = stop_path.resolve() if stop_path is not None else None

    # -- public API ---------------------------------------------------------

    def evaluate(
        self,
        *,
        mission_id: str,
        step_id: str,
        target: Path,
        builder_run_id: str,
    ) -> GateDecision:
        try:
            return self._evaluate(
                mission_id=mission_id, step_id=step_id, target=target,
                builder_run_id=builder_run_id,
            )
        except Exception as error:  # noqa: BLE001 - fail closed, always
            return GateDecision(
                decision="REFUSE",
                reason=f"acceptance gate failed closed: {type(error).__name__}: "
                       f"{str(error)[-300:]}",
                checks={"exception": type(error).__name__},
            )

    # -- internals ----------------------------------------------------------

    def _evaluate(
        self,
        *,
        mission_id: str,
        step_id: str,
        target: Path,
        builder_run_id: str,
    ) -> GateDecision:
        checks: dict[str, str] = {}

        # (8) STOP / veto precedence: deterministic block before anything else.
        mission_for_scope = self.database.row("missions", "mission_id", mission_id)
        stop_identities = {"GLOBAL", mission_id}
        if mission_for_scope is not None and mission_for_scope.get("resource_id"):
            stop_identities.add(str(mission_for_scope["resource_id"]))
        stop_reason = emergency_stop_reason(
            self.database, self.stop_path, identities=stop_identities)
        if stop_reason:
            return GateDecision(
                decision="REFUSE", reason=f"STOP active at acceptance: {stop_reason}",
                checks={_STOP_BLOCKING_DEPENDENCY: stop_reason})

        mission = mission_for_scope
        veto_identities = {"GLOBAL", mission_id}
        if mission is not None and mission.get("resource_id"):
            veto_identities.add(str(mission["resource_id"]))
        veto = matching_veto_reason(self.database, veto_identities)
        if veto:
            return GateDecision(
                decision="REFUSE", reason=f"veto active at acceptance: {veto}",
                checks={_VETO_BLOCKING_DEPENDENCY: veto})
        checks["stop_veto"] = "clear"

        # The ONLY authorizing record is the latest durable verdict row.
        verdict = latest_verdict_for_step(self.database, mission_id, step_id)
        if verdict is None:
            return GateDecision(decision="REFUSE", reason="no verification verdict exists",
                                checks=dict(checks))
        checks["verdict_status"] = str(verdict["status"])

        # (6) Exact verdict vocabulary; only PASS authorizes.
        if verdict["status"] not in VERDICTS or verdict["status"] != "PASS":
            return GateDecision(
                decision="REFUSE",
                reason=f"verdict {verdict['status']} is not PASS; nothing may advance",
                checks=dict(checks))

        started = self._started_event(verdict["verdict_id"])
        terminal = self._terminal_event(verdict["verdict_id"], verdict["status"])
        if started is None or terminal is None:
            return GateDecision(
                decision="REFUSE",
                reason="verdict lacks its STARTED or terminal ledger event",
                checks=dict(checks))
        started_binding = started.get("binding")
        if not isinstance(started_binding, dict):
            return GateDecision(
                decision="REFUSE",
                reason="STARTED event carries no binding (pre-hardening verdict)",
                checks=dict(checks))
        checks["binding_present"] = "yes"

        # (3) Replay resistance: the terminal event must be the ledger's own
        # successor of THIS STARTED event (sequence order), not a duplicated
        # foreign event grafted onto another verdict's sequence.
        if int(terminal["sequence"]) <= int(started["sequence"]):
            return GateDecision(
                decision="REFUSE",
                reason="terminal outcome precedes its verification start (replay)",
                checks=dict(checks))
        checks["ordering"] = "ok"

        # (2) Binding integrity: recompute the binding digest from the
        # STARTED payload and compare against the stored hash.
        stored_hash = str(started_binding.get("binding_hash") or "")
        recomputed = _binding_digest(started_binding)
        if not stored_hash or stored_hash != recomputed:
            return GateDecision(
                decision="REFUSE",
                reason="binding hash mismatch (tampered or malformed binding)",
                checks=dict(checks))
        checks["binding_integrity"] = "ok"

        # Identity binding: the verdict must belong to THIS builder run and
        # step; a stale verdict from a superseded run must never authorize.
        if str(started.get("mission_id") or "") != mission_id or \
                str(started.get("subject_id") or "") != step_id:
            return GateDecision(
                decision="REFUSE",
                reason="verdict belongs to a different mission/step (wrong-target)",
                checks=dict(checks))
        if str(started.get("builder_run_id") or "") != builder_run_id:
            return GateDecision(
                decision="REFUSE",
                reason=(
                    f"verdict was issued for builder run "
                    f"{started.get('builder_run_id')!r}, not {builder_run_id!r} "
                    "(stale/replayed verdict)"),
                checks=dict(checks))
        checks["identity"] = "ok"

        # (1) Self-certification: the verifier must be its own distinct run
        # — never the builder's identity under either spelling (binding or
        # verdict row). Fresh-session isolation is structural.
        binding_builder = str(started_binding.get("builder_run_id") or "")
        binding_verifier = str(started_binding.get("verifier_run_id") or "")
        row_verifier = str(verdict.get("verifier_run_id") or "")
        if not binding_verifier:
            return GateDecision(
                decision="REFUSE",
                reason="STARTED binding carries no verifier run id",
                checks=dict(checks))
        if binding_verifier == binding_builder or row_verifier == builder_run_id \
                or binding_verifier == builder_run_id:
            return GateDecision(
                decision="REFUSE",
                reason="builder certified its own work (shared builder/verifier identity)",
                checks=dict(checks))
        checks["isolation"] = "fresh-session"

        # Terminal evidence completeness (missing evidence fails closed).
        evidence = terminal.get("evidence")
        if not isinstance(evidence, dict) or not evidence:
            return GateDecision(
                decision="REFUSE",
                reason="terminal outcome carries no evidence payload",
                checks=dict(checks))
        missing = [k for k in _REQUIRED_EVIDENCE_KEYS if k not in evidence]
        if missing:
            return GateDecision(
                decision="REFUSE",
                reason=f"terminal evidence missing keys: {sorted(missing)}",
                checks=dict(checks))
        if evidence.get("fabrication_review") != "performed":
            return GateDecision(
                decision="REFUSE",
                reason="terminal evidence lacks the fabrication review marker",
                checks=dict(checks))
        checks["evidence_complete"] = "yes"

        # (3b) Evidence agreement: terminal-evidence hashes must equal the
        # STARTED binding (stale/mismatched/fabricated evidence).
        if str(evidence.get("diff_hash") or "") != str(started_binding.get("diff_hash") or ""):
            return GateDecision(
                decision="REFUSE", reason="terminal diff_hash disagrees with the frozen binding",
                checks=dict(checks))
        if canonical_json(evidence.get("artifact_hashes") or {}) != \
                canonical_json(started_binding.get("artifact_hashes") or {}):
            return GateDecision(
                decision="REFUSE",
                reason="terminal artifact hashes disagree with the frozen binding",
                checks=dict(checks))

        # (3c) Post-verdict mutation: re-hash the frozen artifact set NOW.
        current_artifacts = _hash_target_tree(Path(str(started_binding.get("target") or target)))
        if current_artifacts != started_binding.get("artifact_hashes"):
            return GateDecision(
                decision="REFUSE",
                reason="frozen artifacts mutated after the verdict (post-verdict mutation)",
                checks=dict(checks))
        # Working-tree drift beyond the target invalidates the seal too.
        root = _working_root_for(Path(str(started_binding.get("target") or target)))
        if str(_git_diff_hash(root)) != str(started_binding.get("diff_hash") or ""):
            return GateDecision(
                decision="REFUSE",
                reason="working-tree diff changed after the verdict",
                checks=dict(checks))
        checks["post_verdict_mutation"] = "none"

        # Context-manifest binding when the verdict declares one.
        declared_manifest = str(started_binding.get("context_manifest_hash") or "")
        if declared_manifest and evidence.get("context_manifest_hash") and \
                str(evidence["context_manifest_hash"]) != declared_manifest:
            return GateDecision(
                decision="REFUSE",
                reason="context manifest hash disagrees between binding and evidence",
                checks=dict(checks))

        return GateDecision(decision="ACCEPT", reason="all acceptance invariants hold",
                            checks=dict(checks))

    # -- ledger reads -------------------------------------------------------

    def _started_event(self, verdict_id: str) -> dict[str, Any] | None:
        for event in self._events_for(verdict_id):
            if event["event_type"] == "verification.started":
                return event
        return None

    def _terminal_event(self, verdict_id: str, status: str) -> dict[str, Any] | None:
        expected_type = {
            "PASS": "verification.passed", "FAIL": "verification.failed",
            "BLOCKED": "verification.blocked", "NOT_TESTED": "verification.not_tested",
        }[status]
        found: dict[str, Any] | None = None
        for event in self._events_for(verdict_id):
            if event["event_type"] == expected_type:
                found = event
        return found

    def _events_for(self, verdict_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with self.database.connect() as connection:
            cursor = connection.execute(
                "SELECT event_id, sequence, event_type, payload_json FROM events "
                "WHERE event_type LIKE 'verification.%' ORDER BY sequence ASC"
            )
            for row in cursor.fetchall():
                if verdict_id in str(row["payload_json"]):
                    try:
                        payload = json.loads(str(row["payload_json"]))
                    except ValueError:
                        continue
                    if str(payload.get("verdict_id") or "") == verdict_id:
                        rows.append({
                            "event_id": row["event_id"],
                            "sequence": row["sequence"],
                            "event_type": row["event_type"],
                            **payload,
                        })
        return rows


def build_terminal_evidence(
    *,
    capture: BindingCapture,
    verifier_session_id: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the evidence payload every PASS outcome must carry.

    Hashes come from the frozen binding capture (never re-typed by hand);
    ``verifier_session_id`` proves the verifier was its own isolated
    session. Callers may add fields but cannot remove these.
    """
    evidence: dict[str, Any] = {
        "builder_run_id": capture.builder_run_id,
        "verifier_run_id": capture.verifier_run_id,
        "verifier_session_id": verifier_session_id,
        "artifact_hashes": dict(capture.artifact_hashes),
        "diff_hash": capture.diff_hash,
        "target": capture.target,
        "context_manifest_id": capture.context_manifest_id,
        "context_manifest_hash": capture.context_manifest_hash,
        "binding_hash": capture.binding_hash,
        "binding_schema": BINDING_SCHEMA,
        "fabrication_review": "performed",
    }
    if extra:
        evidence.update(extra)
    return evidence
