#!/usr/bin/env python3
"""
Seed vinaysaini's config using the new owner schema.

Usage:
  python scripts/seed_vinay_config.py --dry-run
  python scripts/seed_vinay_config.py --apply
  python scripts/seed_vinay_config.py --apply --force
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import MongoClient

COLLECTION = "user_configs"

VINAY_USER_ID = "usr_25068fa27b5a878e13c680da5aeda5f3"
VINAY_EMAIL = "vinaysaini@arogyamhealth.in"

ROOT = Path(__file__).resolve().parent.parent

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

FILES = {
    "product_master_doc": ROOT / "input" / "docs" / "product master doc.txt",
    "starting_prompt": ROOT / "input" / "startingprompt.txt",
    "copy_prompt_templates": ROOT / "dashboard" / "backend" / "copy_prompt_templates.json",
    "persona_seeds": ROOT / "persona_seeds.json",
    "copy_architecture": ROOT / "dashboard" / "backend" / "copy_architecture.json",
    "background_variant": ROOT / "background_variant.json",
    "prompt_assembler_templates": ROOT / "scripts" / "prompt_assembler_templates.json",
    "conversion_916_prompt": ROOT / "input" / "prompt_916_from_45.txt",
}

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


def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  WARNING: Could not read {path}: {e}")
        return ""


def _seed_config(client: MongoClient, db_name: str, args: argparse.Namespace) -> int:
    db = client[db_name]
    coll = db[COLLECTION]

    # Verify user exists in users collection
    user = db["users"].find_one({"user_id": VINAY_USER_ID})
    if user is None:
        # Try by email
        user = db["users"].find_one({"email": VINAY_EMAIL})
        if user is None:
            print(f"ERROR: User not found (user_id={VINAY_USER_ID}, email={VINAY_EMAIL})")
            return 1
        actual_user_id = user["user_id"]
        print(f"Found user by email: {actual_user_id}")
    else:
        actual_user_id = user["user_id"]
        print(f"Found user: {actual_user_id}")

    # Read all config files
    files_content = {}
    for key, path in FILES.items():
        content = read_file(path)
        files_content[key] = content
        print(f"  {key}: {len(content)} chars from {path.name}")

    # Check existing
    existing = coll.find_one({
        "owner_type": "user",
        "owner_id": actual_user_id,
        "is_active": True,
    })

    if existing and not args.force:
        print(f"\nConfig already exists for {actual_user_id} (use --force to overwrite)")
        return 0

    now = time.time()
    file_entries = {}
    for k in CONFIG_KEYS:
        file_entries[f"files.{k}"] = {
            "content": files_content.get(k, ""),
            "content_type": CONTENT_TYPES.get(k, "text/plain"),
            "updated_at": now,
        }

    if args.dry_run:
        print(f"\nWOULD CREATE config for user_id={actual_user_id}")
        print(f"  source: obesity_killer_seed")
        print(f"  config_scope: personal")
        print(f"  config_mode: full")
        print(f"  files: {len(CONFIG_KEYS)} keys")
        return 0

    if existing:
        coll.update_one(
            {"_id": existing["_id"]},
            {"$set": {
                **file_entries,
                "updated_at": now,
                "updated_by_user_id": actual_user_id,
                "source": "obesity_killer_seed",
                "is_active": True,
            }},
        )
        print(f"\nUpdated config for {actual_user_id}")
    else:
        new_doc = {
            "config_id": f"cfg_{uuid.uuid4().hex}",
            "owner_type": "user",
            "owner_id": actual_user_id,
            "config_scope": "personal",
            "config_mode": "full",
            "created_by_user_id": actual_user_id,
            "updated_by_user_id": actual_user_id,
            "source": "obesity_killer_seed",
            "is_active": True,
            "created_at": now,
            "updated_at": now,
            **file_entries,
        }
        coll.insert_one(new_doc)
        print(f"\nCreated config for {actual_user_id}")

    # Verify
    doc = coll.find_one({
        "owner_type": "user",
        "owner_id": actual_user_id,
        "is_active": True,
    })
    if doc:
        files = doc.get("files", {})
        print(f"Verified: {len(files)} config keys stored")
        for k in CONFIG_KEYS:
            f = files.get(k, {})
            content = f.get("content", "") if isinstance(f, dict) else ""
            print(f"  {k}: {len(content)} chars")
    else:
        print("ERROR: Document not found after upsert!")

    return 0


def main():
    parser = argparse.ArgumentParser(description="Seed vinaysaini config")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--apply", action="store_true", help="Apply seed")
    parser.add_argument("--force", action="store_true", help="Overwrite existing config")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        print("ERROR: Must specify --dry-run or --apply")
        return 1

    mongo_uri = os.environ.get("MONGODB_URI", "").strip()
    if not mongo_uri:
        print("ERROR: MONGODB_URI is required", file=sys.stderr)
        return 1
    db_name = os.environ.get("MONGODB_DB_NAME", "ad_factory").strip() or "ad_factory"

    client = None
    try:
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        return _seed_config(client, db_name, args)
    except Exception:
        print("ERROR: MongoDB operation failed", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
