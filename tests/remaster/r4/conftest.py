from __future__ import annotations

import sys
from pathlib import Path

_R4_TESTS = Path(__file__).resolve().parent
if str(_R4_TESTS) not in sys.path:
    sys.path.insert(0, str(_R4_TESTS))
