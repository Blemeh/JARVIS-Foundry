"""Evidence-backed production trust lifecycle.

Trust is a durable, event-derived state machine.  A heartbeat or an elapsed
clock can never promote the product: every required acceptance dimension must
have an explicit, immutable evidence record.  The Supervisor owns transitions
and the Command Center only projects them.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .clock import isoformat, parse, utc_now
from .database import FoundryDatabase
from .events import Event, EventConflict, canonical_json, payload_hash
from .trust_states import TRUST_STATES

_ALLOWED_TRANSITIONS = {
    "LAB": frozenset({"PROVING", "DEGRADED", "SUSPENDED"}),
    "PROVING": frozenset({"LIMITED_AUTONOMY", "LAB", "DEGRADED", "SUSPENDED"}),
    "LIMITED_AUTONOMY": frozenset({
        "TRUSTED_AUTONOMOUS", "PROVING", "DEGRADED", "SUSPENDED",
    }),
    "TRUSTED_AUTONOMOUS": frozenset({
        "LIMITED_AUTONOMY", "DEGRADED", "SUSPENDED", "PROVING",
    }),
    "DEGRADED": frozenset({"PROVING", "LAB", "SUSPENDED"}),
    "SUSPENDED": frozenset({"PROVING", "DEGRADED", "LAB"}),
}
TRUST_GATE_REPORT_EVENT = "trust.gate_report_recorded"
TRUST_GATE_REPORT_SCHEMA = "jarvis.trust-gate-report.v1"
TRUST_GATE_REPORT_PROFILE = "live-supervisor-runtime-v1"
TRUST_GATE_REPORT_REFRESH_SECONDS = 1800
TRUST_GATE_REPORT_MAX_AGE_SECONDS = TRUST_GATE_REPORT_REFRESH_SECONDS * 2
_RUNTIME_REPORT_PASS_KEYS = frozenset({"release_parity", "schema_integrity"})
REQUIRED_EVIDENCE = (
    "acceptance_profiles",
    "runtime_durability",
    "restart_recovery",
    "stop_revocation",
    "verifier_integrity",
    "false_pass_resistance",
    "provider_routing",
    "release_parity",
    "bounded_failures",
    "schema_integrity",
    "owner_control_gates",
)

# Limited autonomy is deliberately a smaller, local-safety gate.  Provider
# routing remains outside this gate because a runtime that has not proved its
# provider/fallback contract must not claim unrestricted autonomous scope.
LIMITED_AUTONOMY_EVIDENCE = (
    "acceptance_profiles",
    "runtime_durability",
    "restart_recovery",
    "stop_revocation",
    "verifier_integrity",
    "false_pass_resistance",
    "release_parity",
    "bounded_failures",
    "schema_integrity",
    "owner_control_gates",
)


class TrustError(ValueError):
    """Raised when a trust transition cannot be justified by evidence."""


@dataclass(frozen=True, slots=True)
class TrustAssessment:
    state: str
    recommended_state: str
    eligible: bool
    checks: dict[str, str]
    missing: tuple[str, ...]
    evidence_digest: str
    limited_eligible: bool
    limited_missing: tuple[str, ...]
    latest_report: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "recommended_state": self.recommended_state,
            "eligible": self.eligible,
            "checks": dict(self.checks),
            "missing": list(self.missing),
            "evidence_digest": self.evidence_digest,
            "required_evidence": list(REQUIRED_EVIDENCE),
            "limited_autonomy_evidence": list(LIMITED_AUTONOMY_EVIDENCE),
            "limited_eligible": self.limited_eligible,
            "limited_missing": list(self.limited_missing),
            "latest_report": self.latest_report,
        }


def _current_state(database: FoundryDatabase) -> str:
    try:
        with database.connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT trust_state FROM supervisor_state WHERE singleton = 1"
            ).fetchone()
    except Exception:  # noqa: BLE001 - trust must fail closed
        return "LAB"
    value = str(row[0]).upper() if row and row[0] else "LAB"
    # ``PROVEN`` was the pre-lifecycle spelling. Read it as the canonical
    # evidence-collection state so an old ledger cannot reset or bypass the
    # explicit promotion gate.
    if value == "PROVEN":
        value = "PROVING"
    return value if value in TRUST_STATES else "LAB"


def _latest_runtime_row(database: FoundryDatabase) -> dict[str, Any] | None:
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT event_id, event_type, source_type, source_id, payload_json "
            "FROM events WHERE event_type IN ("
            "'supervisor.started','supervisor.heartbeat','supervisor.degraded',"
            "'supervisor.stopped','emergency_stop.requested','emergency_stop.frozen',"
            "'emergency_stop.reconciled') ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except (TypeError, ValueError):
        payload = {}
    return {
        "event_id": str(row["event_id"]),
        "event_type": str(row["event_type"]),
        "source_type": str(row["source_type"]),
        "source_id": str(row["source_id"]),
        "payload": payload if isinstance(payload, dict) else {},
    }


def _report_event_is_current(
    database: FoundryDatabase, event: Event, report: dict[str, Any],
) -> bool:
    runtime = report.get("runtime")
    if not isinstance(runtime, dict):
        return False
    instance_id = str(runtime.get("instance_id") or "")
    if (
        not instance_id
        or event.source_type != "supervisor"
        or event.source_id != instance_id
        or event.correlation_id != str(report.get("report_id") or "")
        or runtime.get("database_path") != str(Path(database.path).resolve())
    ):
        return False
    try:
        observed_at = parse(str(report.get("observed_at") or ""))
    except (TypeError, ValueError, OverflowError):
        return False
    age_seconds = (utc_now() - observed_at).total_seconds()
    if age_seconds < 0 or age_seconds > TRUST_GATE_REPORT_MAX_AGE_SECONDS:
        return False
    stop_path = Path(str(runtime.get("stop_path") or ""))
    from .paths import FoundryPaths

    canonical_stop_path = FoundryPaths.discover(
        Path(database.path).resolve().parent,
    ).emergency_stop.resolve()
    if (
        not str(stop_path)
        or stop_path.resolve() != canonical_stop_path
        or stop_path.exists()
    ):
        return False
    with database.connect(readonly=True) as connection:
        lease = connection.execute(
            "SELECT owner_id, expires_at FROM leases WHERE lease_key = ?",
            ("supervisor:singleton",),
        ).fetchone()
        start_row = connection.execute(
            "SELECT event_type, source_type, source_id, payload_json FROM events "
            "WHERE event_id = ?",
            (str(runtime.get("started_event_id") or ""),),
        ).fetchone()
    if lease is None or str(lease["owner_id"]) != instance_id:
        return False
    try:
        if parse(str(lease["expires_at"])) <= utc_now():
            return False
    except (TypeError, ValueError, OverflowError):
        return False
    latest = _latest_runtime_row(database)
    if (
        latest is None
        or latest["event_type"] not in {"supervisor.started", "supervisor.heartbeat"}
        or latest["source_type"] != "supervisor"
        or latest["source_id"] != instance_id
    ):
        return False
    latest_payload = latest["payload"]
    if start_row is None:
        return False
    try:
        start_payload = json.loads(start_row["payload_json"] or "{}")
    except (TypeError, ValueError):
        return False
    identity_keys = (
        "instance_id", "pid", "creation_identity", "release_id", "generation",
    )
    if (
        str(start_row["event_type"]) != "supervisor.started"
        or str(start_row["source_type"]) != "supervisor"
        or str(start_row["source_id"]) != instance_id
        or not isinstance(start_payload, dict)
        or not isinstance(latest_payload, dict)
        or any(
            runtime.get(key) != start_payload.get(key)
            or runtime.get(key) != latest_payload.get(key)
            for key in identity_keys
        )
    ):
        return False
    try:
        from .supervisor.worker import pid_alive, process_creation_identity

        pid = int(runtime["pid"])
        if not pid_alive(pid):
            return False
        if process_creation_identity(pid) != runtime.get("creation_identity"):
            return False
    except (ImportError, KeyError, TypeError, ValueError, OSError):
        return False
    try:
        from .deployment.layout import install_root
        from .deployment.release import read_current, read_release_json, release_dir

        current = read_current(install_root())
        manifest = read_release_json(release_dir(current, install_root()) / "RELEASE.json") if current else {}
    except (OSError, ValueError, TypeError):
        return False
    return str(runtime.get("release_id") or "") in {
        str(current or ""), str(manifest.get("release_id") or ""),
    }


def _latest_gate_report(database: FoundryDatabase) -> tuple[Event, dict[str, Any]] | None:
    events = database.events(
        readonly=True,
        limit=1,
        newest_first=True,
        event_types=(TRUST_GATE_REPORT_EVENT,),
    )
    if not events:
        return None
    event = events[0]
    report = event.payload
    if not isinstance(report, dict):
        return None
    try:
        report_is_current = _report_event_is_current(database, event, report)
    except Exception:  # noqa: BLE001 - malformed or unavailable evidence fails closed
        return None
    if (
        event.payload_hash != payload_hash(report)
        or report.get("schema") != TRUST_GATE_REPORT_SCHEMA
        or report.get("profile_id") != TRUST_GATE_REPORT_PROFILE
        or not report_is_current
    ):
        return None
    content = {
        key: value for key, value in report.items()
        if key not in {"report_id", "report_digest"}
    }
    digest = payload_hash(content)
    runtime = report.get("runtime") or {}
    expected_id = (
        f"trust-report:{runtime.get('instance_id')}:{runtime.get('generation')}:{digest[:20]}"
    )
    if report.get("report_digest") != digest or report.get("report_id") != expected_id:
        return None
    dimensions = report.get("dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != set(REQUIRED_EVIDENCE):
        return None
    for key, item in dimensions.items():
        if not isinstance(item, dict):
            return None
        status = str(item.get("status") or "").upper()
        evidence = item.get("evidence")
        if status not in {"PASS", "FAIL", "NOT_RUN"}:
            return None
        expected_hash = payload_hash(evidence) if isinstance(evidence, dict) else ""
        if not evidence or item.get("evidence_hash") != expected_hash:
            return None
        if status == "PASS" and key not in _RUNTIME_REPORT_PASS_KEYS:
            return None
    return event, report


def record_live_supervisor_gate_report(
    database: FoundryDatabase,
    *,
    stop_path: str | Path,
) -> Event | None:
    """Record a report from the active Supervisor's own runtime observations.

    The caller supplies no evidence or status values.  The active lease,
    process identity, runtime events, release authority, and database schema
    are read directly from the live installation.  Unobserved dimensions are
    recorded as NOT_RUN.
    """
    runtime_event = _latest_runtime_row(database)
    if (
        runtime_event is None
        or runtime_event["event_type"] not in {"supervisor.started", "supervisor.heartbeat"}
        or runtime_event["source_type"] != "supervisor"
    ):
        return None
    runtime_payload = runtime_event["payload"]
    if not isinstance(runtime_payload, dict):
        return None
    instance_id = str(runtime_payload.get("instance_id") or runtime_event["source_id"])
    home = Path(database.path).resolve().parent
    from .paths import FoundryPaths

    canonical_stop_path = FoundryPaths.discover(home).emergency_stop.resolve()
    if (
        not instance_id
        or instance_id != runtime_event["source_id"]
        or not runtime_payload.get("release_id")
        or Path(stop_path).resolve() != canonical_stop_path
        or Path(stop_path).exists()
    ):
        return None
    with database.connect(readonly=True) as connection:
        lease = connection.execute(
            "SELECT owner_id, expires_at FROM leases WHERE lease_key = ?",
            ("supervisor:singleton",),
        ).fetchone()
        started = connection.execute(
            "SELECT event_id, payload_json FROM events "
            "WHERE event_type = 'supervisor.started' AND source_id = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (instance_id,),
        ).fetchone()
    if lease is None or str(lease["owner_id"]) != instance_id or started is None:
        return None
    try:
        if parse(str(lease["expires_at"])) <= utc_now():
            return None
        started_payload = json.loads(started["payload_json"] or "{}")
        from .supervisor.worker import pid_alive, process_creation_identity

        pid = int(runtime_payload["pid"])
        if (
            not isinstance(started_payload, dict)
            or any(started_payload.get(key) != runtime_payload.get(key) for key in (
                "instance_id", "pid", "creation_identity", "release_id", "generation",
            ))
            or not pid_alive(pid)
            or process_creation_identity(pid) != runtime_payload.get("creation_identity")
        ):
            return None
    except (ImportError, KeyError, TypeError, ValueError, OSError, OverflowError):
        return None
    try:
        from .deployment.release import release_authority_report, schema_version
        from .migrations import SCHEMA_VERSION

        authority = release_authority_report(foundry_home=home)
        live_schema = schema_version(database.path)
        quick_check = database.quick_check(readonly=True)
    except Exception:  # noqa: BLE001 - trust collection is fail-closed
        authority = {"status": "UNAVAILABLE"}
        live_schema = 0
        quick_check = "unavailable"
        SCHEMA_VERSION = -1
    schema_evidence = {
        "database_path": str(Path(database.path).resolve()),
        "quick_check": quick_check,
        "database_schema_version": live_schema,
        "package_schema_version": SCHEMA_VERSION,
    }
    schema_status = (
        "PASS"
        if quick_check == "ok" and live_schema == SCHEMA_VERSION
        else "FAIL" if quick_check not in {"unavailable", "missing"} else "NOT_RUN"
    )
    release_evidence = {
        "authority_status": str(authority.get("status") or "UNKNOWN"),
        "authority_mode": str(authority.get("database_authority_mode") or "UNKNOWN"),
        "current_source_sha": authority.get("current"),
        "database_active_source_sha": authority.get("database_active_source_sha"),
        "database_active_release_id": authority.get("database_active_release_id"),
        "runtime_release_id": runtime_payload.get("release_id"),
        "runtime_identity_match": authority.get("database_runtime_identity_match"),
    }
    current_sha = str(authority.get("current") or "")
    release_matches = bool(
        authority.get("status") == "CONSISTENT"
        and authority.get("database_authority_mode") == "DATABASE_LIVE_SLOT"
        and authority.get("database_runtime_identity_match") is True
        and authority.get("database_active_source_sha") == current_sha
        and runtime_payload.get("release_id") in {
            current_sha, authority.get("database_active_release_id"),
        }
    )
    if release_matches:
        release_status = "PASS"
    elif authority.get("status") in {
        "DATABASE_FILESYSTEM_MISMATCH", "DATABASE_RUNTIME_IDENTITY_MISMATCH",
        "DATABASE_AUTHORITY_MANIFEST_MISSING",
    }:
        release_status = "FAIL"
    else:
        release_status = "NOT_RUN"
    dimensions: dict[str, dict[str, Any]] = {}
    for key in REQUIRED_EVIDENCE:
        status = {
            "schema_integrity": schema_status,
            "release_parity": release_status,
        }.get(key, "NOT_RUN")
        evidence = (
            schema_evidence if key == "schema_integrity"
            else release_evidence if key == "release_parity"
            else {"reason": "no qualifying live observation was recorded for this dimension"}
        )
        dimensions[key] = {
            "status": status,
            "evidence": evidence,
            "evidence_hash": payload_hash(evidence),
        }
    now = utc_now()
    bucket_minute = (now.minute // 30) * 30
    observed_at = isoformat(now.replace(minute=bucket_minute, second=0, microsecond=0))
    runtime = {
        "instance_id": instance_id,
        "pid": runtime_payload.get("pid"),
        "creation_identity": runtime_payload.get("creation_identity"),
        "release_id": runtime_payload.get("release_id"),
        "generation": runtime_payload.get("generation"),
        "database_path": str(Path(database.path).resolve()),
        "stop_path": str(Path(stop_path).resolve()),
        "started_event_id": str(started["event_id"]),
    }
    content = {
        "schema": TRUST_GATE_REPORT_SCHEMA,
        "profile_id": TRUST_GATE_REPORT_PROFILE,
        "observed_at": observed_at,
        "runtime": runtime,
        "dimensions": dimensions,
    }
    digest = payload_hash(content)
    report_id = f"trust-report:{instance_id}:{runtime.get('generation')}:{digest[:20]}"
    payload = {**content, "report_id": report_id, "report_digest": digest}
    event = Event.create(
        TRUST_GATE_REPORT_EVENT,
        payload,
        source_type="supervisor",
        source_id=instance_id,
        correlation_id=report_id,
        idempotency_key=f"{TRUST_GATE_REPORT_EVENT}:{report_id}:{digest}",
    )
    try:
        _sequence, created = database.append(event)
    except EventConflict:
        for existing in database.events(
            readonly=True, event_types=(TRUST_GATE_REPORT_EVENT,), newest_first=True,
        ):
            if existing.idempotency_key == event.idempotency_key:
                return existing
        raise
    if created:
        return event
    for existing in database.events(
        readonly=True, event_types=(TRUST_GATE_REPORT_EVENT,), newest_first=True,
    ):
        if existing.idempotency_key == event.idempotency_key:
            return existing
    raise TrustError("trust gate report idempotency row disappeared")


def assess_trust(database: FoundryDatabase) -> TrustAssessment:
    """Assess only one complete, current Supervisor report.

    Legacy single-dimension rows remain in the ledger for audit history, but
    cannot contribute to readiness because they are not bound to one live
    runtime report.
    """
    accepted = _latest_gate_report(database)
    latest_report = accepted[1] if accepted is not None else None
    checks: dict[str, str] = {}
    if latest_report is not None:
        dimensions = latest_report["dimensions"]
        for key in REQUIRED_EVIDENCE:
            item = dimensions[key]
            status = str(item.get("status") or "UNKNOWN").upper()
            evidence = item.get("evidence")
            evidence_ok = (
                isinstance(evidence, dict)
                and bool(evidence)
                and item.get("evidence_hash") == payload_hash(evidence)
            )
            if not evidence_ok:
                checks[key] = "FAIL"
            elif status == "PASS" and key == "schema_integrity":
                try:
                    from .deployment.release import schema_version
                    from .migrations import SCHEMA_VERSION

                    live_schema = schema_version(database.path)
                    quick_check = database.quick_check(readonly=True)
                    checks[key] = (
                        "PASS"
                        if quick_check == "ok"
                        and evidence.get("quick_check") == "ok"
                        and evidence.get("database_path")
                        == str(Path(database.path).resolve())
                        and live_schema == SCHEMA_VERSION
                        and evidence.get("database_schema_version") == live_schema
                        and evidence.get("package_schema_version") == SCHEMA_VERSION
                        else "FAIL"
                    )
                except Exception:  # noqa: BLE001 - trust validation fails closed
                    checks[key] = "FAIL"
            elif status == "PASS" and key == "release_parity":
                try:
                    from .deployment.release import release_authority_report

                    authority = release_authority_report(
                        foundry_home=Path(database.path).resolve().parent,
                    )
                    runtime_release_id = latest_report["runtime"].get("release_id")
                    current_sha = str(authority.get("current") or "")
                    checks[key] = (
                        "PASS"
                        if authority.get("status") == "CONSISTENT"
                        and authority.get("database_authority_mode") == "DATABASE_LIVE_SLOT"
                        and authority.get("database_runtime_identity_match") is True
                        and authority.get("database_active_source_sha") == current_sha
                        and evidence.get("authority_status") == authority.get("status")
                        and evidence.get("authority_mode")
                        == authority.get("database_authority_mode")
                        and evidence.get("database_active_source_sha")
                        == authority.get("database_active_source_sha")
                        and evidence.get("database_active_release_id")
                        == authority.get("database_active_release_id")
                        and evidence.get("runtime_identity_match") is True
                        and runtime_release_id in {
                            current_sha, authority.get("database_active_release_id"),
                        }
                        and evidence.get("current_source_sha") == current_sha
                        and evidence.get("runtime_release_id") == runtime_release_id
                        else "FAIL"
                    )
                except Exception:  # noqa: BLE001 - trust validation fails closed
                    checks[key] = "FAIL"
            else:
                checks[key] = status
    missing = tuple(key for key in REQUIRED_EVIDENCE if checks.get(key) != "PASS")
    limited_missing = tuple(
        key for key in LIMITED_AUTONOMY_EVIDENCE if checks.get(key) != "PASS"
    )
    digest = (
        str(latest_report.get("report_digest") or "")
        if latest_report is not None
        else hashlib.sha256(canonical_json({}).encode("utf-8")).hexdigest()
    )
    current = _current_state(database)
    if current in {"DEGRADED", "SUSPENDED"}:
        recommended = current
    elif latest_report is None:
        recommended = "LAB"
    elif current == "PROVING" and not limited_missing:
        recommended = (
            "TRUSTED_AUTONOMOUS" if not missing else "LIMITED_AUTONOMY"
        )
    elif current == "LIMITED_AUTONOMY" and not missing:
        recommended = "TRUSTED_AUTONOMOUS"
    elif missing:
        recommended = "PROVING"
    else:
        recommended = "TRUSTED_AUTONOMOUS"
    return TrustAssessment(
        state=current, recommended_state=recommended, eligible=not missing,
        checks=checks, missing=missing, evidence_digest=digest,
        limited_eligible=not limited_missing, limited_missing=limited_missing,
        latest_report=latest_report,
    )


def record_trust_evidence(
    database: FoundryDatabase,
    evidence_key: str,
    *,
    status: str,
    evidence: dict[str, Any],
    controller: str = "supervisor",
    profile_id: str | None = None,
) -> Event:
    """Record a legacy unbound row for audit; it cannot establish trust.

    Production eligibility is derived exclusively from a complete live
    Supervisor report. This compatibility writer remains append-only for old
    tooling, while ``assess_trust`` ignores its rows.
    """
    if controller != "supervisor":
        raise TrustError("trust evidence is Supervisor-owned")
    if evidence_key not in REQUIRED_EVIDENCE:
        raise TrustError(f"unknown trust evidence key: {evidence_key}")
    normalized = str(status).upper().strip()
    if normalized not in {"PASS", "FAIL", "NOT_RUN"}:
        raise TrustError("trust evidence status must be PASS, FAIL, or NOT_RUN")
    if not isinstance(evidence, dict) or not evidence:
        raise TrustError("trust evidence payload must be a non-empty object")
    evidence_hash = hashlib.sha256(canonical_json(evidence).encode("utf-8")).hexdigest()
    payload = {
        "evidence_key": evidence_key,
        "status": normalized,
        "evidence": dict(evidence),
        "evidence_hash": evidence_hash,
        "profile_id": profile_id,
        "observed_at": isoformat(),
    }
    event = Event.create(
        "trust.evidence_recorded", payload,
        source_type="supervisor", source_id=controller,
        # Status and profile are part of the evidence identity.  A later
        # failing observation of the same evidence subject must be able to
        # supersede an earlier PASS; omitting them made that transition hit
        # an idempotency collision and could leave trust incorrectly green.
        idempotency_key=(
            f"trust.evidence_recorded:{evidence_key}:{normalized}:"
            f"{profile_id or ''}:{evidence_hash}"
        ),
    )
    try:
        _sequence, created = database.append(event)
    except EventConflict:
        # ``observed_at`` is intentionally fresh for each call, but retries of
        # the same evidence content must resolve to the original immutable
        # record rather than becoming a false idempotency conflict.
        for existing in database.events(
            readonly=True, event_types=("trust.evidence_recorded",)
        ):
            if (existing.idempotency_key == event.idempotency_key
                    and existing.payload.get("evidence_key") == evidence_key
                    and existing.payload.get("status") == normalized
                    and existing.payload.get("evidence_hash") == evidence_hash
                    and existing.payload.get("profile_id") == profile_id):
                return existing
        raise
    if created:
        return event
    # A retry with the same content-addressed evidence returns the immutable
    # ledger event rather than a freshly-generated, unpersisted UUID.
    for existing in database.events(
        readonly=True, event_types=("trust.evidence_recorded",)
    ):
        if existing.idempotency_key == event.idempotency_key:
            return existing
    raise TrustError("trust evidence idempotency row disappeared")


def transition_trust(
    database: FoundryDatabase,
    target: str,
    *,
    reason: str,
    controller: str = "supervisor",
) -> Event:
    """Persist a reversible trust transition after checking its evidence."""
    if controller != "supervisor":
        raise TrustError("trust transitions are Supervisor-owned")
    target = str(target).upper().strip()
    if target not in TRUST_STATES:
        raise TrustError(f"unknown trust state: {target}")
    if not str(reason).strip():
        raise TrustError("trust transitions require a reason")
    assessment = assess_trust(database)
    current = _current_state(database)
    gate_evidence = {
        "LIMITED_AUTONOMY": LIMITED_AUTONOMY_EVIDENCE,
        "TRUSTED_AUTONOMOUS": REQUIRED_EVIDENCE,
    }.get(target, ())
    gate_missing = tuple(
        key for key in gate_evidence if assessment.checks.get(key) != "PASS"
    )
    if gate_missing:
        raise TrustError(
            f"trust promotion refused for {target}; missing evidence: "
            + ", ".join(gate_missing)
        )
    if target != current and target not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise TrustError(f"invalid trust transition: {current} -> {target}")
    if target == current:
        # Return the existing transition when possible; otherwise create a
        # deterministic no-op audit record so the owner can see the request.
        for event in reversed(database.events(
                readonly=True, event_types=("trust.state_changed",))):
            if (event.source_type == "supervisor"
                    and event.source_id == "supervisor"
                    and event.payload.get("to_state") == target):
                return event
    payload = {
        "from_state": current,
        "to_state": target,
        "reason": str(reason).strip(),
        "controller": controller,
        "evidence_digest": assessment.evidence_digest,
        "checks": dict(assessment.checks),
        "missing": list(assessment.missing),
        "gate_required_evidence": list(gate_evidence),
        "gate_missing": list(gate_missing),
    }
    event = Event.create(
        "trust.state_changed", payload,
        source_type="supervisor", source_id=controller,
        idempotency_key=(
            f"trust.state_changed:{current}:{target}:"
            f"{assessment.evidence_digest}:{hashlib.sha256(str(reason).encode()).hexdigest()[:16]}"
        ),
    )
    _sequence, created = database.append(event)
    if created:
        return event
    for existing in database.events(
        readonly=True, event_types=("trust.state_changed",)
    ):
        if existing.idempotency_key == event.idempotency_key:
            return existing
    raise TrustError("trust transition idempotency row disappeared")


def trust_status(database: FoundryDatabase) -> dict[str, Any]:
    assessment = assess_trust(database)
    history = [
        event.payload for event in database.events(
            readonly=True, event_types=("trust.state_changed",)
        ) if event.source_type == "supervisor" and event.source_id == "supervisor"
    ]
    return {**assessment.to_dict(), "history": history}
