"""Phase-4 bridge to the existing zero-effect shadow machinery."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..database import FoundryDatabase
from ..evolution.shadow import shadow_run
from .contracts import ProcedureBundle


def shadow_procedure(
    database: FoundryDatabase,
    *,
    bundle: ProcedureBundle,
    improvement_id: str,
    candidate_release_id: str,
    inputs_dir: str | Path,
    controller: str,
    live_marker: str | Path | None = None,
    stop_check: Any = None,
) -> str:
    """Propose a frozen bundle in shadow mode without opening a capability."""
    def decision(_case: Path, sink: Any) -> None:
        sink.propose(
            decision="PROPOSE_PROCEDURE",
            expected_effect=(bundle.expected_effects[0]
                             if bundle.expected_effects else "procedure replay"),
            cost_usd=0.0,
            kind="openadapt.bundle.reference",
        )

    return shadow_run(
        database,
        improvement_id=improvement_id,
        candidate_release_id=candidate_release_id,
        decision_fn=decision,
        inputs_dir=inputs_dir,
        controller=controller,
        live_marker=live_marker,
        stop_check=stop_check,
        source_type="supervisor",
        source_id="phase4-procedural-learning",
    )

