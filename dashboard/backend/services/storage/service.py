from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dashboard.backend.db.settings import settings
from dashboard.backend.services.storage.base import StorageBackend
from dashboard.backend.services.storage.local import LocalStorageBackend
from dashboard.backend.services.storage.cloudinary import CloudinaryStorageBackend


_backend: StorageBackend | None = None


def get_storage_backend() -> StorageBackend:
    """Return the active storage backend based on config.

    In production with Cloudinary configured, returns CloudinaryStorageBackend.
    Otherwise returns LocalStorageBackend.
    """
    global _backend
    if _backend is not None:
        return _backend

    configured_provider = os.getenv("STORAGE_PROVIDER", "local").strip().lower()

    if configured_provider == "cloudinary" or (configured_provider == "auto" and settings.is_production):
        cloudinary = CloudinaryStorageBackend()
        if cloudinary.available:
            _backend = cloudinary
            return _backend

    _backend = LocalStorageBackend()
    return _backend


def reset_storage_backend() -> None:
    """Reset cached backend (useful for tests)."""
    global _backend
    _backend = None


def upload_image(local_path: Path, public_id: str | None = None, **options: Any) -> dict[str, Any]:
    """Upload an image file using the active storage backend.

    Returns metadata dict suitable for COLL_IMAGES update.
    """
    backend = get_storage_backend()
    return backend.upload(local_path, public_id=public_id, **options)


def image_metadata_for_db(local_path: Path, run_id: str, user_id: str, batch: str, file_path: str | None = None, **upload_opts: Any) -> dict[str, Any]:
    """Convenience: upload an image and return the full COLL_IMAGES document fields.

    Args:
        local_path: Absolute path to the image file on disk.
        run_id, user_id, batch: MongoDB document identifiers.
        file_path: Relative path for the image (e.g. 'generated_images/v1/...').
                   Defaults to str(local_path) if not provided.
    """
    import time
    import hashlib

    rel = file_path or str(local_path)
    image_id = hashlib.sha256(rel.encode()).hexdigest()[:16]
    upload_result = upload_image(local_path, **upload_opts)
    now = time.time()

    doc: dict[str, Any] = {
        "user_id": user_id,
        "run_id": run_id,
        "image_id": image_id,
        "batch": batch,
        "file_path": rel,
        "local_path": str(local_path),
        "filename": local_path.name,
        "storage_provider": upload_result.get("storage_provider", "local"),
        "status": "completed",
        "created_at": now,
        "updated_at": now,
    }

    if upload_result.get("cloudinary_public_id"):
        doc["cloudinary_public_id"] = upload_result["cloudinary_public_id"]
    if upload_result.get("secure_url"):
        doc["secure_url"] = upload_result["secure_url"]
        doc["storage_url"] = upload_result["secure_url"]
    doc["width"] = upload_result.get("width", 0)
    doc["height"] = upload_result.get("height", 0)
    doc["format"] = upload_result.get("format", "")
    doc["bytes"] = upload_result.get("bytes", 0)
    doc["uploaded_at"] = upload_result.get("uploaded_at", now)
    doc["metadata"] = upload_result.get("metadata", {})

    return doc
