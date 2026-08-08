#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.backend.agent.migration import (
    cleanup_local_job_payloads,
    cleanup_mongo_job_documents,
)
from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_AGENT_JOBS
from local_agent_runtime.storage import AgentPaths, resolve_data_root


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect or clean legacy content-bearing agent job records"
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--local-data-dir", default=str(resolve_data_root()))
    parser.add_argument("--skip-local", action="store_true")
    parser.add_argument("--skip-mongo", action="store_true")
    args = parser.parse_args()

    reports = []
    if not args.skip_local:
        reports.append(
            cleanup_local_job_payloads(
                AgentPaths(resolve_data_root(args.local_data_dir)),
                apply=args.apply,
            )
        )
    if not args.skip_mongo:
        reports.append(
            cleanup_mongo_job_documents(
                get_sync_db()[COLL_AGENT_JOBS],
                apply=args.apply,
            )
        )
    print(json.dumps({"apply": args.apply, "reports": reports}, indent=2, sort_keys=True))
    if not args.apply:
        print("Dry run only. Re-run with --apply after reviewing the redacted counts.")


if __name__ == "__main__":
    main()
