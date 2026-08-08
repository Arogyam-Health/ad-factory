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


SCHEMA_VERSION = 1


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
            _atomic_write_text(version_path, json.dumps({"schema_version": SCHEMA_VERSION}) + "\n")


@dataclass(frozen=True)
class ContentObject:
    sha256: str
    path: Path
    bytes: int


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
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    owner_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
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
                );
                CREATE INDEX IF NOT EXISTS idx_artifacts_run
                    ON artifacts(owner_key, run_id, prompt_id, aspect_ratio);
                CREATE TABLE IF NOT EXISTS outbox (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    delivered_at REAL
                );
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
                );
                CREATE INDEX IF NOT EXISTS idx_revisions_status
                    ON revisions(status, created_at);
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO metadata(key, value) VALUES ('change_sequence', 0);
                """
            )
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
                "INSERT INTO revisions VALUES (?, ?, ?, ?, 'queued', NULL, ?, ?)",
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
            row = conn.execute("SELECT value FROM metadata WHERE key = 'change_sequence'").fetchone()
        return int(row["value"] if row is not None else 0)


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
