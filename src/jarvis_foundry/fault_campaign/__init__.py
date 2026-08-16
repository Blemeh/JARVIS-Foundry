"""R12 integrated fault campaign for the remastered JARVIS stack.

The campaign runs only inside isolated proving workspaces under
``JARVIS_R12_TEMP``; it never touches the live daemon, protected main,
vault, or any owner project.
"""

from __future__ import annotations

from .harness import run_campaign
from .records import REQUIRED_OUTCOME_FIELDS, FaultOutcome, FaultSpec
from .scenarios import FAULT_SPECS

__all__ = [
    "FAULT_SPECS",
    "REQUIRED_OUTCOME_FIELDS",
    "FaultOutcome",
    "FaultSpec",
    "run_campaign",
]
