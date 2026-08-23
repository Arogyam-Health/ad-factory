#!/usr/bin/env python3
"""Repair user_configs documents that have dotted keys (files.product_master_doc)
instead of a proper nested files object.

Usage:
    python scripts/repair_dotted_config_files.py --dry-run
    python scripts/repair_dotted_config_files.py --apply
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_USER_CONFIGS
from dashboard.backend.services.user_config import CONFIG_KEYS, _CONTENT_TYPES


def find_dotted_key_docs() -> list[dict]:
    """Find docs that have dotted keys like 'files.product_master_doc'."""
    db = get_sync_db()
    coll = db[COLL_USER_CONFIGS]

    dotted_prefix = "files."
    query = {k: {"$exists": True} for k in [f"files.{ck}" for ck in CONFIG_KEYS[:1]]}

    docs = list(coll.find(
        {"$or": [
            {f"files.{ck}": {"$exists": True}} for ck in CONFIG_KEYS
        ]}
    ))
    return docs


def has_proper_files(doc: dict) -> bool:
    files = doc.get("files")
    if not isinstance(files, dict):
        return False
    for k in CONFIG_KEYS:
        entry = files.get(k)
        if entry is not None and not isinstance(entry, dict):
            return False
    return True


def has_dotted_keys(doc: dict) -> bool:
    for ck in CONFIG_KEYS:
        dotted = f"files.{ck}"
        if dotted in doc:
            return True
    return False


def repair_doc(doc: dict) -> dict | None:
    """Convert dotted keys to proper nested files. Returns update spec or None."""
    if not has_dotted_keys(doc):
        return None

    files_obj = doc.get("files")
    if not isinstance(files_obj, dict):
        files_obj = {}

    for ck in CONFIG_KEYS:
        dotted = f"files.{ck}"
        if dotted in doc:
            val = doc[dotted]
            if isinstance(val, dict):
                files_obj[ck] = val
            else:
                files_obj[ck] = {
                    "content": str(val),
                    "content_type": _CONTENT_TYPES.get(ck, "text/plain"),
                    "updated_at": doc.get("updated_at", time.time()),
                }

    unset_keys = {f"files.{ck}": "" for ck in CONFIG_KEYS if f"files.{ck}" in doc}

    return {
        "$set": {"files": files_obj},
        "$unset": unset_keys,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair dotted-key config docs")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be repaired without applying")
    parser.add_argument("--apply", action="store_true", help="Apply repairs")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        parser.print_help()
        return 1

    docs = find_dotted_key_docs()
    if not docs:
        print("No config documents found.")
        return 0

    coll = get_sync_db()[COLL_USER_CONFIGS]
    repaired = 0
    skipped = 0

    for doc in docs:
        update = repair_doc(doc)
        if update is None:
            skipped += 1
            continue

        config_id = doc.get("config_id", doc.get("_id", "?"))
        dotted_keys = [k for k in update.get("$unset", {})]
        print(f"  Would repair {config_id}: dotted keys = {dotted_keys}")

        if args.apply:
            coll.update_one({"_id": doc["_id"]}, update)
            print(f"    -> Repaired {config_id}")

        repaired += 1

    print(f"\n{repaired} doc(s) {'would be' if args.dry_run else ''} repaired, {skipped} skipped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
