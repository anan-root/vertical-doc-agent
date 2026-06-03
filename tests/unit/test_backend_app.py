from zipfile import ZIP_DEFLATED, ZipFile
import base64
import json
import time

from fastapi.testclient import TestClient

from construction_bidding_agent.backend.app import (
    create_app,
    _ai_review_report_summary,
    _generation_report_summary,
    _score_point_coverage_summary,
)
from construction_bidding_agent.backend.workflow_executor import (
    _extract_score_points,
    _hybrid_tender_parse_run,
    _select_chapter_packages,
)


def _use_lightweight_parse(monkeypatch):
    monkeypatch.setenv("TENDER_PARSE_MODE", "lightweight")


def test_frontend_index_is_served():
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert "技术标智能编制工作台" in response.text


def test_model_config_api_returns_runtime_config():
    client = TestClient(create_app())

    response = client.get("/api/v1/model-config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "model" in payload["data"]
    assert "tasks" in payload["data"]


def test_health_reports_dev_json_runtime_when_database_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOW_DEV_JSON_FALLBACK", "true")
    client = TestClient(create_app())

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["runtime_storage"] == "dev_json"
    assert data["allow_dev_json_fallback"] is True


def test_account_management_dev_store_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    client = TestClient(create_app())

    seeded = client.get("/api/v1/accounts")
    created = client.post(
        "/api/v1/accounts",
        json={
            "username": "bid.user",
            "password": "Passw0rd!",
            "display_name": "编标人员",
            "role": "bid_staff",
            "department": "投标管理部",
            "phone": "13800000000",
            "email": "bid.user@example.com",
        },
    )
    duplicate = client.post(
        "/api/v1/accounts",
        json={"username": "BID.USER", "password": "Passw0rd!", "display_name": "重复账号", "role": "bid_staff"},
    )
    account_id = created.json()["data"]["account_id"]
    disabled = client.post(f"/api/v1/accounts/{account_id}/status", json={"status": "disabled"})
    listed = client.get("/api/v1/accounts")

    assert seeded.status_code == 200
    assert seeded.json()["data"]["total"] == 0
    assert created.status_code == 200
    assert created.json()["data"]["role_label"] == "编标人员"
    assert "password_hash" not in (created.json()["data"].get("metadata") or {})
    assert duplicate.status_code == 409
    assert disabled.status_code == 200
    assert disabled.json()["data"]["status_label"] == "停用"
    assert listed.json()["data"]["total"] == 1


def test_password_auth_flow_protects_workbench(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", "Passw0rd!")
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    client = TestClient(create_app())

    blocked = client.get("/api/v1/projects")
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "Passw0rd!"})
    me = client.get("/api/v1/auth/me")
    accounts = client.get("/api/v1/accounts")
    logout = client.post("/api/v1/auth/logout")
    blocked_after_logout = client.get("/api/v1/projects")
    login_again = client.post("/api/v1/auth/login", json={"username": "admin", "password": "Passw0rd!"})
    projects = client.get("/api/v1/projects")

    assert blocked.status_code == 401
    assert login.status_code == 200
    assert login.json()["data"]["account"]["role"] == "admin"
    assert "password_hash" not in login.json()["data"]["account"]
    assert "password_hash" not in (login.json()["data"]["account"].get("metadata") or {})
    assert me.json()["data"]["authenticated"] is True
    assert accounts.status_code == 200
    assert logout.status_code == 200
    assert blocked_after_logout.status_code == 401
    assert login_again.status_code == 200
    assert projects.status_code == 200


