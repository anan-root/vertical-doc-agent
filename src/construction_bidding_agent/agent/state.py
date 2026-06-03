"""受控 Agent 的项目状态和推荐结果模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentStateName(str, Enum):
    EMPTY_PROJECT = "empty_project"
    FILES_UPLOADED = "files_uploaded"
    TENDER_PARSED = "tender_parsed"
    PARSE_NEEDS_REVIEW = "parse_needs_review"
    SCORE_POINTS_CONFIRMED = "score_points_confirmed"
    OUTLINE_GENERATED = "outline_generated"
    OUTLINE_NEEDS_REVIEW = "outline_needs_review"
    OUTLINE_CONFIRMED = "outline_confirmed"
    CHAPTER_INPUTS_READY = "chapter_inputs_ready"
    CHAPTERS_GENERATING = "chapters_generating"
    CHAPTERS_GENERATED_WITH_WARNINGS = "chapters_generated_with_warnings"
    CHAPTERS_GENERATED = "chapters_generated"
    WORD_EXPORTED = "word_exported"
    WORD_REVIEWING = "word_reviewing"
    FINAL_READY = "final_ready"


@dataclass(frozen=True, slots=True)
class RecommendedAction:
    action_key: str
    title: str
    reason: str
    risk_level: str
    target_tool: str | None = None
    requires_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_key": self.action_key,
            "title": self.title,
            "reason": self.reason,
            "risk_level": self.risk_level,
            "target_tool": self.target_tool,
            "requires_approval": self.requires_approval,
        }


@dataclass(frozen=True, slots=True)
class BlockedTool:
    tool_name: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"tool_name": self.tool_name, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class RequiredApproval:
    approval_key: str
    title: str
    reason: str
    risk_level: str

    def to_dict(self) -> dict[str, str]:
        return {
            "approval_key": self.approval_key,
            "title": self.title,
            "reason": self.reason,
            "risk_level": self.risk_level,
        }


@dataclass(frozen=True, slots=True)
class QualityFlag:
    flag_key: str
    severity: str
    title: str
    detail: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "flag_key": self.flag_key,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class AgentRecommendation:
    project_id: str
    current_state: AgentStateName
    state_label: str
    state_summary: str
    recommended_next_action: RecommendedAction
    allowed_tools: list[dict[str, Any]]
    blocked_tools: list[BlockedTool] = field(default_factory=list)
    required_approvals: list[RequiredApproval] = field(default_factory=list)
    quality_flags: list[QualityFlag] = field(default_factory=list)
    risk_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "current_state": self.current_state.value,
            "state_label": self.state_label,
            "state_summary": self.state_summary,
            "recommended_next_action": self.recommended_next_action.to_dict(),
            "allowed_tools": self.allowed_tools,
            "blocked_tools": [item.to_dict() for item in self.blocked_tools],
            "required_approvals": [item.to_dict() for item in self.required_approvals],
            "quality_flags": [item.to_dict() for item in self.quality_flags],
            "risk_summary": self.risk_summary,
        }

