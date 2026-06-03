"""PostgreSQL 不可用时的开发期本地状态存储。"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .repository import JobRecord, ProjectRecord, UploadedFileRecord


class DevJsonStore:
    """仅用于本地前端联调的 JSON 存储，不作为正式数据方案。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"projects": [], "files": [], "jobs": [], "accounts": []}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data.setdefault("projects", [])
        data.setdefault("files", [])
        data.setdefault("jobs", [])
        data.setdefault("accounts", [])
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def append_project(self, record: ProjectRecord) -> None:
        data = self.load()
        data["projects"].append(_record_dict(record))
        self.save(data)

    def list_projects(self) -> list[dict[str, Any]]:
        return self.load()["projects"]

    def list_accounts(self) -> list[dict[str, Any]]:
        return sorted(self.load()["accounts"], key=lambda item: str(item.get("created_at") or ""))

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        return next((item for item in self.load()["accounts"] if item.get("account_id") == account_id), None)

    def get_account_by_username(self, username: str) -> dict[str, Any] | None:
        normalized = str(username or "").strip().lower()
        return next((item for item in self.load()["accounts"] if str(item.get("username") or "").strip().lower() == normalized), None)

    def append_account(self, record: dict[str, Any]) -> dict[str, Any]:
        data = self.load()
        data["accounts"].append(_json_ready(record))
        self.save(data)
        return record

    def update_account(self, account_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        data = self.load()
        for item in data["accounts"]:
            if item.get("account_id") != account_id:
                continue
            item.update(_json_ready(updates))
            self.save(data)
            return item
        return None

    def mark_account_login(self, account_id: str, timestamp: str) -> dict[str, Any] | None:
        return self.update_account(account_id, {"last_login_at": timestamp, "updated_at": timestamp})

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        return next((item for item in self.load()["projects"] if item["project_id"] == project_id), None)

    def append_file(self, record: UploadedFileRecord) -> None:
        data = self.load()
        data["files"].append(_record_dict(record))
        self.save(data)

    def list_files(self, project_id: str) -> list[dict[str, Any]]:
        return [item for item in self.load()["files"] if item["project_id"] == project_id]

    def append_job(self, record: JobRecord) -> None:
        data = self.load()
        data["jobs"].append(_record_dict(record))
        self.save(data)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return next((item for item in self.load()["jobs"] if item["job_id"] == job_id), None)

    def update_job(self, job_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        data = self.load()
        for item in data["jobs"]:
            if item["job_id"] != job_id:
                continue
            item.update(_json_ready(updates))
            self.save(data)
            return item
        return None

    def delete_project(self, project_id: str) -> bool:
        data = self.load()
        before = len(data["projects"])
        data["projects"] = [item for item in data["projects"] if item["project_id"] != project_id]
        data["files"] = [item for item in data["files"] if item.get("project_id") != project_id]
        data["jobs"] = [item for item in data["jobs"] if item.get("project_id") != project_id]
        self.save(data)
        return len(data["projects"]) < before

    def delete_file(self, project_id: str, file_id: str) -> dict[str, Any] | None:
        data = self.load()
        deleted = None
        remaining = []
        for item in data["files"]:
            if item.get("project_id") == project_id and item.get("file_id") == file_id:
                deleted = item
                continue
            remaining.append(item)
        if deleted is None:
            return None
        data["files"] = remaining
        self.save(data)
        return deleted


def _record_dict(record: Any) -> dict[str, Any]:
    data = asdict(record)
    return _json_ready(data)


def _json_ready(data: dict[str, Any]) -> dict[str, Any]:
    for key, value in list(data.items()):
        if isinstance(value, datetime):
            data[key] = value.isoformat()
    return data
