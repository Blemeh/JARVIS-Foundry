"""Read-only project understanding for the existing Executive path.

Only an explicitly registered repository may supply this advisory profile.
The profile never creates a candidate, changes owner authority, or admits work.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..database import FoundryDatabase
from ..events import Event
from ..project_context import ProjectProfileService, ProjectTarget


def resolve_registered_project_context(
    database: FoundryDatabase,
    *,
    project_id: str,
    resource_id: str,
    root: str | Path,
    cycle_id: str,
    min_interval_s: int = 0,
) -> dict[str, Any]:
    """Compile one registered repository profile and record its evidence ref."""

    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT resolved_root, root_path FROM resources WHERE resource_id = ?",
            (resource_id,),
        ).fetchone()
    if row is None:
        return {"status": "UNAVAILABLE", "reason": "resource is not registered"}
    registered = Path(str(row["resolved_root"] or row["root_path"] or "")).resolve()
    selected = Path(root).expanduser().resolve()
    if os.path.normcase(str(registered)) != os.path.normcase(str(selected)):
        return {"status": "UNAVAILABLE", "reason": "project root differs from registration"}
    key = f"executive.project_context_resolved:{cycle_id}:{resource_id}"
    with database.connect(readonly=True) as connection:
        prior = connection.execute(
            "SELECT payload_json FROM events WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
    if prior is not None:
        return dict(json.loads(str(prior["payload_json"])))
    if min_interval_s > 0:
        with database.connect(readonly=True) as connection:
            recent = connection.execute(
                "SELECT payload_json, recorded_at FROM events "
                "WHERE event_type = 'executive.project_context_resolved' "
                "AND json_extract(payload_json, '$.resource_id') = ? "
                "ORDER BY sequence DESC LIMIT 1",
                (resource_id,),
            ).fetchone()
        if recent is not None:
            recorded = datetime.fromisoformat(
                str(recent["recorded_at"]).replace("Z", "+00:00")
            )
            if (
                datetime.now(timezone.utc) - recorded
            ).total_seconds() < min_interval_s:
                return dict(json.loads(str(recent["payload_json"])))
    try:
        resolution = ProjectProfileService(
            database.path.parent / "project_profiles"
        ).get_profile(ProjectTarget(project_id=project_id, root=selected))
    except (OSError, ValueError, RuntimeError) as error:
        return {
            "status": "UNAVAILABLE",
            "reason": f"{type(error).__name__}: {error}"[:240],
        }
    profile = resolution.profile
    context = {
        "status": "AVAILABLE",
        "authority": "ADVISORY_REPOSITORY_DATA_ONLY",
        "project_id": project_id,
        "resource_id": resource_id,
        "profile_id": profile.profile_id,
        "profile_hash": profile.profile_hash,
        "evidence_hash": profile.evidence_hash,
        "source_fingerprint": profile.source_fingerprint,
        "lifecycle": resolution.lifecycle,
        "classification": str(profile.stack.get("classification") or "UNKNOWN")[:80],
        "known_constraint_count": len(profile.known_constraints),
        "unknown_count": len(profile.unknowns),
    }
    database.append(Event.create(
        "executive.project_context_resolved",
        context,
        source_type="supervisor",
        source_id="executive-project-context",
        idempotency_key=key,
    ))
    return context


__all__ = ["resolve_registered_project_context"]
