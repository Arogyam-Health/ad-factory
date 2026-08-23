from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from local_agent_runtime.storage import AgentPaths, metadata_job_payload

from .service import validate_job_envelope


def cleanup_local_job_payloads(
    paths: AgentPaths, *, apply: bool = False
) -> dict[str, Any]:
    """Inspect or remove content-bearing fields from legacy SQLite job rows."""
    paths.ensure()
    changed_rows: list[tuple[str, str]] = []
    with sqlite3.connect(paths.database) as conn:
        rows = conn.execute("SELECT job_id, payload_json FROM jobs").fetchall()
        for job_id, serialized in rows:
            try:
                current = json.loads(serialized)
            except (TypeError, json.JSONDecodeError):
                current = {}
            clean = metadata_job_payload(current, strict=False)
            normalized = json.dumps(clean, ensure_ascii=True, sort_keys=True)
            try:
                current_normalized = json.dumps(
                    current, ensure_ascii=True, sort_keys=True
                )
            except TypeError:
                current_normalized = ""
            if normalized != current_normalized:
                changed_rows.append((str(job_id), normalized))
        if apply:
            now = time.time()
            for job_id, normalized in changed_rows:
                conn.execute(
                    "UPDATE jobs SET payload_json = ?, updated_at = ? WHERE job_id = ?",
                    (normalized, now, job_id),
                )
    return {
        "kind": "local_agent_jobs",
        "apply": bool(apply),
        "scanned": len(rows),
        "changed": len(changed_rows),
        "mutated": bool(apply and changed_rows),
    }


def _clean_mongo_job(doc: dict[str, Any]) -> dict[str, Any]:
    payload = doc.get("payload") if isinstance(doc.get("payload"), dict) else {}
    parameters = {
        key: payload[key]
        for key in (
            "engine",
            "mode",
            "count",
            "manifest_version",
            "config_version_id",
            "prompt_version_id",
            "resource_version",
            "upload_set_version",
            "output_version",
        )
        if key in payload
    }
    parameters.update(
        doc.get("parameters") if isinstance(doc.get("parameters"), dict) else {}
    )
    now = time.time()
    status = str(doc.get("status") or "pending")
    user_id = str(doc.get("user_id") or "")
    job_type = str(doc.get("job_type") or "execute_run")
    clean: dict[str, Any] = {
        "job_id": str(doc.get("job_id") or ""),
        "agent_id": str(doc.get("agent_id") or ""),
        "device_id": str(doc.get("device_id") or ""),
        "user_id": user_id,
        "owner_type": str(doc.get("owner_type") or "user"),
        "owner_id": str(doc.get("owner_id") or user_id),
        "run_id": str(doc.get("run_id") or payload.get("run_id") or f"control:{job_type}"),
        "job_type": job_type,
        "command": str(
            doc.get("command")
            or {
                "run_browser_batch": "generate_images",
                "run_chatgpt_batch": "generate_images",
            }.get(job_type, job_type)
        ),
        "parameters": parameters,
        "client_operation_id": str(
            doc.get("client_operation_id") or f"migration:{doc.get('job_id') or ''}"
        ),
        "status": status,
        "progress_code": str(
            doc.get("progress_code")
            or ("completed" if status == "completed" else "queued")
        ),
        "created_at": float(doc.get("created_at") or now),
        "updated_at": float(doc.get("updated_at") or now),
        "started_at": doc.get("started_at"),
        "completed_at": doc.get("completed_at"),
        "lease_expires_at": doc.get("lease_expires_at"),
        "fence": int(doc.get("fence") or 0),
        "purge_at": doc.get("purge_at"),
    }
    if status in {"completed", "failed", "canceled"} and clean["purge_at"] is None:
        clean["purge_at"] = datetime.now(timezone.utc) + timedelta(days=7)
    for key in (
        "claim_id",
        "terminal_event_id",
        "cancel_requested_at",
        "error_code",
        "error_message",
    ):
        if doc.get(key) is not None:
            clean[key] = doc[key]
    return validate_job_envelope(clean)


def cleanup_mongo_job_documents(
    collection: Any, *, apply: bool = False
) -> dict[str, Any]:
    """Dry-run-first metadata-only cleanup for legacy Mongo agent jobs."""
    changed: list[tuple[dict[str, Any], dict[str, Any], dict[str, str]]] = []
    invalid: list[dict[str, Any]] = []
    scanned = 0
    for document in collection.find({}):
        scanned += 1
        selector = (
            {"_id": document["_id"]}
            if document.get("_id") is not None
            else {"job_id": document.get("job_id")}
        )
        try:
            clean = _clean_mongo_job(document)
        except (TypeError, ValueError):
            invalid.append(selector)
            continue
        comparable = {
            key: value for key, value in document.items() if key != "_id"
        }
        if comparable == clean:
            continue
        unset = {
            key: ""
            for key in document
            if key != "_id" and key not in clean
        }
        changed.append((selector, clean, unset))
    if apply:
        for selector, clean, unset in changed:
            update: dict[str, Any] = {"$set": clean}
            if unset:
                update["$unset"] = unset
            collection.update_one(selector, update)
        for selector in invalid:
            collection.delete_one(selector)
    return {
        "kind": "mongo_agent_jobs",
        "apply": bool(apply),
        "scanned": scanned,
        "changed": len(changed),
        "invalid": len(invalid),
        "deleted": len(invalid) if apply else 0,
        "mutated": bool(apply and (changed or invalid)),
    }
