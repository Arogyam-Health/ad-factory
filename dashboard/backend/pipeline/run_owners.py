from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from dashboard.backend.pipeline.paths import GENERATED_IMAGES_ROOT, ROOT, RUNS_ROOT

# Run ownership registry: maps run_id -> user_id
# Populated when runs are created; used for ownership checks and LLM traces
_run_owner_registry: dict[str, str] = {}
_run_owner_registry_lock = threading.Lock()
def _resolve_run_owner_scope(user_id: str, org_id: str = "") -> tuple[str, str]:
    """Use the shared org as the run scope; individual org runs remain user-scoped."""
    try:
        from dashboard.backend.services.org_helper import (
            get_org_by_id,
            get_user_default_org,
            get_user_org_membership,
        )

        target_org_id = org_id or str((get_user_default_org(user_id) or {}).get("org_id") or "")
        if target_org_id and get_user_org_membership(user_id, target_org_id):
            org = get_org_by_id(target_org_id)
            if org and org.get("config_mode", "shared_org_config") == "shared_org_config":
                return "org", target_org_id
    except Exception:
        pass
    return "user", user_id


def _record_run_owner(
    run_id: str,
    user_id: str,
    config: dict[str, Any] | None = None,
    *,
    owner_type: str = "user",
    owner_id: str = "",
) -> None:
    """Record that a user owns a run, both in-memory and in MongoDB."""
    with _run_owner_registry_lock:
        _run_owner_registry[run_id] = user_id
    try:
        from dashboard.backend.services.run_storage import create_run, get_run, update_run
        doc = get_run(user_id, run_id)
        if doc:
            update_run(user_id, run_id, {"status": "created"})
        else:
            create_run(user_id, run_id, {
                "status": "created",
                "config": config or {},
                "owner_type": owner_type,
                "owner_id": owner_id or user_id,
            })
    except Exception:
        pass
    try:
        owner_path = RUNS_ROOT / run_id / ".owner"
        owner_path.parent.mkdir(parents=True, exist_ok=True)
        owner_path.write_text(user_id, encoding="utf-8")
    except Exception:
        pass


def _update_run_status_db(run_id: str, status: str, user_id: str | None = None, extra: dict[str, Any] | None = None) -> None:
    """Update a run's status in MongoDB (best-effort, no-op on failure)."""
    if not user_id:
        user_id = _get_run_owner(run_id) or ""
    if not user_id:
        return
    try:
        from dashboard.backend.services.run_storage import update_run
        updates: dict[str, Any] = {"status": status}
        if extra:
            updates.update(extra)
        update_run(user_id, run_id, updates)
    except Exception:
        pass


def _manifest_fields_for_db(manifest: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "batch",
        "prompt_files",
        "image_files",
        "regeneration_queue_files",
        "image_generated",
        "generated_variant",
        "generated_images_for_prompts_45",
        "generated_images_for_prompts_916",
        "conversion_failures",
        "llm_mode",
        "copy_source",
        "opencode_model",
        "context_source",
        "context_extractor_model",
        "copy_generation_failures",
        "copy_generation_warnings",
        "copy_generation_notes",
        "copy_warning_log",
        "copy_session_rollovers",
        "copy_session_schedule",
        "copy_session_log",
        "failed_ads_count",
        "failed_ads",
        "failed_ads_log",
        "visual_pattern_reuse_from_run_id",
        "copy_edits_applied",
        "on_image_copy_import_applied",
        "image_sources_file",
        "input_images_dir",
        "input_images_uploaded",
        "local_artifacts",
        "local_output_dir",
        "artifact_base_url",
        "local_agent_warnings",
        "run_number",
        "display_batch",
        "flow_type",
        "prompt_count",
        "image_count",
        "image_generation",
        "device_id",
        "agent_id",
        "reference_job_id",
        "updated_at",
    }
    return {key: manifest.get(key) for key in fields if key in manifest}


def _persist_run_manifest_db(run_id: str, user_id: str | None, manifest: dict[str, Any], status: str = "completed") -> None:
    """Persist dashboard-visible run manifest fields to MongoDB, best-effort."""
    user_id = user_id or _get_run_owner(run_id) or ""
    if not user_id:
        return
    try:
        from dashboard.backend.db.client import get_sync_db
        from dashboard.backend.db.collections import COLL_RUNS

        now = time.time()
        updates = _manifest_fields_for_db(manifest)
        updates.update({
            "user_id": user_id,
            "run_id": run_id,
            "status": status,
            "manifest_summary": {
                "batch": manifest.get("batch", ""),
                "prompt_count": len(manifest.get("prompt_files") or []),
                "image_count": len(manifest.get("image_files") or []),
            },
            "updated_at": now,
        })
        get_sync_db()[COLL_RUNS].update_one(
            {"user_id": user_id, "run_id": run_id},
            {"$set": updates, "$setOnInsert": {"created_at": now, "config": {}}},
            upsert=True,
        )
    except Exception:
        pass


