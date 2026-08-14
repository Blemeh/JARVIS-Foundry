from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_R3 = Path(__file__).resolve().parents[3] / "scripts" / "remaster" / "r3"
if str(_SCRIPTS_R3) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_R3))
