from __future__ import annotations

import io
import os
import time
from pathlib import Path
from typing import Any

from dashboard.backend.services.storage.base import StorageBackend


class CloudinaryStorageBackend(StorageBackend):
    """Cloudinary storage backend — uploads image files to Cloudinary."""

    def __init__(self) -> None:
        self._cloud_name: str = os.getenv("CLOUDINARY_CLOUD_NAME", "")
        self._api_key: str = os.getenv("CLOUDINARY_API_KEY", "")
        self._api_secret: str = os.getenv("CLOUDINARY_API_SECRET", "")
        self._folder: str = "ad-factory"
        self._available = bool(self._cloud_name and self._api_key and self._api_secret)

    @property
    def provider_name(self) -> str:
        return "cloudinary"

    @property
    def available(self) -> bool:
        return self._available

    def upload(self, local_path: Path, public_id: str | None = None, **options: Any) -> dict[str, Any]:
        if not self._available:
            return {"storage_provider": "cloudinary", "error": "Cloudinary not configured"}

        import cloudinary
        import cloudinary.uploader
        import cloudinary.api

        cloudinary.config(
            cloud_name=self._cloud_name,
            api_key=self._api_key,
            api_secret=self._api_secret,
            secure=True,
        )

        upload_public_id = public_id or local_path.stem
        folder = options.get("folder", self._folder)

        result = cloudinary.uploader.upload(
            str(local_path),
            public_id=f"{folder}/{upload_public_id}",
            overwrite=True,
            resource_type="image",
        )

        now = time.time()
        return {
            "storage_provider": "cloudinary",
            "cloudinary_public_id": result.get("public_id", ""),
            "secure_url": result.get("secure_url", ""),
            "local_path": str(local_path),
            "width": result.get("width", 0),
            "height": result.get("height", 0),
            "format": result.get("format", ""),
            "bytes": result.get("bytes", 0),
            "uploaded_at": now,
            "metadata": {
                "etag": result.get("etag", ""),
                "version": result.get("version", ""),
                "signature": result.get("signature", ""),
                "original_filename": result.get("original_filename", ""),
            },
        }

    def delete(self, public_id: str) -> bool:
        if not self._available:
            return False
        import cloudinary.uploader
        cloudinary.config(
            cloud_name=self._cloud_name,
            api_key=self._api_key,
            api_secret=self._api_secret,
            secure=True,
        )
        result = cloudinary.uploader.destroy(public_id)
        return result.get("result") == "ok"

    def get_url(self, public_id: str, **options: Any) -> str | None:
        if not self._available:
            return None
        import cloudinary.utils
        cloudinary.config(
            cloud_name=self._cloud_name,
            api_key=self._api_key,
            api_secret=self._api_secret,
            secure=True,
        )
        url, _ = cloudinary.utils.cloudinary_url(public_id, **options)
        return url
