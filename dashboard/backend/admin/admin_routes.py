from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from dashboard.backend.admin.admin_auth import require_super_admin_dependency
from dashboard.backend.admin.admin_serializers import (
    safe_user,
    safe_invite,
    safe_session,
    safe_audit_log,
    safe_provider_config,
    safe_run,
    safe_image,
    safe_prompt,
    redact_sensitive,
)
from dashboard.backend.db.client import get_sync_db, ping
from dashboard.backend.db.collections import (
    COLL_USERS,
    COLL_AUTH_IDENTITIES,
    COLL_SESSIONS,
    COLL_ORGS,
    COLL_ORG_MEMBERS,
    COLL_ORG_INVITES,
    COLL_AUDIT_LOGS,
    COLL_USER_CONFIGS,
    COLL_CONFIG_VERSIONS,
    COLL_RUNS,
    COLL_IMAGES,
    COLL_PROMPTS,
    COLL_PROVIDER_CONFIGS,
    COLL_AGENTS,
    COLL_AGENT_JOBS,
    COLL_LOCAL_CONFIG_REFERENCES,
)
from dashboard.backend.services.org_helper import write_audit_event

router = APIRouter()


def _paginate(
    coll_name: str,
    query_filter: dict[str, Any],
    page: int,
    per_page: int,
    sort: list[tuple[str, int]] | None = None,
) -> dict[str, Any]:
    db = get_sync_db()
    total = db[coll_name].count_documents(query_filter)
    cursor = db[coll_name].find(query_filter)
    if sort:
        cursor = cursor.sort(sort)
    cursor = cursor.skip((page - 1) * per_page).limit(per_page)
    items = list(cursor)
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": -(-total // per_page) if total > 0 else 0,
    }


# ── Overview ─────────────────────────────────────────────────────────────────


