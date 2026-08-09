from __future__ import annotations

"""Database initialization and index creation."""

from pymongo import IndexModel, ASCENDING, DESCENDING, TEXT
from pymongo.errors import OperationFailure

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import *


JOB_OPERATION_PARTIAL_FILTER = {
    "owner_type": {"$type": "string"},
    "owner_id": {"$type": "string"},
    "client_operation_id": {"$type": "string"},
}


INDEX_SPECS: dict[str, list[IndexModel]] = {
    COLL_USERS: [
        IndexModel([("email", ASCENDING)], unique=True, sparse=True),
        IndexModel([("google_id", ASCENDING)], unique=True, sparse=True),
        IndexModel([(FIELD_CREATED_AT, DESCENDING)]),
        IndexModel([("is_super_admin", ASCENDING)], sparse=True),
        IndexModel([("is_active", ASCENDING)]),
    ],
    COLL_AUTH_IDENTITIES: [
        IndexModel([("provider", ASCENDING), ("provider_user_id", ASCENDING)], unique=True),
        IndexModel([(FIELD_USER_ID, ASCENDING)]),
    ],
    COLL_SESSIONS: [
        IndexModel([("token", ASCENDING)], unique=True),
        IndexModel([(FIELD_USER_ID, ASCENDING)]),
        IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0),
    ],
    COLL_PROVIDER_CONFIGS: [
        IndexModel([(FIELD_USER_ID, ASCENDING), (FIELD_PROVIDER, ASCENDING)], unique=True),
    ],
    COLL_JSON_BLOBS: [
        IndexModel([(FIELD_USER_ID, ASCENDING), (FIELD_BLOB_TYPE, ASCENDING)]),
        IndexModel([(FIELD_USER_ID, ASCENDING), (FIELD_BLOB_TYPE, ASCENDING), ("name", ASCENDING)], unique=True),
    ],
    COLL_RUNS: [
        IndexModel([(FIELD_USER_ID, ASCENDING), (FIELD_CREATED_AT, DESCENDING)]),
        IndexModel([(FIELD_RUN_ID, ASCENDING)], unique=True),
        IndexModel([(FIELD_STATUS, ASCENDING)]),
        IndexModel(
            [("owner_type", ASCENDING), ("owner_id", ASCENDING), ("run_number", ASCENDING)],
            unique=True,
            partialFilterExpression={"run_number": {"$exists": True}},
        ),
    ],
    COLL_PROMPTS: [
        IndexModel([(FIELD_USER_ID, ASCENDING), (FIELD_RUN_ID, ASCENDING)]),
        IndexModel([(FIELD_USER_ID, ASCENDING), ("batch", ASCENDING)]),
    ],
    COLL_IMAGES: [
        IndexModel([(FIELD_USER_ID, ASCENDING), (FIELD_RUN_ID, ASCENDING)]),
        IndexModel([(FIELD_USER_ID, ASCENDING), ("batch", ASCENDING)]),
    ],
    COLL_LLM_TRACES: [
        IndexModel([(FIELD_USER_ID, ASCENDING), (FIELD_CREATED_AT, DESCENDING)]),
        IndexModel([(FIELD_RUN_ID, ASCENDING)]),
    ],
    COLL_AGENTS: [
        IndexModel([("token_hash", ASCENDING)], unique=True),
        IndexModel([(FIELD_USER_ID, ASCENDING)]),
        IndexModel([(FIELD_USER_ID, ASCENDING), ("is_active", ASCENDING), ("last_heartbeat_at", DESCENDING)]),
        IndexModel([(FIELD_AGENT_ID, ASCENDING)], unique=True),
        IndexModel([("last_heartbeat_at", DESCENDING)]),
    ],
    COLL_AGENT_JOBS: [
        IndexModel(
            [
                (FIELD_AGENT_ID, ASCENDING),
                ("device_id", ASCENDING),
                (FIELD_STATUS, ASCENDING),
                (FIELD_CREATED_AT, ASCENDING),
            ]
        ),
        IndexModel([(FIELD_USER_ID, ASCENDING), (FIELD_CREATED_AT, DESCENDING)]),
        IndexModel([(FIELD_USER_ID, ASCENDING), (FIELD_STATUS, ASCENDING), ("updated_at", DESCENDING)]),
        IndexModel([("job_id", ASCENDING)], unique=True),
        IndexModel(
            [
                ("owner_type", ASCENDING),
                ("owner_id", ASCENDING),
                ("client_operation_id", ASCENDING),
            ],
            unique=True,
            partialFilterExpression=JOB_OPERATION_PARTIAL_FILTER,
        ),
        IndexModel([("terminal_event_id", ASCENDING)], unique=True, sparse=True),
        IndexModel([("purge_at", ASCENDING)], expireAfterSeconds=0),
    ],
    COLL_AGENT_PAIRINGS: [
        IndexModel([("challenge_id", ASCENDING)], unique=True),
        IndexModel([("challenge_hash", ASCENDING)], unique=True),
        IndexModel(
            [
                (FIELD_AGENT_ID, ASCENDING),
                ("device_id", ASCENDING),
                (FIELD_STATUS, ASCENDING),
                ("expires_at", ASCENDING),
            ]
        ),
        IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0),
    ],
    COLL_RENDER_COPY_JOBS: [
        IndexModel([("copy_job_id", ASCENDING)], unique=True),
        IndexModel(
            [
                (FIELD_STATUS, ASCENDING),
                ("lease_expires_at", ASCENDING),
                (FIELD_CREATED_AT, ASCENDING),
            ]
        ),
        IndexModel(
            [
                ("owner_type", ASCENDING),
                ("owner_id", ASCENDING),
                ("client_operation_id", ASCENDING),
            ],
            unique=True,
        ),
        IndexModel([("purge_at", ASCENDING)], expireAfterSeconds=0),
    ],
    COLL_PROMPT_DELIVERIES: [
        IndexModel([("delivery_id", ASCENDING)], unique=True),
        IndexModel(
            [
                (FIELD_AGENT_ID, ASCENDING),
                ("device_id", ASCENDING),
                (FIELD_STATUS, ASCENDING),
                (FIELD_CREATED_AT, ASCENDING),
            ]
        ),
        IndexModel([(FIELD_RUN_ID, ASCENDING)], unique=True),
        IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0),
    ],
    COLL_RUN_COUNTERS: [
        IndexModel([("owner_type", ASCENDING), ("owner_id", ASCENDING)], unique=True),
    ],
    COLL_LOCAL_CONFIG_REFERENCES: [
        IndexModel(
            [("scope", ASCENDING), ("owner_id", ASCENDING), ("logical_key", ASCENDING)],
            unique=True,
        ),
        IndexModel([("authority_device_id", ASCENDING)]),
        IndexModel([("verified_replica_device_ids", ASCENDING)]),
    ],
    COLL_BROWSER_SESSIONS: [
        IndexModel([(FIELD_USER_ID, ASCENDING)]),
        IndexModel([("domain", ASCENDING)]),
        IndexModel([(FIELD_CREATED_AT, ASCENDING)], expireAfterSeconds=86400 * 30),
    ],
    COLL_FILE_MAP: [
        IndexModel([("file_path", ASCENDING)]),
        IndexModel([("file_path", ASCENDING), (FIELD_USER_ID, ASCENDING)]),
        IndexModel([(FIELD_RUN_ID, ASCENDING)]),
        IndexModel([(FIELD_BATCH, ASCENDING)]),
    ],
    COLL_USER_CONFIGS: [
        IndexModel([("config_id", ASCENDING)], unique=True, sparse=True),
        IndexModel(
            [
                ("owner_type", ASCENDING),
                ("owner_id", ASCENDING),
                ("is_active", ASCENDING),
            ],
            unique=True,
            partialFilterExpression={"is_active": True},
        ),
        IndexModel([("created_by_user_id", ASCENDING)]),
        IndexModel([("updated_at", DESCENDING)]),
        IndexModel([(FIELD_USER_ID, ASCENDING)], sparse=True),
    ],
    COLL_ORGS: [
        IndexModel([("org_id", ASCENDING)], unique=True),
        IndexModel([("domain", ASCENDING)], unique=True, sparse=True),
        IndexModel([("owner_user_id", ASCENDING)]),
        IndexModel([("is_active", ASCENDING)]),
    ],
    COLL_ORG_MEMBERS: [
        IndexModel([("membership_id", ASCENDING)], unique=True, sparse=True),
        IndexModel([("org_id", ASCENDING), ("user_id", ASCENDING)], unique=True, partialFilterExpression={"status": "active"}),
        IndexModel([("user_id", ASCENDING)]),
        IndexModel([("org_id", ASCENDING)]),
        IndexModel([("org_id", ASCENDING), ("status", ASCENDING)]),
        IndexModel([("user_id", ASCENDING), ("status", ASCENDING)]),
        IndexModel([("org_id", ASCENDING), ("role", ASCENDING)]),
    ],
    COLL_ORG_INVITES: [
        IndexModel([("invite_id", ASCENDING)], unique=True, sparse=True),
        IndexModel([("token_hash", ASCENDING)], unique=True, sparse=True),
        IndexModel([("org_id", ASCENDING), ("email", ASCENDING), ("status", ASCENDING)]),
        IndexModel([("email", ASCENDING)]),
        IndexModel([("expires_at", ASCENDING)]),
        IndexModel([("status", ASCENDING)]),
        IndexModel([("created_at", DESCENDING)]),
    ],
    COLL_AUDIT_LOGS: [
        IndexModel([("event_id", ASCENDING)], unique=True, sparse=True),
        IndexModel([("actor_user_id", ASCENDING), ("created_at", DESCENDING)]),
        IndexModel([("org_id", ASCENDING), ("created_at", DESCENDING)]),
        IndexModel([("target_type", ASCENDING), ("target_id", ASCENDING)]),
        IndexModel([("event_type", ASCENDING), ("created_at", DESCENDING)]),
    ],
    COLL_CONFIG_VERSIONS: [
        IndexModel([("version_id", ASCENDING)], unique=True, sparse=True),
        IndexModel([("config_id", ASCENDING), ("created_at", DESCENDING)]),
        IndexModel([("owner_type", ASCENDING), ("owner_id", ASCENDING), ("created_at", DESCENDING)]),
        IndexModel([("org_id", ASCENDING), ("created_at", DESCENDING)]),
        IndexModel([("changed_by_user_id", ASCENDING), ("created_at", DESCENDING)]),
        IndexModel([("change_reason", ASCENDING), ("created_at", DESCENDING)]),
    ],
}


