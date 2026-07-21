from __future__ import annotations

import json
import re
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from dashboard.backend.app import (
    DEFAULT_PRODUCT_MASTER,
    GENERATED_IMAGES_ROOT,
    INPUT_IMAGES_DIR,
    PERSONA_SEEDS_PATH,
    ROOT,
    RUNS_ROOT,
    cancel_event_for_run,
    ensure_dirs,
    enrich_manifest_for_dashboard,
    load_manifest_for_run,
    make_run_id,
    now_iso,
    persona_slug,
    run_916_conversion_from_45_for_batch,
    run_chatgpt_generation,
    run_gemini_generation,
    scan_image_files_for_batch,
    scan_prompt_files_for_batch,
    store_uploaded_input_images,
)

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_BATCH_LOCK = threading.Lock()
_STATUS_LOCK = threading.Lock()


def _safe_filename(value: str, fallback: str) -> str:
    name = Path(value or "").name.strip()
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or fallback


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")
    return slug or "persona"


def _load_persona_map() -> dict[int, dict[str, Any]]:
    try:
        payload = json.loads(PERSONA_SEEDS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read persona seeds: {exc}") from exc
    result: dict[int, dict[str, Any]] = {}
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("persona_number"))
        except (TypeError, ValueError):
            continue
        result[number] = item
    return result


def _reserve_batch_name() -> str:
    """Reserve a vN folder immediately so structured and reference runs cannot collide."""
    with _BATCH_LOCK:
        output_root = ROOT / "output"
        output_root.mkdir(parents=True, exist_ok=True)
        existing: list[int] = []
        for root in (output_root, GENERATED_IMAGES_ROOT):
            if not root.exists():
                continue
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                match = re.fullmatch(r"v(\d+)", child.name, flags=re.IGNORECASE)
                if match:
                    existing.append(int(match.group(1)))
        number = max(existing, default=0) + 1
        while True:
            batch = f"v{number}"
            batch_dir = output_root / batch
            try:
                batch_dir.mkdir(parents=True, exist_ok=False)
                (GENERATED_IMAGES_ROOT / batch).mkdir(parents=True, exist_ok=True)
                return batch
            except FileExistsError:
                number += 1


def _status_path(run_dir: Path) -> Path:
    return run_dir / "context" / "reference_status.json"


def _write_status(run_dir: Path, **updates: Any) -> dict[str, Any]:
    path = _status_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _STATUS_LOCK:
        current: dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    current = loaded
            except Exception:
                current = {}
        current.update(updates)
        current["updated_at"] = now_iso()
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)
        return current


def api_reference_run_status(run_id: str) -> dict[str, Any]:
    run_dir = RUNS_ROOT / run_id
    path = _status_path(run_dir)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Reference run status not found")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read reference run status: {exc}") from exc
    return payload if isinstance(payload, dict) else {"status": "unknown"}


async def _save_reference_uploads(run_dir: Path, uploads: list[UploadFile]) -> list[dict[str, str]]:
    reference_dir = run_dir / "inputs" / "reference_images"
    reference_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, str]] = []
    for index, upload in enumerate(uploads, start=1):
        original = upload.filename or f"reference_{index:03d}.png"
        suffix = Path(original).suffix.lower()
        if suffix not in _IMAGE_EXTENSIONS:
            continue
        data = await upload.read()
        if not data:
            continue
        if len(data) > 30 * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"Reference image is larger than 30 MB: {original}")
        filename = f"{index:03d}_{_safe_filename(original, f'reference_{index:03d}{suffix}') }"
        destination = reference_dir / filename
        destination.write_bytes(data)
        saved.append(
            {
                "index": str(index),
                "original_name": original,
                "path": str(destination.relative_to(ROOT)).replace("\\", "/"),
                "absolute_path": str(destination.resolve()),
            }
        )
    return saved


async def _save_product_doc(run_dir: Path, product_info_file: UploadFile | None) -> Path:
    if product_info_file is None:
        return DEFAULT_PRODUCT_MASTER
    data = await product_info_file.read()
    if not data:
        return DEFAULT_PRODUCT_MASTER
    destination = run_dir / "inputs" / "product master doc.txt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return destination


