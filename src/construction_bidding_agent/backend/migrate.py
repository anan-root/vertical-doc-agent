"""PostgreSQL migration 命令行入口。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from .config import backend_settings, load_env_file
from .db import apply_migrations, connect_postgres, migration_files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="管理 PostgreSQL 数据库迁移。")
    parser.add_argument("--env-file", default=".env", help="要加载的 .env 文件路径，默认 .env。")
    parser.add_argument("--database-url", help="PostgreSQL DATABASE_URL，默认读取环境变量。")
    parser.add_argument("--migrations-dir", help="migration 文件目录，默认 migrations。")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list", action="store_true", help="列出 migration 文件，不连接数据库。")
    action.add_argument("--check", action="store_true", help="检查数据库连接是否可用。")
    action.add_argument("--apply", action="store_true", help="应用未执行过的 migration。")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_env_file(args.env_file)
    settings = backend_settings(
        database_url=args.database_url,
        migrations_dir=args.migrations_dir,
    )

    if args.check:
        connection = connect_postgres(settings.database_url)
        try:
            connection.execute("SELECT 1")
        finally:
            connection.close()
        print("PostgreSQL 连接正常。")
        return 0

    if args.apply:
        result = apply_migrations(settings.database_url, settings.migrations_dir)
        print(f"已应用 migration：{', '.join(result.applied_versions) or '无'}")
        print(f"已跳过 migration：{', '.join(result.skipped_versions) or '无'}")
        return 0

    migrations = migration_files(settings.migrations_dir)
    print("可用 migration：")
    for migration_path in migrations:
        print(f"- {migration_path.name}")
    if not migrations:
        print("- 无")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