def test_non_admin_cannot_manage_accounts(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", "Passw0rd!")
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    admin_client = TestClient(create_app())
    user_client = TestClient(create_app())

    admin_login = admin_client.post("/api/v1/auth/login", json={"username": "admin", "password": "Passw0rd!"})
    user_register = user_client.post(
        "/api/v1/auth/register",
        json={"username": "bidder", "password": "Passw0rd!", "display_name": "编标人员"},
    )
    admin_accounts = admin_client.get("/api/v1/accounts")
    user_accounts = user_client.get("/api/v1/accounts")
    user_projects = user_client.get("/api/v1/projects")

    assert admin_login.status_code == 200
    assert admin_login.json()["data"]["account"]["role"] == "admin"
    assert admin_accounts.status_code == 200
    assert user_register.status_code == 200
    assert user_register.json()["data"]["account"]["role"] == "bid_staff"
    assert user_accounts.status_code == 403
    assert user_projects.status_code == 200


def test_api_errors_use_envelope_and_request_id(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    client = TestClient(create_app())

    response = client.get("/api/v1/projects/not-found", headers={"X-Request-ID": "REQ-UNIT-001"})

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "REQ-UNIT-001"
    payload = response.json()
    assert payload["success"] is False
    assert payload["data"] is None
    assert payload["request_id"] == "REQ-UNIT-001"
    assert payload["error"]["code"] == "NOT_FOUND"
    assert "项目不存在" in payload["error"]["message"]


def test_model_config_api_updates_task_profiles(tmp_path, monkeypatch):
    profile_path = tmp_path / "llm-task-profiles.json"
    profile_path.write_text(
        json.dumps(
            {
                "schema_version": "llm_task_profiles_v1",
                "default": {"max_workers": 1, "temperature": 0},
                "tasks": {
                    "technical_bid_chapter_generation": {
                        "max_tokens": 12000,
                        "timeout_seconds": 300,
                        "max_workers": 3,
                        "temperature": 0.35,
                        "top_p": 0.9,
                        "structured_output_type": "json_object",
                        "enable_thinking": False,
                        "reasoning_effort": "none",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_TASK_PROFILES_PATH", str(profile_path))
    client = TestClient(create_app())

    response = client.patch(
        "/api/v1/model-config",
        json={
            "provider": "deepseek",
            "api_type": "chat_completions",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "tasks": {
                "technical_bid_chapter_generation": {
                    "max_tokens": 12000,
                    "timeout_seconds": 300,
                    "max_workers": 5,
                    "temperature": 0.35,
                    "top_p": 0.9,
                    "structured_output_type": "json_object",
                    "enable_thinking": False,
                    "reasoning_effort": "none",
                }
            },
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["tasks"]["technical_bid_chapter_generation"]["max_workers"] == 5
    saved = json.loads(profile_path.read_text(encoding="utf-8"))
    assert saved["tasks"]["technical_bid_chapter_generation"]["max_workers"] == 5


def test_excellent_bid_upload_requires_project_type_and_desensitization(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    client = TestClient(create_app())

    missing_project_type = client.post(
        "/api/v1/knowledge-base/excellent-bids/upload",
        data={"desensitized_confirmed": "true"},
        files={"file": ("excellent.docx", b"docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    missing_confirm = client.post(
        "/api/v1/knowledge-base/excellent-bids/upload",
        data={"project_type": "building_construction", "desensitized_confirmed": "false"},
        files={"file": ("excellent.docx", b"docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )

    assert missing_project_type.status_code == 422
    assert missing_confirm.status_code == 422
    assert "脱敏" in missing_confirm.json()["error"]["message"]


def test_excellent_bid_upload_saves_docx_and_creates_job(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/knowledge-base/excellent-bids/upload",
        data={
            "title": "测试优秀标书",
            "knowledge_type": "law_regulation",
            "project_type": "building_construction",
            "bid_type": "construction_technical_bid",
            "allow_image_reuse": "true",
            "desensitized_confirmed": "true",
            "remarks": "单元测试",
        },
        files={"file": ("excellent.docx", b"docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    source = data["source"]
    assert source["title"] == "测试优秀标书"
    assert source["knowledge_type"] == "law_regulation"
    assert source["knowledge_type_label"] == "法律法规"
    assert source["project_type_label"] == "房建"
    assert source["status"] == "processing"
    assert data["file"]["business_type"] == "excellent_bid"
    assert data["file"]["related_source_bid_id"] == source["source_bid_id"]
    assert data["file"]["metadata"]["knowledge_type_label"] == "法律法规"
    assert data["job"]["job_type"] == "excellent_bid_ingestion"
    assert (tmp_path / "knowledge_base" / "excellent_bids" / "originals" / source["source_bid_id"] / "excellent.docx").exists()


def test_excellent_bid_upload_runs_docx_ingestion_job(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    client = TestClient(create_app())
    docx_path = tmp_path / "excellent.docx"
    _write_minimal_excellent_bid_docx(docx_path)

    with docx_path.open("rb") as handle:
        response = client.post(
            "/api/v1/knowledge-base/excellent-bids/upload",
            data={
                "title": "测试优秀标书",
                "project_type": "building_construction",
                "bid_type": "construction_technical_bid",
                "allow_image_reuse": "true",
                "desensitized_confirmed": "true",
            },
            files={
                "file": (
                    "excellent.docx",
                    handle.read(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )

    assert response.status_code == 200
    source_id = response.json()["data"]["source"]["source_bid_id"]
    job_id = response.json()["data"]["job"]["job_id"]
    job = client.get(f"/api/v1/jobs/{job_id}").json()["data"]
    manifest = client.get("/api/v1/knowledge-base/excellent-bids").json()["data"]
    source = next(item for item in manifest["sources"] if item["source_bid_id"] == source_id)

    assert job["status"] == "succeeded"
    assert source["status"] == "pending_review"
    assert source["status_label"] == "待复核"
    assert source["slice_count"] >= 1
    assert source["project_type_label"] == "房建"
    assert source["quality_level"] == "review"
    assert "待人工复核" in source["quality_flags"]
    assert "待人工复核" in source["usage_advice"]
    assert manifest["quality_summary"]["source_count"] >= 1
    assert manifest["quality_summary"]["pending_review_count"] >= 1
    assert manifest["quality_summary"]["readiness_score"] > 0


def test_retry_failed_only_does_not_expand_parent_generation_unit():
    packages = [
        _chapter_package("GU-PARENT", "level2_section_group", ["评分点", "父章节"]),
        _chapter_package(
            "GU-CHILD-1",
            "level3_subsection_unit",
            ["评分点", "父章节", "子章节1"],
            parent_level_2_node_id="PARENT",
        ),
        _chapter_package(
            "GU-CHILD-2",
            "level3_subsection_unit",
            ["评分点", "父章节", "子章节2"],
            parent_level_2_node_id="PARENT",
        ),
    ]

    expanded = _select_chapter_packages(packages, {"target_unit_ids": ["GU-PARENT"]})
    strict = _select_chapter_packages(
        packages,
        {"target_unit_ids": ["GU-PARENT"], "retry_failed_only": True},
    )

    assert [item["generation_unit"]["unit_id"] for item in expanded] == ["GU-PARENT", "GU-CHILD-1", "GU-CHILD-2"]
    assert [item["generation_unit"]["unit_id"] for item in strict] == ["GU-PARENT"]


def test_project_flow_works_with_dev_json_fallback(tmp_path, monkeypatch):
    _use_lightweight_parse(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    client = TestClient(create_app())

    created = client.post("/api/v1/projects", json={"name": "测试项目"}).json()["data"]
    project_id = created["project_id"]
    listed = client.get("/api/v1/projects").json()["data"]

    assert listed[0]["project_id"] == project_id

    uploaded = client.post(
        f"/api/v1/projects/{project_id}/files",
        data={"business_type": "tender_document"},
        files={"file": ("招标文件.docx", b"docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    ).json()["data"]
    files = client.get(f"/api/v1/projects/{project_id}/files").json()["data"]

    assert uploaded["file_ext"] == "docx"
    assert files[0]["file_name"] == "招标文件.docx"

    job = client.post(f"/api/v1/projects/{project_id}/jobs", json={"job_type": "tender_parse"}).json()["data"]
    fetched_job = _wait_for_job(client, job["job_id"])

    assert fetched_job["job_type"] == "tender_parse"


def test_workflow_summary_reflects_uploads_and_jobs(tmp_path, monkeypatch):
    _use_lightweight_parse(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    client = TestClient(create_app())

    project = client.post("/api/v1/projects", json={"name": "工作流测试项目"}).json()["data"]
    project_id = project["project_id"]
    client.post(
        f"/api/v1/projects/{project_id}/files",
        data={"business_type": "tender_document"},
        files={"file": ("招标文件.pdf", b"pdf", "application/pdf")},
    )
    job = client.post(f"/api/v1/projects/{project_id}/jobs", json={"job_type": "tender_parse"}).json()["data"]
    _wait_for_job(client, job["job_id"])

    response = client.get(f"/api/v1/projects/{project_id}/workflow-summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    summary = payload["data"]
    assert summary["stats"]["files"] == 1
    assert summary["stats"]["jobs"] == 1
    assert summary["steps"][0]["status"] == "done"
    assert summary["steps"][1]["status"] == "done"
    assert summary["score_point_coverage"]["schema_version"] == "score_point_coverage_v1"
    assert summary["ai_review_report"]["schema_version"] == "ai_review_report_v1"
    assert summary["generation_report"]["schema_version"] == "generation_report_v1"
    assert summary["generation_report"]["status"] == "waiting"


def test_score_point_coverage_summary_links_outline_and_generation_units():
    coverage = _score_point_coverage_summary(
        [
            {"title": "施工组织设计方案完整性", "status": "已识别"},
            {"title": "质量安全文明施工措施", "status": "已识别"},
        ],
        [
            {
                "node_id": "N1",
                "title": "施工组织设计方案完整性",
                "children": [{"node_id": "N1-1", "title": "施工部署", "children": []}],
            },
            {
                "node_id": "N2",
                "title": "质量安全文明施工措施",
                "children": [{"node_id": "N2-1", "title": "质量安全措施", "children": []}],
            },
        ],
        [
            {
                "unit_id": "GU-N1-1",
                "target_node_id": "N1-1",
                "chapter": "施工部署",
                "chapter_path": ["施工组织设计方案完整性", "施工部署"],
                "status": "已生成",
            },
            {
                "unit_id": "GU-N2-1",
                "target_node_id": "N2-1",
                "chapter": "质量安全措施",
                "chapter_path": ["质量安全文明施工措施", "质量安全措施"],
                "status": "待生成",
            },
        ],
        review_items_count_hint=0,
    )

    assert coverage["schema_version"] == "score_point_coverage_v1"
    assert coverage["summary"]["total"] == 2
    assert coverage["summary"]["outline_covered"] == 2
    assert coverage["summary"]["covered"] == 1
    assert coverage["summary"]["pending"] == 1
    assert coverage["items"][0]["outline_text"] == "施工组织设计方案完整性"
    assert coverage["items"][0]["generation_text"] == "已生成 1/1"


def test_ai_review_report_summary_uses_score_coverage_and_generation_status():
    coverage = {
        "schema_version": "score_point_coverage_v1",
        "summary": {"total": 2, "covered": 1, "pending": 1, "risk": 0, "outline_covered": 2},
        "items": [
            {"title": "施工组织设计方案完整性", "status_key": "covered", "generation_text": "已生成 1/1"},
            {"title": "质量安全文明施工措施", "status_key": "pending", "generation_text": "已生成 0/1"},
        ],
    }

    report = _ai_review_report_summary(
        score_point_coverage=coverage,
        generation_units=[
            {"status": "已生成"},
            {"status": "待生成"},
        ],
        review_items=[],
        artifacts={"word_draft_docx": {"exists": False}, "word_draft_json": {"exists": False}},
    )

    assert report["schema_version"] == "ai_review_report_v1"
    assert report["level"] == "warn"
    assert report["metrics"]["score_points_covered"] == 1
    assert report["metrics"]["chapters_pending"] == 1
    assert "质量安全文明施工措施" in "；".join(report["focus_items"])


def test_generation_report_summary_collects_job_usage_and_review_metrics():
    coverage = {
        "schema_version": "score_point_coverage_v1",
        "summary": {"total": 3, "covered": 2, "pending": 0, "risk": 1, "outline_covered": 3},
        "items": [],
    }
    ai_review = {
        "schema_version": "ai_review_report_v1",
        "metrics": {
            "score_points_total": 3,
            "score_points_covered": 2,
            "score_points_risk": 1,
            "manual_review_items": 2,
        },
    }

    report = _generation_report_summary(
        jobs=[
            {
                "job_id": "JOB-001",
                "job_type": "chapter_llm_generation",
                "status": "succeeded",
                "progress_total": 3,
                "progress_completed": 2,
                "progress_failed": 1,
                "message": "真实正文生成完成",
                "started_at": "2026-05-20T10:00:00+08:00",
                "ended_at": "2026-05-20T10:02:00+08:00",
                "updated_at": "2026-05-20T10:02:00+08:00",
                "metadata": {
                    "duration_seconds": 120,
                    "llm_usage_summary": {
                        "call_count": 5,
                        "failed_count": 1,
                        "estimated_total_tokens": 12345,
                        "models": ["deepseek-v4"],
                        "providers": ["deepseek"],
                    },
                },
            }
        ],
        score_point_coverage=coverage,
        ai_review_report=ai_review,
        generation_units=[
            {"status": "已生成"},
            {"status": "已生成"},
            {"status": "生成失败"},
        ],
        review_items=[{"title": "确认安全文明施工措施"}],
        artifacts={"word_draft_docx": {"exists": True}},
    )

    assert report["schema_version"] == "generation_report_v1"
    assert report["available"] is True
    assert report["status"] == "failed"
    assert report["metrics"]["duration_seconds"] == 120
    assert report["metrics"]["llm_call_count"] == 5
    assert report["metrics"]["estimated_total_tokens"] == 12345
    assert report["metrics"]["score_points_covered"] == 2
    assert report["metrics"]["chapters_failed"] == 1
    assert report["metrics"]["word_ready"] is True
    assert report["latest_job"]["job_label"] == "真实正文生成"
    assert "重试失败" in "；".join(report["next_actions"])


def test_generation_report_summary_includes_stage_timings():
    report = _generation_report_summary(
        jobs=[
            {
                "job_id": "JOB-001",
                "job_type": "chapter_llm_generation",
                "status": "succeeded",
                "started_at": "2026-05-20T10:00:00+08:00",
                "ended_at": "2026-05-20T10:02:00+08:00",
                "updated_at": "2026-05-20T10:02:00+08:00",
                "metadata": {"duration_seconds": 120},
            }
        ],
        score_point_coverage={"summary": {"total": 1, "covered": 1}},
        ai_review_report={"metrics": {}},
        generation_units=[{"status": "已生成"}],
        review_items=[],
        artifacts={"word_draft_docx": {"exists": True}},
        timing_profile={
            "stage_metrics": [
                {"key": "tender_parse", "label": "招标文件解析", "status": "succeeded", "duration_seconds": 12.3, "source": "parse"},
                {"key": "outline_generation", "label": "技术标目录生成", "status": "succeeded", "duration_seconds": 8, "source": "outline"},
                {"key": "chapter_llm_generation", "label": "正文分章节生成", "status": "succeeded", "duration_seconds": 120, "source": "generation"},
                {"key": "chapter_aggregate_refresh", "label": "Word 初稿刷新", "status": "available", "duration_seconds": 5.5, "source": "docx"},
            ]
        },
    )

    timings = report["metrics"]["stage_timings"]
    assert [item["key"] for item in timings] == [
        "tender_parse",
        "outline_generation",
        "chapter_llm_generation",
        "chapter_aggregate_refresh",
    ]
    assert timings[0]["label"] == "解析确认"
    assert timings[-1]["label"] == "Word 整理"
    assert timings[-1]["duration_seconds"] == 5.5


def test_ai_assistant_readonly_endpoints_work_with_dev_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    client = TestClient(create_app())

    project = client.post("/api/v1/projects", json={"name": "AI 展示测试项目", "project_type": "construction"}).json()["data"]
    project_id = project["project_id"]

    summary = client.get(f"/api/v1/projects/{project_id}/assistant/summary")
    chat = client.post(f"/api/v1/projects/{project_id}/assistant/chat", json={"message": "这个项目下一步应该做什么？"})
    templates = client.get("/api/v1/bid-templates")
    template_rec = client.get(f"/api/v1/projects/{project_id}/bid-template/recommendation")
    rag = client.get(f"/api/v1/projects/{project_id}/rag/materials?limit=3")
    rag_sources = client.get("/api/v1/rag/sources")
    rag_search = client.get("/api/v1/rag/sources/search?q=模板&limit=3")

    assert summary.status_code == 200
    assert "AI 展示测试项目" in summary.json()["data"]["summary"]
    assert chat.status_code == 200
    assert "上传招标文件" in chat.json()["data"]["answer"]
    assert templates.status_code == 200
    assert templates.json()["data"]["templates"]
    assert template_rec.status_code == 200
    recommendations = template_rec.json()["data"]["recommendations"]
    assert recommendations
    assert "fit_level" in recommendations[0]
    assert "usage_boundary" in recommendations[0]
    assert rag.status_code == 200
    assert "results" in rag.json()["data"]
    assert rag_sources.status_code == 200
    assert "sources" in rag_sources.json()["data"]
    assert rag_search.status_code == 200
    assert "results" in rag_search.json()["data"]


def test_bid_template_upload_json_and_docx_parse(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTH_REQUIRED", "0")
    monkeypatch.setattr(
        "construction_bidding_agent.backend.app.DEFAULT_BID_TEMPLATE_DIR",
        tmp_path / "bid_templates",
    )
    client = TestClient(create_app())

    template_json = {
        "name": "企业房建专项模板",
        "project_type": "construction",
        "version": "v2",
        "tags": ["质量", "安全"],
        "chapters": [{"title": "质量安全管理", "writing_focus": ["质量目标", "安全文明"]}],
        "tables": ["质量检查表"],
    }
    json_response = client.post(
        "/api/v1/bid-templates/upload",
        files={"file": ("enterprise_template.json", json.dumps(template_json, ensure_ascii=False).encode("utf-8"), "application/json")},
    )

    assert json_response.status_code == 200
    imported = json_response.json()["data"]["template"]
    assert imported["name"] == "企业房建专项模板"
    assert imported["chapter_count"] == 1
    assert imported["table_count"] == 1

    docx_path = tmp_path / "bid_template.docx"
    _write_minimal_bid_template_docx(docx_path)
    with docx_path.open("rb") as handle:
        docx_response = client.post(
            "/api/v1/bid-templates/upload",
            data={"name": "Word 解析模板", "project_type": "construction", "version": "v1"},
            files={"file": ("bid_template.docx", handle, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )

    assert docx_response.status_code == 200
    parsed = docx_response.json()["data"]["template"]
    assert parsed["name"] == "Word 解析模板"
    assert parsed["chapter_count"] >= 2
    assert parsed["table_count"] >= 1
    assert parsed["parse_summary"]["detected_chapter_count"] >= 2


def test_cancel_job_marks_running_job_cancelled(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    client = TestClient(create_app())

    project = client.post("/api/v1/projects", json={"name": "取消任务测试项目"}).json()["data"]
    project_id = project["project_id"]
    job = client.post(f"/api/v1/projects/{project_id}/jobs", json={"job_type": "manual_long_task"}).json()["data"]
    cancelled = client.post(f"/api/v1/jobs/{job['job_id']}/cancel").json()["data"]

    assert cancelled["status"] == "cancelled"
    assert cancelled["error_code"] == "USER_CANCELLED"
    assert cancelled["effective_status"] == "cancelled"
    assert cancelled["is_terminal"] is True
    assert cancelled["retryable"] is False
    assert cancelled["error"]["code"] == "USER_CANCELLED"


def test_job_detail_returns_normalized_runtime_state(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr("construction_bidding_agent.backend.app._run_background_workflow_job", lambda *args, **kwargs: None)
    client = TestClient(create_app())

    project = client.post("/api/v1/projects", json={"name": "任务状态标准化测试项目"}).json()["data"]
    created = client.post(f"/api/v1/projects/{project['project_id']}/jobs", json={"job_type": "tender_parse"}).json()["data"]
    fetched = client.get(f"/api/v1/jobs/{created['job_id']}").json()["data"]

    assert fetched["status"] == "pending"
    assert fetched["effective_status"] == "pending"
    assert fetched["status_label"] == "排队中"
    assert fetched["is_active"] is True
    assert fetched["is_terminal"] is False
    assert fetched["retryable"] is False
    assert fetched["error"] is None


def test_duplicate_active_workflow_job_reuses_existing_job(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr("construction_bidding_agent.backend.app._run_background_workflow_job", lambda *args, **kwargs: None)
    client = TestClient(create_app())

    project = client.post("/api/v1/projects", json={"name": "重复任务保护测试项目"}).json()["data"]
    project_id = project["project_id"]

    first = client.post(f"/api/v1/projects/{project_id}/jobs", json={"job_type": "tender_parse"}).json()["data"]
    second = client.post(f"/api/v1/projects/{project_id}/jobs", json={"job_type": "tender_parse"}).json()["data"]
    jobs = client.get(f"/api/v1/projects/{project_id}/workflow-summary").json()["data"]["latest_jobs"]

    assert second["job_id"] == first["job_id"]
    assert second["reused_existing_job"] is True
    assert second["effective_status"] == "pending"
    assert second["is_active"] is True
    assert second["metadata"]["duplicate_policy"] == "reuse_active_same_type_job"
    assert len([item for item in jobs if item["job_type"] == "tender_parse"]) == 1


def test_main_workflow_generates_real_artifacts(tmp_path, monkeypatch):
    _use_lightweight_parse(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("MODEL", "test-outline-model")
    monkeypatch.setenv("BASE_URL", "https://compatible.example.com/v1")
    monkeypatch.setenv("MAX_WORKERS", "2")
    monkeypatch.setattr(
        "construction_bidding_agent.outline_generator.refinement_runner.call_openai_json",
        _fake_outline_refinement_call,
    )
    client = TestClient(create_app())
    tender_path = tmp_path / "sample_tender.docx"
    _write_minimal_tender_docx(tender_path)

    project = client.post("/api/v1/projects", json={"name": "主流程测试项目", "project_type": "construction"}).json()["data"]
    project_id = project["project_id"]
    with tender_path.open("rb") as handle:
        client.post(
            f"/api/v1/projects/{project_id}/files",
            data={"business_type": "tender_document"},
            files={
                "file": (
                    "sample_tender.docx",
                    handle.read(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )

    parse_job = client.post(f"/api/v1/projects/{project_id}/jobs", json={"job_type": "tender_parse"}).json()["data"]
    parse_job = _wait_for_job(client, parse_job["job_id"])
    outline_job = client.post(f"/api/v1/projects/{project_id}/jobs", json={"job_type": "outline_generation"}).json()["data"]
    outline_job = _wait_for_job(client, outline_job["job_id"])
    chapter_job = client.post(f"/api/v1/projects/{project_id}/jobs", json={"job_type": "chapter_generation"}).json()["data"]
    chapter_job = _wait_for_job(client, chapter_job["job_id"])
    summary = client.get(f"/api/v1/projects/{project_id}/workflow-summary").json()["data"]

    assert parse_job["status"] == "succeeded"
    assert outline_job["status"] == "succeeded"
    assert chapter_job["status"] == "succeeded"
    assert (tmp_path / "projects" / project_id / "parse" / "tender_parse_report.md").exists()
    outline_path = tmp_path / "projects" / project_id / "outline" / "technical_bid_outline.json"
    refinement_inputs_path = tmp_path / "projects" / project_id / "outline" / "outline_refinement_inputs.json"
    assert outline_path.exists()
    assert refinement_inputs_path.exists()
    assert (tmp_path / "projects" / project_id / "documents" / "technical_bid_draft.md").exists()
    outline = json.loads(outline_path.read_text(encoding="utf-8"))
    refinement_inputs = json.loads(refinement_inputs_path.read_text(encoding="utf-8"))
    assert outline["outline_generation_mode"] == "rule_skeleton_plus_llm_refinement"
    assert outline["generator_version"].endswith("+llm_refinement")
    assert all(node["template_source"] == "llm_refined" for node in outline["nodes"])
    assert any(package["target_outline_node"]["existing_children"] for package in refinement_inputs["packages"])
    assert summary["stats"]["score_points"] >= 3
    assert len(summary["generation_units"]) >= 1
    assert summary["artifacts"]["draft_markdown"]["file_name"] == "technical_bid_draft.md"


def test_project_artifact_preview_and_download(tmp_path, monkeypatch):
    _use_lightweight_parse(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    client = TestClient(create_app())
    tender_path = tmp_path / "sample_tender.docx"
    _write_minimal_tender_docx(tender_path)

    project = client.post("/api/v1/projects", json={"name": "产物预览测试项目"}).json()["data"]
    project_id = project["project_id"]
    with tender_path.open("rb") as handle:
        client.post(
            f"/api/v1/projects/{project_id}/files",
            data={"business_type": "tender_document"},
            files={
                "file": (
                    "sample_tender.docx",
                    handle.read(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
    job = client.post(f"/api/v1/projects/{project_id}/jobs", json={"job_type": "tender_parse"}).json()["data"]
    _wait_for_job(client, job["job_id"])

    artifacts = client.get(f"/api/v1/projects/{project_id}/artifacts").json()["data"]
    preview = client.get(f"/api/v1/projects/{project_id}/artifacts/parse_report")
    download = client.get(f"/api/v1/projects/{project_id}/artifacts/parse_report/download")

    assert artifacts["parse_report"]["previewable"] is True
    assert artifacts["parse_report"]["download_url"].endswith("/artifacts/parse_report/download")
    assert preview.status_code == 200
    assert preview.json()["data"]["render_type"] == "markdown"
    assert "招标文件解析报告" in preview.json()["data"]["text"]
    assert download.status_code == 200
    assert "招标文件解析报告" in download.text


def test_word_export_profile_summary_export_and_download_api(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr("construction_bidding_agent.backend.app._resolve_word_export_material_library", lambda: None)
    client = TestClient(create_app())

    project = client.post("/api/v1/projects", json={"name": "成稿导出 API 测试项目"}).json()["data"]
    project_id = project["project_id"]
    generation_dir = tmp_path / "projects" / project_id / "generation"
    generation_dir.mkdir(parents=True, exist_ok=True)
    _write_minimal_word_generation_inputs(generation_dir)

    default_profile = client.get("/api/v1/word/export-profiles/default")
    profile = client.get(f"/api/v1/projects/{project_id}/word/export-profile")
    saved_profile = client.put(
        f"/api/v1/projects/{project_id}/word/export-profile",
        json={"profile": {"body": {"font_size_pt": 11}}},
    )
    export_response = client.post(
        f"/api/v1/projects/{project_id}/word/export",
        json={"save_profile": False, "profile": {"body": {"font_size_pt": 12}}},
    )
    summary_response = client.get(f"/api/v1/projects/{project_id}/word/summary")
    download_response = client.get(f"/api/v1/projects/{project_id}/word/download?version=latest")
    reset_profile = client.post(f"/api/v1/projects/{project_id}/word/export-profile/reset")

    assert default_profile.status_code == 200
    assert default_profile.json()["data"]["toc"]["title"] == "目录"
    assert profile.status_code == 200
    assert profile.json()["data"]["schema_version"] == "word_export_profile_v1"
    assert saved_profile.status_code == 200
    assert saved_profile.json()["data"]["body"]["font_size_pt"] == 11
    assert export_response.status_code == 200
    assert export_response.json()["data"]["llm_called"] is False
    assert summary_response.status_code == 200
    summary = summary_response.json()["data"]
    assert summary["latest_version"] == "system_generated"
    assert summary["files"]["system_generated"]["exists"] is True
    assert summary["stats"]["heading_count"] >= 1
    assert download_response.status_code == 200
    assert download_response.headers["content-disposition"].endswith('filename="system_generated.docx"')
    assert reset_profile.status_code == 200
    assert reset_profile.json()["data"]["body"]["font_size_pt"] == 12


def test_onlyoffice_config_file_and_callback_api(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BACKEND_INTERNAL_URL", "http://backend:8000")
    monkeypatch.setenv("ONLYOFFICE_PUBLIC_URL", "http://localhost/onlyoffice")
    monkeypatch.setenv("ONLYOFFICE_JWT_SECRET", "test-secret")
    monkeypatch.setattr("construction_bidding_agent.backend.app._resolve_word_export_material_library", lambda: None)
    client = TestClient(create_app())

    project = client.post("/api/v1/projects", json={"name": "OnlyOffice API 测试项目"}).json()["data"]
    project_id = project["project_id"]
    generation_dir = tmp_path / "projects" / project_id / "generation"
    generation_dir.mkdir(parents=True, exist_ok=True)
    _write_minimal_word_generation_inputs(generation_dir)
    client.post(f"/api/v1/projects/{project_id}/word/export", json={})

    config_response = client.get(f"/api/v1/projects/{project_id}/word/onlyoffice-config")
    file_response = client.get(f"/api/v1/projects/{project_id}/word/onlyoffice-file?version=system_generated")
    callback_response = client.post(
        f"/api/v1/projects/{project_id}/word/onlyoffice-callback",
        json={
            "status": 2,
            "key": "test-key",
            "file_content_base64": base64.b64encode(b"edited-docx").decode("ascii"),
        },
    )
    summary_response = client.get(f"/api/v1/projects/{project_id}/word/summary")

    assert config_response.status_code == 200
    config = config_response.json()["data"]
    assert config["document_server_url"] == "/onlyoffice"
    assert config["jwt_secret_configured"] is True
    assert config["editor_config"]["document"]["url"].startswith("http://backend:8000/api/v1/projects/")
    assert config["editor_config"]["editorConfig"]["callbackUrl"].endswith("/word/onlyoffice-callback")
    assert file_response.status_code == 200
    assert callback_response.status_code == 200
    assert callback_response.json()["saved"] is True
    assert (tmp_path / "projects" / project_id / "documents" / "review_editing.docx").read_bytes() == b"edited-docx"
    assert summary_response.json()["data"]["latest_version"] == "review_editing"


def test_delete_uploaded_file_and_project_cleanup(tmp_path, monkeypatch):
    _use_lightweight_parse(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    client = TestClient(create_app())

    project = client.post("/api/v1/projects", json={"name": "删除测试项目"}).json()["data"]
    project_id = project["project_id"]
    uploaded = client.post(
        f"/api/v1/projects/{project_id}/files",
        data={"business_type": "tender_document"},
        files={"file": ("招标文件.docx", b"docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    ).json()["data"]
    uploaded_path = tmp_path / "projects" / project_id / "uploads" / "招标文件.docx"

    file_deleted = client.delete(f"/api/v1/projects/{project_id}/files/{uploaded['file_id']}")
    files_after_delete = client.get(f"/api/v1/projects/{project_id}/files").json()["data"]
    project_deleted = client.delete(f"/api/v1/projects/{project_id}")
    project_after_delete = client.get(f"/api/v1/projects/{project_id}")

    assert uploaded_path.exists() is False
    assert file_deleted.status_code == 200
    assert file_deleted.json()["data"]["deleted"] is True
    assert files_after_delete == []
    assert project_deleted.status_code == 200
    assert (tmp_path / "projects" / project_id).exists() is False
    assert project_after_delete.status_code == 404


def test_tender_parse_llm_mode_falls_back_without_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("TENDER_PARSE_MODE", "llm_with_rule_fallback")
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    client = TestClient(create_app())
    tender_path = tmp_path / "sample_tender.docx"
    _write_minimal_tender_docx(tender_path)

    project = client.post("/api/v1/projects", json={"name": "LLM 兜底测试项目"}).json()["data"]
    project_id = project["project_id"]
    with tender_path.open("rb") as handle:
        client.post(
            f"/api/v1/projects/{project_id}/files",
            data={"business_type": "tender_document"},
            files={
                "file": (
                    "sample_tender.docx",
                    handle.read(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
    job = client.post(f"/api/v1/projects/{project_id}/jobs", json={"job_type": "tender_parse"}).json()["data"]
    final_job = _wait_for_job(client, job["job_id"])
    parse_result = client.get(f"/api/v1/projects/{project_id}/artifacts/parse_result").json()["data"]["json"]

    assert final_job["status"] == "succeeded"
    assert final_job["metadata"]["execution_mode"] == "lightweight_rule_based"
    assert "API_KEY" in final_job["metadata"]["llm_error"]
    assert parse_result["execution"]["mode"] == "lightweight_rule_based"
    assert "llm_fallback_reason" in parse_result["execution"]


def test_hybrid_tender_parse_run_keeps_successful_llm_tasks_and_rules_for_failed_task():
    llm_run = {
        "schema_version": "tender_llm_extraction_run_v0.2",
        "execution_mode": "parallel",
        "duration_seconds": 30,
        "tasks": [
            {
                "task_key": "project_info_extraction_input",
                "task_title": "项目信息",
                "status": "completed",
                "parsed_json": {"project_type": "construction"},
                "validation": {},
            },
            {
                "task_key": "score_points_extraction_input",
                "task_title": "评分点",
                "status": "failed",
                "error": "Unterminated string",
                "validation": {},
            },
            {
                "task_key": "technical_requirements_extraction_input",
                "task_title": "技术要求",
                "status": "completed",
                "parsed_json": {"requirements": []},
                "validation": {},
            },
        ],
    }
    fallback_score_run = {
        "execution_mode": "lightweight_rule_based",
        "tasks": [
            {
                "task_key": "score_points_extraction_input",
                "task_title": "技术标评分点抽取",
                "status": "completed",
                "parsed_json": {"system_final_score_points": [{"score_point_raw": "施工方案"}]},
                "validation": {"summary": "轻量规则抽取 1 个技术标评分点。"},
            }
        ],
    }

    hybrid = _hybrid_tender_parse_run(
        llm_run_data=llm_run,
        fallback_runs=[fallback_score_run],
        failed_tasks=[
            {
                "task_key": "score_points_extraction_input",
                "task_title": "评分点",
                "status": "failed",
                "error": "Unterminated string",
            }
        ],
    )

    by_key = {task["task_key"]: task for task in hybrid["tasks"]}
    assert hybrid["execution_mode"] == "llm_with_rule_fallback"
    assert by_key["project_info_extraction_input"]["status"] == "completed"
    assert by_key["technical_requirements_extraction_input"]["status"] == "completed"
    assert by_key["score_points_extraction_input"]["status"] == "fallback_completed"
    assert by_key["score_points_extraction_input"]["cache_status"] == "rule_fallback"


def test_rule_score_points_ignore_pricing_rows_and_total_score_headers():
    cells = [
            {"cell_id": "B1_R62_C1", "row_index": 62, "cell_index": 1, "text_raw": "分值构成（总分100分）"},
            {"cell_id": "B1_R62_C2", "row_index": 62, "cell_index": 2, "text_raw": "施工组织设计：20分"},
            {"cell_id": "B1_R65_C1", "row_index": 65, "cell_index": 1, "text_raw": "评标基准价计算方法"},
            {"cell_id": "B1_R65_C2", "row_index": 65, "cell_index": 2, "text_raw": "安全文明施工措施费不参与评标基准价计算。"},
            {"cell_id": "B1_R68_C1", "row_index": 68, "cell_index": 1, "text_raw": "施工组织设计（总分20分）"},
            {"cell_id": "B1_R68_C2", "row_index": 68, "cell_index": 2, "text_raw": "评分因素"},
            {"cell_id": "B1_R68_C3", "row_index": 68, "cell_index": 3, "text_raw": "参考评分标准"},
            {"cell_id": "B1_R69_C0", "row_index": 69, "cell_index": 0, "text_raw": "主要施工方案 与技术措施"},
            {"cell_id": "B1_R69_C1", "row_index": 69, "cell_index": 1, "text_raw": "3"},
            {"cell_id": "B1_R69_C2", "row_index": 69, "cell_index": 2, "text_raw": "施工方案总体安排合理。（0-3）"},
            {"cell_id": "B1_R71_C0", "row_index": 71, "cell_index": 0, "text_raw": "安全管理体系与措施"},
            {"cell_id": "B1_R71_C1", "row_index": 71, "cell_index": 1, "text_raw": "1.5"},
            {
                "cell_id": "B1_R71_C2",
                "row_index": 71,
                "cell_index": 2,
                "text_raw": "现场重大危险源辨识全面，有项目危险性较大的分部分项工程清单。（0-1.5）",
            },
            {"cell_id": "B1_R72_C0", "row_index": 72, "cell_index": 0, "text_raw": "文明施工、环境保护管理体系及施工现场扬尘治理措施"},
            {"cell_id": "B1_R72_C1", "row_index": 72, "cell_index": 1, "text_raw": "1.5"},
            {
                "cell_id": "B1_R72_C2",
                "row_index": 72,
                "cell_index": 2,
                "text_raw": "创建保证措施和安全文明施工措施费投入使用计划合理。（0-1.5）",
            },
            {"cell_id": "B1_R83_C1", "row_index": 83, "cell_index": 1, "text_raw": "投标报价（总分45分）"},
            {"cell_id": "B1_R84_C0", "row_index": 84, "cell_index": 0, "text_raw": "措施项目费（不含安全文明施工措施费）（满分5分）"},
    ]
    package = {"cell_refs": [{**cell, "block_index": 1, "table_index": 1} for cell in cells], "block_refs": []}

    points, used_fallback = _extract_score_points(package)
    titles = [point["score_point_raw"] for point in points]

    assert used_fallback is False
    assert "施工组织设计：20分" not in titles
    assert "评标基准价计算方法" not in titles
    assert "措施项目费（不含安全文明施工措施费）（满分5分）" not in titles
    assert titles == [
        "主要施工方案 与技术措施",
        "安全管理体系与措施",
        "文明施工、环境保护管理体系及施工现场扬尘治理措施",
    ]
    assert [point["score"] for point in points] == ["3分", "1.5分", "1.5分"]


def test_outline_generation_fails_without_api_key_instead_of_rule_fallback(tmp_path, monkeypatch):
    _use_lightweight_parse(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    client = TestClient(create_app())
    tender_path = tmp_path / "sample_tender.docx"
    _write_minimal_tender_docx(tender_path)

    project = client.post("/api/v1/projects", json={"name": "目录无 Key 测试项目"}).json()["data"]
    project_id = project["project_id"]
    with tender_path.open("rb") as handle:
        client.post(
            f"/api/v1/projects/{project_id}/files",
            data={"business_type": "tender_document"},
            files={
                "file": (
                    "sample_tender.docx",
                    handle.read(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
    parse_job = client.post(f"/api/v1/projects/{project_id}/jobs", json={"job_type": "tender_parse"}).json()["data"]
    _wait_for_job(client, parse_job["job_id"])
    outline_job = client.post(f"/api/v1/projects/{project_id}/jobs", json={"job_type": "outline_generation"}).json()["data"]
    outline_job = _wait_for_job(client, outline_job["job_id"])

    assert outline_job["status"] == "failed"
    assert "未配置 API_KEY" in outline_job["error_message"]
    assert not (tmp_path / "projects" / project_id / "outline" / "technical_bid_outline.json").exists()


def test_supported_workflow_job_is_created_before_background_completion(tmp_path, monkeypatch):
    _use_lightweight_parse(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    client = TestClient(create_app())
    project = client.post("/api/v1/projects", json={"name": "异步任务测试项目"}).json()["data"]

    response = client.post(f"/api/v1/projects/{project['project_id']}/jobs", json={"job_type": "outline_generation"})
    job = response.json()["data"]
    final_job = _wait_for_job(client, job["job_id"])

    assert response.status_code == 200
    assert job["status"] in {"pending", "running", "failed"}
    assert final_job["status"] == "failed"
    assert "请先完成招标文件解析" in final_job["error_message"]


def test_chapter_aggregate_refresh_does_not_call_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "construction_bidding_agent.backend.workflow_executor._ensure_chapter_generation_inputs",
        _fake_chapter_inputs,
    )
    monkeypatch.setattr(
        "construction_bidding_agent.backend.workflow_executor._try_export_word_draft_docx",
        _fake_word_export,
    )
    monkeypatch.setattr(
        "construction_bidding_agent.backend.workflow_executor.run_chapter_generation_batch",
        _raise_if_called,
    )
    client = TestClient(create_app())
    project = client.post("/api/v1/projects", json={"name": "刷新聚合测试项目"}).json()["data"]
    project_id = project["project_id"]
    chapter_dir = tmp_path / "projects" / project_id / "generation" / "chapter_llm_state" / "chapters"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    _write_chapter_state(chapter_dir / "GU-1.json", "GU-1", ["主要施工方案与技术措施", "测试章节"])

    job = client.post(f"/api/v1/projects/{project_id}/jobs", json={"job_type": "chapter_aggregate_refresh"}).json()["data"]
    final_job = _wait_for_job(client, job["job_id"])
    aggregate_path = tmp_path / "projects" / project_id / "generation" / "chapter_llm_generation_aggregate_result.json"
    preview_path = tmp_path / "projects" / project_id / "documents" / "technical_bid_llm_draft_preview.md"
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))

    assert final_job["status"] == "succeeded"
    assert final_job["metadata"]["refresh_only"] is True
    assert final_job["metadata"]["refresh_timing"]["llm_called"] is False
    stage_keys = {stage["key"] for stage in final_job["metadata"]["refresh_timing"]["stages"]}
    assert "prepare_chapter_inputs" in stage_keys
    assert "read_chapter_state" in stage_keys
    assert "aggregate_chapter_state" in stage_keys
    assert "render_markdown_preview" in stage_keys
    assert "export_word_draft" in stage_keys
    assert aggregate["completed_count"] == 1
    assert aggregate["failed_count"] == 0
    assert "未调用大模型" in "".join(aggregate["warnings"])
    assert preview_path.exists()
    timing_report = tmp_path / "projects" / project_id / "reports" / "tender_to_word_timing_profile.md"
    timing_json = tmp_path / "projects" / project_id / "reports" / "tender_to_word_timing_profile.json"
    timing_data = json.loads(timing_json.read_text(encoding="utf-8"))

    assert timing_report.exists()
    assert "慢章节 Top 10" in timing_report.read_text(encoding="utf-8")
    assert timing_data["schema_version"] == "project_timing_profile_v0.1"
    assert timing_data["chapter_generation"]["task_count"] == 1
    assert timing_data["chapter_generation"]["input_char_total"] == 1234
    assert timing_data["chapter_generation"]["full_package_char_total"] == 5678
    assert timing_data["chapter_generation"]["slow_chapters_top"][0]["llm_input_profile"] == "slim_v3"
    assert timing_data["chapter_generation"]["slow_chapters_top"][0]["chapter_path_text"] == "主要施工方案与技术措施 > 测试章节"
    word_stage = next(stage for stage in timing_data["stage_metrics"] if stage["key"] == "chapter_aggregate_refresh")
    assert word_stage["duration_seconds"] < 5


def test_timing_profile_reports_chapter_generation_history(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/missing")
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "construction_bidding_agent.backend.workflow_executor._ensure_chapter_generation_inputs",
        _fake_chapter_inputs,
    )
    monkeypatch.setattr(
        "construction_bidding_agent.backend.workflow_executor._try_export_word_draft_docx",
        _fake_word_export,
    )
    client = TestClient(create_app())
    project = client.post("/api/v1/projects", json={"name": "正文耗时历史测试项目"}).json()["data"]
    project_id = project["project_id"]
    store_path = tmp_path / "app" / "dev_state.json"
    chapter_dir = tmp_path / "projects" / project_id / "generation" / "chapter_llm_state" / "chapters"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    _write_chapter_state(chapter_dir / "GU-1.json", "GU-1", ["主要施工方案与技术措施", "测试章节"])

    data = json.loads(store_path.read_text(encoding="utf-8"))
    data["jobs"].extend(
        [
            {
                "job_id": "JOB-interrupted",
                "project_id": project_id,
                "job_type": "chapter_llm_generation",
                "status": "interrupted",
                "progress_total": 56,
                "progress_completed": 50,
                "progress_failed": 0,
                "progress_percent": 89.29,
                "message": "中断前已完成 50/56。",
                "result_ref": None,
                "error_code": None,
                "error_message": None,
                "started_at": "2026-05-11T15:11:20+08:00",
                "ended_at": "2026-05-11T16:05:33+08:00",
                "created_at": "2026-05-11T15:11:20+08:00",
                "updated_at": "2026-05-11T16:05:33+08:00",
                "config_snapshot": None,
                "metadata": {},
            },
            {
                "job_id": "JOB-resumed",
                "project_id": project_id,
                "job_type": "chapter_llm_generation",
                "status": "succeeded",
                "progress_total": 56,
                "progress_completed": 56,
                "progress_failed": 0,
                "progress_percent": 100,
                "message": "续跑完成 56/56。",
                "result_ref": None,
                "error_code": None,
                "error_message": None,
                "started_at": "2026-05-11T17:24:34+08:00",
                "ended_at": "2026-05-11T18:03:56+08:00",
                "created_at": "2026-05-11T17:24:34+08:00",
                "updated_at": "2026-05-11T18:03:56+08:00",
                "config_snapshot": None,
                "metadata": {},
            },
        ]
    )
    store_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    job = client.post(f"/api/v1/projects/{project_id}/jobs", json={"job_type": "chapter_aggregate_refresh"}).json()["data"]
    _wait_for_job(client, job["job_id"])
    timing_json = tmp_path / "projects" / project_id / "reports" / "tender_to_word_timing_profile.json"
    timing_md = tmp_path / "projects" / project_id / "reports" / "tender_to_word_timing_profile.md"
    timing_data = json.loads(timing_json.read_text(encoding="utf-8"))
    timing_text = timing_md.read_text(encoding="utf-8")

    history = timing_data["chapter_job_history"]
    assert history["run_count"] == 2
    assert history["interrupted_count"] == 1
    assert history["succeeded_count"] == 1
    assert history["latest_successful_run"]["job_id"] == "JOB-resumed"
    assert history["cumulative_active_seconds"] == 5615
    assert "正文生成任务口径" in timing_text
    assert "JOB-interrupted" in timing_text
    assert "JOB-resumed" in timing_text


def test_excellent_bid_library_migrates_existing_indexes(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    client = TestClient(create_app())

    response = client.post("/api/v1/knowledge-base/excellent-bids/migrate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    manifest = payload["data"]
    assert "quality_summary" in manifest
    assert manifest["quality_summary"]["source_count"] == manifest["source_count"]
    if manifest["source_count"]:
        assert manifest["slice_count"] > 0
        assert any(source["title"] == "总体施工方案" for source in manifest["sources"])
        assert all(source["project_type"] == "building_construction" for source in manifest["sources"])
        assert all(source["project_type_label"] == "房建" for source in manifest["sources"])
        assert all(source["bid_type"] == "construction_technical_bid" for source in manifest["sources"])
        assert all(source["bid_type_label"] == "施工技术标" for source in manifest["sources"])
        assert all(source["allow_image_reuse"] is True for source in manifest["sources"])
        assert manifest["quality_summary"]["readiness_score"] > 0
        assert all(source["quality_level"] for source in manifest["sources"])
        assert all(source["usage_advice"] for source in manifest["sources"])

    listed = client.get("/api/v1/knowledge-base/excellent-bids").json()["data"]
    assert listed["source_count"] == manifest["source_count"]
    assert "quality_summary" in listed


def test_excellent_bid_detail_and_search_api(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    client = TestClient(create_app())
    manifest = client.post("/api/v1/knowledge-base/excellent-bids/migrate").json()["data"]
    if not manifest["sources"]:
        docx_path = tmp_path / "excellent.docx"
        _write_minimal_excellent_bid_docx(docx_path)
        with docx_path.open("rb") as handle:
            upload = client.post(
                "/api/v1/knowledge-base/excellent-bids/upload",
                data={
                    "title": "测试优秀标书",
                    "project_type": "building_construction",
                    "bid_type": "construction_technical_bid",
                    "allow_image_reuse": "true",
                    "desensitized_confirmed": "true",
                },
                files={
                    "file": (
                        "excellent.docx",
                        handle.read(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            ).json()["data"]
        _wait_for_job(client, upload["job"]["job_id"])
        manifest = client.get("/api/v1/knowledge-base/excellent-bids").json()["data"]
    source_id = manifest["sources"][0]["source_bid_id"]

    detail_response = client.get(f"/api/v1/knowledge-base/excellent-bids/{source_id}?limit=5")
    search_response = client.get("/api/v1/knowledge-base/excellent-bids/search?q=模板&limit=5")

    assert detail_response.status_code == 200
    detail = detail_response.json()["data"]
    assert detail["source"]["source_bid_id"] == source_id
    assert detail["slice_preview_count"] <= 5
    assert detail["total_slice_count"] > 0

    assert search_response.status_code == 200
    search_result = search_response.json()["data"]
    assert search_result["query"] == "模板"
    assert search_result["total"] >= len(search_result["results"])


def test_rag_source_delete_removes_manifest_detail_and_search(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    client = TestClient(create_app())
    docx_path = tmp_path / "excellent.docx"
    _write_minimal_excellent_bid_docx(docx_path)
    with docx_path.open("rb") as handle:
        upload = client.post(
            "/api/v1/rag/sources/upload",
            data={
                "title": "待删除智库资料",
                "project_type": "building_construction",
                "bid_type": "construction_technical_bid",
                "allow_image_reuse": "true",
                "desensitized_confirmed": "true",
            },
            files={
                "file": (
                    "excellent.docx",
                    handle.read(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        ).json()["data"]
    _wait_for_job(client, upload["job"]["job_id"])
    source_id = upload["source"]["source_bid_id"]

    deleted = client.delete(f"/api/v1/rag/sources/{source_id}")
    listed = client.get("/api/v1/rag/sources").json()["data"]
    detail_after_delete = client.get(f"/api/v1/rag/sources/{source_id}")
    search_after_delete = client.get(f"/api/v1/rag/sources/search?q=模板&source_bid_id={source_id}").json()["data"]

    assert deleted.status_code == 200
    assert deleted.json()["data"]["deleted"] is True
    assert all(source["source_bid_id"] != source_id for source in listed["sources"])
    assert detail_after_delete.status_code == 404
    assert search_after_delete["total"] == 0


def test_rag_source_post_delete_alias_removes_manifest_detail_and_search(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_STORAGE_ROOT", str(tmp_path))
    client = TestClient(create_app())
    docx_path = tmp_path / "excellent.docx"
    _write_minimal_excellent_bid_docx(docx_path)
    with docx_path.open("rb") as handle:
        upload = client.post(
            "/api/v1/rag/sources/upload",
            data={
                "title": "POST 删除智库资料",
                "project_type": "building_construction",
                "bid_type": "construction_technical_bid",
                "allow_image_reuse": "true",
                "desensitized_confirmed": "true",
            },
            files={
                "file": (
                    "excellent.docx",
                    handle.read(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        ).json()["data"]
    _wait_for_job(client, upload["job"]["job_id"])
    source_id = upload["source"]["source_bid_id"]

    deleted = client.post(f"/api/v1/rag/sources/{source_id}/delete")
    listed = client.get("/api/v1/rag/sources").json()["data"]
    detail_after_delete = client.get(f"/api/v1/rag/sources/{source_id}")
    search_after_delete = client.get(f"/api/v1/rag/sources/search?q=模板&source_bid_id={source_id}").json()["data"]

    assert deleted.status_code == 200
    assert deleted.json()["data"]["deleted"] is True
    assert all(source["source_bid_id"] != source_id for source in listed["sources"])
    assert detail_after_delete.status_code == 404
    assert search_after_delete["total"] == 0


def _write_minimal_tender_docx(path):
    def paragraph(text):
        return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"

    def table(rows):
        row_xml = []
        for row in rows:
            row_xml.append("<w:tr>")
            for cell in row:
                row_xml.append(f"<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>")
            row_xml.append("</w:tr>")
        return f"<w:tbl>{''.join(row_xml)}</w:tbl>"

    body = "".join(
        [
            paragraph("第一章 招标公告"),
            paragraph("项目名称：测试房建工程"),
            paragraph("建设地点：测试地点"),
            paragraph("第二章 投标人须知"),
            paragraph("投标人须知前附表"),
            table(
                [
                    ["条款号", "条款名称", "编列内容"],
                    ["1.1.4", "项目名称", "测试房建工程"],
                    ["1.3.2", "计划工期", "365日历天"],
                    ["1.3.3", "质量要求", "合格"],
                ]
            ),
            paragraph("第三章 评标办法"),
            paragraph("评标办法前附表"),
            table(
                [
                    ["序号", "评审因素", "评分标准"],
                    ["1", "主要施工方案与技术措施", "施工方案总体安排合理，技术措施完整。"],
                    ["2", "质量管理体系与措施", "质量保证体系完整。"],
                    ["3", "安全管理体系与措施", "安全管理制度完善。"],
                ]
            ),
            paragraph("第八章 技术标准和要求"),
            paragraph("本工程执行国家现行施工质量验收规范和安全文明施工标准。"),
        ]
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}<w:sectPr/></w:body></w:document>"
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as package:
        package.writestr(
            "[Content_Types].xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/word/document.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                "</Types>"
            ),
        )
        package.writestr(
            "_rels/.rels",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                'Target="word/document.xml"/>'
                "</Relationships>"
            ),
        )
        package.writestr("word/document.xml", document_xml)
        package.writestr(
            "word/_rels/document.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
        )


def _write_minimal_excellent_bid_docx(path):
    def paragraph(text):
        return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"

    def table(rows):
        row_xml = []
        for row in rows:
            row_xml.append("<w:tr>")
            for cell in row:
                row_xml.append(f"<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>")
            row_xml.append("</w:tr>")
        return f"<w:tbl>{''.join(row_xml)}</w:tbl>"

    body = "".join(
        [
            paragraph("1. 主要施工方案与技术措施"),
            paragraph("本章结合房建工程特点，编制施工部署、土建工程和关键工艺措施。"),
            paragraph("1.1 土建工程施工方案"),
            paragraph("钢筋、模板、混凝土等分项工程按样板引路和过程控制组织实施。"),
            table([["序号", "项目", "措施"], ["1", "模板工程", "模板支设前完成轴线复核。"]]),
        ]
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}<w:sectPr/></w:body></w:document>"
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as package:
        package.writestr(
            "[Content_Types].xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/word/document.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                "</Types>"
            ),
        )
        package.writestr(
            "_rels/.rels",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                'Target="word/document.xml"/>'
                "</Relationships>"
            ),
        )
        package.writestr("word/document.xml", document_xml)
        package.writestr(
            "word/_rels/document.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
        )


def _write_minimal_bid_template_docx(path):
    def paragraph(text):
        return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"

    def table(rows):
        row_xml = []
        for row in rows:
            row_xml.append("<w:tr>")
            for cell in row:
                row_xml.append(f"<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>")
            row_xml.append("</w:tr>")
        return f"<w:tbl>{''.join(row_xml)}</w:tbl>"

    body = "".join(
        [
            paragraph("施工总承包技术标模板"),
            paragraph("第一章 施工组织总体部署"),
            paragraph("项目管理目标"),
            paragraph("施工区段划分"),
            paragraph("第二章 质量安全管理措施"),
            paragraph("质量管理体系"),
            paragraph("安全文明施工"),
            table([["序号", "表格名称"], ["1", "劳动力计划表"]]),
        ]
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}<w:sectPr/></w:body></w:document>"
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as package:
        package.writestr(
            "[Content_Types].xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/word/document.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                "</Types>"
            ),
        )
        package.writestr(
            "_rels/.rels",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                'Target="word/document.xml"/>'
                "</Relationships>"
            ),
        )
        package.writestr("word/document.xml", document_xml)
        package.writestr(
            "word/_rels/document.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
        )


def _fake_outline_refinement_call(*, config, system_prompt, user_input):
    package = json.loads(user_input)
    target = package["target_outline_node"]
    return json.dumps(
        {
            "schema_version": "outline_refinement_v1",
            "target_node_id": target["node_id"],
            "level_1_title": target["level_1_title"],
            "level_1_title_unchanged": True,
            "domain": target.get("domain") or "construction",
            "category": target.get("category") or "施工方案",
            "refined_children": [
                {
                    "level": 2,
                    "title": "评分点响应目标",
                    "title_source": "generated",
                    "children": [{"level": 3, "title": "响应范围与编制重点", "title_source": "generated"}],
                },
                {
                    "level": 2,
                    "title": "组织体系与职责分工",
                    "title_source": "generated",
                    "children": [{"level": 3, "title": "管理职责划分", "title_source": "generated"}],
                },
                {
                    "level": 2,
                    "title": "实施流程与控制要点",
                    "title_source": "generated",
                    "children": [{"level": 3, "title": "过程检查与纠偏", "title_source": "generated"}],
                },
                {
                    "level": 2,
                    "title": "资源配置与保障措施",
                    "title_source": "generated",
                    "children": [{"level": 3, "title": "资源投入计划", "title_source": "generated"}],
                },
                {
                    "level": 2,
                    "title": "质量安全与风险控制",
                    "title_source": "generated",
                    "children": [{"level": 3, "title": "风险识别与应对", "title_source": "generated"}],
                },
                {
                    "level": 2,
                    "title": "资料管理与复核要求",
                    "title_source": "generated",
                    "children": [{"level": 3, "title": "成果复核清单", "title_source": "generated"}],
                },
                {
                    "level": 2,
                    "title": "持续改进与响应承诺",
                    "title_source": "generated",
                    "children": [{"level": 3, "title": "改进闭环措施", "title_source": "generated"}],
                },
                {
                    "level": 2,
                    "title": "本章小结",
                    "title_source": "generated",
                    "children": [{"level": 3, "title": "评分条款对应关系", "title_source": "generated"}],
                },
            ],
            "quality_self_check": {"needs_human_review": False},
        },
        ensure_ascii=False,
    )


def _fake_chapter_inputs(*, storage, project_id):
    generation_dir = storage.storage_root / "projects" / project_id / "generation"
    generation_dir.mkdir(parents=True, exist_ok=True)
    inputs_json = generation_dir / "chapter_generation_inputs.json"
    package = {
        "generation_unit": {
            "unit_id": "GU-1",
            "target_node_id": "NODE-1",
            "chapter_path": ["主要施工方案与技术措施", "测试章节"],
        }
    }
    inputs_json.write_text(json.dumps({"packages": [package]}, ensure_ascii=False), encoding="utf-8")
    return [package], inputs_json


def _chapter_package(unit_id, unit_type, chapter_path, *, parent_level_2_node_id=None):
    unit = {
        "unit_id": unit_id,
        "target_node_id": unit_id.replace("GU", "NODE"),
        "unit_type": unit_type,
        "chapter_path": chapter_path,
    }
    if parent_level_2_node_id:
        unit["parent_level_2_node_id"] = parent_level_2_node_id
    return {"generation_unit": unit}


def _write_minimal_word_generation_inputs(generation_dir):
    inputs = {
        "packages": [
            {
                "generation_unit": {
                    "unit_id": "GU-1",
                    "target_node_id": "NODE-1",
                    "chapter_path": ["主要施工方案与技术措施", "测试章节"],
                },
                "score_point": {"score_point_raw": "主要施工方案与技术措施"},
            }
        ]
    }
    result = {
        "chapters": [
            {
                "unit_id": "GU-1",
                "target_node_id": "NODE-1",
                "chapter_path": ["主要施工方案与技术措施", "测试章节"],
                "sections": [
                    {
                        "heading": "测试小节",
                        "level": 3,
                        "blocks": [
                            {"type": "paragraph", "text": "这是用于测试 Word 成稿导出的正文。"},
                            {
                                "type": "rich_table",
                                "columns": [{"key": "col_1", "title": "序号"}, {"key": "col_2", "title": "措施"}],
                                "rows": [{"cells": {"col_1": "1", "col_2": "执行过程检查。"}}],
                            },
                        ],
                    }
                ],
            }
        ]
    }
    (generation_dir / "chapter_generation_inputs.json").write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")
    (generation_dir / "chapter_llm_generation_aggregate_result.json").write_text(
        json.dumps(result, ensure_ascii=False),
        encoding="utf-8",
    )


def _fake_word_export(**kwargs):
    output_docx = kwargs["output_docx"]
    output_json = kwargs["output_json"]
    output_docx.parent.mkdir(parents=True, exist_ok=True)
    output_docx.write_bytes(b"fake-docx")
    output_json.write_text(json.dumps({"ok": True}, ensure_ascii=False), encoding="utf-8")
    return {
        "enabled": True,
        "status": "succeeded",
        "docx_uri": "local://fake.docx",
        "json_uri": "local://fake.json",
        "summary": {},
    }


def _raise_if_called(*args, **kwargs):
    raise AssertionError("刷新 Word 初稿不应调用章节正文大模型生成。")


def _write_chapter_state(path, unit_id, chapter_path):
    chapter = {
        "schema_version": "technical_bid_chapter_draft_v1",
        "unit_id": unit_id,
        "target_node_id": "NODE-1",
        "chapter_path": chapter_path,
        "title": chapter_path[-1],
        "sections": [
            {
                "heading": "测试小节",
                "level": 3,
                "blocks": [{"type": "paragraph", "text": "这是用于测试聚合刷新的章节正文。"}],
            }
        ],
        "score_response_check": {"covered": True, "response_summary": "已响应。", "evidence_headings": ["测试小节"]},
        "source_usage": [],
        "review_items": [],
    }
    task = {
        "unit_id": unit_id,
        "target_node_id": "NODE-1",
        "chapter_path": chapter_path,
        "status": "completed",
        "duration_seconds": 1,
        "started_at": "2026-05-10T00:00:00+08:00",
        "completed_at": "2026-05-10T00:00:01+08:00",
        "model": "deepseek-v4-flash",
        "output_text": json.dumps(chapter, ensure_ascii=False),
        "parsed_json": chapter,
        "validation": {"valid": True, "blocking": False, "issue_count": 0, "issues": []},
        "error": None,
        "llm_input_schema_version": "chapter_llm_input_v1",
        "llm_input_profile": "slim_v3",
        "llm_input_char_count": 1234,
        "full_package_char_count": 5678,
        "llm_input_compression_ratio": 0.2173,
        "llm_input_metrics": {
            "llm_input_char_count": 1234,
            "full_package_char_count": 5678,
            "compression_ratio": 0.2173,
        },
    }
    artifact = {
        "schema_version": "chapter_generation_task_artifact_v0.1",
        "generated_at": "2026-05-10T00:00:01+08:00",
        "unit_id": unit_id,
        "target_node_id": "NODE-1",
        "chapter_path": chapter_path,
        "package_hash": "hash",
        "status": "completed",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com",
        "task": task,
        "chapter": chapter,
    }
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")


def _wait_for_job(client: TestClient, job_id: str, timeout_seconds: float = 8.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_job = None
    while time.monotonic() < deadline:
        last_job = client.get(f"/api/v1/jobs/{job_id}").json()["data"]
        if last_job["status"] not in {"pending", "running"}:
            return last_job
        time.sleep(0.05)
    raise AssertionError(f"任务未在预期时间内完成：{last_job}")
