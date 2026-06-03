"""受控 Agent 的只读推荐控制器。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .state import AgentRecommendation, AgentStateName, BlockedTool, QualityFlag, RecommendedAction, RequiredApproval
from .tool_registry import ToolRegistry, default_tool_registry


STATE_LABELS = {
    AgentStateName.EMPTY_PROJECT: "空项目",
    AgentStateName.FILES_UPLOADED: "已上传资料",
    AgentStateName.TENDER_PARSED: "招标已解析",
    AgentStateName.PARSE_NEEDS_REVIEW: "解析待复核",
    AgentStateName.SCORE_POINTS_CONFIRMED: "评分点已确认",
    AgentStateName.OUTLINE_GENERATED: "目录已生成",
    AgentStateName.OUTLINE_NEEDS_REVIEW: "目录待复核",
    AgentStateName.OUTLINE_CONFIRMED: "目录已确认",
    AgentStateName.CHAPTER_INPUTS_READY: "正文输入已准备",
    AgentStateName.CHAPTERS_GENERATING: "正文生成中",
    AgentStateName.CHAPTERS_GENERATED_WITH_WARNINGS: "正文生成有警告",
    AgentStateName.CHAPTERS_GENERATED: "正文已生成",
    AgentStateName.WORD_EXPORTED: "Word 已导出",
    AgentStateName.WORD_REVIEWING: "Word 复核中",
    AgentStateName.FINAL_READY: "最终稿就绪",
}


class AgentController:
    """根据现有 workflow summary 输出下一步建议。

    控制器第一版只读，不执行 tool，也不改变任何项目产物。
    """

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or default_tool_registry()

    def recommend_next_action(self, workflow_summary: Mapping[str, Any]) -> dict[str, Any]:
        state = self._infer_state(workflow_summary)
        recommendation = self._build_recommendation(state, workflow_summary)
        return recommendation.to_dict()

    def _infer_state(self, summary: Mapping[str, Any]) -> AgentStateName:
        stats = _dict(summary.get("stats"))
        artifacts = _dict(summary.get("artifacts"))
        latest_jobs = _list(summary.get("latest_jobs"))
        review_items = _list(summary.get("review_items"))
        generation_units = _list(summary.get("generation_units"))

        if _artifact_exists(artifacts, "word_draft_docx") or _artifact_exists(artifacts, "word_draft_json"):
            return AgentStateName.WORD_EXPORTED

        if _has_active_job(latest_jobs, {"chapter_llm_generation", "chapter_generation"}):
            return AgentStateName.CHAPTERS_GENERATING

        has_generation = (
            _artifact_exists(artifacts, "llm_generation_summary")
            or _artifact_exists(artifacts, "generation_summary")
            or _artifact_exists(artifacts, "llm_draft_markdown")
            or _artifact_exists(artifacts, "draft_markdown")
        )
        if has_generation:
            if _has_failed_generation_unit(generation_units) or _has_high_review_item(review_items):
                return AgentStateName.CHAPTERS_GENERATED_WITH_WARNINGS
            return AgentStateName.CHAPTERS_GENERATED

        if _artifact_exists(artifacts, "chapter_inputs"):
            return AgentStateName.CHAPTER_INPUTS_READY

        if _artifact_exists(artifacts, "outline"):
            if _has_high_review_item(review_items):
                return AgentStateName.OUTLINE_NEEDS_REVIEW
            return AgentStateName.OUTLINE_GENERATED

        if _artifact_exists(artifacts, "parse_result"):
            if int(stats.get("score_points") or 0) <= 0 or _has_high_review_item(review_items):
                return AgentStateName.PARSE_NEEDS_REVIEW
            return AgentStateName.TENDER_PARSED

        if int(stats.get("tender_files") or 0) > 0:
            return AgentStateName.FILES_UPLOADED

        return AgentStateName.EMPTY_PROJECT

    def _build_recommendation(
        self,
        state: AgentStateName,
        summary: Mapping[str, Any],
    ) -> AgentRecommendation:
        project = _dict(summary.get("project"))
        project_id = str(project.get("project_id") or "")
        stats = _dict(summary.get("stats"))
        review_items = _list(summary.get("review_items"))
        state_summary = self._state_summary(state, stats)
        quality_flags = self._quality_flags(state, summary)

        action, allowed, blocked, approvals, risk_summary = self._rule_for_state(state, review_items)
        return AgentRecommendation(
            project_id=project_id,
            current_state=state,
            state_label=STATE_LABELS[state],
            state_summary=state_summary,
            recommended_next_action=action,
            allowed_tools=self.registry.select_payload(allowed),
            blocked_tools=blocked,
            required_approvals=approvals,
            quality_flags=quality_flags,
            risk_summary=risk_summary,
        )

    def _state_summary(self, state: AgentStateName, stats: Mapping[str, Any]) -> str:
        tender_files = int(stats.get("tender_files") or 0)
        score_points = int(stats.get("score_points") or 0)
        review_items = int(stats.get("review_items") or 0)
        chapters = int(stats.get("estimated_chapters") or 0)
        if state == AgentStateName.EMPTY_PROJECT:
            return "项目尚未上传招标文件，当前只能先准备资料。"
        if state == AgentStateName.FILES_UPLOADED:
            return f"已上传 {tender_files} 份招标文件，下一步应解析项目信息和评分点。"
        if state in {AgentStateName.TENDER_PARSED, AgentStateName.PARSE_NEEDS_REVIEW}:
            return f"已识别 {score_points} 个评分点，存在 {review_items} 个复核项。"
        if state in {AgentStateName.OUTLINE_GENERATED, AgentStateName.OUTLINE_NEEDS_REVIEW}:
            return f"技术标目录已生成，预计正文生成单元约 {chapters} 个。"
        if state == AgentStateName.CHAPTER_INPUTS_READY:
            return f"章节生成输入已准备，预计正文生成单元约 {chapters} 个。"
        if state == AgentStateName.CHAPTERS_GENERATING:
            return "正文生成任务正在运行，应先查看进度，避免并发重跑同一章节。"
        if state == AgentStateName.CHAPTERS_GENERATED_WITH_WARNINGS:
            return f"正文已有生成结果，但仍有 {review_items} 个复核或警告项。"
        if state == AgentStateName.CHAPTERS_GENERATED:
            return "正文生成结果已就绪，可以刷新聚合并导出 Word 初稿。"
        if state == AgentStateName.WORD_EXPORTED:
            return "Word 初稿已导出，下一步应进入在线复核和人工校对。"
        return STATE_LABELS[state]

    def _rule_for_state(
        self,
        state: AgentStateName,
        review_items: list[Any],
    ) -> tuple[RecommendedAction, list[str], list[BlockedTool], list[RequiredApproval], str]:
        base_allowed = ["project_state_read_tool", "human_review_advice_tool"]
        common_generation_blocks = [
            BlockedTool("chapter_llm_generation_tool", "前置解析和目录确认尚未完成，不建议生成正文。"),
            BlockedTool("word_export_tool", "正文尚未生成，暂不能导出 Word 初稿。"),
        ]

        if state == AgentStateName.EMPTY_PROJECT:
            return (
                RecommendedAction(
                    "upload_tender_document",
                    "上传招标文件",
                    "项目尚未上传招标文件，Agent 需要先获得招标文件才能解析评分点和技术要求。",
                    "read_only",
                ),
                base_allowed,
                [
                    BlockedTool("tender_llm_extraction_tool", "尚未上传招标文件。"),
                    BlockedTool("outline_generation_tool", "尚未完成招标解析。"),
                    *common_generation_blocks,
                ],
                [],
                "当前只建议上传资料，不触发任何生成动作。",
            )

        if state == AgentStateName.FILES_UPLOADED:
            return (
                RecommendedAction(
                    "parse_tender",
                    "解析招标文件",
                    "已上传招标文件，但尚未生成项目信息、评分点和技术要求摘要。",
                    "external_call",
                    target_tool="tender_llm_extraction_tool",
                ),
                [
                    *base_allowed,
                    "tender_document_index_tool",
                    "tender_extraction_input_tool",
                    "tender_llm_extraction_tool",
                    "tender_parse_report_tool",
                ],
                [BlockedTool("outline_generation_tool", "尚未完成招标解析，缺少评分点输入。"), *common_generation_blocks],
                [],
                "下一步会调用解析链路，其中 LLM 抽取属于外部调用，应保留审计记录。",
            )

        if state == AgentStateName.PARSE_NEEDS_REVIEW:
            return (
                RecommendedAction(
                    "review_parse_result",
                    "复核招标解析结果",
                    "评分点或解析报告存在缺失、冲突或高优先级复核项，应先人工确认。",
                    "read_only",
                ),
                [*base_allowed, "score_point_quality_gate_tool", "tender_parse_report_tool"],
                [
                    BlockedTool("outline_generation_tool", "解析结果仍需人工复核，继续生成目录可能放大错误。"),
                    *common_generation_blocks,
                ],
                [
                    RequiredApproval(
                        "score_points_confirmed",
                        "确认评分点后再生成目录",
                        "评分点是技术标目录和正文生成的核心约束。",
                        "generate_artifact",
                    )
                ],
                "存在解析复核风险，暂不建议进入目录和正文生成。",
            )

        if state == AgentStateName.TENDER_PARSED:
            return (
                RecommendedAction(
                    "confirm_score_points",
                    "确认评分点并生成目录",
                    "已识别评分点，建议先人工确认关键评分项，再生成技术标目录。",
                    "generate_artifact",
                    target_tool="outline_generation_tool",
                    requires_approval=True,
                ),
                [*base_allowed, "score_point_quality_gate_tool", "outline_generation_tool", "excellent_bid_search_tool"],
                common_generation_blocks,
                [
                    RequiredApproval(
                        "score_points_confirmed",
                        "确认评分点",
                        "目录生成前应确认评分点完整、分值和原文来源无明显问题。",
                        "generate_artifact",
                    )
                ],
                "目录生成会产生新产物；若评分点有疑问，应先人工复核。",
            )

        if state == AgentStateName.OUTLINE_NEEDS_REVIEW:
            return (
                RecommendedAction(
                    "review_outline",
                    "复核并修订目录",
                    "目录存在高优先级复核项，正文生成前应先修订章节结构。",
                    "overwrite_artifact",
                    target_tool="outline_update_tool",
                    requires_approval=True,
                ),
                [*base_allowed, "outline_update_tool", "outline_refinement_tool", "excellent_bid_search_tool"],
                [BlockedTool("chapter_llm_generation_tool", "目录仍需复核，批量生成正文可能基于错误章节结构。")],
                [
                    RequiredApproval(
                        "outline_confirmed",
                        "确认目录结构",
                        "正文生成前必须确认一级目录与评分点覆盖关系。",
                        "overwrite_artifact",
                    )
                ],
                "目录修改和补强可能覆盖已有目录，必须由人工确认。",
            )

        if state == AgentStateName.OUTLINE_GENERATED:
            return (
                RecommendedAction(
                    "review_outline",
                    "复核技术标目录",
                    "已生成目录，但尚未确认章节结构和评分点覆盖关系。",
                    "read_only",
                    target_tool="human_review_advice_tool",
                    requires_approval=True,
                ),
                [*base_allowed, "outline_update_tool", "excellent_bid_search_tool", "chapter_input_build_tool"],
                [BlockedTool("chapter_llm_generation_tool", "目录尚未确认，批量生成正文需要人工确认。")],
                [
                    RequiredApproval(
                        "outline_confirmed",
                        "确认目录后再生成正文",
                        "目录确认后再构建章节输入和批量生成正文。",
                        "generate_artifact",
                    )
                ],
                "下一步建议人工确认目录；正文生成属于较高成本外部调用。",
            )

        if state == AgentStateName.CHAPTER_INPUTS_READY:
            return (
                RecommendedAction(
                    "generate_selected_chapters",
                    "试生成选中章节",
                    "章节输入已准备，建议先选择 1-3 个典型章节试跑，再扩大生成范围。",
                    "external_call",
                    target_tool="chapter_llm_generation_tool",
                    requires_approval=True,
                ),
                [
                    *base_allowed,
                    "chapter_material_retrieval_tool",
                    "chapter_llm_generation_tool",
                    "chapter_retry_tool",
                    "excellent_bid_search_tool",
                ],
                [BlockedTool("word_export_tool", "正文尚未生成，暂不能导出 Word 初稿。")],
                [
                    RequiredApproval(
                        "chapter_batch_generation_confirmed",
                        "确认正文生成范围",
                        "批量章节生成会调用 LLM，建议先控制章节数量和并发。",
                        "external_call",
                    )
                ],
                "正文生成会调用外部模型，建议先小批量试跑。",
            )

        if state == AgentStateName.CHAPTERS_GENERATING:
            return (
                RecommendedAction(
                    "wait_generation",
                    "查看正文生成进度",
                    "已有正文生成任务正在运行，应等待完成或查看失败章节。",
                    "read_only",
                ),
                [*base_allowed, "word_quality_summary_tool"],
                [
                    BlockedTool("chapter_llm_generation_tool", "已有正文生成任务运行中，避免并发重跑。"),
                    BlockedTool("chapter_retry_tool", "当前任务未结束，暂不建议重试。"),
                ],
                [],
                "当前以观察进度为主，不建议启动新的正文生成任务。",
            )

        if state == AgentStateName.CHAPTERS_GENERATED_WITH_WARNINGS:
            return (
                RecommendedAction(
                    "review_or_retry_chapters",
                    "复核警告章节",
                    "正文生成完成但存在失败或质量警告，应先复核并只重试问题章节。",
                    "overwrite_artifact",
                    target_tool="chapter_retry_tool",
                    requires_approval=True,
                ),
                [
                    *base_allowed,
                    "chapter_retry_tool",
                    "chapter_aggregate_refresh_tool",
                    "word_quality_summary_tool",
                    "excellent_bid_search_tool",
                ],
                [],
                [
                    RequiredApproval(
                        "retry_generated_chapters_confirmed",
                        "确认重试范围",
                        "重跑已成功章节可能覆盖已有正文，应只选择失败或低质量章节。",
                        "overwrite_artifact",
                    )
                ],
                "重试属于覆盖类外部调用，必须确认范围。",
            )

        if state == AgentStateName.CHAPTERS_GENERATED:
            return (
                RecommendedAction(
                    "export_word_draft",
                    "导出 Word 初稿",
                    "正文生成结果已就绪，可以刷新聚合并导出 Word 初稿供人工复核。",
                    "generate_artifact",
                    target_tool="word_export_tool",
                ),
                [*base_allowed, "chapter_aggregate_refresh_tool", "word_export_tool", "word_quality_summary_tool"],
                [],
                [],
                "Word 初稿是新产物；覆盖正式稿前仍需人工确认。",
            )

        if state == AgentStateName.WORD_EXPORTED:
            return (
                RecommendedAction(
                    "review_word_draft",
                    "进入 Word 初稿复核",
                    "Word 初稿已生成，建议通过 OnlyOffice 或下载文件进行人工校对。",
                    "external_call",
                    target_tool="onlyoffice_review_tool",
                    requires_approval=True,
                ),
                [*base_allowed, "word_quality_summary_tool", "onlyoffice_review_tool", "word_export_tool"],
                [],
                [
                    RequiredApproval(
                        "final_word_confirmed",
                        "确认最终 Word 成稿",
                        "最终提交、发布或对外共享前必须人工确认。",
                        "external_call",
                    )
                ],
                "最终稿确认和对外共享必须由人工完成。",
            )

        return (
            RecommendedAction(
                "read_project_state",
                "读取项目状态",
                "当前状态暂未匹配到明确流程动作，建议先查看项目状态和产物。",
                "read_only",
                target_tool="project_state_read_tool",
            ),
            base_allowed,
            [],
            [],
            "未知状态下只允许只读动作。",
        )

    def _quality_flags(self, state: AgentStateName, summary: Mapping[str, Any]) -> list[QualityFlag]:
        flags: list[QualityFlag] = []
        stats = _dict(summary.get("stats"))
        review_items = _list(summary.get("review_items"))
        if state == AgentStateName.PARSE_NEEDS_REVIEW:
            flags.append(
                QualityFlag(
                    "parse_needs_review",
                    "high",
                    "招标解析需要人工复核",
                    "评分点缺失、数量为 0 或存在高优先级复核项。",
                )
            )
        if int(stats.get("score_points") or 0) == 0 and state != AgentStateName.EMPTY_PROJECT:
            flags.append(QualityFlag("score_points_empty", "high", "尚未识别到评分点", "后续目录和正文会缺少核心约束。"))
        for index, item in enumerate(review_items[:5]):
            if isinstance(item, Mapping):
                title = str(item.get("title") or item.get("item") or "复核项")
                severity = str(item.get("severity") or item.get("priority") or "medium")
            else:
                title = str(item)
                severity = "medium"
            flags.append(QualityFlag(f"review_item_{index + 1}", severity, title))
        return flags


def _artifact_exists(artifacts: Mapping[str, Any], key: str) -> bool:
    value = artifacts.get(key)
    return isinstance(value, Mapping)


def _has_active_job(jobs: list[Any], job_types: set[str]) -> bool:
    for job in jobs:
        if not isinstance(job, Mapping):
            continue
        if str(job.get("job_type") or "") in job_types and bool(job.get("is_active")):
            return True
    return False


def _has_high_review_item(items: list[Any]) -> bool:
    high_values = {"high", "critical", "blocking", "error", "严重", "阻塞"}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        severity = str(item.get("severity") or item.get("priority") or "").lower()
        if severity in high_values:
            return True
    return False


def _has_failed_generation_unit(items: list[Any]) -> bool:
    for item in items:
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "").lower()
        if "失败" in status or "failed" in status or "error" in status:
            return True
    return False


def _dict(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []

