from __future__ import annotations

import io
import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import Body, HTTPException, UploadFile

from dashboard.backend import reference_flow as flow
from dashboard.backend.pipeline.clock import ensure_dirs, make_run_id, now_iso
from dashboard.backend.pipeline.copy_text import persona_slug
from dashboard.backend.pipeline.images import scan_image_files_for_batch, scan_prompt_files_for_batch
from dashboard.backend.pipeline.paths import (
    DEFAULT_PRODUCT_MASTER,
    GENERATED_IMAGES_ROOT,
    PERSONA_SEEDS_PATH,
    ROOT,
    RUNS_ROOT,
    STORAGE_ROOT,
)
from dashboard.backend.pipeline.run_control import cancel_event_for_run
from dashboard.backend.reference_library import (
    _copy_persistent_references,
    _reserve_reference_batch_name,
    _save_direct_references,
)

WORKSPACE_ROOT = STORAGE_ROOT / "reference_workspace"
PRODUCT_IMAGES_DIR = WORKSPACE_ROOT / "product_images"
PRODUCT_DOC_PATH = WORKSPACE_ROOT / "product_document.txt"
STARTING_PROMPT_PATH = WORKSPACE_ROOT / "starting_prompt.txt"
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _ensure_workspace() -> None:
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    PRODUCT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    if not PRODUCT_DOC_PATH.exists() and DEFAULT_PRODUCT_MASTER.exists():
        shutil.copy2(DEFAULT_PRODUCT_MASTER, PRODUCT_DOC_PATH)
    if not STARTING_PROMPT_PATH.exists():
        STARTING_PROMPT_PATH.write_text(
            "Use the uploaded reference image as the visual direction. Preserve the uploaded product packshots exactly and create one finished advertisement only.",
            encoding="utf-8",
        )


def _safe_name(value: str, fallback: str) -> str:
    name = Path(value or "").name.strip()
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or fallback


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def _product_image_item(path: Path) -> dict[str, Any]:
    stat = path.stat()
    rel = _rel(path)
    return {
        "name": path.name,
        "path": rel,
        "url": "/storage/" + rel.removeprefix("dashboard_storage/"),
        "size_bytes": stat.st_size,
        "modified_at": stat.st_mtime,
    }


def _product_images() -> list[dict[str, Any]]:
    _ensure_workspace()
    items = [
        _product_image_item(path)
        for path in PRODUCT_IMAGES_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in _IMAGE_EXTENSIONS
    ]
    items.sort(key=lambda item: float(item["modified_at"]), reverse=True)
    for item in items:
        item.pop("modified_at", None)
    return items


def api_reference_workspace() -> dict[str, Any]:
    _ensure_workspace()
    doc = PRODUCT_DOC_PATH.read_text(encoding="utf-8", errors="ignore") if PRODUCT_DOC_PATH.exists() else ""
    starter = STARTING_PROMPT_PATH.read_text(encoding="utf-8", errors="ignore") if STARTING_PROMPT_PATH.exists() else ""
    persona_text = PERSONA_SEEDS_PATH.read_text(encoding="utf-8", errors="ignore") if PERSONA_SEEDS_PATH.exists() else "[]"
    return {
        "product_document": {
            "path": _rel(PRODUCT_DOC_PATH),
            "name": PRODUCT_DOC_PATH.name,
            "content": doc,
            "size_bytes": PRODUCT_DOC_PATH.stat().st_size if PRODUCT_DOC_PATH.exists() else 0,
        },
        "product_images": _product_images(),
        "starting_prompt": {"path": _rel(STARTING_PROMPT_PATH), "content": starter},
        "persona_seed": {"path": _rel(PERSONA_SEEDS_PATH), "content": persona_text},
    }


async def api_upload_reference_product_images(files: list[UploadFile], replace: bool = False) -> dict[str, Any]:
    _ensure_workspace()
    if replace:
        for existing in PRODUCT_IMAGES_DIR.iterdir():
            if existing.is_file():
                existing.unlink(missing_ok=True)
    saved: list[dict[str, Any]] = []
    for index, upload in enumerate(files or [], start=1):
        original = upload.filename or f"product_{index:03d}.png"
        suffix = Path(original).suffix.lower()
        if suffix not in _IMAGE_EXTENSIONS:
            continue
        data = await upload.read()
        if not data:
            continue
        if len(data) > 30 * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"Product image exceeds 30 MB: {original}")
        base = _safe_name(original, f"product_{index:03d}{suffix}")
        destination = PRODUCT_IMAGES_DIR / base
        if destination.exists():
            destination = PRODUCT_IMAGES_DIR / f"{Path(base).stem}_{uuid.uuid4().hex[:6]}{suffix}"
        destination.write_bytes(data)
        saved.append(_product_image_item(destination))
    return {"saved": saved, "product_images": _product_images()}


