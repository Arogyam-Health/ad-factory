#!/usr/bin/env python3
"""
Push ALL config files (every JSON + text) to vinaysaini's user_config in MongoDB.
Also deletes the bad entry that used email as user_id.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import MongoClient

MONGO_URI = "mongodb+srv://vinaysaini_db_user:jytQDmcPYtCk6O5F@adstorage.f7ahuc3.mongodb.net/ad_factory?retryWrites=true&w=majority&appName=adstorage"
DB_NAME = "ad_factory"
COLLECTION = "user_configs"

VINAY_USER_ID = "usr_25068fa27b5a878e13c680da5aeda5f3"
BAD_USER_ID = "vinaysaini@arogyamhealth.in"

ROOT = Path(__file__).resolve().parent.parent

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


def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  WARNING: Could not read {path}: {e}")
        return ""


def main():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client[DB_NAME]
    coll = db[COLLECTION]

    # 1. Delete the bad entry with email as user_id
    result = coll.delete_one({"user_id": BAD_USER_ID})
    if result.deleted_count:
        print(f"Deleted bad entry with user_id='{BAD_USER_ID}'")
    else:
        print(f"No bad entry found with user_id='{BAD_USER_ID}'")

    # 2. Read all files and build config
    config = {}
    for key, path in FILES.items():
        content = read_file(path)
        config[key] = content
        size = len(content)
        print(f"  {key}: {size} chars from {path.name}")

    # 3. Upsert to vinaysaini's correct user_id
    import time
    config["updated_at"] = time.time()

    coll.update_one(
        {"user_id": VINAY_USER_ID},
        {"$set": config},
        upsert=True,
    )
    print(f"\nUpserted {len(config)} fields to user_id='{VINAY_USER_ID}'")

    # 4. Verify
    doc = coll.find_one({"user_id": VINAY_USER_ID})
    if doc:
        fields = [k for k in doc.keys() if k not in ("_id", "updated_at")]
        print(f"Verified: {len(fields)} config fields stored: {fields}")
    else:
        print("ERROR: Document not found after upsert!")

    client.close()


if __name__ == "__main__":
    main()
