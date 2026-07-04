from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from dashboard.backend.services.storage.base import StorageBackend


class LocalStorageBackend(StorageBackend):
    """Local filesystem storage — used in dev mode.

    Does not actually copy files; just returns metadata pointing at
    the existing local path.
    """

    @property
    def provider_name(self) -> str:
        return "local"

    def upload(self, local_path: Path, public_id: str | None = None, **options: Any) -> dict[str, Any]:
        st = local_path.stat()
        fmt = local_path.suffix.lstrip(".").lower()
        return {
            "storage_provider": "local",
            "local_path": str(local_path),
            "public_id": public_id or local_path.stem,
            "secure_url": "",
            "width": options.get("width", 0),
            "height": options.get("height", 0),
            "format": fmt,
            "bytes": st.st_size,
            "uploaded_at": time.time(),
        }

    def delete(self, public_id: str) -> bool:
        return True

    def get_url(self, public_id: str, **options: Any) -> str | None:
        return None
