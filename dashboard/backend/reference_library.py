from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from dashboard.backend.pipeline.clock import ensure_dirs, make_run_id, now_iso
from dashboard.backend.pipeline.input_assets import store_uploaded_input_images
from dashboard.backend.pipeline.paths import GENERATED_IMAGES_ROOT, ROOT, RUNS_ROOT, STORAGE_ROOT
import dashboard.backend.reference_flow as _reference_flow

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
REFERENCE_LIBRARY_DIR = STORAGE_ROOT / "reference_images"
_REFERENCE_BATCH_LOCK = threading.Lock()


def _safe_filename(value: str, fallback: str) -> str:
    name = Path(value or "").name.strip()
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or fallback


def _sidecar_path(image_path: Path) -> Path:
    return image_path.with_suffix(image_path.suffix + ".json")


def _relative_path(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def _library_item(image_path: Path) -> dict[str, Any]:
    sidecar = _sidecar_path(image_path)
    metadata: dict[str, Any] = {}
    if sidecar.exists():
        try:
            loaded = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                metadata = loaded
        except Exception:
            metadata = {}
    stat = image_path.stat()
    relative = _relative_path(image_path)
    return {
        "id": str(metadata.get("id") or image_path.stem),
        "name": str(metadata.get("original_name") or image_path.name),
        "stored_name": image_path.name,
        "path": relative,
        "url": "/storage/" + relative.removeprefix("dashboard_storage/"),
        "size_bytes": int(metadata.get("size_bytes") or stat.st_size),
        "uploaded_at": str(metadata.get("uploaded_at") or now_iso()),
        "modified_at": stat.st_mtime,
    }


def api_reference_images() -> dict[str, Any]:
    REFERENCE_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for image_path in REFERENCE_LIBRARY_DIR.iterdir():
        if not image_path.is_file() or image_path.suffix.lower() not in _IMAGE_EXTENSIONS:
            continue
        try:
            items.append(_library_item(image_path))
        except OSError:
            continue
    items.sort(key=lambda item: float(item.get("modified_at") or 0), reverse=True)
    for item in items:
        item.pop("modified_at", None)
    return {"items": items, "count": len(items)}


async def api_upload_reference_images(files: list[UploadFile]) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="Choose at least one reference image")
    if len(files) > 250:
        raise HTTPException(status_code=400, detail="A maximum of 250 reference images can be uploaded at once")

    REFERENCE_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for index, upload in enumerate(files, start=1):
        original_name = upload.filename or f"reference_{index:03d}.png"
        suffix = Path(original_name).suffix.lower()
        if suffix not in _IMAGE_EXTENSIONS:
            skipped.append({"name": original_name, "reason": "unsupported image type"})
            continue
        data = await upload.read()
        if not data:
            skipped.append({"name": original_name, "reason": "empty file"})
            continue
        if len(data) > 30 * 1024 * 1024:
            skipped.append({"name": original_name, "reason": "larger than 30 MB"})
            continue

        item_id = f"ref_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        safe_name = _safe_filename(original_name, f"reference_{index:03d}{suffix}")
        destination = REFERENCE_LIBRARY_DIR / f"{item_id}_{safe_name}"
        destination.write_bytes(data)
        metadata = {
            "id": item_id,
            "original_name": original_name,
            "stored_name": destination.name,
            "path": _relative_path(destination),
            "size_bytes": len(data),
            "uploaded_at": now_iso(),
        }
        _sidecar_path(destination).write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        saved.append(_library_item(destination))

    if not saved and skipped:
        raise HTTPException(status_code=400, detail="No supported reference images were uploaded")
    return {"items": saved, "saved": len(saved), "skipped": skipped}


def _resolve_library_path(raw_path: str) -> Path:
    normalized = str(raw_path or "").strip().replace("\\", "/")
    if not normalized:
        raise HTTPException(status_code=400, detail="Reference image path is required")
    candidate = (ROOT / normalized).resolve()
    library_root = REFERENCE_LIBRARY_DIR.resolve()
    if candidate != library_root and library_root not in candidate.parents:
        raise HTTPException(status_code=400, detail="Reference image is outside the persistent library")
    if not candidate.exists() or not candidate.is_file() or candidate.suffix.lower() not in _IMAGE_EXTENSIONS:
        raise HTTPException(status_code=404, detail="Reference image not found")
    return candidate


def api_delete_reference_image(payload: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_library_path(str(payload.get("path") or ""))
    relative = _relative_path(path)
    sidecar = _sidecar_path(path)
    path.unlink()
    if sidecar.exists():
        sidecar.unlink()
    return {"status": "deleted", "path": relative}


def _copy_persistent_references(run_dir: Path, paths: list[Any]) -> list[dict[str, str]]:
    destination_dir = run_dir / "inputs" / "reference_images"
    destination_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(paths, start=1):
        source = _resolve_library_path(str(raw or ""))
        source_key = str(source)
        if source_key in seen:
            continue
        seen.add(source_key)
        item = _library_item(source)
        suffix = source.suffix.lower()
        filename = f"{len(saved) + 1:03d}_{_safe_filename(item['name'], f'reference_{index:03d}{suffix}')}"
        destination = destination_dir / filename
        shutil.copy2(source, destination)
        saved.append(
            {
                "index": str(len(saved) + 1),
                "original_name": str(item["name"]),
                "library_path": str(item["path"]),
                "path": _relative_path(destination),
                "absolute_path": str(destination.resolve()),
            }
        )
    return saved


async def _save_direct_references(
    run_dir: Path,
    uploads: list[UploadFile],
    start_index: int,
) -> list[dict[str, str]]:
    destination_dir = run_dir / "inputs" / "reference_images"
    destination_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, str]] = []
    for offset, upload in enumerate(uploads, start=1):
        original_name = upload.filename or f"reference_{start_index + offset:03d}.png"
        suffix = Path(original_name).suffix.lower()
        if suffix not in _IMAGE_EXTENSIONS:
            continue
        data = await upload.read()
        if not data:
            continue
        if len(data) > 30 * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"Reference image is larger than 30 MB: {original_name}")
        index = start_index + len(saved) + 1
        filename = f"{index:03d}_{_safe_filename(original_name, f'reference_{index:03d}{suffix}')}"
        destination = destination_dir / filename
        destination.write_bytes(data)
        saved.append(
            {
                "index": str(index),
                "original_name": original_name,
                "path": _relative_path(destination),
                "absolute_path": str(destination.resolve()),
            }
        )
    return saved