@router.get("/api/admin/overview")
def admin_overview(
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    db = get_sync_db()
    now_ts = time.time()
    day_ago = now_ts - 86400
    week_ago = now_ts - 604800
    return {
        "users": {
            "total": db[COLL_USERS].count_documents({}),
            "active": db[COLL_USERS].count_documents({"is_active": {"$ne": False}}),
            "super_admins": db[COLL_USERS].count_documents({"is_super_admin": True}),
            "new_today": db[COLL_USERS].count_documents({"created_at": {"$gte": day_ago}}),
            "new_this_week": db[COLL_USERS].count_documents({"created_at": {"$gte": week_ago}}),
        },
        "orgs": {
            "total": db[COLL_ORGS].count_documents({}),
            "active": db[COLL_ORGS].count_documents({"is_active": True}),
        },
        "members": {
            "active": db[COLL_ORG_MEMBERS].count_documents({"status": "active"}),
        },
        "runs": {
            "total": db[COLL_RUNS].count_documents({}),
        },
        "images": {
            "total": db[COLL_IMAGES].count_documents({}),
        },
        "sessions": {
            "active": db[COLL_SESSIONS].count_documents({"expires_at": {"$gt": now_ts}}),
        },
        "invites": {
            "pending": db[COLL_ORG_INVITES].count_documents({"status": "pending"}),
        },
    }


# ── User Management ──────────────────────────────────────────────────────────


@router.get("/api/admin/users")
def admin_list_users(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    search: str | None = Query(None),
    include_disabled: bool = Query(False),
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    q: dict[str, Any] = {}
    if not include_disabled:
        q["is_active"] = {"$ne": False}
    if search:
        q["$or"] = [
            {"email": {"$regex": search, "$options": "i"}},
            {"display_name": {"$regex": search, "$options": "i"}},
            {"user_id": {"$regex": search, "$options": "i"}},
        ]
    result = _paginate(COLL_USERS, q, page, per_page, sort=[("created_at", -1)])
    result["items"] = [safe_user(u) for u in result["items"]]
    return result


@router.get("/api/admin/individual-users")
def admin_individual_users(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    search: str | None = Query(None),
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    db = get_sync_db()
    active_member_user_ids = db[COLL_ORG_MEMBERS].distinct("user_id", {"status": "active"})
    q: dict[str, Any] = {"user_id": {"$nin": active_member_user_ids}}
    if search:
        q["$or"] = [
            {"email": {"$regex": search, "$options": "i"}},
            {"display_name": {"$regex": search, "$options": "i"}},
            {"user_id": {"$regex": search, "$options": "i"}},
        ]
    result = _paginate(COLL_USERS, q, page, per_page, sort=[("created_at", -1)])
    result["items"] = [safe_user(u) for u in result["items"]]
    return result


@router.get("/api/admin/users/{user_id}")
def admin_get_user(
    user_id: str,
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    user = get_sync_db()[COLL_USERS].find_one({"user_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return safe_user(user)


@router.patch("/api/admin/users/{user_id}")
def admin_update_user(
    user_id: str,
    payload: dict[str, Any],
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    admin_user_id = _admin["user_id"]
    admin_email = _admin.get("email", "")
    user = get_sync_db()[COLL_USERS].find_one({"user_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = time.time()
    updates: dict[str, Any] = {}
    audit_events: list[dict[str, Any]] = []

    if "is_active" in payload:
        if user_id == admin_user_id and payload["is_active"] is False:
            raise HTTPException(status_code=400, detail="Cannot disable your own account")
        new_active = bool(payload["is_active"])
        if new_active != user.get("is_active", True):
            updates["is_active"] = new_active
            if new_active:
                updates["reenabled_at"] = now
                updates["reenabled_by_user_id"] = admin_user_id
                audit_events.append({
                    "event_type": "admin_user_enabled",
                    "target_type": "user",
                    "target_id": user_id,
                    "metadata": {"reenabled_by": admin_user_id},
                })
            else:
                updates["disabled_at"] = now
                updates["disabled_by_user_id"] = admin_user_id
                updates["disabled_reason"] = payload.get("reason", "manual_disable")
                audit_events.append({
                    "event_type": "admin_user_disabled",
                    "target_type": "user",
                    "target_id": user_id,
                    "metadata": {
                        "disabled_by": admin_user_id,
                        "reason": payload.get("reason", "manual_disable"),
                    },
                })

    if "is_super_admin" in payload:
        if user_id == admin_user_id and payload["is_super_admin"] is False:
            raise HTTPException(status_code=400, detail="Cannot revoke your own super admin status")
        new_sa = bool(payload["is_super_admin"])
        if new_sa != user.get("is_super_admin", False):
            updates["is_super_admin"] = new_sa
            updates["is_platform_admin"] = new_sa
            audit_events.append({
                "event_type": "admin_super_admin_granted" if new_sa else "admin_super_admin_revoked",
                "target_type": "user",
                "target_id": user_id,
                "metadata": {"changed_by": admin_user_id},
            })

    if "display_name" in payload:
        updates["display_name"] = str(payload["display_name"])

    if not updates:
        return safe_user(user)

    updates["updated_at"] = now
    get_sync_db()[COLL_USERS].update_one({"user_id": user_id}, {"$set": updates})

    for ev in audit_events:
        write_audit_event(
            event_type=ev["event_type"],
            actor_user_id=admin_user_id,
            actor_email=admin_email,
            target_type=ev["target_type"],
            target_id=ev["target_id"],
            org_id=None,
            metadata=ev["metadata"],
        )

    updated = get_sync_db()[COLL_USERS].find_one({"user_id": user_id})
    return safe_user(updated or user)


@router.delete("/api/admin/users/{user_id}")
def admin_delete_user(
    user_id: str,
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    admin_user_id = _admin["user_id"]
    admin_email = _admin.get("email", "")
    user = get_sync_db()[COLL_USERS].find_one({"user_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user_id == admin_user_id:
        raise HTTPException(status_code=400, detail="Cannot disable your own account")

    now = time.time()
    get_sync_db()[COLL_USERS].update_one(
        {"user_id": user_id},
        {"$set": {
            "is_active": False,
            "disabled_at": now,
            "disabled_by_user_id": admin_user_id,
            "disabled_reason": "admin_delete",
            "updated_at": now,
        }},
    )
    get_sync_db()[COLL_SESSIONS].delete_many({"user_id": user_id})

    write_audit_event(
        event_type="admin_user_disabled",
        actor_user_id=admin_user_id,
        actor_email=admin_email,
        target_type="user",
        target_id=user_id,
        org_id=None,
        metadata={"disabled_by": admin_user_id, "reason": "admin_delete"},
    )

    return {"status": "disabled", "user_id": user_id}


# ── Session Management ──────────────────────────────────────────────────────


@router.get("/api/admin/users/{user_id}/sessions")
def admin_list_user_sessions(
    user_id: str,
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    user = get_sync_db()[COLL_USERS].find_one({"user_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    sessions = list(
        get_sync_db()[COLL_SESSIONS]
        .find({"user_id": user_id})
        .sort("created_at", -1)
    )
    return {"sessions": [safe_session(s) for s in sessions]}


@router.delete("/api/admin/users/{user_id}/sessions")
def admin_revoke_user_sessions(
    user_id: str,
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    user = get_sync_db()[COLL_USERS].find_one({"user_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    result = get_sync_db()[COLL_SESSIONS].delete_many({"user_id": user_id})
    return {"status": "revoked", "deleted_count": result.deleted_count, "user_id": user_id}


# ── Org Management ───────────────────────────────────────────────────────────


@router.get("/api/admin/orgs")
def admin_list_orgs(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    search: str | None = Query(None),
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    q: dict[str, Any] = {}
    if search:
        q["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"domain": {"$regex": search, "$options": "i"}},
            {"org_id": {"$regex": search, "$options": "i"}},
        ]
    result = _paginate(COLL_ORGS, q, page, per_page, sort=[("created_at", -1)])
    for item in result["items"]:
        item.pop("_id", None)
    return result


@router.get("/api/admin/orgs/{org_id}")
def admin_get_org(
    org_id: str,
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    db = get_sync_db()
    org = db[COLL_ORGS].find_one({"org_id": org_id})
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    org.pop("_id", None)
    members = list(
        db[COLL_ORG_MEMBERS].find({"org_id": org_id}).sort("created_at", -1)
    )
    for m in members:
        m.pop("_id", None)
    invites = list(
        db[COLL_ORG_INVITES]
        .find({"org_id": org_id})
        .sort("created_at", -1)
    )
    return {
        "org": org,
        "members": members,
        "invites": [safe_invite(i) for i in invites],
    }


@router.patch("/api/admin/orgs/{org_id}")
def admin_update_org(
    org_id: str,
    payload: dict[str, Any],
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    db = get_sync_db()
    org = db[COLL_ORGS].find_one({"org_id": org_id})
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    now = time.time()
    updates: dict[str, Any] = {"updated_at": now}
    if "name" in payload:
        updates["name"] = str(payload["name"])
    if "config_mode" in payload:
        mode = payload["config_mode"]
        if mode not in ("shared_org_config", "individual_member_config"):
            raise HTTPException(status_code=400, detail="Invalid config_mode")
        updates["config_mode"] = mode
    if "is_active" in payload:
        updates["is_active"] = bool(payload["is_active"])
    if not updates:
        return {"org": org}
    db[COLL_ORGS].update_one({"org_id": org_id}, {"$set": updates})
    updated = db[COLL_ORGS].find_one({"org_id": org_id})
    updated.pop("_id", None) if updated else None
    return {"org": updated or org}


@router.delete("/api/admin/orgs/{org_id}")
def admin_disable_org(
    org_id: str,
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    db = get_sync_db()
    org = db[COLL_ORGS].find_one({"org_id": org_id})
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    now = time.time()
    db[COLL_ORGS].update_one(
        {"org_id": org_id},
        {"$set": {"is_active": False, "updated_at": now}},
    )
    return {"status": "disabled", "org_id": org_id}


# ── Audit & Invites ─────────────────────────────────────────────────────────


@router.get("/api/admin/audit-logs")
def admin_list_audit_logs(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    event_type: str | None = Query(None),
    org_id: str | None = Query(None),
    actor_user_id: str | None = Query(None),
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    q: dict[str, Any] = {}
    if event_type:
        q["event_type"] = event_type
    if org_id:
        q["org_id"] = org_id
    if actor_user_id:
        q["actor_user_id"] = actor_user_id
    result = _paginate(COLL_AUDIT_LOGS, q, page, per_page, sort=[("created_at", -1)])
    result["items"] = [safe_audit_log(e) for e in result["items"]]
    return result


@router.get("/api/admin/orgs/{org_id}/invites")
def admin_list_org_invites(
    org_id: str,
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    org = get_sync_db()[COLL_ORGS].find_one({"org_id": org_id})
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    invites = list(
        get_sync_db()[COLL_ORG_INVITES]
        .find({"org_id": org_id})
        .sort("created_at", -1)
    )
    return {"invites": [safe_invite(i) for i in invites]}


# ── Config Management ────────────────────────────────────────────────────────


@router.get("/api/admin/configs")
def admin_list_configs(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    owner_type: str | None = Query(None),
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    q: dict[str, Any] = {}
    if owner_type:
        q["owner_type"] = owner_type
    result = _paginate(COLL_USER_CONFIGS, q, page, per_page, sort=[("updated_at", -1)])
    for item in result["items"]:
        item.pop("_id", None)
        files = item.get("files", {})
        for fk in list(files.keys()):
            entry = files[fk]
            if isinstance(entry, dict):
                entry.pop("content", None)
    return result


@router.get("/api/admin/configs/{config_id}")
def admin_get_config(
    config_id: str,
    include_content: bool = Query(False),
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    doc = get_sync_db()[COLL_USER_CONFIGS].find_one({"config_id": config_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Config not found")
    doc.pop("_id", None)
    if not include_content:
        files = doc.get("files", {})
        for fk in list(files.keys()):
            entry = files[fk]
            if isinstance(entry, dict):
                entry.pop("content", None)
    versions = list(
        get_sync_db()[COLL_CONFIG_VERSIONS]
        .find({"config_id": config_id})
        .sort("created_at", -1)
        .limit(50)
    )
    for v in versions:
        v.pop("_id", None)
        if not include_content:
            snapshot = v.get("snapshot", {})
            sf = snapshot.get("files", {})
            for sk in list(sf.keys()):
                entry = sf[sk]
                if isinstance(entry, dict):
                    entry.pop("content", None)
    return {
        "config": doc,
        "versions": versions,
    }


@router.delete("/api/admin/configs/{config_id}")
def admin_delete_config(
    config_id: str,
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    doc = get_sync_db()[COLL_USER_CONFIGS].find_one({"config_id": config_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Config not found")
    now = time.time()
    get_sync_db()[COLL_USER_CONFIGS].update_one(
        {"config_id": config_id},
        {"$set": {"is_active": False, "updated_at": now}},
    )
    return {"status": "disabled", "config_id": config_id}


@router.post("/api/admin/configs/copy")
def admin_copy_config(
    payload: dict[str, Any],
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    from dashboard.backend.services.config_version_service import copy_config as _copy_cfg
    from dashboard.backend.services.user_config import get_config_doc

    source_owner_type = payload.get("source_owner_type", "")
    source_owner_id = payload.get("source_owner_id", "")
    target_owner_type = payload.get("target_owner_type", "")
    target_owner_id = payload.get("target_owner_id", "")
    mode = payload.get("mode", "replace_all")
    if not all([source_owner_type, source_owner_id, target_owner_type, target_owner_id]):
        raise HTTPException(status_code=400, detail="source_owner_type, source_owner_id, target_owner_type, target_owner_id required")
    if mode not in ("replace_all", "merge_missing"):
        raise HTTPException(status_code=400, detail="mode must be 'replace_all' or 'merge_missing'")

    source_doc = get_config_doc(source_owner_type, source_owner_id)
    if not source_doc:
        raise HTTPException(status_code=404, detail="Source config not found")

    try:
        result = _copy_cfg(
            source_owner_type=source_owner_type,
            source_owner_id=source_owner_id,
            target_owner_type=target_owner_type,
            target_owner_id=target_owner_id,
            actor_user_id=_admin["user_id"],
            actor_email=_admin.get("email", ""),
            mode=mode,
            reason="admin_copy",
            org_id=None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "copied", "config": result, "mode": mode}


# ── Provider Configs ────────────────────────────────────────────────────────


@router.get("/api/admin/provider-configs")
def admin_list_provider_configs(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    provider: str | None = Query(None),
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    q: dict[str, Any] = {}
    if provider:
        q["provider"] = provider
    result = _paginate(COLL_PROVIDER_CONFIGS, q, page, per_page, sort=[("updated_at", -1)])
    result["items"] = [safe_provider_config(item) for item in result["items"]]
    return result


# ── Runs ─────────────────────────────────────────────────────────────────────


@router.get("/api/admin/runs")
def admin_list_runs(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user_id: str | None = Query(None),
    status: str | None = Query(None),
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    q: dict[str, Any] = {}
    if user_id:
        q["user_id"] = user_id
    if status:
        q["status"] = status
    result = _paginate(COLL_RUNS, q, page, per_page, sort=[("created_at", -1)])
    result["items"] = [safe_run(item) for item in result["items"]]
    return result


# ── Images ───────────────────────────────────────────────────────────────────


@router.get("/api/admin/images")
def admin_list_images(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user_id: str | None = Query(None),
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    q: dict[str, Any] = {}
    if user_id:
        q["user_id"] = user_id
    result = _paginate(COLL_IMAGES, q, page, per_page, sort=[("created_at", -1)])
    result["items"] = [safe_image(item) for item in result["items"]]
    return result


# ── Prompts ──────────────────────────────────────────────────────────────────


@router.get("/api/admin/prompts")
def admin_list_prompts(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user_id: str | None = Query(None),
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    q: dict[str, Any] = {}
    if user_id:
        q["user_id"] = user_id
    result = _paginate(COLL_PROMPTS, q, page, per_page, sort=[("created_at", -1)])
    result["items"] = [safe_prompt(item) for item in result["items"]]
    return result


# ── Stats & Health ───────────────────────────────────────────────────────────


@router.get("/api/admin/stats")
def admin_stats(
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    db = get_sync_db()
    return {
        "total_users": db[COLL_USERS].count_documents({}),
        "active_users": db[COLL_USERS].count_documents({"is_active": {"$ne": False}}),
        "super_admins": db[COLL_USERS].count_documents({"is_super_admin": True}),
        "total_orgs": db[COLL_ORGS].count_documents({}),
        "active_orgs": db[COLL_ORGS].count_documents({"is_active": True}),
        "total_org_members": db[COLL_ORG_MEMBERS].count_documents({"status": "active"}),
        "total_invites": db[COLL_ORG_INVITES].count_documents({}),
        "pending_invites": db[COLL_ORG_INVITES].count_documents({"status": "pending"}),
        "total_configs": db[COLL_USER_CONFIGS].count_documents({}),
        "active_configs": db[COLL_USER_CONFIGS].count_documents({"is_active": True}),
        "total_config_versions": db[COLL_CONFIG_VERSIONS].count_documents({}),
        "total_sessions": db[COLL_SESSIONS].count_documents({}),
        "total_runs": db[COLL_RUNS].count_documents({}),
        "total_images": db[COLL_IMAGES].count_documents({}),
        "total_audit_logs": db[COLL_AUDIT_LOGS].count_documents({}),
    }


@router.get("/api/admin/health")
def admin_health(
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    db_ok = ping()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "connected" if db_ok else "disconnected",
    }


# ── Readiness ─────────────────────────────────────────────────────────────────


CHECK_ENV_KEYS = frozenset({
    "MONGODB_URI", "APP_SECRET_KEY", "ENCRYPTION_KEY",
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI",
    "FRONTEND_ORIGIN", "SUPER_ADMIN_EMAILS",
})


@router.get("/api/admin/readiness")
def admin_readiness(
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    db = get_sync_db()

    # 1. mongodb
    try:
        db_ok = ping()
        checks.append({
            "key": "mongodb",
            "status": "ok" if db_ok else "error",
            "message": "MongoDB connected" if db_ok else "MongoDB unreachable",
        })
    except Exception as exc:
        checks.append({"key": "mongodb", "status": "error", "message": str(exc)})

    # 2. required_env
    import os
    missing: list[str] = []
    for k in sorted(CHECK_ENV_KEYS):
        if not os.environ.get(k, "").strip():
            missing.append(k)
    if missing:
        checks.append({
            "key": "required_env",
            "status": "error",
            "message": f"Missing env vars: {', '.join(missing)}",
        })
    else:
        checks.append({
            "key": "required_env",
            "status": "ok",
            "message": "All required env vars present",
        })

    # 3. google_oauth
    redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", "")
    frontend_origin = os.environ.get("FRONTEND_ORIGIN", "")
    expected_redirect = f"{frontend_origin}/api/auth/google/callback" if frontend_origin else ""
    if redirect_uri and expected_redirect and redirect_uri != expected_redirect:
        checks.append({
            "key": "google_oauth",
            "status": "warning",
            "message": f"GOOGLE_REDIRECT_URI ({redirect_uri}) does not match FRONTEND_ORIGIN + /api/auth/google/callback ({expected_redirect})",
        })
    elif redirect_uri and not expected_redirect:
        checks.append({
            "key": "google_oauth",
            "status": "warning",
            "message": "GOOGLE_REDIRECT_URI set but FRONTEND_ORIGIN is empty, cannot validate",
        })
    else:
        checks.append({
            "key": "google_oauth",
            "status": "ok" if redirect_uri else "error",
            "message": "Google OAuth configured" if redirect_uri else "GOOGLE_REDIRECT_URI not set",
        })

    # 4. frontend_origin
    cors_origins = os.environ.get("CORS_ORIGINS", "")
    if not frontend_origin:
        checks.append({
            "key": "frontend_origin",
            "status": "error",
            "message": "FRONTEND_ORIGIN not set",
        })
    elif cors_origins and frontend_origin not in cors_origins:
        checks.append({
            "key": "frontend_origin",
            "status": "warning",
            "message": f"FRONTEND_ORIGIN ({frontend_origin}) not found in CORS_ORIGINS ({cors_origins})",
        })
    else:
        checks.append({
            "key": "frontend_origin",
            "status": "ok",
            "message": f"FRONTEND_ORIGIN set{f' and in CORS_ORIGINS' if cors_origins else ''}",
        })

    # 5. super_admins
    sa_emails_env = os.environ.get("SUPER_ADMIN_EMAILS", "").strip()
    sa_in_db = db[COLL_USERS].count_documents({"is_super_admin": True}) if db_ok else 0
    if not sa_emails_env:
        checks.append({
            "key": "super_admins",
            "status": "error",
            "message": "SUPER_ADMIN_EMAILS env var is empty",
        })
    elif sa_in_db == 0:
        checks.append({
            "key": "super_admins",
            "status": "warning",
            "message": f"SUPER_ADMIN_EMAILS has entries but no user with is_super_admin=true found (may need login)",
        })
    else:
        checks.append({
            "key": "super_admins",
            "status": "ok",
            "message": f"{sa_in_db} super admin(s) configured",
        })

    # 6. indexes
    try:
        job_indexes = list(db[COLL_AGENT_JOBS].list_indexes()) if db_ok else []
        has_ttl = any(
            index.get("key") == {"purge_at": 1}
            and index.get("expireAfterSeconds") == 0
            for index in job_indexes
        )
        checks.append({
            "key": "indexes",
            "status": "ok" if has_ttl else "error",
            "message": (
                "Required control-plane indexes present"
                if has_ttl
                else "Terminal agent-job TTL index is missing"
            ),
        })
    except Exception as exc:
        checks.append({
            "key": "indexes",
            "status": "warning",
            "message": f"Could not verify indexes: {exc}",
        })

    # 7. storage
    checks.append({
        "key": "storage",
        "status": "ok",
        "message": "Render is a stateless metadata-only control plane",
    })

    # 8. config_integrity
    if db_ok:
        try:
            from dashboard.backend.services.user_config import CONFIG_KEYS

            active_configs = db[COLL_USER_CONFIGS].count_documents(
                {"is_active": True}
            )
            invalid_configs = db[COLL_USER_CONFIGS].count_documents(
                {
                    "is_active": True,
                    "files": {"$exists": False},
                }
            )
            generic_config = db[COLL_USER_CONFIGS].find_one(
                {
                    "owner_type": "system",
                    "owner_id": "generic",
                    "is_active": True,
                },
                {"files": 1},
            )
            generic_keys = set((generic_config or {}).get("files", {}))
            if invalid_configs or generic_keys != set(CONFIG_KEYS):
                checks.append({
                    "key": "config_integrity",
                    "status": "error",
                    "message": (
                        f"{invalid_configs} invalid config document(s); "
                        "global config file set is incomplete"
                    ),
                })
            else:
                checks.append({
                    "key": "config_integrity",
                    "status": "ok",
                    "message": (
                        f"{active_configs} active Mongo-backed dashboard config(s); "
                        "global config file set is complete"
                    ),
                })
        except Exception as exc:
            checks.append({
                "key": "config_integrity",
                "status": "warning",
                "message": f"Could not check config integrity: {exc}",
            })
    else:
        checks.append({
            "key": "config_integrity",
            "status": "warning",
            "message": "Skipped (DB unavailable)",
        })

    # 9. invite_security
    if db_ok:
        try:
            missing_hash = db[COLL_ORG_INVITES].count_documents({
                "$or": [
                    {"token_hash": {"$exists": False}},
                    {"token_hash": None},
                    {"token_hash": ""},
                ],
            })
            raw_token = db[COLL_ORG_INVITES].count_documents({
                "$or": [
                    {"token": {"$exists": True}},
                    {"raw_token": {"$exists": True}},
                ],
            })
            inv_warnings: list[str] = []
            if missing_hash:
                inv_warnings.append(f"{missing_hash} invite(s) missing token_hash")
            if raw_token:
                inv_warnings.append(f"{raw_token} invite(s) contain raw token field")
            if inv_warnings:
                checks.append({
                    "key": "invite_security",
                    "status": "error" if raw_token else "warning",
                    "message": "; ".join(inv_warnings),
                })
            else:
                checks.append({
                    "key": "invite_security",
                    "status": "ok",
                    "message": "No invite security issues",
                })
        except Exception as exc:
            checks.append({
                "key": "invite_security",
                "status": "warning",
                "message": f"Could not check invite security: {exc}",
            })
    else:
        checks.append({
            "key": "invite_security",
            "status": "warning",
            "message": "Skipped (DB unavailable)",
        })

    # 10. provider_config_security
    if db_ok:
        try:
            provider_docs = db[COLL_PROVIDER_CONFIGS].count_documents({})
            if provider_docs:
                checks.append({
                    "key": "provider_config_security",
                    "status": "error",
                    "message": (
                        f"{provider_docs} legacy provider config document(s) require migration"
                    ),
                })
            else:
                checks.append({
                    "key": "provider_config_security",
                    "status": "ok",
                    "message": "No provider config bodies or secrets are stored in MongoDB",
                })
        except Exception as exc:
            checks.append({
                "key": "provider_config_security",
                "status": "warning",
                "message": f"Could not check provider config security: {exc}",
            })
    else:
        checks.append({
            "key": "provider_config_security",
            "status": "warning",
            "message": "Skipped (DB unavailable)",
        })

    # 11. disabled_users
    if db_ok:
        try:
            disabled = db[COLL_USERS].count_documents({"is_active": False})
            checks.append({
                "key": "disabled_users",
                "status": "ok",
                "message": f"{disabled} disabled user(s)",
            })
        except Exception as exc:
            checks.append({
                "key": "disabled_users",
                "status": "warning",
                "message": f"Could not count disabled users: {exc}",
            })
    else:
        checks.append({
            "key": "disabled_users",
            "status": "warning",
            "message": "Skipped (DB unavailable)",
        })

    # 12. admin_routes
    admin_paths = [
        "/api/admin/overview", "/api/admin/readiness", "/api/admin/users",
        "/api/admin/orgs", "/api/admin/configs", "/api/admin/audit-logs",
        "/api/admin/stats", "/api/admin/health",
    ]
    try:
        for r in router.routes:
            if hasattr(r, "path") and r.path in admin_paths:
                admin_paths.remove(r.path)
        if admin_paths:
            checks.append({
                "key": "admin_routes",
                "status": "warning",
                "message": f"Routes not found in router: {', '.join(admin_paths)}",
            })
        else:
            checks.append({
                "key": "admin_routes",
                "status": "ok",
                "message": "All admin routes registered",
            })
    except Exception as exc:
        checks.append({
            "key": "admin_routes",
            "status": "warning",
            "message": f"Could not verify admin routes: {exc}",
        })

    # Stateless local-data-plane readiness
    if db_ok:
        try:
            incompatible = db[COLL_AGENTS].count_documents({
                "is_active": True,
                "$or": [
                    {"protocol_version": {"$ne": "v1"}},
                    {"supports_pairing": {"$ne": True}},
                    {"device_id": {"$in": [None, ""]}},
                ],
            })
            checks.append({
                "key": "protocol_compatibility",
                "status": "ok" if incompatible == 0 else "error",
                "message": (
                    "All active devices support local-data-plane protocol v1"
                    if incompatible == 0
                    else f"{incompatible} active device(s) are protocol-incompatible"
                ),
            })
        except Exception as exc:
            checks.append({
                "key": "protocol_compatibility",
                "status": "warning",
                "message": f"Could not verify device protocols: {exc}",
            })

        try:
            forbidden_job_fields = [
                "payload", "content", "prompt", "base64", "path", "url",
                "comment", "api_key", "secret",
            ]
            unsafe_jobs = db[COLL_AGENT_JOBS].count_documents({
                "$or": [
                    {field: {"$exists": True}} for field in forbidden_job_fields
                ]
            })
            checks.append({
                "key": "metadata_only_jobs",
                "status": "ok" if unsafe_jobs == 0 else "error",
                "message": (
                    "Agent jobs contain bounded metadata only"
                    if unsafe_jobs == 0
                    else f"{unsafe_jobs} agent job(s) contain prohibited fields"
                ),
            })
        except Exception as exc:
            checks.append({
                "key": "metadata_only_jobs",
                "status": "warning",
                "message": f"Could not verify agent-job policy: {exc}",
            })

        try:
            job_indexes = list(db[COLL_AGENT_JOBS].list_indexes())
            ttl_ok = any(
                dict(index.get("key", {})) == {"purge_at": 1}
                and index.get("expireAfterSeconds") == 0
                for index in job_indexes
            )
            checks.append({
                "key": "ttl_indexes",
                "status": "ok" if ttl_ok else "error",
                "message": (
                    "Terminal agent-job TTL index is present"
                    if ttl_ok
                    else "Terminal agent-job TTL index is missing"
                ),
            })
        except Exception as exc:
            checks.append({
                "key": "ttl_indexes",
                "status": "warning",
                "message": f"Could not verify TTL indexes: {exc}",
            })

        try:
            online_cutoff = time.time() - 180
            online_rows = list(db[COLL_AGENTS].find(
                {
                    "is_active": True,
                    "protocol_version": "v1",
                    "last_heartbeat_at": {"$gte": online_cutoff},
                },
                {"_id": 0, "device_id": 1},
            ))
            online_ids = {
                str(row.get("device_id") or "") for row in online_rows
                if row.get("device_id")
            }
            checks.append({
                "key": "online_devices",
                "status": "ok" if online_ids else "warning",
                "message": f"{len(online_ids)} compatible local device(s) online",
            })

            references = list(db[COLL_LOCAL_CONFIG_REFERENCES].find(
                {},
                {
                    "_id": 0,
                    "resource_id": 1,
                    "authority_device_id": 1,
                    "verified_replica_device_ids": 1,
                },
            ))
            missing = 0
            offline = 0
            for reference in references:
                if not reference.get("resource_id") or not reference.get(
                    "authority_device_id"
                ):
                    missing += 1
                    continue
                candidates = {
                    str(reference.get("authority_device_id") or ""),
                    *map(str, reference.get("verified_replica_device_ids") or []),
                }
                if not candidates & online_ids:
                    offline += 1
            checks.append({
                "key": "resource_references",
                "status": "error" if missing else ("warning" if offline else "ok"),
                "message": (
                    f"{missing} missing and {offline} offline local resource reference(s)"
                    if missing or offline
                    else "All local resource references are complete and online"
                ),
            })
        except Exception as exc:
            checks.extend([
                {
                    "key": "online_devices",
                    "status": "warning",
                    "message": f"Could not count online devices: {exc}",
                },
                {
                    "key": "resource_references",
                    "status": "warning",
                    "message": f"Could not verify local resource references: {exc}",
                },
            ])
    else:
        for key in (
            "protocol_compatibility",
            "metadata_only_jobs",
            "ttl_indexes",
            "online_devices",
            "resource_references",
        ):
            checks.append({
                "key": key,
                "status": "warning",
                "message": "Skipped (DB unavailable)",
            })

    storage_env_keys = (
        "STORAGE_PROVIDER",
        "CLOUDINARY_CLOUD_NAME",
        "CLOUDINARY_API_KEY",
        "CLOUDINARY_API_SECRET",
        "REDIS_URL",
        "GRIDFS_BUCKET",
    )
    configured_content_storage = [
        key for key in storage_env_keys if os.environ.get(key, "").strip()
    ]
    checks.append({
        "key": "content_storage_absent",
        "status": "error" if configured_content_storage else "ok",
        "message": (
            "Render content-storage configuration must be removed"
            if configured_content_storage
            else "No cloud or Render-local content storage is configured"
        ),
    })

    ok_count = sum(1 for c in checks if c["status"] == "ok")
    warn_count = sum(1 for c in checks if c["status"] == "warning")
    err_count = sum(1 for c in checks if c["status"] == "error")
    overall = "error" if err_count else ("warning" if warn_count else "ok")

    return {
        "status": overall,
        "checks": checks,
        "summary": {"ok": ok_count, "warning": warn_count, "error": err_count},
    }


# ── Safe Exports ──────────────────────────────────────────────────────────────


@router.get("/api/admin/exports/users")
def admin_export_users(
    status: str = Query("all"),
    limit: int = Query(1000, ge=1, le=5000),
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    db = get_sync_db()
    q: dict[str, Any] = {}
    if status == "active":
        q["is_active"] = {"$ne": False}
    elif status == "disabled":
        q["is_active"] = False
    items = list(
        db[COLL_USERS].find(q).sort("created_at", -1).limit(limit)
    )
    return {
        "exported_at": time.time(),
        "type": "users",
        "count": len(items),
        "items": [safe_user(u) for u in items],
    }


@router.get("/api/admin/exports/orgs")
def admin_export_orgs(
    limit: int = Query(1000, ge=1, le=5000),
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    db = get_sync_db()
    items = list(
        db[COLL_ORGS].find({}).sort("created_at", -1).limit(limit)
    )
    result_items: list[dict[str, Any]] = []
    for org in items:
        org.pop("_id", None)
        member_count = db[COLL_ORG_MEMBERS].count_documents({"org_id": org.get("org_id", "")})
        invite_count = db[COLL_ORG_INVITES].count_documents({"org_id": org.get("org_id", "")})
        org["member_count"] = member_count
        org["pending_invite_count"] = invite_count
        result_items.append(org)
    return {
        "exported_at": time.time(),
        "type": "orgs",
        "count": len(result_items),
        "items": result_items,
    }


@router.get("/api/admin/exports/configs")
def admin_export_configs(
    limit: int = Query(1000, ge=1, le=5000),
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    db = get_sync_db()
    items = list(
        db[COLL_USER_CONFIGS].find({}).sort("updated_at", -1).limit(limit)
    )
    result_items: list[dict[str, Any]] = []
    for cfg in items:
        cfg.pop("_id", None)
        cfg.pop("files", None)
        version_count = db[COLL_CONFIG_VERSIONS].count_documents({"config_id": cfg.get("config_id", "")})
        cfg["version_count"] = version_count
        result_items.append(cfg)
    return {
        "exported_at": time.time(),
        "type": "configs",
        "count": len(result_items),
        "items": result_items,
    }


@router.get("/api/admin/exports/audit-logs")
def admin_export_audit_logs(
    limit: int = Query(1000, ge=1, le=10000),
    event_type: str | None = Query(None),
    org_id: str | None = Query(None),
    actor_user_id: str | None = Query(None),
    target_type: str | None = Query(None),
    target_id: str | None = Query(None),
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    db = get_sync_db()
    q: dict[str, Any] = {}
    if event_type:
        q["event_type"] = event_type
    if org_id:
        q["org_id"] = org_id
    if actor_user_id:
        q["actor_user_id"] = actor_user_id
    if target_type:
        q["target_type"] = target_type
    if target_id:
        q["target_id"] = target_id
    items = list(
        db[COLL_AUDIT_LOGS].find(q).sort("created_at", -1).limit(limit)
    )
    return {
        "exported_at": time.time(),
        "type": "audit-logs",
        "count": len(items),
        "items": [safe_audit_log(e) for e in items],
    }
