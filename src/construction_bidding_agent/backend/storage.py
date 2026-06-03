"""本地文件存储适配器。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


LOCAL_URI_PREFIX = "local://"
PROJECT_CATEGORIES = {"uploads", "parse", "outline", "generation", "documents", "reports", "review"}
KNOWLEDGE_BASE_CATEGORIES = {"images", "tables", "chunks", "reports", "indexes"}


@dataclass(frozen=True, slots=True)
class StoredFile:
    storage_uri: str
    path: Path
    size: int


class LocalStorageService:
    """以 data/ 为根目录的本地文件系统存储实现。"""

    def __init__(self, storage_root: str | Path = "data") -> None:
        self.storage_root = Path(storage_root)
        self.storage_root.mkdir(parents=True, exist_ok=True)

    def ensure_layout(self) -> None:
        """创建 MVP 约定的顶层目录。"""

        for relative in [
            "raw",
            "projects",
            "knowledge_base/excellent_bids/originals",
            "knowledge_base/excellent_bids/extracted",
            "knowledge_base/excellent_bids/indexes",
            "app/exports",
            "app/tmp",
        ]:
            (self.storage_root / relative).mkdir(parents=True, exist_ok=True)

    def save_project_upload(self, project_id: str, file_name: str, source: bytes | BinaryIO | str | Path) -> StoredFile:
        return self._save(
            relative_path=Path("projects") / _safe_segment(project_id) / "uploads" / _safe_file_name(file_name),
            source=source,
        )

    def save_project_artifact(
        self,
        project_id: str,
        category: str,
        file_name: str,
        source: bytes | BinaryIO | str | Path,
    ) -> StoredFile:
        if category not in PROJECT_CATEGORIES:
            raise ValueError(f"Unsupported project artifact category: {category}")
        return self._save(
            relative_path=Path("projects")
            / _safe_segment(project_id)
            / category
            / _safe_file_name(file_name),
            source=source,
        )

    def save_knowledge_base_original(
        self,
        source_bid_id: str,
        file_name: str,
        source: bytes | BinaryIO | str | Path,
    ) -> StoredFile:
        return self._save(
            relative_path=Path("knowledge_base")
            / "excellent_bids"
            / "originals"
            / _safe_segment(source_bid_id)
            / _safe_file_name(file_name),
            source=source,
        )

    def save_knowledge_base_extracted(
        self,
        source_bid_id: str,
        category: str,
        file_name: str,
        source: bytes | BinaryIO | str | Path,
    ) -> StoredFile:
        if category not in KNOWLEDGE_BASE_CATEGORIES:
            raise ValueError(f"Unsupported knowledge-base category: {category}")
        return self._save(
            relative_path=Path("knowledge_base")
            / "excellent_bids"
            / "extracted"
            / _safe_segment(source_bid_id)
            / category
            / _safe_file_name(file_name),
            source=source,
        )

    def open_file(self, storage_uri: str, mode: str = "rb"):
        path = self.resolve_local_path(storage_uri)
        return path.open(mode)

    def read_bytes(self, storage_uri: str) -> bytes:
        return self.resolve_local_path(storage_uri).read_bytes()

    def delete_file(self, storage_uri: str) -> None:
        path = self.resolve_local_path(storage_uri)
        path.unlink(missing_ok=True)

    def resolve_local_path(self, storage_uri: str) -> Path:
        if not storage_uri.startswith(LOCAL_URI_PREFIX):
            raise ValueError(f"Unsupported storage URI: {storage_uri}")
        relative = storage_uri.removeprefix(LOCAL_URI_PREFIX)
        return _safe_join(self.storage_root, Path(relative))

    def to_uri(self, path: str | Path) -> str:
        absolute = Path(path).resolve()
        root = self.storage_root.resolve()
        try:
            relative = absolute.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Path is outside storage root: {path}") from exc
        return LOCAL_URI_PREFIX + relative.as_posix()

    def _save(self, *, relative_path: Path, source: bytes | BinaryIO | str | Path) -> StoredFile:
        target = _safe_join(self.storage_root, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(source, bytes):
            target.write_bytes(source)
        elif isinstance(source, (str, Path)):
            source_path = Path(source)
            with source_path.open("rb") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        else:
            with target.open("wb") as dst:
                shutil.copyfileobj(source, dst)
        return StoredFile(storage_uri=self.to_uri(target), path=target, size=target.stat().st_size)


def _safe_join(root: Path, relative_path: Path) -> Path:
    if relative_path.is_absolute():
        raise ValueError(f"Storage relative path must not be absolute: {relative_path}")
    root_resolved = root.resolve()
    candidate = (root_resolved / relative_path).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"Path escapes storage root: {relative_path}") from exc
    return candidate


def _safe_segment(value: str) -> str:
    text = value.strip()
    if not text or text in {".", ".."} or "/" in text or "\\" in text:
        raise ValueError(f"Invalid path segment: {value!r}")
    return text


def _safe_file_name(value: str) -> str:
    return _safe_segment(value)
