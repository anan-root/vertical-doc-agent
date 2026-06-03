"""后端核心表的 PostgreSQL 访问层。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .db import DatabaseConnection, utc_now


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    project_id: str
    name: str
    description: str | None
    project_type: str | None
    stage: str
    stage_label: str | None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class UploadedFileRecord:
    file_id: str
    project_id: str | None
    business_type: str
    file_name: str
    file_ext: str | None
    mime_type: str | None
    file_size: int | None
    page_count: int | None
    storage_uri: str
    sha256: str | None
    status: str
    related_source_bid_id: str | None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: str
    project_id: str | None
    job_type: str
    status: str
    progress_total: int | None
    progress_completed: int | None
    progress_failed: int | None
    progress_percent: float | None
    message: str | None
    result_ref: str | None
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime
    config_snapshot: dict[str, Any] | None
    metadata: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class AccountRecord:
    account_id: str
    username: str
    display_name: str
    role: str
    department: str | None
    phone: str | None
    email: str | None
    password_hash: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None
    metadata: dict[str, Any] | None


class BackendRepository:
    """封装项目、文件和任务三类核心表的基础读写。"""

    def __init__(self, connection: DatabaseConnection) -> None:
        self.connection = connection

    def create_project(
        self,
        *,
        name: str,
        project_id: str | None = None,
        description: str | None = None,
        project_type: str | None = None,
        stage: str = "draft",
        stage_label: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProjectRecord:
        now = utc_now()
        record = ProjectRecord(
            project_id=project_id or _new_id("P"),
            name=name,
            description=description,
            project_type=project_type,
            stage=stage,
            stage_label=stage_label,
            created_at=now,
            updated_at=now,
            metadata=metadata,
        )
        self.connection.execute(
            """
            INSERT INTO projects(
              project_id, name, description, project_type, stage, stage_label,
              created_at, updated_at, metadata_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.project_id,
                record.name,
                record.description,
                record.project_type,
                record.stage,
                record.stage_label,
                record.created_at,
                record.updated_at,
                _jsonb(record.metadata),
            ),
        )
        self.connection.commit()
        return record

    def get_project(self, project_id: str) -> ProjectRecord | None:
        row = self.connection.execute(
            """
            SELECT project_id, name, description, project_type, stage, stage_label,
                   created_at, updated_at, metadata_json
            FROM projects
            WHERE project_id = %s
            """,
            (project_id,),
        ).fetchone()
        return _project_from_row(row) if row else None

    def list_projects(self, *, limit: int = 50, offset: int = 0) -> list[ProjectRecord]:
        rows = self.connection.execute(
            """
            SELECT project_id, name, description, project_type, stage, stage_label,
                   created_at, updated_at, metadata_json
            FROM projects
            ORDER BY updated_at DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        ).fetchall()
        return [_project_from_row(row) for row in rows]

    def create_account(
        self,
        *,
        username: str,
        display_name: str,
        role: str = "bid_staff",
        account_id: str | None = None,
        department: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        status: str = "active",
        metadata: dict[str, Any] | None = None,
        password_hash: str | None = None,
    ) -> AccountRecord:
        now = utc_now()
        record = AccountRecord(
            account_id=account_id or _new_id("U"),
            username=username,
            display_name=display_name,
            role=role,
            department=department,
            phone=phone,
            email=email,
            password_hash=password_hash,
            status=status,
            created_at=now,
            updated_at=now,
            last_login_at=None,
            metadata=metadata,
        )
        self.connection.execute(
            """
            INSERT INTO user_accounts(
              account_id, username, display_name, role, department, phone, email, password_hash,
              status, created_at, updated_at, last_login_at, metadata_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.account_id,
                record.username,
                record.display_name,
                record.role,
                record.department,
                record.phone,
                record.email,
                record.password_hash,
                record.status,
                record.created_at,
                record.updated_at,
                record.last_login_at,
                _jsonb(record.metadata),
            ),
        )
        self.connection.commit()
        return record

    def list_accounts(self, *, limit: int = 100, offset: int = 0) -> list[AccountRecord]:
        rows = self.connection.execute(
            """
            SELECT account_id, username, display_name, role, department, phone, email, password_hash,
                   status, created_at, updated_at, last_login_at, metadata_json
            FROM user_accounts
            ORDER BY created_at ASC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        ).fetchall()
        return [_account_from_row(row) for row in rows]

    def get_account(self, account_id: str) -> AccountRecord | None:
        row = self.connection.execute(
            """
            SELECT account_id, username, display_name, role, department, phone, email, password_hash,
                   status, created_at, updated_at, last_login_at, metadata_json
            FROM user_accounts
            WHERE account_id = %s
            """,
            (account_id,),
        ).fetchone()
        return _account_from_row(row) if row else None

    def get_account_by_username(self, username: str) -> AccountRecord | None:
        row = self.connection.execute(
            """
            SELECT account_id, username, display_name, role, department, phone, email, password_hash,
                   status, created_at, updated_at, last_login_at, metadata_json
            FROM user_accounts
            WHERE lower(username) = lower(%s)
            """,
            (username,),
        ).fetchone()
        return _account_from_row(row) if row else None

    def update_account(self, account_id: str, updates: dict[str, Any]) -> AccountRecord | None:
        record = self.get_account(account_id)
        if record is None:
            return None
        next_values = {
            "display_name": updates.get("display_name", record.display_name),
            "role": updates.get("role", record.role),
            "department": updates.get("department", record.department),
            "phone": updates.get("phone", record.phone),
            "email": updates.get("email", record.email),
            "password_hash": updates.get("password_hash", record.password_hash),
            "status": updates.get("status", record.status),
            "metadata": updates.get("metadata", record.metadata),
        }
        self.connection.execute(
            """
            UPDATE user_accounts
            SET display_name = %s,
                role = %s,
                department = %s,
                phone = %s,
                email = %s,
                password_hash = %s,
                status = %s,
                metadata_json = %s,
                updated_at = %s
            WHERE account_id = %s
            """,
            (
                next_values["display_name"],
                next_values["role"],
                next_values["department"],
                next_values["phone"],
                next_values["email"],
                next_values["password_hash"],
                next_values["status"],
                _jsonb(next_values["metadata"]),
                utc_now(),
                account_id,
            ),
        )
        self.connection.commit()
        return self.get_account(account_id)

    def mark_account_login(self, account_id: str) -> AccountRecord | None:
        record = self.get_account(account_id)
        if record is None:
            return None
        self.connection.execute(
            """
            UPDATE user_accounts
            SET last_login_at = %s,
                updated_at = %s
            WHERE account_id = %s
            """,
            (utc_now(), utc_now(), account_id),
        )
        self.connection.commit()
        return self.get_account(account_id)

    def create_uploaded_file(
        self,
        *,
        business_type: str,
        file_name: str,
        storage_uri: str,
        file_id: str | None = None,
        project_id: str | None = None,
        file_ext: str | None = None,
        mime_type: str | None = None,
        file_size: int | None = None,
        page_count: int | None = None,
        sha256: str | None = None,
        status: str = "uploaded",
        related_source_bid_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UploadedFileRecord:
        now = utc_now()
        resolved_ext = file_ext if file_ext is not None else _suffix(file_name)
        record = UploadedFileRecord(
            file_id=file_id or _new_id("F"),
            project_id=project_id,
            business_type=business_type,
            file_name=file_name,
            file_ext=resolved_ext,
            mime_type=mime_type,
            file_size=file_size,
            page_count=page_count,
            storage_uri=storage_uri,
            sha256=sha256,
            status=status,
            related_source_bid_id=related_source_bid_id,
            created_at=now,
            updated_at=now,
            metadata=metadata,
        )
        self.connection.execute(
            """
            INSERT INTO uploaded_files(
              file_id, project_id, business_type, file_name, file_ext, mime_type,
              file_size, page_count, storage_uri, sha256, status, related_source_bid_id,
              created_at, updated_at, metadata_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.file_id,
                record.project_id,
                record.business_type,
                record.file_name,
                record.file_ext,
                record.mime_type,
                record.file_size,
                record.page_count,
                record.storage_uri,
                record.sha256,
                record.status,
                record.related_source_bid_id,
                record.created_at,
                record.updated_at,
                _jsonb(record.metadata),
            ),
        )
        self.connection.commit()
        return record

    def list_uploaded_files(
        self,
        *,
        project_id: str | None = None,
        business_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[UploadedFileRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id is not None:
            clauses.append("project_id = %s")
            params.append(project_id)
        if business_type is not None:
            clauses.append("business_type = %s")
            params.append(business_type)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            f"""
            SELECT file_id, project_id, business_type, file_name, file_ext, mime_type,
                   file_size, page_count, storage_uri, sha256, status, related_source_bid_id,
                   created_at, updated_at, metadata_json
            FROM uploaded_files
            {where_sql}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
        ).fetchall()
        return [_uploaded_file_from_row(row) for row in rows]

    def get_uploaded_file(self, file_id: str) -> UploadedFileRecord | None:
        row = self.connection.execute(
            """
            SELECT file_id, project_id, business_type, file_name, file_ext, mime_type,
                   file_size, page_count, storage_uri, sha256, status, related_source_bid_id,
                   created_at, updated_at, metadata_json
            FROM uploaded_files
            WHERE file_id = %s
            """,
            (file_id,),
        ).fetchone()
        return _uploaded_file_from_row(row) if row else None

    def delete_uploaded_file(self, *, project_id: str, file_id: str) -> UploadedFileRecord | None:
        record = self.get_uploaded_file(file_id)
        if record is None or record.project_id != project_id:
            return None
        self.connection.execute(
            "DELETE FROM uploaded_files WHERE project_id = %s AND file_id = %s",
            (project_id, file_id),
        )
        self.connection.commit()
        return record

    def create_job(
        self,
        *,
        job_type: str,
        job_id: str | None = None,
        project_id: str | None = None,
        status: str = "pending",
        message: str | None = None,
        config_snapshot: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> JobRecord:
        now = utc_now()
        record = JobRecord(
            job_id=job_id or _new_id("JOB"),
            project_id=project_id,
            job_type=job_type,
            status=status,
            progress_total=None,
            progress_completed=None,
            progress_failed=None,
            progress_percent=None,
            message=message,
            result_ref=None,
            error_code=None,
            error_message=None,
            started_at=now if status == "running" else None,
            ended_at=None,
            created_at=now,
            updated_at=now,
            config_snapshot=config_snapshot,
            metadata=metadata,
        )
        self.connection.execute(
            """
            INSERT INTO jobs(
              job_id, project_id, job_type, status, progress_total, progress_completed,
              progress_failed, progress_percent, message, result_ref, error_code,
              error_message, started_at, ended_at, created_at, updated_at,
              config_snapshot_json, metadata_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.job_id,
                record.project_id,
                record.job_type,
                record.status,
                record.progress_total,
                record.progress_completed,
                record.progress_failed,
                record.progress_percent,
                record.message,
                record.result_ref,
                record.error_code,
                record.error_message,
                record.started_at,
                record.ended_at,
                record.created_at,
                record.updated_at,
                _jsonb(record.config_snapshot),
                _jsonb(record.metadata),
            ),
        )
        self.connection.commit()
        return record

    def get_job(self, job_id: str) -> JobRecord | None:
        row = self.connection.execute(
            """
            SELECT job_id, project_id, job_type, status, progress_total, progress_completed,
                   progress_failed, progress_percent, message, result_ref, error_code,
                   error_message, started_at, ended_at, created_at, updated_at,
                   config_snapshot_json, metadata_json
            FROM jobs
            WHERE job_id = %s
            """,
            (job_id,),
        ).fetchone()
        return _job_from_row(row) if row else None

    def list_jobs(
        self,
        *,
        project_id: str | None = None,
        job_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[JobRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id is not None:
            clauses.append("project_id = %s")
            params.append(project_id)
        if job_type is not None:
            clauses.append("job_type = %s")
            params.append(job_type)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            f"""
            SELECT job_id, project_id, job_type, status, progress_total, progress_completed,
                   progress_failed, progress_percent, message, result_ref, error_code,
                   error_message, started_at, ended_at, created_at, updated_at,
                   config_snapshot_json, metadata_json
            FROM jobs
            {where_sql}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
        ).fetchall()
        return [_job_from_row(row) for row in rows]

    def update_job_progress(
        self,
        job_id: str,
        *,
        status: str,
        progress_total: int | None = None,
        progress_completed: int | None = None,
        progress_failed: int | None = None,
        progress_percent: float | None = None,
        message: str | None = None,
        result_ref: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE jobs
            SET status = %s,
                progress_total = %s,
                progress_completed = %s,
                progress_failed = %s,
                progress_percent = %s,
                message = %s,
                result_ref = %s,
                error_code = %s,
                error_message = %s,
                started_at = COALESCE(%s, started_at),
                ended_at = COALESCE(%s, ended_at),
                metadata_json = COALESCE(%s, metadata_json),
                updated_at = %s
            WHERE job_id = %s
            """,
            (
                status,
                progress_total,
                progress_completed,
                progress_failed,
                progress_percent,
                message,
                result_ref,
                error_code,
                error_message,
                started_at,
                ended_at,
                _jsonb(metadata) if metadata is not None else None,
                utc_now(),
                job_id,
            ),
        )
        self.connection.commit()

    def delete_project(self, project_id: str) -> bool:
        if self.get_project(project_id) is None:
            return False
        for table in [
            "project_material_usage",
            "review_items",
            "review_sessions",
            "document_versions",
            "chapter_generation_tasks",
            "chapter_generation_runs",
            "technical_bid_outlines",
            "tender_parse_results",
            "jobs",
            "uploaded_files",
        ]:
            self.connection.execute(f"DELETE FROM {table} WHERE project_id = %s", (project_id,))
        self.connection.execute("DELETE FROM projects WHERE project_id = %s", (project_id,))
        self.connection.commit()
        return True


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def _suffix(file_name: str) -> str | None:
    suffix = Path(file_name).suffix.lower().lstrip(".")
    return suffix or None


def _jsonb(value: dict[str, Any] | list[Any] | None) -> Any:
    if value is None:
        return None
    try:
        from psycopg.types.json import Jsonb  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return json.dumps(value, ensure_ascii=False)
    return Jsonb(value)


def _project_from_row(row: Any) -> ProjectRecord:
    return ProjectRecord(
        project_id=_row(row, "project_id"),
        name=_row(row, "name"),
        description=_row(row, "description"),
        project_type=_row(row, "project_type"),
        stage=_row(row, "stage"),
        stage_label=_row(row, "stage_label"),
        created_at=_row(row, "created_at"),
        updated_at=_row(row, "updated_at"),
        metadata=_metadata(row, "metadata_json"),
    )


def _uploaded_file_from_row(row: Any) -> UploadedFileRecord:
    return UploadedFileRecord(
        file_id=_row(row, "file_id"),
        project_id=_row(row, "project_id"),
        business_type=_row(row, "business_type"),
        file_name=_row(row, "file_name"),
        file_ext=_row(row, "file_ext"),
        mime_type=_row(row, "mime_type"),
        file_size=_row(row, "file_size"),
        page_count=_row(row, "page_count"),
        storage_uri=_row(row, "storage_uri"),
        sha256=_row(row, "sha256"),
        status=_row(row, "status"),
        related_source_bid_id=_row(row, "related_source_bid_id"),
        created_at=_row(row, "created_at"),
        updated_at=_row(row, "updated_at"),
        metadata=_metadata(row, "metadata_json"),
    )


def _job_from_row(row: Any) -> JobRecord:
    return JobRecord(
        job_id=_row(row, "job_id"),
        project_id=_row(row, "project_id"),
        job_type=_row(row, "job_type"),
        status=_row(row, "status"),
        progress_total=_row(row, "progress_total"),
        progress_completed=_row(row, "progress_completed"),
        progress_failed=_row(row, "progress_failed"),
        progress_percent=_row(row, "progress_percent"),
        message=_row(row, "message"),
        result_ref=_row(row, "result_ref"),
        error_code=_row(row, "error_code"),
        error_message=_row(row, "error_message"),
        started_at=_row(row, "started_at"),
        ended_at=_row(row, "ended_at"),
        created_at=_row(row, "created_at"),
        updated_at=_row(row, "updated_at"),
        config_snapshot=_metadata(row, "config_snapshot_json"),
        metadata=_metadata(row, "metadata_json"),
    )


def _account_from_row(row: Any) -> AccountRecord:
    return AccountRecord(
        account_id=_row(row, "account_id"),
        username=_row(row, "username"),
        display_name=_row(row, "display_name"),
        role=_row(row, "role"),
        department=_row(row, "department"),
        phone=_row(row, "phone"),
        email=_row(row, "email"),
        password_hash=_row(row, "password_hash") if _has_row_key(row, "password_hash") else None,
        status=_row(row, "status"),
        created_at=_row(row, "created_at"),
        updated_at=_row(row, "updated_at"),
        last_login_at=_row(row, "last_login_at"),
        metadata=_metadata(row, "metadata_json"),
    )


def _metadata(row: Any, key: str) -> dict[str, Any] | None:
    value = _row(row, key)
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        return decoded if isinstance(decoded, dict) else {"value": decoded}
    return dict(value)


def _row(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row[key]
    return getattr(row, key)


def _has_row_key(row: Any, key: str) -> bool:
    if isinstance(row, dict):
        return key in row
    return hasattr(row, key)
