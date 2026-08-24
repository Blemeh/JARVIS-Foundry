"""Reusable, typed, evidence-gated JARVIS procedure library."""

from .contracts import (
    CatalogError,
    FailureMode,
    OracleSpec,
    ParameterSpec,
    ProcedureDefinition,
    ProcedureStep,
    ProofStatus,
    RetryPolicy,
)
from .definitions import PROCEDURES, build_catalog
from .quality import QualityObservation, QualityRank, VersionRecord, VersionRegistry, rank_observations
from .sandbox import (
    EvidenceRecord,
    LibraryProofReport,
    ProcedureSandbox,
    SandboxProof,
    VerificationResult,
    run_library_proof,
    verify_proof,
)
from .validator import ProcedureCatalog, ValidationIssue, ValidationReport, validate_definition

__all__ = [
    "CatalogError", "EvidenceRecord", "FailureMode", "LibraryProofReport", "OracleSpec",
    "ParameterSpec", "ProcedureCatalog", "ProcedureDefinition", "ProcedureSandbox",
    "ProcedureStep", "ProofStatus", "QualityObservation", "QualityRank", "RetryPolicy",
    "SandboxProof", "ValidationIssue", "ValidationReport", "VerificationResult",
    "VersionRecord", "VersionRegistry", "PROCEDURES", "build_catalog", "rank_observations",
    "run_library_proof", "validate_definition", "verify_proof",
]
