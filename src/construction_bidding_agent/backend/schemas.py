"""后端 API 数据结构。"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiEnvelope(BaseModel):
    success: bool = True
    data: Any = None
    error: dict[str, Any] | None = None
    request_id: str | None = None


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None
    project_type: str | None = None


class AssistantChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=800)
    active_view: str | None = None
    active_step: str | None = None
    project_id: str | None = None
    selected_template_id: str | None = None
    account_id: str | None = None
    account_display_name: str | None = None
    account_role: str | None = None
    account_role_label: str | None = None


class AuthRegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)
    department: str | None = None
    phone: str | None = None
    email: str | None = None


class AuthLoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class AccountCreateRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)
    role: str = "bid_staff"
    department: str | None = None
    phone: str | None = None
    email: str | None = None
    status: str = "active"


class AccountUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    role: str | None = None
    department: str | None = None
    phone: str | None = None
    email: str | None = None
    status: str | None = None


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: str
    username: str
    display_name: str
    role: str
    role_label: str
    department: str | None = None
    phone: str | None = None
    email: str | None = None
    status: str
    status_label: str
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None
    metadata: dict[str, Any] | None = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: str
    name: str
    description: str | None
    project_type: str | None
    stage: str
    stage_label: str | None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] | None = None


class UploadedFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
    metadata: dict[str, Any] | None = None


class JobCreateRequest(BaseModel):
    job_type: str = Field(min_length=1)
    message: str | None = None
    target_unit_ids: list[str] | None = None
    run_all: bool | None = None
    max_packages: int | None = Field(default=None, ge=1)
    max_workers: int | None = Field(default=None, ge=1, le=12)
    force: bool | None = None
    retry_failed_only: bool | None = None
    chapter_title_contains: str | None = None


class OutlineUpdateRequest(BaseModel):
    nodes: list[dict[str, Any]]


class WordExportProfileUpdateRequest(BaseModel):
    profile: dict[str, Any]


class WordExportRequest(BaseModel):
    profile: dict[str, Any] | None = None
    save_profile: bool | None = None
    force: bool | None = None


class OnlyOfficeCallbackRequest(BaseModel):
    status: int | None = None
    url: str | None = None
    key: str | None = None
    file_content_base64: str | None = None

    def decoded_file_content(self) -> bytes | None:
        if not self.file_content_base64:
            return None
        return base64.b64decode(self.file_content_base64)


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
    config_snapshot: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class ExcellentBidUploadResponse(BaseModel):
    source: dict[str, Any]
    file: dict[str, Any] | None = None
    job: dict[str, Any] | None = None


class ModelProviderConfigRequest(BaseModel):
    provider: str = "dashscope"
    api_type: str = "responses"
    base_url: str = ""
    model: str = ""
    api_key: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    timeout_seconds: int | None = None
    max_retries: int | None = None
    max_workers: int | None = None
    enable_thinking: bool | None = None
    structured_output_type: str | None = None
    default_profile: dict[str, Any] | None = None
    tasks: dict[str, dict[str, Any]] | None = None


class ModelRuntimeConfigResponse(BaseModel):
    provider: str | None
    api_type: str | None
    base_url: str | None
    model: str | None
    api_key_masked: str | None
    task_profiles_path: str | None
    effective_default: dict[str, Any]
    default_profile: dict[str, Any]
    tasks: dict[str, Any]
