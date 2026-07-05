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
)

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
    user = get_sync_db()[COLL_USERS].find_one({"user_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    updates: dict[str, Any] = {}
    if "is_active" in payload:
        updates["is_active"] = bool(payload["is_active"])
    if "is_super_admin" in payload:
        updates["is_super_admin"] = bool(payload["is_super_admin"])
    if "display_name" in payload:
        updates["display_name"] = str(payload["display_name"])
    if not updates:
        return safe_user(user)
    updates["updated_at"] = time.time()
    get_sync_db()[COLL_USERS].update_one({"user_id": user_id}, {"$set": updates})
    updated = get_sync_db()[COLL_USERS].find_one({"user_id": user_id})
    return safe_user(updated or user)


@router.delete("/api/admin/users/{user_id}")
def admin_delete_user(
    user_id: str,
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    user = get_sync_db()[COLL_USERS].find_one({"user_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    now = time.time()
    get_sync_db()[COLL_USERS].update_one(
        {"user_id": user_id},
        {"$set": {"is_active": False, "updated_at": now}},
    )
    get_sync_db()[COLL_SESSIONS].delete_many({"user_id": user_id})
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
    _admin: dict[str, Any] = Depends(require_super_admin_dependency),
) -> dict[str, Any]:
    doc = get_sync_db()[COLL_USER_CONFIGS].find_one({"config_id": config_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Config not found")
    doc.pop("_id", None)
    versions = list(
        get_sync_db()[COLL_CONFIG_VERSIONS]
        .find({"config_id": config_id})
        .sort("created_at", -1)
        .limit(50)
    )
    for v in versions:
        v.pop("_id", None)
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
