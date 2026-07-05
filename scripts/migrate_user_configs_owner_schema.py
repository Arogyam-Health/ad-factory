#!/usr/bin/env python3
"""
Migrate old user_configs documents to the new owner schema.

Old schema:
  { "user_id": "usr_...", "product_master_doc": "...", ... }

New schema:
  { "config_id": "cfg_...", "owner_type": "user", "owner_id": "usr_...",
    "files": { "product_master_doc": { "content": "...", "content_type": "...", "updated_at": ... } }, ... }

Usage:
  python scripts/migrate_user_configs_owner_schema.py --dry-run
  python scripts/migrate_user_configs_owner_schema.py --apply
  python scripts/migrate_user_configs_owner_schema.py --apply --force
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import MongoClient

MONGO_URI = "mongodb+srv://vinaysaini_db_user:jytQDmcPYtCk6O5F@adstorage.f7ahuc3.mongodb.net/ad_factory?retryWrites=true&w=majority&appName=adstorage"
DB_NAME = "ad_factory"
COLLECTION = "user_configs"

CONFIG_KEYS = [
    "product_master_doc",
    "starting_prompt",
    "copy_prompt_templates",
    "persona_seeds",
    "copy_architecture",
    "background_variant",
    "prompt_assembler_templates",
    "conversion_916_prompt",
]

CONTENT_TYPES = {
    "product_master_doc": "text/plain",
    "starting_prompt": "text/plain",
    "copy_prompt_templates": "application/json",
    "persona_seeds": "application/json",
    "copy_architecture": "application/json",
    "background_variant": "application/json",
    "prompt_assembler_templates": "application/json",
    "conversion_916_prompt": "text/plain",
}


def is_old_schema(doc: dict) -> bool:
    """Check if doc is old-style (has user_id, no owner_type)."""
    return "user_id" in doc and "owner_type" not in doc


def convert_to_new_schema(old_doc: dict, force: bool = False) -> dict | None:
    """Convert old doc to new schema. Returns None if should skip."""
    user_id = old_doc.get("user_id", "")
    if not user_id:
        return None

    now = time.time()
    files = {}
    for k in CONFIG_KEYS:
        val = old_doc.get(k, "")
        files[k] = {
            "content": val,
            "content_type": CONTENT_TYPES.get(k, "text/plain"),
            "updated_at": old_doc.get("updated_at", now),
        }

    return {
        "config_id": f"cfg_{uuid.uuid4().hex}",
        "owner_type": "user",
        "owner_id": user_id,
        "config_scope": "personal",
        "config_mode": "inherit_generic",
        "files": files,
        "created_by_user_id": user_id,
        "updated_by_user_id": user_id,
        "source": "migration",
        "is_active": True,
        "created_at": old_doc.get("created_at", old_doc.get("updated_at", now)),
        "updated_at": old_doc.get("updated_at", now),
        "migration": {
            "old_doc_id": str(old_doc["_id"]),
            "migrated_at": now,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Migrate user_configs to owner schema")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    parser.add_argument("--apply", action="store_true", help="Apply migration")
    parser.add_argument("--force", action="store_true", help="Overwrite existing new-schema docs")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        print("ERROR: Must specify --dry-run or --apply")
        sys.exit(1)

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client[DB_NAME]
    coll = db[COLLECTION]

    old_docs = list(coll.find({"owner_type": {"$exists": False}}))
    found = len(old_docs)
    migrated = 0
    skipped = 0
    errors = 0

    print(f"Found {found} old-schema documents")

    for old_doc in old_docs:
        user_id = old_doc.get("user_id", "")
        try:
            # Check if new-schema doc already exists
            existing_new = coll.find_one({
                "owner_type": "user",
                "owner_id": user_id,
                "is_active": True,
            })

            if existing_new and not args.force:
                print(f"  SKIP {user_id} (new-schema doc already exists, use --force to overwrite)")
                skipped += 1
                continue

            new_doc = convert_to_new_schema(old_doc, force=args.force)
            if new_doc is None:
                print(f"  SKIP {user_id} (no user_id)")
                skipped += 1
                continue

            if args.dry_run:
                print(f"  WOULD MIGRATE {user_id} ({sum(1 for k in CONFIG_KEYS if old_doc.get(k))} config keys)")
                migrated += 1
            else:
                # Mark old doc as legacy
                coll.update_one(
                    {"_id": old_doc["_id"]},
                    {"$set": {"legacy_migrated": True, "legacy_migrated_at": time.time()}},
                )
                # Insert new doc
                coll.insert_one(new_doc)
                print(f"  MIGRATED {user_id}")
                migrated += 1

        except Exception as e:
            print(f"  ERROR {user_id}: {e}")
            errors += 1

    print(f"\nSummary:")
    print(f"  found:    {found}")
    print(f"  migrated: {migrated}")
    print(f"  skipped:  {skipped}")
    print(f"  errors:   {errors}")

    if args.dry_run:
        print("\nThis was a dry run. Run with --apply to execute migration.")

    client.close()


if __name__ == "__main__":
    main()
