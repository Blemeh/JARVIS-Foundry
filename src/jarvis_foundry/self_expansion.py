"""One bounded V12 gap-to-probation self-expansion engine.

The engine is glue over existing authorities.  Capability Fabric alone
establishes/readies a gap; discovery remains read-only and reuse-first; the
Adapter Factory alone produces ``jarvis.capability.adapter.v1``; the artifact
trial independently verifies before Catalog adoption; and the gap bridge
alone wakes/replans the original objective.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from .adapter_factory import (
    ADAPTER_PROTOCOL,
    AdapterFactory,
    SelectedSolutionDescriptor,
    SolutionClass,
)
from .capability_solution_discovery import (
    CandidateDisposition,
    CapabilityGapRequirement,
    CapabilitySolutionDiscovery,
    CapabilitySolutionStore,
    DiscoveryRun,
    SolutionCandidate,
)
from .control.broker import emergency_stop_reason, matching_veto_reason
from .control.redaction import redact_text
from .database import FoundryDatabase
from .effects import EffectClass
from .events import Event, canonical_json
from .fabric.artifact_trial import (
    ArtifactTrialStatus,
    ArtifactTrialStopped,
    CanonicalArtifactTrialPipeline,
    TrialMission,
)
from .fabric.catalog import (
    capability_registration,
    disable_capability,
    listed_capabilities,
    observed_readiness,
)
from .fabric.executors import ExecutorContext
from .fabric.gap_bridge import ExpansionWorkPort, ExpansionWorkRequest, GapBridgeStopped
from .fabric.loop import execute_action
from .fabric.model import (
    CapabilityTrust,
    ClaimOutcome,
    Readiness,
    TaskIntent,
)
from .mcp_backend import (
    CANONICAL_MCP_RUNTIME,
    MCP_ADOPTION_SEAM_VERSION,
    FabricMcpBackend,
    selected_solution_mcp_adoption,
    stable_tool_identity,
)


CONTRACT_VERSION = "jarvis.v12.self-expansion.v1"
SOURCE_ID = "v12-self-expansion-engine"
EVENT_PREFIX = "fabric.self_expansion."
RUN_STARTED = EVENT_PREFIX + "run_started"
DISCOVERY_COMPLETED = EVENT_PREFIX + "discovery_completed"
SOLUTION_SELECTED = EVENT_PREFIX + "solution_selected"
SOLUTION_REJECTED = EVENT_PREFIX + "solution_rejected"
ADAPTER_BUILT = EVENT_PREFIX + "adapter_built"
TRIAL_FINISHED = EVENT_PREFIX + "trial_finished"
MCP_ADOPTION_VERIFIED = EVENT_PREFIX + "mcp_adoption_verified"
RUN_COMPLETED = EVENT_PREFIX + "completed"
RUN_FAILED = EVENT_PREFIX + "failed"


class SelfExpansionError(ValueError):
    """The convergence contract or a trusted adapter blueprint is invalid."""


@dataclass(frozen=True, slots=True)
class ExpansionBlueprint:
    """Trusted conversion from one DATA_ONLY candidate to one Factory input."""

    descriptor: SelectedSolutionDescriptor
    mission: TrialMission

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, SelectedSolutionDescriptor):
            raise SelfExpansionError("blueprint descriptor must be a SelectedSolutionDescriptor")
        if not isinstance(self.mission, TrialMission):
            raise SelfExpansionError("blueprint mission must be a TrialMission")


@runtime_checkable
class BlueprintResolver(Protocol):
    """Trusted system seam that describes how an observed solution is adapted."""

    def __call__(
        self,
        candidate: SolutionCandidate,
        request: ExpansionWorkRequest,
    ) -> ExpansionBlueprint | None:
        """Return a bounded Factory blueprint, or ``None`` when not adaptable."""


@dataclass(frozen=True, slots=True)
class SelfExpansionPolicy:
    max_candidate_trials: int = 3
    search_all_tiers: bool = False
    include_custom_placeholder: bool = True

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_candidate_trials, int)
            or isinstance(self.max_candidate_trials, bool)
            or not 1 <= self.max_candidate_trials <= 12
        ):
            raise SelfExpansionError("max_candidate_trials must be between 1 and 12")


def stable_expansion_run_id(request: ExpansionWorkRequest) -> str:
    material = {
        "work_id": request.work_id,
        "gap_id": request.gap_id,
        "root_objective_id": request.root_objective_id,
        "required_capability_id": request.required_capability_id,
        "requirement_set_id": request.requirement_set_id,
        "attempt_id": request.attempt_id,
        "attempt_ordinal": request.attempt_ordinal,
    }
    digest = hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()[:24]
    return f"self-expansion:{digest}"


def _append(
    database: FoundryDatabase,
    event_type: str,
    run_id: str,
    payload: Mapping[str, Any],
    *,
    suffix: str = "",
) -> None:
    database.append(Event.create(
        event_type,
        {"contract_version": CONTRACT_VERSION, "run_id": run_id, **dict(payload)},
        source_type="supervisor",
        source_id=SOURCE_ID,
        correlation_id=run_id,
        idempotency_key=f"{event_type}:{run_id}{':' + suffix if suffix else ''}",
    ))


def _events(database: FoundryDatabase, run_id: str) -> tuple[Event, ...]:
    return tuple(
        event for event in database.events(event_type_prefixes=(EVENT_PREFIX,), readonly=True)
        if event.payload.get("run_id") == run_id
    )


def _subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and all(
            key in actual and _subset(value, actual[key])
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(
                _subset(left, right)
                for left, right in zip(expected, actual, strict=True)
            )
        )
    return expected == actual


def self_expansion_record(database: FoundryDatabase, run_id: str) -> dict[str, Any] | None:
    rows = _events(database, run_id)
    if not rows:
        return None
    record: dict[str, Any] = {"run_id": run_id, "events": []}
    for event in rows:
        record["events"].append({"event_type": event.event_type, "event_id": event.event_id})
        if event.event_type == DISCOVERY_COMPLETED:
            record["discovery"] = dict(event.payload)
        elif event.event_type == SOLUTION_SELECTED:
            descriptor = event.payload.get("mcp_descriptor")
            if isinstance(descriptor, Mapping):
                record["mcp_descriptor"] = dict(descriptor)
        elif event.event_type == SOLUTION_REJECTED:
            record.setdefault("rejections", []).append(dict(event.payload))
        elif event.event_type == ADAPTER_BUILT:
            record.setdefault("artifacts", []).append(dict(event.payload))
        elif event.event_type == TRIAL_FINISHED:
            record.setdefault("trials", []).append(dict(event.payload))
        elif event.event_type == MCP_ADOPTION_VERIFIED:
            record.setdefault("mcp_adoptions", []).append(dict(event.payload))
        elif event.event_type in {RUN_COMPLETED, RUN_FAILED}:
            record["receipt"] = dict(event.payload.get("receipt") or {})
            record["status"] = record["receipt"].get("status")
    return record


class SelfExpansionEngine(ExpansionWorkPort):
    """Discover, adapt, trial, verify, and adopt for one stable bridge attempt."""

    def __init__(
        self,
        database: FoundryDatabase,
        *,
        repo_root: str | Path,
        artifact_root: str | Path,
        trial_root: str | Path,
        resolver: BlueprintResolver,
        discovery: CapabilitySolutionDiscovery | None = None,
        policy: SelfExpansionPolicy | None = None,
    ) -> None:
        if not callable(resolver):
            raise SelfExpansionError("a trusted blueprint resolver is required")
        self.database = database
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.artifact_root = Path(artifact_root).expanduser().resolve()
        self.trial_root = Path(trial_root).expanduser().resolve()
        self.resolver = resolver
        self.discovery = discovery or CapabilitySolutionDiscovery(
            database, repo_root=self.repo_root
        )
        self.policy = policy or SelfExpansionPolicy()
        self._mcp_backends: dict[str, FabricMcpBackend] = {}

    def _gate(self, request: ExpansionWorkRequest, *targets: str) -> None:
        stop = emergency_stop_reason(
            self.database,
            Path(self.database.path).resolve().parent / "EMERGENCY_STOP",
            identities={
                "GLOBAL",
                request.origin_objective_id,
                request.root_objective_id,
                request.gap_id,
                str(request.attempt_id or ""),
                *targets,
            },
        )
        if stop:
            raise GapBridgeStopped(f"STOP active: {stop}")
        veto = matching_veto_reason(
            self.database,
            {
                "GLOBAL", "capability-expansion", "capability-trial",
                request.origin_objective_id, request.gap_id,
                str(request.attempt_id or ""), *targets,
            },
        )
        if veto:
            raise GapBridgeStopped(f"owner veto active: {veto}")

    def mcp_backend_for(self, capability_id: str) -> FabricMcpBackend | None:
        """Return the active canonical backend for one adopted MCP capability."""

        return self._mcp_backends.get(str(capability_id))

    def close(self) -> None:
        """Close each owned MCP backend once."""

        seen: set[int] = set()
        for backend in self._mcp_backends.values():
            identity = id(backend)
            if identity in seen:
                continue
            seen.add(identity)
            backend.close()
        self._mcp_backends.clear()

    def _remember_mcp_backend(
        self,
        backend: FabricMcpBackend,
        capability_ids: tuple[str, ...],
    ) -> None:
        for capability_id in capability_ids:
            prior = self._mcp_backends.get(capability_id)
            if prior is not None and prior is not backend:
                raise SelfExpansionError(
                    f"MCP capability {capability_id} already has an active backend"
                )
            self._mcp_backends[capability_id] = backend

    def _connect_selected_mcp(
        self,
        descriptor: SelectedSolutionDescriptor,
        request: ExpansionWorkRequest,
        run_id: str,
        *,
        mission: TrialMission | None,
        verify_trial: bool,
    ) -> tuple[FabricMcpBackend, dict[str, Any]]:
        """Connect, project, and verify one selected solution via FabricMcpBackend."""

        adoption = selected_solution_mcp_adoption(descriptor.as_dict())
        server = adoption.config.servers[0]
        before_ids = {
            spec.capability_id for spec in listed_capabilities(self.database)
        }
        backend = adoption.create_backend(self.database)
        try:
            health, projected = backend.connect_server(
                server.server_id,
                include_disabled=False,
            )
            if str(health.state.value) != "READY":
                raise SelfExpansionError("selected MCP server did not become READY")
            if len(projected) != 1:
                raise SelfExpansionError(
                    "selected MCP solution must project exactly one enabled capability"
                )
            contract = descriptor.capabilities[0]
            spec = projected[0]
            expected_capability_id, expected_action_kind = stable_tool_identity(
                server.server_id,
                contract.invocation.target,
            )
            if (
                contract.capability_id != expected_capability_id
                or contract.action_kind != expected_action_kind
                or spec.capability_id != contract.capability_id
                or spec.action_kind != contract.action_kind
                or spec.capability_id != request.required_capability_id
            ):
                raise SelfExpansionError(
                    "selected MCP descriptor is not bound to the stable discovered tool identity"
                )
            registration = capability_registration(
                self.database, spec.capability_id
            )
            readiness = observed_readiness(self.database, spec.capability_id)
            if (
                registration is None
                or readiness.state is not Readiness.READY
                or readiness.trust_state is not CapabilityTrust.PROBATION
            ):
                raise SelfExpansionError(
                    "MCP projection did not reach Catalog READY/PROBATION"
                )

            verification: dict[str, Any] = {
                "verdict": "PASS",
                "runtime": CANONICAL_MCP_RUNTIME,
                "server_id": server.server_id,
                "capability_id": spec.capability_id,
                "action_kind": spec.action_kind,
                "transport": server.transport.value,
                "trust": CapabilityTrust.PROBATION.value,
                "second_mcp_runtime": False,
                "remote_content_authority": "DATA_ONLY",
            }
            if verify_trial:
                if mission is None:
                    raise SelfExpansionError(
                        "selected MCP adoption requires a bounded trial mission"
                    )
                trial_workspace = self.trial_root / (
                    "mcp-" + hashlib.sha256(
                        run_id.encode("utf-8")
                    ).hexdigest()[:20]
                )
                trial_workspace.mkdir(parents=True, exist_ok=True)
                result = execute_action(
                    self.database,
                    ExecutorContext(
                        database=self.database,
                        owned_root=trial_workspace,
                        mcp_backend=backend,
                    ),
                    TaskIntent(
                        task_id=f"{run_id}:mcp-trial",
                        required_actions=(spec.action_kind,),
                        unattended=False,
                    ),
                    spec.action_kind,
                    mission.payload,
                    workflow_id=run_id,
                    step_id=mission.case_id,
                )
                if contract.effect_class is EffectClass.IRREVERSIBLE_OR_HIGH_COST:
                    if (
                        result.claimed is not ClaimOutcome.BLOCKED
                        or "PROBATION" not in result.note
                    ):
                        raise SelfExpansionError(
                            "irreversible MCP probation trial did not fail closed"
                        )
                    verification.update({
                        "trial_mode": "PROBATION_SAFETY_GATE",
                        "mutation_exercised": False,
                        "effect_claimed": False,
                    })
                else:
                    if (
                        not result.success
                        or not _subset(mission.expected, result.as_dict())
                    ):
                        raise SelfExpansionError(
                            "selected MCP trial did not satisfy the JARVIS-owned contract"
                        )
                    verification.update({
                        "trial_mode": "FABRIC_MCP_INVOCATION",
                        "mutation_exercised": (
                            contract.effect_class is not EffectClass.READ_ONLY
                        ),
                        "action_result": result.as_dict(),
                    })
            else:
                verification["trial_mode"] = "RESTART_RECONNECTION"

            self._remember_mcp_backend(
                backend, tuple(spec.capability_id for spec in projected)
            )
            return backend, {
                "adoption": adoption.as_dict(),
                "registration_id": str(registration["registration_id"]),
                "verification": verification,
            }
        except Exception:
            try:
                backend.close()
            except Exception:
                # Preserve the adoption failure while still rolling back the
                # Catalog projection. FabricMcpBackend.close is best effort.
                pass
            after_ids = {
                spec.capability_id
                for spec in listed_capabilities(self.database)
            }
            for capability_id in sorted(after_ids - before_ids):
                disable_capability(
                    self.database,
                    capability_id,
                    reason=(
                        "selected MCP self-expansion candidate failed "
                        "before verified adoption"
                    ),
                )
            raise

    @staticmethod
    def _requirement(request: ExpansionWorkRequest) -> CapabilityGapRequirement:
        capability_id = str(request.required_capability_id)
        action = capability_id[4:] if capability_id.startswith("cap.") else capability_id
        return CapabilityGapRequirement(
            gap_id=request.gap_id,
            requirement_set_id=request.requirement_set_id,
            requested_action=action,
            missing_capability_id=capability_id,
            description=f"Fabric Catalog has no registered {capability_id}",
            required_actions=(action,),
            required_capability_ids=(capability_id,),
            candidate_identity={
                "gap_candidate_id": request.candidate_id,
                "origin_objective_id": request.origin_objective_id,
                "root_objective_id": request.root_objective_id,
                "attempt_id": request.attempt_id,
            },
        )

    @staticmethod
    def _ordered_candidates(run: DiscoveryRun) -> tuple[str, ...]:
        candidates = {item.candidate_id: item for item in run.candidates}
        eligible = []
        for candidate_id in run.comparison.ranked_candidate_ids:
            row = run.comparison.scores.get(candidate_id) or {}
            item = candidates.get(candidate_id)
            if item is None:
                continue
            # A later tier may be deferred only because an earlier reusable
            # solution won.  It becomes the next candidate if that winner's
            # actual isolated trial fails.
            if bool(row.get("eligible_before_reuse_order")):
                eligible.append(candidate_id)
        return tuple(eligible)

    def _discover(
        self,
        request: ExpansionWorkRequest,
        run_id: str,
    ) -> tuple[tuple[SolutionCandidate, ...], tuple[str, ...], Mapping[str, Any]]:
        prior = self_expansion_record(self.database, run_id) or {}
        discovery_record = prior.get("discovery")
        if isinstance(discovery_record, Mapping):
            candidates = CapabilitySolutionStore.candidates(
                self.database, gap_id=request.gap_id
            )
            order = tuple(discovery_record.get("candidate_order") or ())
            return candidates, order, discovery_record
        self._gate(request, "discovery")
        run = self.discovery.discover(
            self._requirement(request),
            search_all_tiers=self.policy.search_all_tiers,
            include_custom=self.policy.include_custom_placeholder,
        )
        order = self._ordered_candidates(run)
        payload = {
            "gap_id": request.gap_id,
            "attempt_id": request.attempt_id,
            "candidate_order": list(order),
            "candidate_count": len(run.candidates),
            "recommended_candidate_id": run.recommendation.selected_candidate_id,
            "recommendation_status": run.recommendation.status,
            "comparison_id": run.comparison.comparison_id,
            "reuse_order": list(run.comparison.reuse_order),
            "search_stages": [item.stage for item in run.search_reports],
            "external_results_authority": "DATA_ONLY",
            "discovery_read_only": run.read_only,
        }
        _append(self.database, DISCOVERY_COMPLETED, run_id, payload)
        self._gate(request, "discovery")
        return run.candidates, order, payload

    @staticmethod
    def _validate_blueprint(
        blueprint: ExpansionBlueprint,
        candidate: SolutionCandidate,
        request: ExpansionWorkRequest,
    ) -> None:
        descriptor = blueprint.descriptor
        if descriptor.solution_id != candidate.candidate_id:
            raise SelfExpansionError("descriptor solution_id is not bound to selected candidate")
        if descriptor.solution_class is SolutionClass.MCP_STDIO:
            adoption = selected_solution_mcp_adoption(descriptor.as_dict())
            if len(descriptor.capabilities) != 1:
                raise SelfExpansionError(
                    "selected MCP solution must adapt exactly one missing capability"
                )
            contract = descriptor.capabilities[0]
            server = adoption.config.servers[0]
            expected = stable_tool_identity(
                server.server_id, contract.invocation.target
            )
            if (
                contract.capability_id,
                contract.action_kind,
            ) != expected:
                raise SelfExpansionError(
                    "selected MCP capability must use its stable Fabric identity"
                )
        capability_ids = {item.capability_id for item in descriptor.capabilities}
        if capability_ids != {request.required_capability_id}:
            raise SelfExpansionError("descriptor must adapt exactly the missing capability")
        if blueprint.mission.capability_id != request.required_capability_id:
            raise SelfExpansionError("trial mission is not bound to the missing capability")
        selection = dict(descriptor.selection)
        if (
            selection.get("candidate_id") != candidate.candidate_id
            or selection.get("gap_id") != request.gap_id
        ):
            raise SelfExpansionError("descriptor selection lacks canonical gap/candidate lineage")
        if candidate.data_only and selection.get("source_authority") != "DATA_ONLY":
            raise SelfExpansionError("external candidate must remain DATA_ONLY in selection lineage")

    def _reject(
        self,
        run_id: str,
        request: ExpansionWorkRequest,
        candidate: SolutionCandidate,
        *,
        stage: str,
        reason: str,
    ) -> None:
        _append(self.database, SOLUTION_REJECTED, run_id, {
            "gap_id": request.gap_id,
            "attempt_id": request.attempt_id,
            "candidate_id": candidate.candidate_id,
            "tier": candidate.tier.label,
            "stage": stage,
            "reason": redact_text(reason)[:600],
            "candidate_authority": candidate.authority,
            "candidate_data_only": candidate.data_only,
        }, suffix=candidate.candidate_id)

    def dispatch(self, request: ExpansionWorkRequest) -> Mapping[str, Any]:
        if not isinstance(request, ExpansionWorkRequest):
            raise SelfExpansionError("dispatch requires ExpansionWorkRequest")
        if not request.attempt_id or not request.attempt_ordinal:
            raise SelfExpansionError("bridge attempt identity is required")
        if request.expansion_depth != 1:
            raise SelfExpansionError("nested capability expansion is forbidden")
        if request.authority != "CAPABILITY_FABRIC_CATALOG":
            raise SelfExpansionError("Fabric Catalog must own the gap authority")
        run_id = stable_expansion_run_id(request)
        prior = self_expansion_record(self.database, run_id)
        if prior is not None and isinstance(prior.get("receipt"), Mapping):
            receipt = dict(prior["receipt"])
            if receipt.get("status") != "REGISTERED":
                return receipt
            if (
                receipt.get("runtime") == CANONICAL_MCP_RUNTIME
                and self.mcp_backend_for(request.required_capability_id) is None
            ):
                descriptor = prior.get("mcp_descriptor")
                if not isinstance(descriptor, Mapping):
                    receipt = {
                        **receipt,
                        "status": "FAILED",
                        "reason_code": "MCP_ADOPTION_RECORD_MISSING",
                        "reason": (
                            "the prior MCP registration cannot be reconstructed "
                            "without its selected descriptor"
                        ),
                        "retryable": False,
                    }
                    _append(
                        self.database,
                        RUN_FAILED,
                        run_id,
                        {"receipt": receipt},
                        suffix="mcp-record-missing",
                    )
                    return receipt
                try:
                    self._connect_selected_mcp(
                        SelectedSolutionDescriptor.from_mapping(descriptor),
                        request,
                        run_id,
                        mission=None,
                        verify_trial=False,
                    )
                except Exception as error:
                    receipt = {
                        **receipt,
                        "status": "FAILED",
                        "reason_code": "MCP_ADOPTION_NO_LONGER_ACTIVE",
                        "reason": redact_text(
                            f"{type(error).__name__}: {error}"
                        )[:600],
                        "retryable": True,
                    }
                    _append(
                        self.database,
                        RUN_FAILED,
                        run_id,
                        {"receipt": receipt},
                        suffix="mcp-inactive",
                    )
                    return receipt
            registration = capability_registration(
                self.database, request.required_capability_id
            )
            readiness = observed_readiness(
                self.database, request.required_capability_id
            )
            if (
                registration
                and registration.get("registration_id") == receipt.get("registration_id")
                and readiness.state is Readiness.READY
                and readiness.trust_state is CapabilityTrust.PROBATION
            ):
                return receipt
            stale = {
                **receipt,
                "status": "FAILED",
                "reason_code": "ADOPTION_NO_LONGER_ACTIVE",
                "reason": "the prior probation registration is no longer active",
                "retryable": False,
            }
            _append(self.database, RUN_FAILED, run_id, {"receipt": stale}, suffix="inactive")
            return stale
        self._gate(request, run_id)
        _append(self.database, RUN_STARTED, run_id, {
            "request": request.as_dict(),
            "attempt_id": request.attempt_id,
            "gap_id": request.gap_id,
            "adapter_protocol": ADAPTER_PROTOCOL,
            "nested_expansion": False,
        })
        candidates, order, discovery_record = self._discover(request, run_id)
        by_id = {item.candidate_id: item for item in candidates}
        rejected_ids = {
            str(event.payload.get("candidate_id"))
            for event in _events(self.database, run_id)
            if event.event_type == SOLUTION_REJECTED
        }
        attempted = 0
        for candidate_id in order:
            if attempted >= self.policy.max_candidate_trials:
                break
            candidate = by_id.get(candidate_id)
            if candidate is None or candidate_id in rejected_ids:
                continue
            if candidate.disposition is CandidateDisposition.REJECTED:
                continue
            attempted += 1
            self._gate(request, candidate_id)
            try:
                blueprint = self.resolver(candidate, request)
                if blueprint is None:
                    raise SelfExpansionError("trusted resolver found no bounded adapter mapping")
                self._validate_blueprint(blueprint, candidate, request)
                selected_payload = {
                    "gap_id": request.gap_id,
                    "attempt_id": request.attempt_id,
                    "candidate_id": candidate_id,
                    "tier": candidate.tier.label,
                    "descriptor_hash": blueprint.descriptor.descriptor_hash,
                    "adapter_id": blueprint.descriptor.adapter_id,
                    "candidate_authority": candidate.authority,
                    "candidate_data_only": candidate.data_only,
                }
                if blueprint.descriptor.solution_class is SolutionClass.MCP_STDIO:
                    selected_payload["mcp_descriptor"] = (
                        blueprint.descriptor.as_dict()
                    )
                _append(
                    self.database,
                    SOLUTION_SELECTED,
                    run_id,
                    selected_payload,
                    suffix=candidate_id,
                )
                if blueprint.descriptor.solution_class is SolutionClass.MCP_STDIO:
                    self._gate(request, candidate_id, "canonical-mcp-backend")
                    _backend, mcp = self._connect_selected_mcp(
                        blueprint.descriptor,
                        request,
                        run_id,
                        mission=blueprint.mission,
                        verify_trial=True,
                    )
                    verification = dict(mcp["verification"])
                    trial_id = (
                        "mcp-trial:"
                        + hashlib.sha256(
                            f"{run_id}\0{candidate_id}".encode("utf-8")
                        ).hexdigest()[:24]
                    )
                    _append(
                        self.database,
                        MCP_ADOPTION_VERIFIED,
                        run_id,
                        {
                            "gap_id": request.gap_id,
                            "attempt_id": request.attempt_id,
                            "candidate_id": candidate_id,
                            "trial_id": trial_id,
                            "registration_id": mcp["registration_id"],
                            "verification": verification,
                            "mcp_adoption": mcp["adoption"],
                        },
                        suffix=candidate_id,
                    )
                    receipt = {
                        "contract_version": CONTRACT_VERSION,
                        "status": "REGISTERED",
                        "run_id": run_id,
                        "work_id": request.work_id,
                        "gap_id": request.gap_id,
                        "attempt_id": request.attempt_id,
                        "candidate_id": candidate_id,
                        "solution_id": blueprint.descriptor.solution_id,
                        "adapter_protocol": MCP_ADOPTION_SEAM_VERSION,
                        "trial_id": trial_id,
                        "registration_id": mcp["registration_id"],
                        "trust": CapabilityTrust.PROBATION.value,
                        "readiness": Readiness.READY.value,
                        "fabric_authority": "CAPABILITY_FABRIC_CATALOG",
                        "external_results_authority": "DATA_ONLY",
                        "independent_verification": "PASS",
                        "runtime": CANONICAL_MCP_RUNTIME,
                        "second_mcp_runtime": False,
                        "mcp_adoption": mcp["adoption"],
                        "trial_mode": verification["trial_mode"],
                        "nested_expansion": False,
                    }
                    _append(
                        self.database,
                        RUN_COMPLETED,
                        run_id,
                        {
                            "receipt": receipt,
                            "discovery_comparison_id": discovery_record.get(
                                "comparison_id"
                            ),
                        },
                    )
                    return receipt
                self._gate(request, candidate_id, "adapter-factory")
                artifact = AdapterFactory(self.artifact_root).build(blueprint.descriptor)
                _append(self.database, ADAPTER_BUILT, run_id, {
                    "gap_id": request.gap_id,
                    "attempt_id": request.attempt_id,
                    "candidate_id": candidate_id,
                    "artifact_id": artifact.artifact_id,
                    "descriptor_hash": artifact.descriptor_hash,
                    "manifest_path": str(artifact.manifest_path),
                    "adapter_protocol": ADAPTER_PROTOCOL,
                    "catalog_mutated": False,
                }, suffix=candidate_id)
                self._gate(request, candidate_id, "isolated-trial")
                trial = CanonicalArtifactTrialPipeline(
                    self.database,
                    self.trial_root,
                    project_root=self.repo_root,
                ).run(artifact, blueprint.mission)
                _append(self.database, TRIAL_FINISHED, run_id, {
                    "gap_id": request.gap_id,
                    "attempt_id": request.attempt_id,
                    "candidate_id": candidate_id,
                    "artifact_id": artifact.artifact_id,
                    "trial_id": trial.trial_id,
                    "registration_id": trial.registration_id,
                    "trial_status": trial.status,
                    "verification_verdict": (
                        (trial.verification or {}).get("verdict")
                    ),
                }, suffix=candidate_id)
                if trial.status != ArtifactTrialStatus.PROBATION.value:
                    self._reject(
                        run_id, request, candidate,
                        stage="isolated_trial",
                        reason=str((trial.rejection or {}).get("reason") or trial.status),
                    )
                    continue
                self._gate(request, candidate_id, "catalog-reconciliation")
                registration = capability_registration(
                    self.database, request.required_capability_id
                )
                readiness = observed_readiness(
                    self.database, request.required_capability_id
                )
                authoritative = bool(
                    registration
                    and registration.get("registration_id") == trial.registration_id
                    and readiness.state is Readiness.READY
                    and readiness.trust_state is CapabilityTrust.PROBATION
                )
                if not authoritative:
                    CanonicalArtifactTrialPipeline(
                        self.database, self.trial_root, project_root=self.repo_root
                    ).disable(
                        artifact,
                        capability_id=request.required_capability_id,
                        reason="Catalog reconciliation failed after artifact trial",
                    )
                    self._reject(
                        run_id, request, candidate,
                        stage="catalog_reconciliation",
                        reason="Fabric Catalog did not confirm READY/PROBATION",
                    )
                    continue
                receipt = {
                    "contract_version": CONTRACT_VERSION,
                    "status": "REGISTERED",
                    "run_id": run_id,
                    "work_id": request.work_id,
                    "gap_id": request.gap_id,
                    "attempt_id": request.attempt_id,
                    "candidate_id": candidate_id,
                    "solution_id": blueprint.descriptor.solution_id,
                    "artifact_id": artifact.artifact_id,
                    "adapter_protocol": ADAPTER_PROTOCOL,
                    "trial_id": trial.trial_id,
                    "registration_id": trial.registration_id,
                    "trust": CapabilityTrust.PROBATION.value,
                    "readiness": Readiness.READY.value,
                    "fabric_authority": "CAPABILITY_FABRIC_CATALOG",
                    "external_results_authority": "DATA_ONLY",
                    "independent_verification": "PASS",
                    "nested_expansion": False,
                }
                _append(self.database, RUN_COMPLETED, run_id, {
                    "receipt": receipt,
                    "discovery_comparison_id": discovery_record.get("comparison_id"),
                })
                return receipt
            except (GapBridgeStopped, ArtifactTrialStopped):
                raise GapBridgeStopped("STOP/veto paused the stable expansion attempt")
            except Exception as error:  # candidate failure is bounded fallback evidence
                self._reject(
                    run_id,
                    request,
                    candidate,
                    stage="blueprint_or_factory",
                    reason=f"{type(error).__name__}: {error}",
                )
                continue
        receipt = {
            "contract_version": CONTRACT_VERSION,
            "status": "FAILED",
            "run_id": run_id,
            "work_id": request.work_id,
            "gap_id": request.gap_id,
            "attempt_id": request.attempt_id,
            "reason_code": "NO_VERIFIED_SOLUTION",
            "reason": "bounded reuse-first candidates did not reach verified PROBATION",
            "retryable": True,
            "candidate_order": list(order),
            "candidate_trials": attempted,
            "external_results_authority": "DATA_ONLY",
            "nested_expansion": False,
        }
        _append(self.database, RUN_FAILED, run_id, {"receipt": receipt})
        return receipt


__all__ = [
    "ADAPTER_PROTOCOL",
    "BlueprintResolver",
    "CONTRACT_VERSION",
    "ExpansionBlueprint",
    "SelfExpansionEngine",
    "SelfExpansionError",
    "SelfExpansionPolicy",
    "self_expansion_record",
    "stable_expansion_run_id",
]
