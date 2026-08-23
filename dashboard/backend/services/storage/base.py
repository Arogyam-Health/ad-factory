from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class StorageBackend(ABC):
    """Abstract storage backend for image files.

    Implementations: LocalStorageBackend, CloudinaryStorageBackend.
    """

    @abstractmethod
    def upload(self, local_path: Path, public_id: str | None = None, **options: Any) -> dict[str, Any]:
        """Upload a local file and return metadata.

        Returns dict with at minimum:
          - storage_provider: str
          - storage_url / secure_url: str
          - public_id / local_path: str
          - width: int
          - height: int
          - format: str
          - bytes: int
          - uploaded_at: float (unix timestamp)
        """
        ...

    @abstractmethod
    def delete(self, public_id: str) -> bool:
        """Delete a file by its public ID / path."""
        ...

    @abstractmethod
    def get_url(self, public_id: str, **options: Any) -> str | None:
        """Get the accessible URL for a stored file."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return 'local'."""
        ...
