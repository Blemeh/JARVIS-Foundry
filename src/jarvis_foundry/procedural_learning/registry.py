"""Register OpenAdapt surfaces in the existing JARVIS registries."""
from __future__ import annotations

from typing import Any

from ..capabilities.registry import register_capability
from ..connectors.contract import ConnectorSpec
from ..connectors.registry import record_health_sample, register_connector
from ..database import FoundryDatabase
from ..systems import probe_system, register_system
from ..supervisor.evolution_control import SUPERVISOR_CONTROLLER
from .adapter import OpenAdaptConfig, check_openadapt

OPENADAPT_BROWSER_SYSTEM = "openadapt-browser"
OPENADAPT_DESKTOP_SYSTEM = "openadapt-desktop"
OPENADAPT_BROWSER_CONNECTOR = "openadapt-browser"
OPENADAPT_DESKTOP_CONNECTOR = "openadapt-desktop"


def register_openadapt_capabilities(
    database: FoundryDatabase,
    *,
    config: OpenAdaptConfig | None = None,
    probe_browser: bool = True,
) -> dict[str, Any]:
    """Idempotently register browser/desktop with separate maturity states."""
    browser_spec = ConnectorSpec(
        connector_id=OPENADAPT_BROWSER_CONNECTOR,
        kind="browser",
        version="1.12.1",
        actions={"openadapt.replay": "execute"},
        capabilities={"openadapt.browser": "READY"},
        retry_policy={"max_attempts": 1, "backoff_s": []},
    )
    desktop_spec = ConnectorSpec(
        connector_id=OPENADAPT_DESKTOP_CONNECTOR,
        kind="desktop",
        version="1.12.1",
        actions={"openadapt.replay": "execute"},
        capabilities={"openadapt.desktop": "PROBING"},
        retry_policy={"max_attempts": 1, "backoff_s": []},
    )
    register_connector(database, browser_spec)
    register_connector(database, desktop_spec)
    register_capability(
        database,
        capability_id="openadapt.browser",
        name="OpenAdapt governed browser replay",
        family="procedural-learning",
        surfaces=["browser"],
        effect_class="browser-session",
        risk="C1",
        verification_hint="independent local system-of-record oracle",
        controller=SUPERVISOR_CONTROLLER,
        source_type="supervisor",
        source_id="phase4-openadapt",
    )
    register_capability(
        database,
        capability_id="openadapt.desktop",
        name="OpenAdapt native desktop replay",
        family="procedural-learning",
        surfaces=["desktop"],
        effect_class="desktop-input",
        risk="C2",
        verification_hint="effect oracle plus owner-approved desktop probe",
        controller=SUPERVISOR_CONTROLLER,
        source_type="supervisor",
        source_id="phase4-openadapt",
    )
    record_health_sample(database, OPENADAPT_BROWSER_CONNECTOR,
                         "UNKNOWN" if not probe_browser else "HEALTHY")
    record_health_sample(database, OPENADAPT_DESKTOP_CONNECTOR, "UNKNOWN")
    register_system(
        database, OPENADAPT_BROWSER_SYSTEM, "browser", "OpenAdapt qualified local browser",
        capabilities={"openadapt.browser": "READY"},
        probe_ref="phase1:openadapt.browser",
    )
    register_system(
        database, OPENADAPT_DESKTOP_SYSTEM, "desktop", "OpenAdapt native desktop (probe only)",
        capabilities={"openadapt.desktop": "PROBING"},
        probe_ref="phase1:openadapt.desktop",
    )
    result: dict[str, Any] = {
        "browser": "NOT_TESTED",
        "desktop": "PROBING",
        "openadapt": None,
    }
    if probe_browser and config is not None:
        try:
            result["openadapt"] = check_openadapt(config)
            probe_system(database, OPENADAPT_BROWSER_SYSTEM,
                         {"openadapt.browser": "READY"},
                         probe_key="phase4-openadapt-browser")
            record_health_sample(database, OPENADAPT_BROWSER_CONNECTOR, "HEALTHY",
                                 detail="qualified Phase-1 version probe")
            result["browser"] = "READY"
        except Exception as exc:  # honest unavailable/degraded state
            probe_system(database, OPENADAPT_BROWSER_SYSTEM,
                         {"openadapt.browser": "FAILED"},
                         probe_key="phase4-openadapt-browser-failed")
            record_health_sample(database, OPENADAPT_BROWSER_CONNECTOR, "DEGRADED",
                                 detail=f"OpenAdapt unavailable: {type(exc).__name__}")
            result["browser"] = "DEGRADED"
            result["openadapt"] = {"error": str(exc)}
    return result


def capability_matrix(database: FoundryDatabase) -> dict[str, Any]:
    """Read current readiness from the existing system/connector tables."""
    rows: dict[str, Any] = {}
    with database.connect() as connection:
        for system_id in (OPENADAPT_BROWSER_SYSTEM, OPENADAPT_DESKTOP_SYSTEM):
            row = connection.execute(
                "SELECT system_id, kind, readiness, capabilities_json FROM systems "
                "WHERE system_id = ?", (system_id,)).fetchone()
            if row is None:
                rows[system_id] = {"status": "NOT_REGISTERED"}
            else:
                import json
                rows[system_id] = {
                    "status": row["readiness"],
                    "kind": row["kind"],
                    "capabilities": json.loads(row["capabilities_json"] or "{}"),
                }
    return rows
