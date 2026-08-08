from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import requests
from cryptography.fernet import Fernet

from .storage import AgentPaths, AgentState, ResourceVersion


_PROVIDERS = frozenset({"opencode", "google_gemini", "fake"})
_SECRET_FIELDS = frozenset({"api_key", "client_secret"})
_LANGUAGES = {
    "EN": ("EN",),
    "HI": ("HI",),
    "HINGLISH": ("HINGLISH",),
    "ALL": ("EN", "HI", "HINGLISH"),
    "BOTH": ("EN", "HI", "HINGLISH"),
}


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _safe_owner(owner_key: str) -> str:
    return hashlib.sha256(owner_key.encode()).hexdigest()[:32]


class LocalProviderStore:
    """Encrypted provider credentials rooted entirely in the local agent data directory."""

    def __init__(self, paths: AgentPaths) -> None:
        self.paths = paths
        self.root = paths.config / "providers"
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.key_path = paths.config / "provider-secrets.key"

    def _fernet(self) -> Fernet:
        if not self.key_path.exists():
            temporary = self.key_path.with_name(f".{self.key_path.name}.{uuid.uuid4().hex}.tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.key_path)
            finally:
                temporary.unlink(missing_ok=True)
        os.chmod(self.key_path, 0o600)
        return Fernet(self.key_path.read_bytes())

    def secret_path(self, owner_key: str, provider: str) -> Path:
        if provider not in _PROVIDERS:
            raise ValueError("Unsupported provider")
        return self.root / f"{_safe_owner(owner_key)}-{provider}.secret"

    @staticmethod
    def _metadata(provider: str, config: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider": provider,
            "api_url": str(config.get("api_url") or "")[:256],
            "default_model": str(config.get("default_model") or "")[:128],
            "has_secret": any(bool(config.get(field)) for field in _SECRET_FIELDS),
        }

    def set(self, owner_key: str, provider: str, config: dict[str, Any]) -> dict[str, Any]:
        if provider not in _PROVIDERS or not isinstance(config, dict):
            raise ValueError("Invalid provider config")
        allowed = {"api_url", "api_key", "client_secret", "default_model"}
        clean = {
            key: str(value)
            for key, value in config.items()
            if key in allowed and isinstance(value, str) and len(value) <= 4096
        }
        existing = self.get(owner_key, provider, required=False) or {}
        for field in _SECRET_FIELDS:
            if not clean.get(field) and existing.get(field):
                clean[field] = existing[field]
        payload = json.dumps(clean, ensure_ascii=False, sort_keys=True).encode()
        encrypted = self._fernet().encrypt(payload)
        destination = self.secret_path(owner_key, provider)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            os.chmod(destination, 0o600)
        finally:
            temporary.unlink(missing_ok=True)
        return self._metadata(provider, clean)

    def get(
        self, owner_key: str, provider: str, *, required: bool = True
    ) -> dict[str, Any] | None:
        path = self.secret_path(owner_key, provider)
        if not path.exists():
            if required:
                raise ValueError("Provider config is not available on this device")
            return None
        os.chmod(path, 0o600)
        value = json.loads(self._fernet().decrypt(path.read_bytes()).decode())
        if not isinstance(value, dict):
            raise ValueError("Provider config is invalid")
        return value

    def metadata(self, owner_key: str, provider: str) -> dict[str, Any] | None:
        config = self.get(owner_key, provider, required=False)
        return self._metadata(provider, config) if config is not None else None

    def list_metadata(self, owner_key: str) -> list[dict[str, Any]]:
        return [
            metadata
            for provider in ("opencode", "google_gemini")
            if (metadata := self.metadata(owner_key, provider)) is not None
        ]

    def delete(self, owner_key: str, provider: str) -> None:
        self.secret_path(owner_key, provider).unlink(missing_ok=True)


@dataclass(frozen=True)
class ProviderResult:
    response: dict[str, Any]
    raw_response: Any
    input_tokens: int = 0
    output_tokens: int = 0


class CopyProvider(Protocol):
    name: str
    model: str

    def generate(self, request: dict[str, Any], *, repair: bool = False) -> ProviderResult:
        ...


class ProviderRequestError(RuntimeError):
    code = "provider_request_failed"

    def __init__(self) -> None:
        super().__init__("Provider request failed")


class DeterministicFakeProvider:
    name = "fake"
    model = "fake-copy-v1"

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def generate(self, request: dict[str, Any], *, repair: bool = False) -> ProviderResult:
        index = self.calls
        self.calls += 1
        outcome = self.outcomes[min(index, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        cloned = json.loads(json.dumps(outcome, ensure_ascii=False))
        return ProviderResult(
            response=cloned,
            raw_response=cloned,
            input_tokens=max(1, len(json.dumps(request)) // 4),
            output_tokens=max(1, len(json.dumps(cloned)) // 4),
        )


class HTTPStructuredProvider:
    def __init__(self, name: str, model: str, config: dict[str, Any]) -> None:
        self.name = name
        self.model = model
        self.config = config

    @staticmethod
    def _json_from_text(text: str) -> dict[str, Any]:
        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json.loads(clean)
        if not isinstance(parsed, dict):
            raise ValueError("Provider response is not a JSON object")
        return parsed

    def generate(self, request: dict[str, Any], *, repair: bool = False) -> ProviderResult:
        api_key = str(self.config.get("api_key") or "")
        if not api_key:
            raise ValueError("Provider credential is unavailable")
        if self.name == "google_gemini":
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.model}:generateContent"
            )
            body = {
                "contents": [{"role": "user", "parts": [{"text": json.dumps(request, ensure_ascii=False)}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3 if repair else 0.7},
            }
            try:
                response = requests.post(
                    url,
                    headers={"x-goog-api-key": api_key},
                    json=body,
                    timeout=(10, 120),
                )
                response.raise_for_status()
            except requests.RequestException as exc:
                raise ProviderRequestError() from exc
            raw = response.json()
            text = raw["candidates"][0]["content"]["parts"][0]["text"]
            usage = raw.get("usageMetadata") or {}
            return ProviderResult(
                self._json_from_text(text),
                raw,
                int(usage.get("promptTokenCount") or 0),
                int(usage.get("candidatesTokenCount") or 0),
            )
        api_url = str(self.config.get("api_url") or "").rstrip("/")
        if not api_url.startswith(("http://", "https://")):
            raise ValueError("OpenCode API URL is invalid")
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": json.dumps(request, ensure_ascii=False)}],
            "response_format": {"type": "json_object"},
            "temperature": 0.3 if repair else 0.7,
        }
        try:
            response = requests.post(
                f"{api_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
                timeout=(10, 120),
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ProviderRequestError() from exc
        raw = response.json()
        text = raw["choices"][0]["message"]["content"]
        usage = raw.get("usage") or {}
        return ProviderResult(
            self._json_from_text(text),
            raw,
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
        )


class StructuredCopyExecutor:
    def __init__(
        self,
        state: AgentState,
        *,
        provider: CopyProvider | None = None,
        provider_store: LocalProviderStore | None = None,
    ) -> None:
        self.state = state
        self.provider = provider
        self.provider_store = provider_store or LocalProviderStore(state.paths)

    def _completed_result(self, job_id: str) -> dict[str, Any] | None:
        with self.state._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json FROM outbox
                WHERE event_type = 'structured_copy_completed'
                ORDER BY created_at DESC
                """
            ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            if payload.get("job_id") == job_id:
                return payload
        return None

    @staticmethod
    def _entry(context: dict[str, Any], role: str) -> dict[str, Any]:
        matches = [entry for entry in context["entries"] if entry.get("role") == role]
        if len(matches) != 1:
            raise ValueError(f"Local run requires exactly one {role} resource")
        return matches[0]

    @staticmethod
    def _read_json(entry: dict[str, Any]) -> dict[str, Any]:
        value = json.loads(Path(entry["local_path"]).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Local config resource must contain a JSON object")
        return value

    def _validate_selected_assets(
        self, owner_key: str, selected: list[dict[str, Any]]
    ) -> list[tuple[str, int]]:
        resolved: list[tuple[str, int]] = []
        with self.state._connect() as conn:
            for item in selected:
                resource_id = str(item.get("resource_id") or "")
                version = int(item.get("version") or 0)
                row = conn.execute(
                    """
                    SELECT 1 FROM resources r JOIN resource_versions rv
                      ON rv.resource_id = r.resource_id
                    WHERE r.resource_id = ? AND r.owner_key = ? AND r.kind = 'product_image'
                      AND rv.version = ? AND r.deleted_at IS NULL
                    """,
                    (resource_id, owner_key, version),
                ).fetchone()
                if row is None:
                    raise ValueError("Selected local product asset is unavailable")
                resolved.append((resource_id, version))
        return resolved

    @staticmethod
    def _normalize(response: dict[str, Any], planned: list[dict[str, Any]]) -> dict[str, Any]:
        candidates = response.get("ads") if isinstance(response.get("ads"), list) else []
        normalized = []
        for index, plan in enumerate(planned):
            candidate = candidates[index] if index < len(candidates) and isinstance(candidates[index], dict) else {}
            copy_value = candidate.get("copy") if isinstance(candidate.get("copy"), dict) else {}
            normalized.append(
                {
                    **{key: value for key, value in plan.items() if key != "copy"},
                    "format": str(plan.get("format") or "").upper(),
                    "concept_angle": str(
                        candidate.get("concept_angle") or plan.get("concept_angle") or "desired_outcome"
                    ),
                    "copy": copy_value,
                }
            )
        return {"default_aspect_ratio": "4:5", "ads": normalized}

    @staticmethod
    def _validation_error(copy_batch: dict[str, Any], languages: tuple[str, ...]) -> str | None:
        ads = copy_batch.get("ads")
        if not isinstance(ads, list) or not ads:
            return "ads_missing"
        for ad in ads:
            fmt = str(ad.get("format") or "")
            blocks = ad.get("copy") if isinstance(ad.get("copy"), dict) else {}
            for language in languages:
                block = blocks.get(language) if isinstance(blocks.get(language), dict) else {}
                if not str(block.get("headline") or "").strip() or not str(block.get("cta") or "").strip():
                    return "required_copy_missing"
                if fmt in {"HERO", "UGC"} and not str(
                    block.get("support_line") or block.get("subheadline") or ""
                ).strip():
                    return "support_line_missing"
                if fmt in {"BA", "FEAT"}:
                    minimum = 4 if fmt == "BA" else 2
                    bullets = [item for item in block.get("bullets", []) if isinstance(item, str) and item.strip()]
                    if len(bullets) < minimum:
                        return "bullets_missing"
                if fmt == "TEST" and not str(block.get("trust_line") or "").strip():
                    return "trust_line_missing"
        return None

    def _put_json(
        self,
        *,
        owner_key: str,
        kind: str,
        logical_key: str,
        operation_id: str,
        value: Any,
        metadata: dict[str, Any],
    ) -> ResourceVersion:
        temporary = self.state.paths.staging / f".structured-{uuid.uuid4().hex}.json"
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            return self.state.put_resource(
                source=temporary,
                owner_key=owner_key,
                kind=kind,
                logical_key=logical_key,
                operation_id=operation_id,
                metadata=metadata,
                media_type="application/json",
            )
        finally:
            temporary.unlink(missing_ok=True)

    def _put_text(
        self,
        *,
        owner_key: str,
        logical_key: str,
        operation_id: str,
        value: str,
        metadata: dict[str, Any],
    ) -> ResourceVersion:
        temporary = self.state.paths.staging / f".structured-{uuid.uuid4().hex}.txt"
        temporary.write_text(value, encoding="utf-8")
        try:
            return self.state.put_resource(
                source=temporary,
                owner_key=owner_key,
                kind="prompt",
                logical_key=logical_key,
                operation_id=operation_id,
                metadata=metadata,
                media_type="text/plain; charset=utf-8",
            )
        finally:
            temporary.unlink(missing_ok=True)

    def _next_position(self, run_id: str) -> int:
        with self.state._connect() as conn:
            return int(
                conn.execute(
                    "SELECT COALESCE(MAX(position), 0) + 1 FROM run_entries WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
            )

    def _add_entry(
        self,
        *,
        run_id: str,
        job_id: str,
        resource: ResourceVersion,
        role: str,
        index: int,
        prompt_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.state.add_run_entry(
            run_id=run_id,
            entry_id=f"ent_{hashlib.sha256(f'{job_id}:{role}:{index}'.encode()).hexdigest()[:24]}",
            resource_id=resource.resource_id,
            resource_version=resource.version,
            role=role,
            prompt_id=prompt_id,
            position=self._next_position(run_id),
            operation_id=f"structured-copy:{job_id}:entry:{role}:{index}",
            metadata=metadata,
        )

    def _provider_for(self, owner_key: str, execution: dict[str, Any]) -> CopyProvider:
        if self.provider is not None:
            return self.provider
        name = str(execution.get("provider") or "").strip().lower()
        if name == "google":
            name = "google_gemini"
        if name == "fake":
            raise ValueError("Fake provider must be injected by deterministic tests")
        config = self.provider_store.get(owner_key, name)
        model = str(execution.get("model") or config.get("default_model") or "").strip()
        if not model:
            raise ValueError("Provider model is unavailable")
        return HTTPStructuredProvider(name, model, config)

    def execute(self, job_id: str) -> dict[str, Any]:
        completed = self._completed_result(job_id)
        if completed is not None:
            return completed
        started = time.monotonic()
        context = self.state.resolve_job_context(job_id)
        run_id = str(context["run"]["run_id"])
        owner_key = str(context["owner_key"])
        trace_request: dict[str, Any] = {}
        trace_response: Any = None
        provider_name = "unknown"
        model = "unknown"
        input_tokens = 0
        output_tokens = 0
        repair_count = 0
        try:
            if context["payload"].get("command") != "generate_copy":
                raise ValueError("Local structured copy command is invalid")
            settings_entry = self._entry(context, "structured_settings")
            backgrounds_entry = self._entry(context, "backgrounds")
            product_entry = self._entry(context, "product_document")
            settings = self._read_json(settings_entry)
            backgrounds = self._read_json(backgrounds_entry)
            execution = settings.get("execution") if isinstance(settings.get("execution"), dict) else {}
            planned = settings.get("planned_ads") if isinstance(settings.get("planned_ads"), list) else []
            if not planned:
                raise ValueError("Local structured ad plan is missing")
            templates = settings.get("prompt_assembler_templates")
            if not isinstance(templates, dict):
                raise ValueError("Local prompt assembler config is missing")
            selected_assets = self._validate_selected_assets(
                owner_key,
                settings.get("product_assets") if isinstance(settings.get("product_assets"), list) else [],
            )
            product_document = Path(product_entry["local_path"]).read_text(encoding="utf-8")
            languages = _LANGUAGES.get(
                str(execution.get("language_mode") or "EN").upper(), ("EN",)
            )
            provider = self._provider_for(owner_key, execution)
            provider_name = provider.name
            model = provider.model
            trace_request = {
                "task": "Generate structured advertising copy as JSON",
                "product_document": product_document,
                "planned_ads": planned,
                "languages": list(languages),
                "requirements": {
                    "json_only": True,
                    "preserve_persona_and_format": True,
                    "no_unverified_claims": True,
                },
            }
            result = provider.generate(trace_request)
            trace_response = result.raw_response
            input_tokens += result.input_tokens
            output_tokens += result.output_tokens
            copy_batch = self._normalize(result.response, planned)
            validation_error = self._validation_error(copy_batch, languages)
            maximum_repairs = max(0, min(int(execution.get("max_repair_attempts") or 1), 2))
            while validation_error and repair_count < maximum_repairs:
                repair_count += 1
                repair_request = {
                    "task": "Repair structured copy validation errors and return JSON only",
                    "validation_error": validation_error,
                    "original_request": trace_request,
                    "invalid_response": trace_response,
                }
                repaired = provider.generate(repair_request, repair=True)
                trace_response = repaired.raw_response
                input_tokens += repaired.input_tokens
                output_tokens += repaired.output_tokens
                copy_batch = self._normalize(repaired.response, planned)
                validation_error = self._validation_error(copy_batch, languages)
            if validation_error:
                raise ValueError("Structured copy validation failed")

            trace = {
                "provider": provider_name,
                "model": model,
                "request": trace_request,
                "response": trace_response,
                "status": "completed",
                "repair_count": repair_count,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }
            trace_resource = self._put_json(
                owner_key=owner_key,
                kind="trace",
                logical_key=f"{run_id}:{job_id}:trace:completed",
                operation_id=f"structured-copy:{job_id}:trace:completed",
                value=trace,
                metadata={"run_id": run_id, "job_id": job_id, "status": "completed"},
            )
            copy_resource = self._put_json(
                owner_key=owner_key,
                kind="copy_batch",
                logical_key=f"{run_id}:{job_id}:copy",
                operation_id=f"structured-copy:{job_id}:copy",
                value=copy_batch,
                metadata={"run_id": run_id, "job_id": job_id, "count": len(copy_batch["ads"])},
            )
            self._add_entry(
                run_id=run_id, job_id=job_id, resource=trace_resource, role="trace", index=0
            )
            self._add_entry(
                run_id=run_id, job_id=job_id, resource=copy_resource, role="copy_batch", index=0
            )

            from scripts import generate_ads

            prompt_count = 0
            prompt_ids: list[str] = []
            prompt_resource_ids: list[str] = []
            seed = int(execution.get("seed") or 1)
            for ad_index, ad in enumerate(copy_batch["ads"], start=1):
                fmt = str(ad["format"]).upper()
                persona = ad["persona"]
                concept = generate_ads.resolve_concept_fields(ad, fmt, persona)
                background = generate_ads.pick_background_slot(
                    backgrounds, fmt, seed + ad_index - 1
                )
                background_seed = random.Random(seed + ad_index * 101).randint(
                    1, 2_147_483_647
                )
                sentence = generate_ads.build_seeded_background_sentence(
                    background, background_seed, "4:5"
                )
                archetype = generate_ads.default_visual_archetype(fmt)
                for language in languages:
                    block = generate_ads.parse_copy_block(fmt, language, ad["copy"][language])
                    prompt_text = generate_ads.render_prompt(
                        fmt,
                        language,
                        "4:5",
                        persona,
                        block,
                        concept,
                        background,
                        background_seed,
                        sentence,
                        archetype,
                        templates=templates,
                    )
                    prompt_id = "prm_" + hashlib.sha256(
                        f"{run_id}:{ad_index}:{language}".encode()
                    ).hexdigest()[:24]
                    sidecar = {
                        "prompt_id": prompt_id,
                        "format": fmt,
                        "persona_number": int(persona["number"]),
                        "persona_name": str(persona["name"]),
                        "language": language,
                        "aspect_ratio": "4:5",
                        "background_id": background["id"],
                        "background_seed": background_seed,
                        "copy_resource_id": copy_resource.resource_id,
                        "copy_resource_version": copy_resource.version,
                        "settings_resource_id": settings_entry["resource_id"],
                        "settings_resource_version": int(settings_entry["resource_version"]),
                        "product_document_resource_id": product_entry["resource_id"],
                        "product_document_version": int(product_entry["resource_version"]),
                        "selected_asset_ids": [item[0] for item in selected_assets],
                    }
                    prompt_resource = self._put_text(
                        owner_key=owner_key,
                        logical_key=prompt_id,
                        operation_id=f"structured-copy:{job_id}:prompt:{prompt_count}",
                        value=prompt_text,
                        metadata={
                            key: sidecar[key]
                            for key in ("prompt_id", "format", "persona_number", "language", "aspect_ratio")
                        },
                    )
                    sidecar_resource = self._put_json(
                        owner_key=owner_key,
                        kind="prompt_sidecar",
                        logical_key=f"{prompt_id}:sidecar",
                        operation_id=f"structured-copy:{job_id}:sidecar:{prompt_count}",
                        value=sidecar,
                        metadata={"run_id": run_id, "prompt_id": prompt_id},
                    )
                    self._add_entry(
                        run_id=run_id,
                        job_id=job_id,
                        resource=prompt_resource,
                        role="prompt",
                        index=prompt_count,
                        prompt_id=prompt_id,
                    )
                    self._add_entry(
                        run_id=run_id,
                        job_id=job_id,
                        resource=sidecar_resource,
                        role="prompt_sidecar",
                        index=prompt_count,
                        prompt_id=prompt_id,
                    )
                    prompt_ids.append(prompt_id)
                    prompt_resource_ids.append(prompt_resource.resource_id)
                    prompt_count += 1

            duration_ms = int((time.monotonic() - started) * 1000)
            projection = {
                "job_id": job_id,
                "run_id": run_id,
                "status": "completed",
                "provider": provider_name,
                "model": model,
                "duration_ms": duration_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "request_sha256": _sha256_json(trace_request),
                "response_sha256": _sha256_json(trace_response),
                "copy_sha256": copy_resource.object_sha256,
                "copy_count": len(copy_batch["ads"]),
                "prompt_count": prompt_count,
                "prompt_ids": prompt_ids,
                "prompt_resource_ids": prompt_resource_ids,
                "asset_count": len(selected_assets),
                "repair_count": repair_count,
                "copy_resource_id": copy_resource.resource_id,
                "copy_resource_version": copy_resource.version,
                "trace_resource_id": trace_resource.resource_id,
                "trace_resource_version": trace_resource.version,
                "settings_resource_id": settings_entry["resource_id"],
                "settings_resource_version": int(settings_entry["resource_version"]),
                "product_document_resource_id": product_entry["resource_id"],
                "product_document_version": int(product_entry["resource_version"]),
            }
            self.state.queue_projection(
                owner_key=owner_key,
                operation_id=f"structured-copy:{job_id}:completed",
                event_type="structured_copy_completed",
                payload=projection,
            )
            self.state.update_job_status(job_id, "completed")
            return projection
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            trace = {
                "provider": provider_name,
                "model": model,
                "request": trace_request,
                "response": trace_response,
                "status": "failed",
                "error_type": type(exc).__name__,
                "error_code": str(
                    getattr(
                        exc,
                        "code",
                        "local_validation_failed"
                        if isinstance(exc, ValueError)
                        else "provider_failed",
                    )
                ),
            }
            trace_resource = self._put_json(
                owner_key=owner_key,
                kind="trace",
                logical_key=f"{run_id}:{job_id}:trace:failed:{uuid.uuid4().hex}",
                operation_id=f"structured-copy:{job_id}:trace:failed:{uuid.uuid4().hex}",
                value=trace,
                metadata={"run_id": run_id, "job_id": job_id, "status": "failed"},
            )
            self._add_entry(
                run_id=run_id,
                job_id=job_id,
                resource=trace_resource,
                role="trace",
                index=-1,
            )
            projection = {
                "job_id": job_id,
                "run_id": run_id,
                "status": "failed",
                "provider": provider_name,
                "model": model,
                "duration_ms": duration_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "request_sha256": _sha256_json(trace_request),
                "response_sha256": _sha256_json(trace_response),
                "repair_count": repair_count,
                "trace_resource_id": trace_resource.resource_id,
                "trace_resource_version": trace_resource.version,
                "error_code": "provider_failed"
                if not isinstance(exc, ValueError)
                else "local_validation_failed",
            }
            self.state.queue_projection(
                owner_key=owner_key,
                operation_id=f"structured-copy:{job_id}:failed:{trace_resource.resource_id}",
                event_type="structured_copy_failed",
                payload=projection,
            )
            self.state.update_job_status(job_id, "failed")
            return projection
