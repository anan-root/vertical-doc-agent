from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from construction_bidding_agent.backend.repository import BackendRepository


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...] | None]] = []
        self.rows: list[dict[str, Any]] = []
        self.commit_count = 0
        self.closed = False

    def execute(self, query: str, params: tuple[Any, ...] | None = None):
        self.calls.append((query, params))
        return FakeCursor(self.rows)

    def commit(self) -> None:
        self.commit_count += 1

    def close(self) -> None:
        self.closed = True


def test_create_project_inserts_record():
    connection = FakeConnection()
    repo = BackendRepository(connection)

    project = repo.create_project(
        project_id="P-001",
        name="测试项目",
        project_type="construction",
        metadata={"source": "unit"},
    )

    assert project.project_id == "P-001"
    assert project.name == "测试项目"
    assert connection.commit_count == 1
    assert "INSERT INTO projects" in connection.calls[0][0]
    assert connection.calls[0][1][0] == "P-001"


def test_list_projects_maps_rows_to_records():
    now = datetime.now(timezone.utc)
    connection = FakeConnection()
    connection.rows = [
        {
            "project_id": "P-001",
            "name": "测试项目",
            "description": None,
            "project_type": "epc",
            "stage": "draft",
            "stage_label": None,
            "created_at": now,
            "updated_at": now,
            "metadata_json": {"k": "v"},
        }
    ]
    repo = BackendRepository(connection)

    projects = repo.list_projects()

    assert len(projects) == 1
    assert projects[0].project_id == "P-001"
    assert projects[0].metadata == {"k": "v"}


def test_create_uploaded_file_infers_extension():
    connection = FakeConnection()
    repo = BackendRepository(connection)

    uploaded = repo.create_uploaded_file(
        file_id="F-001",
        project_id="P-001",
        business_type="tender_document",
        file_name="招标文件.docx",
        storage_uri="local://projects/P-001/uploads/招标文件.docx",
    )

    assert uploaded.file_ext == "docx"
    assert connection.commit_count == 1
    assert "INSERT INTO uploaded_files" in connection.calls[0][0]


def test_create_and_update_job_use_expected_sql():
    connection = FakeConnection()
    repo = BackendRepository(connection)

    job = repo.create_job(job_id="JOB-001", project_id="P-001", job_type="tender_parse")
    repo.update_job_progress(
        "JOB-001",
        status="succeeded",
        progress_total=10,
        progress_completed=10,
        progress_failed=0,
        progress_percent=100,
        result_ref="local://projects/P-001/parse/report.md",
    )

    assert job.status == "pending"
    assert connection.commit_count == 2
    assert "INSERT INTO jobs" in connection.calls[0][0]
    assert "UPDATE jobs" in connection.calls[1][0]
    assert connection.calls[1][1][-1] == "JOB-001"


def test_list_jobs_filters_by_project_id():
    now = datetime.now(timezone.utc)
    connection = FakeConnection()
    connection.rows = [
        {
            "job_id": "JOB-001",
            "project_id": "P-001",
            "job_type": "tender_parse",
            "status": "pending",
            "progress_total": None,
            "progress_completed": None,
            "progress_failed": None,
            "progress_percent": None,
            "message": "已创建",
            "result_ref": None,
            "error_code": None,
            "error_message": None,
            "started_at": None,
            "ended_at": None,
            "created_at": now,
            "updated_at": now,
            "config_snapshot_json": None,
            "metadata_json": {"source": "unit"},
        }
    ]
    repo = BackendRepository(connection)

    jobs = repo.list_jobs(project_id="P-001")

    assert len(jobs) == 1
    assert jobs[0].job_type == "tender_parse"
    assert "FROM jobs" in connection.calls[0][0]
    assert "project_id = %s" in connection.calls[0][0]
    assert connection.calls[0][1][0] == "P-001"