def _fix_indexes(db) -> dict[str, int]:
    """Drop and recreate indexes whose options changed between code versions.

    create_indexes() only adds new indexes and never touches existing ones.
    If a deployed index was created with stale options (e.g. unique=True
    when the code now says unique=False), we must drop it and recreate.
    """
    FIXUPS: list[tuple[str, str, IndexModel]] = [
        # user_configs.user_id_1: was unique in early versions; must be sparse-only.
        (COLL_USER_CONFIGS, "user_id_1",
         IndexModel([(FIELD_USER_ID, ASCENDING)], sparse=True)),
        # A prior declaration attempted a non-unique and unique partial index
        # with the same generated name. A failed startup can leave the
        # non-unique variant behind, so normalize it before normal creation.
        (
            COLL_USER_CONFIGS,
            "owner_type_1_owner_id_1_is_active_1",
            IndexModel(
                [
                    ("owner_type", ASCENDING),
                    ("owner_id", ASCENDING),
                    ("is_active", ASCENDING),
                ],
                unique=True,
                partialFilterExpression={"is_active": True},
            ),
        ),
        # Legacy agent jobs do not have V2 owner/operation fields. Exclude
        # those documents from operation idempotency rather than indexing
        # repeated null compound keys.
        (
            COLL_AGENT_JOBS,
            "owner_type_1_owner_id_1_client_operation_id_1",
            IndexModel(
                [
                    ("owner_type", ASCENDING),
                    ("owner_id", ASCENDING),
                    ("client_operation_id", ASCENDING),
                ],
                unique=True,
                partialFilterExpression=JOB_OPERATION_PARTIAL_FILTER,
            ),
        ),
    ]

    results: dict[str, int] = {}
    for coll_name, idx_name, desired_idx in FIXUPS:
        try:
            existing = {idx["name"]: idx for idx in db[coll_name].list_indexes()}
        except OperationFailure:
            continue
        if idx_name not in existing:
            continue
        existing_opts = existing[idx_name]
        desired_opts = desired_idx.document
        # Check for options that matter: unique, sparse, partialFilterExpression
        changed = False
        for key in ("unique", "sparse", "partialFilterExpression"):
            if existing_opts.get(key) != desired_opts.get(key):
                changed = True
                break
        if changed:
            try:
                db[coll_name].drop_index(idx_name)
                db[coll_name].create_indexes([desired_idx])
                results[f"{coll_name}.{idx_name}"] = 1
            except OperationFailure:
                results[f"{coll_name}.{idx_name}"] = -1
        else:
            results[f"{coll_name}.{idx_name}"] = 0
    return results


def create_indexes() -> dict[str, int]:
    db = get_sync_db()
    results: dict[str, int] = {}

    # Fix stale indexes first
    results.update(_fix_indexes(db))

    for coll_name, indexes in INDEX_SPECS.items():
        try:
            existing = db[coll_name].list_indexes()
            existing_names = {idx["name"] for idx in existing}
            created = 0
            for idx in indexes:
                if idx.document["name"] not in existing_names:
                    db[coll_name].create_indexes([idx])
                    existing_names.add(idx.document["name"])
                    created += 1
            results[coll_name] = created
        except OperationFailure as e:
            results[coll_name] = -1
    return results
