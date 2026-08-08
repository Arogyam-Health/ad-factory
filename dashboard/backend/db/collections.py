from __future__ import annotations

"""MongoDB collection names and document field constants."""

COLL_USERS = "users"
COLL_AUTH_IDENTITIES = "auth_identities"
COLL_SESSIONS = "sessions"
COLL_PROVIDER_CONFIGS = "provider_configs"
COLL_JSON_BLOBS = "json_blobs"
COLL_RUNS = "runs"
COLL_PROMPTS = "prompts"
COLL_IMAGES = "images"
COLL_LLM_TRACES = "llm_traces"
COLL_AGENTS = "agents"
COLL_AGENT_JOBS = "agent_jobs"
COLL_RUN_COUNTERS = "run_counters"
COLL_BROWSER_SESSIONS = "browser_sessions"
COLL_FILE_MAP = "file_map"
COLL_USER_CONFIGS = "user_configs"

# Organization collections
COLL_ORGS = "orgs"
COLL_ORG_MEMBERS = "org_members"
COLL_ORG_INVITES = "org_invites"
COLL_AUDIT_LOGS = "audit_logs"
COLL_CONFIG_VERSIONS = "config_versions"


FIELD_USER_ID = "user_id"
FIELD_RUN_ID = "run_id"
FIELD_AGENT_ID = "agent_id"
FIELD_CREATED_AT = "created_at"
FIELD_UPDATED_AT = "updated_at"
FIELD_BLOB_TYPE = "blob_type"
FIELD_JOB_TYPE = "job_type"
FIELD_STATUS = "status"
FIELD_PROVIDER = "provider"
FIELD_BATCH = "batch"