def _reserve_reference_batch_name() -> str:
    """Use a separate ref_vN namespace so reference outputs never mix with structured vN batches."""
    with _REFERENCE_BATCH_LOCK:
        output_root = ROOT / "output"
        output_root.mkdir(parents=True, exist_ok=True)
        GENERATED_IMAGES_ROOT.mkdir(parents=True, exist_ok=True)
        existing: list[int] = []
        for root in (output_root, GENERATED_IMAGES_ROOT):
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                match = re.fullmatch(r"ref_v(\d+)", child.name, flags=re.IGNORECASE)
                if match:
                    existing.append(int(match.group(1)))
        number = max(existing, default=0) + 1
        while True:
            batch = f"ref_v{number}"
            try:
                (output_root / batch).mkdir(parents=True, exist_ok=False)
                (GENERATED_IMAGES_ROOT / batch).mkdir(parents=True, exist_ok=True)
                return batch
            except FileExistsError:
                number += 1


async def api_run_execute_reference_persistent(
    *,
    config: str,
    reference_image_files: list[UploadFile] | None,
    product_info_file: UploadFile | None,
    input_image_files: list[UploadFile] | None,
    clear_input_images: bool,
) -> dict[str, Any]:
    ensure_dirs()
    try:
        cfg = json.loads(config)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid config JSON") from exc

    selected_raw = cfg.get("selected_personas")
    if not isinstance(selected_raw, list) or not selected_raw:
        raise HTTPException(status_code=400, detail="Select at least one persona")

    # Persona seeds are loaded from persona_seeds.json for every new run.
    persona_map = _reference_flow._load_persona_map()
    selected_numbers: list[int] = []
    for raw in selected_raw:
        try:
            number = int(raw)
        except (TypeError, ValueError):
            continue
        if number in persona_map and number not in selected_numbers:
            selected_numbers.append(number)
    if not selected_numbers:
        raise HTTPException(status_code=400, detail="No valid personas were selected")

    engine = str(cfg.get("engine") or "gemini").strip().lower()
    if engine not in {"gemini", "chatgpt"}:
        raise HTTPException(status_code=400, detail="engine must be gemini or chatgpt")
    headless = bool(cfg.get("headless", False))
    generate_916 = bool(cfg.get("generate_916", True))
    stored_paths = cfg.get("reference_image_paths")
    if not isinstance(stored_paths, list):
        stored_paths = []
    direct_uploads = reference_image_files or []
    if len(stored_paths) + len(direct_uploads) > 250:
        raise HTTPException(status_code=400, detail="A maximum of 250 reference images is supported per run")

    run_id = make_run_id()
    run_dir = RUNS_ROOT / run_id
    for folder in ("inputs", "logs", "context"):
        (run_dir / folder).mkdir(parents=True, exist_ok=True)

    references = _copy_persistent_references(run_dir, stored_paths)
    references.extend(await _save_direct_references(run_dir, direct_uploads, len(references)))
    if not references:
        raise HTTPException(status_code=400, detail="Select at least one stored reference image")

    product_doc_path = await _reference_flow._save_product_doc(run_dir, product_info_file)
    if not product_doc_path.exists():
        raise HTTPException(status_code=400, detail="Product document is missing")
    uploaded_product_images = store_uploaded_input_images(input_image_files or [], clear_input_images)

    batch = _reserve_reference_batch_name()
    personas = [persona_map[number] for number in selected_numbers]
    request_snapshot = {
        "run_id": run_id,
        "batch": batch,
        "flow_type": "reference_image",
        "engine": engine,
        "headless": headless,
        "generate_916": generate_916,
        "selected_personas": selected_numbers,
        "reference_images": [{k: v for k, v in item.items() if k != "absolute_path"} for item in references],
        "product_doc": str(product_doc_path),
        "persona_seed_source": "persona_seeds.json",
        "created_at": now_iso(),
    }
    (run_dir / "context" / "reference_flow.json").write_text(
        json.dumps(request_snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _reference_flow._write_status(
        run_dir,
        status="queued",
        phase="queued",
        run_id=run_id,
        batch=batch,
        engine=engine,
        completed_jobs=0,
        total_jobs=len(personas) * len(references),
        message="Reference-image run queued",
    )

    threading.Thread(
        target=_reference_flow._run_reference_generation,
        kwargs={
            "run_dir": run_dir,
            "run_id": run_id,
            "batch": batch,
            "engine": engine,
            "headless": headless,
            "generate_916": generate_916,
            "personas": personas,
            "references": references,
            "product_doc_path": product_doc_path,
            "uploaded_product_images": uploaded_product_images,
        },
        daemon=True,
    ).start()
    return {
        "run_id": run_id,
        "batch": batch,
        "status": "started",
        "total_jobs": len(personas) * len(references),
    }