def _get_run_owner(run_id: str) -> str | None:
    """Resolve the owner user_id for a run.

    Checks: in-memory registry -> MongoDB -> .owner file.
    Returns None if unknown.
    """
    with _run_owner_registry_lock:
        uid = _run_owner_registry.get(run_id)
        if uid is not None:
            return uid
    try:
        from dashboard.backend.db.client import get_sync_db
        from dashboard.backend.db.collections import COLL_RUNS
        doc = get_sync_db()[COLL_RUNS].find_one({"run_id": run_id}, {"user_id": 1})
        if doc and doc.get("user_id"):
            uid = doc["user_id"]
            with _run_owner_registry_lock:
                _run_owner_registry[run_id] = uid
            return uid
    except Exception:
        pass
    owner_file = RUNS_ROOT / run_id / ".owner"
    if owner_file.exists():
        uid = owner_file.read_text(encoding="utf-8").strip()
        if uid:
            with _run_owner_registry_lock:
                _run_owner_registry[run_id] = uid
            return uid
    return None


def _parse_prompt_meta(file_path: str) -> dict[str, str]:
    """Parse format, language, persona_slug, concept_angle from a prompt filename.

    Expected pattern: {FMT}_{slug}_{LANG}_{angle}[_A{NN}].txt
    Returns dict with format, language, persona_slug, concept_angle keys (may be empty).
    """
    import re
    stem = Path(file_path).stem
    m = re.match(
        r"^(HERO|BA|TEST|FEAT|UGC)_(.+?)_(EN|HI|HINGLISH)_(.+?)(?:_A\d{2,})?$",
        stem,
        re.IGNORECASE,
    )
    if m:
        return {
            "format": m.group(1).upper(),
            "persona_slug": m.group(2),
            "language": m.group(3).upper(),
            "concept_angle": m.group(4),
        }
    return {"format": "", "persona_slug": "", "language": "", "concept_angle": ""}


