#!/usr/bin/env python3
"""Execute one Reference Flow browser job in an isolated process.

The dashboard launches this helper in its own process group. Cancelling the
reference run can therefore terminate the helper and every browser automation
child immediately without changing Structured Flow execution.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp