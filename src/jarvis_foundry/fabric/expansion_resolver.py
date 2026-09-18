"""Canonical production resolver + work port for the capability-expansion seam.

``SelfExpansionEngine`` is the only authority allowed to discover, trial,
independently verify and admit a new capability, and ``run_isolated_expansion_then_resume``
is the product glue over it - but until this module existed neither had a
production caller, so a durable capability gap parked its objective in WAIT
forever.

This module supplies exactly the two coordinates that were missing:

* :func:`canonical_blueprint_resolver` - the trusted ``BlueprintResolver`` for
  the tier discovery can actually produce from *current JARVIS source*.  It
  adapts a declared JARVIS source component to exactly one missing ``cap.*``
  capability, using :data:`JARVIS_SOURCE_ADAPTERS` as the declarative table and
  refusing (returning ``None``, i.e. "not adaptable") everything else -
  including every EXTERNAL/DATA_ONLY candidate tiers.
* :func:`production_port` - constructs the engine with database-adjacent
  artifact/trial roots and the repository root the source tier searches.

No new authority is introduced: the ``ExpansionWorkPort`` protocol, the
engine's own blueprint validation, the Fabric Catalog readiness rules and the
gap bridge's WAIT/wake/replan ownership are all unchanged.  A declaration whose
Python target no longer exists in this checkout is declined rather than
adapted, so the table cannot certify a component that is not there.
"""

from __future__ import annotations

import hashlib
from importlib import import_module
from pathlib import Path
from typing import Any, Mapping

from ..adapter_factory import (
    AdapterShape,
    CapabilityAdapterContract,
    ContractCase,
    DependencyDeclaration,
    DependencyKind,
    InvocationMapping,
    ProbeKind,
    ReadinessProbe,
    SelectedSolutionDescriptor,
    SolutionClass,
)
from ..capability_solution_discovery import ReuseTier, SolutionCandidate
from ..database import FoundryDatabase
from ..effects import EffectClass
from ..fabric.model import Semantic
from .artifact_trial import TrialMission
from ..self_expansion import (
    ExpansionBlueprint,
    ExpansionWorkRequest,
    SelfExpansionEngine,
    SelfExpansionPolicy,
)

CONTRACT_VERSION = "fabric-expansion-resolver-1"
ADAPTER_REVISION = "1.0.0"
CAPABILITY_PREFIX = "cap."
SOURCE_LOCATOR_PREFIX = "source:"
ADAPTER_TABLE_AUTHORITY = "JARVIS_SYSTEM_METADATA"

#: Declarative adapter table: canonical ``cap.<action>`` -> the current JARVIS
#: source component that already implements the action.  Every entry must name
#: a tracked file (``source_path``) and the importable callable that performs
#: the action; the resolver re-checks both at resolve time.
JARVIS_SOURCE_ADAPTERS: dict[str, dict[str, Any]] = {
    "hash.file.sha256": {
        "source_path": "src/jarvis_foundry/fabric/executors.py",
        "module": "jarvis_foundry.fabric.executors",
        "attribute": "sha256_path",
        "semantic": Semantic.READ,
        "effect_class": EffectClass.READ_ONLY,
        "invocation_target": "jarvis_foundry.fabric.executors:sha256_path",
        "arguments": ("$input.path",),
        "output": {"sha256": "$raw.value"},
        "input_schema": {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string", "format": "path"}},
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "required": [
                "claimed", "sha256", "adapter_id", "capability_id", "action_kind",
            ],
            "properties": {
                "claimed": {"type": "string", "enum": ["COMPLETED"]},
                "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "adapter_id": {"type": "string"},
                "capability_id": {"type": "string"},
                "action_kind": {"type": "string"},
            },
        },
        "limitations": ("owned-root files only", "read-only"),
        "dependency": {
            "name": "jarvis-foundry",
            "kind": DependencyKind.JARVIS,
            "version": "0.1.0",
            "source": "jarvis-wheel",
            "import_name": "jarvis_foundry",
        },
    },
}


def action_for_capability(capability_id: str) -> str:
    """Return the canonical action form of a ``cap.*`` capability id."""

    value = str(capability_id or "").strip()
    if value.startswith(CAPABILITY_PREFIX):
        return value[len(CAPABILITY_PREFIX):]
    return value


def _fixture_texts(action: str) -> tuple[str, str]:
    return (
        f"jarvis self-expansion adapter contract: {action} (revision {ADAPTER_REVISION})\n",
        f"jarvis self-expansion adapter trial payload: {action} (revision {ADAPTER_REVISION})\n",
    )


def _callable_present(module_name: str, attribute: str) -> bool:
    try:
        module = import_module(module_name)
    except Exception:  # noqa: BLE001 - an unimportable target is a decline
        return False
    return callable(getattr(module, attribute, None))


def _source_path_of(locator: str) -> str:
    if not locator.startswith(SOURCE_LOCATOR_PREFIX):
        return ""
    remainder = locator[len(SOURCE_LOCATOR_PREFIX):]
    path = remainder.split("@", 1)[0]
    return path.replace("\\", "/").strip()


