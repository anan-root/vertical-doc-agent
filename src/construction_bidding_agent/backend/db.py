"""PostgreSQL 数据库迁移工具。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


class DatabaseConnection(Protocol):
    """迁移工具需要的最小数据库连接协议。"""

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> Any:
        ...

    def commit(self) -> None:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True, slots=True)
class MigrationResult:
    applied_versions: tuple[str, ...]
    skipped_versions: tuple[str, ...]


def connect_postgres(database_url: str, *, connect_timeout_seconds: int = 3) -> DatabaseConnection:
    """创建 PostgreSQL 连接。

    当前项目不强制安装 PostgreSQL Python 驱动；真正接后端服务时安装 `psycopg`
    后即可使用该入口。
    """

    try:
        import psycopg  # type: ignore[import-not-found]
        from psycopg.rows import dict_row  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 PostgreSQL 驱动，请先安装 psycopg。") from exc
    return psycopg.connect(
        database_url,
        row_factory=dict_row,
        connect_timeout=connect_timeout_seconds,
    )


def apply_migrations(database_url: str, migrations_dir: str | Path) -> MigrationResult:
    """按文件名顺序应用未执行过的 PostgreSQL migration。"""

    connection = connect_postgres(database_url)
    try:
        return apply_migrations_with_connection(connection, migrations_dir)
    finally:
        connection.close()


def apply_migrations_with_connection(
    connection: DatabaseConnection,
    migrations_dir: str | Path,
) -> MigrationResult:
    """使用已建立连接应用迁移，便于后续连接池或测试替换。"""

    migration_paths = migration_files(migrations_dir)
    applied: list[str] = []
    skipped: list[str] = []
    ensure_migration_table(connection)
    applied_versions = set(read_applied_versions(connection))
    for migration_path in migration_paths:
        version = migration_path.stem
        if version in applied_versions:
            skipped.append(version)
            continue
        sql = migration_path.read_text(encoding="utf-8")
        connection.execute(sql)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (%s, %s)",
            (version, utc_now()),
        )
        applied.append(version)
    connection.commit()
    return MigrationResult(tuple(applied), tuple(skipped))


def migration_files(migrations_dir: str | Path) -> list[Path]:
    """读取 migration 文件列表。"""

    path = Path(migrations_dir)
    if not path.exists():
        raise FileNotFoundError(f"Migrations directory does not exist: {path}")
    return sorted(p for p in path.glob("*.sql") if p.is_file())


def ensure_migration_table(connection: DatabaseConnection) -> None:
    """确保迁移版本表存在。"""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version TEXT PRIMARY KEY,
          applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def read_applied_versions(connection: DatabaseConnection) -> tuple[str, ...]:
    """读取已应用的 migration 版本。"""

    rows = connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    versions: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            versions.append(str(row["version"]))
        else:
            versions.append(str(row[0]))
    return tuple(versions)


def utc_now() -> datetime:
    """返回带时区的 UTC 时间，供 PostgreSQL TIMESTAMPTZ 写入。"""

    return datetime.now(timezone.utc)
