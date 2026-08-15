"""Additive historical-compatibility readers and replay tooling for R5.

These modules are strictly read-only with respect to legacy sources.  They map
legacy V1-V7 event rows into the R4 ``LedgerEvent`` shape, replay them through
the unmodified R4 kernel projections, and provide rehearsed migration,
correction, restore, and cutover tooling that only ever works on isolated
copies.
"""

from __future__ import annotations

from jarvis_foundry.kernel.compat.corrections import (
    CORRECTION_EVENT_TYPE,
    Correction,
    CorrectionLedger,
)
from jarvis_foundry.kernel.compat.cutover import CutoverReport, rehearse_dual_run
from jarvis_foundry.kernel.compat.errors import (
    CompatError,
    CompatMigrationError,
    CompatReadError,
    CompatReplayError,
)
from jarvis_foundry.kernel.compat.invariants import (
    InvariantReport,
    ProjectionInvariantReport,
    check_projection_snapshot,
    validate_rows,
)
from jarvis_foundry.kernel.compat.migrations import (
    MIGRATION_STEPS,
    MigrationRehearsal,
)
from jarvis_foundry.kernel.compat.projections import (
    HistoricalLedgerProjectionHandler,
    build_projection_registry,
)
from jarvis_foundry.kernel.compat.readers import (
    LEGACY_EVENTS_COLUMNS,
    SUPPORTED_MIGRATION_VERSIONS,
    SUPPORTED_ROW_SCHEMA_VERSIONS,
    LegacyEventsReader,
    LegacyRow,
)
from jarvis_foundry.kernel.compat.registry import (
    HISTORICAL_EVENT_TYPE_CATALOG,
    compat_kind_registry,
)
from jarvis_foundry.kernel.compat.replay import Refusal, ReplayResult, replay_legacy

__all__ = [
    "CORRECTION_EVENT_TYPE",
    "HISTORICAL_EVENT_TYPE_CATALOG",
    "LEGACY_EVENTS_COLUMNS",
    "MIGRATION_STEPS",
    "SUPPORTED_MIGRATION_VERSIONS",
    "SUPPORTED_ROW_SCHEMA_VERSIONS",
    "CompatError",
    "CompatMigrationError",
    "CompatReadError",
    "CompatReplayError",
    "Correction",
    "CorrectionLedger",
    "CutoverReport",
    "HistoricalLedgerProjectionHandler",
    "InvariantReport",
    "LegacyEventsReader",
    "LegacyRow",
    "MigrationRehearsal",
    "ProjectionInvariantReport",
    "Refusal",
    "ReplayResult",
    "build_projection_registry",
    "check_projection_snapshot",
    "compat_kind_registry",
    "rehearse_dual_run",
    "replay_legacy",
    "validate_rows",
]
