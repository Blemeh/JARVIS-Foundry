"""R10 Command Center package.

The R10 package is additive: it loads the pre-existing ``command_center.py``
module under a private alias so legacy callers keep the exact same public API
(``build_snapshot``, ``decision_timeline``, ``CommandCenterServer``) while the
R10 proving-store UI, canonical directive service, briefings, and voice probe
live in this package.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_LEGACY_MODULE = Path(__file__).resolve().parent.parent / "command_center.py"


def _load_legacy() -> Any:
    spec = importlib.util.spec_from_file_location(
        "jarvis_foundry._command_center_legacy", _LEGACY_MODULE
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"legacy command center module is not loadable: {_LEGACY_MODULE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_legacy = _load_legacy()

for _name in dir(_legacy):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_legacy, _name)

# Explicit legacy API kept importable by tests/foundry and briefings.py.
build_snapshot = _legacy.build_snapshot
decision_timeline = _legacy.decision_timeline
CommandCenterServer = _legacy.CommandCenterServer

from .briefings import BriefingCategory, BriefingItem, generate_briefing
from .directives import (
    DIRECTIVE_COMMANDS,
    DirectiveConflictError,
    DirectiveError,
    DirectiveReceipt,
    DirectiveService,
    DirectiveState,
)
from .proving import ProvingLayout, default_proving_layout
from .server import CommandCenterApp, serve
from .state import build_r10_snapshot, evidence_for_record, record_lookup
from .voice_probe import VoiceProbeResult, probe_voice

__all__ = [
    "DIRECTIVE_COMMANDS",
    "BriefingCategory",
    "BriefingItem",
    "CommandCenterApp",
    "DirectiveConflictError",
    "DirectiveError",
    "DirectiveReceipt",
    "DirectiveService",
    "DirectiveState",
    "ProvingLayout",
    "VoiceProbeResult",
    "build_r10_snapshot",
    "default_proving_layout",
    "evidence_for_record",
    "generate_briefing",
    "probe_voice",
    "record_lookup",
    "serve",
]
