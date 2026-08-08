from __future__ import annotations

import hashlib
import json
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
