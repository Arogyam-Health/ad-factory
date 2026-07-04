#!/usr/bin/env python3
from __future__ import annotations

"""
One-time migration script: imports existing local JSON/data files into
a user's MongoDB workspace.

Usage:
    python scripts/migrate_to_mongo.py --user-id <user_id> [--dry-run]

If --user-id is not given, the script lists available local runs and JSON files.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT))


def discover_local_data() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}

    json_files = [
        "persona_seeds.json",
        "dashboard/backend/copy_architecture.json",
        "dashboard/backend/copy_prompt_templates.json",
    ]
    result["json_blobs"] = [f for f in json_files if (ROOT / f).exists()]

    if (ROOT / "input" / "docs" / "product master doc.txt").exists():
        result["product_doc"] = ["input/docs/product master doc.txt"]

    if (ROOT / "input" / "startingprompt.txt").exists():
        result["starting_prompt"] = ["input/startingprompt.txt"]

    if (ROOT / "input" / "prompt_916_from_45.txt").exists():
        result["prompt_916"] = ["input/prompt_916_from_45.txt"]

    runs_dir = ROOT / "dashboard_storage" / "runs"
    if runs_dir.exists():
        result["runs"] = sorted(
            d.name for d in runs_dir.iterdir() if d.is_dir()
        )

    traces_dir = ROOT / "runtime" / "llm_traces"
    if traces_dir.exists():
        result["llm_traces"] = sorted(
            f.name for f in traces_dir.iterdir() if f.suffix == ".json"
        )

    return result


def import_json_blobs(user_id: str, dry_run: bool = False) -> int:
    from dashboard.backend.services.json_blobs import get_json_blob, set_json_blob

    MAPPING = {
        "persona_seeds.json": "persona_seeds",
        "dashboard/backend/copy_architecture.json": "copy_architecture",
        "dashboard/backend/copy_prompt_templates.json": "copy_prompt_templates",
    }
    count = 0
    for rel_path, blob_type in MAPPING.items():
        src = ROOT / rel_path
        if not src.exists():
            continue
        if get_json_blob(user_id, blob_type) is not None:
            print(f"  SKIP {blob_type} - already exists in DB")
            continue
        data = json.loads(src.read_text(encoding="utf-8"))
        if not dry_run:
            set_json_blob(user_id, blob_type, data)
        print(f"  {'WOULD IMPORT' if dry_run else 'IMPORTED'} {blob_type} ({len(json.dumps(data))} chars)")
        count += 1
    return count


def import_text_file(user_id: str, rel_path: str, blob_type: str, dry_run: bool = False) -> bool:
    from dashboard.backend.services.json_blobs import get_json_blob, set_json_blob

    src = ROOT / rel_path
    if not src.exists():
        return False
    if get_json_blob(user_id, blob_type) is not None:
        print(f"  SKIP {blob_type} - already exists in DB")
        return False
    content = src.read_text(encoding="utf-8")
    if not dry_run:
        set_json_blob(user_id, blob_type, {"content": content})
    print(f"  {'WOULD IMPORT' if dry_run else 'IMPORTED'} {blob_type} ({len(content)} chars)")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate local data to MongoDB")
    parser.add_argument("--user-id", help="Target user ID")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be imported")
    args = parser.parse_args()

    print("=" * 60)
    print("Ad Factory - Local to MongoDB Migration")
    print("=" * 60)

    data = discover_local_data()
    print(f"\nDiscovered local data:")
    for dtype, items in data.items():
        print(f"  {dtype}: {len(items)} items")
        for item in items[:5]:
            print(f"    - {item}")
        if len(items) > 5:
            print(f"    ... and {len(items) - 5} more")

    if not args.user_id:
        print("\nNo --user-id provided. Run with --user-id <user_id> to import.")
        return

    print(f"\nImporting to user workspace: {args.user_id}")
    if args.dry_run:
        print("  DRY RUN - no changes will be made")
    print()

    count = import_json_blobs(args.user_id, args.dry_run)
    count += import_text_file(args.user_id, "input/docs/product master doc.txt", "product_doc", args.dry_run)
    count += import_text_file(args.user_id, "input/startingprompt.txt", "starting_prompt", args.dry_run)
    count += import_text_file(args.user_id, "input/prompt_916_from_45.txt", "prompt_916", args.dry_run)

    print(f"\nTotal items imported: {count}")
    if args.dry_run:
        print("Run without --dry-run to actually import.")


if __name__ == "__main__":
    main()
