from __future__ import annotations

"""Database initialization and index creation."""

from pymongo import IndexModel, ASCENDING, DESCENDING, TEXT
from pymongo.errors import OperationFailure

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import *


INDEX_SPECS: dict[str, list[IndexModel]] = {
    COLL_USERS: [
        IndexModel([("email", ASCENDING)], unique=True, sparse=True),
        IndexModel([("google_id", ASCENDING)], unique=True, sparse=True),
        IndexModel([(FIELD_CREATED_AT, DESCENDING)]),
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
        IndexModel([(FIELD_AGENT_ID, ASCENDING)], unique=True),
        IndexModel([("last_heartbeat_at", DESCENDING)]),
    ],
    COLL_AGENT_JOBS: [
        IndexModel([(FIELD_AGENT_ID, ASCENDING), (FIELD_STATUS, ASCENDING)]),
        IndexModel([(FIELD_USER_ID, ASCENDING), (FIELD_CREATED_AT, DESCENDING)]),
        IndexModel([("job_id", ASCENDING)], unique=True),
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
        IndexModel([("owner_type", ASCENDING), ("owner_id", ASCENDING), ("is_active", ASCENDING)]),
        IndexModel([("owner_type", ASCENDING), ("owner_id", ASCENDING), ("is_active", ASCENDING)], unique=True, partialFilterExpression={"is_active": True}),
        IndexModel([("created_by_user_id", ASCENDING)]),
        IndexModel([("updated_at", DESCENDING)]),
        IndexModel([(FIELD_USER_ID, ASCENDING)], sparse=True),
    ],
    COLL_ORGS: [
        IndexModel([("org_id", ASCENDING)], unique=True),
        IndexModel([("domain", ASCENDING)], unique=True, partialFilterExpression={"is_active": True}),
        IndexModel([("owner_user_id", ASCENDING)]),
        IndexModel([("is_active", ASCENDING)]),
    ],
    COLL_ORG_MEMBERS: [
        IndexModel([("membership_id", ASCENDING)], unique=True, sparse=True),
        IndexModel([("org_id", ASCENDING), ("user_id", ASCENDING)], unique=True, partialFilterExpression={"status": "active"}),
        IndexModel([("user_id", ASCENDING)]),
        IndexModel([("org_id", ASCENDING)]),
        IndexModel([("role", ASCENDING)]),
    ],
    COLL_AUDIT_LOGS: [
        IndexModel([("event_id", ASCENDING)], unique=True, sparse=True),
        IndexModel([("actor_user_id", ASCENDING), ("created_at", DESCENDING)]),
        IndexModel([("org_id", ASCENDING), ("created_at", DESCENDING)]),
        IndexModel([("target_type", ASCENDING), ("target_id", ASCENDING)]),
        IndexModel([("event_type", ASCENDING), ("created_at", DESCENDING)]),
    ],
}


def create_indexes() -> dict[str, int]:
    db = get_sync_db()
    results: dict[str, int] = {}
    for coll_name, indexes in INDEX_SPECS.items():
        try:
            existing = db[coll_name].list_indexes()
            existing_names = {idx["name"] for idx in existing}
            created = 0
            for idx in indexes:
                if idx.document["name"] not in existing_names:
                    db[coll_name].create_indexes([idx])
                    created += 1
            results[coll_name] = created
        except OperationFailure as e:
            results[coll_name] = -1
    return results
