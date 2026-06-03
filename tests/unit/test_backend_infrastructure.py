import pytest

from construction_bidding_agent.backend.config import backend_settings
from construction_bidding_agent.backend.db import migration_files
from construction_bidding_agent.backend.knowledge_base import (
    AGGREGATE_INDEX_CANDIDATES,
    normalize_excellent_bid_manifest,
)
from construction_bidding_agent.backend.migrate import main as migrate_main
from construction_bidding_agent.backend.storage import LocalStorageService


def _init_sql() -> str:
    return (migration_files("migrations")[0]).read_text(encoding="utf-8")


def test_backend_settings_uses_database_url_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/bidding")
    monkeypatch.setenv("APP_STORAGE_ROOT", "data-test")

    settings = backend_settings()

    assert settings.database_url == "postgresql://user:pass@localhost:5432/bidding"
    assert settings.storage_root.as_posix() == "data-test"


def test_backend_settings_reads_runtime_environment_flags(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOW_DEV_JSON_FALLBACK", "false")

    settings = backend_settings()

    assert settings.app_env == "production"
    assert settings.allow_dev_json_fallback is False


def test_excellent_bid_manifest_prefers_two_word_source_index():
    assert AGGREGATE_INDEX_CANDIDATES[0] == "excellent_bid_material_library_two_word_sources.json"


def test_excellent_bid_manifest_enriches_legacy_source_metadata():
    manifest = normalize_excellent_bid_manifest(
        {
            "schema_version": "excellent_bid_library_manifest_v1",
            "sources": [
                {
                    "source_bid_id": "SRC0001",
                    "title": "总体施工方案",
                    "status": "已入库",
                    "slice_count": 2,
                    "table_count": 3,
                    "image_count": 4,
                }
            ],
        }
    )

    source = manifest["sources"][0]
    assert source["project_type"] == "building_construction"
    assert source["project_type_label"] == "房建"
    assert source["bid_type"] == "construction_technical_bid"
    assert source["bid_type_label"] == "施工技术标"
    assert source["status"] == "ready"
    assert source["status_label"] == "已入库"
    assert source["allow_image_reuse"] is True
    assert source["desensitized_confirmed"] is True
    assert manifest["source_count"] == 1
    assert manifest["image_count"] == 4


def test_migration_files_are_sorted(tmp_path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "002_next.sql").write_text("-- next", encoding="utf-8")
    (migrations_dir / "001_init.sql").write_text("-- init", encoding="utf-8")

    assert [path.name for path in migration_files(migrations_dir)] == ["001_init.sql", "002_next.sql"]


def test_migrate_cli_lists_migrations(capsys):
    exit_code = migrate_main(["--list", "--migrations-dir", "migrations"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "可用 migration" in output
    assert "001_init.sql" in output


def test_real_init_migration_uses_postgresql_types():
    sql = _init_sql()

    assert "TIMESTAMPTZ" in sql
    assert "JSONB" in sql
    assert "BOOLEAN NOT NULL DEFAULT FALSE" in sql
    assert "DOUBLE PRECISION" in sql
    assert "app.sqlite3" not in sql
    assert "sqlite" not in sql.lower()


def test_real_init_migration_defines_core_tables():
    sql = _init_sql()

    for table_name in [
        "projects",
        "uploaded_files",
        "jobs",
        "document_versions",
        "review_items",
        "model_provider_configs",
        "llm_task_profiles",
        "excellent_bid_sources",
        "excellent_bid_images",
        "excellent_bid_image_groups",
        "project_material_usage",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in sql


def test_real_init_migration_enforces_project_type_constraint():
    sql = _init_sql()

    assert "project_type TEXT CHECK (project_type IN ('construction', 'epc') OR project_type IS NULL)" in sql


def test_local_storage_creates_expected_layout(tmp_path):
    storage = LocalStorageService(tmp_path)

    storage.ensure_layout()

    assert (tmp_path / "projects").is_dir()
    assert (tmp_path / "knowledge_base" / "excellent_bids" / "originals").is_dir()
    assert (tmp_path / "app" / "tmp").is_dir()


def test_local_storage_saves_project_upload_and_resolves_uri(tmp_path):
    storage = LocalStorageService(tmp_path)

    stored = storage.save_project_upload("P-001", "招标文件.docx", b"docx-bytes")

    assert stored.storage_uri == "local://projects/P-001/uploads/招标文件.docx"
    assert stored.path.read_bytes() == b"docx-bytes"
    assert storage.read_bytes(stored.storage_uri) == b"docx-bytes"
    assert storage.resolve_local_path(stored.storage_uri) == stored.path


def test_local_storage_saves_knowledge_base_files(tmp_path):
    storage = LocalStorageService(tmp_path)

    original = storage.save_knowledge_base_original("SRC0001", "总体施工方案.docx", b"original")
    image = storage.save_knowledge_base_extracted("SRC0001", "images", "image001.png", b"image")

    assert original.storage_uri == "local://knowledge_base/excellent_bids/originals/SRC0001/总体施工方案.docx"
    assert image.storage_uri == "local://knowledge_base/excellent_bids/extracted/SRC0001/images/image001.png"
    assert image.size == 5


def test_local_storage_rejects_path_traversal(tmp_path):
    storage = LocalStorageService(tmp_path)

    with pytest.raises(ValueError):
        storage.save_project_upload("../P-001", "x.docx", b"x")
    with pytest.raises(ValueError):
        storage.save_project_artifact("P-001", "documents", "../x.docx", b"x")
    with pytest.raises(ValueError):
        storage.resolve_local_path("local://../outside.txt")
    with pytest.raises(ValueError):
        storage.resolve_local_path("s3://bucket/file.txt")