def _compact_persona(persona: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "persona_number",
        "persona_name",
        "core_pattern",
        "primary_tags",
        "common_indian_moments",
        "failed_attempts",
        "why_it_failed",
        "relevant_ok_kit_role",
        "guardrail",
        "headline_anchor_rule",
    )
    return {key: persona.get(key) for key in keep if persona.get(key) not in (None, "", [])}


def build_reference_prompt(persona: dict[str, Any], product_doc: str) -> str:
    """Build the deliberately simple prompt requested for the reference-image flow."""
    return (
        "I have uploaded a reference image. Create an ad for my product in the style of the reference image. "
        "I have uploaded the reference image, my product images, and my product document.\n\n"
        "Target persona:\n"
        f"{json.dumps(_compact_persona(persona), ensure_ascii=False, indent=2)}\n\n"
        "Create the first ad as an exact 4:5 portrait image. Keep every critical element—including headline, "
        "logo, product pack, CTA, offer, and faces—inside a protected safe zone with at least 8% clear margin "
        "from every edge, so the ad remains crop-safe. Use the reference image as the primary source for style, "
        "layout, composition, visual hierarchy, typography direction, and overall art direction. Think through "
        "the creative yourself; do not ask questions and do not add explanations—generate the image.\n\n"
        "Product document (source of truth):\n"
        f"{product_doc.strip()}\n"
    )


def _write_prompt(
    *,
    batch: str,
    persona: dict[str, Any],
    reference: dict[str, str],
    product_doc_text: str,
    reference_index: int,
) -> Path:
    number = int(persona["persona_number"])
    slug = persona_slug(number)
    prompt_dir = ROOT / "output" / batch / "45" / slug
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_name = f"REF_{slug}_EN_reference_A{reference_index:03d}.txt"
    prompt_path = prompt_dir / prompt_name
    prompt_path.write_text(build_reference_prompt(persona, product_doc_text), encoding="utf-8")
    sidecar = {
        "flow_type": "reference_image",
        "format": "REF",
        "language": "EN",
        "aspect_ratio": "4:5",
        "persona": slug,
        "persona_number": number,
        "persona_name": persona.get("persona_name", slug),
        "creative_index": reference_index,
        "creative_total": 0,
        "concept_angle": "reference",
        "reference_image": reference.get("path", ""),
        "reference_image_name": reference.get("original_name", ""),
        "prompt_file_relative": str(prompt_path.relative_to(ROOT)).replace("\\", "/"),
    }
    prompt_path.with_suffix(".json").write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return prompt_path


def _run_image_engine(
    *,
    engine: str,
    batch: str,
    prompt_path: Path,
    source_file: Path,
    aspect_ratio: str,
    headless: bool,
    run_dir: Path,
) -> Any:
    kwargs = {
        "batch": batch,
        "prompt_files": [str(prompt_path)],
        "aspect_ratio": aspect_ratio,
        "image_sources_file": str(source_file),
        "headless": headless,
        "run_dir": run_dir,
        "prepend_starting_prompt": False,
        "first_tab_mode": "new",
    }
    if engine == "chatgpt":
        return run_chatgpt_generation(**kwargs)
    return run_gemini_generation(**kwargs)


def _image_candidates(batch: str, aspect_dir: str, prompt_stem: str) -> list[Path]:
    root = GENERATED_IMAGES_ROOT / batch / aspect_dir
    candidates: list[Path] = []
    if not root.exists():
        return candidates
    expected_stem = f"{prompt_stem}_{aspect_dir}"
    for ext in _IMAGE_EXTENSIONS:
        candidates.extend(path for path in root.glob(f"**/*{ext}") if path.stem == expected_stem)
    return sorted(candidates, key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)


