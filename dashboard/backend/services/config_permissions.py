from __future__ import annotations

from typing import Any, Optional

from dashboard.backend.services.user_config import CONFIG_KEYS


def get_config_access_context(
    user_id: str,
    config_id: str | None = None,
    owner_type: str | None = None,
    owner_id: str | None = None,
    org_id: str | None = None,
) -> dict:
    """Build permission context for a config access decision."""
    context = {
        "user_id": user_id,
        "config_id": config_id,
        "owner_type": owner_type,
        "owner_id": owner_id,
        "org_id": org_id,
        "is_owner": owner_type == "user" and owner_id == user_id,
        "membership": None,
        "role": None,
        "org": None,
    }

    if org_id or (owner_type == "org" and owner_id):
        resolved_org_id = org_id or owner_id
        from dashboard.backend.services.org_helper import get_user_org_membership, get_org_by_id
        membership = get_user_org_membership(user_id, resolved_org_id)
        if membership:
            context["membership"] = membership
            context["role"] = membership.get("role", "")
            org = get_org_by_id(resolved_org_id)
            context["org"] = org

    return context


def _get_role_and_org(user_id: str, org_id: str | None) -> tuple[str | None, dict | None]:
    if org_id is None:
        return None, None
    from dashboard.backend.services.org_helper import get_user_org_membership, get_org_by_id
    membership = get_user_org_membership(user_id, org_id)
    if membership is None:
        return None, None
    org = get_org_by_id(org_id)
    return membership.get("role"), org


def can_view_config(user_id: str, config_doc: dict, org_id: str | None = None) -> bool:
    owner_type = config_doc.get("owner_type", "")
    owner_id = config_doc.get("owner_id", "")

    if owner_type == "user" and owner_id == user_id:
        return True

    if owner_type == "org":
        resolved_org_id = org_id or owner_id
        role, _ = _get_role_and_org(user_id, resolved_org_id)
        return role is not None

    return False


def can_edit_config(user_id: str, config_doc: dict, org_id: str | None = None) -> bool:
    owner_type = config_doc.get("owner_type", "")
    owner_id = config_doc.get("owner_id", "")

    if owner_type == "user" and owner_id == user_id:
        if org_id:
            role, _ = _get_role_and_org(user_id, org_id)
            if role == "creator":
                return False
        return True

    if owner_type == "org":
        resolved_org_id = org_id or owner_id
        role, _ = _get_role_and_org(user_id, resolved_org_id)
        return role in ("owner", "config_admin")

    return False


def can_copy_config(user_id: str, org_id: str) -> bool:
    role, _ = _get_role_and_org(user_id, org_id)
    return role in ("owner", "config_admin")


def can_rollback_config(user_id: str, config_doc: dict, org_id: str | None = None) -> bool:
    return can_edit_config(user_id, config_doc, org_id)


def can_view_versions(user_id: str, config_doc: dict, org_id: str | None = None) -> bool:
    owner_type = config_doc.get("owner_type", "")

    if owner_type == "user":
        owner_id = config_doc.get("owner_id", "")
        return owner_id == user_id

    if owner_type == "org":
        resolved_org_id = org_id or config_doc.get("owner_id", "")
        role, _ = _get_role_and_org(user_id, resolved_org_id)
        return role in ("owner", "config_admin")

    return False


def can_view_version_snapshot(user_id: str, config_doc: dict, org_id: str | None = None) -> bool:
    return can_view_versions(user_id, config_doc, org_id)
