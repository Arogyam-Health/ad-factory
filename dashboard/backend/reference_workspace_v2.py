from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from dashboard.backend import reference_flow as flow
from dashboard.backend import reference_workspace as base
from dashboard.backend.pipeline.clock import ensure_dirs, make_run_id, now_iso
from dashboard.backend.pipeline.images import scan_image_files_for_batch, scan_prompt_files_for_batch
from dashboard.backend.pipeline.paths import ROOT, RUNS_ROOT
from dashboard.backend.pipeline.run_control import cancel_event_for_run
from dashboard.backend.reference_library import (
    _copy_persistent_references,
    _reserve_reference_batch_name,
    _save_direct_references,
)

CLEAN_REFERENCE_STARTING_PROMPT = (
    "Use the uploaded reference image as the visual direction. Preserve only the selected uploaded "
    "product packshots exactly, create one finished advertisement, and return no explanation."
)
LEGACY_PROMPT_MARKERS = (
    "OBESITY KILLER KIT - GLOBAL PRODUCT RULES",
    "A0. OUTPUT COUNT - ABSOLUTE RULE",
)


def _migrate_reference_prompt() -> str:
    base._ensure_workspace()
    current = base.STARTING_PROMPT_PATH.read_text(encoding="utf-8", errors="ignore").strip()
    if not current or any(marker in current for marker in LEGACY_PROMPT_MARKERS):
        base.STARTING_PROMPT_PATH.write_text(CLEAN_REFERENCE_STARTING_PROMPT + "\n", encoding="utf-8")
        return CLEAN_REFERENCE_STARTING_PROMPT
    return current


def api_reference_workspace_v2() -> dict[str, Any]:
    _migrate_reference_prompt()
    return base.api_reference_workspace()


def _resolve_selected_product_images(paths: list[Any]) -> list[Path]:
    base._ensure_workspace()
    selected: list[Path] = []
    seen: set[str] = set()
    root = base.PRODUCT_IMAGES_DIR.resolve()
    for raw in paths:
        rel = str(raw or "").strip().replace("\\", "/")
        if not rel:
            continue
        candidate = (ROOT / rel).resolve()
        if root not in candidate.parents or not candidate.exists() or not candidate.is_file():
            continue
        if candidate.suffix.lower() not in base._IMAGE_EXTENSIONS:
            continue
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            selected.append(candidate)
    return selected


def _snapshot_selected_workspace(run_dir: Path, selected_paths: list[Any]) -> tuple[Path, list[Path], str]:
    base._ensure_workspace()
    starter = _migrate_reference_prompt()
    asset_root = run_dir / "inputs" / "reference_workspace"
    product_dir = asset_root / "product_images"
    product_dir.mkdir(parents=True, exist_ok=True)
    doc_target = asset_root / "product_document.txt"
    if not base.PRODUCT_DOC_PATH.exists():
        raise HTTPException(status_code=400, detail="Reference product document is missing")
    import shutil
    shutil.copy2(base.PRODUCT_DOC_PATH, doc_target)

    sources = _resolve_selected_product_images(selected_paths)
    product_paths: list[Path] = []
    for source in sources:
        target = product_dir / source.name
        shutil.copy2(source, target)
        product_paths.append(target.resolve())
    return doc_target, product_paths, starter


