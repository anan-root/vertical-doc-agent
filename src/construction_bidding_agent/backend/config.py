"""后端应用配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_STORAGE_ROOT = Path("data")
DEFAULT_MIGRATIONS_DIR = Path("migrations")
DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/construction_bidding_agent"


@dataclass(frozen=True, slots=True)
class BackendSettings:
    storage_root: Path = DEFAULT_STORAGE_ROOT
    database_url: str = DEFAULT_DATABASE_URL
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR
    secret_key: str | None = None
    app_env: str = "local"
    allow_dev_json_fallback: bool = True
    app_public_url: str = "http://localhost:8000"
    backend_internal_url: str = "http://localhost:8000"
    onlyoffice_public_url: str = "http://localhost/onlyoffice"
    onlyoffice_internal_url: str = "http://onlyoffice"
    onlyoffice_jwt_secret: str | None = None


def backend_settings(
    *,
    storage_root: str | Path | None = None,
    database_url: str | None = None,
    migrations_dir: str | Path | None = None,
    secret_key: str | None = None,
) -> BackendSettings:
    """从显式参数与环境变量合成后端配置。"""

    final_storage_root = _path_value(storage_root, "APP_STORAGE_ROOT", DEFAULT_STORAGE_ROOT)
    final_database_url = _str_value(database_url, "DATABASE_URL", DEFAULT_DATABASE_URL)
    final_migrations_dir = _path_value(migrations_dir, "APP_MIGRATIONS_DIR", DEFAULT_MIGRATIONS_DIR)
    return BackendSettings(
        storage_root=final_storage_root,
        database_url=final_database_url,
        migrations_dir=final_migrations_dir,
        secret_key=secret_key if secret_key is not None else _env("APP_SECRET_KEY"),
        app_env=_str_value(None, "APP_ENV", "local").lower(),
        allow_dev_json_fallback=_bool_value("ALLOW_DEV_JSON_FALLBACK", default=True),
        app_public_url=_str_value(None, "APP_PUBLIC_URL", "http://localhost:8000"),
        backend_internal_url=_str_value(None, "BACKEND_INTERNAL_URL", "http://localhost:8000"),
        onlyoffice_public_url=_str_value(None, "ONLYOFFICE_PUBLIC_URL", "http://localhost/onlyoffice"),
        onlyoffice_internal_url=_str_value(None, "ONLYOFFICE_INTERNAL_URL", "http://onlyoffice"),
        onlyoffice_jwt_secret=_env("ONLYOFFICE_JWT_SECRET"),
    )


def load_env_file(path: str | Path = ".env") -> None:
    """加载 .env 文件，已存在的环境变量不覆盖。"""

    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _path_value(value: str | Path | None, env_name: str, default: Path) -> Path:
    raw = value if value is not None else _env(env_name)
    return Path(raw) if raw else default


def _str_value(value: str | None, env_name: str, default: str) -> str:
    raw = value if value is not None else _env(env_name)
    return raw.strip() if raw else default


def _bool_value(env_name: str, *, default: bool) -> bool:
    raw = _env(env_name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return value.strip()
