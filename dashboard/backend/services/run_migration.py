from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_RUNS, COLL_PROMPTS, COLL_IMAGES, COLL_LLM_TRACES


MIGRATED_RUNS_DIR = Path(__file__).resolve().parents[3] / "dashboard_storage" / "runs"


def import_run_from_disk(user_id: str, run_id: str) -> Optional[dict[str, Any]]:
    manifest_path = MIGRATED_RUNS_DIR / run_id / "manifest.json"
    if not manifest_path.exists():
        return None

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_doc = create_run(user_id, run_id, {
        "status": manifest.get("status", "unknown"),
        "batch": manifest.get("batch", ""),
        "config": manifest.get("config", {}),
    })
    return run_doc


def list_disk_runs() -> list[str]:
    if not MIGRATED_RUNS_DIR.exists():
        return []
    return sorted(
        (d.name for d in MIGRATED_RUNS_DIR.iterdir() if d.is_dir()),
        reverse=True,
    )
