"""前端 MVP 对应的 FastAPI 应用。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import backend_settings, load_env_file
from .db import connect_postgres
from .dev_store import DevJsonStore
from .knowledge_base import (
    DEFAULT_EXCELLENT_BID_TYPE,
    EXCELLENT_BID_PROJECT_TYPE_LABELS,
    EXCELLENT_BID_TYPE_LABELS,
    RAG_KNOWLEDGE_TYPE_LABELS,
    delete_excellent_bid_source,
    get_excellent_bid_detail,
    load_or_migrate_excellent_bid_manifest,
    migrate_existing_excellent_bid_indexes,
    normalize_excellent_bid_source_metadata,
    rebuild_excellent_bid_manifest_from_library,
    search_excellent_bid_slices,
    upsert_excellent_bid_source,
)
from .model_config_store import read_model_runtime_config, write_model_env_config
from .repository import AccountRecord, BackendRepository, JobRecord, ProjectRecord, UploadedFileRecord
from .schemas import (
    AccountCreateRequest,
    AccountResponse,
    AccountUpdateRequest,
    ApiEnvelope,
    AssistantChatRequest,
    AuthLoginRequest,
    AuthRegisterRequest,
    ExcellentBidUploadResponse,
    JobCreateRequest,
    JobResponse,
    ModelProviderConfigRequest,
    OutlineUpdateRequest,
    OnlyOfficeCallbackRequest,
    ProjectCreateRequest,
    ProjectResponse,
    UploadedFileResponse,
    WordExportProfileUpdateRequest,
    WordExportRequest,
)
from .storage import LocalStorageService
from .timing_profile import PROFILE_JSON_NAME, PROFILE_MD_NAME, build_project_timing_profile, write_project_timing_profile
from .workflow_executor import (
    SUPPORTED_WORKFLOW_JOB_TYPES,
    WorkflowExecutionError,
    execute_workflow_job,
)
from construction_bidding_agent.document_parser.docx_section_material_index import (
    build_docx_section_material_index,
    write_section_material_index_outputs,
)
from construction_bidding_agent.document_parser.excellent_bid_material_library import (
    build_excellent_bid_material_library_from_files,
    write_excellent_bid_material_library_outputs,
)
from construction_bidding_agent.chapter_generator.chapter_batch_runner import BATCH_ARTIFACT_SCHEMA_VERSION
from construction_bidding_agent.chapter_generator.full_bid_docx_exporter import export_full_bid_docx_from_files
from construction_bidding_agent.chapter_generator.word_export_profile import (
    default_word_export_profile,
    load_word_export_profile,
    reset_word_export_profile,
    save_word_export_profile,
)
from construction_bidding_agent.chapter_generator.word_version_manager import (
    read_word_quality_summary,
    word_version_paths,
)
from construction_bidding_agent.agent import AgentController
from construction_bidding_agent.assistant import build_assistant_chat_response, build_project_ai_summary
from construction_bidding_agent.bid_templates import load_bid_templates, parse_bid_template_docx, recommend_bid_templates, save_bid_template
from construction_bidding_agent.llm_gateway import llm_audit_context, summarize_llm_audit_for_job
from construction_bidding_agent.qa_fusion import PlatformAssistantContext, build_platform_assistant_response
from construction_bidding_agent.rag import search_project_rag_materials


ROOT = Path(__file__).resolve().parents[3]
WEB_DIR = ROOT / "web"
DEFAULT_BID_TEMPLATE_DIR = ROOT / "configs" / "bid_templates"
DB_UNAVAILABLE_UNTIL = 0.0
REQUEST_ID_CTX: ContextVar[str | None] = ContextVar("request_id", default=None)
AUTH_COOKIE_NAME = "zhibiao_session"
AUTH_SESSION_TTL_SECONDS = 60 * 60 * 12
PASSWORD_HASH_ITERATIONS = 210_000
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_DISPLAY_NAME = "系统管理员"
DEFAULT_ADMIN_INITIAL_PASSWORD = "Admin@123456"
PUBLIC_API_PATHS = {
    "/api/v1/health",
    "/api/v1/auth/me",
    "/api/v1/auth/register",
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
}


def create_app() -> FastAPI:
    load_env_file(ROOT / ".env")
    app = FastAPI(title="Construction Bidding AI Agent", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid4().hex
        token = REQUEST_ID_CTX.set(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            REQUEST_ID_CTX.reset(token)

    @app.middleware("http")
    async def require_authenticated_user(request: Request, call_next):
        if not _auth_required() or _is_public_request(request):
            return await call_next(request)
        account = _account_from_session_cookie(request.cookies.get(AUTH_COOKIE_NAME))
        if account is None:
            return _error_response(
                status_code=401,
                code="UNAUTHORIZED",
                message="请先登录后再使用智标工坊。",
                detail="AUTH_REQUIRED",
                request_id=_request_id_from_request(request),
            )
        request.state.current_account = account
        return await call_next(request)

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        return _error_response(
            status_code=exc.status_code,
            code=_http_error_code(exc.status_code),
            message=_http_error_message(exc.detail),
            detail=exc.detail,
            request_id=_request_id_from_request(request),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_exception(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(
            status_code=422,
            code="VALIDATION_ERROR",
            message="请求参数格式不正确。",
            detail=_validation_error_details(exc),
            request_id=_request_id_from_request(request),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
        return _error_response(
            status_code=500,
            code="INTERNAL_SERVER_ERROR",
            message="服务内部异常，请稍后重试或联系管理员。",
            detail=str(exc),
            request_id=_request_id_from_request(request),
        )

    @app.get("/")
    def index() -> FileResponse:
        index_path = WEB_DIR / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="前端页面尚未构建。")
        return FileResponse(index_path)

    @app.get("/api/v1/health")
    def health() -> ApiEnvelope:
        settings = backend_settings()
        storage = LocalStorageService(settings.storage_root)
        storage.ensure_layout()
        runtime = _runtime_storage_mode(settings)
        return _ok(
            {
                "status": "ok" if runtime["mode"] != "unavailable" else "degraded",
                "database": runtime["database"],
                "runtime_storage": runtime["mode"],
                "app_env": runtime["app_env"],
                "allow_dev_json_fallback": runtime["allow_dev_json_fallback"],
                "storage_root": str(settings.storage_root),
                "auth_required": _auth_required(),
            }
        )

    @app.get("/api/v1/auth/me")
    def current_auth_user(request: Request) -> ApiEnvelope:
        account = _account_from_session_cookie(request.cookies.get(AUTH_COOKIE_NAME))
        return _ok({"authenticated": account is not None, "account": _account_public_payload(account) if account else None, "auth_required": _auth_required()})

    @app.post("/api/v1/auth/register")
    def register_auth_user(
        request: AuthRegisterRequest,
        response: Response,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        username = _normalize_account_username(request.username)
        _ensure_default_admin_account(repo)
        existing = _account_by_username(username, repo, include_sensitive=True)
        role = "bid_staff"
        if existing is not None:
            if _account_password_hash(existing):
                raise HTTPException(status_code=409, detail="账号已存在，请直接登录。")
            account = _upgrade_legacy_account_password(
                repo=repo,
                account=existing,
                password=request.password,
                display_name=request.display_name,
                role=str(existing.get("role") or role),
                department=request.department,
                phone=request.phone,
                email=request.email,
            )
            account = _mark_account_login(str(account["account_id"]), repo) or account
            _set_auth_cookie(response, account)
            return _ok({"account": _account_public_payload(account), "upgraded_legacy_account": True})
        account = _create_account_record(
            repo=repo,
            username=username,
            display_name=request.display_name,
            password=request.password,
            role=role,
            department=request.department,
            phone=request.phone,
            email=request.email,
            status="active",
            auth_mode="password",
        )
        account = _mark_account_login(str(account["account_id"]), repo) or account
        _set_auth_cookie(response, account)
        return _ok({"account": _account_public_payload(account)})

    @app.post("/api/v1/auth/login")
    def login_auth_user(
        request: AuthLoginRequest,
        response: Response,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        username = _normalize_account_username(request.username)
        _ensure_default_admin_account(repo)
        account = _account_by_username(username, repo, include_sensitive=True)
        if account is None or not _verify_password(request.password, _account_password_hash(account)):
            raise HTTPException(status_code=401, detail="账号或密码不正确。")
        if account.get("status") != "active":
            raise HTTPException(status_code=403, detail="账号已停用，请联系管理员。")
        account = _mark_account_login(str(account["account_id"]), repo) or account
        _set_auth_cookie(response, account)
        return _ok({"account": _account_public_payload(account)})

    @app.post("/api/v1/auth/logout")
    def logout_auth_user(response: Response) -> ApiEnvelope:
        response.delete_cookie(AUTH_COOKIE_NAME, path="/")
        return _ok({"authenticated": False})

    @app.get("/api/v1/accounts")
    def list_accounts(
        request: Request,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        _require_admin_account(request)
        accounts = _account_list_payload(repo)
        return _ok({"accounts": accounts, "total": len(accounts), "auth_enforced": _auth_required()})

    @app.post("/api/v1/accounts")
    def create_account(
        request: AccountCreateRequest,
        http_request: Request,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        _require_admin_account(http_request)
        username = _normalize_account_username(request.username)
        role = _normalize_account_role(request.role)
        status = _normalize_account_status(request.status)
        if _account_by_username(username, repo) is not None:
            raise HTTPException(status_code=409, detail="账号已存在。")
        account = _create_account_record(
            repo=repo,
            username=username,
            display_name=request.display_name,
            password=request.password,
            role=role,
            department=request.department,
            phone=request.phone,
            email=request.email,
            status=status,
            auth_mode="password",
        )
        return _ok(_account_public_payload(account))

    @app.patch("/api/v1/accounts/{account_id}")
    def update_account(
        account_id: str,
        request: AccountUpdateRequest,
        http_request: Request,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        _require_admin_account(http_request)
        updates = _account_update_payload(request)
        if not updates:
            account = _account_payload_or_404(account_id, repo)
            return _ok(_account_public_payload(account))
        if repo is None:
            existing = _dev_store().get_account(account_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="账号不存在。")
            updated = _dev_store().update_account(account_id, {**updates, "updated_at": _utc_now_iso()})
            return _ok(_account_public_payload(updated or existing))
        updated_record = repo.update_account(account_id, updates)
        if updated_record is None:
            raise HTTPException(status_code=404, detail="账号不存在。")
        return _ok(_account_public_payload(updated_record))

    @app.post("/api/v1/accounts/{account_id}/status")
    def update_account_status(
        account_id: str,
        request: AccountUpdateRequest,
        http_request: Request,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        _require_admin_account(http_request)
        status = _normalize_account_status(request.status or "active")
        if repo is None:
            existing = _dev_store().get_account(account_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="账号不存在。")
            updated = _dev_store().update_account(account_id, {"status": status, "updated_at": _utc_now_iso()})
            return _ok(_account_public_payload(updated or existing))
        updated_record = repo.update_account(account_id, {"status": status})
        if updated_record is None:
            raise HTTPException(status_code=404, detail="账号不存在。")
        return _ok(_account_public_payload(updated_record))

    @app.get("/api/v1/projects")
    def list_projects(repo: Annotated[BackendRepository | None, Depends(_repo_or_none)]) -> ApiEnvelope:
        if repo is None:
            projects = _dev_store().list_projects()
            return _ok(projects)
        return _ok([ProjectResponse.model_validate(project).model_dump(mode="json") for project in repo.list_projects()])

    @app.post("/api/v1/projects")
    def create_project(
        request: ProjectCreateRequest,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        if repo is None:
            project = _new_project_record(request)
            _dev_store().append_project(project)
            return _ok(ProjectResponse.model_validate(project).model_dump(mode="json"))
        project = repo.create_project(name=request.name, description=request.description, project_type=request.project_type)
        return _ok(ProjectResponse.model_validate(project).model_dump(mode="json"))

    @app.get("/api/v1/projects/{project_id}")
    def get_project(project_id: str, repo: Annotated[BackendRepository | None, Depends(_repo_or_none)]) -> ApiEnvelope:
        if repo is None:
            project_data = _dev_store().get_project(project_id)
            if project_data is None:
                raise HTTPException(status_code=404, detail="项目不存在。")
            return _ok(project_data)
        project = repo.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在。")
        return _ok(ProjectResponse.model_validate(project).model_dump(mode="json"))

    @app.delete("/api/v1/projects/{project_id}")
    def delete_project(project_id: str, repo: Annotated[BackendRepository | None, Depends(_repo_or_none)]) -> ApiEnvelope:
        settings = backend_settings()
        storage = LocalStorageService(settings.storage_root)
        if repo is None:
            deleted = _dev_store().delete_project(project_id)
        else:
            deleted = repo.delete_project(project_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="项目不存在。")
        _delete_project_directory(storage, project_id)
        return _ok({"project_id": project_id, "deleted": True})

    @app.get("/api/v1/projects/{project_id}/files")
    def list_project_files(
        project_id: str,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        if repo is None:
            return _ok(_dev_store().list_files(project_id))
        files = repo.list_uploaded_files(project_id=project_id)
        return _ok([UploadedFileResponse.model_validate(item).model_dump(mode="json") for item in files])

    @app.delete("/api/v1/projects/{project_id}/files/{file_id}")
    def delete_project_file(
        project_id: str,
        file_id: str,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        settings = backend_settings()
        storage = LocalStorageService(settings.storage_root)
        if repo is None:
            record = _dev_store().delete_file(project_id, file_id)
        else:
            deleted = repo.delete_uploaded_file(project_id=project_id, file_id=file_id)
            record = UploadedFileResponse.model_validate(deleted).model_dump(mode="json") if deleted else None
        if record is None:
            raise HTTPException(status_code=404, detail="文件不存在。")
        deleted_file = _delete_storage_file(storage, str(record.get("storage_uri") or ""))
        return _ok({"project_id": project_id, "file_id": file_id, "deleted": True, "deleted_file": deleted_file})

    @app.get("/api/v1/projects/{project_id}/workflow-summary")
    def project_workflow_summary(
        project_id: str,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        project = _project_payload(project_id, repo)
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在。")
        files = _project_files_payload(project_id, repo)
        jobs = _project_jobs_payload(project_id, repo)
        return _ok(_build_workflow_summary(project, files, jobs))

    @app.get("/api/v1/projects/{project_id}/assistant/summary")
    def get_project_ai_summary(
        project_id: str,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        summary = _workflow_summary_or_404(project_id, repo)
        recommendation = AgentController().recommend_next_action(summary)
        return _ok(build_project_ai_summary(summary, recommendation))

    @app.post("/api/v1/projects/{project_id}/assistant/chat")
    def assistant_chat(
        project_id: str,
        request: AssistantChatRequest,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
        http_request: Request,
    ) -> ApiEnvelope:
        current_account = getattr(http_request.state, "current_account", None)
        return _ok(
            _build_project_assistant_chat_payload(
                project_id=project_id,
                request=request,
                repo=repo,
                current_account=current_account,
            )
        )

    @app.post("/api/v1/assistant/chat")
    def platform_assistant_chat(
        request: AssistantChatRequest,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
        http_request: Request,
    ) -> ApiEnvelope:
        current_account = getattr(http_request.state, "current_account", None)
        project_id = str(request.project_id or "").strip()
        workflow_summary = None
        project_answer = None
        rag_preview = None
        if project_id:
            workflow_summary = _workflow_summary_or_404(project_id, repo)
            project_answer = _build_project_assistant_chat_payload(
                project_id=project_id,
                request=request,
                repo=repo,
                current_account=current_account,
                workflow_summary=workflow_summary,
            )
            rag_preview = search_project_rag_materials(
                project_root=ROOT,
                storage_root=backend_settings().storage_root,
                workflow_summary=workflow_summary,
                query=request.message,
                limit=3,
            )
        else:
            rag_preview = search_excellent_bid_slices(
                project_root=ROOT,
                storage_root=backend_settings().storage_root,
                query=request.message,
                limit=3,
            )
        return _ok(
            build_platform_assistant_response(
                PlatformAssistantContext(
                    message=request.message,
                    active_view=request.active_view or "home",
                    active_step=request.active_step,
                    project_id=project_id or None,
                    selected_template_id=request.selected_template_id,
                    account_context=_assistant_account_context(
                        current_account=current_account,
                        request=request,
                    ),
                    workflow_summary=workflow_summary,
                    project_answer=project_answer,
                    knowledge_manifest=load_or_migrate_excellent_bid_manifest(
                        project_root=ROOT,
                        storage_root=backend_settings().storage_root,
                    ),
                    bid_templates=load_bid_templates(DEFAULT_BID_TEMPLATE_DIR),
                    rag_preview=rag_preview,
                )
            )
        )

    @app.get("/api/v1/bid-templates")
    def list_bid_templates() -> ApiEnvelope:
        return _ok({"templates": load_bid_templates(DEFAULT_BID_TEMPLATE_DIR)})

    @app.post("/api/v1/bid-templates/upload")
    def upload_bid_template(
        http_request: Request,
        file: UploadFile = File(...),
        name: str | None = Form(default=None),
        project_type: str = Form(default="construction"),
        version: str = Form(default="v1"),
        description: str | None = Form(default=None),
    ) -> ApiEnvelope:
        _require_admin_account(http_request)
        uploaded = _create_bid_template_upload(
            file=file,
            name=name,
            project_type=project_type,
            version=version,
            description=description,
        )
        return _ok(uploaded)

    @app.get("/api/v1/projects/{project_id}/bid-template/recommendation")
    def get_bid_template_recommendation(
        project_id: str,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        summary = _workflow_summary_or_404(project_id, repo)
        return _ok(_bid_template_recommendation(summary))

    @app.get("/api/v1/projects/{project_id}/rag/materials")
    def get_project_rag_materials(
        project_id: str,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
        q: str = "",
        chapter: str = "",
        limit: int = 5,
    ) -> ApiEnvelope:
        summary = _workflow_summary_or_404(project_id, repo)
        return _ok(
            search_project_rag_materials(
                project_root=ROOT,
                storage_root=backend_settings().storage_root,
                workflow_summary=summary,
                query=q,
                chapter=chapter,
                limit=max(1, min(limit, 20)),
            )
        )

    @app.get("/api/v1/projects/{project_id}/artifacts")
    def list_project_artifacts(
        project_id: str,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        if _project_payload(project_id, repo) is None:
            raise HTTPException(status_code=404, detail="项目不存在。")
        return _ok(_artifact_summary(_workflow_artifacts(project_id)))

    @app.get("/api/v1/projects/{project_id}/artifacts/{artifact_key}")
    def get_project_artifact(
        project_id: str,
        artifact_key: str,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        artifact_path = _resolve_project_artifact_path(project_id, artifact_key, repo)
        metadata = _artifact_metadata(artifact_key, artifact_path)
        suffix = artifact_path.suffix.lower()
        if suffix == ".json":
            text = artifact_path.read_text(encoding="utf-8")
            try:
                content = json.loads(text)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=422, detail="JSON 产物无法解析。") from exc
            return _ok({**metadata, "render_type": "json", "json": content, "text": json.dumps(content, ensure_ascii=False, indent=2)})
        if suffix in {".md", ".txt"}:
            render_type = "markdown" if suffix == ".md" else "text"
            return _ok({**metadata, "render_type": render_type, "text": artifact_path.read_text(encoding="utf-8")})
        raise HTTPException(status_code=415, detail="该产物暂不支持页面内预览，请下载后查看。")

    @app.get("/api/v1/projects/{project_id}/artifacts/{artifact_key}/download")
    def download_project_artifact(
        project_id: str,
        artifact_key: str,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> FileResponse:
        artifact_path = _resolve_project_artifact_path(project_id, artifact_key, repo)
        media_type = mimetypes.guess_type(artifact_path.name)[0] or "application/octet-stream"
        return FileResponse(artifact_path, media_type=media_type, filename=artifact_path.name)

    @app.patch("/api/v1/projects/{project_id}/outline")
    def update_project_outline(
        project_id: str,
        request: OutlineUpdateRequest,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        if _project_payload(project_id, repo) is None:
            raise HTTPException(status_code=404, detail="项目不存在。")
        outline_path = _resolve_project_artifact_path(project_id, "outline", repo)
        outline = _read_artifact_json(outline_path)
        if not outline:
            raise HTTPException(status_code=404, detail="目录尚未生成。")
        saved_outline = _apply_outline_editor_nodes(outline, request.nodes)
        outline_path.write_text(json.dumps(saved_outline, ensure_ascii=False, indent=2), encoding="utf-8")
        return _ok({"outline_preview": _outline_preview(saved_outline), "artifact": _artifact_metadata("outline", outline_path)})

    @app.get("/api/v1/word/export-profiles/default")
    def get_default_word_export_profile() -> ApiEnvelope:
        return _ok(default_word_export_profile())

    @app.get("/api/v1/projects/{project_id}/word/summary")
    def get_project_word_summary(
        project_id: str,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        if _project_payload(project_id, repo) is None:
            raise HTTPException(status_code=404, detail="项目不存在。")
        documents_dir = _project_documents_dir(project_id)
        return _ok(_word_summary_payload(project_id, documents_dir))

    @app.get("/api/v1/projects/{project_id}/word/export-profile")
    def get_project_word_export_profile(
        project_id: str,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        if _project_payload(project_id, repo) is None:
            raise HTTPException(status_code=404, detail="项目不存在。")
        return _ok(load_word_export_profile(_word_export_profile_path(project_id)))

    @app.put("/api/v1/projects/{project_id}/word/export-profile")
    def update_project_word_export_profile(
        project_id: str,
        request: WordExportProfileUpdateRequest,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        if _project_payload(project_id, repo) is None:
            raise HTTPException(status_code=404, detail="项目不存在。")
        return _ok(save_word_export_profile(_word_export_profile_path(project_id), request.profile))

    @app.post("/api/v1/projects/{project_id}/word/export-profile/reset")
    def reset_project_word_export_profile(
        project_id: str,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        if _project_payload(project_id, repo) is None:
            raise HTTPException(status_code=404, detail="项目不存在。")
        return _ok(reset_word_export_profile(_word_export_profile_path(project_id)))

    @app.post("/api/v1/projects/{project_id}/word/export")
    def export_project_word(
        project_id: str,
        request: WordExportRequest,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        if _project_payload(project_id, repo) is None:
            raise HTTPException(status_code=404, detail="项目不存在。")
        return _ok(_export_project_word_now(project_id, request))

    @app.get("/api/v1/projects/{project_id}/word/download")
    def download_project_word(
        project_id: str,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
        version: str = "latest",
    ) -> FileResponse:
        if _project_payload(project_id, repo) is None:
            raise HTTPException(status_code=404, detail="项目不存在。")
        target = _resolve_word_download_path(project_id, version)
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(target, media_type=media_type, filename=target.name)

    @app.get("/api/v1/projects/{project_id}/word/onlyoffice-config")
    def get_project_onlyoffice_config(
        project_id: str,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
        version: str = "latest",
    ) -> ApiEnvelope:
        if _project_payload(project_id, repo) is None:
            raise HTTPException(status_code=404, detail="项目不存在。")
        return _ok(_onlyoffice_config(project_id, version))

    @app.get("/api/v1/projects/{project_id}/word/onlyoffice-file")
    def get_project_onlyoffice_file(
        project_id: str,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
        version: str = "latest",
    ) -> FileResponse:
        if _project_payload(project_id, repo) is None:
            raise HTTPException(status_code=404, detail="项目不存在。")
        target = _resolve_word_download_path(project_id, version)
        return FileResponse(
            target,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=target.name,
        )

    @app.post("/api/v1/projects/{project_id}/word/onlyoffice-callback")
    def project_onlyoffice_callback(
        project_id: str,
        request: OnlyOfficeCallbackRequest,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> dict:
        if _project_payload(project_id, repo) is None:
            raise HTTPException(status_code=404, detail="项目不存在。")
        saved = _save_onlyoffice_callback_file(project_id, request)
        return {"error": 0, "saved": saved}

    @app.post("/api/v1/projects/{project_id}/word/onlyoffice-force-save")
    def project_onlyoffice_force_save(
        project_id: str,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
        version: str = "latest",
    ) -> ApiEnvelope:
        if _project_payload(project_id, repo) is None:
            raise HTTPException(status_code=404, detail="项目不存在。")
        command_result = _request_onlyoffice_force_save(project_id, version)
        saved_path = _wait_for_onlyoffice_review_file(project_id)
        documents_dir = _project_documents_dir(project_id)
        return _ok(
            {
                "command": command_result,
                "saved": saved_path is not None,
                "saved_version": "review_editing" if saved_path else None,
                "summary": _word_summary_payload(project_id, documents_dir),
                "download_url": f"/api/v1/projects/{project_id}/word/download?version=latest",
            }
        )

    @app.post("/api/v1/projects/{project_id}/files")
    async def upload_project_file(
        project_id: str,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
        business_type: Annotated[str, Form()] = "tender_document",
        file: UploadFile = File(...),
    ) -> ApiEnvelope:
        settings = backend_settings()
        storage = LocalStorageService(settings.storage_root)
        content = await file.read()
        stored = storage.save_project_upload(project_id, file.filename or "upload.bin", content)
        if repo is None:
            record = _new_uploaded_file_record(
                project_id=project_id,
                business_type=business_type,
                file_name=file.filename or stored.path.name,
                mime_type=file.content_type,
                file_size=stored.size,
                storage_uri=stored.storage_uri,
                sha256=hashlib.sha256(content).hexdigest(),
            )
            _dev_store().append_file(record)
        else:
            record = repo.create_uploaded_file(
                project_id=project_id,
                business_type=business_type,
                file_name=file.filename or stored.path.name,
                mime_type=file.content_type,
                file_size=stored.size,
                storage_uri=stored.storage_uri,
                sha256=hashlib.sha256(content).hexdigest(),
            )
        return _ok(UploadedFileResponse.model_validate(record).model_dump(mode="json"))

    @app.post("/api/v1/projects/{project_id}/jobs")
    def create_project_job(
        project_id: str,
        request: JobCreateRequest,
        background_tasks: BackgroundTasks,
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)],
    ) -> ApiEnvelope:
        project = _project_payload(project_id, repo)
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在。")
        if request.job_type == "chapter_llm_generation" and not request.run_all and not request.target_unit_ids:
            raise HTTPException(status_code=400, detail="请先选择要生成的章节，或使用一键生成全部。")
        config_snapshot = _job_config_snapshot(request)
        idempotency_key = _job_idempotency_key(project_id, request.job_type, config_snapshot)
        existing_job = _find_active_project_job(
            project_id=project_id,
            job_type=request.job_type,
            idempotency_key=idempotency_key,
            repo=repo,
        )
        if existing_job is not None:
            return _ok(existing_job)
        metadata = _job_initial_metadata(
            job_type=request.job_type,
            idempotency_key=idempotency_key,
            storage="dev_json" if repo is None else "postgres",
        )
        if repo is None:
            job = _new_job_record(
                project_id=project_id,
                job_type=request.job_type,
                message=request.message or "任务已创建，等待后台执行。",
                config_snapshot=config_snapshot,
                metadata=metadata,
            )
            store = _dev_store()
            store.append_job(job)
            if request.job_type in SUPPORTED_WORKFLOW_JOB_TYPES:
                background_tasks.add_task(_run_background_workflow_job, job.job_id, use_dev_store=True)
            return _ok(_job_payload(job))
        job = repo.create_job(
            project_id=project_id,
            job_type=request.job_type,
            status="pending",
            message=request.message or "任务已创建，等待后台执行。",
            config_snapshot=config_snapshot,
            metadata=metadata,
        )
        if request.job_type in SUPPORTED_WORKFLOW_JOB_TYPES:
            background_tasks.add_task(_run_background_workflow_job, job.job_id, use_dev_store=False)
        return _ok(_job_payload(job))

    @app.get("/api/v1/jobs/{job_id}")
    def get_job(job_id: str, repo: Annotated[BackendRepository | None, Depends(_repo_or_none)]) -> ApiEnvelope:
        if repo is None:
            job_data = _dev_store().get_job(job_id)
            if job_data is None:
                raise HTTPException(status_code=404, detail="任务不存在。")
            return _ok(_job_payload(job_data))
        job = repo.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在。")
        return _ok(_job_payload(job))

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, repo: Annotated[BackendRepository | None, Depends(_repo_or_none)]) -> ApiEnvelope:
        if repo is None:
            store = _dev_store()
            job_data = store.get_job(job_id)
            if job_data is None:
                raise HTTPException(status_code=404, detail="任务不存在。")
            if job_data.get("status") not in {"pending", "running"}:
                return _ok(_job_payload(job_data))
            updated = store.update_job(job_id, _cancelled_job_updates("任务已手动取消。"))
            return _ok(_job_payload(updated or job_data))
        job = repo.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在。")
        if job.status in {"pending", "running"}:
            repo.update_job_progress(job_id, **_cancelled_job_progress_kwargs("任务已手动取消。"))
            job = repo.get_job(job_id) or job
        return _ok(_job_payload(job))

    @app.get("/api/v1/model-config")
    def get_model_config() -> ApiEnvelope:
        return _ok(read_model_runtime_config().model_dump(mode="json"))

    @app.patch("/api/v1/model-config")
    def update_model_config(request: ModelProviderConfigRequest) -> ApiEnvelope:
        write_model_env_config(request, ROOT / ".env")
        return _ok(read_model_runtime_config().model_dump(mode="json"))

    @app.get("/api/v1/rag/sources")
    def list_rag_sources() -> ApiEnvelope:
        return _ok(_rag_sources_manifest())

    @app.post("/api/v1/rag/sources/migrate")
    def migrate_rag_sources() -> ApiEnvelope:
        settings = backend_settings()
        storage = LocalStorageService(settings.storage_root)
        storage.ensure_layout()
        manifest = migrate_existing_excellent_bid_indexes(project_root=ROOT, storage_root=settings.storage_root)
        return _ok(manifest)

    @app.post("/api/v1/rag/sources/upload")
    def upload_rag_source(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        title: str | None = Form(default=None),
        knowledge_type: str = Form(default="excellent_bid"),
        project_type: str = Form(...),
        bid_type: str | None = Form(default=None),
        allow_image_reuse: bool = Form(default=True),
        desensitized_confirmed: bool = Form(default=False),
        remarks: str | None = Form(default=None),
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)] = None,
    ) -> ApiEnvelope:
        payload = _create_excellent_bid_upload(
            file=file,
            title=title,
            knowledge_type=knowledge_type,
            project_type=project_type,
            bid_type=bid_type,
            allow_image_reuse=allow_image_reuse,
            desensitized_confirmed=desensitized_confirmed,
            remarks=remarks,
            repo=repo,
        )
        job = payload.get("job") or {}
        job_id = str(job.get("job_id") or "")
        if job_id:
            background_tasks.add_task(_run_excellent_bid_ingestion_job, job_id, repo is None)
        return _ok(ExcellentBidUploadResponse(**payload).model_dump(mode="json"))

    @app.get("/api/v1/rag/sources/search")
    def search_rag_sources(q: str = "", source_bid_id: str | None = None, limit: int = 30) -> ApiEnvelope:
        return _ok(_search_rag_source_slices(q=q, source_bid_id=source_bid_id, limit=limit))

    @app.get("/api/v1/rag/sources/{source_bid_id}")
    def get_rag_source(source_bid_id: str, limit: int = 20) -> ApiEnvelope:
        detail = _rag_source_detail_or_404(source_bid_id, limit=limit)
        return _ok(detail)

    @app.delete("/api/v1/rag/sources/{source_bid_id}")
    def delete_rag_source(
        source_bid_id: str,
        http_request: Request,
    ) -> ApiEnvelope:
        _require_admin_account(http_request)
        return _ok(_delete_rag_source_or_404(source_bid_id))

    @app.post("/api/v1/rag/sources/{source_bid_id}/delete")
    def post_delete_rag_source(
        source_bid_id: str,
        http_request: Request,
    ) -> ApiEnvelope:
        _require_admin_account(http_request)
        return _ok(_delete_rag_source_or_404(source_bid_id))

    @app.get("/api/v1/knowledge-base/excellent-bids")
    def list_excellent_bids() -> ApiEnvelope:
        return _ok(_rag_sources_manifest())

    @app.post("/api/v1/knowledge-base/excellent-bids/migrate")
    def migrate_excellent_bids() -> ApiEnvelope:
        settings = backend_settings()
        storage = LocalStorageService(settings.storage_root)
        storage.ensure_layout()
        manifest = migrate_existing_excellent_bid_indexes(project_root=ROOT, storage_root=settings.storage_root)
        return _ok(manifest)

    @app.post("/api/v1/knowledge-base/excellent-bids/upload")
    def upload_excellent_bid(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        title: str | None = Form(default=None),
        knowledge_type: str = Form(default="excellent_bid"),
        project_type: str = Form(...),
        bid_type: str | None = Form(default=None),
        allow_image_reuse: bool = Form(default=True),
        desensitized_confirmed: bool = Form(default=False),
        remarks: str | None = Form(default=None),
        repo: Annotated[BackendRepository | None, Depends(_repo_or_none)] = None,
    ) -> ApiEnvelope:
        payload = _create_excellent_bid_upload(
            file=file,
            title=title,
            knowledge_type=knowledge_type,
            project_type=project_type,
            bid_type=bid_type,
            allow_image_reuse=allow_image_reuse,
            desensitized_confirmed=desensitized_confirmed,
            remarks=remarks,
            repo=repo,
        )
        job = payload.get("job") or {}
        job_id = str(job.get("job_id") or "")
        if job_id:
            background_tasks.add_task(_run_excellent_bid_ingestion_job, job_id, repo is None)
        return _ok(ExcellentBidUploadResponse(**payload).model_dump(mode="json"))

    @app.get("/api/v1/knowledge-base/excellent-bids/search")
    def search_excellent_bid_materials(q: str = "", source_bid_id: str | None = None, limit: int = 30) -> ApiEnvelope:
        return _ok(_search_rag_source_slices(q=q, source_bid_id=source_bid_id, limit=limit))

    @app.get("/api/v1/knowledge-base/excellent-bids/{source_bid_id}")
    def get_excellent_bid(source_bid_id: str, limit: int = 20) -> ApiEnvelope:
        return _ok(_rag_source_detail_or_404(source_bid_id, limit=limit))

    @app.delete("/api/v1/knowledge-base/excellent-bids/{source_bid_id}")
    def delete_excellent_bid(
        source_bid_id: str,
        http_request: Request,
    ) -> ApiEnvelope:
        _require_admin_account(http_request)
        return _ok(_delete_rag_source_or_404(source_bid_id))

    @app.post("/api/v1/knowledge-base/excellent-bids/{source_bid_id}/delete")
    def post_delete_excellent_bid(
        source_bid_id: str,
        http_request: Request,
    ) -> ApiEnvelope:
        _require_admin_account(http_request)
        return _ok(_delete_rag_source_or_404(source_bid_id))

    return app


def _repo() -> Iterator[BackendRepository]:
    settings = backend_settings()
    connection = connect_postgres(settings.database_url)
    try:
        yield BackendRepository(connection)
    finally:
        connection.close()


def _repo_or_none() -> Iterator[BackendRepository | None]:
    global DB_UNAVAILABLE_UNTIL
    settings = backend_settings()
    allow_fallback = _allow_dev_json_fallback(settings)
    if time.monotonic() < DB_UNAVAILABLE_UNTIL:
        if not allow_fallback:
            raise RuntimeError("PostgreSQL 当前不可用，生产环境禁止降级到 dev JSON 存储。")
        yield None
        return
    try:
        connection = connect_postgres(settings.database_url, connect_timeout_seconds=1)
    except Exception as exc:
        DB_UNAVAILABLE_UNTIL = time.monotonic() + 10
        if not allow_fallback:
            raise RuntimeError("PostgreSQL 当前不可用，生产环境禁止降级到 dev JSON 存储。") from exc
        yield None
        return
    try:
        yield BackendRepository(connection)
    finally:
        connection.close()


def _allow_dev_json_fallback(settings=None) -> bool:
    settings = settings or backend_settings()
    if settings.app_env in {"production", "prod"}:
        return False
    return bool(settings.allow_dev_json_fallback)


def _runtime_storage_mode(settings=None) -> dict:
    settings = settings or backend_settings()
    allow_fallback = _allow_dev_json_fallback(settings)
    db_status = "not_checked"
    mode = "postgres"
    try:
        connection = connect_postgres(settings.database_url, connect_timeout_seconds=1)
        try:
            connection.execute("SELECT 1")
            db_status = "ok"
        finally:
            connection.close()
    except Exception as exc:
        db_status = f"unavailable: {type(exc).__name__}"
        mode = "dev_json" if allow_fallback else "unavailable"
    return {
        "mode": mode,
        "database": db_status,
        "app_env": settings.app_env,
        "allow_dev_json_fallback": allow_fallback,
    }


def _dev_store() -> DevJsonStore:
    settings = backend_settings()
    return DevJsonStore(settings.storage_root / "app" / "dev_state.json")


ACCOUNT_ROLE_LABELS = {
    "admin": "系统管理员",
    "bid_manager": "投标经理",
    "bid_staff": "编标人员",
    "technical_reviewer": "技术负责人",
    "viewer": "只读查看",
}

ACCOUNT_STATUS_LABELS = {
    "active": "启用",
    "disabled": "停用",
}


def _auth_required() -> bool:
    raw = os.getenv("AUTH_REQUIRED")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _is_public_request(request: Request) -> bool:
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        return True
    return path in PUBLIC_API_PATHS


def _require_admin_account(request: Request) -> dict:
    if not _auth_required():
        return {"role": "admin", "username": "dev"}
    account = getattr(request.state, "current_account", None)
    if not isinstance(account, dict):
        raise HTTPException(status_code=401, detail="请先登录。")
    if account.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅系统管理员可管理账户。")
    return account


def _create_account_record(
    *,
    repo: BackendRepository | None,
    username: str,
    display_name: str,
    password: str,
    role: str,
    department: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    status: str = "active",
    auth_mode: str = "password",
) -> dict:
    password_hash = _hash_password(password)
    if repo is None:
        account = _new_dev_account_record(
            username=username,
            display_name=display_name,
            role=role,
            department=department,
            phone=phone,
            email=email,
            status=status,
            password_hash=password_hash,
            auth_mode=auth_mode,
        )
        _dev_store().append_account(account)
        return _account_payload(account)
    account_record = repo.create_account(
        username=username,
        display_name=display_name,
        role=role,
        department=department,
        phone=phone,
        email=email,
        status=status,
        metadata={"auth_mode": auth_mode},
        password_hash=password_hash,
    )
    return _account_payload(account_record)


def _account_list_payload(repo: BackendRepository | None) -> list[dict]:
    if repo is None:
        accounts = _dev_store().list_accounts()
        return [_account_public_payload(item) for item in accounts]
    accounts = repo.list_accounts()
    return [_account_public_payload(item) for item in accounts]


def _account_payload_or_404(account_id: str, repo: BackendRepository | None) -> dict:
    if repo is None:
        account = _dev_store().get_account(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="账号不存在。")
        return _account_payload(account)
    account = repo.get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在。")
    return _account_payload(account)


def _account_by_username(username: str, repo: BackendRepository | None, *, include_sensitive: bool = False) -> dict | None:
    if repo is None:
        account = _dev_store().get_account_by_username(username)
        return _account_payload(account, include_sensitive=include_sensitive) if account else None
    account = repo.get_account_by_username(username)
    return _account_payload(account, include_sensitive=include_sensitive) if account else None


def _account_payload(account: AccountRecord | dict | None, *, include_sensitive: bool = True) -> dict:
    if isinstance(account, AccountRecord):
        data = AccountResponse.model_construct(
            account_id=account.account_id,
            username=account.username,
            display_name=account.display_name,
            role=account.role,
            role_label=ACCOUNT_ROLE_LABELS.get(account.role, account.role),
            department=account.department,
            phone=account.phone,
            email=account.email,
            status=account.status,
            status_label=ACCOUNT_STATUS_LABELS.get(account.status, account.status),
            created_at=account.created_at,
            updated_at=account.updated_at,
            last_login_at=account.last_login_at,
            metadata=account.metadata,
        ).model_dump(mode="json")
        if account.password_hash:
            metadata = dict(data.get("metadata") or {})
            metadata["password_hash"] = account.password_hash
            data["metadata"] = metadata
    else:
        data = dict(account or {})
        top_level_password_hash = data.pop("password_hash", None)
        if top_level_password_hash:
            metadata = dict(data.get("metadata") or {})
            metadata.setdefault("password_hash", top_level_password_hash)
            data["metadata"] = metadata
    role = _normalize_account_role(data.get("role") or "bid_staff")
    status = _normalize_account_status(data.get("status") or "active")
    data["role"] = role
    data["role_label"] = ACCOUNT_ROLE_LABELS.get(role, role)
    data["status"] = status
    data["status_label"] = ACCOUNT_STATUS_LABELS.get(status, status)
    if not include_sensitive:
        metadata = data.get("metadata")
        if isinstance(metadata, dict) and "password_hash" in metadata:
            metadata = dict(metadata)
            metadata.pop("password_hash", None)
            data["metadata"] = metadata
    return data


def _account_public_payload(account: AccountRecord | dict | None) -> dict:
    return _account_payload(account, include_sensitive=False)


def _assistant_account_context(*, current_account: dict | None, request: AssistantChatRequest) -> dict[str, Any]:
    account = current_account or {}
    return {
        "account_id": request.account_id or account.get("account_id"),
        "username": account.get("username"),
        "display_name": request.account_display_name or account.get("display_name"),
        "role": request.account_role or account.get("role"),
        "role_label": request.account_role_label or account.get("role_label"),
    }


def _build_project_assistant_chat_payload(
    *,
    project_id: str,
    request: AssistantChatRequest,
    repo: BackendRepository | None,
    current_account: dict | None,
    workflow_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build existing project-scoped assistant answer without touching workflow execution."""

    summary = workflow_summary or _workflow_summary_or_404(project_id, repo)
    recommendation = AgentController().recommend_next_action(summary)
    templates = _bid_template_recommendation(summary)
    rag_preview = search_project_rag_materials(
        project_root=ROOT,
        storage_root=backend_settings().storage_root,
        workflow_summary=summary,
        query=request.message,
        limit=3,
    )
    return build_assistant_chat_response(
        message=request.message,
        workflow_summary=summary,
        agent_recommendation=recommendation,
        template_recommendation=templates,
        rag_preview=rag_preview,
        active_view=request.active_view,
        active_step=request.active_step,
        account_context=_assistant_account_context(
            current_account=current_account,
            request=request,
        ),
    )


def _has_any_account(repo: BackendRepository | None) -> bool:
    if repo is None:
        return bool(_dev_store().list_accounts())
    return bool(repo.list_accounts(limit=1))


def _default_admin_password() -> str:
    return os.getenv("DEFAULT_ADMIN_PASSWORD") or DEFAULT_ADMIN_INITIAL_PASSWORD


def _ensure_default_admin_account(repo: BackendRepository | None) -> dict:
    existing = _account_by_username(DEFAULT_ADMIN_USERNAME, repo, include_sensitive=True)
    if existing is None:
        return _create_account_record(
            repo=repo,
            username=DEFAULT_ADMIN_USERNAME,
            display_name=DEFAULT_ADMIN_DISPLAY_NAME,
            password=_default_admin_password(),
            role="admin",
            status="active",
            auth_mode="default_admin",
        )
    needs_update = False
    updates: dict[str, object] = {}
    metadata = dict(existing.get("metadata") or {})
    if not _account_password_hash(existing):
        password_hash = _hash_password(_default_admin_password())
        metadata["password_hash"] = password_hash
        metadata["auth_mode"] = "default_admin"
        updates["password_hash"] = password_hash
        updates["metadata"] = metadata
        needs_update = True
    if existing.get("role") != "admin":
        updates["role"] = "admin"
        needs_update = True
    if existing.get("status") != "active":
        updates["status"] = "active"
        needs_update = True
    if not str(existing.get("display_name") or "").strip():
        updates["display_name"] = DEFAULT_ADMIN_DISPLAY_NAME
        needs_update = True
    if not needs_update:
        return existing
    account_id = str(existing.get("account_id") or "")
    if repo is None:
        updated = _dev_store().update_account(account_id, {**updates, "updated_at": _utc_now_iso()})
        return _account_payload(updated or existing)
    updated_record = repo.update_account(account_id, updates)
    return _account_payload(updated_record) if updated_record else existing


