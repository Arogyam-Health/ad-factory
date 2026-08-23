from __future__ import annotations

from pathlib import Path
from typing import Any

from dashboard.backend.services.storage.base import StorageBackend
from dashboard.backend.services.storage.local import LocalStorageBackend


_backend: StorageBackend | None = None


def get_storage_backend() -> StorageBackend:
    global _backend
    if _backend is not None:
        return _backend
    _backend = LocalStorageBackend()
    return _backend


def reset_storage_backend() -> None:
    global _backend
    _backend = None


def upload_image(local_path: Path, public_id: str | None = None, **options: Any) -> dict[str, Any]:
    backend = get_storage_backend()
    return backend.upload(local_path, public_id=public_id, **options)


def image_metadata_for_db(local_path: Path, run_id: str, user_id: str, batch: str, file_path: str | None = None, **upload_opts: Any) -> dict[str, Any]:
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
        "storage_provider": "local",
        "status": "completed",
        "created_at": now,
        "updated_at": now,
    }

    doc["width"] = upload_result.get("width", 0)
    doc["height"] = upload_result.get("height", 0)
    doc["format"] = upload_result.get("format", "")
    doc["bytes"] = upload_result.get("bytes", 0)
    doc["uploaded_at"] = upload_result.get("uploaded_at", now)
    doc["metadata"] = upload_result.get("metadata", {})

    return doc
