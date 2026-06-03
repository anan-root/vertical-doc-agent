"""受控 Agent 的 tool 注册表。"""

from __future__ import annotations

from .tools import ToolDefinition, ToolRiskLevel


class ToolRegistry:
    """保存 Agent 可见的工具元数据。

    第一版只做元数据注册和推荐判断，不在这里执行真实业务动作。
    """

    def __init__(self, tools: list[ToolDefinition]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def require(self, name: str) -> ToolDefinition:
        tool = self.get(name)
        if tool is None:
            raise KeyError(f"Tool is not registered: {name}")
        return tool

    def list(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def to_payload(self) -> list[dict]:
        return [tool.to_dict() for tool in self.list()]

    def select_payload(self, names: list[str]) -> list[dict]:
        result = []
        for name in names:
            tool = self.get(name)
            if tool is not None:
                result.append(tool.to_dict())
        return result


def default_tool_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolDefinition(
                name="project_state_read_tool",
                title="读取项目状态",
                description="读取项目、文件、任务和产物状态，汇总当前编标进度。",
                risk_levels=(ToolRiskLevel.READ_ONLY,),
                requires_approval=False,
                existing_anchor="backend/app.py::_build_workflow_summary",
                input_schema={"project_id": "str"},
                output_schema={"workflow_summary": "dict", "agent_recommendation": "dict"},
                preconditions=("项目存在",),
                idempotent=True,
                can_retry=True,
                audit_fields=("project_id", "current_state"),
            ),
            ToolDefinition(
                name="tender_document_index_tool",
                title="构建招标文件索引",
                description="构建招标文件结构索引，为后续抽取评分点和技术要求做准备。",
                risk_levels=(ToolRiskLevel.GENERATE_ARTIFACT,),
                requires_approval=False,
                existing_anchor="document_parser/tender_document_index.py::build_tender_document_index",
            ),
            ToolDefinition(
                name="tender_extraction_input_tool",
                title="构建招标抽取输入",
                description="从招标文件索引中构造 LLM 抽取输入片段。",
                risk_levels=(ToolRiskLevel.GENERATE_ARTIFACT,),
                requires_approval=False,
                existing_anchor="document_parser/tender_extraction_input_builder.py::build_tender_extraction_inputs_from_path",
            ),
            ToolDefinition(
                name="tender_llm_extraction_tool",
                title="LLM 招标解析",
                description="调用大模型抽取项目信息、评分点和技术要求。",
                risk_levels=(ToolRiskLevel.GENERATE_ARTIFACT, ToolRiskLevel.EXTERNAL_CALL),
                requires_approval=False,
                existing_anchor="document_parser/tender_llm_extractor.py::run_tender_llm_extraction_from_file",
                input_schema={"project_id": "str", "tender_file_id": "str?"},
                output_schema={"parse_result": "artifact", "quality_report": "artifact"},
                preconditions=("已上传招标文件",),
                postconditions=("生成招标解析产物", "生成评分点复核项"),
                idempotent=False,
                can_retry=True,
                rollback_strategy="保留旧产物，新产物以任务产物版本区分；失败时保留上一次成功解析结果。",
                audit_fields=("project_id", "job_id", "model", "prompt_hash", "duration_ms"),
            ),
            ToolDefinition(
                name="tender_parse_report_tool",
                title="生成解析报告",
                description="生成招标解析报告和可复核摘要。",
                risk_levels=(ToolRiskLevel.GENERATE_ARTIFACT,),
                requires_approval=False,
                existing_anchor="document_parser/tender_parse_report.py::build_tender_parse_result",
            ),
            ToolDefinition(
                name="score_point_quality_gate_tool",
                title="评分点质量闸门",
                description="检查评分点完整性、blocking 问题和人工复核要求。",
                risk_levels=(ToolRiskLevel.READ_ONLY, ToolRiskLevel.GENERATE_ARTIFACT),
                requires_approval=False,
                existing_anchor="document_parser/tender_parse_quality.py",
            ),
            ToolDefinition(
                name="outline_generation_tool",
                title="生成技术标目录",
                description="根据招标解析结果生成技术标目录树。",
                risk_levels=(ToolRiskLevel.GENERATE_ARTIFACT,),
                requires_approval=False,
                existing_anchor="outline_generator/generator.py::build_outline_tree",
            ),
            ToolDefinition(
                name="outline_refinement_tool",
                title="优化目录",
                description="基于规则或反馈补强目录结构。",
                risk_levels=(ToolRiskLevel.OVERWRITE_ARTIFACT,),
                requires_approval=True,
                existing_anchor="outline_generator/refinement_runner.py::run_outline_refinement",
            ),
            ToolDefinition(
                name="outline_update_tool",
                title="保存目录调整",
                description="保存人工调整后的技术标目录。",
                risk_levels=(ToolRiskLevel.OVERWRITE_ARTIFACT,),
                requires_approval=True,
                existing_anchor="web/app.js::saveOutlineAdjustments",
            ),
            ToolDefinition(
                name="excellent_bid_ingestion_tool",
                title="优秀标书入库",
                description="上传优秀标书并解析入企业素材库。",
                risk_levels=(ToolRiskLevel.GENERATE_ARTIFACT, ToolRiskLevel.EXTERNAL_CALL),
                requires_approval=True,
                existing_anchor="backend/app.py::upload_excellent_bid",
            ),
            ToolDefinition(
                name="excellent_bid_search_tool",
                title="检索优秀标书素材",
                description="检索企业优秀标书素材切片。",
                risk_levels=(ToolRiskLevel.READ_ONLY,),
                requires_approval=False,
                existing_anchor="backend/knowledge_base.py::search_excellent_bid_slices",
                input_schema={"query": "str?", "source_bid_id": "str?", "limit": "int?"},
                output_schema={"results": "list", "total": "int"},
                preconditions=("参考资料库可读取",),
                idempotent=True,
                can_retry=True,
                audit_fields=("query", "source_bid_id", "limit"),
            ),
            ToolDefinition(
                name="chapter_input_build_tool",
                title="构建章节输入",
                description="根据目录、评分点和素材构建章节生成输入。",
                risk_levels=(ToolRiskLevel.GENERATE_ARTIFACT,),
                requires_approval=False,
                existing_anchor="chapter_generator/input_builder.py::build_chapter_generation_inputs",
            ),
            ToolDefinition(
                name="chapter_material_retrieval_tool",
                title="章节素材召回",
                description="为章节召回优秀标书素材。",
                risk_levels=(ToolRiskLevel.READ_ONLY, ToolRiskLevel.GENERATE_ARTIFACT),
                requires_approval=False,
                existing_anchor="chapter_generator/input_builder.py",
            ),
            ToolDefinition(
                name="chapter_llm_generation_tool",
                title="LLM 章节正文生成",
                description="调用大模型生成章节正文。",
                risk_levels=(ToolRiskLevel.GENERATE_ARTIFACT, ToolRiskLevel.EXTERNAL_CALL),
                requires_approval=True,
                existing_anchor="chapter_generator/chapter_batch_runner.py::run_chapter_generation_batch",
                input_schema={"project_id": "str", "target_unit_ids": "list[str]?", "run_all": "bool?"},
                output_schema={"chapter_drafts": "artifact", "generation_summary": "artifact"},
                preconditions=("目录已确认", "章节输入已准备", "已确认生成范围"),
                postconditions=("生成章节正文草稿", "生成复核提示"),
                idempotent=False,
                can_retry=True,
                rollback_strategy="仅重试失败或人工选择章节；默认不覆盖已确认正文。",
                audit_fields=("project_id", "job_id", "target_unit_count", "model", "duration_ms"),
            ),
            ToolDefinition(
                name="chapter_retry_tool",
                title="重试章节生成",
                description="重试失败或低质量章节。",
                risk_levels=(ToolRiskLevel.OVERWRITE_ARTIFACT, ToolRiskLevel.EXTERNAL_CALL),
                requires_approval=True,
                existing_anchor="backend/workflow_executor.py::_execute_chapter_llm_generation",
            ),
            ToolDefinition(
                name="chapter_aggregate_refresh_tool",
                title="刷新正文聚合",
                description="刷新章节聚合结果和 Word 预览输入。",
                risk_levels=(ToolRiskLevel.GENERATE_ARTIFACT,),
                requires_approval=False,
                existing_anchor="backend/workflow_executor.py::_execute_chapter_aggregate_refresh",
            ),
            ToolDefinition(
                name="word_export_tool",
                title="导出 Word",
                description="导出完整技术标 Word 初稿。",
                risk_levels=(ToolRiskLevel.GENERATE_ARTIFACT,),
                requires_approval=False,
                existing_anchor="chapter_generator/full_bid_docx_exporter.py::export_full_bid_docx_from_files",
                input_schema={"project_id": "str", "profile": "dict?"},
                output_schema={"word_docx": "artifact", "quality_summary": "dict"},
                preconditions=("正文草稿已生成",),
                postconditions=("生成 Word 初稿",),
                idempotent=False,
                can_retry=True,
                rollback_strategy="保留历史 Word 版本，不直接覆盖最终稿。",
                audit_fields=("project_id", "profile_hash", "output_docx"),
            ),
            ToolDefinition(
                name="word_profile_update_tool",
                title="修改 Word 导出配置",
                description="修改 Word 渲染 profile 和导出参数。",
                risk_levels=(ToolRiskLevel.OVERWRITE_ARTIFACT,),
                requires_approval=True,
                existing_anchor="configs/word_export_profiles.*",
            ),
            ToolDefinition(
                name="onlyoffice_review_tool",
                title="OnlyOffice 复核",
                description="打开或跟踪 OnlyOffice 在线复核。",
                risk_levels=(ToolRiskLevel.EXTERNAL_CALL,),
                requires_approval=True,
                existing_anchor="backend/app.py OnlyOffice routes",
            ),
            ToolDefinition(
                name="word_quality_summary_tool",
                title="读取 Word 质量摘要",
                description="读取 Word 初稿结构和质量摘要。",
                risk_levels=(ToolRiskLevel.READ_ONLY,),
                requires_approval=False,
                existing_anchor="chapter_generator/word_version_manager.py::read_word_quality_summary",
                input_schema={"project_id": "str"},
                output_schema={"word_summary": "dict"},
                preconditions=("项目存在",),
                idempotent=True,
                can_retry=True,
                audit_fields=("project_id",),
            ),
            ToolDefinition(
                name="human_review_advice_tool",
                title="生成人工复核建议",
                description="根据当前状态输出人工复核重点。",
                risk_levels=(ToolRiskLevel.READ_ONLY,),
                requires_approval=False,
                existing_anchor="agent/controller.py",
                input_schema={"project_id": "str"},
                output_schema={"recommendation": "dict", "quality_flags": "list"},
                preconditions=("项目状态可读取",),
                idempotent=True,
                can_retry=True,
                audit_fields=("project_id", "current_state"),
            ),
            ToolDefinition(
                name="llm_call_audit_tool",
                title="LLM 调用审计",
                description="记录 LLM 调用元数据、耗时、错误和脱敏摘要。",
                risk_levels=(ToolRiskLevel.READ_ONLY, ToolRiskLevel.GENERATE_ARTIFACT),
                requires_approval=False,
                existing_anchor="llm_gateway.py",
            ),
        ]
    )
