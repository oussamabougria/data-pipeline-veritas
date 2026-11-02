from __future__ import annotations

import sys
from pathlib import Path


OP_SRC = Path(__file__).resolve().parents[1] / "veritas_operational" / "src"
if str(OP_SRC) not in sys.path:
    sys.path.insert(0, str(OP_SRC))
