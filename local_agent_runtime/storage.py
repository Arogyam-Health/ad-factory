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
_LOCAL_JOB_KEYS = frozenset(
    {
        "run_id",
        "command",
        "parameters",
        "output_id",
        "source_output_version",
        "prompt_resource_id",
        "prompt_resource_version",
        "resource_id",
        "resource_version",
        "engine",
        "mode",
    }
)
_LOCAL_JOB_PARAMETER_KEYS = frozenset(
    {
        "engine",
        "mode",
        "count",
        "manifest_version",
        "config_version_id",
        "prompt_version_id",
        "resource_version",
        "upload_set_version",
        "output_version",
        "product_asset_ids",
    }
)
_PRODUCT_ASSET_ID_LIMIT = 48
_LOCAL_FORBIDDEN_PARTS = (
    "base64",
    "body",
    "bytes",
    "comment",
    "content",
    "credential",
    "document",
    "log",
    "path",
    "secret",
    "token",
    "trace",
    "url",
)


def _local_parameter_string_ok(value: str) -> bool:
    return not (
        not value
        or len(value) > 64
        or "://" in value
        or value.startswith(("/", "\\\\", "data:", "file:"))
        or bool(re.match(r"^[A-Za-z]:[\\/]", value))
    )


def _clean_product_asset_ids(value: Any, *, strict: bool) -> list[str] | None:
    if not isinstance(value, list) or not (1 <= len(value) <= _PRODUCT_ASSET_ID_LIMIT):
        if strict:
            raise ValueError("Local job parameters must be bounded scalars")
        return None
    clean_ids: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _local_parameter_string_ok(item):
            if strict:
                raise ValueError("Local job parameter is prohibited")
            return None
        clean_ids.append(item)
    return clean_ids


def metadata_job_payload(payload: dict[str, Any], *, strict: bool = True) -> dict[str, Any]:
    """Bound a local job row to IDs and scalar control metadata."""
    if not isinstance(payload, dict):
        raise ValueError("Job payload must be metadata")
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        lowered = str(key).lower()
        allowed = key in _LOCAL_JOB_KEYS
        prohibited = any(part in lowered for part in _LOCAL_FORBIDDEN_PARTS)
        if prohibited or not allowed:
            if strict:
                raise ValueError("Local job payload contains prohibited fields")
            continue
        if key == "parameters":
            if not isinstance(value, dict):
                if strict:
                    raise ValueError("Local job parameters must be metadata")
                continue
            parameters: dict[str, Any] = {}
            for parameter_key, parameter_value in value.items():
                if parameter_key not in _LOCAL_JOB_PARAMETER_KEYS:
                    if strict:
                        raise ValueError("Local job parameters contain unsupported fields")
                    continue
                if parameter_key == "product_asset_ids":
                    asset_ids = _clean_product_asset_ids(parameter_value, strict=strict)
                    if asset_ids is not None:
                        parameters[parameter_key] = asset_ids
                    continue
                if isinstance(parameter_value, bool) or not isinstance(
                    parameter_value, (str, int)
                ):
                    if strict:
                        raise ValueError("Local job parameters must be bounded scalars")
                    continue
                if isinstance(parameter_value, str) and not _local_parameter_string_ok(
                    parameter_value
                ):
                    if strict:
                        raise ValueError("Local job parameter is prohibited")
                    continue
                if isinstance(parameter_value, int) and not 0 <= parameter_value <= 10_000:
                    if strict:
                        raise ValueError("Local job parameter is out of bounds")
                    continue
                parameters[parameter_key] = parameter_value
            clean[key] = parameters
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            if strict:
                raise ValueError("Local job payload must use bounded scalars")
            continue
        if isinstance(value, str) and (
            not value
            or len(value) > 200
            or "://" in value
            or value.startswith(("/", "\\\\", "data:", "file:"))
            or re.match(r"^[A-Za-z]:[\\/]", value)
            or "\n" in value
        ):
            if strict:
                raise ValueError("Local job payload contains prohibited values")
            continue
        clean[key] = value
    return clean


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

    def record_job(self, job_id: str, owner_key: str, status: str, payload: dict[str, Any]) -> None:
        payload = metadata_job_payload(payload)
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

    def resolve_job_context(self, job_id: str) -> dict[str, Any]:
        """Resolve job IDs to authoritative local manifest/resource records."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner_key, status, payload_json FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ValueError(
                    "Local job metadata is missing. Reconnect the agent and queue the run again."
                )
            payload = metadata_job_payload(json.loads(row["payload_json"]))
            run_id = str(payload.get("run_id") or "")
            run = conn.execute(
                "SELECT * FROM runs WHERE run_id = ? AND owner_key = ?",
                (run_id, row["owner_key"]),
            ).fetchone()
            if run is None:
                raise ValueError(
                    "Local run workspace is missing. Restore a local backup or start a new run."
                )
            entries = conn.execute(
                """
                SELECT re.*, r.kind, o.relative_path, o.media_type, o.bytes
                FROM run_entries re
                JOIN resources r ON r.resource_id = re.resource_id
                JOIN resource_versions rv
                  ON rv.resource_id = re.resource_id
                 AND rv.version = re.resource_version
                JOIN objects o ON o.sha256 = rv.object_sha256
                WHERE re.run_id = ?
                ORDER BY re.position, re.entry_id
                """,
                (run_id,),
            ).fetchall()
        resolved_entries = []
        for entry in entries:
            item = dict(entry)
            local_path = (self.paths.root / str(item.pop("relative_path"))).resolve()
            local_path.relative_to(self.paths.root.resolve())
            item["local_path"] = local_path
            resolved_entries.append(item)
        return {
            "job_id": job_id,
            "owner_key": str(row["owner_key"]),
            "status": str(row["status"]),
            "payload": payload,
            "run": dict(run),
            "entries": resolved_entries,
        }

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

    def change_sequence(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(sequence) AS value FROM change_log").fetchone()
            legacy = conn.execute(
                "SELECT value FROM metadata WHERE key = 'change_sequence'"
            ).fetchone()
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

    def resource_path(self, resource_id: str, version: int | None = None) -> Path:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT o.relative_path
                FROM resources r
                JOIN resource_versions rv
                  ON rv.resource_id = r.resource_id
                 AND rv.version = COALESCE(?, r.current_version)
                JOIN objects o ON o.sha256 = rv.object_sha256
                WHERE r.resource_id = ?
                """,
                (version, resource_id),
            ).fetchone()
        if row is None:
            raise ValueError("Resource version not found")
        path = (self.paths.root / str(row["relative_path"])).resolve()
        path.relative_to(self.paths.root.resolve())
        if not path.is_file():
            raise ValueError("Resource content not found")
        return path

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
                    display_batch
                    or (
                        f"ref_v{int(run_number)}"
                        if flow_type == "reference"
                        else f"v{int(run_number)}"
                    ),
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
        source_output_version: int | None = None,
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
                ) VALUES (?, 1, ?, ?, ?, NULL, ?)
                """,
                (
                    output_id,
                    resource_id,
                    int(resource_version),
                    (
                        int(source_output_version)
                        if source_output_version is not None
                        else None
                    ),
                    now,
                ),
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

    def output(self, output_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM outputs WHERE output_id = ?", (output_id,)).fetchone()
        return dict(row) if row is not None else None

    def replace_output(
        self,
        *,
        output_id: str,
        source_output_version: int,
        source: Path,
        operation_id: str,
        media_type: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT o.*, r.owner_key
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
        resource = self.put_resource(
            source=source,
            owner_key=owner_key,
            kind="output_image",
            logical_key=f"{output_id}/replacement/{operation_id}",
            operation_id=operation_id + ":resource",
            media_type=media_type,
            metadata={"output_id": output_id},
        )
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = self._operation_result(conn, owner_key, operation_id)
            if prior is not None:
                conn.commit()
                return prior
            current = conn.execute(
                "SELECT current_version FROM outputs WHERE output_id = ?", (output_id,)
            ).fetchone()
            if current is None or int(current["current_version"]) != int(source_output_version):
                conn.rollback()
                raise VersionConflictError("Output source version is not active")
            result_version = int(source_output_version) + 1
            conn.execute(
                """
                INSERT INTO output_versions(
                    output_id, version, resource_id, resource_version,
                    source_output_version, revision_id, created_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    output_id,
                    result_version,
                    resource.resource_id,
                    resource.version,
                    int(source_output_version),
                    now,
                ),
            )
            conn.execute(
                "UPDATE outputs SET current_version = ?, status = 'available', updated_at = ? "
                "WHERE output_id = ?",
                (result_version, now, output_id),
            )
            result = {
                "output_id": output_id,
                "source_output_version": int(source_output_version),
                "result_output_version": result_version,
                "resource_id": resource.resource_id,
                "resource_version": resource.version,
            }
            self._record_change(
                conn,
                owner_key=owner_key,
                resource_type="output",
                resource_id=output_id,
                version=result_version,
                operation="replaced",
            )
            self._save_operation(conn, owner_key, operation_id, "replace_output", result)
            conn.commit()
        return result

    def _resource_text(self, resource_id: str, version: int) -> str:
        path = self.resource_path(resource_id, version)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def _latest_config_text(self, owner_key: str, logical_key: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT resource_id, current_version FROM resources
                WHERE owner_key = ? AND kind = 'config_file'
                  AND logical_key = ? AND deleted_at IS NULL
                  AND status != 'deleted'
                """,
                (owner_key, logical_key),
            ).fetchone()
        if row is None:
            return ""
        return self._resource_text(str(row["resource_id"]), int(row["current_version"]))

    def _run_role_text(self, run_id: str, role: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT resource_id, resource_version
                FROM run_entries
                WHERE run_id = ? AND role = ?
                ORDER BY position LIMIT 1
                """,
                (run_id, role),
            ).fetchone()
        if row is None:
            return ""
        return self._resource_text(str(row["resource_id"]), int(row["resource_version"]))

    def _revision_rule_sources(
        self, owner_key: str, run_id: str
    ) -> tuple[dict[str, Any], str]:
        from local_agent_runtime.revision_prompt import parse_assembler_templates

        templates = parse_assembler_templates(
            self._latest_config_text(owner_key, "prompt_assembler_templates")
        )
        if not templates:
            settings_text = self._run_role_text(run_id, "structured_settings")
            if settings_text:
                try:
                    settings = json.loads(settings_text)
                except json.JSONDecodeError:
                    settings = {}
                if isinstance(settings, dict):
                    templates = parse_assembler_templates(
                        settings.get("prompt_assembler_templates")
                    )
        conversion = self._latest_config_text(owner_key, "conversion_916_prompt")
        if not conversion:
            conversion = self._run_role_text(run_id, "conversion_prompt")
        return templates, conversion

    def queue_output_revision(
        self,
        *,
        output_id: str,
        source_output_version: int,
        comment: str,
        engine: str,
        operation_id: str,
    ) -> dict[str, Any]:
        comment = str(comment).strip()
        if not comment:
            raise ValueError("Revision comment is required")
        if engine not in {"chatgpt", "gemini"}:
            raise ValueError("Revision engine must be chatgpt or gemini")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT o.current_version, o.prompt_id, o.aspect_ratio, r.owner_key, o.run_id
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
            prompt = conn.execute(
                """
                SELECT re.resource_id, re.resource_version
                FROM run_entries re JOIN resources r ON r.resource_id = re.resource_id
                WHERE re.run_id = ? AND re.prompt_id = ? AND r.kind = 'prompt'
                ORDER BY re.position LIMIT 1
                """,
                (row["run_id"], row["prompt_id"]),
            ).fetchone()
        original = ""
        if prompt is not None:
            original = self.resource_path(
                str(prompt["resource_id"]), int(prompt["resource_version"])
            ).read_text(encoding="utf-8")
        templates, conversion_prompt = self._revision_rule_sources(
            owner_key, str(row["run_id"])
        )
        from local_agent_runtime.revision_prompt import build_output_revision_prompt

        full_prompt = build_output_revision_prompt(
            comment=comment,
            aspect_ratio=str(row["aspect_ratio"] or "4:5"),
            original_prompt=original,
            assembler_templates=templates,
            conversion_916_prompt=conversion_prompt,
        )
        temporary = self.paths.staging / f".revision-prompt-{uuid.uuid4().hex}.tmp"
        temporary.write_text(full_prompt, encoding="utf-8")
        try:
            prompt_resource = self.put_resource(
                source=temporary,
                owner_key=owner_key,
                kind="revision_prompt",
                logical_key=f"{output_id}/{operation_id}",
                operation_id=operation_id + ":prompt",
                metadata={"output_id": output_id},
                media_type="text/plain; charset=utf-8",
            )
        finally:
            temporary.unlink(missing_ok=True)
        revision_id = "rev_" + hashlib.sha256(
            f"{owner_key}\0{operation_id}".encode("utf-8")
        ).hexdigest()[:24]
        now = time.time()
        result = {
            "revision_id": revision_id,
            "output_id": output_id,
            "source_output_version": int(source_output_version),
            "status": "queued",
        }
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = self._operation_result(conn, owner_key, operation_id)
            if prior is not None:
                conn.commit()
                return prior
            conn.execute(
                """
                INSERT INTO revisions(
                    revision_id, artifact_id, comment, output_id, source_output_version,
                    result_output_version, prompt_resource_id, prompt_resource_version,
                    engine, status, attempt, error, created_at, updated_at
                ) VALUES (?, NULL, '', ?, ?, NULL, ?, ?, ?, 'queued', 1, NULL, ?, ?)
                """,
                (
                    revision_id,
                    output_id,
                    int(source_output_version),
                    prompt_resource.resource_id,
                    prompt_resource.version,
                    engine,
                    now,
                    now,
                ),
            )
            self._record_change(
                conn,
                owner_key=owner_key,
                resource_type="revision",
                resource_id=revision_id,
                version=None,
                operation="queued",
            )
            self._save_operation(conn, owner_key, operation_id, "queue_output_revision", result)
            conn.commit()
        self.record_job(
            revision_id,
            owner_key,
            "queued",
            {
                "output_id": output_id,
                "command": "revision",
                "source_output_version": int(source_output_version),
                "prompt_resource_id": prompt_resource.resource_id,
                "prompt_resource_version": prompt_resource.version,
                "engine": engine,
            },
        )
        return result

    def claim_next_output_revision(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM revisions
                WHERE output_id IS NOT NULL AND status = 'queued'
                ORDER BY created_at LIMIT 1
                """
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            conn.execute(
                "UPDATE revisions SET status = 'running', updated_at = ? "
                "WHERE revision_id = ? AND status = 'queued'",
                (time.time(), row["revision_id"]),
            )
            conn.commit()
        with self._connect() as conn:
            claimed = conn.execute(
                "SELECT * FROM revisions WHERE revision_id = ?", (row["revision_id"],)
            ).fetchone()
        return dict(claimed) if claimed is not None else None

    def complete_output_revision(
        self,
        revision_id: str,
        *,
        result_source: Path,
        media_type: str,
        raw_source: Path | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            revision = conn.execute(
                """
                SELECT rev.*, run.owner_key
                FROM revisions rev
                JOIN outputs out ON out.output_id = rev.output_id
                JOIN runs run ON run.run_id = out.run_id
                WHERE rev.revision_id = ? AND rev.status = 'running'
                """,
                (revision_id,),
            ).fetchone()
            if revision is None:
                raise ValueError("Running output revision not found")
        resource = self.put_resource(
            source=result_source,
            owner_key=str(revision["owner_key"]),
            kind="output_image",
            logical_key=f"{revision['output_id']}/revision/{revision_id}",
            operation_id=f"{revision_id}:result",
            metadata={"output_id": revision["output_id"], "revision_id": revision_id},
            media_type=media_type,
        )
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT current_version FROM outputs WHERE output_id = ?",
                (revision["output_id"],),
            ).fetchone()
            if current is None or int(current["current_version"]) != int(
                revision["source_output_version"]
            ):
                conn.rollback()
                raise VersionConflictError("Output changed while revision was running")
            result_version = int(revision["source_output_version"]) + 1
            conn.execute(
                """
                INSERT INTO output_versions(
                    output_id, version, resource_id, resource_version,
                    source_output_version, revision_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision["output_id"],
                    result_version,
                    resource.resource_id,
                    resource.version,
                    int(revision["source_output_version"]),
                    revision_id,
                    now,
                ),
            )
            conn.execute(
                "UPDATE outputs SET current_version = ?, status = 'available', updated_at = ? "
                "WHERE output_id = ?",
                (result_version, now, revision["output_id"]),
            )
            conn.execute(
                """
                UPDATE revisions
                SET result_output_version = ?, status = 'completed', error = NULL, updated_at = ?
                WHERE revision_id = ?
                """,
                (result_version, now, revision_id),
            )
            conn.execute(
                "UPDATE jobs SET status = 'completed', updated_at = ? WHERE job_id = ?",
                (now, revision_id),
            )
            self._record_change(
                conn,
                owner_key=str(revision["owner_key"]),
                resource_type="revision",
                resource_id=revision_id,
                version=result_version,
                operation="completed",
            )
            conn.commit()
        if raw_source is not None and Path(raw_source).is_file():
            self.put_resource(
                source=raw_source,
                owner_key=str(revision["owner_key"]),
                kind="output_raw",
                logical_key=f"{revision['output_id']}:raw:v{result_version}",
                operation_id=f"{revision_id}:raw",
                metadata={
                    "output_id": revision["output_id"],
                    "output_version": result_version,
                    "revision_id": revision_id,
                },
                media_type=media_type,
            )
        return {
            "revision_id": revision_id,
            "output_id": revision["output_id"],
            "source_output_version": int(revision["source_output_version"]),
            "result_output_version": result_version,
            "status": "completed",
        }

    def fail_output_revision(self, revision_id: str, error: str) -> None:
        bounded = str(error).replace("\n", " ")[:512]
        with self._connect() as conn:
            conn.execute(
                "UPDATE revisions SET status = 'error', error = ?, updated_at = ? "
                "WHERE revision_id = ? AND output_id IS NOT NULL",
                (bounded, time.time(), revision_id),
            )
            conn.execute(
                "UPDATE jobs SET status = 'error', updated_at = ? WHERE job_id = ?",
                (time.time(), revision_id),
            )

    def activate_output(self, output_id: str, version: int, *, operation_id: str) -> None:
        self._mutate_output(output_id, operation_id, version=version)

    def set_output_status(self, output_id: str, status: str, *, operation_id: str) -> None:
        if status not in {"available", "archived"}:
            raise ValueError("Invalid output status")
        self._mutate_output(output_id, operation_id, status=status)

    def _mutate_output(
        self,
        output_id: str,
        operation_id: str,
        *,
        version: int | None = None,
        status: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT o.*, r.owner_key FROM outputs o
                JOIN runs r ON r.run_id = o.run_id WHERE o.output_id = ?
                """,
                (output_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Output not found")
            owner_key = str(row["owner_key"])
            if self._operation_result(conn, owner_key, operation_id) is not None:
                conn.commit()
                return
            target_version = int(version if version is not None else row["current_version"])
            target_status = str(status or "available")
            exists = conn.execute(
                "SELECT 1 FROM output_versions WHERE output_id = ? AND version = ?",
                (output_id, target_version),
            ).fetchone()
            if exists is None:
                raise ValueError("Output version not found")
            conn.execute(
                "UPDATE outputs SET current_version = ?, status = ?, updated_at = ? "
                "WHERE output_id = ?",
                (target_version, target_status, time.time(), output_id),
            )
            result = {
                "output_id": output_id,
                "version": target_version,
                "status": target_status,
            }
            self._record_change(
                conn,
                owner_key=owner_key,
                resource_type="output",
                resource_id=output_id,
                version=target_version,
                operation="activated" if version is not None else target_status,
            )
            self._save_operation(conn, owner_key, operation_id, "mutate_output", result)
            conn.commit()

    def delete_output(self, output_id: str, *, operation_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT o.*, r.owner_key FROM outputs o
                JOIN runs r ON r.run_id = o.run_id WHERE o.output_id = ?
                """,
                (output_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Output not found")
            owner_key = str(row["owner_key"])
            prior = self._operation_result(conn, owner_key, operation_id)
            if prior is not None:
                conn.commit()
                return prior
            conn.execute(
                "UPDATE outputs SET status = 'deleted', updated_at = ? WHERE output_id = ?",
                (time.time(), output_id),
            )
            raw_ids = [
                str(item["resource_id"])
                for item in conn.execute(
                    """
                    SELECT resource_id FROM resources
                    WHERE owner_key = ? AND kind = 'output_raw'
                      AND logical_key LIKE ? AND deleted_at IS NULL
                    """,
                    (owner_key, f"{output_id}:raw:%"),
                ).fetchall()
            ]
            for resource_id in raw_ids:
                conn.execute("DELETE FROM resource_versions WHERE resource_id = ?", (resource_id,))
                conn.execute("DELETE FROM resources WHERE resource_id = ?", (resource_id,))
            event_id = "evt_" + hashlib.sha256(
                f"{owner_key}\0{operation_id}\0output.deleted".encode("utf-8")
            ).hexdigest()[:32]
            receipt = {
                "output_id": output_id,
                "run_id": str(row["run_id"]),
                "status": "deleted",
                "event_id": event_id,
            }
            conn.execute(
                """
                INSERT INTO outbox(
                    event_id, owner_key, operation_id, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, 'output.deleted', ?, ?)
                """,
                (event_id, owner_key, operation_id, json.dumps(receipt), time.time()),
            )
            self._record_change(
                conn,
                owner_key=owner_key,
                resource_type="output",
                resource_id=output_id,
                version=int(row["current_version"]),
                operation="deleted",
            )
            self._save_operation(conn, owner_key, operation_id, "delete_output", receipt)
            conn.commit()
        return receipt

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

    _RECLAIMABLE_RESOURCES = """
        SELECT r.resource_id
        FROM resources r
        WHERE r.deleted_at IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM run_entries re WHERE re.resource_id = r.resource_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM output_versions ov WHERE ov.resource_id = r.resource_id
          )
    """

    def storage_report(self) -> dict[str, Any]:
        with self._connect() as conn:
            objects = conn.execute(
                "SELECT COUNT(*) AS count, COALESCE(SUM(bytes), 0) AS bytes FROM objects"
            ).fetchone()
            unreferenced = conn.execute(
                """
                SELECT COUNT(*) AS count, COALESCE(SUM(bytes), 0) AS bytes FROM objects o
                WHERE NOT EXISTS (
                    SELECT 1 FROM resource_versions rv WHERE rv.object_sha256 = o.sha256
                )
                """
            ).fetchone()
            reclaimable = conn.execute(
                f"SELECT COUNT(*) AS count FROM ({self._RECLAIMABLE_RESOURCES})"
            ).fetchone()
        staging_bytes = 0
        staging_dirs = 0
        if self.paths.staging.exists():
            for entry in self.paths.staging.rglob("*"):
                if entry.is_file():
                    staging_bytes += entry.stat().st_size
            staging_dirs = sum(1 for entry in self.paths.staging.iterdir() if entry.is_dir())
        return {
            "objects": int(objects["count"]),
            "object_bytes": int(objects["bytes"]),
            "unreferenced_objects": int(unreferenced["count"]),
            "unreferenced_bytes": int(unreferenced["bytes"]),
            "reclaimable_resources": int(reclaimable["count"]),
            "staging_bytes": staging_bytes,
            "staging_roots": staging_dirs,
        }

    def collect_garbage(self, *, staging_max_age_seconds: float = 86400.0) -> dict[str, Any]:
        """Hard-delete soft-deleted resources, then reclaim their unreferenced blobs."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(self._RECLAIMABLE_RESOURCES).fetchall()
            for row in rows:
                conn.execute(
                    "DELETE FROM resources WHERE resource_id = ?", (row["resource_id"],)
                )
            conn.commit()
        reclaimed = self.garbage_collect_objects()
        return {
            "deleted_resources": len(rows),
            "reclaimed_objects": len(reclaimed),
            "swept_staging_roots": self.sweep_staging(
                max_age_seconds=staging_max_age_seconds
            ),
        }

    def sweep_staging(self, *, max_age_seconds: float = 86400.0) -> int:
        """Remove abandoned per-job staging trees left behind by interrupted jobs."""
        if not self.paths.staging.exists():
            return 0
        cutoff = time.time() - max(0.0, max_age_seconds)
        removed = 0
        for group in self.paths.staging.iterdir():
            if not group.is_dir():
                continue
            for entry in group.iterdir():
                if not entry.is_dir() or entry.stat().st_mtime > cutoff:
                    continue
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        return removed

    def delete_run(
        self,
        run_id: str,
        *,
        operation_id: str,
        purge_resources: bool = False,
        owner_key: str | None = None,
    ) -> dict[str, Any]:
        """Delete a run. Pass owner_key so one account cannot purge another's run."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT * FROM runs WHERE run_id = ? AND (? IS NULL OR owner_key = ?)",
                (run_id, owner_key, owner_key),
            ).fetchone()
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
                    UNION
                    SELECT resource_id FROM resources
                    WHERE kind = 'output_raw' AND deleted_at IS NULL
                      AND EXISTS (
                          SELECT 1 FROM outputs o
                          WHERE o.run_id = ?
                            AND resources.logical_key LIKE o.output_id || ':raw:%'
                      )
                    """,
                    (run_id, run_id, run_id),
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

    # Tables that carry owner_key directly; the rest are reached through runs.
    _OWNED_TABLES = ("jobs", "artifacts", "outbox", "operations", "change_log", "runs")

    def reset_local_data(
        self, *, home: Path | None = None, owner_key: str | None = None
    ) -> dict[str, Any]:
        """Wipe local runs, prompts, outputs and staging. Keep device config and product images.

        Several dashboard accounts can share one device, so pass owner_key to
        reset a single account instead of every account on the machine.
        """
        preserved_kinds = ("product_image",)
        placeholders = ",".join("?" for _ in preserved_kinds)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            owned_runs = [
                str(row["run_id"])
                for row in conn.execute(
                    "SELECT run_id FROM runs WHERE (? IS NULL OR owner_key = ?)",
                    (owner_key, owner_key),
                ).fetchall()
            ]
            deleted_runs = len(owned_runs)
            run_filter = f"run_id IN ({','.join('?' for _ in owned_runs)})"
            if owned_runs:
                for table, statement in (
                    (
                        "upload_set_entries",
                        "DELETE FROM upload_set_entries WHERE upload_set_id IN "
                        f"(SELECT upload_set_id FROM upload_sets WHERE {run_filter})",
                    ),
                    ("upload_sets", f"DELETE FROM upload_sets WHERE {run_filter}"),
                    (
                        "output_versions",
                        "DELETE FROM output_versions WHERE output_id IN "
                        f"(SELECT output_id FROM outputs WHERE {run_filter})",
                    ),
                    (
                        "revisions",
                        "DELETE FROM revisions WHERE output_id IN "
                        f"(SELECT output_id FROM outputs WHERE {run_filter})",
                    ),
                    ("outputs", f"DELETE FROM outputs WHERE {run_filter}"),
                    ("run_entries", f"DELETE FROM run_entries WHERE {run_filter}"),
                ):
                    if self._table_exists(conn, table):
                        conn.execute(statement, owned_runs)
            for table in self._OWNED_TABLES:
                if self._table_exists(conn, table):
                    conn.execute(
                        f"DELETE FROM {table} WHERE (? IS NULL OR owner_key = ?)",
                        (owner_key, owner_key),
                    )
            leftover = conn.execute(
                f"""
                SELECT resource_id FROM resources
                WHERE kind NOT IN ({placeholders})
                  AND (? IS NULL OR owner_key = ?)
                """,
                (*preserved_kinds, owner_key, owner_key),
            ).fetchall()
            for row in leftover:
                conn.execute(
                    "DELETE FROM resource_versions WHERE resource_id = ?",
                    (row["resource_id"],),
                )
                conn.execute(
                    "DELETE FROM resources WHERE resource_id = ?",
                    (row["resource_id"],),
                )
            conn.commit()
        reclaimed = self.garbage_collect_objects()
        staging_removed = False
        legacy_deleted = False
        # Scratch directories are shared by every account on the device, so only a
        # device-wide reset may remove them.
        if owner_key is None:
            if self.paths.staging.exists():
                shutil.rmtree(self.paths.staging, ignore_errors=True)
                self.paths.staging.mkdir(parents=True, exist_ok=True)
                staging_removed = True
            if self.paths.artifacts.exists():
                shutil.rmtree(self.paths.artifacts, ignore_errors=True)
                self.paths.artifacts.mkdir(parents=True, exist_ok=True)
            legacy_root = (home or Path.home()) / "ad-factory-agent-output"
            if legacy_root.exists() or legacy_root.is_symlink():
                if legacy_root.is_dir() and not legacy_root.is_symlink():
                    shutil.rmtree(legacy_root, ignore_errors=True)
                else:
                    legacy_root.unlink(missing_ok=True)
                legacy_deleted = True
        return {
            "deleted_runs": int(deleted_runs),
            "deleted_resources": len(leftover),
            "reclaimed_objects": len(reclaimed),
            "staging_removed": staging_removed,
            "legacy_output_deleted": legacy_deleted,
            "preserved_kinds": list(preserved_kinds),
            "owner_key": owner_key or "",
        }

    @staticmethod
    def _table_exists(conn: Any, table: str) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            is not None
        )


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
