from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from .storage import AgentPaths, AgentState, ContentStore


def inspect_legacy_root(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    jobs = sorted(path for path in root.glob("*/job_*") if path.is_dir()) if root.exists() else []
    digest_files: dict[str, list[tuple[Path, int]]] = defaultdict(list)
    image_count = 0
    input_count = 0
    total_bytes = 0
    jobs_missing_metadata = 0

    for job in jobs:
        if not (job / ".agent-job.json").is_file():
            jobs_missing_metadata += 1
        for path in job.rglob("*"):
            if not path.is_file():
                continue
            size = path.stat().st_size
            total_bytes += size
            if "input_images" in path.parts:
                input_count += 1
                digest_files[_sha256(path)].append((path, size))
            if "generated_images" in path.parts and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                image_count += 1

    duplicate_groups = [files for files in digest_files.values() if len(files) > 1]
    duplicate_bytes = sum((len(files) - 1) * files[0][1] for files in duplicate_groups)
    return {
        "root": str(root),
        "job_count": len(jobs),
        "jobs_missing_metadata": jobs_missing_metadata,
        "input_count": input_count,
        "image_count": image_count,
        "total_bytes": total_bytes,
        "duplicate_object_groups": len(duplicate_groups),
        "duplicate_bytes": duplicate_bytes,
        "jobs": [str(path.relative_to(root)) for path in jobs],
        "mutated": False,
    }


def format_inspection(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


def migrate_legacy_root(root: Path, paths: AgentPaths, *, apply: bool = False) -> dict[str, Any]:
    report = inspect_legacy_root(root)
    report.update({"apply": apply, "imported_artifacts": 0, "preserved_unassigned_files": 0})
    if not apply:
        return report
    paths.ensure()
    state = AgentState(paths)
    objects = ContentStore(paths)
    root = root.expanduser().resolve()
    for relative_job in report["jobs"]:
        job = root / relative_job
        metadata_path = job / ".agent-job.json"
        metadata: dict[str, Any] = {}
        if metadata_path.is_file():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                metadata = {}
        run_ids = [str(value) for value in (metadata.get("run_ids") or []) if str(value).strip()]
        batch = str(metadata.get("batch") or job.parent.name)
        batch_match = re.fullmatch(r"v(\d+)", batch, flags=re.IGNORECASE)
        run_number = int(batch_match.group(1)) if batch_match else 0
        job_id = str(metadata.get("job_id") or job.name)

        for input_path in (job / "input_images").rglob("*") if (job / "input_images").exists() else []:
            if input_path.is_file():
                objects.put_file(input_path)

        generated = [
            path for path in (job / "generated_images").rglob("*")
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            and not any(part in {"debug", ".browser_downloads"} for part in path.parts)
        ] if (job / "generated_images").exists() else []
        if len(run_ids) == 1:
            for image_path in generated:
                aspect_ratio = "9:16" if "9_16" in image_path.parts else "4:5"
                prompt_stem = re.sub(r"_(?:4_5|9_16)$", "", image_path.stem)
                state.publish_artifact(
                    source=image_path,
                    owner_key="legacy",
                    run_id=run_ids[0],
                    run_number=run_number,
                    job_id=job_id,
                    item_id="legacy_" + hashlib.sha256(f"{job_id}:{prompt_stem}".encode()).hexdigest()[:16],
                    prompt_id=hashlib.sha256(prompt_stem.encode()).hexdigest()[:16],
                    aspect_ratio=aspect_ratio,
                    filename=image_path.name,
                )
                report["imported_artifacts"] += 1
            continue

        destination = paths.legacy / relative_job
        for source in job.rglob("*"):
            if not source.is_file() or "input_images" in source.parts:
                continue
            target = destination / source.relative_to(job)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or _sha256(target) != _sha256(source):
                shutil.copy2(source, target)
            report["preserved_unassigned_files"] += 1
    report["mutated"] = True
    return report


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