def _store_output_mapping(run_id: str, user_id: str, batch: str, manifest: dict[str, Any]) -> None:
    """Scan output and generated_images dirs for a batch and store file->run mapping in MongoDB.

    Called after a pipeline completes so that download endpoints can resolve
    file paths to run owners via MongoDB instead of parsing filesystem paths.
    Also upserts prompt and image documents into COLL_PROMPTS and COLL_IMAGES
    so the DB-backed download endpoints (/image/{image_id}, /prompt/{prompt_id})
    have usable records.
    Best-effort: silently ignores if MongoDB is unavailable.
    """
    try:
        import time
        import hashlib
        from dashboard.backend.db.client import get_sync_db
        from dashboard.backend.db.collections import COLL_FILE_MAP, COLL_PROMPTS, COLL_IMAGES

        db = get_sync_db()
        now = time.time()

        # Remove any stale mapping for this run
        db[COLL_FILE_MAP].delete_many({"run_id": run_id})

        file_entries: list[dict[str, Any]] = []

        # Map the batch's output directory
        output_batch_dir = ROOT / "output" / batch
        if output_batch_dir.is_dir():
            file_entries.append({
                "file_path": f"output/{batch}/",
                "file_type": "output_dir",
                "run_id": run_id,
                "user_id": user_id,
                "batch": batch,
                "created_at": now,
            })
            for f in sorted(output_batch_dir.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(ROOT).as_posix()
                    ext = f.suffix.lower()
                    file_type = "output_json" if ext == ".json" else "prompt"
                    file_entries.append({
                        "file_path": rel,
                        "file_type": file_type,
                        "run_id": run_id,
                        "user_id": user_id,
                        "batch": batch,
                        "created_at": now,
                    })
                    if ext == ".txt":
                        prompt_id = hashlib.sha256(rel.encode()).hexdigest()[:16]
                        meta = _parse_prompt_meta(rel)
                        content = f.read_text(encoding="utf-8", errors="ignore")
                        db[COLL_PROMPTS].update_one(
                            {"prompt_id": prompt_id},
                            {"$set": {
                                "user_id": user_id,
                                "run_id": run_id,
                                "prompt_id": prompt_id,
                                "batch": batch,
                                "file_path": rel,
                                "format": meta.get("format", ""),
                                "persona_slug": meta.get("persona_slug", ""),
                                "language": meta.get("language", ""),
                                "concept_angle": meta.get("concept_angle", ""),
                                "filename": f.name,
                                "content": content,
                                "status": "completed",
                                "storage_provider": "local",
                                "created_at": now,
                                "updated_at": now,
                            }},
                            upsert=True,
                        )

        # Map the batch's generated_images directory
        img_batch_dir = GENERATED_IMAGES_ROOT / batch
        if img_batch_dir.is_dir():
            file_entries.append({
                "file_path": f"generated_images/{batch}/",
                "file_type": "image_dir",
                "run_id": run_id,
                "user_id": user_id,
                "batch": batch,
                "created_at": now,
            })
            for f in sorted(img_batch_dir.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(ROOT).as_posix()
                    ext = f.suffix.lower()
                    file_type = "image_metadata" if ext == ".json" else "image"
                    file_entries.append({
                        "file_path": rel,
                        "file_type": file_type,
                        "run_id": run_id,
                        "user_id": user_id,
                        "batch": batch,
                        "created_at": now,
                    })
                    if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                        image_id = hashlib.sha256(rel.encode()).hexdigest()[:16]
                        meta: dict[str, Any] = {}
                        sidecar = f.with_suffix(f"{f.suffix}.json")
                        if not sidecar.exists():
                            sidecar = f.with_suffix(".json")
                        if sidecar.exists():
                            try:
                                meta = json.loads(sidecar.read_text(encoding="utf-8", errors="ignore"))
                            except Exception:
                                pass
                        # Store image metadata in MongoDB
                        try:
                            from dashboard.backend.services.storage.service import image_metadata_for_db
                            img_doc = image_metadata_for_db(
                                f, run_id=run_id, user_id=user_id, batch=batch,
                                file_path=rel,
                                width=meta.get("width", 0), height=meta.get("height", 0),
                            )
                        except Exception:
                            img_doc = {
                                "user_id": user_id,
                                "run_id": run_id,
                                "image_id": image_id,
                                "batch": batch,
                                "file_path": rel,
                                "local_path": rel,
                                "filename": f.name,
                                "format": meta.get("format", ""),
                                "status": "completed",
                                "storage_provider": "local",
                                "metadata": meta,
                                "created_at": now,
                                "updated_at": now,
                            }
                        db[COLL_IMAGES].update_one(
                            {"image_id": image_id},
                            {"$set": img_doc},
                            upsert=True,
                        )

        # Also map all known prompt files from manifest
        for pf in manifest.get("prompt_files") or []:
            pf_str = str(pf).replace("\\", "/")
            if not any(e["file_path"] == pf_str for e in file_entries):
                file_entries.append({
                    "file_path": pf_str,
                    "file_type": "prompt",
                    "run_id": run_id,
                    "user_id": user_id,
                    "batch": batch,
                    "created_at": now,
                })

        # Also map all known image files from manifest
        for imgf in manifest.get("image_files") or []:
            imgf_str = str(imgf).replace("\\", "/")
            if not any(e["file_path"] == imgf_str for e in file_entries):
                file_entries.append({
                    "file_path": imgf_str,
                    "file_type": "image",
                    "run_id": run_id,
                    "user_id": user_id,
                    "batch": batch,
                    "created_at": now,
                })

        if file_entries:
            db[COLL_FILE_MAP].insert_many(file_entries)
    except Exception:
        pass


def _resolve_file_owner(file_path: str) -> dict[str, str] | None:
    """Look up the owner of a file path from the MongoDB file map.

    Returns dict with "run_id" and "user_id" if found, else None.
    """
    try:
        from dashboard.backend.db.client import get_sync_db
        from dashboard.backend.db.collections import COLL_FILE_MAP
        doc = get_sync_db()[COLL_FILE_MAP].find_one(
            {"file_path": file_path},
            {"run_id": 1, "user_id": 1},
        )
        if doc and doc.get("run_id") and doc.get("user_id"):
            return {"run_id": doc["run_id"], "user_id": doc["user_id"]}
    except Exception:
        pass
    return None


def _extract_run_id_from_output_path(path: str) -> str | None:
    """Resolve output/{batch}/... path to run_id via MongoDB file map."""
    clean = path.replace("\\", "/").lstrip("/")
    owner = _resolve_file_owner(clean)
    if owner:
        return owner["run_id"]
    # Fallback: extract batch from path, look up in runs table
    parts = clean.split("/")
    for i, p in enumerate(parts):
        if p.startswith("v") and p[1:].isdigit():
            batch = p
            try:
                from dashboard.backend.db.client import get_sync_db
                from dashboard.backend.db.collections import COLL_RUNS
                doc = get_sync_db()[COLL_RUNS].find_one(
                    {"batch": batch},
                    {"run_id": 1, "user_id": 1},
                    sort=[("created_at", -1)],
                )
                if doc and doc.get("run_id"):
                    return doc["run_id"]
            except Exception:
                pass
            break
    return None


def _extract_run_id_from_generated_path(path: str) -> str | None:
    """Resolve generated_images/{batch}/... path to run_id via MongoDB file map."""
    clean = path.replace("\\", "/").lstrip("/")
    owner = _resolve_file_owner(clean)
    if owner:
        return owner["run_id"]
    # Legacy fallback: check if path contains run_X / batch_v prefix
    parts = clean.split("/")
    for part in parts:
        if part.startswith("run_"):
            return part
    # Fallback to batch lookup
    for i, p in enumerate(parts):
        if p.startswith("v") and p[1:].isdigit():
            batch = p
            try:
                from dashboard.backend.db.client import get_sync_db
                from dashboard.backend.db.collections import COLL_RUNS
                doc = get_sync_db()[COLL_RUNS].find_one(
                    {"batch": batch},
                    {"run_id": 1},
                    sort=[("created_at", -1)],
                )
                if doc and doc.get("run_id"):
                    return doc["run_id"]
            except Exception:
                pass
            break
    return None