def _upgrade_legacy_account_password(
    *,
    repo: BackendRepository | None,
    account: dict,
    password: str,
    display_name: str,
    role: str,
    department: str | None = None,
    phone: str | None = None,
    email: str | None = None,
) -> dict:
    metadata = dict(account.get("metadata") or {})
    metadata["password_hash"] = _hash_password(password)
    metadata["auth_mode"] = "password"
    updates = {
        "display_name": display_name or account.get("display_name"),
        "role": _normalize_account_role(role),
        "department": department if department is not None else account.get("department"),
        "phone": phone if phone is not None else account.get("phone"),
        "email": email if email is not None else account.get("email"),
        "password_hash": metadata["password_hash"],
        "status": "active",
        "metadata": metadata,
    }
    account_id = str(account.get("account_id") or "")
    if repo is None:
        updated = _dev_store().update_account(account_id, {**updates, "updated_at": _utc_now_iso()})
        return _account_payload(updated or account)
    updated_record = repo.update_account(account_id, updates)
    if updated_record is None:
        raise HTTPException(status_code=404, detail="账号不存在。")
    return _account_payload(updated_record)


def _new_dev_account_record(
    *,
    username: str,
    display_name: str,
    role: str,
    department: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    status: str = "active",
    password_hash: str | None = None,
    auth_mode: str = "password",
) -> dict:
    from .repository import _new_id

    now = _utc_now_iso()
    return {
        "account_id": _new_id("U"),
        "username": username,
        "display_name": display_name,
        "role": _normalize_account_role(role),
        "department": department,
        "phone": phone,
        "email": email,
        "status": _normalize_account_status(status),
        "created_at": now,
        "updated_at": now,
        "last_login_at": None,
        "metadata": {"storage": "dev_json", "auth_mode": auth_mode, "password_hash": password_hash},
    }


def _account_update_payload(request: AccountUpdateRequest) -> dict:
    updates = request.model_dump(exclude_unset=True)
    if "role" in updates and updates["role"] is not None:
        updates["role"] = _normalize_account_role(updates["role"])
    if "status" in updates and updates["status"] is not None:
        updates["status"] = _normalize_account_status(updates["status"])
    return updates


def _normalize_account_username(value: object) -> str:
    username = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{2,64}", username):
        raise HTTPException(status_code=422, detail="账号只能包含字母、数字、下划线、点和短横线，长度 2-64。")
    return username


def _normalize_account_role(value: object) -> str:
    role = str(value or "bid_staff").strip()
    if role not in ACCOUNT_ROLE_LABELS:
        raise HTTPException(status_code=422, detail="不支持的账号角色。")
    return role


def _normalize_account_status(value: object) -> str:
    status = str(value or "active").strip()
    if status not in ACCOUNT_STATUS_LABELS:
        raise HTTPException(status_code=422, detail="不支持的账号状态。")
    return status


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_HASH_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${base64.b64encode(salt).decode('ascii')}${base64.b64encode(digest).decode('ascii')}"


