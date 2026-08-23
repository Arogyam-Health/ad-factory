#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.backend.agent.content_migration import (
    EncryptedBackupVault,
    LocalAgentMigrationClient,
    MongoContentMigrator,
)
from local_agent_runtime.migration import migrate_local_sources, migrate_render_files
from local_agent_runtime.storage import AgentPaths, resolve_data_root


class _DryRunImporter:
    authenticated = False
    device_id = ""

    def __init__(self, owner_key: str) -> None:
        self.owner_key = owner_key

    def import_content(self, **_request: Any) -> dict[str, Any]:
        raise RuntimeError("Dry run cannot import content")

    def import_provider_secret(self, **_request: Any) -> dict[str, Any]:
        raise RuntimeError("Dry run cannot import provider secrets")


def _decrypt_secret(value: str) -> str:
    try:
        from dashboard.backend.security.crypto import decrypt_value
    except ImportError as exc:
        raise RuntimeError("Provider secret decryption is unavailable") from exc
    return decrypt_value(value)


def _owner_root(value: str) -> tuple[str, Path]:
    owner, separator, raw_path = value.partition("=")
    if not separator or not owner.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("Expected OWNER_KEY=PATH")
    return owner.strip(), Path(raw_path).expanduser()


def _category_root(value: str) -> tuple[str, Path]:
    category, separator, raw_path = value.partition("=")
    if not separator or not category.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("Expected CATEGORY=PATH")
    return category.strip(), Path(raw_path).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run-first migration of legacy content to authenticated localhost "
            "references. Reports never include content, credentials, or owner IDs."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Import, hash-verify, back up, then remove verified Mongo bodies",
    )
    parser.add_argument(
        "--owner-key",
        required=True,
        help="Exact owner scope authenticated by LOCAL_AGENT_MIGRATION_TOKEN",
    )
    parser.add_argument(
        "--agent-url",
        default=os.getenv("LOCAL_AGENT_URL", "http://127.0.0.1:8765"),
        help="Loopback local agent URL (default: LOCAL_AGENT_URL or 127.0.0.1)",
    )
    parser.add_argument(
        "--data-dir",
        default=str(resolve_data_root()),
        help="Local agent data directory for encrypted backups/checkpoints",
    )
    parser.add_argument(
        "--local-source",
        action="append",
        default=[],
        type=_category_root,
        metavar="CATEGORY=PATH",
        help=(
            "Explicit local source: artifacts, output_roots, revision_history, "
            "content_store, or prompt_job_staging"
        ),
    )
    parser.add_argument(
        "--render-owner-root",
        action="append",
        default=[],
        type=_owner_root,
        metavar="OWNER_KEY=PATH",
        help="Explicit owner-scoped Render file root",
    )
    parser.add_argument(
        "--render-unassigned-root",
        action="append",
        default=[],
        type=Path,
        metavar="PATH",
        help="Ownerless/global Render root to report only; never imports",
    )
    parser.add_argument(
        "--skip-mongo",
        action="store_true",
        help="Inspect or migrate only explicitly supplied file roots",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = os.getenv("LOCAL_AGENT_MIGRATION_TOKEN", "").strip()
    mongo_uri = os.getenv("MONGODB_URI", "").strip()
    if args.apply and not token:
        print(
            "ERROR: LOCAL_AGENT_MIGRATION_TOKEN is required for --apply",
            file=sys.stderr,
        )
        return 2
    if not args.skip_mongo and not mongo_uri:
        print("ERROR: MONGODB_URI is required unless --skip-mongo is used", file=sys.stderr)
        return 2

    paths = AgentPaths(resolve_data_root(args.data_dir))
    importer: Any
    try:
        importer = (
            LocalAgentMigrationClient(
                base_url=args.agent_url,
                session_token=token,
                owner_key=args.owner_key,
            )
            if args.apply
            else _DryRunImporter(args.owner_key)
        )
    except ValueError:
        print("ERROR: Local agent migration settings are invalid", file=sys.stderr)
        return 2

    report: dict[str, Any] = {"apply": bool(args.apply), "redacted": True}
    try:
        if not args.skip_mongo:
            from dashboard.backend.db.client import get_sync_db
            from dashboard.backend.db.collections import (
                COLL_AGENT_JOBS,
                COLL_CONFIG_VERSIONS,
                COLL_JSON_BLOBS,
                COLL_LLM_TRACES,
                COLL_PROMPTS,
                COLL_PROVIDER_CONFIGS,
                COLL_USER_CONFIGS,
            )

            db = get_sync_db()
            collections = {
                "prompts": db[COLL_PROMPTS],
                "user_configs": db[COLL_USER_CONFIGS],
                "config_versions": db[COLL_CONFIG_VERSIONS],
                "agent_jobs": db[COLL_AGENT_JOBS],
                "llm_traces": db[COLL_LLM_TRACES],
                "json_blobs": db[COLL_JSON_BLOBS],
                "provider_configs": db[COLL_PROVIDER_CONFIGS],
            }
            report["mongo"] = MongoContentMigrator(
                collections=collections,
                importer=importer,
                backup=EncryptedBackupVault(paths.root / "migration" / "backups"),
                checkpoint_path=paths.root / "migration" / "checkpoint.json",
                decrypt_secret=_decrypt_secret,
            ).run(apply=args.apply)
        if args.local_source:
            report["local"] = migrate_local_sources(
                paths,
                source_roots=dict(args.local_source),
                owner_key=args.owner_key,
                apply=args.apply,
            )
        if args.render_owner_root or args.render_unassigned_root:
            report["render"] = migrate_render_files(
                owner_roots=dict(args.render_owner_root),
                unassigned_roots=list(args.render_unassigned_root),
                importer=importer,
                apply=args.apply,
            )
    except Exception:
        print(
            "ERROR: Migration stopped safely; review the redacted report and checkpoint",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(report, indent=2, sort_keys=True))
    if not args.apply:
        print("Dry run only. Re-run with --apply after reviewing redacted counts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
