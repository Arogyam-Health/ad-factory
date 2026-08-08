from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

if os.name == "nt":
    import msvcrt
else:
    import fcntl


SCHEMA_VERSION = 2


class SchemaMigrationError(RuntimeError):
    pass


class VersionConflictError(RuntimeError):
    pass


def resolve_data_root(
    cli_value: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    configured = str(cli_value or env.get("AGENT_DATA_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    home_dir = (home or Path.home()).expanduser().resolve()
    return home_dir / "ad-factory-agent"


def artifact_access_token(paths: "AgentPaths", owner_key: str) -> str:
    """Derive an owner-scoped capability from a root-local secret."""
    paths.ensure()
    secret_path = paths.config / "artifact-secret"
    if not secret_path.exists():
        secret = os.urandom(32).hex()
        try:
            descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write(secret + "\n")
    secret = secret_path.read_text(encoding="ascii").strip()
    return hmac.new(secret.encode("ascii"), owner_key.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class AgentPaths:
    root: Path

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def database(self) -> Path:
        return self.state / "agent.sqlite3"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def objects(self) -> Path:
        return self.root / "objects" / "sha256"

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def browser(self) -> Path:
        return self.root / "browser"

    @property
    def locks(self) -> Path:
        return self.root / "locks"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def legacy(self) -> Path:
        return self.root / "legacy" / "unassigned"

    def ensure(self) -> None:
        for path in (
            self.state,
            self.artifacts,
            self.objects,
            self.staging,
            self.logs,
            self.browser,
            self.locks,
            self.config,
            self.legacy,
        ):
            path.mkdir(parents=True, exist_ok=True)
        version_path = self.state / "schema-version.json"
        if not version_path.exists():
            _atomic_write_text(version_path, json.dumps({"schema_version": 0}) + "\n")


@dataclass(frozen=True)
class ContentObject:
    sha256: str
    path: Path
    bytes: int


@dataclass(frozen=True)
class ResourceVersion:
    resource_id: str
    version: int
    object_sha256: str
    content_hash: str
    path: Path
    bytes: int
    media_type: str


class ContentStore:
    def __init__(self, paths: AgentPaths) -> None:
        self.paths = paths
        self.paths.ensure()

    def put_file(self, source: Path) -> ContentObject:
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        sha256 = digest.hexdigest()
        target_dir = self.paths.objects / sha256[:2]
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{sha256}.blob"
        if not target.exists():
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            shutil.copyfile(source, temporary)
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
            finally:
                temporary.unlink(missing_ok=True)
        return ContentObject(sha256=sha256, path=target, bytes=size)


class LockHeldError(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, paths: AgentPaths) -> None:
        self.paths = paths
        self._handle = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.paths.ensure()
        lock_path = self.paths.locks / "agent.lock"
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            if os.name == "nt":
                if lock_path.stat().st_size == 0:
                    handle.write("0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            handle.seek(0)
            details = handle.read().strip()
            handle.close()
            suffix = f" ({details})" if details else ""
            raise LockHeldError(f"Another local agent owns {self.paths.root}{suffix}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid(), "root": str(self.paths.root), "acquired_at": time.time()}))
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        if os.name == "nt":
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


@dataclass(frozen=True)
class PublishedArtifact:
    artifact_id: str
    path: Path
    sha256: str


class AgentState:
    def __init__(self, paths: AgentPaths) -> None:
        self.paths = paths
        self.paths.ensure()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.paths.database, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _initialize(self) -> None:
        migrate_database(self.paths)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                "UPDATE revisions SET status = 'queued', error = NULL, updated_at = ? WHERE status = 'running'",
                (time.time(),),
            )

    def publish_artifact(
        self,
        *,
        source: Path,
        owner_key: str,
        run_id: str,
        run_number: int,
        job_id: str,
        item_id: str,
        prompt_id: str,
        aspect_ratio: str,
        filename: str,
    ) -> PublishedArtifact:
        safe_owner = _safe_component(owner_key)
        safe_run = _safe_component(run_id)
        safe_prompt = _safe_component(prompt_id)
        safe_aspect = _safe_component(aspect_ratio.replace(":", "_"))
        safe_filename = _safe_filename(filename)
        identity = "\0".join((owner_key, run_id, prompt_id, aspect_ratio, item_id, safe_filename))
        artifact_id = "art_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        destination = (
            self.paths.artifacts
            / "owners"
            / safe_owner
            / "runs"
            / f"{int(run_number):06d}-{safe_run}"
            / "prompts"
            / safe_prompt
            / safe_aspect
            / safe_filename
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        relative_path = destination.relative_to(self.paths.root).as_posix()
        source_digest = _sha256_file(source)
        source_size = source.stat().st_size
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT relative_path, sha256, bytes FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        if (
            existing is not None
            and existing["relative_path"] == relative_path
            and existing["sha256"] == source_digest
            and int(existing["bytes"]) == source_size
            and destination.is_file()
            and destination.stat().st_size == source_size
        ):
            return PublishedArtifact(artifact_id=artifact_id, path=destination, sha256=source_digest)
        source_resolved = source.resolve()
        destination_resolved = destination.resolve()
        if source_resolved != destination_resolved:
            temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, owner_key, run_id, run_number, job_id, item_id,
                    prompt_id, aspect_ratio, filename, relative_path, sha256,
                    bytes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    relative_path = excluded.relative_path,
                    sha256 = excluded.sha256,
                    bytes = excluded.bytes,
                    updated_at = excluded.updated_at
                """,
                (
                    artifact_id,
                    owner_key,
                    run_id,
                    int(run_number),
                    job_id,
                    item_id,
                    prompt_id,
                    aspect_ratio,
                    safe_filename,
                    relative_path,
                    source_digest,
                    source_size,
                    now,
                    now,
                ),
            )
            conn.execute("UPDATE metadata SET value = value + 1 WHERE key = 'change_sequence'")
        return PublishedArtifact(artifact_id=artifact_id, path=destination, sha256=source_digest)

    def record_job(self, job_id: str, owner_key: str, status: str, payload: dict[str, Any]) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs(job_id, owner_key, status, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status = excluded.status,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (job_id, owner_key, status, json.dumps(payload, ensure_ascii=True), now, now),
            )

    def update_job_status(self, job_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE job_id = ?",
                (status, time.time(), job_id),
            )

    def queue_outbox(self, event_type: str, payload: dict[str, Any]) -> str:
        event_id = "evt_" + uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO outbox(event_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (event_id, event_type, json.dumps(payload, ensure_ascii=True), time.time()),
            )
        return event_id

    def record_terminal_outbox(
        self,
        job_id: str,
        status: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> str:
        event_id = "evt_" + uuid.uuid4().hex
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE job_id = ?",
                (status, now, job_id),
            )
            conn.execute(
                "INSERT INTO outbox(event_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (event_id, event_type, json.dumps(payload, ensure_ascii=True), now),
            )
        return event_id

    def pending_outbox(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM outbox WHERE delivered_at IS NULL ORDER BY created_at LIMIT ?",
                (limit,),
            ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["payload"] = json.loads(event.pop("payload_json"))
            events.append(event)
        return events

    def mark_outbox_delivered(self, event_id: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE outbox SET delivered_at = ? WHERE event_id = ?", (time.time(), event_id))

    def manifest(
        self,
        artifact_base_url: str = "http://127.0.0.1:8765",
        *,
        job_id: str | None = None,
        owner_key: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            if job_id:
                rows = conn.execute(
                    "SELECT * FROM artifacts WHERE job_id = ? ORDER BY updated_at DESC",
                    (job_id,),
                ).fetchall()
            elif owner_key:
                rows = conn.execute(
                    "SELECT * FROM artifacts WHERE owner_key = ? ORDER BY updated_at DESC",
                    (owner_key,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM artifacts ORDER BY updated_at DESC").fetchall()
        images = []
        for row in rows:
            item = dict(row)
            item["path"] = item.pop("relative_path")
            item["url"] = f"{artifact_base_url.rstrip('/')}/files/{item['artifact_id']}"
            item["run_ids"] = [item["run_id"]]
            item["batch"] = f"v{item['run_number']}"
            images.append(item)
        return {
            "schema_version": 3,
            "data_root": str(self.paths.root),
            "local_output_dir": str(self.paths.artifacts),
            "artifact_base_url": artifact_base_url.rstrip("/"),
            "images": images,
        }

    def artifact_path(self, artifact_id: str) -> Path | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT relative_path FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            return None
        path = (self.paths.root / row["relative_path"]).resolve()
        try:
            path.relative_to(self.paths.root.resolve())
        except ValueError:
            return None
        return path

    def artifact_record(self, artifact_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        return dict(row) if row is not None else None

    def queue_revision(self, artifact_id: str, comment: str, engine: str) -> dict[str, Any]:
        if self.artifact_path(artifact_id) is None:
            raise ValueError("Artifact not found")
        comment = str(comment).strip()
        if not comment:
            raise ValueError("Revision comment is required")
        if engine not in {"chatgpt", "gemini"}:
            raise ValueError("Revision engine must be chatgpt or gemini")
        revision_id = "rev_" + uuid.uuid4().hex[:24]
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO revisions(
                    revision_id, artifact_id, comment, engine, status, attempt,
                    error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', 1, NULL, ?, ?)
                """,
                (revision_id, artifact_id, comment, engine, now, now),
            )
            conn.execute("UPDATE metadata SET value = value + 1 WHERE key = 'change_sequence'")
        return self.revision(revision_id) or {}

    def revision(self, revision_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM revisions WHERE revision_id = ?", (revision_id,)).fetchone()
        return dict(row) if row is not None else None

    def claim_next_revision(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM revisions WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            now = time.time()
            conn.execute(
                "UPDATE revisions SET status = 'running', updated_at = ? WHERE revision_id = ? AND status = 'queued'",
                (now, row["revision_id"]),
            )
            conn.commit()
        return self.revision(str(row["revision_id"]))

    def finish_revision(self, revision_id: str, *, error: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE revisions SET status = ?, error = ?, updated_at = ? WHERE revision_id = ?",
                ("error" if error else "completed", error or None, time.time(), revision_id),
            )
            conn.execute("UPDATE metadata SET value = value + 1 WHERE key = 'change_sequence'")

    def delete_artifact(self, artifact_id: str) -> bool:
        path = self.artifact_path(artifact_id)
        if path is None:
            return False
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM artifacts WHERE artifact_id = ?", (artifact_id,))
            if cursor.rowcount > 0:
                conn.execute("UPDATE metadata SET value = value + 1 WHERE key = 'change_sequence'")
        if cursor.rowcount <= 0:
            return False
        path.unlink(missing_ok=True)
        return True

    def refresh_artifact(self, artifact_id: str) -> None:
        path = self.artifact_path(artifact_id)
        if path is None or not path.is_file():
            raise ValueError("Artifact not found")
        with self._connect() as conn:
            conn.execute(
                "UPDATE artifacts SET sha256 = ?, bytes = ?, updated_at = ? WHERE artifact_id = ?",
                (_sha256_file(path), path.stat().st_size, time.time(), artifact_id),
            )
            conn.execute("UPDATE metadata SET value = value + 1 WHERE key = 'change_sequence'")

    def change_sequence(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(sequence) AS value FROM change_log").fetchone()
            legacy = conn.execute("SELECT value FROM metadata WHERE key = 'change_sequence'").fetchone()
        return max(int(row["value"] or 0), int(legacy["value"] if legacy is not None else 0))

    def _operation_result(
        self,
        conn: sqlite3.Connection,
        owner_key: str,
        operation_id: str,
    ) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT result_json FROM operations WHERE owner_key = ? AND operation_id = ?",
            (owner_key, operation_id),
        ).fetchone()
        return json.loads(row["result_json"]) if row is not None else None

    def _save_operation(
        self,
        conn: sqlite3.Connection,
        owner_key: str,
        operation_id: str,
        operation_type: str,
        result: dict[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO operations(owner_key, operation_id, operation_type, result_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (owner_key, operation_id, operation_type, json.dumps(result, sort_keys=True), time.time()),
        )

    def _record_change(
        self,
        conn: sqlite3.Connection,
        *,
        owner_key: str,
        resource_type: str,
        resource_id: str,
        version: int | None,
        operation: str,
    ) -> int:
        cursor = conn.execute(
            """
            INSERT INTO change_log(owner_key, resource_type, resource_id, version, operation, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (owner_key, resource_type, resource_id, version, operation, time.time()),
        )
        sequence = int(cursor.lastrowid)
        conn.execute(
            "UPDATE metadata SET value = MAX(value, ?) WHERE key = 'change_sequence'",
            (sequence,),
        )
        return sequence

    def _resource_version_from_result(self, result: dict[str, Any]) -> ResourceVersion:
        return ResourceVersion(
            resource_id=str(result["resource_id"]),
            version=int(result["version"]),
            object_sha256=str(result["object_sha256"]),
            content_hash=str(result["content_hash"]),
            path=self.paths.root / str(result["relative_path"]),
            bytes=int(result["bytes"]),
            media_type=str(result["media_type"]),
        )

    def put_resource(
        self,
        *,
        source: Path,
        owner_key: str,
        kind: str,
        logical_key: str,
        operation_id: str,
        resource_id: str | None = None,
        expected_version: int | None = None,
        metadata: dict[str, Any] | None = None,
        media_type: str = "application/octet-stream",
    ) -> ResourceVersion:
        if not operation_id:
            raise ValueError("operation_id is required")
        with self._connect() as conn:
            prior = self._operation_result(conn, owner_key, operation_id)
        if prior is not None:
            return self._resource_version_from_result(prior)

        existing_row = None
        with self._connect() as conn:
            if resource_id:
                existing_row = conn.execute(
                    "SELECT * FROM resources WHERE resource_id = ? AND owner_key = ?",
                    (resource_id, owner_key),
                ).fetchone()
            else:
                existing_row = conn.execute(
                    """
                    SELECT * FROM resources
                    WHERE owner_key = ? AND kind = ? AND logical_key = ? AND deleted_at IS NULL
                    """,
                    (owner_key, kind, logical_key),
                ).fetchone()
            current_version = int(existing_row["current_version"]) if existing_row is not None else 0
            if expected_version is not None and expected_version != current_version:
                raise VersionConflictError(
                    f"Expected resource version {expected_version}, found {current_version}"
                )
            if resource_id and existing_row is None:
                raise ValueError("Resource not found")

        content = ContentStore(self.paths).put_file(source)
        relative_path = content.path.relative_to(self.paths.root).as_posix()
        now = time.time()
        resolved_resource_id = (
            str(existing_row["resource_id"])
            if existing_row is not None
            else resource_id or "res_" + uuid.uuid4().hex
        )
        version = current_version + 1
        result = {
            "resource_id": resolved_resource_id,
            "version": version,
            "object_sha256": content.sha256,
            "content_hash": content.sha256,
            "relative_path": relative_path,
            "bytes": content.bytes,
            "media_type": media_type,
        }
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = self._operation_result(conn, owner_key, operation_id)
            if prior is not None:
                conn.commit()
                return self._resource_version_from_result(prior)
            latest = conn.execute(
                "SELECT current_version FROM resources WHERE resource_id = ?",
                (resolved_resource_id,),
            ).fetchone()
            actual_version = int(latest["current_version"]) if latest is not None else 0
            if actual_version != current_version:
                conn.rollback()
                raise VersionConflictError(
                    f"Expected resource version {current_version}, found {actual_version}"
                )
            conn.execute(
                """
                INSERT OR IGNORE INTO objects(
                    sha256, relative_path, bytes, media_type, created_at, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (content.sha256, relative_path, content.bytes, media_type, now, now),
            )
            if existing_row is None:
                conn.execute(
                    """
                    INSERT INTO resources(
                        resource_id, owner_key, kind, logical_key, current_version,
                        status, created_at, updated_at, deleted_at
                    ) VALUES (?, ?, ?, ?, ?, 'available', ?, ?, NULL)
                    """,
                    (resolved_resource_id, owner_key, kind, logical_key, version, now, now),
                )
            else:
                conn.execute(
                    """
                    UPDATE resources
                    SET current_version = ?, status = 'available', updated_at = ?
                    WHERE resource_id = ?
                    """,
                    (version, now, resolved_resource_id),
                )
            conn.execute(
                """
                INSERT INTO resource_versions(
                    resource_id, version, object_sha256, content_hash, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_resource_id,
                    version,
                    content.sha256,
                    content.sha256,
                    json.dumps(metadata or {}, sort_keys=True),
                    now,
                ),
            )
            self._record_change(
                conn,
                owner_key=owner_key,
                resource_type=kind,
                resource_id=resolved_resource_id,
                version=version,
                operation="created" if version == 1 else "versioned",
            )
            self._save_operation(conn, owner_key, operation_id, "put_resource", result)
            conn.commit()
        return self._resource_version_from_result(result)

    def resource_versions(self, resource_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT rv.*, o.relative_path, o.bytes, o.media_type
                FROM resource_versions rv
                JOIN objects o ON o.sha256 = rv.object_sha256
                WHERE rv.resource_id = ?
                ORDER BY rv.version
                """,
                (resource_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_run(
        self,
        *,
        run_id: str,
        owner_key: str,
        device_id: str,
        workspace_id: str,
        run_number: int,
        flow_type: str,
        operation_id: str,
        display_batch: str | None = None,
        status: str = "created",
        manifest_resource_id: str | None = None,
        manifest_version: int | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            prior = self._operation_result(conn, owner_key, operation_id)
            if prior is not None:
                return prior
            now = time.time()
            conn.execute(
                """
                INSERT INTO runs(
                    run_id, owner_key, device_id, workspace_id, run_number, display_batch,
                    flow_type, status, manifest_resource_id, manifest_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    owner_key,
                    device_id,
                    workspace_id,
                    int(run_number),
                    display_batch or f"v{int(run_number)}",
                    flow_type,
                    status,
                    manifest_resource_id,
                    manifest_version,
                    now,
                    now,
                ),
            )
            result = {"run_id": run_id, "owner_key": owner_key, "status": status}
            self._record_change(
                conn,
                owner_key=owner_key,
                resource_type="run",
                resource_id=run_id,
                version=None,
                operation="created",
            )
            self._save_operation(conn, owner_key, operation_id, "create_run", result)
        return result

    def _run_owner(self, conn: sqlite3.Connection, run_id: str) -> str:
        row = conn.execute("SELECT owner_key FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("Run not found")
        return str(row["owner_key"])

    def add_run_entry(
        self,
        *,
        run_id: str,
        entry_id: str,
        resource_id: str,
        resource_version: int,
        role: str,
        position: int,
        operation_id: str,
        prompt_id: str | None = None,
        item_id: str | None = None,
        aspect_ratio: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            owner_key = self._run_owner(conn, run_id)
            prior = self._operation_result(conn, owner_key, operation_id)
            if prior is not None:
                return prior
            conn.execute(
                """
                INSERT INTO run_entries(
                    run_id, entry_id, resource_id, resource_version, role, prompt_id,
                    item_id, aspect_ratio, position, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    entry_id,
                    resource_id,
                    int(resource_version),
                    role,
                    prompt_id,
                    item_id,
                    aspect_ratio,
                    int(position),
                    json.dumps(metadata or {}, sort_keys=True),
                ),
            )
            result = {"run_id": run_id, "entry_id": entry_id}
            self._record_change(
                conn,
                owner_key=owner_key,
                resource_type="run_entry",
                resource_id=entry_id,
                version=resource_version,
                operation="created",
            )
            self._save_operation(conn, owner_key, operation_id, "add_run_entry", result)
        return result

    def run_manifest(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            run = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                return None
            entries = conn.execute(
                "SELECT * FROM run_entries WHERE run_id = ? ORDER BY position, entry_id",
                (run_id,),
            ).fetchall()
        result = dict(run)
        result["entries"] = [dict(row) for row in entries]
        return result

    def create_upload_set(
        self,
        *,
        upload_set_id: str,
        run_id: str,
        prompt_id: str,
        phase: str,
        version: int,
        entries: list[tuple[str, int, str]],
        operation_id: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            owner_key = self._run_owner(conn, run_id)
            prior = self._operation_result(conn, owner_key, operation_id)
            if prior is not None:
                return prior
            conn.execute(
                "INSERT INTO upload_sets VALUES (?, ?, ?, ?, ?, ?)",
                (upload_set_id, run_id, prompt_id, phase, int(version), time.time()),
            )
            result_entries = []
            for position, (resource_id, resource_version, role) in enumerate(entries, start=1):
                conn.execute(
                    "INSERT INTO upload_set_entries VALUES (?, ?, ?, ?, ?)",
                    (upload_set_id, position, resource_id, int(resource_version), role),
                )
                result_entries.append(
                    {
                        "position": position,
                        "resource_id": resource_id,
                        "resource_version": int(resource_version),
                        "role": role,
                    }
                )
            result = {"upload_set_id": upload_set_id, "entries": result_entries}
            self._record_change(
                conn,
                owner_key=owner_key,
                resource_type="upload_set",
                resource_id=upload_set_id,
                version=version,
                operation="created",
            )
            self._save_operation(conn, owner_key, operation_id, "create_upload_set", result)
        return result

    def create_output(
        self,
        *,
        output_id: str,
        run_id: str,
        prompt_id: str,
        item_id: str,
        aspect_ratio: str,
        resource_id: str,
        resource_version: int,
        operation_id: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            owner_key = self._run_owner(conn, run_id)
            prior = self._operation_result(conn, owner_key, operation_id)
            if prior is not None:
                return prior
            now = time.time()
            conn.execute(
                """
                INSERT INTO outputs VALUES (?, ?, ?, ?, ?, 1, 'available', ?, ?)
                """,
                (output_id, run_id, prompt_id, item_id, aspect_ratio, now, now),
            )
            conn.execute(
                """
                INSERT INTO output_versions(
                    output_id, version, resource_id, resource_version,
                    source_output_version, revision_id, created_at
                ) VALUES (?, 1, ?, ?, NULL, NULL, ?)
                """,
                (output_id, resource_id, int(resource_version), now),
            )
            result = {"output_id": output_id, "version": 1}
            self._record_change(
                conn,
                owner_key=owner_key,
                resource_type="output",
                resource_id=output_id,
                version=1,
                operation="created",
            )
            self._save_operation(conn, owner_key, operation_id, "create_output", result)
        return result

    def record_revision(
        self,
        *,
        revision_id: str,
        output_id: str,
        source_output_version: int,
        result_resource_id: str,
        result_resource_version: int,
        engine: str,
        status: str,
        attempt: int,
        operation_id: str,
        prompt_resource_id: str | None = None,
        prompt_resource_version: int | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT o.current_version, r.owner_key
                FROM outputs o JOIN runs r ON r.run_id = o.run_id
                WHERE o.output_id = ?
                """,
                (output_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Output not found")
            owner_key = str(row["owner_key"])
            prior = self._operation_result(conn, owner_key, operation_id)
            if prior is not None:
                return prior
            if int(row["current_version"]) != int(source_output_version):
                raise VersionConflictError("Output source version is not active")
            result_version = int(row["current_version"]) + 1
            now = time.time()
            conn.execute(
                """
                INSERT INTO revisions(
                    revision_id, artifact_id, comment, output_id, source_output_version,
                    result_output_version, prompt_resource_id, prompt_resource_version,
                    engine, status, attempt, error, created_at, updated_at
                ) VALUES (?, NULL, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    output_id,
                    int(source_output_version),
                    result_version,
                    prompt_resource_id,
                    prompt_resource_version,
                    engine,
                    status,
                    int(attempt),
                    error,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO output_versions(
                    output_id, version, resource_id, resource_version,
                    source_output_version, revision_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    output_id,
                    result_version,
                    result_resource_id,
                    int(result_resource_version),
                    int(source_output_version),
                    revision_id,
                    now,
                ),
            )
            conn.execute(
                "UPDATE outputs SET current_version = ?, updated_at = ? WHERE output_id = ?",
                (result_version, now, output_id),
            )
            result = {
                "revision_id": revision_id,
                "output_id": output_id,
                "source_output_version": int(source_output_version),
                "result_output_version": result_version,
                "status": status,
            }
            self._record_change(
                conn,
                owner_key=owner_key,
                resource_type="revision",
                resource_id=revision_id,
                version=result_version,
                operation="created",
            )
            self._save_operation(conn, owner_key, operation_id, "record_revision", result)
        return result

    def output_versions(self, output_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM output_versions WHERE output_id = ? ORDER BY version",
                (output_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def changes(self, *, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM change_log
                WHERE sequence > ?
                ORDER BY sequence
                LIMIT ?
                """,
                (int(after), max(1, min(int(limit), 1000))),
            ).fetchall()
        return [dict(row) for row in rows]

    def queue_projection(
        self,
        *,
        owner_key: str,
        operation_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> str:
        event_id = "evt_" + hashlib.sha256(
            f"{owner_key}\0{operation_id}\0{event_type}".encode("utf-8")
        ).hexdigest()[:32]
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT event_id FROM outbox WHERE owner_key = ? AND operation_id = ?",
                (owner_key, operation_id),
            ).fetchone()
            if existing is not None:
                return str(existing["event_id"])
            conn.execute(
                """
                INSERT INTO outbox(
                    event_id, owner_key, operation_id, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    owner_key,
                    operation_id,
                    event_type,
                    json.dumps(payload, sort_keys=True),
                    time.time(),
                ),
            )
        return event_id

    def garbage_collect_objects(self) -> list[str]:
        deleted: list[tuple[str, str]] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT o.sha256, o.relative_path
                FROM objects o
                WHERE NOT EXISTS (
                    SELECT 1 FROM resource_versions rv WHERE rv.object_sha256 = o.sha256
                )
                ORDER BY o.sha256
                """
            ).fetchall()
            for row in rows:
                conn.execute("DELETE FROM objects WHERE sha256 = ?", (row["sha256"],))
                deleted.append((str(row["sha256"]), str(row["relative_path"])))
            conn.commit()
        for _, relative_path in deleted:
            (self.paths.root / relative_path).unlink(missing_ok=True)
        return [sha256 for sha256, _ in deleted]

    def delete_run(
        self,
        run_id: str,
        *,
        operation_id: str,
        purge_resources: bool = False,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                operation = conn.execute(
                    "SELECT result_json FROM operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if operation is not None:
                    conn.commit()
                    return json.loads(operation["result_json"])
                conn.rollback()
                raise ValueError("Run not found")
            owner_key = str(run["owner_key"])
            prior = self._operation_result(conn, owner_key, operation_id)
            if prior is not None:
                conn.commit()
                return prior
            candidates = [
                str(row["resource_id"])
                for row in conn.execute(
                    """
                    SELECT resource_id FROM run_entries WHERE run_id = ?
                    UNION
                    SELECT ov.resource_id
                    FROM output_versions ov JOIN outputs o ON o.output_id = ov.output_id
                    WHERE o.run_id = ?
                    """,
                    (run_id, run_id),
                ).fetchall()
            ]
            conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
            purged: list[str] = []
            if purge_resources:
                for resource_id in candidates:
                    referenced = conn.execute(
                        """
                        SELECT 1 FROM run_entries WHERE resource_id = ?
                        UNION ALL
                        SELECT 1 FROM output_versions WHERE resource_id = ?
                        LIMIT 1
                        """,
                        (resource_id, resource_id),
                    ).fetchone()
                    if referenced is None:
                        conn.execute(
                            "DELETE FROM resource_versions WHERE resource_id = ?",
                            (resource_id,),
                        )
                        conn.execute("DELETE FROM resources WHERE resource_id = ?", (resource_id,))
                        purged.append(resource_id)
            event_id = "evt_" + hashlib.sha256(
                f"{owner_key}\0{operation_id}\0run.deleted".encode("utf-8")
            ).hexdigest()[:32]
            receipt = {
                "run_id": run_id,
                "status": "deleted",
                "purged_resource_ids": sorted(purged),
                "event_id": event_id,
            }
            conn.execute(
                """
                INSERT INTO outbox(
                    event_id, owner_key, operation_id, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, 'run.deleted', ?, ?)
                """,
                (event_id, owner_key, operation_id, json.dumps(receipt, sort_keys=True), time.time()),
            )
            self._record_change(
                conn,
                owner_key=owner_key,
                resource_type="run",
                resource_id=run_id,
                version=None,
                operation="deleted",
            )
            self._save_operation(conn, owner_key, operation_id, "delete_run", receipt)
            conn.commit()
        self.garbage_collect_objects()
        return receipt


def _safe_component(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._")
    return clean[:120] or "unknown"


def _safe_filename(value: str) -> str:
    return _safe_component(Path(value).name)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


_V1_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS jobs (
        job_id TEXT PRIMARY KEY,
        owner_key TEXT NOT NULL,
        status TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        artifact_id TEXT PRIMARY KEY,
        owner_key TEXT NOT NULL,
        run_id TEXT NOT NULL,
        run_number INTEGER NOT NULL,
        job_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        prompt_id TEXT NOT NULL,
        aspect_ratio TEXT NOT NULL,
        filename TEXT NOT NULL,
        relative_path TEXT NOT NULL UNIQUE,
        sha256 TEXT NOT NULL,
        bytes INTEGER NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(owner_key, run_id, prompt_id, aspect_ratio)",
    """
    CREATE TABLE IF NOT EXISTS outbox (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at REAL NOT NULL,
        delivered_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS revisions (
        revision_id TEXT PRIMARY KEY,
        artifact_id TEXT NOT NULL,
        comment TEXT NOT NULL,
        engine TEXT NOT NULL,
        status TEXT NOT NULL,
        error TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_revisions_status ON revisions(status, created_at)",
    "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value INTEGER NOT NULL)",
    "INSERT OR IGNORE INTO metadata(key, value) VALUES ('change_sequence', 0)",
)


def _migration_v2(conn: sqlite3.Connection) -> None:
    for statement in _V1_SCHEMA:
        conn.execute(statement)
    conn.execute("ALTER TABLE outbox RENAME TO legacy_outbox")
    conn.execute("ALTER TABLE revisions RENAME TO legacy_revisions")
    statements = (
        """
        CREATE TABLE objects (
            sha256 TEXT PRIMARY KEY,
            relative_path TEXT NOT NULL UNIQUE,
            bytes INTEGER NOT NULL CHECK(bytes >= 0),
            media_type TEXT NOT NULL,
            created_at REAL NOT NULL,
            verified_at REAL
        )
        """,
        """
        CREATE TABLE resources (
            resource_id TEXT PRIMARY KEY,
            owner_key TEXT NOT NULL,
            kind TEXT NOT NULL,
            logical_key TEXT NOT NULL,
            current_version INTEGER NOT NULL CHECK(current_version > 0),
            status TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            deleted_at REAL,
            UNIQUE(owner_key, kind, logical_key)
        )
        """,
        """
        CREATE TABLE resource_versions (
            resource_id TEXT NOT NULL,
            version INTEGER NOT NULL CHECK(version > 0),
            object_sha256 TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            PRIMARY KEY(resource_id, version),
            FOREIGN KEY(resource_id) REFERENCES resources(resource_id) ON DELETE CASCADE,
            FOREIGN KEY(object_sha256) REFERENCES objects(sha256)
        )
        """,
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            owner_key TEXT NOT NULL,
            device_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            run_number INTEGER NOT NULL,
            display_batch TEXT NOT NULL,
            flow_type TEXT NOT NULL,
            status TEXT NOT NULL,
            manifest_resource_id TEXT,
            manifest_version INTEGER,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(manifest_resource_id, manifest_version)
                REFERENCES resource_versions(resource_id, version)
        )
        """,
        """
        CREATE TABLE run_entries (
            run_id TEXT NOT NULL,
            entry_id TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            resource_version INTEGER NOT NULL,
            role TEXT NOT NULL,
            prompt_id TEXT,
            item_id TEXT,
            aspect_ratio TEXT,
            position INTEGER NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(run_id, entry_id),
            UNIQUE(run_id, position),
            FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(resource_id, resource_version)
                REFERENCES resource_versions(resource_id, version)
        )
        """,
        """
        CREATE TABLE upload_sets (
            upload_set_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            prompt_id TEXT NOT NULL,
            phase TEXT NOT NULL,
            version INTEGER NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE upload_set_entries (
            upload_set_id TEXT NOT NULL,
            position INTEGER NOT NULL CHECK(position > 0),
            resource_id TEXT NOT NULL,
            resource_version INTEGER NOT NULL,
            role TEXT NOT NULL,
            PRIMARY KEY(upload_set_id, position),
            FOREIGN KEY(upload_set_id) REFERENCES upload_sets(upload_set_id) ON DELETE CASCADE,
            FOREIGN KEY(resource_id, resource_version)
                REFERENCES resource_versions(resource_id, version)
        )
        """,
        """
        CREATE TABLE outputs (
            output_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            prompt_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            aspect_ratio TEXT NOT NULL,
            current_version INTEGER NOT NULL CHECK(current_version > 0),
            status TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE revisions (
            revision_id TEXT PRIMARY KEY,
            artifact_id TEXT,
            comment TEXT NOT NULL DEFAULT '',
            output_id TEXT,
            source_output_version INTEGER,
            result_output_version INTEGER,
            prompt_resource_id TEXT,
            prompt_resource_version INTEGER,
            engine TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 1,
            error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
            FOREIGN KEY(output_id) REFERENCES outputs(output_id) ON DELETE CASCADE,
            FOREIGN KEY(prompt_resource_id, prompt_resource_version)
                REFERENCES resource_versions(resource_id, version)
        )
        """,
        """
        CREATE TABLE output_versions (
            output_id TEXT NOT NULL,
            version INTEGER NOT NULL CHECK(version > 0),
            resource_id TEXT NOT NULL,
            resource_version INTEGER NOT NULL,
            source_output_version INTEGER,
            revision_id TEXT,
            created_at REAL NOT NULL,
            PRIMARY KEY(output_id, version),
            FOREIGN KEY(output_id) REFERENCES outputs(output_id) ON DELETE CASCADE,
            FOREIGN KEY(resource_id, resource_version)
                REFERENCES resource_versions(resource_id, version),
            FOREIGN KEY(revision_id) REFERENCES revisions(revision_id)
        )
        """,
        """
        CREATE TABLE change_log (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_key TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            version INTEGER,
            operation TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """,
        """
        CREATE TABLE outbox (
            event_id TEXT PRIMARY KEY,
            owner_key TEXT,
            operation_id TEXT,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            delivered_at REAL,
            UNIQUE(owner_key, operation_id)
        )
        """,
        """
        CREATE TABLE operations (
            owner_key TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            operation_type TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY(owner_key, operation_id)
        )
        """,
        """
        INSERT INTO revisions(
            revision_id, artifact_id, comment, engine, status, attempt, error, created_at, updated_at
        )
        SELECT revision_id, artifact_id, comment, engine, status, 1, error, created_at, updated_at
        FROM legacy_revisions
        """,
        """
        INSERT INTO outbox(event_id, event_type, payload_json, created_at, delivered_at)
        SELECT event_id, event_type, payload_json, created_at, delivered_at FROM legacy_outbox
        """,
        "DROP TABLE legacy_revisions",
        "DROP TABLE legacy_outbox",
        "CREATE INDEX idx_resources_owner_kind ON resources(owner_key, kind, status)",
        "CREATE INDEX idx_resource_versions_object ON resource_versions(object_sha256)",
        "CREATE INDEX idx_runs_owner_number ON runs(owner_key, run_number)",
        "CREATE INDEX idx_run_entries_resource ON run_entries(resource_id, resource_version)",
        "CREATE INDEX idx_outputs_run ON outputs(run_id, prompt_id, aspect_ratio)",
        "CREATE INDEX idx_output_versions_resource ON output_versions(resource_id, resource_version)",
        "CREATE INDEX idx_revisions_status ON revisions(status, created_at)",
        "CREATE INDEX idx_change_log_owner_sequence ON change_log(owner_key, sequence)",
        "CREATE INDEX idx_outbox_pending ON outbox(delivered_at, created_at)",
        """
        CREATE TRIGGER resource_versions_immutable
        BEFORE UPDATE ON resource_versions
        BEGIN
            SELECT RAISE(ABORT, 'resource versions are immutable');
        END
        """,
        """
        CREATE TRIGGER output_versions_immutable
        BEFORE UPDATE ON output_versions
        BEGIN
            SELECT RAISE(ABORT, 'output versions are immutable');
        END
        """,
    )
    for statement in statements:
        conn.execute(statement)


def _database_has_tables(database: Path) -> bool:
    if not database.exists():
        return False
    with sqlite3.connect(database) as conn:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
        ).fetchone() is not None


def _backup_database(paths: AgentPaths, version: int) -> Path:
    backup = paths.state / f"agent.sqlite3.pre-v{version}-{time.time_ns()}.bak"
    with sqlite3.connect(paths.database) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)
    return backup


def migrate_database(
    paths: AgentPaths,
    *,
    target_version: int = SCHEMA_VERSION,
    migrations: dict[int, tuple[str, ...]] | None = None,
) -> None:
    paths.ensure()
    existed = _database_has_tables(paths.database)
    with sqlite3.connect(paths.database, timeout=30) as conn:
        current = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if current == 0 and existed:
            current = 1
            conn.execute("PRAGMA user_version = 1")
    if current > target_version:
        raise SchemaMigrationError(
            f"Database schema {current} is newer than supported schema {target_version}"
        )
    if current == target_version:
        _atomic_write_text(
            paths.state / "schema-version.json",
            json.dumps({"schema_version": target_version}) + "\n",
        )
        return
    if existed:
        _backup_database(paths, current)

    selected: dict[int, Any] = migrations or {1: _V1_SCHEMA, 2: _migration_v2}
    try:
        with sqlite3.connect(paths.database, timeout=30, isolation_level=None) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            for version in range(current + 1, target_version + 1):
                migration = selected.get(version)
                if migration is None:
                    raise SchemaMigrationError(f"Missing schema migration {version}")
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if callable(migration):
                        migration(conn)
                    else:
                        for statement in migration:
                            conn.execute(statement)
                    conn.execute(f"PRAGMA user_version = {version}")
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
    except Exception as exc:
        if isinstance(exc, SchemaMigrationError):
            raise
        raise SchemaMigrationError(
            f"Failed to migrate local database from version {current} to {target_version}"
        ) from exc
    _atomic_write_text(
        paths.state / "schema-version.json",
        json.dumps({"schema_version": target_version}) + "\n",
    )
