"""Schemas for the platform assistant fusion layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PlatformAssistantContext:
    message: str
    active_view: str = "home"
    active_step: str | None = None
    project_id: str | None = None
    selected_template_id: str | None = None
    account_context: dict[str, Any] = field(default_factory=dict)
    workflow_summary: dict[str, Any] | None = None
    project_answer: dict[str, Any] | None = None
    knowledge_manifest: dict[str, Any] | None = None
    bid_templates: list[dict[str, Any]] = field(default_factory=list)
    rag_preview: dict[str, Any] | None = None


@dataclass(frozen=True)
class PlatformAssistantIntent:
    intent: str
    confidence: float = 0.0
    source: str = "rule"
    reason: str = ""

