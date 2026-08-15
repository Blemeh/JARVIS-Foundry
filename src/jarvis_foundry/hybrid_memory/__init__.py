"""JARVIS V8 hybrid memory — native Tier C experiential memory.

Tier C is ADVISORY: it influences judgment, never authority. Tier A
(ledger/STOP/directives/verdicts/evidence) outranks Tier B (claims/beliefs)
outranks Tier C (experience). No authority path exists upward from Tier C;
recall returns context, not truth.
"""
from .defense import DefenseScanner, PoisonedMemoryError
from .lifecycle import InvalidTransition, TierCMemoryStore
from .blocks import BlockBudgetExceeded, CoreMemoryRegistry, ReadOnlyBlock
from .recall import RecallEngine
from .procedures import ProcedureRegistry
from .curation import CurationPipeline
from .versioning import DivergenceError, TierCGitVersioning
from .compiler import ContextCompiler, RoleContextPackage

__all__ = [
    "DefenseScanner", "PoisonedMemoryError",
    "InvalidTransition", "TierCMemoryStore",
    "BlockBudgetExceeded", "CoreMemoryRegistry", "ReadOnlyBlock",
    "RecallEngine", "ProcedureRegistry", "CurationPipeline",
    "DivergenceError", "TierCGitVersioning",
    "ContextCompiler", "RoleContextPackage",
]
