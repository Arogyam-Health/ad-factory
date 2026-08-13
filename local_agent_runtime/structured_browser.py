from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .storage import AgentState


_ENGINES = frozenset({"chatgpt", "gemini"})
_MODES = {"45": "45", "4:5": "45", "both": "both", "916": "916", "9:16": "916"}
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class BrowserAutomation(Protocol):
    def generate(
        self,
        *,
        engine: str,
        prompt_id: str,
        aspect_ratio: str,
        prompt_path: Path,
        upload_manifest_path: Path,
        output_dir: Path,
    ) -> bytes:
        ...


class DeterministicFakeBrowser:
    """Deterministic local browser used only by focused tests."""

    def __init__(self, outcomes: list[Any] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        *,
        engine: str,
        prompt_id: str,
        aspect_ratio: str,
        prompt_path: Path,
        upload_manifest_path: Path,
        output_dir: Path,
    ) -> bytes:
        manifest = json.loads(upload_manifest_path.read_text(encoding="utf-8"))
        self.calls.append(
            {
                "engine": engine,
                "prompt_id": prompt_id,
                "aspect_ratio": aspect_ratio,
                "prompt_path": prompt_path,
                "manifest_path": upload_manifest_path,
                "manifest": manifest,
            }
        )
        index = len(self.calls) - 1
        if self.outcomes:
            outcome = self.outcomes[min(index, len(self.outcomes) - 1)]
            if isinstance(outcome, Exception):
                raise outcome
            return bytes(outcome)
        return f"fake:{engine}:{prompt_id}:{aspect_ratio}".encode()


