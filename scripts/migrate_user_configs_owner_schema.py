#!/usr/bin/env python3
"""Retired: content-bearing Mongo config migration is prohibited."""
from __future__ import annotations

import os
import sys

from pymongo import MongoClient


def main() -> int:
    mongo_uri = os.getenv("MONGODB_URI", "").strip()
    if not mongo_uri:
        print("ERROR: MONGODB_URI is required", file=sys.stderr)
        return 1
    try:
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        client.close()
    except Exception:
        print("ERROR: MongoDB operation failed", file=sys.stderr)
        return 1
    print(
        "ERROR: Retired. Use scripts/migrate_content_to_local.py; Mongo content writes are disabled.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
