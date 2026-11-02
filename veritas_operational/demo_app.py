"""Compatibility wrapper for older app entry points.

Use ``veritas_operational.app:app`` for product-facing runs.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app  # noqa: F401
