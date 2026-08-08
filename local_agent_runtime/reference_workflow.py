from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .storage import AgentState
from .structured_browser import (
    BrowserAutomation,
    LocalResource,
    StructuredBrowserExecutor,
)


_ENGINES = frozenset({"chatgpt", "gemini"})
_MODES = {"45": "45", "4:5": "45", "both": "both", "916": "916", "9:16": "916"}


class ReferenceWorkflowExecutor(StructuredBrowserExecutor):
    """Resolve and execute an owner-scoped Reference run entirely locally."""

    def __init__(
        self,
        state: AgentState,
        *,
        browser: BrowserAutomation | None = None,
        max_attempts: int = 2,
    ) -> None:
        super().__init__(
            state,
            browser=browser,
            max_attempts=max_attempts,
            workflow_prefix="reference-workflow",
        )

    def _completed_result(self, job_id: str) -> dict[str, Any] | None:
        with self.state._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json FROM outbox
                WHERE event_type = 'reference_generation_completed'
                ORDER BY created_at DESC
                """
            ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            if payload.get("job_id") == job_id:
                return payload
        return None

    @staticmethod
    def _settings(context: dict[str, Any]) -> dict[str, Any]:
        entries = [
            entry for entry in context["entries"] if entry.get("role") == "reference_settings"
        ]
        if len(entries) != 1:
            raise ValueError("Versioned local Reference settings are unavailable")
        value = json.loads(Path(entries[0]["local_path"]).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Local Reference settings are invalid")
        return value

    def _declared_resource(
        self,
        context: dict[str, Any],
        declaration: Any,
        role: str,
        required_kind: str,
    ) -> LocalResource:
        if not isinstance(declaration, dict):
            raise ValueError("Versioned local Reference resource declaration is invalid")
        return self._resource(
            str(context["owner_key"]),
            str(declaration.get("resource_id") or ""),
            int(declaration.get("version") or 0),
            role,
            required_kind=required_kind,
        )

    def _attached_resource(
        self,
        context: dict[str, Any],
        settings: dict[str, Any],
        key: str,
        role: str,
        required_kind: str,
    ) -> LocalResource:
        resource = self._declared_resource(context, settings.get(key), role, required_kind)
        matches = [
            entry
            for entry in context["entries"]
            if entry.get("role") == role
            and entry.get("resource_id") == resource.resource_id
            and int(entry.get("resource_version") or 0) == resource.version
        ]
        if len(matches) != 1:
            raise ValueError("Explicit local Reference resource version is not pinned to this run")
        return resource

    def _references(
        self, context: dict[str, Any], settings: dict[str, Any]
    ) -> list[tuple[LocalResource, LocalResource | None]]:
        declarations = settings.get("references")
        if not isinstance(declarations, list) or not declarations or len(declarations) > 250:
            raise ValueError("Selected local Reference resources are invalid")
        result: list[tuple[LocalResource, LocalResource | None]] = []
        seen: set[tuple[str, int]] = set()
        for declaration in declarations:
            reference = self._declared_resource(
                context, declaration, "reference", "reference_image"
            )
            identity = (reference.resource_id, reference.version)
            if identity in seen:
                continue
            seen.add(identity)
            comment = None
            if isinstance(declaration, dict) and declaration.get("comment_resource_id"):
                comment = self._resource(
                    str(context["owner_key"]),
                    str(declaration["comment_resource_id"]),
                    int(declaration.get("comment_version") or 0),
                    "reference_comment",
                    required_kind="config_file",
                )
            result.append((reference, comment))
        if not result:
            raise ValueError("At least one selected Reference resource is required")
        return result

    def _products(
        self, context: dict[str, Any], settings: dict[str, Any]
    ) -> list[LocalResource]:
        declarations = settings.get("products")
        if not isinstance(declarations, list) or not declarations or len(declarations) > 250:
            raise ValueError("Selected local product resources are invalid")
        products = [
            self._declared_resource(context, item, "product", "product_image")
            for item in declarations
        ]
        if len({(item.resource_id, item.version) for item in products}) != len(products):
            raise ValueError("Selected local product resources contain duplicates")
        return products

    @staticmethod
    def _persona_id(persona: dict[str, Any]) -> str:
        return str(
            persona.get("persona_id")
            or persona.get("persona_number")
            or persona.get("number")
            or ""
        )

    def _personas(
        self, settings: dict[str, Any], config: LocalResource
    ) -> list[dict[str, Any]]:
        value = json.loads(config.path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            raw = value.get("personas", value)
            candidates = list(raw.values()) if isinstance(raw, dict) else raw
        else:
            candidates = value
        if not isinstance(candidates, list):
            raise ValueError("Versioned local persona config is invalid")
        by_id = {
            self._persona_id(item): item
            for item in candidates
            if isinstance(item, dict) and self._persona_id(item)
        }
        selected = settings.get("persona_ids")
        if not isinstance(selected, list) or not selected or len(selected) > 100:
            raise ValueError("Selected local persona IDs are invalid")
        selected_ids = [str(value) for value in selected]
        if len(set(selected_ids)) != len(selected_ids) or any(
            persona_id not in by_id for persona_id in selected_ids
        ):
            raise ValueError("A selected persona is not in the pinned local config")
        return [by_id[persona_id] for persona_id in selected_ids]

    def _put_prompt(
        self,
        *,
        job_id: str,
        owner_key: str,
        run_id: str,
        persona: dict[str, Any],
        reference: LocalResource,
        starting_prompt: LocalResource,
        product_document: LocalResource,
        comment: LocalResource | None,
    ) -> tuple[str, LocalResource]:
        persona_id = self._persona_id(persona)
        prompt_id = self._stable_id(
            "prm_", run_id, persona_id, reference.resource_id, str(reference.version)
        )
        parts = [
            starting_prompt.path.read_text(encoding="utf-8").strip(),
            "TARGET PERSONA:\n" + json.dumps(persona, ensure_ascii=False, indent=2),
            (
                "REFERENCE INSTRUCTION:\n"
                + comment.path.read_text(encoding="utf-8").strip()
                if comment is not None
                else ""
            ),
            (
                "PRODUCT DOCUMENT (SOURCE OF TRUTH):\n"
                + product_document.path.read_text(encoding="utf-8").strip()
            ),
            (
                "Create one exact 4:5 portrait ad using the first uploaded image as the "
                "visual reference and only the subsequently uploaded product resources."
            ),
        ]
        body = "\n\n".join(part for part in parts if part).strip() + "\n"
        temporary = self.state.paths.staging / f".reference-prompt-{uuid.uuid4().hex}.txt"
        temporary.write_text(body, encoding="utf-8")
        try:
            version = self.state.put_resource(
                source=temporary,
                owner_key=owner_key,
                kind="prompt",
                logical_key=prompt_id,
                operation_id=f"reference-workflow:{job_id}:prompt:{prompt_id}",
                metadata={
                    "run_id": run_id,
                    "prompt_id": prompt_id,
                    "persona": persona_id,
                    "reference_resource_id": reference.resource_id,
                    "reference_resource_version": reference.version,
                    "aspect_ratio": "4:5",
                },
                media_type="text/plain; charset=utf-8",
            )
        finally:
            temporary.unlink(missing_ok=True)
        with self.state._connect() as conn:
            position = int(
                conn.execute(
                    "SELECT COALESCE(MAX(position), 0) + 1 FROM run_entries WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
            )
        self.state.add_run_entry(
            run_id=run_id,
            entry_id=f"entry-{prompt_id}",
            resource_id=version.resource_id,
            resource_version=version.version,
            role="prompt",
            prompt_id=prompt_id,
            item_id=self._stable_id("item_", persona_id, reference.resource_id),
            aspect_ratio="4:5",
            position=position,
            operation_id=f"reference-workflow:{job_id}:entry:{prompt_id}",
            metadata={
                "persona_id": persona_id,
                "reference_resource_id": reference.resource_id,
            },
        )
        return prompt_id, LocalResource(
            version.resource_id,
            version.version,
            "prompt",
            version.path,
            version.media_type,
        )

    @staticmethod
    def _reference_projection(
        *,
        job_id: str,
        run_id: str,
        status: str,
        engine: str,
        mode: str,
        total_count: int,
        completed_count: int,
        reference_count: int,
        persona_count: int,
        latest: dict[str, Any] | None = None,
        retry_count: int = 0,
        error_code: str = "",
    ) -> dict[str, Any]:
        projection: dict[str, Any] = {
            "job_id": job_id,
            "run_id": run_id,
            "status": status,
            "flow_type": "reference",
            "engine": engine,
            "mode": mode,
            "total_count": total_count,
            "completed_count": completed_count,
            "output_count": completed_count,
            "prompt_count": reference_count * persona_count,
            "reference_count": reference_count,
            "persona_count": persona_count,
            "retry_count": retry_count,
        }
        if latest:
            projection.update(
                {
                    "latest_output_id": latest["output_id"],
                    "latest_output_version": int(latest["version"]),
                    "latest_output_sha256": latest["sha256"],
                }
            )
        if error_code:
            projection["error_code"] = error_code
        return projection

    def execute(self, job_id: str) -> dict[str, Any]:
        completed = self._completed_result(job_id)
        if completed is not None:
            return completed
        context = self.state.resolve_job_context(job_id)
        payload = context["payload"]
        if payload.get("command") != "generate_reference":
            raise ValueError("Local Reference command is invalid")
        parameters = payload.get("parameters") or {}
        engine = str(parameters.get("engine") or "").lower()
        mode = _MODES.get(str(parameters.get("mode") or "").lower(), "")
        if engine not in _ENGINES or not mode:
            raise ValueError("Local Reference browser settings are invalid")
        run_id = str(context["run"]["run_id"])
        owner_key = str(context["owner_key"])
        completed_count = 0
        retry_count = 0
        latest: dict[str, Any] | None = None
        reference_count = 0
        persona_count = 0
        total_count = 0
        try:
            if str(context["run"].get("flow_type") or "") != "reference":
                raise ValueError("Local run is not a Reference workflow")
            settings = self._settings(context)
            references = self._references(context, settings)
            products = self._products(context, settings)
            product_document = self._attached_resource(
                context,
                settings,
                "product_document",
                "reference_product_document",
                "product_document",
            )
            starting_prompt = self._attached_resource(
                context,
                settings,
                "starting_prompt",
                "reference_starting_prompt",
                "config_file",
            )
            persona_config = self._attached_resource(
                context,
                settings,
                "persona_config",
                "reference_persona_config",
                "config_file",
            )
            personas = self._personas(settings, persona_config)
            conversion_prompt = (
                self._attached_resource(
                    context,
                    settings,
                    "conversion_prompt",
                    "conversion_prompt",
                    "config_file",
                )
                if mode in {"both", "916"}
                else None
            )
            reference_count = len(references)
            persona_count = len(personas)
            phases = (
                ("4:5", "9:16")
                if mode == "both"
                else (("9:16",) if mode == "916" else ("4:5",))
            )
            total_count = reference_count * persona_count * len(phases)
            prompts: list[tuple[str, LocalResource, LocalResource]] = []
            for persona in personas:
                for reference, comment in references:
                    prompt_id, prompt = self._put_prompt(
                        job_id=job_id,
                        owner_key=owner_key,
                        run_id=run_id,
                        persona=persona,
                        reference=reference,
                        starting_prompt=starting_prompt,
                        product_document=product_document,
                        comment=comment,
                    )
                    prompts.append((prompt_id, prompt, reference))
            for aspect_ratio in phases:
                for prompt_id, prompt, reference in prompts:
                    existing = self._existing_output(run_id, prompt_id, aspect_ratio)
                    if existing is not None:
                        completed_count += 1
                        latest = {
                            "output_id": existing["output_id"],
                            "version": existing["current_version"],
                            "sha256": existing["object_sha256"],
                        }
                        continue
                    source_output_id = None
                    source_output_version = None
                    effective_prompt = prompt.path.read_bytes()
                    uploads = [reference, *products]
                    if aspect_ratio == "9:16":
                        source = self._existing_output(run_id, prompt_id, "4:5")
                        if source is None:
                            raise ValueError("Matching local 4:5 Reference output is unavailable")
                        if conversion_prompt is None:
                            raise ValueError("Versioned local 9:16 conversion prompt is unavailable")
                        source_output_id = str(source["output_id"])
                        source_output_version = int(source["current_version"])
                        uploads = [
                            self._resource(
                                owner_key,
                                str(source["resource_id"]),
                                int(source["resource_version"]),
                                "source_creative",
                                required_kind="output_image",
                            )
                        ]
                        effective_prompt = (
                            conversion_prompt.path.read_text(encoding="utf-8").strip()
                            + "\n\n[LOCAL BOUNDED CONVERSION CONTEXT]\n"
                            + f"prompt_id={prompt_id}\n"
                            + f"source_output_id={source_output_id}\n"
                            + f"source_output_version={source_output_version}\n"
                            + f"source_creative_sha256={source['object_sha256']}\n"
                            + f"conversion_prompt_resource_id={conversion_prompt.resource_id}\n"
                            + f"conversion_prompt_version={conversion_prompt.version}\n"
                            + "target_aspect_ratio=9:16\n"
                        ).encode()
                    prompt_path, manifest_path, output_dir, _ = self._materialize(
                        job_id=job_id,
                        run_id=run_id,
                        prompt_id=prompt_id,
                        aspect_ratio=aspect_ratio,
                        prompt_content=effective_prompt,
                        resources=uploads,
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
                        raise RuntimeError("Local Reference browser output is empty")
                    latest = self._commit_output(
                        job_id=job_id,
                        owner_key=owner_key,
                        run_id=run_id,
                        prompt_id=prompt_id,
                        item_id=self._stable_id("item_", prompt_id),
                        aspect_ratio=aspect_ratio,
                        content=content,
                        source_output_version=source_output_version,
                        source_output_id=source_output_id,
                        conversion_prompt=conversion_prompt if aspect_ratio == "9:16" else None,
                    )
                    completed_count += 1
                    progress = self._reference_projection(
                        job_id=job_id,
                        run_id=run_id,
                        status="running",
                        engine=engine,
                        mode=mode,
                        total_count=total_count,
                        completed_count=completed_count,
                        reference_count=reference_count,
                        persona_count=persona_count,
                        latest=latest,
                        retry_count=retry_count,
                    )
                    self.state.queue_projection(
                        owner_key=owner_key,
                        operation_id=f"reference-workflow:{job_id}:progress:{completed_count}",
                        event_type="reference_generation_progress",
                        payload=progress,
                    )
            final = self._reference_projection(
                job_id=job_id,
                run_id=run_id,
                status="completed",
                engine=engine,
                mode=mode,
                total_count=total_count,
                completed_count=completed_count,
                reference_count=reference_count,
                persona_count=persona_count,
                latest=latest,
                retry_count=retry_count,
            )
            self.state.queue_projection(
                owner_key=owner_key,
                operation_id=f"reference-workflow:{job_id}:completed",
                event_type="reference_generation_completed",
                payload=final,
            )
            self.state.update_job_status(job_id, "completed")
            with self.state._connect() as conn:
                conn.execute(
                    "UPDATE runs SET status = 'completed', updated_at = strftime('%s','now') "
                    "WHERE run_id = ?",
                    (run_id,),
                )
            return final
        except Exception as exc:
            error_code = (
                "local_resource_missing"
                if isinstance(exc, (ValueError, json.JSONDecodeError, UnicodeDecodeError))
                else "browser_automation_failed"
            )
            failed = self._reference_projection(
                job_id=job_id,
                run_id=run_id,
                status="failed",
                engine=engine,
                mode=mode,
                total_count=total_count,
                completed_count=completed_count,
                reference_count=reference_count,
                persona_count=persona_count,
                latest=latest,
                retry_count=retry_count,
                error_code=error_code,
            )
            self.state.queue_projection(
                owner_key=owner_key,
                operation_id=f"reference-workflow:{job_id}:failed:{completed_count}",
                event_type="reference_generation_failed",
                payload=failed,
            )
            self.state.update_job_status(job_id, "failed")
            return failed