def api_delete_reference_product_image(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    _ensure_workspace()
    raw = str(payload.get("path") or "").strip().replace("\\", "/")
    candidate = (ROOT / raw).resolve()
    root = PRODUCT_IMAGES_DIR.resolve()
    if root not in candidate.parents or not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Product image not found")
    candidate.unlink()
    return {"status": "deleted", "path": raw, "product_images": _product_images()}


async def api_upload_reference_product_doc(file: UploadFile) -> dict[str, Any]:
    _ensure_workspace()
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Product document is empty")
    PRODUCT_DOC_PATH.write_bytes(data)
    return api_reference_workspace()["product_document"]


def api_save_reference_starting_prompt(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    _ensure_workspace()
    content = str(payload.get("content") or "").strip()
    STARTING_PROMPT_PATH.write_text(content + ("\n" if content else ""), encoding="utf-8")
    return {"status": "saved", "path": _rel(STARTING_PROMPT_PATH), "content": content}


def _snapshot_workspace(run_dir: Path) -> tuple[Path, list[Path], str]:
    _ensure_workspace()
    asset_root = run_dir / "inputs" / "reference_workspace"
    product_dir = asset_root / "product_images"
    product_dir.mkdir(parents=True, exist_ok=True)
    doc_target = asset_root / "product_document.txt"
    shutil.copy2(PRODUCT_DOC_PATH, doc_target)
    product_paths: list[Path] = []
    for source in PRODUCT_IMAGES_DIR.iterdir():
        if source.is_file() and source.suffix.lower() in _IMAGE_EXTENSIONS:
            target = product_dir / source.name
            shutil.copy2(source, target)
            product_paths.append(target.resolve())
    starter = STARTING_PROMPT_PATH.read_text(encoding="utf-8", errors="ignore").strip()
    return doc_target, product_paths, starter


def _write_reference_prompt(
    *,
    batch: str,
    persona: dict[str, Any],
    reference: dict[str, str],
    product_doc_text: str,
    reference_index: int,
    starting_prompt: str,
) -> Path:
    number = int(persona["persona_number"])
    slug = persona_slug(number)
    prompt_dir = ROOT / "output" / batch / "45" / slug
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = prompt_dir / f"REF_{slug}_EN_reference_A{reference_index:03d}.txt"
    comment = str(reference.get("comment") or "").strip()
    parts = []
    if starting_prompt:
        parts.append("REFERENCE FLOW STARTING PROMPT:\n" + starting_prompt)
    parts.append(flow.build_reference_prompt(persona, product_doc_text).strip())
    if comment:
        parts.append("INSTRUCTION FOR THIS REFERENCE IMAGE ONLY:\n" + comment)
    prompt_path.write_text("\n\n".join(parts).strip() + "\n", encoding="utf-8")
    sidecar = {
        "flow_type": "reference_image",
        "format": "REF",
        "language": "EN",
        "aspect_ratio": "4:5",
        "persona": slug,
        "persona_number": number,
        "persona_name": persona.get("persona_name", slug),
        "creative_index": reference_index,
        "concept_angle": "reference",
        "reference_image": reference.get("path", ""),
        "reference_image_name": reference.get("original_name", ""),
        "reference_comment": comment,
        "prompt_file_relative": _rel(prompt_path),
    }
    prompt_path.with_suffix(".json").write_text(json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return prompt_path


def _run_reference_worker(
    *,
    run_dir: Path,
    run_id: str,
    batch: str,
    engine: str,
    headless: bool,
    generate_916: bool,
    personas: list[dict[str, Any]],
    references: list[dict[str, str]],
    product_doc_path: Path,
    product_images: list[Path],
    starting_prompt: str,
) -> None:
    failures: list[dict[str, Any]] = []
    completed = 0
    total = len(personas) * len(references)
    cancel_event = cancel_event_for_run(run_id)
    cancel_event.clear()
    try:
        product_doc_text = product_doc_path.read_text(encoding="utf-8", errors="ignore")
        if not product_doc_text.strip():
            raise RuntimeError("Reference product document is empty")
        if not product_images:
            raise RuntimeError("No reference-flow product images are stored")
        flow._write_status(run_dir, status="running", phase="4:5 generation", completed_jobs=0, total_jobs=total, message="Preparing reference jobs")
        partial_image_files: list[str] = []
        for persona_index, persona in enumerate(personas, start=1):
            for reference_index, reference in enumerate(references, start=1):
                if cancel_event.is_set():
                    raise InterruptedError("Cancelled by user")
                job_index = (persona_index - 1) * len(references) + reference_index
                prompt_path = _write_reference_prompt(
                    batch=batch,
                    persona=persona,
                    reference=reference,
                    product_doc_text=product_doc_text,
                    reference_index=reference_index,
                    starting_prompt=starting_prompt,
                )
                source_file = run_dir / "context" / "reference_sources" / f"{prompt_path.stem}.images.txt"
                source_file.parent.mkdir(parents=True, exist_ok=True)
                all_sources = [reference["absolute_path"], *[str(path) for path in product_images]]
                source_file.write_text("\n".join(all_sources) + "\n", encoding="utf-8")
                flow._write_status(
                    run_dir,
                    status="running",
                    phase="4:5 generation",
                    completed_jobs=job_index - 1,
                    total_jobs=total,
                    current_persona=int(persona["persona_number"]),
                    current_persona_name=persona.get("persona_name", ""),
                    current_reference=reference.get("original_name", ""),
                    message=f"Generating 4:5 ad {job_index}/{total}",
                )
                result = flow._run_image_engine(
                    engine=engine,
                    batch=batch,
                    prompt_path=prompt_path,
                    source_file=source_file,
                    aspect_ratio="4:5",
                    headless=headless,
                    run_dir=run_dir,
                )
                if result.returncode == 0:
                    grouped = flow._group_generated_output(
                        batch=batch,
                        aspect_dir="4_5",
                        prompt_path=prompt_path,
                        persona=persona,
                        reference=reference,
                    )
                    if grouped:
                        completed += 1
                        partial_image_files.append(grouped)
                    else:
                        failures.append({"persona_number": int(persona["persona_number"]), "reference_image": reference.get("original_name", ""), "error": "Output image could not be located"})
                else:
                    failures.append({"persona_number": int(persona["persona_number"]), "reference_image": reference.get("original_name", ""), "error": (result.stderr or result.stdout or "Generation failed")[-1200:]})
                flow._write_status(run_dir, status="running", phase="4:5 generation", completed_jobs=job_index, total_jobs=total, completed_45=completed, failures=len(failures), partial_image_files=list(partial_image_files), message=f"Completed {job_index}/{total} reference jobs")
        if completed == 0:
            raise RuntimeError("No 4:5 reference job succeeded")
        conversion: dict[str, Any] | None = None
        if generate_916 and not cancel_event.is_set():
            flow._write_status(run_dir, status="running", phase="9:16 conversion", completed_jobs=total, total_jobs=total, message=f"Converting {completed} images to 9:16")
            try:
                conversion = flow.run_916_conversion_from_45_for_batch(batch=batch, headless=headless, run_dir=run_dir, engine=engine)
                flow._group_all_916_outputs(batch, personas)
            except Exception as exc:
                failures.append({"phase": "9:16 conversion", "error": str(exc)})
                conversion = {"completed": 0, "attempted": completed, "failures": [str(exc)]}
        manifest = {
            "run_id": run_id,
            "batch": batch,
            "flow_type": "reference_image",
            "flow_label": "Reference Image Flow",
            "status": "completed",
            "updated_at": now_iso(),
            "image_generated": True,
            "generated_variant": "4:5+9:16" if conversion and conversion.get("completed") else "4:5",
            "prompt_files": scan_prompt_files_for_batch(batch),
            "image_files": scan_image_files_for_batch(batch),
            "regeneration_queue_files": [],
            "engine": engine,
            "selected_personas": [int(item["persona_number"]) for item in personas],
            "reference_images": [{k: v for k, v in item.items() if k != "absolute_path"} for item in references],
            "reference_job_count": total,
            "generation_failures": failures,
            "conversion_916": conversion or {},
            "product_doc": _rel(product_doc_path),
            "product_images": [_rel(path) for path in product_images],
            "starting_prompt": starting_prompt,
            "persona_groups": flow._persona_groups(batch, personas),
        }
        (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        flow._write_status(run_dir, status="completed", phase="completed", completed_jobs=total, total_jobs=total, completed_45=completed, failures=len(failures), manifest_ready=True, message=f"Reference run complete: {len(manifest['image_files'])} images")
    except InterruptedError as exc:
        flow._write_status(run_dir, status="cancelled", phase="cancelled", message=str(exc), failures=len(failures))
    except Exception as exc:
        (run_dir / "logs" / "reference_flow_error.txt").write_text(str(exc), encoding="utf-8")
        flow._write_status(run_dir, status="error", phase="error", error=str(exc), failures=len(failures), message=f"Reference run failed: {exc}")


async def api_run_execute_reference_workspace(
    *,
    config: str,
    reference_image_files: list[UploadFile] | None,
    product_info_file: UploadFile | None,
    input_image_files: list[UploadFile] | None,
    clear_input_images: bool,
) -> dict[str, Any]:
    ensure_dirs()
    _ensure_workspace()
    try:
        cfg = json.loads(config)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid config JSON") from exc
    if product_info_file is not None:
        await api_upload_reference_product_doc(product_info_file)
    if input_image_files:
        await api_upload_reference_product_images(input_image_files, replace=clear_input_images)
    persona_map = flow._load_persona_map()
    selected: list[int] = []
    for raw in cfg.get("selected_personas") or []:
        try:
            number = int(raw)
        except (TypeError, ValueError):
            continue
        if number in persona_map and number not in selected:
            selected.append(number)
    if not selected:
        raise HTTPException(status_code=400, detail="Select at least one valid persona")
    engine = str(cfg.get("engine") or "gemini").strip().lower()
    if engine not in {"gemini", "chatgpt"}:
        raise HTTPException(status_code=400, detail="engine must be gemini or chatgpt")
    run_id = make_run_id()
    run_dir = RUNS_ROOT / run_id
    for folder in ("inputs", "logs", "context"):
        (run_dir / folder).mkdir(parents=True, exist_ok=True)
    references = _copy_persistent_references(run_dir, cfg.get("reference_image_paths") or [])
    references.extend(await _save_direct_references(run_dir, reference_image_files or [], len(references)))
    if not references:
        raise HTTPException(status_code=400, detail="Select at least one reference image")
    comments = cfg.get("reference_comments") if isinstance(cfg.get("reference_comments"), dict) else {}
    for reference in references:
        library_path = str(reference.get("library_path") or "")
        reference["comment"] = str(comments.get(library_path) or comments.get(reference.get("path")) or "").strip()
    product_doc_path, product_images, starting_prompt = _snapshot_workspace(run_dir)
    if not product_images:
        raise HTTPException(status_code=400, detail="Upload at least one product image in Reference Image Flow")
    batch = _reserve_reference_batch_name()
    personas = [persona_map[number] for number in selected]
    snapshot = {
        "run_id": run_id,
        "batch": batch,
        "flow_type": "reference_image",
        "engine": engine,
        "selected_personas": selected,
        "reference_images": [{k: v for k, v in item.items() if k != "absolute_path"} for item in references],
        "product_doc": _rel(product_doc_path),
        "product_images": [_rel(path) for path in product_images],
        "starting_prompt": starting_prompt,
        "created_at": now_iso(),
    }
    (run_dir / "context" / "reference_flow.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    total = len(personas) * len(references)
    flow._write_status(run_dir, status="queued", phase="queued", run_id=run_id, batch=batch, engine=engine, completed_jobs=0, total_jobs=total, message="Reference run queued")
    threading.Thread(
        target=_run_reference_worker,
        kwargs={
            "run_dir": run_dir,
            "run_id": run_id,
            "batch": batch,
            "engine": engine,
            "headless": bool(cfg.get("headless", False)),
            "generate_916": bool(cfg.get("generate_916", True)),
            "personas": personas,
            "references": references,
            "product_doc_path": product_doc_path,
            "product_images": product_images,
            "starting_prompt": starting_prompt,
        },
        daemon=True,
    ).start()
    return {"run_id": run_id, "batch": batch, "status": "started", "total_jobs": total}