def _move_metadata(source: Path, destination: Path, extra: dict[str, Any]) -> None:
    metadata_candidates = [source.with_suffix(".json"), source.with_suffix(source.suffix + ".json")]
    for metadata_source in metadata_candidates:
        if not metadata_source.exists():
            continue
        metadata_destination = destination.with_suffix(".json")
        metadata_destination.parent.mkdir(parents=True, exist_ok=True)
        if metadata_destination.exists():
            metadata_destination.unlink()
        shutil.move(str(metadata_source), str(metadata_destination))
        try:
            payload = json.loads(metadata_destination.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload.update(extra)
        payload["saved_file"] = str(destination.relative_to(ROOT)).replace("\\", "/")
        metadata_destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return


def _group_generated_output(
    *,
    batch: str,
    aspect_dir: str,
    prompt_path: Path,
    persona: dict[str, Any],
    reference: dict[str, str],
) -> str:
    candidates = _image_candidates(batch, aspect_dir, prompt_path.stem)
    if not candidates:
        return ""
    source = candidates[0]
    slug = persona_slug(int(persona["persona_number"]))
    destination = GENERATED_IMAGES_ROOT / batch / aspect_dir / slug / "generated images" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != destination.resolve():
        if destination.exists():
            destination.unlink()
        shutil.move(str(source), str(destination))
        _move_metadata(
            source,
            destination,
            {
                "flow_type": "reference_image",
                "persona": slug,
                "persona_number": int(persona["persona_number"]),
                "persona_name": persona.get("persona_name", slug),
                "reference_image": reference.get("path", ""),
                "reference_image_name": reference.get("original_name", ""),
            },
        )
    else:
        metadata_path = destination.with_suffix(".json")
        if metadata_path.exists():
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                payload.update(
                    {
                        "flow_type": "reference_image",
                        "persona": slug,
                        "persona_number": int(persona["persona_number"]),
                        "persona_name": persona.get("persona_name", slug),
                        "reference_image": reference.get("path", ""),
                        "reference_image_name": reference.get("original_name", ""),
                    }
                )
                metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(destination.relative_to(ROOT)).replace("\\", "/")


def _group_all_916_outputs(batch: str, personas: list[dict[str, Any]]) -> None:
    root = GENERATED_IMAGES_ROOT / batch / "9_16"
    if not root.exists():
        return
    for persona in personas:
        slug = persona_slug(int(persona["persona_number"]))
        destination_dir = root / slug / "generated images"
        destination_dir.mkdir(parents=True, exist_ok=True)
        for ext in _IMAGE_EXTENSIONS:
            for source in list(root.glob(f"generated images/*{ext}")):
                if f"_{slug}_" not in source.stem.lower():
                    continue
                destination = destination_dir / source.name
                if destination.exists():
                    destination.unlink()
                shutil.move(str(source), str(destination))
                _move_metadata(
                    source,
                    destination,
                    {
                        "flow_type": "reference_image",
                        "persona": slug,
                        "persona_number": int(persona["persona_number"]),
                        "persona_name": persona.get("persona_name", slug),
                    },
                )


def _persona_groups(batch: str, personas: list[dict[str, Any]]) -> dict[str, Any]:
    prompts = scan_prompt_files_for_batch(batch)
    images = scan_image_files_for_batch(batch)
    groups: dict[str, Any] = {}
    for persona in personas:
        number = int(persona["persona_number"])
        slug = persona_slug(number)
        groups[slug] = {
            "persona_number": number,
            "persona_name": persona.get("persona_name", slug),
            "prompt_files": [path for path in prompts if f"/{slug}/" in path or f"_{slug}_" in Path(path).name.lower()],
            "image_files": [path for path in images if f"/{slug}/" in path or f"_{slug}_" in Path(path).name.lower()],
        }
    return groups


def _final_manifest(
    *,
    run_dir: Path,
    run_id: str,
    batch: str,
    engine: str,
    personas: list[dict[str, Any]],
    references: list[dict[str, str]],
    product_doc_path: Path,
    uploaded_product_images: list[Any],
    failures: list[dict[str, Any]],
    conversion: dict[str, Any] | None,
) -> dict[str, Any]:
    prompt_files = scan_prompt_files_for_batch(batch)
    image_files = scan_image_files_for_batch(batch)
    manifest = {
        "run_id": run_id,
        "batch": batch,
        "flow_type": "reference_image",
        "flow_label": "Reference Image Flow",
        "status": "completed",
        "updated_at": now_iso(),
        "image_generated": bool(image_files),
        "generated_variant": "4:5+9:16" if conversion and conversion.get("completed") else "4:5",
        "prompt_files": prompt_files,
        "image_files": image_files,
        "regeneration_queue_files": [],
        "engine": engine,
        "selected_personas": [int(item["persona_number"]) for item in personas],
        "selected_persona_names": [str(item.get("persona_name") or "") for item in personas],
        "reference_images": [{k: v for k, v in item.items() if k != "absolute_path"} for item in references],
        "reference_image_count": len(references),
        "reference_job_count": len(references) * len(personas),
        "generation_failures": failures,
        "conversion_916": conversion or {},
        "product_doc": str(product_doc_path.relative_to(ROOT)).replace("\\", "/") if product_doc_path.is_relative_to(ROOT) else str(product_doc_path),
        "input_images_dir": str(INPUT_IMAGES_DIR.relative_to(ROOT)).replace("\\", "/"),
        "input_images_uploaded": uploaded_product_images,
        "persona_groups": _persona_groups(batch, personas),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return enrich_manifest_for_dashboard(manifest)


def _run_reference_generation(
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
    uploaded_product_images: list[Any],
) -> None:
    failures: list[dict[str, Any]] = []
    completed_45 = 0
    total_jobs = len(personas) * len(references)
    cancel_event = cancel_event_for_run(run_id)
    cancel_event.clear()
    try:
        product_doc_text = product_doc_path.read_text(encoding="utf-8", errors="ignore")
        if not product_doc_text.strip():
            raise RuntimeError("Product document is empty")

        _write_status(
            run_dir,
            status="running",
            phase="4:5 generation",
            run_id=run_id,
            batch=batch,
            engine=engine,
            completed_jobs=0,
            total_jobs=total_jobs,
            failures=0,
            message="Preparing reference-image jobs",
        )

        for persona_position, persona in enumerate(personas, start=1):
            if cancel_event.is_set():
                raise InterruptedError("Cancelled by user")
            for reference_position, reference in enumerate(references, start=1):
                if cancel_event.is_set():
                    raise InterruptedError("Cancelled by user")
                global_index = (persona_position - 1) * len(references) + reference_position
                prompt_path = _write_prompt(
                    batch=batch,
                    persona=persona,
                    reference=reference,
                    product_doc_text=product_doc_text,
                    reference_index=reference_position,
                )
                sidecar_path = prompt_path.with_suffix(".json")
                try:
                    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
                    sidecar["creative_total"] = len(references)
                    sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                except Exception:
                    pass

                source_file = run_dir / "context" / "reference_sources" / f"{prompt_path.stem}.images.txt"
                source_file.parent.mkdir(parents=True, exist_ok=True)
                source_file.write_text(reference["absolute_path"] + "\n", encoding="utf-8")

                _write_status(
                    run_dir,
                    status="running",
                    phase="4:5 generation",
                    completed_jobs=global_index - 1,
                    current_persona=int(persona["persona_number"]),
                    current_persona_name=persona.get("persona_name", ""),
                    current_reference=reference.get("original_name", ""),
                    message=f"Generating 4:5 ad {global_index}/{total_jobs}",
                )
                result = _run_image_engine(
                    engine=engine,
                    batch=batch,
                    prompt_path=prompt_path,
                    source_file=source_file,
                    aspect_ratio="4:5",
                    headless=headless,
                    run_dir=run_dir,
                )
                if result.returncode != 0:
                    failures.append(
                        {
                            "persona_number": int(persona["persona_number"]),
                            "reference_image": reference.get("original_name", ""),
                            "prompt_file": str(prompt_path.relative_to(ROOT)).replace("\\", "/"),
                            "error": (result.stderr or result.stdout or "Generation failed")[-1200:],
                        }
                    )
                else:
                    grouped = _group_generated_output(
                        batch=batch,
                        aspect_dir="4_5",
                        prompt_path=prompt_path,
                        persona=persona,
                        reference=reference,
                    )
                    if grouped:
                        completed_45 += 1
                    else:
                        failures.append(
                            {
                                "persona_number": int(persona["persona_number"]),
                                "reference_image": reference.get("original_name", ""),
                                "prompt_file": str(prompt_path.relative_to(ROOT)).replace("\\", "/"),
                                "error": "Generation process succeeded but the output image could not be located",
                            }
                        )

                _write_status(
                    run_dir,
                    status="running",
                    phase="4:5 generation",
                    completed_jobs=global_index,
                    completed_45=completed_45,
                    failures=len(failures),
                    message=f"Completed {global_index}/{total_jobs} reference jobs",
                )

        if completed_45 == 0:
            raise RuntimeError("No 4:5 reference-image generation succeeded")

        summary_path = GENERATED_IMAGES_ROOT / batch / "batch_run_summary.json"
        if summary_path.exists():
            summary_path.unlink()

        conversion: dict[str, Any] | None = None
        if generate_916 and not cancel_event.is_set():
            _write_status(
                run_dir,
                status="running",
                phase="9:16 conversion",
                completed_jobs=total_jobs,
                message=f"Converting {completed_45} generated 4:5 ads to 9:16",
            )
            try:
                conversion = run_916_conversion_from_45_for_batch(
                    batch=batch,
                    headless=headless,
                    run_dir=run_dir,
                    engine=engine,
                )
                _group_all_916_outputs(batch, personas)
                if summary_path.exists():
                    summary_path.unlink()
            except Exception as exc:
                failures.append({"phase": "9:16 conversion", "error": str(exc)})
                conversion = {"completed": 0, "attempted": completed_45, "failures": [str(exc)]}

        manifest = _final_manifest(
            run_dir=run_dir,
            run_id=run_id,
            batch=batch,
            engine=engine,
            personas=personas,
            references=references,
            product_doc_path=product_doc_path,
            uploaded_product_images=uploaded_product_images,
            failures=failures,
            conversion=conversion,
        )
        _write_status(
            run_dir,
            status="completed",
            phase="completed",
            completed_jobs=total_jobs,
            completed_45=completed_45,
            completed_916=int((conversion or {}).get("completed") or 0),
            failures=len(failures),
            manifest_ready=True,
            message=f"Reference run complete: {len(manifest.get('image_files') or [])} images",
        )
    except InterruptedError as exc:
        _write_status(run_dir, status="cancelled", phase="cancelled", message=str(exc), failures=len(failures))
    except Exception as exc:
        (run_dir / "logs" / "reference_flow_error.txt").write_text(
            f"{exc}\n\n{traceback.format_exc()}", encoding="utf-8"
        )
        _write_status(
            run_dir,
            status="error",
            phase="error",
            error=str(exc),
            failures=len(failures),
            message=f"Reference run failed: {exc}",
        )


async def api_run_execute_reference(
    *,
    config: str,
    reference_image_files: list[UploadFile],
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
    persona_map = _load_persona_map()
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

    if not reference_image_files:
        raise HTTPException(status_code=400, detail="Upload at least one reference image")
    if len(reference_image_files) > 250:
        raise HTTPException(status_code=400, detail="A maximum of 250 reference images is supported per run")

    run_id = make_run_id()
    run_dir = RUNS_ROOT / run_id
    for folder in ("inputs", "logs", "context"):
        (run_dir / folder).mkdir(parents=True, exist_ok=True)

    references = await _save_reference_uploads(run_dir, reference_image_files)
    if not references:
        raise HTTPException(status_code=400, detail="No supported reference images were uploaded")
    product_doc_path = await _save_product_doc(run_dir, product_info_file)
    if not product_doc_path.exists():
        raise HTTPException(status_code=400, detail="Product document is missing")
    uploaded_product_images = store_uploaded_input_images(input_image_files or [], clear_input_images)

    batch = _reserve_batch_name()
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
        "created_at": now_iso(),
    }
    (run_dir / "context" / "reference_flow.json").write_text(
        json.dumps(request_snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_status(
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
        target=_run_reference_generation,
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


def _revision_status_path(run_dir: Path, revision_id: str) -> Path:
    return run_dir / "context" / "revisions" / f"{revision_id}.json"


def _write_revision_status(run_dir: Path, revision_id: str, **updates: Any) -> dict[str, Any]:
    path = _revision_status_path(run_dir, revision_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                current = loaded
        except Exception:
            current = {}
    current.update(updates)
    current["revision_id"] = revision_id
    current["updated_at"] = now_iso()
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return current


def api_image_revision_status(run_id: str, revision_id: str) -> dict[str, Any]:
    run_dir = RUNS_ROOT / run_id
    path = _revision_status_path(run_dir, revision_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Revision status not found")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {"status": "unknown"}


def _revision_prompt(comment: str, original_prompt: str, aspect_ratio: str) -> str:
    safe_zone = (
        "Keep all critical elements inside an 8% safe margin from every edge."
        if aspect_ratio == "4:5"
        else "Keep all critical elements inside a crop-safe central zone for a 9:16 mobile placement."
    )
    return (
        "Edit the uploaded current ad image. Apply the user's requested changes accurately. Preserve the product "
        "identity, pack appearance, claims compliance, and every part the user did not ask to change. Generate the "
        f"updated image in {aspect_ratio}. {safe_zone}\n\n"
        "USER REVISION COMMENT:\n"
        f"{comment.strip()}\n\n"
        "ORIGINAL GENERATION INSTRUCTIONS:\n"
        f"{original_prompt.strip()}\n\n"
        "Return only the revised image. Do not explain the changes.\n"
    )


def _find_image_item(manifest: dict[str, Any], image_file: str) -> dict[str, Any] | None:
    enriched = enrich_manifest_for_dashboard(manifest)
    for item in enriched.get("image_items") or []:
        if isinstance(item, dict) and str(item.get("path") or "") == image_file:
            return item
    return None


def _run_revision(
    *,
    run_id: str,
    revision_id: str,
    image_file: str,
    comment: str,
    engine: str,
    headless: bool,
) -> None:
    run_dir = RUNS_ROOT / run_id
    backup_dir = run_dir / "context" / "revision_history" / revision_id
    backup_dir.mkdir(parents=True, exist_ok=True)
    original_path: Path | None = None
    backup_image: Path | None = None
    backup_meta: Path | None = None
    try:
        _write_revision_status(run_dir, revision_id, status="running", phase="preparing", image_file=image_file, comment=comment, engine=engine)
        _run_dir, manifest, has_storage_manifest = load_manifest_for_run(run_id)
        if not has_storage_manifest:
            raise RuntimeError("Image comments require a dashboard run manifest")
        batch = str(manifest.get("batch") or "").strip()
        if not batch:
            raise RuntimeError("Run has no batch")
        image_item = _find_image_item(manifest, image_file)
        if not image_item:
            raise RuntimeError("Image is not an active image in this run")
        prompt_file = str(image_item.get("prompt_file") or "").strip()
        if not prompt_file:
            raise RuntimeError("The image is not mapped to a prompt, so it cannot be revised safely")
        prompt_path = ROOT / prompt_file
        if not prompt_path.exists():
            raise RuntimeError(f"Original prompt not found: {prompt_file}")
        original_prompt = prompt_path.read_text(encoding="utf-8", errors="ignore")

        original_path = (ROOT / image_file).resolve()
        generated_root = GENERATED_IMAGES_ROOT.resolve()
        if generated_root not in original_path.parents or not original_path.exists():
            raise RuntimeError("Image path is invalid or missing")
        aspect_ratio = "9:16" if "/9_16/" in image_file.replace("\\", "/") else "4:5"
        aspect_dir = "9_16" if aspect_ratio == "9:16" else "4_5"

        backup_image = backup_dir / original_path.name
        shutil.copy2(original_path, backup_image)
        metadata_source = original_path.with_suffix(".json")
        if metadata_source.exists():
            backup_meta = backup_dir / metadata_source.name
            shutil.copy2(metadata_source, backup_meta)

        revision_prompt_path = backup_dir / prompt_path.name
        revision_prompt_path.write_text(_revision_prompt(comment, original_prompt, aspect_ratio), encoding="utf-8")
        source_file = backup_dir / "current_image.images.txt"
        source_file.write_text(str(backup_image.resolve()) + "\n", encoding="utf-8")

        original_path.unlink()
        if metadata_source.exists():
            metadata_source.unlink()

        _write_revision_status(run_dir, revision_id, status="running", phase="generating", message="Sending revision comment and current image to the selected engine")
        result = _run_image_engine(
            engine=engine,
            batch=batch,
            prompt_path=revision_prompt_path,
            source_file=source_file,
            aspect_ratio=aspect_ratio,
            headless=headless,
            run_dir=run_dir,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "Revision generation failed")[-1600:])

        candidates = _image_candidates(batch, aspect_dir, revision_prompt_path.stem)
        if not candidates:
            raise RuntimeError("Revision engine completed but the new image could not be located")
        generated = candidates[0]
        original_path.parent.mkdir(parents=True, exist_ok=True)
        if generated.resolve() != original_path.resolve():
            if original_path.exists():
                original_path.unlink()
            shutil.move(str(generated), str(original_path))
            _move_metadata(
                generated,
                original_path,
                {
                    "regenerated": True,
                    "regenerated_at": now_iso(),
                    "revision_id": revision_id,
                    "revision_comment": comment,
                    "revision_engine": engine,
                },
            )
        else:
            meta = original_path.with_suffix(".json")
            if meta.exists():
                try:
                    payload = json.loads(meta.read_text(encoding="utf-8"))
                except Exception:
                    payload = {}
                if isinstance(payload, dict):
                    payload.update(
                        {
                            "regenerated": True,
                            "regenerated_at": now_iso(),
                            "revision_id": revision_id,
                            "revision_comment": comment,
                            "revision_engine": engine,
                        }
                    )
                    meta.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        manifest["prompt_files"] = scan_prompt_files_for_batch(batch)
        manifest["image_files"] = scan_image_files_for_batch(batch)
        manifest["updated_at"] = now_iso()
        history = manifest.get("image_revision_history") if isinstance(manifest.get("image_revision_history"), list) else []
        history.append(
            {
                "revision_id": revision_id,
                "image_file": image_file,
                "comment": comment,
                "engine": engine,
                "aspect_ratio": aspect_ratio,
                "created_at": now_iso(),
                "original_backup": str(backup_image.relative_to(ROOT)).replace("\\", "/") if backup_image else "",
            }
        )
        manifest["image_revision_history"] = history
        (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _write_revision_status(run_dir, revision_id, status="completed", phase="completed", image_file=image_file, message="Image revision completed")
    except Exception as exc:
        if original_path is not None and backup_image is not None and backup_image.exists():
            try:
                original_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_image, original_path)
                if backup_meta is not None and backup_meta.exists():
                    shutil.copy2(backup_meta, original_path.with_suffix(".json"))
            except Exception:
                pass
        (run_dir / "logs" / f"revision_{revision_id}_error.txt").write_text(
            f"{exc}\n\n{traceback.format_exc()}", encoding="utf-8"
        )
        _write_revision_status(run_dir, revision_id, status="error", phase="error", error=str(exc), message=f"Revision failed: {exc}")


def api_revise_image_with_comment(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    image_file = str(payload.get("image_file") or "").strip().replace("\\", "/")
    comment = str(payload.get("comment") or "").strip()
    engine = str(payload.get("engine") or "gemini").strip().lower()
    headless = bool(payload.get("headless", False))
    if not image_file:
        raise HTTPException(status_code=400, detail="image_file is required")
    if not comment:
        raise HTTPException(status_code=400, detail="Write the changes you want")
    if len(comment) > 8000:
        raise HTTPException(status_code=400, detail="Comment is too long (maximum 8,000 characters)")
    if engine not in {"gemini", "chatgpt"}:
        raise HTTPException(status_code=400, detail="engine must be gemini or chatgpt")

    run_dir = RUNS_ROOT / run_id
    if not (run_dir / "manifest.json").exists():
        raise HTTPException(status_code=404, detail="Run not found")
    revision_id = f"rev_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    _write_revision_status(
        run_dir,
        revision_id,
        status="queued",
        phase="queued",
        image_file=image_file,
        comment=comment,
        engine=engine,
        message="Image revision queued",
    )
    threading.Thread(
        target=_run_revision,
        kwargs={
            "run_id": run_id,
            "revision_id": revision_id,
            "image_file": image_file,
            "comment": comment,
            "engine": engine,
            "headless": headless,
        },
        daemon=True,
    ).start()
    return {"status": "started", "run_id": run_id, "revision_id": revision_id}