def _terminate_process_tree(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _run_cancellable_image_job(
    *,
    run_dir: Path,
    run_id: str,
    engine: str,
    batch: str,
    prompt_path: Path,
    source_file: Path,
    headless: bool,
) -> subprocess.CompletedProcess[str]:
    job_dir = run_dir / "context" / "active_jobs"
    job_dir.mkdir(parents=True, exist_ok=True)
    token = f"{prompt_path.stem}_{int(time.time() * 1000)}"
    config_path = job_dir / f"{token}.json"
    result_path = job_dir / f"{token}.result.json"
    config_path.write_text(
        json.dumps(
            {
                "engine": engine,
                "batch": batch,
                "prompt_path": str(prompt_path.resolve()),
                "source_file": str(source_file.resolve()),
                "aspect_ratio": "4:5",
                "headless": headless,
                "run_dir": str(run_dir.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    kwargs: dict[str, Any] = {
        "cwd": str(ROOT),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        [sys.executable, "scripts/reference_image_job.py", "--config", str(config_path), "--result", str(result_path)],
        **kwargs,
    )
    cancel_event = cancel_event_for_run(run_id)
    while proc.poll() is None:
        if cancel_event.is_set():
            _terminate_process_tree(proc)
            output = proc.stdout.read() if proc.stdout else ""
            return subprocess.CompletedProcess(proc.args, 130, output, "Cancelled by user")
        time.sleep(0.2)
    output = proc.stdout.read() if proc.stdout else ""
    if result_path.exists():
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            return subprocess.CompletedProcess(
                proc.args,
                int(payload.get("returncode", proc.returncode or 0)),
                str(payload.get("stdout") or output),
                str(payload.get("stderr") or ""),
            )
        except Exception:
            pass
    return subprocess.CompletedProcess(proc.args, int(proc.returncode or 0), output, "")


def _run_reference_worker_v2(
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
            raise RuntimeError("No product images were selected")
        flow._write_status(run_dir, status="running", phase="4:5 generation", completed_jobs=0, total_jobs=total, message="Preparing reference jobs")
        for persona_index, persona in enumerate(personas, start=1):
            for reference_index, reference in enumerate(references, start=1):
                if cancel_event.is_set():
                    raise InterruptedError("Cancelled by user")
                job_index = (persona_index - 1) * len(references) + reference_index
                prompt_path = base._write_reference_prompt(
                    batch=batch,
                    persona=persona,
                    reference=reference,
                    product_doc_text=product_doc_text,
                    reference_index=reference_index,
                    starting_prompt=starting_prompt,
                )
                source_file = run_dir / "context" / "reference_sources" / f"{prompt_path.stem}.images.txt"
                source_file.parent.mkdir(parents=True, exist_ok=True)
                source_file.write_text("\n".join([reference["absolute_path"], *[str(path) for path in product_images]]) + "\n", encoding="utf-8")
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
                result = _run_cancellable_image_job(
                    run_dir=run_dir,
                    run_id=run_id,
                    engine=engine,
                    batch=batch,
                    prompt_path=prompt_path,
                    source_file=source_file,
                    headless=headless,
                )
                if cancel_event.is_set() or result.returncode == 130:
                    raise InterruptedError("Cancelled by user")
                if result.returncode == 0:
                    grouped = flow._group_generated_output(batch=batch, aspect_dir="4_5", prompt_path=prompt_path, persona=persona, reference=reference)
                    if grouped:
                        completed += 1
                    else:
                        failures.append({"persona_number": int(persona["persona_number"]), "reference_image": reference.get("original_name", ""), "error": "Output image could not be located"})
                else:
                    failures.append({"persona_number": int(persona["persona_number"]), "reference_image": reference.get("original_name", ""), "error": (result.stderr or result.stdout or "Generation failed")[-1200:]})
                flow._write_status(run_dir, status="running", phase="4:5 generation", completed_jobs=job_index, total_jobs=total, completed_45=completed, failures=len(failures), message=f"Completed {job_index}/{total} reference jobs")
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
        if cancel_event.is_set():
            raise InterruptedError("Cancelled by user")
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
            "product_doc": base._rel(product_doc_path),
            "product_images": [base._rel(path) for path in product_images],
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


async def api_run_execute_reference_workspace_v2(
    *,
    config: str,
    reference_image_files: list[UploadFile] | None,
    product_info_file: UploadFile | None,
    input_image_files: list[UploadFile] | None,
    clear_input_images: bool,
) -> dict[str, Any]:
    ensure_dirs()
    base._ensure_workspace()
    try:
        cfg = json.loads(config)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid config JSON") from exc
    if product_info_file is not None:
        await base.api_upload_reference_product_doc(product_info_file)
    if input_image_files:
        await base.api_upload_reference_product_images(input_image_files, replace=clear_input_images)
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
    selected_product_paths = cfg.get("product_image_paths") if isinstance(cfg.get("product_image_paths"), list) else []
    product_doc_path, product_images, starting_prompt = _snapshot_selected_workspace(run_dir, selected_product_paths)
    if not product_images:
        raise HTTPException(status_code=400, detail="Select at least one product image")
    batch = _reserve_reference_batch_name()
    personas = [persona_map[number] for number in selected]
    snapshot = {
        "run_id": run_id,
        "batch": batch,
        "flow_type": "reference_image",
        "engine": engine,
        "selected_personas": selected,
        "reference_images": [{k: v for k, v in item.items() if k != "absolute_path"} for item in references],
        "product_doc": base._rel(product_doc_path),
        "product_images": [base._rel(path) for path in product_images],
        "starting_prompt": starting_prompt,
        "created_at": now_iso(),
    }
    (run_dir / "context" / "reference_flow.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    total = len(personas) * len(references)
    flow._write_status(run_dir, status="queued", phase="queued", run_id=run_id, batch=batch, engine=engine, completed_jobs=0, total_jobs=total, message="Reference run queued")
    threading.Thread(
        target=_run_reference_worker_v2,
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
