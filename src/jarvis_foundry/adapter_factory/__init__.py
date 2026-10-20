"""V12 capability adapter factory public API."""
from .builder import (
    AdapterFactory,
    disable_artifact,
    enable_artifact,
    inspect_solution,
    install_isolated_runtime,
    remove_artifact,
    remove_isolated_runtime,
)
from .model import (
    ADAPTER_PROTOCOL,
    ARTIFACT_VERSION,
    DESCRIPTOR_VERSION,
    AdapterBuildArtifact,
    AdapterFactoryError,
    AdapterShape,
    CapabilityAdapterContract,
    ConfigurationRequirement,
    ContractCase,
    DependencyDeclaration,
    DependencyKind,
    InvocationMapping,
    ProbeKind,
    ReadinessProbe,
    ReconciliationMapping,
    SelectedSolutionDescriptor,
    SolutionClass,
)
from .runtime import (
    AdapterRuntimeError,
    invoke_artifact,
    invoke_typed_adapter,
    load_verified_artifact,
    probe_artifact,
    validate_schema,
)


def __getattr__(name: str):
    """Load the optional worker bridge only when callers request it.

    Artifact runtime imports run in a dependency-minimal isolated environment.
    Importing the package must not eagerly load Hermes/OpenHands worker adapters
    and their optional provider dependencies.
    """

    if name == "GeneratedWorkerAdapter":
        from .worker_bridge import GeneratedWorkerAdapter

        return GeneratedWorkerAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "ADAPTER_PROTOCOL", "ARTIFACT_VERSION", "DESCRIPTOR_VERSION",
    "AdapterBuildArtifact", "AdapterFactory", "AdapterFactoryError",
    "AdapterRuntimeError", "AdapterShape", "CapabilityAdapterContract",
    "ConfigurationRequirement", "ContractCase", "DependencyDeclaration",
    "DependencyKind", "GeneratedWorkerAdapter", "InvocationMapping",
    "ProbeKind", "ReadinessProbe", "ReconciliationMapping",
    "SelectedSolutionDescriptor", "SolutionClass", "disable_artifact",
    "enable_artifact", "inspect_solution", "install_isolated_runtime",
    "invoke_artifact", "invoke_typed_adapter", "load_verified_artifact",
    "probe_artifact", "remove_artifact", "remove_isolated_runtime",
    "validate_schema",
]
