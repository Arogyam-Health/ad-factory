from __future__ import annotations

"""Stateless Render control-plane boundary enforcement."""

from typing import Any


METADATA_COLLECTIONS = frozenset(
    {"runs", "prompts", "images", "agent_jobs", "audit_logs", "configs"}
)

_FORBIDDEN_FIELD_PARTS = frozenset(
    {
        "base64",
        "body",
        "capability",
        "comment",
        "content",
        "file_path",
        "files",
        "browser_log",
        "local_path",
        "localhost_url",
        "log_body",
        "payload",
        "prompt_text",
        "raw_log",
        "request",
        "response",
        "secret",
        "snapshot",
        "token",
        "url",
    }
)
_SECRET_FIELD_PARTS = frozenset(
    {
        "api_key",
        "authorization",
        "client_secret",
        "cookie",
        "encrypted_api_key",
        "password",
        "provider_key",
        "raw_token",
        "session",
        "token_hash",
    }
)
_FORBIDDEN_VALUE_MARKERS = (
    "data:",
    "file://",
    "http://127.0.0.1",
    "http://localhost",
    "https://127.0.0.1",
    "https://localhost",
)

_EXACT_CONTENT_ROUTES = frozenset(
    {
        "/api/config/provider",
        "/api/google/models",
        "/api/input-images",
        "/api/input-prompt",
        "/api/opencode/catalog",
        "/api/product-doc",
        "/api/prompt-file-content",
        "/api/runs/cancel-current",
        "/api/runs/download-batches",
        "/api/runs/execute",
        "/api/storage/info",
        "/api/upload-input-images",
        "/api/user/json-blobs/bootstrap",
    }
)
_CONTENT_PREFIXES = (
    "/api/file-content",
    "/api/files/",
    "/api/generic-config",
    "/api/reference-images",
    "/api/reference-workspace",
    "/api/seeds",
    "/api/user/json-blobs",
)
_RUN_CONTENT_SUFFIXES = (
    "/content",
    "/delete-image",
    "/delete-prompt",
    "/download-batch",
    "/download-image",
    "/edit-prompt",
    "/export-on-image-copy",
    "/generate-916",
    "/generate-916-selected",
    "/generate-images-45",
    "/generate-images-916-from-45",
    "/import-on-image-copy",
    "/mark-images-to-regenerate",
    "/prompt-copies",
    "/reference-status",
    "/regenerate-queued-images",
    "/replace-image",
    "/revisions/",
    "/restore-images-from-queue",
    "/revise-image",
)


def is_render_content_route(method: str, path: str) -> bool:
    """Return whether a request belongs exclusively on the localhost data plane."""
    normalized = "/" + str(path or "").lstrip("/")
    if normalized in _EXACT_CONTENT_ROUTES:
        return True
    if normalized.startswith(_CONTENT_PREFIXES):
        return True
    if normalized.startswith("/api/batch/generate-images"):
        return True
    if normalized == "/api/runs/execute-reference":
        return True
    if normalized.startswith("/api/runs/") and any(
        suffix in normalized for suffix in _RUN_CONTENT_SUFFIXES
    ):
        return True
    return False


def validate_metadata_document(
    collection: str, document: dict[str, Any]
) -> dict[str, Any]:
    """Reject content-bearing fields or values before a control-plane write."""
    if collection not in METADATA_COLLECTIONS or not isinstance(document, dict):
        raise ValueError("Unsupported control-plane metadata document")

    def walk(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            if len(value) > 100:
                raise ValueError("Metadata object is too large")
            for raw_key, child in value.items():
                key = str(raw_key).lower()
                if (
                    key in _FORBIDDEN_FIELD_PARTS
                    or key in _SECRET_FIELD_PARTS
                    or key.endswith(
                        (
                            "_body",
                            "_capability",
                            "_content",
                            "_log",
                            "_path",
                            "_request",
                            "_response",
                            "_secret",
                            "_url",
                        )
                    )
                    or "base64" in key
                ):
                    raise ValueError(
                        f"Content-bearing field is forbidden: {'.'.join((*path, key))}"
                    )
                walk(child, (*path, key))
        elif isinstance(value, (list, tuple)):
            if len(value) > 500:
                raise ValueError("Metadata list is too large")
            for child in value:
                walk(child, path)
        elif isinstance(value, bytes):
            raise ValueError("Binary values are forbidden in control-plane metadata")
        elif isinstance(value, str):
            if len(value) > 1024:
                raise ValueError("Metadata string is too large")
            lowered = value.lower()
            if any(marker in lowered for marker in _FORBIDDEN_VALUE_MARKERS):
                raise ValueError("Local or embedded content references are forbidden")
            if value.startswith(("/", "\\\\")) or (
                len(value) > 2 and value[1:3] in {":\\", ":/"}
            ):
                raise ValueError("Local paths are forbidden in control-plane metadata")

    walk(document)
    return document
