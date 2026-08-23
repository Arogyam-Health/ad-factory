from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any

from .storage import AgentPaths, AgentState


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def migrate_render_files(
    *,
    owner_roots: dict[str, Path],
    unassigned_roots: list[Path],
    importer: Any,
    apply: bool = False,
) -> dict[str, Any]:
    """Explicitly import owner-scoped Render files and only count ownerless files."""
    report: dict[str, Any] = {
        "apply": bool(apply),
        "owner_roots": len(owner_roots),
        "owner_scoped": 0,
        "imported": 0,
        "unassigned": 0,
        "errors": 0,
        "redacted": True,
    }
    candidates: list[tuple[str, Path, Path]] = []
    for owner_key, raw_root in sorted(owner_roots.items()):
        root = raw_root.expanduser().resolve()
        if not owner_key or not root.exists():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            candidates.append((owner_key, root, path))
            report["owner_scoped"] += 1
    for raw_root in unassigned_roots:
        root = raw_root.expanduser().resolve()
        if root.exists():
            report["unassigned"] += sum(1 for item in root.rglob("*") if item.is_file())
    if not apply:
        return report
    if not getattr(importer, "authenticated", False):
        raise RuntimeError("Authenticated local agent session is required")
    for owner_key, root, path in candidates:
        relative = path.relative_to(root).as_posix()
        digest = _sha256(path)
        operation_id = "mig13_render_" + hashlib.sha256(
            f"{owner_key}\0{relative}\0{digest}".encode("utf-8")
        ).hexdigest()
        try:
            result = importer.import_content(
                owner_key=owner_key,
                kind=_legacy_kind(path),
                logical_key="render-" + hashlib.sha256(relative.encode()).hexdigest()[:24],
                content=path.read_bytes(),
                media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                operation_id=operation_id,
                expected_sha256=digest,
            )
            if str(result.get("sha256") or "") != digest:
                raise ValueError("Local import hash verification failed")
            report["imported"] += 1
        except Exception:
            report["errors"] += 1
    return report


def migrate_local_sources(
    paths: AgentPaths,
    *,
    source_roots: dict[str, Path],
    owner_key: str,
    apply: bool = False,
) -> dict[str, Any]:
    """Import legacy artifacts, revisions, content objects, and staging without deleting sources."""
    allowed_categories = {
        "artifacts",
        "content_store",
        "output_roots",
        "prompt_job_staging",
        "revision_history",
    }
    unknown = set(source_roots) - allowed_categories
    if unknown:
        raise ValueError("Unsupported local migration source category")
    candidates: list[tuple[str, Path, Path]] = []
    for category, raw_root in sorted(source_roots.items()):
        root = raw_root.expanduser().resolve()
        if not root.exists():
            continue
        for source in sorted(item for item in root.rglob("*") if item.is_file()):
            candidates.append((category, root, source))
    report: dict[str, Any] = {
        "apply": bool(apply),
        "scanned": len(candidates),
        "imported": 0,
        "verified": 0,
        "errors": 0,
        "redacted": True,
    }
    if not apply:
        return report
    if not owner_key:
        raise ValueError("An explicit owner is required for local source migration")
    state = AgentState(paths)
    for category, root, source in candidates:
        relative = source.relative_to(root).as_posix()
        digest = _sha256(source)
        operation_id = "mig13_local_" + hashlib.sha256(
            f"{category}\0{owner_key}\0{relative}\0{digest}".encode("utf-8")
        ).hexdigest()
        logical_key = (
            f"migration/{category}/"
            + hashlib.sha256(relative.encode("utf-8")).hexdigest()[:24]
        )
        try:
            version = state.put_resource(
                source=source,
                owner_key=owner_key,
                kind=_legacy_kind(source, category),
                logical_key=logical_key,
                operation_id=operation_id,
                metadata={"migration_category": category},
                media_type=mimetypes.guess_type(source.name)[0]
                or "application/octet-stream",
            )
            report["imported"] += 1
            if version.object_sha256 != digest or _sha256(version.path) != digest:
                raise ValueError("Local migration hash verification failed")
            report["verified"] += 1
        except Exception:
            report["errors"] += 1
    return report


def _legacy_kind(path: Path, category: str = "") -> str:
    if category == "revision_history" or "revision" in path.parts:
        return "legacy_revision"
    if category == "prompt_job_staging":
        return "legacy_staging"
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        return "generated_image"
    if path.suffix.lower() in {".txt", ".md"}:
        return "legacy_document"
    return "legacy_content"