def canonical_blueprint_resolver(
    candidate: SolutionCandidate,
    request: ExpansionWorkRequest,
) -> ExpansionBlueprint | None:
    """Trusted conversion from one current-JARVIS source candidate to a trial.

    Returns ``None`` whenever this resolver has no declared, verifiable
    adaptation for the candidate - which is the honest answer for every tier
    other than :data:`ReuseTier.JARVIS_SOURCE` and for any declaration whose
    Python target is absent from the running checkout.
    """

    if not isinstance(candidate, SolutionCandidate):
        return None
    if not isinstance(request, ExpansionWorkRequest):
        return None
    if getattr(candidate, "tier", None) is not ReuseTier.JARVIS_SOURCE:
        return None
    capability_id = str(request.required_capability_id or "").strip()
    action = action_for_capability(capability_id)
    if not capability_id.startswith(CAPABILITY_PREFIX) or not action:
        return None
    spec = JARVIS_SOURCE_ADAPTERS.get(action)
    if spec is None:
        return None
    if _source_path_of(str(candidate.locator or "")) != spec["source_path"]:
        return None
    if not _callable_present(str(spec["module"]), str(spec["attribute"])):
        return None

    contract_text, mission_text = _fixture_texts(action)
    contract_hash = hashlib.sha256(contract_text.encode("utf-8")).hexdigest()
    mission_hash = hashlib.sha256(mission_text.encode("utf-8")).hexdigest()
    suffix = str(candidate.candidate_id)[-10:]
    adapter_id = f"adapter.jarvis.source.{action.replace('.', '-')}.{suffix}"
    dependency = dict(spec["dependency"])
    descriptor = SelectedSolutionDescriptor(
        adapter_id=adapter_id,
        solution_id=candidate.candidate_id,
        solution_class=SolutionClass.JARVIS_COMPONENT,
        solution_name=f"Adapted current JARVIS {action} ({suffix})",
        solution_version=ADAPTER_REVISION,
        locator=str(candidate.locator),
        adapter_shape=AdapterShape.CONNECTOR,
        readiness=ReadinessProbe(
            ProbeKind.JARVIS_IMPORT,
            str(spec["module"]),
            (),
            {"module": str(spec["module"])},
        ),
        capabilities=(
            CapabilityAdapterContract(
                capability_id=capability_id,
                version=ADAPTER_REVISION,
                action_kind=action,
                semantic=spec["semantic"],
                effect_class=spec["effect_class"],
                input_schema=dict(spec["input_schema"]),
                output_schema=dict(spec["output_schema"]),
                invocation=InvocationMapping(
                    target=str(spec["invocation_target"]),
                    arguments=tuple(spec["arguments"]),
                    output=dict(spec["output"]),
                ),
                contract_cases=(
                    ContractCase(
                        f"{action}-known-file",
                        {"path": "$trial_root/contract.txt"},
                        {"claimed": "COMPLETED", "sha256": contract_hash},
                    ),
                ),
                limitations=tuple(spec["limitations"]),
            ),
        ),
        dependencies=(
            DependencyDeclaration(
                str(dependency["name"]),
                dependency["kind"],
                str(dependency["version"]),
                str(dependency["source"]),
                import_name=str(dependency["import_name"]),
            ),
        ),
        provenance={
            "source": "fabric.expansion_resolver",
            "authority": ADAPTER_TABLE_AUTHORITY,
            "contract_version": CONTRACT_VERSION,
            "action": action,
            "source_path": str(spec["source_path"]),
        },
        selection={
            "candidate_id": candidate.candidate_id,
            "gap_id": request.gap_id,
            "tier": candidate.tier.label,
            "source_authority": candidate.authority,
            "external_content_policy": "DATA_ONLY",
        },
    )
    return ExpansionBlueprint(
        descriptor=descriptor,
        mission=TrialMission(
            case_id=f"{action}-source-adapter-trial",
            capability_id=capability_id,
            payload={"path": "$trial_root/mission.txt"},
            expected={"claimed": "COMPLETED", "sha256": mission_hash},
            fixtures={
                "contract.txt": contract_text,
                "mission.txt": mission_text,
            },
        ),
    )


def default_repo_root() -> Path:
    """The checkout/installation root that contains ``src/jarvis_foundry``."""

    import jarvis_foundry

    package_root = Path(jarvis_foundry.__file__).resolve().parent
    candidate = package_root.parent.parent
    return candidate if (candidate / "src").is_dir() else package_root


def production_port(
    database: FoundryDatabase,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    trial_root: str | Path | None = None,
    policy: SelfExpansionPolicy | None = None,
    resolver: Any | None = None,
) -> SelfExpansionEngine:
    """Construct the expansion engine with database-adjacent owned roots.

    The roots follow the same db-adjacent convention as the STOP sentinel and
    the gap bridge, so a candidate home owns its own trials and artifacts.
    The trial bound is the policy ceiling rather than the engine default: the
    source tier legitimately ranks files that merely *reference* an action above
    the file that implements it, so a smaller bound would refuse to reach a
    declared, verified implementation. Every trial stays isolated and bounded.
    """

    base = Path(database.path).resolve().parent / "self-expansion"
    return SelfExpansionEngine(
        database,
        repo_root=Path(repo_root) if repo_root else default_repo_root(),
        artifact_root=Path(artifact_root) if artifact_root else base / "artifacts",
        trial_root=Path(trial_root) if trial_root else base / "trials",
        resolver=resolver or canonical_blueprint_resolver,
        policy=policy or SelfExpansionPolicy(max_candidate_trials=12),
    )


def expansion_work_request_summary(request: ExpansionWorkRequest) -> Mapping[str, Any]:
    """Bounded receipt material for one dispatched request (no new state)."""

    return {
        "work_id": request.work_id,
        "gap_id": request.gap_id,
        "root_objective_id": request.root_objective_id,
        "origin_objective_id": request.origin_objective_id,
        "required_capability_id": request.required_capability_id,
        "attempt_id": request.attempt_id,
        "attempt_ordinal": request.attempt_ordinal,
        "expansion_depth": request.expansion_depth,
    }


__all__ = [
    "ADAPTER_REVISION",
    "CONTRACT_VERSION",
    "JARVIS_SOURCE_ADAPTERS",
    "action_for_capability",
    "canonical_blueprint_resolver",
    "default_repo_root",
    "expansion_work_request_summary",
    "production_port",
]
