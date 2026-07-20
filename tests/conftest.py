"""Root pytest configuration and shared fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
HEAD_UNIT = ROOT / "tests" / "unit" / "head"
for path in (SRC, HEAD_UNIT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