class LocalScriptBrowser:
    """Run one local prompt through the selected local browser automation script."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = project_root or Path(__file__).resolve().parents[1]

    def generate(
        self,
        *,
        engine: str,
        prompt_id: str,
        aspect_ratio: str,
        prompt_path: Path,
        upload_manifest_path: Path,
        output_dir: Path,
    ) -> bytes:
        script = self.project_root / "scripts" / (
            "gemini_web_automation.py" if engine == "gemini" else "chatgpt_web_sutomation.py"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        result_manifest = output_dir / "result.json"
        command = [
            sys.executable,
            "-u",
            str(script),
            "--prompt-dir",
            str(prompt_path.parent),
            "--prompt-glob",
            prompt_path.name,
            "--out-dir",
            str(output_dir),
            "--starting-prompt-file",
            "",
            "--aspect-ratio",
            aspect_ratio,
            "--upload-manifest",
            str(upload_manifest_path),
            "--result-manifest",
            str(result_manifest),
        ]
        if engine == "chatgpt":
            command.extend(["--cdp-url", os.getenv("AGENT_CDP_URL", "http://127.0.0.1:9222")])
        completed = subprocess.run(
            command,
            cwd=self.project_root,
            timeout=900,
        )
        if completed.returncode:
            raise RuntimeError("Local browser automation failed")
        result = json.loads(result_manifest.read_text(encoding="utf-8"))
        output_path = Path(str(result.get("output_path") or "")).resolve()
        output_path.relative_to(output_dir.resolve())
        if not output_path.is_file() or output_path.suffix.lower() not in _IMAGE_SUFFIXES:
            raise RuntimeError("Local browser automation produced no output")
        return output_path.read_bytes()


@dataclass(frozen=True)
class LocalResource:
    resource_id: str
    version: int
    role: str
    path: Path
    media_type: str


class StructuredBrowserExecutor:
    def __init__(
        self,
        state: AgentState,
        *,
        browser: BrowserAutomation | None = None,
        max_attempts: int = 2,
        workflow_prefix: str = "structured-browser",
        product_assets: list[dict[str, Any]] | None = None,
        conversion_prompt_text: str = "",
    ) -> None:
        self.state = state
        self.browser = browser or LocalScriptBrowser()
        self.max_attempts = max(1, min(int(max_attempts), 3))
        self.workflow_prefix = self._identifier(workflow_prefix)
        self.product_assets = product_assets
        self.conversion_prompt_text = conversion_prompt_text

    def _completed_result(self, job_id: str) -> dict[str, Any] | None:
        with self.state._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json FROM outbox
                WHERE event_type = 'structured_images_completed'
                ORDER BY created_at DESC
                """
            ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            if payload.get("job_id") == job_id:
                return payload
        return None

    @staticmethod
    def _identifier(value: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
        return clean[:120] or "item"

    @staticmethod
    def _stable_id(prefix: str, *parts: str) -> str:
        return prefix + hashlib.sha256("\0".join(parts).encode()).hexdigest()[:24]

    def _resource(
        self,
        owner_key: str,
        resource_id: str,
        version: int,
        role: str,
        *,
        required_kind: str | None = None,
    ) -> LocalResource:
        with self.state._connect() as conn:
            row = conn.execute(
                """
                SELECT r.kind, r.deleted_at, o.relative_path, o.media_type
                FROM resources r
                JOIN resource_versions rv
                  ON rv.resource_id = r.resource_id AND rv.version = ?
                JOIN objects o ON o.sha256 = rv.object_sha256
                WHERE r.resource_id = ? AND r.owner_key = ?
                """,
                (int(version), resource_id, owner_key),
            ).fetchone()
        if row is None or row["deleted_at"] is not None:
            raise ValueError("Selected local resource version is unavailable")
        if required_kind and str(row["kind"]) != required_kind:
            raise ValueError("Selected local resource has the wrong kind")
        path = (self.state.paths.root / str(row["relative_path"])).resolve()
        path.relative_to(self.state.paths.root.resolve())
        if not path.is_file():
            raise ValueError("Selected local resource bytes are unavailable")
        return LocalResource(resource_id, int(version), role, path, str(row["media_type"]))

    @staticmethod
    def _settings(context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        settings_entries = [
            entry for entry in context["entries"] if entry.get("role") == "structured_settings"
        ]
        if len(settings_entries) != 1:
            raise ValueError("Local structured settings are unavailable")
        settings = json.loads(
            Path(settings_entries[0]["local_path"]).read_text(encoding="utf-8")
        )
        if not isinstance(settings, dict):
            raise ValueError("Local structured settings are invalid")
        return settings_entries[0], settings

    def _settings_uploads(
        self, context: dict[str, Any], settings: dict[str, Any]
    ) -> list[LocalResource]:
        selected: list[LocalResource] = []
        for key, role in (("product_assets", "product"), ("logo_assets", "logo")):
            values = settings.get(key)
            if values is None and key == "logo_assets":
                values = settings.get("logos")
            if not isinstance(values, list):
                values = []
            for value in values:
                if not isinstance(value, dict):
                    raise ValueError("Selected local resource declaration is invalid")
                selected.append(
                    self._resource(
                        str(context["owner_key"]),
                        str(value.get("resource_id") or ""),
                        int(value.get("version") or 0),
                        role,
                        required_kind="product_image",
                    )
                )
        if not any(item.role == "product" for item in selected):
            raise ValueError("At least one selected product resource is required")
        return selected

    def _conversion_prompt(
        self,
        context: dict[str, Any],
        settings: dict[str, Any],
    ) -> LocalResource:
        reference = settings.get("conversion_prompt")
        if not isinstance(reference, dict):
            raise ValueError("Versioned local 9:16 conversion prompt is unavailable")
        resource_id = str(reference.get("resource_id") or "")
        version = int(reference.get("version") or 0)
        matching_entries = [
            entry
            for entry in context["entries"]
            if entry.get("role") == "conversion_prompt"
            and str(entry.get("resource_id") or "") == resource_id
            and int(entry.get("resource_version") or 0) == version
        ]
        if len(matching_entries) != 1:
            raise ValueError("Versioned local 9:16 conversion prompt is not in this run")
        return self._resource(
            str(context["owner_key"]),
            resource_id,
            version,
            "conversion_prompt",
            required_kind="config_file",
        )

    @staticmethod
    def _prompts(context: dict[str, Any], selected_prompt_id: str) -> list[dict[str, Any]]:
        prompts = [
            entry
            for entry in context["entries"]
            if entry.get("kind") == "prompt"
            and entry.get("role") == "prompt"
            and str(entry.get("aspect_ratio") or "4:5") == "4:5"
        ]
        if selected_prompt_id:
            prompts = [
                entry for entry in prompts if str(entry.get("prompt_id") or "") == selected_prompt_id
            ]
        if not prompts:
            raise ValueError("No matching local structured prompt is available")
        return prompts

    @staticmethod
    def _display_stem(prompt: dict[str, Any]) -> str:
        """Read the canonical stem Render assigned, falling back to the prompt id."""
        raw = prompt.get("metadata_json")
        metadata: dict[str, Any] = {}
        if isinstance(raw, str) and raw:
            try:
                metadata = json.loads(raw)
            except json.JSONDecodeError:
                metadata = {}
        elif isinstance(raw, dict):
            metadata = raw
        stem = re.sub(
            r"[^A-Za-z0-9_.-]+", "_", str(metadata.get("display_stem") or "")
        ).strip("_")
        return stem or str(prompt.get("prompt_id") or "prompt")

    def _existing_output(
        self, run_id: str, prompt_id: str, aspect_ratio: str
    ) -> dict[str, Any] | None:
        with self.state._connect() as conn:
            row = conn.execute(
                """
                SELECT out.*, ov.resource_id, ov.resource_version, rv.object_sha256
                FROM outputs out
                JOIN output_versions ov
                  ON ov.output_id = out.output_id AND ov.version = out.current_version
                JOIN resource_versions rv
                  ON rv.resource_id = ov.resource_id
                 AND rv.version = ov.resource_version
                WHERE out.run_id = ? AND out.prompt_id = ? AND out.aspect_ratio = ?
                  AND out.status = 'available'
                """,
                (run_id, prompt_id, aspect_ratio),
            ).fetchone()
        return dict(row) if row else None

    def _materialize(
        self,
        *,
        job_id: str,
        run_id: str,
        prompt_id: str,
        aspect_ratio: str,
        prompt_content: bytes,
        resources: list[LocalResource],
        display_stem: str = "",
    ) -> tuple[Path, Path, Path, dict[str, Any]]:
        phase = "916" if aspect_ratio == "9:16" else "45"
        root = (
            self.state.paths.staging
            / self.workflow_prefix
            / self._identifier(job_id)
            / self._identifier(prompt_id)
            / phase
        )
        uploads = root / "uploads"
        output = root / "output"
        uploads.mkdir(parents=True, exist_ok=True)
        output.mkdir(parents=True, exist_ok=True)
        # The automation derives the generated image name from this stem, so the
        # downloaded creative inherits the canonical prompt name.
        prompt_path = root / f"{display_stem or 'prompt'}.txt"
        prompt_path.write_bytes(prompt_content)
        upload_set_id = self._stable_id("ups_", job_id, prompt_id, aspect_ratio)
        manifest_entries = []
        state_entries = []
        for position, resource in enumerate(resources, start=1):
            suffix = mimetypes.guess_extension(resource.media_type.split(";", 1)[0]) or resource.path.suffix
            if suffix == ".jpe":
                suffix = ".jpg"
            target = uploads / f"{position:04d}{suffix if suffix in _IMAGE_SUFFIXES else '.bin'}"
            target.write_bytes(resource.path.read_bytes())
            manifest_entries.append(
                {
                    "position": position,
                    "resource_id": resource.resource_id,
                    "version": resource.version,
                    "role": resource.role,
                    "path": str(target.resolve()),
                }
            )
            state_entries.append((resource.resource_id, resource.version, resource.role))
        manifest = {
            "upload_set_id": upload_set_id,
            "run_id": run_id,
            "prompt_id": prompt_id,
            "aspect_ratio": aspect_ratio,
            "entries": manifest_entries,
        }
        manifest_path = root / "upload-manifest.json"
        temporary = manifest_path.with_name(f".{manifest_path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, manifest_path)
        self.state.create_upload_set(
            upload_set_id=upload_set_id,
            run_id=run_id,
            prompt_id=prompt_id,
            phase=aspect_ratio,
            version=1,
            entries=state_entries,
            operation_id=f"{self.workflow_prefix}:{job_id}:upload-set:{prompt_id}:{phase}",
        )
        return prompt_path, manifest_path, output, manifest

    def _commit_output(
        self,
        *,
        job_id: str,
        owner_key: str,
        run_id: str,
        prompt_id: str,
        item_id: str,
        aspect_ratio: str,
        content: bytes,
        source_output_version: int | None,
        source_output_id: str | None = None,
        conversion_prompt: LocalResource | None = None,
        display_stem: str = "",
    ) -> dict[str, Any]:
        phase = "916" if aspect_ratio == "9:16" else "45"
        display_name = (
            f"{display_stem}_{aspect_ratio.replace(':', '_')}" if display_stem else ""
        )
        temporary = self.state.paths.staging / f".browser-output-{uuid.uuid4().hex}.png"
        temporary.write_bytes(content)
        output_id = self._stable_id("out_", run_id, prompt_id, aspect_ratio)
        try:
            resource = self.state.put_resource(
                source=temporary,
                owner_key=owner_key,
                kind="output_image",
                logical_key=f"{output_id}:v1",
                operation_id=f"{self.workflow_prefix}:{job_id}:resource:{prompt_id}:{phase}",
                metadata={
                    "run_id": run_id,
                    "prompt_id": prompt_id,
                    "item_id": item_id,
                    "aspect_ratio": aspect_ratio,
                    "attempt": 1,
                    **({"display_name": display_name} if display_name else {}),
                    **(
                        {
                            "source_output_id": source_output_id,
                            "source_output_version": source_output_version,
                        }
                        if source_output_id and source_output_version is not None
                        else {}
                    ),
                    **(
                        {
                            "conversion_prompt_resource_id": conversion_prompt.resource_id,
                            "conversion_prompt_version": conversion_prompt.version,
                        }
                        if conversion_prompt is not None
                        else {}
                    ),
                },
                media_type="image/png",
            )
        finally:
            temporary.unlink(missing_ok=True)
        output = self.state.create_output(
            output_id=output_id,
            run_id=run_id,
            prompt_id=prompt_id,
            item_id=item_id,
            aspect_ratio=aspect_ratio,
            resource_id=resource.resource_id,
            resource_version=resource.version,
            source_output_version=source_output_version,
            operation_id=f"{self.workflow_prefix}:{job_id}:output:{prompt_id}:{phase}",
        )
        return {
            **output,
            "resource_id": resource.resource_id,
            "resource_version": resource.version,
            "sha256": resource.object_sha256,
        }

    def _projection(
        self,
        *,
        job_id: str,
        run_id: str,
        status: str,
        engine: str,
        mode: str,
        total_count: int,
        completed_count: int,
        retry_count: int,
        latest: dict[str, Any] | None = None,
        error_code: str = "",
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "job_id": job_id,
            "run_id": run_id,
            "status": status,
            "engine": engine,
            "mode": mode,
            "total_count": total_count,
            "completed_count": completed_count,
            "output_count": completed_count,
            "retry_count": retry_count,
        }
        if latest:
            result.update(
                {
                    "latest_output_id": latest["output_id"],
                    "latest_output_version": int(latest["version"]),
                    "latest_output_sha256": latest["sha256"],
                }
            )
        if error_code:
            result["error_code"] = error_code
        return result

    def execute(self, job_id: str) -> dict[str, Any]:
        completed = self._completed_result(job_id)
        if completed is not None:
            return completed
        context = self.state.resolve_job_context(job_id)
        payload = context["payload"]
        if payload.get("command") != "generate_images":
            raise ValueError("Local structured browser command is invalid")
        parameters = payload.get("parameters") or {}
        engine = str(parameters.get("engine") or "").lower()
        mode = _MODES.get(str(parameters.get("mode") or "").lower(), "")
        if engine not in _ENGINES or not mode:
            raise ValueError("Local structured browser settings are invalid")
        run_id = str(context["run"]["run_id"])
        owner_key = str(context["owner_key"])
        selected_prompt_id = str(parameters.get("prompt_version_id") or "")
        prompts = self._prompts(context, selected_prompt_id)
        phases = ("4:5", "9:16") if mode == "both" else (("9:16",) if mode == "916" else ("4:5",))
        total_count = len(prompts) * len(phases)
        completed_count = 0
        retry_count = 0
        latest: dict[str, Any] | None = None
        error_code = ""
        try:
            try:
                _, settings = self._settings(context)
            except ValueError:
                settings = {}
            if self.product_assets is not None:
                settings = {**settings, "product_assets": self.product_assets}
            product_uploads = (
                self._settings_uploads(context, settings) if "4:5" in phases else []
            )
            conversion_prompt = None
            if "9:16" in phases and not self.conversion_prompt_text:
                conversion_prompt = self._conversion_prompt(context, settings)
            for aspect_ratio in phases:
                for prompt in prompts:
                    prompt_id = str(prompt.get("prompt_id") or "")
                    item_id = str(prompt.get("item_id") or prompt.get("entry_id") or prompt_id)
                    existing = self._existing_output(run_id, prompt_id, aspect_ratio)
                    if existing is not None:
                        completed_count += 1
                        continue
                    source_output_version = None
                    source_output_id = None
                    uploads = product_uploads
                    prompt_content = Path(prompt["local_path"]).read_bytes()
                    if aspect_ratio == "9:16":
                        source = self._existing_output(run_id, prompt_id, "4:5")
                        if source is None:
                            raise ValueError("Matching local 4:5 output version is unavailable")
                        source_output_version = int(source["current_version"])
                        source_output_id = str(source["output_id"])
                        uploads = [
                            self._resource(
                                owner_key,
                                str(source["resource_id"]),
                                int(source["resource_version"]),
                                "source_creative",
                                required_kind="output_image",
                            )
                        ]
                        conversion_body = self.conversion_prompt_text.strip()
                        if not conversion_body and conversion_prompt is not None:
                            conversion_body = conversion_prompt.path.read_text(
                                encoding="utf-8"
                            ).strip()
                        if not conversion_body:
                            raise ValueError("Render 9:16 conversion prompt is empty")
                        context_lines = [
                            "[LOCAL BOUNDED CONVERSION CONTEXT]",
                            f"prompt_id={prompt_id}",
                            f"source_output_id={source_output_id}",
                            f"source_output_version={source_output_version}",
                            f"source_creative_sha256={source['object_sha256']}",
                        ]
                        if conversion_prompt is not None:
                            context_lines.extend(
                                [
                                    "conversion_prompt_resource_id="
                                    f"{conversion_prompt.resource_id}",
                                    "conversion_prompt_version="
                                    f"{conversion_prompt.version}",
                                ]
                            )
                        context_lines.append("target_aspect_ratio=9:16")
                        bounded_context = "\n".join(context_lines)
                        prompt_content = (
                            conversion_body + "\n\n" + bounded_context + "\n"
                        ).encode("utf-8")
                    prompt_path, manifest_path, output_dir, _ = self._materialize(
                        job_id=job_id,
                        run_id=run_id,
                        prompt_id=prompt_id,
                        aspect_ratio=aspect_ratio,
                        prompt_content=prompt_content,
                        resources=uploads,
                        display_stem=self._display_stem(prompt),
                    )
                    content: bytes | None = None
                    for attempt in range(1, self.max_attempts + 1):
                        try:
                            content = self.browser.generate(
                                engine=engine,
                                prompt_id=prompt_id,
                                aspect_ratio=aspect_ratio,
                                prompt_path=prompt_path,
                                upload_manifest_path=manifest_path,
                                output_dir=output_dir,
                            )
                            break
                        except Exception:
                            if attempt >= self.max_attempts:
                                raise
                            retry_count += 1
                    if not content:
                        raise RuntimeError("Local browser output is empty")
                    latest = self._commit_output(
                        job_id=job_id,
                        owner_key=owner_key,
                        run_id=run_id,
                        prompt_id=prompt_id,
                        item_id=item_id,
                        aspect_ratio=aspect_ratio,
                        content=content,
                        source_output_version=source_output_version,
                        source_output_id=source_output_id,
                        conversion_prompt=(
                            conversion_prompt if aspect_ratio == "9:16" else None
                        ),
                        display_stem=self._display_stem(prompt),
                    )
                    completed_count += 1
                    progress = self._projection(
                        job_id=job_id,
                        run_id=run_id,
                        status="running",
                        engine=engine,
                        mode=mode,
                        total_count=total_count,
                        completed_count=completed_count,
                        retry_count=retry_count,
                        latest=latest,
                    )
                    self.state.queue_projection(
                        owner_key=owner_key,
                        operation_id=f"structured-browser:{job_id}:progress:{completed_count}",
                        event_type="structured_images_progress",
                        payload=progress,
                    )
            final = self._projection(
                job_id=job_id,
                run_id=run_id,
                status="completed",
                engine=engine,
                mode=mode,
                total_count=total_count,
                completed_count=completed_count,
                retry_count=retry_count,
                latest=latest,
            )
            self.state.queue_projection(
                owner_key=owner_key,
                operation_id=f"structured-browser:{job_id}:completed",
                event_type="structured_images_completed",
                payload=final,
            )
            self.state.update_job_status(job_id, "completed")
            return final
        except Exception as exc:
            error_code = (
                "local_resource_missing" if isinstance(exc, ValueError) else "browser_automation_failed"
            )
            failed = self._projection(
                job_id=job_id,
                run_id=run_id,
                status="failed",
                engine=engine,
                mode=mode,
                total_count=total_count,
                completed_count=completed_count,
                retry_count=retry_count,
                latest=latest,
                error_code=error_code,
            )
            self.state.queue_projection(
                owner_key=owner_key,
                operation_id=f"structured-browser:{job_id}:failed:{completed_count}",
                event_type="structured_images_failed",
                payload=failed,
            )
            self.state.update_job_status(job_id, "failed")
            return failed