def _verify_password(password: str, encoded_hash: str | None) -> bool:
    if not encoded_hash:
        return False
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = base64.b64decode(salt_text.encode("ascii"))
        expected = base64.b64decode(digest_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _account_password_hash(account: dict | None) -> str | None:
    metadata = (account or {}).get("metadata")
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("password_hash")
    return str(value) if value else None


def _set_auth_cookie(response: Response, account: dict) -> None:
    signed = _sign_auth_session(account, max_age_seconds=AUTH_SESSION_TTL_SECONDS)
    response.set_cookie(
        AUTH_COOKIE_NAME,
        signed,
        max_age=AUTH_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )


def _sign_auth_session(account: dict, *, max_age_seconds: int) -> str:
    issued_at = int(time.time())
    payload = {
        "account_id": account.get("account_id"),
        "username": account.get("username"),
        "role": account.get("role"),
        "iat": issued_at,
        "exp": issued_at + max_age_seconds,
    }
    body = _base64url(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    signature = _base64url(hmac.new(_auth_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def _account_from_session_cookie(cookie_value: str | None) -> dict | None:
    if not cookie_value or "." not in cookie_value:
        return None
    body, signature = cookie_value.rsplit(".", 1)
    expected = _base64url(hmac.new(_auth_secret(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception:
        return None
    if int(payload.get("exp") or 0) < int(time.time()):
        return None
    username = str(payload.get("username") or "")
    if not username:
        return None
    repo_iter = _repo_or_none()
    try:
        repo = next(repo_iter)
        account = _account_by_username(username, repo, include_sensitive=False)
    except Exception:
        return None
    finally:
        repo_iter.close()
    if account is None or account.get("status") != "active":
        return None
    if payload.get("account_id") and payload.get("account_id") != account.get("account_id"):
        return None
    return account


def _auth_secret() -> bytes:
    secret = os.getenv("APP_SECRET_KEY") or os.getenv("AUTH_SECRET_KEY") or os.getenv("API_KEY") or "local-dev-auth-secret"
    return secret.encode("utf-8")


def _mark_account_login(account_id: str, repo: BackendRepository | None) -> dict | None:
    if repo is None:
        return _account_payload(_dev_store().mark_account_login(account_id, _utc_now_iso()))
    return _account_payload(repo.mark_account_login(account_id))


def _utc_now_iso() -> str:
    from .db import utc_now

    return utc_now().isoformat()


def _project_payload(project_id: str, repo: BackendRepository | None) -> dict | None:
    if repo is None:
        return _dev_store().get_project(project_id)
    project = repo.get_project(project_id)
    if project is None:
        return None
    return ProjectResponse.model_validate(project).model_dump(mode="json")


def _project_files_payload(project_id: str, repo: BackendRepository | None) -> list[dict]:
    if repo is None:
        return _dev_store().list_files(project_id)
    return [
        UploadedFileResponse.model_validate(item).model_dump(mode="json")
        for item in repo.list_uploaded_files(project_id=project_id)
    ]


def _project_jobs_payload(project_id: str, repo: BackendRepository | None) -> list[dict]:
    if repo is None:
        return [_job_payload(item) for item in _dev_store().load()["jobs"] if item.get("project_id") == project_id]
    return [_job_payload(item) for item in repo.list_jobs(project_id=project_id)]


def _workflow_summary_or_404(project_id: str, repo: BackendRepository | None) -> dict:
    project = _project_payload(project_id, repo)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在。")
    files = _project_files_payload(project_id, repo)
    jobs = _project_jobs_payload(project_id, repo)
    return _build_workflow_summary(project, files, jobs)


def _bid_template_recommendation(workflow_summary: dict) -> dict:
    templates = load_bid_templates(DEFAULT_BID_TEMPLATE_DIR)
    return recommend_bid_templates(templates, workflow_summary)


def _create_bid_template_upload(
    *,
    file: UploadFile,
    name: str | None,
    project_type: str,
    version: str,
    description: str | None,
) -> dict:
    file_name = file.filename or "bid_template.json"
    suffix = Path(file_name).suffix.lower()
    content = _read_upload_file_bytes(file)
    if suffix == ".json":
        try:
            template = json.loads(content.decode("utf-8-sig"))
        except Exception as exc:
            raise HTTPException(status_code=422, detail="模板 JSON 无法解析，请检查文件格式。") from exc
        if name:
            template["name"] = name
        if project_type:
            template["project_type"] = project_type
        if version:
            template["version"] = version
        if description:
            template["description"] = description
        parse_mode = "json"
    elif suffix == ".docx":
        tmp_dir = backend_settings().storage_root / "template_uploads" / f"TPL{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid4().hex[:6]}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / Path(file_name).name
        tmp_path.write_bytes(content)
        try:
            template = parse_bid_template_docx(
                tmp_path,
                name=name,
                project_type=project_type,
                version=version,
                description=description,
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"模板 DOCX 解析失败：{exc}") from exc
        parse_mode = "docx"
    else:
        raise HTTPException(status_code=415, detail="模板上传暂支持 JSON 和 DOCX 文件。")
    try:
        saved = save_bid_template(DEFAULT_BID_TEMPLATE_DIR, template)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    templates = load_bid_templates(DEFAULT_BID_TEMPLATE_DIR)
    return {
        "template": saved,
        "templates": templates,
        "parse_mode": parse_mode,
        "message": "模板已导入，可在模板库中预览；不会自动覆盖任何项目目录或正文。",
    }


def _rag_sources_manifest() -> dict:
    settings = backend_settings()
    storage = LocalStorageService(settings.storage_root)
    storage.ensure_layout()
    return load_or_migrate_excellent_bid_manifest(project_root=ROOT, storage_root=settings.storage_root)


def _search_rag_source_slices(*, q: str = "", source_bid_id: str | None = None, limit: int = 30) -> dict:
    settings = backend_settings()
    return search_excellent_bid_slices(
        project_root=ROOT,
        storage_root=settings.storage_root,
        query=q,
        source_bid_id=source_bid_id,
        limit=max(1, min(limit, 100)),
    )


def _rag_source_detail_or_404(source_bid_id: str, *, limit: int = 20) -> dict:
    settings = backend_settings()
    detail = get_excellent_bid_detail(
        project_root=ROOT,
        storage_root=settings.storage_root,
        source_bid_id=source_bid_id,
        limit=max(1, min(limit, 100)),
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="参考资料不存在。")
    return detail


def _delete_rag_source_or_404(source_bid_id: str) -> dict:
    settings = backend_settings()
    storage = LocalStorageService(settings.storage_root)
    storage.ensure_layout()
    result = delete_excellent_bid_source(
        project_root=ROOT,
        storage_root=settings.storage_root,
        source_bid_id=source_bid_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="参考资料不存在。")
    return result


def _create_excellent_bid_upload(
    *,
    file: UploadFile,
    title: str | None,
    knowledge_type: str,
    project_type: str,
    bid_type: str | None,
    allow_image_reuse: bool,
    desensitized_confirmed: bool,
    remarks: str | None,
    repo: BackendRepository | None,
) -> dict:
    from .db import utc_now

    if knowledge_type not in RAG_KNOWLEDGE_TYPE_LABELS:
        raise HTTPException(status_code=422, detail="请选择有效的参考资料类型。")
    if project_type not in EXCELLENT_BID_PROJECT_TYPE_LABELS:
        raise HTTPException(status_code=422, detail="请选择有效的项目类型。")
    resolved_bid_type = bid_type or DEFAULT_EXCELLENT_BID_TYPE
    if resolved_bid_type not in EXCELLENT_BID_TYPE_LABELS:
        raise HTTPException(status_code=422, detail="请选择有效的标书类型。")
    if not desensitized_confirmed:
        raise HTTPException(status_code=422, detail="上传参考资料前必须确认文件已完成脱敏或允许入库。")

    file_name = file.filename or "excellent_bid.docx"
    if Path(file_name).suffix.lower() != ".docx":
        raise HTTPException(status_code=415, detail="一期参考资料自动入库仅支持 DOCX 文件。")

    content = _read_upload_file_bytes(file)
    source_bid_id = f"SRC{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid4().hex[:6].upper()}"
    settings = backend_settings()
    storage = LocalStorageService(settings.storage_root)
    storage.ensure_layout()
    stored = storage.save_knowledge_base_original(source_bid_id, file_name, content)
    source = normalize_excellent_bid_source_metadata(
        {
            "source_bid_id": source_bid_id,
            "title": (title or Path(file_name).stem).strip() or Path(file_name).stem,
            "source_type": "docx_only",
            "source_type_label": "Word 优秀标书",
            "status": "processing",
            "knowledge_type": knowledge_type,
            "project_type": project_type,
            "bid_type": resolved_bid_type,
            "allow_image_reuse": allow_image_reuse,
            "desensitized_confirmed": desensitized_confirmed,
            "remarks": remarks,
            "uploaded_at": utc_now().isoformat(),
            "original_file_names": [file_name],
            "original_uris": [stored.storage_uri],
            "slice_count": 0,
            "table_count": 0,
            "image_count": 0,
            "warning_count": 0,
            "warnings": [],
        }
    )
    metadata = {
        "source_bid_id": source_bid_id,
        "knowledge_type": knowledge_type,
        "knowledge_type_label": source["knowledge_type_label"],
        "project_type": project_type,
        "project_type_label": source["project_type_label"],
        "bid_type": resolved_bid_type,
        "bid_type_label": source["bid_type_label"],
        "allow_image_reuse": allow_image_reuse,
        "desensitized_confirmed": desensitized_confirmed,
        "remarks": remarks,
    }
    sha256 = hashlib.sha256(content).hexdigest()
    if repo is None:
        file_record = _new_uploaded_file_record(
            project_id=None,
            business_type="excellent_bid",
            file_name=file_name,
            mime_type=file.content_type,
            file_size=stored.size,
            storage_uri=stored.storage_uri,
            sha256=sha256,
            related_source_bid_id=source_bid_id,
            metadata=metadata,
        )
        job_record = _new_job_record(
            project_id=None,
            job_type="excellent_bid_ingestion",
            message="优秀标书已上传，等待解析入库。",
            config_snapshot=metadata,
            metadata={"storage": "dev_json", **metadata},
        )
        store = _dev_store()
        store.append_file(file_record)
        store.append_job(job_record)
    else:
        file_record = repo.create_uploaded_file(
            project_id=None,
            business_type="excellent_bid",
            file_name=file_name,
            mime_type=file.content_type,
            file_size=stored.size,
            storage_uri=stored.storage_uri,
            sha256=sha256,
            related_source_bid_id=source_bid_id,
            metadata=metadata,
        )
        job_record = repo.create_job(
            project_id=None,
            job_type="excellent_bid_ingestion",
            status="pending",
            message="优秀标书已上传，等待解析入库。",
            config_snapshot=metadata,
            metadata=metadata,
        )

    upsert_excellent_bid_source(project_root=ROOT, storage_root=settings.storage_root, source=source)
    return {
        "source": source,
        "file": UploadedFileResponse.model_validate(file_record).model_dump(mode="json"),
        "job": JobResponse.model_validate(job_record).model_dump(mode="json"),
    }


def _read_upload_file_bytes(file: UploadFile) -> bytes:
    content = file.file.read()
    if not content:
        raise HTTPException(status_code=422, detail="上传文件为空。")
    return content


def _run_excellent_bid_ingestion_job(job_id: str, use_dev_store: bool) -> None:
    """解析上传的 DOCX 优秀标书，并更新优秀标书素材库。"""

    if use_dev_store:
        store = _dev_store()
        job_data = store.get_job(job_id)
        if job_data is None:
            return
        store.update_job(job_id, _running_job_updates())
        try:
            result = _execute_excellent_bid_ingestion(job_data.get("config_snapshot") or job_data.get("metadata") or {})
            store.update_job(job_id, _succeeded_job_updates("优秀标书解析入库完成。", result))
        except Exception as exc:
            store.update_job(job_id, _failed_job_updates(str(exc)))
            _mark_excellent_bid_ingestion_failed(job_data.get("config_snapshot") or job_data.get("metadata") or {}, str(exc))
        return

    repo_iter = _repo()
    repo = next(repo_iter)
    try:
        job = repo.get_job(job_id)
        if job is None:
            return
        repo.update_job_progress(job_id, **_running_job_progress_kwargs())
        try:
            result = _execute_excellent_bid_ingestion(job.config_snapshot or job.metadata or {})
            repo.update_job_progress(job_id, **_succeeded_job_progress_kwargs("优秀标书解析入库完成。", result))
        except Exception as exc:
            repo.update_job_progress(job_id, **_failed_job_progress_kwargs(str(exc)))
            _mark_excellent_bid_ingestion_failed(job.config_snapshot or job.metadata or {}, str(exc))
    finally:
        try:
            next(repo_iter)
        except StopIteration:
            pass


def _execute_excellent_bid_ingestion(metadata: dict) -> dict:
    source_bid_id = str(metadata.get("source_bid_id") or "")
    if not source_bid_id:
        raise ValueError("缺少优秀标书来源 ID。")
    settings = backend_settings()
    storage = LocalStorageService(settings.storage_root)
    storage.ensure_layout()
    source = _excellent_bid_source_from_manifest(source_bid_id, settings.storage_root)
    original_uri = next(iter(source.get("original_uris") or []), None)
    if not original_uri:
        raise ValueError("找不到优秀标书原始文件。")
    original_path = storage.resolve_local_path(str(original_uri))
    if not original_path.exists():
        raise ValueError("优秀标书原始文件不存在。")

    source = normalize_excellent_bid_source_metadata({**source, "status": "processing"})
    upsert_excellent_bid_source(project_root=ROOT, storage_root=settings.storage_root, source=source)
    base_name = _safe_output_stem(source.get("title") or source_bid_id)
    index_path = storage.save_knowledge_base_extracted(
        source_bid_id,
        "indexes",
        f"{base_name}_section_material_index.json",
        b"{}",
    ).path
    report_path = storage.save_knowledge_base_extracted(
        source_bid_id,
        "reports",
        f"{base_name}_section_material_index.md",
        b"",
    ).path
    index_result = build_docx_section_material_index(original_path)
    write_section_material_index_outputs(index_result, index_path, report_path)
    index_data = json.loads(index_path.read_text(encoding="utf-8"))
    index_data["source_bid_id"] = source_bid_id
    index_data["source_id"] = source_bid_id
    index_data["source_title"] = source.get("title")
    index_data["source_metadata"] = {
        "knowledge_type": source.get("knowledge_type"),
        "knowledge_type_label": source.get("knowledge_type_label"),
        "project_type": source.get("project_type"),
        "project_type_label": source.get("project_type_label"),
        "bid_type": source.get("bid_type"),
        "bid_type_label": source.get("bid_type_label"),
        "allow_image_reuse": source.get("allow_image_reuse"),
        "desensitized_confirmed": source.get("desensitized_confirmed"),
    }
    index_path.write_text(json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")

    index_paths = _excellent_bid_single_source_index_paths(settings.storage_root)
    library_result = build_excellent_bid_material_library_from_files(
        index_paths,
        library_id="excellent_bid_material_library",
    )
    library_json_path = settings.storage_root / "knowledge_base" / "excellent_bids" / "indexes" / "excellent_bid_material_library_uploaded.json"
    library_report_path = settings.storage_root / "knowledge_base" / "excellent_bids" / "reports" / "excellent_bid_material_library_uploaded.md"
    write_excellent_bid_material_library_outputs(library_result, library_json_path, library_report_path)
    source_overrides = _excellent_bid_source_overrides(settings.storage_root)
    manifest = rebuild_excellent_bid_manifest_from_library(
        project_root=ROOT,
        storage_root=settings.storage_root,
        library_path=library_json_path,
        source_overrides=source_overrides,
    )
    updated_source = _find_manifest_source(manifest, source_bid_id)
    if updated_source:
        updated_source = normalize_excellent_bid_source_metadata(
            {
                **updated_source,
                "status": "pending_review",
                "index_uri": storage.to_uri(index_path),
                "index_file_name": index_path.name,
                "report_uri": storage.to_uri(report_path),
                "report_file_name": report_path.name,
            }
        )
        manifest = upsert_excellent_bid_source(project_root=ROOT, storage_root=settings.storage_root, source=updated_source)
    return {
        "source_bid_id": source_bid_id,
        "source": updated_source or source,
        "manifest": {
            "source_count": manifest.get("source_count"),
            "slice_count": manifest.get("slice_count"),
            "table_count": manifest.get("table_count"),
            "image_count": manifest.get("image_count"),
        },
        "index_uri": storage.to_uri(index_path),
        "report_uri": storage.to_uri(report_path),
    }


def _mark_excellent_bid_ingestion_failed(metadata: dict, message: str) -> None:
    source_bid_id = str(metadata.get("source_bid_id") or "")
    if not source_bid_id:
        return
    settings = backend_settings()
    source = _excellent_bid_source_from_manifest(source_bid_id, settings.storage_root)
    if not source:
        return
    warnings = [str(item) for item in source.get("warnings", [])]
    warnings.append(message)
    source = normalize_excellent_bid_source_metadata({**source, "status": "failed", "warnings": warnings, "warning_count": len(warnings)})
    upsert_excellent_bid_source(project_root=ROOT, storage_root=settings.storage_root, source=source)


def _run_background_workflow_job(job_id: str, *, use_dev_store: bool) -> None:
    """在 FastAPI 后台任务中执行项目工作流，并把状态写回任务记录。"""

    if use_dev_store:
        store = _dev_store()
        job_data = store.get_job(job_id)
        if job_data is None:
            return
        project_id = str(job_data.get("project_id") or "")
        project = store.get_project(project_id)
        if project is None:
            store.update_job(job_id, _failed_job_updates("项目不存在，任务无法执行。"))
            return
        files = store.list_files(project_id)
        store.update_job(job_id, _running_job_updates())
        job_record = _job_record_from_payload(store.get_job(job_id) or job_data)
        final_job = _run_job_if_supported(
            project,
            files,
            job_record,
            repo=None,
            progress_callback=lambda percent, message, extra=None: store.update_job(
                job_id,
                _progress_job_updates(percent, message, extra),
            ),
        )
        if final_job is not None:
            store.update_job(job_id, final_job)
        _refresh_project_timing_profile(project_id, repo=None)
        return

    repo_iter = _repo()
    repo = next(repo_iter)
    try:
        job = repo.get_job(job_id)
        if job is None or job.project_id is None:
            return
        project = _project_payload(job.project_id, repo)
        if project is None:
            repo.update_job_progress(job_id, **_failed_job_progress_kwargs("项目不存在，任务无法执行。"))
            return
        files = _project_files_payload(job.project_id, repo)
        repo.update_job_progress(job_id, **_running_job_progress_kwargs())
        job = repo.get_job(job_id) or job
        _run_job_if_supported(
            project,
            files,
            job,
            repo=repo,
            progress_callback=lambda percent, message, extra=None: repo.update_job_progress(
                job_id,
                **_progress_job_progress_kwargs(percent, message, extra),
            ),
        )
        _refresh_project_timing_profile(job.project_id, repo=repo)
    finally:
        try:
            next(repo_iter)
        except StopIteration:
            pass


def _running_job_updates() -> dict:
    from .db import utc_now

    now = utc_now()
    return {
        "status": "running",
        "progress_total": 1,
        "progress_completed": 0,
        "progress_failed": 0,
        "progress_percent": 5.0,
        "message": "任务正在后台执行，正在准备解析环境。",
        "started_at": now,
        "updated_at": now,
    }


def _running_job_progress_kwargs() -> dict:
    from .db import utc_now

    return {
        "status": "running",
        "progress_total": 1,
        "progress_completed": 0,
        "progress_failed": 0,
        "progress_percent": 5.0,
        "message": "任务正在后台执行，正在准备解析环境。",
        "started_at": utc_now(),
    }


def _succeeded_job_updates(message: str, result: dict | None = None) -> dict:
    from .db import utc_now

    now = utc_now()
    return {
        "status": "succeeded",
        "progress_total": 1,
        "progress_completed": 1,
        "progress_failed": 0,
        "progress_percent": 100.0,
        "message": message,
        "result_ref": json.dumps(result or {}, ensure_ascii=False),
        "ended_at": now,
        "updated_at": now,
        "metadata": result,
    }


def _succeeded_job_progress_kwargs(message: str, result: dict | None = None) -> dict:
    from .db import utc_now

    return {
        "status": "succeeded",
        "progress_total": 1,
        "progress_completed": 1,
        "progress_failed": 0,
        "progress_percent": 100.0,
        "message": message,
        "result_ref": json.dumps(result or {}, ensure_ascii=False),
        "ended_at": utc_now(),
        "metadata": result,
    }


def _progress_job_updates(percent: float, message: str, extra: dict | None = None) -> dict:
    from .db import utc_now

    now = utc_now()
    extra = extra or {}
    total = extra.get("progress_total")
    completed = extra.get("progress_completed")
    failed = extra.get("progress_failed")
    return {
        "status": "running",
        "progress_total": int(total) if total is not None else 100,
        "progress_completed": int(completed) if completed is not None else int(max(0, min(100, round(percent)))),
        "progress_failed": int(failed) if failed is not None else 0,
        "progress_percent": float(max(0.0, min(99.0, percent))),
        "message": message,
        "metadata": extra or None,
        "updated_at": now,
    }


def _progress_job_progress_kwargs(percent: float, message: str, extra: dict | None = None) -> dict:
    extra = extra or {}
    total = extra.get("progress_total")
    completed = extra.get("progress_completed")
    failed = extra.get("progress_failed")
    return {
        "status": "running",
        "progress_total": int(total) if total is not None else 100,
        "progress_completed": int(completed) if completed is not None else int(max(0, min(100, round(percent)))),
        "progress_failed": int(failed) if failed is not None else 0,
        "progress_percent": float(max(0.0, min(99.0, percent))),
        "message": message,
        "metadata": extra or None,
    }


def _failed_job_updates(message: str) -> dict:
    from .db import utc_now

    now = utc_now()
    return {
        "status": "failed",
        "progress_total": 1,
        "progress_completed": 0,
        "progress_failed": 1,
        "progress_percent": 100.0,
        "message": "任务执行失败。",
        "error_code": "WORKFLOW_EXECUTION_ERROR",
        "error_message": message,
        "ended_at": now,
        "updated_at": now,
    }


def _failed_job_progress_kwargs(message: str) -> dict:
    from .db import utc_now

    return {
        "status": "failed",
        "progress_total": 1,
        "progress_completed": 0,
        "progress_failed": 1,
        "progress_percent": 100.0,
        "message": "任务执行失败。",
        "error_code": "WORKFLOW_EXECUTION_ERROR",
        "error_message": message,
        "ended_at": utc_now(),
    }


def _excellent_bid_source_from_manifest(source_bid_id: str, storage_root: Path) -> dict:
    manifest = load_or_migrate_excellent_bid_manifest(project_root=ROOT, storage_root=storage_root)
    return _find_manifest_source(manifest, source_bid_id) or {}


def _find_manifest_source(manifest: dict, source_bid_id: str) -> dict | None:
    return next(
        (
            item
            for item in manifest.get("sources", [])
            if isinstance(item, dict) and item.get("source_bid_id") == source_bid_id
        ),
        None,
    )


def _excellent_bid_single_source_index_paths(storage_root: Path) -> list[Path]:
    root = storage_root / "knowledge_base" / "excellent_bids" / "extracted"
    if not root.exists():
        return []
    paths = sorted(root.glob("*/indexes/*section_material_index.json"))
    if paths:
        return paths
    index_dir = storage_root / "knowledge_base" / "excellent_bids" / "indexes"
    return sorted(path for path in index_dir.glob("*section_material_index.json") if path.is_file())


def _excellent_bid_source_overrides(storage_root: Path) -> dict[str, dict]:
    manifest = load_or_migrate_excellent_bid_manifest(project_root=ROOT, storage_root=storage_root)
    return {
        str(item.get("source_bid_id")): item
        for item in manifest.get("sources", [])
        if isinstance(item, dict) and item.get("source_bid_id")
    }


def _safe_output_stem(value: object) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(value or "").strip(), flags=re.UNICODE).strip("._")
    return text[:80] or "excellent_bid"


def _cancelled_job_updates(message: str) -> dict:
    from .db import utc_now

    now = utc_now()
    return {
        "status": "cancelled",
        "message": message,
        "error_code": "USER_CANCELLED",
        "error_message": message,
        "ended_at": now,
        "updated_at": now,
    }


def _cancelled_job_progress_kwargs(message: str) -> dict:
    from .db import utc_now

    return {
        "status": "cancelled",
        "message": message,
        "error_code": "USER_CANCELLED",
        "error_message": message,
        "ended_at": utc_now(),
    }


def _job_record_from_payload(data: dict) -> JobRecord:
    def parse_dt(value: object):
        from datetime import datetime

        if value is None or isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))

    return JobRecord(
        job_id=str(data.get("job_id")),
        project_id=data.get("project_id"),
        job_type=str(data.get("job_type")),
        status=str(data.get("status")),
        progress_total=data.get("progress_total"),
        progress_completed=data.get("progress_completed"),
        progress_failed=data.get("progress_failed"),
        progress_percent=data.get("progress_percent"),
        message=data.get("message"),
        result_ref=data.get("result_ref"),
        error_code=data.get("error_code"),
        error_message=data.get("error_message"),
        started_at=parse_dt(data.get("started_at")),
        ended_at=parse_dt(data.get("ended_at")),
        created_at=parse_dt(data.get("created_at")),
        updated_at=parse_dt(data.get("updated_at")),
        config_snapshot=data.get("config_snapshot"),
        metadata=data.get("metadata"),
    )


def _run_job_if_supported(
    project: dict,
    files: list[dict],
    job: JobRecord,
    *,
    repo: BackendRepository | None,
    progress_callback=None,
) -> dict | None:
    if job.job_type not in SUPPORTED_WORKFLOW_JOB_TYPES:
        return None
    from .db import utc_now

    settings = backend_settings()
    storage = LocalStorageService(settings.storage_root)
    storage.ensure_layout()
    started_at = utc_now()
    project_id = str(project.get("project_id") or job.project_id or "")
    try:
        with llm_audit_context(project_id=project_id, job_id=job.job_id, tool_name=job.job_type):
            result = execute_workflow_job(
                project_root=ROOT,
                storage=storage,
                project=project,
                files=files,
                job_id=job.job_id,
                job_type=job.job_type,
                job_config=job.config_snapshot,
                progress_callback=progress_callback,
            )
        ended_at = utc_now()
        metadata = _workflow_result_metadata_with_llm_usage(result.metadata, job_id=job.job_id, project_id=project_id)
        updates = {
            "status": result.status,
            "progress_total": result.progress_total,
            "progress_completed": result.progress_completed,
            "progress_failed": result.progress_failed,
            "progress_percent": result.progress_percent,
            "message": result.message,
            "result_ref": result.result_ref,
            "error_code": None,
            "error_message": None,
            "started_at": started_at,
            "ended_at": ended_at,
            "updated_at": ended_at,
            "metadata": {"storage": "dev_json", **metadata},
        }
        if repo is not None:
            repo.update_job_progress(
                job.job_id,
                status=result.status,
                progress_total=result.progress_total,
                progress_completed=result.progress_completed,
                progress_failed=result.progress_failed,
                progress_percent=result.progress_percent,
                message=result.message,
                result_ref=result.result_ref,
                started_at=started_at,
                ended_at=ended_at,
                metadata=metadata,
            )
            return None
        return updates
    except WorkflowExecutionError as exc:
        ended_at = utc_now()
        metadata = _workflow_result_metadata_with_llm_usage(None, job_id=job.job_id, project_id=project_id)
        updates = {
            "status": "failed",
            "progress_total": 1,
            "progress_completed": 0,
            "progress_failed": 1,
            "progress_percent": 100.0,
            "message": "任务执行失败。",
            "result_ref": None,
            "error_code": "WORKFLOW_EXECUTION_ERROR",
            "error_message": str(exc),
            "started_at": started_at,
            "ended_at": ended_at,
            "updated_at": ended_at,
            "metadata": {"storage": "dev_json", **metadata},
        }
        if repo is not None:
            repo.update_job_progress(
                job.job_id,
                status="failed",
                progress_total=1,
                progress_completed=0,
                progress_failed=1,
                progress_percent=100.0,
                message="任务执行失败。",
                error_code="WORKFLOW_EXECUTION_ERROR",
                error_message=str(exc),
                started_at=started_at,
                ended_at=ended_at,
                metadata=metadata or None,
            )
            return None
        return updates


def _workflow_result_metadata_with_llm_usage(metadata: dict | None, *, job_id: str, project_id: str | None) -> dict:
    result = dict(metadata or {})
    summary = summarize_llm_audit_for_job(job_id, project_id=project_id)
    if summary.get("call_count"):
        result["llm_usage_summary"] = summary
    return result


def _refresh_project_timing_profile(project_id: str | None, repo: BackendRepository | None) -> None:
    """刷新项目耗时画像；失败时不影响主任务状态。"""

    if not project_id:
        return
    try:
        settings = backend_settings()
        project = _project_payload(project_id, repo)
        if project is None:
            return
        files = _project_files_payload(project_id, repo)
        jobs = _project_jobs_payload(project_id, repo)
        write_project_timing_profile(
            storage_root=settings.storage_root,
            project=project,
            files=files,
            jobs=jobs,
        )
    except Exception:
        return


def _build_workflow_summary(project: dict, files: list[dict], jobs: list[dict]) -> dict:
    tender_files = [item for item in files if item.get("business_type") == "tender_document"]
    excellent_files = [item for item in files if item.get("business_type") == "excellent_bid"]
    succeeded_job_types = {item.get("job_type") for item in jobs if item.get("status") == "succeeded"}
    running_job_types = {item.get("job_type") for item in jobs if _is_active_job(item)}
    llm_generation_running = "chapter_llm_generation" in running_job_types
    artifacts = _workflow_artifacts(str(project.get("project_id") or ""))
    parse_result = _read_artifact_json(artifacts["parse_result"])
    outline = _read_artifact_json(artifacts["outline"])
    generation_summary = _read_artifact_json(artifacts["llm_generation_summary"]) or _read_artifact_json(artifacts["generation_summary"])
    chapter_inputs = _read_artifact_json(artifacts["chapter_inputs"])
    llm_state = _read_chapter_llm_state(artifacts["chapter_inputs"].parent / "chapter_llm_state")
    score_points = _score_points_summary(parse_result)
    outline_preview = _outline_preview(outline)
    generation_units = _generation_units_summary(generation_summary, outline, llm_state, chapter_inputs=chapter_inputs)
    review_items = _review_items_summary(parse_result, outline, generation_summary)
    score_point_coverage = _score_point_coverage_summary(
        score_points,
        outline_preview,
        generation_units,
        review_items_count_hint=len(review_items),
    )
    artifacts_summary = _artifact_summary(artifacts)
    ai_review_report = _ai_review_report_summary(
        score_point_coverage=score_point_coverage,
        generation_units=generation_units,
        review_items=review_items,
        artifacts=artifacts_summary,
    )
    generation_report = _generation_report_summary(
        jobs=jobs,
        score_point_coverage=score_point_coverage,
        ai_review_report=ai_review_report,
        generation_units=generation_units,
        review_items=review_items,
        artifacts=artifacts_summary,
        timing_profile=_current_project_timing_profile(project, files, jobs),
    )
    steps = [
        {
            "key": "upload",
            "title": "资料上传",
            "status": "done" if tender_files else "active",
            "hint": f"已上传 {len(tender_files)} 份招标文件，{len(excellent_files)} 份优秀标书",
        },
        {
            "key": "parse",
            "title": "解析确认",
            "status": (
                "done"
                if parse_result or "tender_parse" in succeeded_job_types
                else ("active" if tender_files or "tender_parse" in running_job_types else "pending")
            ),
            "hint": "确认项目信息、评分点和技术标准要求",
        },
        {
            "key": "outline",
            "title": "目录确认",
            "status": (
                "done"
                if outline or "outline_generation" in succeeded_job_types
                else ("active" if parse_result or "outline_generation" in running_job_types else "pending")
            ),
            "hint": "一级目录锁定评分点原文，二三级目录可人工调整",
        },
        {
            "key": "generate",
            "title": "正文生成",
            "status": (
                "active"
                if llm_generation_running
                else (
                    "done"
                    if generation_summary or "chapter_llm_generation" in succeeded_job_types or "chapter_generation" in succeeded_job_types
                    else ("active" if outline or "chapter_generation" in running_job_types else "pending")
                )
            ),
            "hint": "按章节生成正文、表格和图片引用",
        },
        {
            "key": "review",
            "title": "Word 初稿",
            "status": "pending",
            "hint": "检查评分点覆盖、图片匹配和格式",
        },
    ]
    return {
        "project": project,
        "stats": {
            "files": len(files),
            "tender_files": len(tender_files),
            "excellent_bid_files": len(excellent_files),
            "jobs": len(jobs),
            "estimated_chapters": len(generation_units) or _outline_generation_unit_count(outline) or 50,
            "score_points": len(score_points),
            "review_items": len(review_items),
        },
        "steps": steps,
        "score_points": score_points,
        "score_point_coverage": score_point_coverage,
        "ai_review_report": ai_review_report,
        "generation_report": generation_report,
        "parse_review_summary": _parse_review_summary(parse_result),
        "outline_preview": outline_preview,
        "generation_units": generation_units,
        "review_items": review_items,
        "artifacts": artifacts_summary,
        "latest_jobs": _latest_jobs_summary(jobs),
    }


def _workflow_artifacts(project_id: str) -> dict[str, Path]:
    settings = backend_settings()
    root = settings.storage_root / "projects" / project_id
    return {
        "timing_profile": root / "reports" / PROFILE_MD_NAME,
        "timing_profile_json": root / "reports" / PROFILE_JSON_NAME,
        "document_index": root / "parse" / "tender_document_index.json",
        "document_index_report": root / "parse" / "tender_document_index_report.md",
        "extraction_inputs": root / "parse" / "tender_extraction_inputs.json",
        "extraction_inputs_report": root / "parse" / "tender_extraction_inputs_report.md",
        "llm_extraction": root / "parse" / "tender_llm_extraction.json",
        "llm_extraction_report": root / "parse" / "tender_llm_extraction_report.md",
        "parse_result": root / "parse" / "tender_parse_result.json",
        "parse_report": root / "parse" / "tender_parse_report.md",
        "outline": root / "outline" / "technical_bid_outline.json",
        "outline_report": root / "outline" / "technical_bid_outline_report.md",
        "outline_refinement_inputs": root / "outline" / "outline_refinement_inputs.json",
        "outline_refinement_result": root / "outline" / "outline_refinement_result.json",
        "outline_refinement_report": root / "outline" / "outline_refinement_report.md",
        "generation_summary": root / "generation" / "chapter_generation_summary.json",
        "chapter_inputs": root / "generation" / "chapter_generation_inputs.json",
        "chapter_inputs_report": root / "generation" / "chapter_generation_inputs_report.md",
        "llm_generation_result": root / "generation" / "chapter_llm_generation_result.json",
        "llm_generation_aggregate_result": root / "generation" / "chapter_llm_generation_aggregate_result.json",
        "llm_generation_report": root / "generation" / "chapter_llm_generation_report.md",
        "llm_generation_summary": root / "generation" / "chapter_llm_generation_summary.json",
        "llm_draft_markdown": root / "documents" / "technical_bid_llm_draft_preview.md",
        "word_draft_docx": root / "documents" / "technical_bid_draft.docx",
        "word_draft_json": root / "documents" / "technical_bid_draft.json",
        "draft_markdown": root / "documents" / "technical_bid_draft.md",
    }


def _current_project_timing_profile(project: dict, files: list[dict], jobs: list[dict]) -> dict | None:
    """为页面实时构建耗时摘要；失败时退回已落盘报告。"""

    settings = backend_settings()
    try:
        return build_project_timing_profile(
            storage_root=settings.storage_root,
            project=project,
            files=files,
            jobs=jobs,
        )
    except Exception:
        project_id = str(project.get("project_id") or "")
        return _read_artifact_json(_workflow_artifacts(project_id)["timing_profile_json"])


def _read_artifact_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _artifact_summary(artifacts: dict[str, Path]) -> dict[str, dict | None]:
    result = {}
    for key, path in artifacts.items():
        if not path.exists():
            result[key] = None
            continue
        result[key] = _artifact_metadata(key, path)
    return result


def _artifact_metadata(key: str, path: Path) -> dict:
    settings = backend_settings()
    storage = LocalStorageService(settings.storage_root)
    return {
        "key": key,
        "label": _artifact_label(key),
        "exists": True,
        "file_name": path.name,
        "storage_uri": storage.to_uri(path),
        "size": path.stat().st_size,
        "previewable": path.suffix.lower() in {".md", ".txt", ".json"},
        "download_url": f"/api/v1/projects/{path.parents[1].name}/artifacts/{key}/download",
    }


def _artifact_label(key: str) -> str:
    labels = {
        "document_index": "招标文件结构索引 JSON",
        "document_index_report": "招标文件结构索引报告",
        "extraction_inputs": "抽取输入包 JSON",
        "extraction_inputs_report": "抽取输入包报告",
        "llm_extraction": "LLM 抽取结果 JSON",
        "llm_extraction_report": "LLM 抽取运行报告",
        "parse_result": "招标文件解析结果 JSON",
        "parse_report": "招标文件解析报告",
        "timing_profile": "全链路耗时画像报告",
        "timing_profile_json": "全链路耗时画像 JSON",
        "outline": "技术标目录 JSON",
        "outline_report": "技术标目录报告",
        "outline_refinement_inputs": "LLM 目录补强输入包",
        "outline_refinement_result": "LLM 目录补强结果 JSON",
        "outline_refinement_report": "LLM 目录补强报告",
        "generation_summary": "正文生成摘要 JSON",
        "chapter_inputs": "正文输入包 JSON",
        "chapter_inputs_report": "正文输入包报告",
        "llm_generation_result": "真实 LLM 正文生成结果 JSON",
        "llm_generation_aggregate_result": "真实 LLM 正文聚合结果 JSON",
        "llm_generation_report": "真实 LLM 正文生成报告",
        "llm_generation_summary": "真实 LLM 正文生成摘要 JSON",
        "llm_draft_markdown": "真实 LLM 正文预览 Markdown",
        "word_draft_docx": "Word 初稿",
        "word_draft_json": "Word 初稿结构 JSON",
        "draft_markdown": "技术标正文初稿 Markdown",
    }
    return labels.get(key, key)


def _delete_project_directory(storage: LocalStorageService, project_id: str) -> None:
    target = storage.storage_root / "projects" / project_id
    root = (storage.storage_root / "projects").resolve()
    resolved = target.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法项目目录。") from exc
    if resolved.exists():
        shutil.rmtree(resolved)


def _delete_storage_file(storage: LocalStorageService, storage_uri: str) -> bool:
    if not storage_uri:
        return False
    try:
        path = storage.resolve_local_path(storage_uri)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法文件路径。") from exc
    root = storage.storage_root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法文件路径。") from exc
    if not resolved.exists():
        return False
    resolved.unlink()
    return True


def _resolve_project_artifact_path(project_id: str, artifact_key: str, repo: BackendRepository | None) -> Path:
    if _project_payload(project_id, repo) is None:
        raise HTTPException(status_code=404, detail="项目不存在。")
    artifacts = _workflow_artifacts(project_id)
    if artifact_key not in artifacts:
        raise HTTPException(status_code=404, detail="产物不存在。")
    path = artifacts[artifact_key]
    if not path.exists():
        raise HTTPException(status_code=404, detail="产物尚未生成。")
    project_root = (backend_settings().storage_root / "projects" / project_id).resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法产物路径。") from exc
    return resolved


def _project_documents_dir(project_id: str) -> Path:
    return backend_settings().storage_root / "projects" / project_id / "documents"


def _word_export_profile_path(project_id: str) -> Path:
    return _project_documents_dir(project_id) / "word_export_profile.json"


def _word_summary_payload(project_id: str, documents_dir: Path) -> dict:
    summary = read_word_quality_summary(documents_dir)
    paths = word_version_paths(documents_dir)
    return {
        **summary,
        "project_id": project_id,
        "download_url": f"/api/v1/projects/{project_id}/word/download?version=latest",
        "profile_url": f"/api/v1/projects/{project_id}/word/export-profile",
        "files": {
            key: {
                **value,
                "download_url": f"/api/v1/projects/{project_id}/word/download?version={key}" if value.get("exists") else None,
            }
            for key, value in (summary.get("versions") or {}).items()
        },
        "paths": {
            "system_generated": str(paths["system_generated"]),
            "review_editing": str(paths["review_editing"]),
            "final_export": str(paths["final_export"]),
            "summary": str(paths["summary"]),
        },
    }


def _export_project_word_now(project_id: str, request: WordExportRequest) -> dict:
    documents_dir = _project_documents_dir(project_id)
    generation_dir = backend_settings().storage_root / "projects" / project_id / "generation"
    inputs_json = generation_dir / "chapter_generation_inputs.json"
    result_json = generation_dir / "chapter_llm_generation_aggregate_result.json"
    if not inputs_json.exists():
        raise HTTPException(status_code=404, detail="章节生成输入包尚未生成。")
    if not result_json.exists():
        raise HTTPException(status_code=404, detail="正文聚合结果尚未生成。")
    profile_path = _word_export_profile_path(project_id)
    profile: dict | Path | None = profile_path
    if request.profile is not None:
        if request.save_profile:
            profile = save_word_export_profile(profile_path, request.profile)
        else:
            profile = request.profile
    elif not profile_path.exists():
        profile = reset_word_export_profile(profile_path)
    documents_dir.mkdir(parents=True, exist_ok=True)
    summary = export_full_bid_docx_from_files(
        inputs_json,
        [result_json],
        documents_dir / "technical_bid_draft.docx",
        output_json=documents_dir / "technical_bid_draft.json",
        material_library_json=_resolve_word_export_material_library(),
        raw_root=_resolve_word_export_raw_root(),
        title="技术标 Word 成稿",
        output_mode="final",
        word_export_profile=profile,
    )
    return {
        "status": "succeeded",
        "llm_called": False,
        "summary": summary.get("word_quality_summary") or read_word_quality_summary(documents_dir),
        "download_url": f"/api/v1/projects/{project_id}/word/download?version=latest",
    }


def _resolve_word_download_path(project_id: str, version: str) -> Path:
    documents_dir = _project_documents_dir(project_id)
    paths = word_version_paths(documents_dir)
    normalized = str(version or "latest").strip()
    if normalized == "latest":
        latest = _latest_word_version_path(paths)
        if latest is not None:
            return latest
        legacy = documents_dir / "technical_bid_draft.docx"
        if legacy.exists():
            return legacy
        raise HTTPException(status_code=404, detail="Word 文件尚未生成。")
    if normalized not in {"system_generated", "review_editing", "final_export"}:
        raise HTTPException(status_code=400, detail="不支持的 Word 版本。")
    path = paths[normalized]
    if not path.exists():
        raise HTTPException(status_code=404, detail="指定 Word 版本尚未生成。")
    return path


def _latest_word_version_path(paths: dict[str, Path]) -> Path | None:
    candidates = [paths[key] for key in ["final_export", "review_editing", "system_generated"] if paths[key].exists()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def _onlyoffice_config(project_id: str, version: str) -> dict:
    settings = backend_settings()
    target = _resolve_word_download_path(project_id, version)
    version_key = str(version or "latest")
    file_url = f"{settings.backend_internal_url.rstrip('/')}/api/v1/projects/{project_id}/word/onlyoffice-file?version={version_key}"
    callback_url = f"{settings.backend_internal_url.rstrip('/')}/api/v1/projects/{project_id}/word/onlyoffice-callback"
    document_key = _onlyoffice_document_key(project_id, target)
    # 浏览器侧只需要同源路径即可，避免不同启动端口导致 OnlyOffice 脚本地址失配。
    browser_onlyoffice_url = "/onlyoffice"
    config = {
        "document_server_url": browser_onlyoffice_url,
        "jwt_enabled": bool(settings.onlyoffice_jwt_secret),
        "jwt_secret_configured": bool(settings.onlyoffice_jwt_secret),
        "editor_config": {
            "document": {
                "fileType": "docx",
                "key": document_key,
                "title": target.name,
                "url": file_url,
                "permissions": {
                    "edit": True,
                    "download": True,
                    "print": True,
                    "review": True,
                },
            },
            "documentType": "word",
            "editorConfig": {
                "callbackUrl": callback_url,
                "lang": "zh-CN",
                "mode": "edit",
                "customization": {
                    "forcesave": True,
                    "compactToolbar": False,
                    "zoom": 80,
                },
            },
        },
    }
    if settings.onlyoffice_jwt_secret:
        config["token"] = None
    return config


def _onlyoffice_document_key(project_id: str, path: Path) -> str:
    stat = path.stat() if path.exists() else None
    # v2 用于避开 Document Server 对旧 key 的转换缓存；文件变化时仍保持可预测更新。
    raw = f"v2:{project_id}:{path.name}:{stat.st_size if stat else 0}:{stat.st_mtime_ns if stat else 0}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _request_onlyoffice_force_save(project_id: str, version: str) -> dict:
    settings = backend_settings()
    target = _resolve_word_download_path(project_id, version)
    document_key = _onlyoffice_document_key(project_id, target)
    command_url = f"{settings.onlyoffice_internal_url.rstrip('/')}/command"
    payload = {"c": "forcesave", "key": document_key}
    body_payload = dict(payload)
    if settings.onlyoffice_jwt_secret:
        token = _onlyoffice_command_token(payload, settings.onlyoffice_jwt_secret)
        body_payload["token"] = token
    body = json.dumps(body_payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    request = urllib.request.Request(command_url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"OnlyOffice 保存命令发送失败：{exc}") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"OnlyOffice 保存命令返回异常：{raw[:200]}") from exc
    error_code = result.get("error")
    # 4 通常表示文档无修改或无需强制保存；此时用户下载当前最新版即可。
    if error_code not in {0, 4, "0", "4", None}:
        raise HTTPException(status_code=502, detail=f"OnlyOffice 保存失败：{result}")
    return {
        "sent": True,
        "document_key": document_key,
        "error": error_code,
        "message": result.get("message") or ("文档无新变更" if str(error_code) == "4" else "保存命令已发送"),
    }


def _onlyoffice_command_token(payload: dict, secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    unsigned = ".".join(
        [
            _base64url_json(header),
            _base64url_json(payload),
        ]
    )
    signature = hmac.new(secret.encode("utf-8"), unsigned.encode("utf-8"), hashlib.sha256).digest()
    return f"{unsigned}.{_base64url(signature)}"


def _base64url_json(data: dict) -> str:
    return _base64url(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _save_onlyoffice_callback_file(project_id: str, request: OnlyOfficeCallbackRequest) -> bool:
    if request.status not in {2, 6}:
        return False
    content = request.decoded_file_content()
    if content is None and request.url:
        with urllib.request.urlopen(_onlyoffice_callback_download_url(request.url), timeout=30) as response:
            content = response.read()
    if content is None:
        raise HTTPException(status_code=400, detail="OnlyOffice 回调缺少文件内容。")
    documents_dir = _project_documents_dir(project_id)
    documents_dir.mkdir(parents=True, exist_ok=True)
    target = word_version_paths(documents_dir)["review_editing"]
    target.write_bytes(content)
    from construction_bidding_agent.chapter_generator.word_version_manager import archive_word_version, write_word_quality_summary

    archive_word_version(target, documents_dir, "review_editing")
    write_word_quality_summary(documents_dir)
    return True


def _onlyoffice_callback_download_url(url: str) -> str:
    """把 OnlyOffice 回调文件地址改写成后端容器可访问的内部地址。"""

    parsed = urllib.parse.urlparse(url)
    if parsed.hostname in {"localhost", "127.0.0.1"}:
        rebuilt = parsed._replace(scheme="http", netloc="onlyoffice")
        return urllib.parse.urlunparse(rebuilt)
    return url


def _wait_for_onlyoffice_review_file(project_id: str, *, timeout_seconds: float = 12.0) -> Path | None:
    """等待 OnlyOffice 异步保存回调真正写入 review_editing.docx。"""

    target = word_version_paths(_project_documents_dir(project_id))["review_editing"]
    start_mtime = target.stat().st_mtime_ns if target.exists() else 0
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if target.exists() and target.stat().st_mtime_ns > start_mtime:
            return target
        time.sleep(0.35)
    if target.exists() and start_mtime > 0 and target.stat().st_mtime_ns >= start_mtime:
        return target
    return None


def _resolve_word_export_material_library() -> Path | None:
    settings = backend_settings()
    manifest_path = settings.storage_root / "knowledge_base" / "excellent_bids" / "indexes" / "library_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            aggregate = manifest.get("aggregate_index")
            if aggregate:
                path = Path(str(aggregate))
                if path.exists():
                    return path
        except Exception:
            pass
    candidates = [
        settings.storage_root / "knowledge_base" / "excellent_bids" / "indexes" / "excellent_bid_material_library_two_word_sources.json",
        ROOT / "outputs" / "json" / "excellent_bid_material_library_two_word_sources.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _resolve_word_export_raw_root() -> Path:
    settings = backend_settings()
    candidates = [
        settings.storage_root / "raw",
        ROOT / "data" / "raw",
    ]
    for path in candidates:
        if path.exists():
            return path
    return settings.storage_root / "raw"


def _score_points_summary(parse_result: dict | None) -> list[dict]:
    if not parse_result:
        return []
    items = []
    for point in parse_result.get("technical_score_points") or []:
        if not isinstance(point, dict):
            continue
        items.append(
            {
                "title": _clean_pdf_heading_spaces(point.get("catalog_level_1_title") or point.get("original_text")) or "未命名评分点",
                "score": point.get("score_value") or "未明确",
                "status": "需复核" if point.get("review_required") else "已识别",
                "source": _source_text(point.get("source_refs")),
            }
        )
    return items


def _score_point_coverage_summary(
    score_points: list[dict],
    outline_preview: list[dict],
    generation_units: list[dict],
    *,
    review_items_count_hint: int | None = None,
) -> dict:
    outline_items = _flatten_outline_preview(outline_preview)
    review_count = int(review_items_count_hint or 0)
    items = []
    for index, score_point in enumerate(score_points):
        outline_match = _find_score_outline_match(score_point, outline_items)
        generation_matches = _find_score_generation_matches(score_point, outline_match, generation_units)
        generated_count = sum(1 for item in generation_matches if _generation_unit_is_generated_for_coverage(item))
        failed_count = sum(1 for item in generation_matches if _generation_unit_is_failed_for_coverage(item))
        total_generation = len(generation_matches)
        review_required = "复核" in str(score_point.get("status") or "") or index < review_count
        outline_text = outline_match.get("path_text") or ("未找到承接目录" if outline_items else "目录待生成")
        generation_text = "正文待生成" if not generation_units else "正文待匹配"
        status_key = "pending"
        label = "待确认"

        if review_required:
            status_key = "risk"
            label = "需复核"
        if outline_items and not outline_match:
            status_key = "uncovered"
            label = "未覆盖"
        if outline_match and not generation_units:
            status_key = "pending"
            label = "目录已承接"
            generation_text = "正文待生成"
        if outline_match and generation_units:
            if not total_generation:
                status_key = "pending"
                label = "待确认"
                generation_text = "目录已承接，正文待匹配"
            elif failed_count:
                status_key = "risk"
                label = "需复核"
                generation_text = f"已生成 {generated_count}/{total_generation}，失败 {failed_count}"
            elif generated_count == total_generation:
                status_key = "covered"
                label = "已覆盖"
                generation_text = f"已生成 {generated_count}/{total_generation}"
            else:
                status_key = "pending"
                label = "待生成"
                generation_text = f"已生成 {generated_count}/{total_generation}"

        items.append(
            {
                "index": index,
                "title": score_point.get("title") or f"评分点 {index + 1}",
                "score": score_point.get("score"),
                "status_key": status_key,
                "status_label": label,
                "outline_text": outline_text,
                "outline_node_id": outline_match.get("node_id"),
                "generation_text": generation_text,
                "generation_total": total_generation,
                "generation_done": generated_count,
                "generation_failed": failed_count,
            }
        )

    covered = sum(1 for item in items if item["status_key"] == "covered")
    pending = sum(1 for item in items if item["status_key"] == "pending")
    risk = sum(1 for item in items if item["status_key"] in {"risk", "uncovered"})
    outline_covered = sum(1 for item in items if item.get("outline_node_id"))
    return {
        "schema_version": "score_point_coverage_v1",
        "summary": {
            "total": len(score_points),
            "covered": covered,
            "pending": pending,
            "risk": risk,
            "outline_covered": outline_covered,
            "has_outline": bool(outline_items),
            "has_generation_units": bool(generation_units),
        },
        "items": items[:80],
    }


def _ai_review_report_summary(
    *,
    score_point_coverage: dict,
    generation_units: list[dict],
    review_items: list[dict],
    artifacts: dict,
) -> dict:
    coverage = score_point_coverage.get("summary") or {}
    coverage_total = int(coverage.get("total") or 0)
    coverage_done = int(coverage.get("covered") or 0)
    coverage_risk = int(coverage.get("risk") or 0)
    generation_total = len(generation_units)
    generation_done = sum(1 for item in generation_units if _generation_unit_is_generated_for_coverage(item))
    generation_failed = sum(1 for item in generation_units if _generation_unit_is_failed_for_coverage(item))
    generation_pending = max(0, generation_total - generation_done - generation_failed)
    word_ready = bool((artifacts.get("word_draft_docx") or {}).get("exists") or (artifacts.get("word_draft_json") or {}).get("exists"))
    manual_count = len(review_items) + coverage_risk + generation_failed + (0 if word_ready else 1 if generation_done else 0)
    if coverage_total == 0:
        level = "waiting"
        label = "等待数据"
    elif manual_count or generation_pending:
        level = "warn"
        label = "需要复核"
    else:
        level = "ok"
        label = "状态良好"

    focus_items: list[str] = []
    for item in (score_point_coverage.get("items") or []):
        if not isinstance(item, dict):
            continue
        if item.get("status_key") in {"risk", "uncovered", "pending"}:
            focus_items.append(f"{item.get('title') or '未命名评分点'}：{item.get('generation_text') or item.get('status_label') or '待复核'}")
    for item in review_items[:5]:
        if isinstance(item, dict) and item.get("title"):
            focus_items.append(str(item["title"]))
    if not word_ready and generation_done:
        focus_items.append("正文已有生成结果，建议刷新 Word 初稿后检查目录、表格和图片。")

    return {
        "schema_version": "ai_review_report_v1",
        "level": level,
        "level_label": label,
        "metrics": {
            "score_points_total": coverage_total,
            "score_points_covered": coverage_done,
            "score_points_risk": coverage_risk,
            "chapters_total": generation_total,
            "chapters_generated": generation_done,
            "chapters_pending": generation_pending,
            "chapters_failed": generation_failed,
            "manual_review_items": manual_count,
            "word_ready": word_ready,
        },
        "focus_items": focus_items[:8],
        "summary": (
            f"评分点覆盖 {coverage_done}/{coverage_total}，"
            f"正文生成 {generation_done}/{generation_total}，"
            f"失败 {generation_failed}，人工复核项 {manual_count}。"
        ),
    }


def _generation_report_summary(
    *,
    jobs: list[dict],
    score_point_coverage: dict,
    ai_review_report: dict,
    generation_units: list[dict],
    review_items: list[dict],
    artifacts: dict,
    timing_profile: dict | None = None,
) -> dict:
    latest_job = _latest_generation_report_job(jobs)
    latest_job_summary = _job_summary_payload(latest_job) if latest_job else None
    metadata = latest_job_summary.get("metadata") if latest_job_summary else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    usage = metadata.get("llm_usage_summary") if isinstance(metadata.get("llm_usage_summary"), dict) else {}

    coverage = score_point_coverage.get("summary") if isinstance(score_point_coverage, dict) else {}
    coverage = coverage if isinstance(coverage, dict) else {}
    ai_metrics = ai_review_report.get("metrics") if isinstance(ai_review_report, dict) else {}
    ai_metrics = ai_metrics if isinstance(ai_metrics, dict) else {}
    generation_total = len(generation_units)
    generation_done = sum(1 for item in generation_units if _generation_unit_is_generated_for_coverage(item))
    generation_failed = sum(1 for item in generation_units if _generation_unit_is_failed_for_coverage(item))
    generation_pending = max(0, generation_total - generation_done - generation_failed)
    word_ready = bool((artifacts.get("word_draft_docx") or {}).get("exists") or (artifacts.get("word_draft_json") or {}).get("exists"))

    duration_seconds = _positive_float(metadata.get("duration_seconds"))
    if duration_seconds is None and latest_job_summary:
        duration_seconds = _job_duration_seconds_for_report(latest_job_summary)
    llm_call_count = _nonnegative_int(usage.get("call_count"))
    llm_failed_count = _nonnegative_int(usage.get("failed_count"))
    token_value = (
        _nonnegative_int(usage.get("estimated_total_tokens"))
        or _nonnegative_int(metadata.get("total_tokens"))
        or _nonnegative_int(metadata.get("token_count"))
    )
    token_available = token_value > 0
    stage_timings = _generation_stage_timings(timing_profile)

    raw_status = str((latest_job_summary or {}).get("status") or "")
    if not latest_job_summary and not generation_done and not word_ready:
        status = "waiting"
        status_label = "等待生成"
    elif raw_status in {"pending", "running"}:
        status = "running"
        status_label = (latest_job_summary or {}).get("status_label") or "执行中"
    elif raw_status == "failed" or generation_failed:
        status = "failed"
        status_label = "有失败项"
    elif _nonnegative_int(coverage.get("risk")) or _nonnegative_int(ai_metrics.get("manual_review_items")) or generation_pending:
        status = "warning"
        status_label = "已生成待复核"
    else:
        status = "succeeded"
        status_label = "生成完成"

    latest_job_payload = _compact_generation_report_job(latest_job_summary)
    metrics = {
        "duration_seconds": duration_seconds,
        "llm_call_count": llm_call_count,
        "llm_failed_count": llm_failed_count,
        "estimated_total_tokens": token_value,
        "token_estimate_available": token_available,
        "score_points_total": _nonnegative_int(coverage.get("total")),
        "score_points_covered": _nonnegative_int(coverage.get("covered")),
        "score_points_risk": _nonnegative_int(coverage.get("risk")),
        "chapters_total": generation_total,
        "chapters_generated": generation_done,
        "chapters_pending": generation_pending,
        "chapters_failed": generation_failed,
        "manual_review_items": _nonnegative_int(ai_metrics.get("manual_review_items")) or len(review_items),
        "word_ready": word_ready,
        "stage_timings": stage_timings,
    }

    highlights = _generation_report_highlights(
        latest_job=latest_job_payload,
        metrics=metrics,
        usage=usage,
    )
    risks = _generation_report_risks(metrics)
    next_actions = _generation_report_next_actions(status=status, metrics=metrics)
    return {
        "schema_version": "generation_report_v1",
        "available": bool(latest_job_summary or generation_done or word_ready),
        "title": "生成小结",
        "status": status,
        "status_label": status_label,
        "summary": _generation_report_text(status=status, metrics=metrics),
        "latest_job": latest_job_payload,
        "metrics": metrics,
        "models": [str(item) for item in usage.get("models") or [] if item],
        "providers": [str(item) for item in usage.get("providers") or [] if item],
        "highlights": highlights,
        "risks": risks,
        "next_actions": next_actions,
    }


def _latest_generation_report_job(jobs: list[dict]) -> dict | None:
    report_job_types = {"chapter_llm_generation", "chapter_aggregate_refresh", "chapter_generation"}
    candidates = [job for job in jobs if job.get("job_type") in report_job_types]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda job: str(job.get("updated_at") or job.get("ended_at") or job.get("created_at") or ""),
        reverse=True,
    )[0]


def _compact_generation_report_job(job: dict | None) -> dict | None:
    if not job:
        return None
    return {
        "job_id": job.get("job_id"),
        "job_type": job.get("job_type"),
        "job_label": job.get("job_label") or _job_type_label(str(job.get("job_type") or "")),
        "status": job.get("status"),
        "status_label": job.get("status_label") or _job_status_label(str(job.get("status") or "")),
        "message": job.get("message"),
        "updated_at": job.get("updated_at"),
        "ended_at": job.get("ended_at"),
    }


def _generation_report_highlights(*, latest_job: dict | None, metrics: dict, usage: dict) -> list[str]:
    highlights: list[str] = []
    if latest_job:
        highlights.append(f"最近任务：{latest_job.get('job_label') or '生成任务'} · {latest_job.get('status_label') or '状态未知'}")
    if metrics.get("duration_seconds"):
        highlights.append(f"本次耗时 {_duration_text(metrics.get('duration_seconds'))}")
    if metrics.get("llm_call_count"):
        highlights.append(f"模型调用 {metrics.get('llm_call_count')} 次")
    if metrics.get("token_estimate_available"):
        highlights.append(f"估算 token {metrics.get('estimated_total_tokens')}")
    if metrics.get("score_points_total"):
        highlights.append(f"评分点覆盖 {metrics.get('score_points_covered')}/{metrics.get('score_points_total')}")
    if metrics.get("chapters_total"):
        highlights.append(f"正文小节 {metrics.get('chapters_generated')}/{metrics.get('chapters_total')} 已生成")
    if metrics.get("word_ready"):
        highlights.append("Word 初稿已生成，可进入复核。")
    models = [str(item) for item in usage.get("models") or [] if item]
    if models:
        highlights.append(f"使用模型：{'、'.join(models[:2])}")
    return highlights[:8]


def _generation_report_risks(metrics: dict) -> list[str]:
    risks: list[str] = []
    if metrics.get("chapters_failed"):
        risks.append(f"{metrics.get('chapters_failed')} 个小节生成失败，建议优先重试。")
    if metrics.get("llm_failed_count"):
        risks.append(f"模型调用失败 {metrics.get('llm_failed_count')} 次，请检查失败章节和模型配置。")
    if metrics.get("score_points_risk"):
        risks.append(f"{metrics.get('score_points_risk')} 个评分点未覆盖或需要复核。")
    if metrics.get("manual_review_items"):
        risks.append(f"{metrics.get('manual_review_items')} 项内容需要人工确认。")
    if metrics.get("chapters_generated") and not metrics.get("word_ready"):
        risks.append("正文已有生成结果，但 Word 初稿尚未刷新。")
    return risks[:8]


def _generation_report_next_actions(*, status: str, metrics: dict) -> list[str]:
    if status == "waiting":
        return ["先确认目录，再选择 1-3 个典型章节试跑。"]
    actions: list[str] = []
    if status == "running":
        actions.append("等待当前生成任务结束，小助手会继续刷新耗时和进度。")
    if metrics.get("chapters_failed"):
        actions.append("优先点击“重试失败”，处理失败小节后再刷新 Word 初稿。")
    if metrics.get("score_points_risk"):
        actions.append("逐项核对未覆盖评分点，确认是否需要补章节或补正文。")
    if metrics.get("chapters_generated") and not metrics.get("word_ready"):
        actions.append("刷新 Word 初稿，再检查目录、表格、图片和页眉页脚。")
    if metrics.get("word_ready"):
        actions.append("打开 Word 初稿复核，重点检查评分点响应和格式风险。")
    if not actions:
        actions.append("抽查重点章节措辞、引用依据和企业模板匹配度。")
    return actions[:5]


def _generation_report_text(*, status: str, metrics: dict) -> str:
    if status == "waiting":
        return "完成正文生成或刷新 Word 后，这里会汇总耗时、token、评分点覆盖和复核风险。"
    duration = _duration_text(metrics.get("duration_seconds")) if metrics.get("duration_seconds") else "暂未记录"
    token = f"估算 token {metrics.get('estimated_total_tokens')}" if metrics.get("token_estimate_available") else "token 暂无精确统计"
    return (
        f"本次生成耗时 {duration}，模型调用 {metrics.get('llm_call_count') or 0} 次，{token}。"
        f"评分点覆盖 {metrics.get('score_points_covered')}/{metrics.get('score_points_total')}，"
        f"正文生成 {metrics.get('chapters_generated')}/{metrics.get('chapters_total')}，"
        f"失败 {metrics.get('chapters_failed')}，人工复核项 {metrics.get('manual_review_items')}。"
    )


def _generation_stage_timings(timing_profile: dict | None) -> list[dict]:
    """返回给前端展示的五步耗时表，隐藏内部调试字段。"""

    if not isinstance(timing_profile, dict):
        return []
    stage_metrics = timing_profile.get("stage_metrics")
    if not isinstance(stage_metrics, list):
        return []
    label_overrides = {
        "upload": "上传资料",
        "tender_parse": "解析确认",
        "outline_generation": "生成目录",
        "chapter_generation": "准备正文输入",
        "chapter_llm_generation": "生成正文",
        "chapter_aggregate_refresh": "Word 整理",
    }
    timings: list[dict] = []
    for index, item in enumerate(stage_metrics, start=1):
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if key == "chapter_generation":
            # 轻量输入包准备经常和正文生成合并完成，前端只在有真实耗时时展示。
            if _positive_float(item.get("duration_seconds")) is None:
                continue
        timings.append(
            {
                "key": key,
                "step": index,
                "label": label_overrides.get(key) or str(item.get("label") or key or "步骤"),
                "status": str(item.get("status") or "missing"),
                "status_label": _timing_stage_status_label(str(item.get("status") or "")),
                "duration_seconds": _positive_float(item.get("duration_seconds")),
                "duration_label": _duration_text(item.get("duration_seconds")),
                "source": str(item.get("source") or ""),
                "note": str(item.get("note") or ""),
            }
        )
    return timings


def _timing_stage_status_label(status: str) -> str:
    if status in {"succeeded", "completed", "available"}:
        return "已记录"
    if status in {"pending", "running"}:
        return "执行中"
    if status == "failed":
        return "失败"
    return "暂无"


def _duration_text(value: object) -> str:
    seconds = _positive_float(value)
    if seconds is None:
        return "暂未记录"
    if seconds < 60:
        return f"{seconds:.1f} 秒" if seconds < 10 else f"{seconds:.0f} 秒"
    minutes = int(seconds // 60)
    remain = int(round(seconds % 60))
    return f"{minutes} 分 {remain} 秒"


def _job_duration_seconds_for_report(job: dict) -> float | None:
    started_at = _parse_datetime(job.get("started_at"))
    ended_at = _parse_datetime(job.get("ended_at"))
    if not started_at or not ended_at or ended_at < started_at:
        return None
    return round((ended_at - started_at).total_seconds(), 3)


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _positive_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return round(number, 3)


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _flatten_outline_preview(nodes: list[dict], parent_titles: list[str] | None = None, depth: int = 1) -> list[dict]:
    parent_titles = parent_titles or []
    result = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        title = str(node.get("title") or "").strip()
        path_titles = [*parent_titles, title] if title else [*parent_titles]
        children = [item for item in node.get("children") or [] if isinstance(item, dict)]
        result.append(
            {
                "node_id": str(node.get("node_id") or ""),
                "title": title,
                "depth": depth,
                "path_titles": path_titles,
                "path_text": " > ".join(path_titles),
                "descendant_node_ids": _collect_outline_preview_node_ids(node),
            }
        )
        result.extend(_flatten_outline_preview(children, path_titles, depth + 1))
    return result


def _collect_outline_preview_node_ids(node: dict) -> list[str]:
    ids = [str(node.get("node_id") or "")] if node.get("node_id") else []
    for child in node.get("children") or []:
        if isinstance(child, dict):
            ids.extend(_collect_outline_preview_node_ids(child))
    return ids


def _find_score_outline_match(score_point: dict, outline_items: list[dict]) -> dict:
    title = str(score_point.get("title") or "")
    best: dict = {}
    best_score = 0.0
    for item in outline_items:
        score = max(
            _coverage_text_match_score(title, item.get("title")),
            _coverage_text_match_score(title, item.get("path_text")) * 0.96,
        )
        if item.get("depth") == 1:
            score += 0.08
        if score > best_score:
            best = item
            best_score = score
    return best if best_score >= 0.48 else {}


def _find_score_generation_matches(score_point: dict, outline_match: dict, generation_units: list[dict]) -> list[dict]:
    matches = []
    seen = set()
    node_ids = {str(item) for item in outline_match.get("descendant_node_ids") or [] if item}
    path_titles = outline_match.get("path_titles") or []
    outline_top_title = str(path_titles[0]) if path_titles else str(outline_match.get("title") or "")
    outline_text = str(outline_match.get("path_text") or "")
    for index, unit in enumerate(generation_units):
        if not isinstance(unit, dict):
            continue
        key = str(unit.get("unit_id") or f"{unit.get('target_node_id') or ''}:{index}")
        target_id = str(unit.get("target_node_id") or "")
        chapter_path = " > ".join(str(item) for item in unit.get("chapter_path") or [] if item)
        chapter_text = f"{chapter_path} {unit.get('chapter') or ''}"
        node_matched = bool(target_id and target_id in node_ids)
        if outline_match:
            text_matched = (
                _coverage_text_match_score(outline_top_title or outline_text, chapter_text) >= 0.48
                or _coverage_text_match_score(outline_text, chapter_text) >= 0.48
            )
        else:
            text_matched = _coverage_text_match_score(str(score_point.get("title") or ""), chapter_text) >= 0.5
        if (node_matched or text_matched) and key not in seen:
            seen.add(key)
            matches.append(unit)
    return matches


def _coverage_normalize_text(value: object) -> str:
    text = str(value or "").lower().strip()
    text = re.sub(r"^\s*\d+(\.\d+)*[、.．\s-]*", "", text)
    return re.sub(r"[（）()【】\[\]《》<>、，。；;：:！!？?\s\"'“”‘’\-_.·]", "", text)


def _coverage_match_segments(value: object) -> list[str]:
    return [
        segment
        for segment in (
            _coverage_normalize_text(item)
            for item in re.split(r"[，。；;：:、\s\"'“”‘’（）()【】\[\]《》<>]+", str(value or ""))
        )
        if len(segment) >= 3
    ][:8]


def _coverage_text_match_score(source_text: object, target_text: object) -> float:
    source = _coverage_normalize_text(source_text)
    target = _coverage_normalize_text(target_text)
    if not source or not target:
        return 0.0
    if source == target:
        return 1.0
    if len(source) >= 4 and source in target:
        return 0.92
    if len(target) >= 4 and target in source:
        return 0.82
    source_segments = _coverage_match_segments(source_text)
    target_segments = _coverage_match_segments(target_text)
    segment_hits = sum(1 for segment in source_segments if segment in target) + sum(1 for segment in target_segments if segment in source)
    if segment_hits:
        return min(0.78, segment_hits / max(2, len(source_segments) + len(target_segments)) + 0.34)
    source_chars = {char for char in source if re.match(r"[\u4e00-\u9fa5a-z0-9]", char)}
    target_chars = {char for char in target if re.match(r"[\u4e00-\u9fa5a-z0-9]", char)}
    if len(source_chars) < 6 or len(target_chars) < 6:
        return 0.0
    return len(source_chars & target_chars) / min(len(source_chars), len(target_chars)) * 0.56


def _generation_unit_is_generated_for_coverage(item: dict) -> bool:
    status = str(item.get("status") or "").lower()
    return any(token in status for token in ["completed", "generated", "succeeded", "已生成", "已完成"])


def _generation_unit_is_failed_for_coverage(item: dict) -> bool:
    status = str(item.get("status") or "").lower()
    return "failed" in status or "error" in status or "失败" in status


def _parse_review_summary(parse_result: dict | None) -> dict:
    if not parse_result:
        return {
            "project_info": [],
            "technical_requirements": [],
            "attention_items": ["启动招标文件解析后生成项目信息和技术要求摘要。"],
        }

    project_info = parse_result.get("project_info") or {}
    project_type = parse_result.get("project_type") or {}
    fields = [
        ("项目名称", _field_value(project_info.get("project_name"))),
        ("项目类型", _project_type_label(project_type.get("value"))),
        ("建设地点", _field_value(project_info.get("construction_location"))),
        ("建设规模", _field_value(project_info.get("construction_scale"))),
        ("招标范围", _field_value(project_info.get("tender_scope"))),
        ("工期要求", _field_value(project_info.get("duration_requirement"))),
        ("质量要求", _field_value(project_info.get("quality_requirement"))),
        ("安全文明要求", _field_value(project_info.get("safety_civilization_requirement"))),
    ]
    project_rows = [{"label": label, "value": _compact_text(value)} for label, value in fields if _compact_text(value)]
    technical_rows = _technical_requirement_summary(parse_result)
    attention_items = _business_review_items(parse_result)
    return {
        "project_info": project_rows,
        "technical_requirements": technical_rows,
        "attention_items": attention_items,
    }


def _project_type_label(value: object) -> str:
    if value == "epc":
        return "EPC 项目"
    if value == "construction":
        return "施工项目"
    return "未明确"


def _technical_requirement_summary(parse_result: dict) -> list[dict]:
    technical = parse_result.get("technical_requirements")
    rows: list[dict] = []
    if isinstance(technical, dict):
        candidates = []
        for key in (
            "technical_standards",
            "quality_requirements",
            "safety_requirements",
            "duration_requirements",
            "construction_requirements",
            "main_requirements",
            "requirements",
        ):
            value = technical.get(key)
            if isinstance(value, list):
                candidates.extend(value)
            elif value:
                candidates.append(value)
        for item in candidates:
            text = ""
            label = "技术要求"
            if isinstance(item, dict):
                text = _field_value(item.get("content")) or _field_value(item.get("requirement")) or _field_value(item.get("text"))
                label = str(item.get("title") or item.get("category") or label)
            else:
                text = str(item)
            text = _compact_text(text, limit=140)
            if text:
                rows.append({"label": label, "value": text})
            if len(rows) >= 6:
                break

    if not rows:
        for item in parse_result.get("review_items") or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("item") or "")
            if "技术" not in title and "编制" not in title:
                continue
            text = _compact_text(item.get("reason"), limit=140)
            if text:
                rows.append({"label": title or "技术要求", "value": text})
            if len(rows) >= 4:
                break

    return rows or [{"label": "技术要求", "value": "未形成明确摘要，建议人工查看招标文件技术标准与要求章节。"}]


def _business_review_items(parse_result: dict) -> list[str]:
    items = []
    for item in parse_result.get("review_items") or []:
        if not isinstance(item, dict):
            continue
        title = _compact_text(item.get("item"), limit=48)
        if title and title not in items:
            items.append(title)
        if len(items) >= 3:
            break
    if not items:
        items.append("确认评分点原文完整后进入目录生成。")
    return items


def _compact_text(value: object, *, limit: int = 110) -> str:
    text = _field_value(value) if isinstance(value, dict) else str(value or "")
    text = " ".join(text.replace("\r", "\n").split())
    if not text or text == "未明确":
        return "未明确"
    return text if len(text) <= limit else f"{text[:limit]}..."


def _clean_pdf_heading_spaces(value: object) -> str:
    text = " ".join(str(value or "").replace("\r", "\n").split()).strip()
    if not text:
        return ""
    if not any("\u4e00" <= char <= "\u9fff" for char in text):
        return text
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fffA-Za-z0-9])", "", text)
    text = re.sub(r"(?<=[A-Za-z0-9])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"(?<=[、，,；;：:（）()])\s+(?=[A-Za-z0-9\u4e00-\u9fff])", "", text)
    return text


def _field_value(value: object) -> str:
    if isinstance(value, dict):
        raw = value.get("value")
        return "" if raw is None else str(raw)
    return "" if value is None else str(value)


def _now_local_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def _apply_outline_editor_nodes(outline: dict, edited_nodes: list[dict]) -> dict:
    saved = json.loads(json.dumps(outline, ensure_ascii=False))
    original_nodes = [node for node in saved.get("nodes") or [] if isinstance(node, dict)]
    if len(edited_nodes) != len(original_nodes):
        raise HTTPException(status_code=400, detail="一级目录数量不能修改。")

    for index, (original, edited) in enumerate(zip(original_nodes, edited_nodes, strict=True), start=1):
        if not isinstance(edited, dict):
            raise HTTPException(status_code=400, detail="目录节点格式不正确。")
        if str(edited.get("title") or "").strip() != str(original.get("title") or "").strip():
            raise HTTPException(status_code=400, detail="一级目录必须保持招标文件评分点原文，不能编辑。")
        original["level"] = 1
        original["number"] = str(index)
        original["children"] = _sanitize_outline_children(
            edited.get("children") or [],
            original.get("children") or [],
            parent_number=str(index),
            parent_node_id=original.get("node_id"),
            inherited_domain=original.get("domain"),
            inherited_category=original.get("category"),
            level=2,
            max_depth=3,
        )
    saved["nodes"] = original_nodes
    saved["level_1_count"] = len(original_nodes)
    saved["updated_at"] = _now_local_iso()
    saved["outline_editor_state"] = {
        "edited": True,
        "edited_at": _now_local_iso(),
        "level_1_locked": True,
    }
    return saved


def _sanitize_outline_children(
    edited_children: list,
    original_children: list,
    *,
    parent_number: str,
    parent_node_id: object,
    inherited_domain: object,
    inherited_category: object,
    level: int,
    max_depth: int,
) -> list[dict]:
    children = []
    original_by_title = {
        str(child.get("title") or ""): child
        for child in original_children
        if isinstance(child, dict)
    }
    original_by_id = {
        str(child.get("node_id") or ""): child
        for child in original_children
        if isinstance(child, dict) and child.get("node_id")
    }
    for index, edited in enumerate(edited_children, start=1):
        if not isinstance(edited, dict):
            continue
        title = str(edited.get("title") or "").strip()
        if not title:
            continue
        number = f"{parent_number}.{index}"
        edited_node_id = str(edited.get("node_id") or "")
        original = dict(original_by_id.get(edited_node_id) or original_by_title.get(title) or {})
        original_child_nodes = original.get("children") or []
        node_id = original.get("node_id") or f"{parent_node_id or 'outline'}_{index:03d}"
        original.update(
            {
                "node_id": node_id,
                "parent_node_id": parent_node_id,
                "level": level,
                "number": number,
                "title": title,
                "domain": original.get("domain") or inherited_domain,
                "category": original.get("category") or inherited_category,
                "title_source": original.get("title_source") or "manual",
                "children": [],
            }
        )
        if level < max_depth:
            original["children"] = _sanitize_outline_children(
                edited.get("children") or [],
                original_child_nodes,
                parent_number=number,
                parent_node_id=node_id,
                inherited_domain=original.get("domain"),
                inherited_category=original.get("category"),
                level=level + 1,
                max_depth=max_depth,
            )
        children.append(original)
    return children


def _outline_preview(outline: dict | None) -> list[dict]:
    if not outline:
        return []
    preview = []
    for node in outline.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        preview.append(
            {
                "node_id": node.get("node_id"),
                "title": node.get("title") or "未命名目录",
                "domain": node.get("domain"),
                "status": node.get("generation_status"),
                "children": [
                    {
                        "node_id": child.get("node_id"),
                        "title": child.get("title") or "",
                        "number": child.get("number") or "",
                        "children": [
                            {
                                "node_id": grandchild.get("node_id"),
                                "title": grandchild.get("title") or "",
                                "number": grandchild.get("number") or "",
                            }
                            for grandchild in child.get("children") or []
                            if isinstance(grandchild, dict)
                        ],
                    }
                    for child in node.get("children") or []
                    if isinstance(child, dict)
                ],
            }
        )
    return preview


def _read_chapter_llm_state(state_dir: Path) -> dict[str, dict]:
    chapter_dir = state_dir / "chapters"
    if not chapter_dir.exists():
        return {}
    result: dict[str, dict] = {}
    for path in chapter_dir.glob("*.json"):
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if artifact.get("schema_version") not in {BATCH_ARTIFACT_SCHEMA_VERSION, None}:
            continue
        task = artifact.get("task") if isinstance(artifact.get("task"), dict) else artifact
        unit_id = str(task.get("unit_id") or artifact.get("unit_id") or "")
        if not unit_id:
            continue
        status = str(task.get("status") or artifact.get("status") or "")
        validation = task.get("validation") if isinstance(task.get("validation"), dict) else {}
        result[unit_id] = {
            "raw_status": status,
            "status": _llm_generation_status_label(status),
            "duration_seconds": task.get("duration_seconds"),
            "completed_at": task.get("completed_at") or artifact.get("generated_at"),
            "validation_issue_count": validation.get("issue_count"),
            "error": task.get("error"),
            "cache_status": task.get("cache_status") or artifact.get("cache_status"),
            "resume_reason": task.get("resume_reason") or artifact.get("resume_reason"),
            "failure_type": task.get("failure_type") or artifact.get("failure_type"),
            "failure_reason": task.get("failure_reason") or artifact.get("failure_reason"),
            "repair_attempt_count": task.get("repair_attempt_count") or 0,
        }
    return result


def _llm_generation_status_label(status: str) -> str:
    if status == "completed":
        return "已生成"
    if status == "failed":
        return "生成失败"
    if status == "skipped":
        return "已跳过"
    return "生成中" if status else "待生成"


def _generation_units_from_chapter_inputs(chapter_inputs: dict | None, llm_state: dict[str, dict]) -> list[dict]:
    if not isinstance(chapter_inputs, dict):
        return []
    grouped: dict[str, dict] = {}
    for package in chapter_inputs.get("packages") or []:
        if not isinstance(package, dict):
            continue
        generation_unit = package.get("generation_unit") if isinstance(package.get("generation_unit"), dict) else {}
        package_unit_id = str(generation_unit.get("unit_id") or "")
        if not package_unit_id:
            continue

        display_unit_id = package_unit_id
        target_node_id = generation_unit.get("target_node_id")
        chapter_path = generation_unit.get("chapter_path") or []
        chapter = generation_unit.get("chapter_title") or generation_unit.get("chapter") or (
            chapter_path[-1] if chapter_path else "未命名章节"
        )
        if generation_unit.get("unit_type") == "level3_subsection_unit" and generation_unit.get("parent_level_2_node_id"):
            display_unit_id = f"GU-{generation_unit.get('parent_level_2_node_id')}"
            target_node_id = generation_unit.get("parent_level_2_node_id")
            chapter_path = chapter_path[:2]
            chapter = generation_unit.get("parent_level_2_title") or (chapter_path[-1] if chapter_path else chapter)

        item = grouped.setdefault(
            display_unit_id,
            {
                "unit_id": display_unit_id,
                "target_node_id": target_node_id,
                "chapter": chapter,
                "chapter_path": chapter_path,
                "status": "待生成",
                "preview_status": "真实正文输入包",
                "material": "等待正文生成",
                "domain": generation_unit.get("domain"),
                "package_unit_ids": [],
                "_states": [],
            },
        )
        item["package_unit_ids"].append(package_unit_id)
        state = llm_state.get(package_unit_id)
        if state:
            item["_states"].append(state)

    units = []
    for item in grouped.values():
        states = item.pop("_states", [])
        package_count = len(item.get("package_unit_ids") or [])
        completed_count = sum(1 for state in states if state.get("raw_status") == "completed")
        failed_states = [state for state in states if state.get("raw_status") == "failed"]
        failed_count = len(failed_states)
        if failed_count:
            status = "生成失败"
        elif package_count > 0 and completed_count == package_count:
            status = "已生成"
        else:
            status = "待生成"
        first_failed = failed_states[0] if failed_states else {}
        item.update(
            {
                "status": status,
                "material": f"真实正文输入包 {completed_count}/{package_count} 个小节包已生成",
                "duration_seconds": sum(float(state.get("duration_seconds") or 0) for state in states) or None,
                "completed_at": max((str(state.get("completed_at") or "") for state in states), default="") or None,
                "validation_issue_count": sum(int(state.get("validation_issue_count") or 0) for state in states),
                "error": first_failed.get("error"),
                "cache_status": "hit" if states and all(state.get("cache_status") == "hit" for state in states) else None,
                "resume_reason": first_failed.get("resume_reason"),
                "failure_type": first_failed.get("failure_type"),
                "failure_reason": first_failed.get("failure_reason"),
                "repair_attempt_count": sum(int(state.get("repair_attempt_count") or 0) for state in states),
            }
        )
        units.append(item)
    return units


def _generation_units_summary(
    generation_summary: dict | None,
    outline: dict | None,
    llm_state: dict[str, dict] | None = None,
    *,
    chapter_inputs: dict | None = None,
) -> list[dict]:
    llm_state = llm_state or {}
    input_units = _generation_units_from_chapter_inputs(chapter_inputs, llm_state)
    if input_units:
        return input_units
    if generation_summary:
        units = []
        for item in generation_summary.get("generation_units") or []:
            if not isinstance(item, dict):
                continue
            unit_id = str(item.get("unit_id") or "")
            state = llm_state.get(unit_id)
            base_status = str(item.get("status") or "待生成")
            status = state.get("status") if state else ("待生成" if "轻量预览" in base_status else base_status)
            units.append(
                {
                    "unit_id": item.get("unit_id"),
                    "target_node_id": item.get("target_node_id"),
                    "chapter": item.get("chapter") or "未命名章节",
                    "chapter_path": item.get("chapter_path") or [],
                    "status": status,
                    "preview_status": base_status,
                    "material": item.get("material") or "等待素材匹配",
                    "domain": item.get("domain"),
                    "duration_seconds": state.get("duration_seconds") if state else item.get("duration_seconds"),
                    "completed_at": state.get("completed_at") if state else item.get("cache_generated_at"),
                    "validation_issue_count": state.get("validation_issue_count") if state else item.get("validation_issue_count"),
                    "error": state.get("error") if state else item.get("error"),
                    "cache_status": state.get("cache_status") if state else item.get("cache_status"),
                    "resume_reason": state.get("resume_reason") if state else item.get("resume_reason"),
                    "failure_type": state.get("failure_type") if state else item.get("failure_type"),
                    "failure_reason": state.get("failure_reason") if state else item.get("failure_reason"),
                    "repair_attempt_count": state.get("repair_attempt_count") if state else item.get("repair_attempt_count"),
                }
            )
        return units
    if not outline:
        return []
    units = []
    for node in outline.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        children = [child for child in node.get("children") or [] if isinstance(child, dict)]
        if children and node.get("category") in {"施工方案", "质量管理", "安全管理", "文明环保", "工期管理", "风险管理", "重点难点", "绿色施工"} and node.get("title") != "内容完整性":
            for child in children:
                units.append(
                {
                    "unit_id": f"GU-{child.get('node_id')}",
                    "target_node_id": child.get("node_id"),
                    "chapter": child.get("title") or "未命名章节",
                    "chapter_path": [node.get("title"), child.get("title")],
                    "status": llm_state.get(f"GU-{child.get('node_id')}", {}).get("status") or "待生成",
                    "material": "等待正文生成",
                    "domain": child.get("domain") or node.get("domain"),
                }
                )
        else:
            units.append(
                {
                    "unit_id": f"GU-{node.get('node_id')}",
                    "target_node_id": node.get("node_id"),
                    "chapter": node.get("title") or "未命名章节",
                    "chapter_path": [node.get("title")],
                    "status": llm_state.get(f"GU-{node.get('node_id')}", {}).get("status") or "待生成",
                    "material": "等待正文生成",
                    "domain": node.get("domain"),
                }
            )
    return units


def _review_items_summary(parse_result: dict | None, outline: dict | None, generation_summary: dict | None) -> list[dict]:
    items = []
    if parse_result:
        for item in parse_result.get("review_items") or []:
            if isinstance(item, dict):
                items.append({"severity": item.get("priority") or "medium", "title": item.get("item") or ""})
    if outline:
        for item in outline.get("review_items") or []:
            if isinstance(item, dict):
                items.append({"severity": item.get("priority") or "medium", "title": item.get("item") or ""})
    if generation_summary:
        for warning in generation_summary.get("warnings") or []:
            items.append({"severity": "medium", "title": str(warning)})
    return items[:80]


def _outline_generation_unit_count(outline: dict | None) -> int:
    if not outline:
        return 0
    return len(_generation_units_summary(None, outline))


def _source_text(source_refs: object) -> str:
    refs = source_refs if isinstance(source_refs, list) else []
    rendered = []
    for ref in refs[:2]:
        if not isinstance(ref, dict):
            continue
        parts = [str(ref.get("file_name") or "")]
        if ref.get("page_no") is not None:
            parts.append(f"第{ref.get('page_no')}页")
        if ref.get("block_index") is not None:
            parts.append(f"B{ref.get('block_index')}")
        rendered.append(" / ".join(part for part in parts if part))
    return "；".join(rendered)


def _latest_jobs_summary(jobs: list[dict]) -> list[dict]:
    sorted_jobs = sorted(
        jobs,
        key=lambda job: str(job.get("updated_at") or job.get("created_at") or ""),
        reverse=True,
    )
    return [
        _job_summary_payload(job)
        for job in sorted_jobs[:8]
    ]


def _job_summary_payload(job: dict) -> dict:
    job = _job_payload(job)
    is_active = bool(job.get("is_active"))
    display_status = str(job.get("effective_status") or job.get("status") or "")
    return {
        "job_id": job.get("job_id"),
        "job_type": job.get("job_type"),
        "job_label": _job_type_label(str(job.get("job_type") or "")),
        "status": display_status,
        "raw_status": job.get("status"),
        "status_label": job.get("status_label"),
        "is_active": is_active,
        "is_terminal": job.get("is_terminal"),
        "retryable": job.get("retryable"),
        "progress_total": job.get("progress_total"),
        "progress_completed": job.get("progress_completed"),
        "progress_failed": job.get("progress_failed"),
        "progress_percent": job.get("progress_percent"),
        "message": job.get("message"),
        "metadata": job.get("metadata"),
        "config_snapshot": job.get("config_snapshot"),
        "result_ref": job.get("result_ref"),
        "error": job.get("error"),
        "error_message": job.get("error_message"),
        "started_at": job.get("started_at"),
        "ended_at": job.get("ended_at"),
        "updated_at": job.get("updated_at"),
    }


def _job_payload(job: JobRecord | dict | None) -> dict:
    if job is None:
        return {}
    if isinstance(job, JobRecord):
        payload = JobResponse.model_validate(job).model_dump(mode="json")
    else:
        payload = dict(job)
    normalized = _job_runtime_state(payload)
    payload.update(normalized)
    return payload


def _job_runtime_state(job: dict) -> dict:
    raw_status = str(job.get("status") or "")
    is_active = _is_active_job(job)
    effective_status = "interrupted" if raw_status in {"pending", "running"} and not is_active else raw_status
    is_terminal = effective_status in {"succeeded", "failed", "cancelled", "interrupted"}
    error_code = job.get("error_code")
    error_message = job.get("error_message")
    error = None
    if error_code or error_message:
        error = {
            "code": error_code or _job_default_error_code(effective_status),
            "message": error_message or _job_status_label(effective_status),
            "recoverable": effective_status in {"failed", "interrupted"},
            "suggested_action": _job_suggested_action(effective_status, str(job.get("job_type") or "")),
        }
    return {
        "raw_status": raw_status,
        "effective_status": effective_status,
        "status_label": _job_status_label(effective_status),
        "is_active": is_active,
        "is_terminal": is_terminal,
        "retryable": effective_status in {"failed", "interrupted"},
        "error": error,
    }


def _is_active_job(job: dict) -> bool:
    if job.get("status") not in {"pending", "running"}:
        return False
    # 电脑重启或进程异常退出后，历史任务可能残留为 running，但已经写入 ended_at。
    # 这类任务不应继续锁住前端的“继续未完成/重试失败”按钮。
    return not bool(job.get("ended_at"))


def _find_active_project_job(
    *,
    project_id: str,
    job_type: str,
    idempotency_key: str,
    repo: BackendRepository | None,
) -> dict | None:
    if job_type not in SUPPORTED_WORKFLOW_JOB_TYPES:
        return None
    jobs = _project_jobs_payload(project_id, repo)
    for job in sorted(jobs, key=lambda item: str(item.get("created_at") or ""), reverse=True):
        if job.get("job_type") != job_type or not _is_active_job(job):
            continue
        payload = dict(job)
        metadata = dict(payload.get("metadata") or {})
        metadata.setdefault("duplicate_policy", "reuse_active_same_type_job")
        metadata["latest_duplicate_idempotency_key"] = idempotency_key
        payload["metadata"] = metadata
        payload["message"] = payload.get("message") or "同类任务正在执行，已返回现有任务。"
        payload["reused_existing_job"] = True
        return _job_payload(payload)
    return None


def _job_type_label(job_type: str) -> str:
    labels = {
        "tender_parse": "招标文件解析",
        "outline_generation": "技术标目录生成",
        "chapter_generation": "正文初稿生成",
        "chapter_llm_generation": "真实正文生成",
        "chapter_aggregate_refresh": "刷新 Word 初稿",
    }
    return labels.get(job_type, job_type or "未知任务")


def _job_status_label(status: str) -> str:
    labels = {
        "pending": "排队中",
        "running": "执行中",
        "succeeded": "已完成",
        "failed": "失败",
        "cancelled": "已取消",
        "interrupted": "已中断",
    }
    return labels.get(status, status or "未知状态")


def _job_default_error_code(status: str) -> str | None:
    if status == "cancelled":
        return "USER_CANCELLED"
    if status == "interrupted":
        return "JOB_INTERRUPTED"
    if status == "failed":
        return "WORKFLOW_EXECUTION_ERROR"
    return None


def _job_suggested_action(status: str, job_type: str) -> str | None:
    if status == "failed":
        if job_type == "chapter_llm_generation":
            return "可优先重试失败小节包，若连续失败请检查模型配置和章节输入。"
        return "请查看错误摘要，确认输入资料和模型配置后重新创建任务。"
    if status == "interrupted":
        return "服务可能重启或任务异常中断，建议刷新项目状态后重新创建任务。"
    if status == "cancelled":
        return "任务已取消，确认需要继续时可重新创建任务。"
    return None


def _new_project_record(request: ProjectCreateRequest) -> ProjectRecord:
    from .db import utc_now
    from .repository import _new_id

    now = utc_now()
    return ProjectRecord(
        project_id=_new_id("P"),
        name=request.name,
        description=request.description,
        project_type=request.project_type,
        stage="draft",
        stage_label=None,
        created_at=now,
        updated_at=now,
        metadata={"storage": "dev_json"},
    )


def _new_uploaded_file_record(
    *,
    project_id: str | None,
    business_type: str,
    file_name: str,
    mime_type: str | None,
    file_size: int,
    storage_uri: str,
    sha256: str,
    related_source_bid_id: str | None = None,
    metadata: dict | None = None,
) -> UploadedFileRecord:
    from pathlib import Path

    from .db import utc_now
    from .repository import _new_id

    now = utc_now()
    suffix = Path(file_name).suffix.lower().lstrip(".") or None
    return UploadedFileRecord(
        file_id=_new_id("F"),
        project_id=project_id,
        business_type=business_type,
        file_name=file_name,
        file_ext=suffix,
        mime_type=mime_type,
        file_size=file_size,
        page_count=None,
        storage_uri=storage_uri,
        sha256=sha256,
        status="uploaded",
        related_source_bid_id=related_source_bid_id,
        created_at=now,
        updated_at=now,
        metadata=metadata or {"storage": "dev_json"},
    )


def _job_config_snapshot(request: JobCreateRequest) -> dict | None:
    data = {
        "target_unit_ids": request.target_unit_ids,
        "run_all": request.run_all,
        "max_packages": request.max_packages,
        "max_workers": request.max_workers,
        "force": request.force,
        "retry_failed_only": request.retry_failed_only,
        "chapter_title_contains": request.chapter_title_contains,
    }
    return {key: value for key, value in data.items() if value is not None}


def _job_idempotency_key(project_id: str, job_type: str, config_snapshot: dict | None) -> str:
    payload = {
        "project_id": project_id,
        "job_type": job_type,
        "config_snapshot": config_snapshot or {},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _job_initial_metadata(*, job_type: str, idempotency_key: str, storage: str) -> dict:
    metadata = {
        "storage": storage,
        "idempotency_key": idempotency_key,
        "duplicate_policy": "reuse_active_same_type_job" if job_type in SUPPORTED_WORKFLOW_JOB_TYPES else "allow",
    }
    if job_type in SUPPORTED_WORKFLOW_JOB_TYPES:
        metadata["execution_mode"] = "fastapi_background_task"
        metadata["production_note"] = "生产环境建议迁移为持久化任务队列和独立 worker。"
    return metadata


def _new_job_record(
    *,
    project_id: str | None,
    job_type: str,
    message: str,
    config_snapshot: dict | None = None,
    metadata: dict | None = None,
) -> JobRecord:
    from .db import utc_now
    from .repository import _new_id

    now = utc_now()
    return JobRecord(
        job_id=_new_id("JOB"),
        project_id=project_id,
        job_type=job_type,
        status="pending",
        progress_total=None,
        progress_completed=None,
        progress_failed=None,
        progress_percent=None,
        message=message,
        result_ref=None,
        error_code=None,
        error_message=None,
        started_at=None,
        ended_at=None,
        created_at=now,
        updated_at=now,
        config_snapshot=config_snapshot,
        metadata=metadata or {"storage": "dev_json"},
    )


def _ok(data):
    return ApiEnvelope(success=True, data=data, error=None, request_id=REQUEST_ID_CTX.get())


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    detail: object = None,
    request_id: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload = ApiEnvelope(
        success=False,
        data=None,
        error={
            "code": code,
            "message": message,
            "detail": _safe_error_detail(detail),
        },
        request_id=request_id or REQUEST_ID_CTX.get(),
    ).model_dump(mode="json")
    response = JSONResponse(status_code=status_code, content=payload, headers=headers)
    if payload["request_id"]:
        response.headers["X-Request-ID"] = str(payload["request_id"])
    return response


def _request_id_from_request(request: Request) -> str | None:
    return getattr(request.state, "request_id", None) or REQUEST_ID_CTX.get()


def _http_error_code(status_code: int) -> str:
    if status_code == 400:
        return "BAD_REQUEST"
    if status_code == 401:
        return "UNAUTHORIZED"
    if status_code == 403:
        return "FORBIDDEN"
    if status_code == 404:
        return "NOT_FOUND"
    if status_code == 409:
        return "CONFLICT"
    if status_code == 415:
        return "UNSUPPORTED_MEDIA_TYPE"
    if status_code == 422:
        return "VALIDATION_ERROR"
    if status_code == 502:
        return "UPSTREAM_ERROR"
    return f"HTTP_{status_code}"


def _http_error_message(detail: object) -> str:
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    if isinstance(detail, dict):
        value = detail.get("message") or detail.get("detail")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "请求处理失败。"


def _validation_error_details(exc: RequestValidationError) -> list[dict]:
    details = []
    for item in exc.errors():
        details.append(
            {
                "loc": list(item.get("loc") or []),
                "message": item.get("msg"),
                "type": item.get("type"),
            }
        )
    return details


def _safe_error_detail(detail: object) -> object:
    if isinstance(detail, str):
        return detail[:500]
    if isinstance(detail, (list, dict)) or detail is None:
        return detail
    return str(detail)[:500]


app = create_app()
