"""技术标章节正文生成调度器。

本模块接收章节正文生成输入包，调用 LLM 生成结构化章节初稿，并输出可供后续 Word
渲染器消费的中间稿。这里不直接生成 docx，先把正文、表格、图片候选和复核项稳定落成
JSON，便于人工检查与后续渲染。
"""

from __future__ import annotations

import copy
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from construction_bidding_agent.chapter_generator.parameter_conflict_guard import (
    find_output_parameter_conflict_residuals,
)
from construction_bidding_agent.llm_client import call_openai_json, parse_json_response, parse_json_response_with_repair_info
from construction_bidding_agent.llm_config import DEFAULT_MAX_WORKERS, LlmClientConfig, llm_config, load_dotenv


TASK_KEY = "technical_bid_chapter_generation"
RUN_SCHEMA_VERSION = "chapter_generation_run_v0.1"
OUTPUT_SCHEMA_VERSION = "technical_bid_chapter_draft_v1"
LLM_INPUT_SCHEMA_VERSION = "chapter_llm_input_v1"
LLM_INPUT_PROFILE = "slim_v3"
DEFAULT_TIMEZONE = "Asia/Shanghai"
TECHNICAL_BID_COMPLETENESS_CATEGORY = "技术标完整性说明"
TECHNICAL_BID_COMPLETENESS_SECTION_TYPE = "technical_bid_response_statement"
TECHNICAL_BID_COMPLETENESS_FORBIDDEN_HEADINGS = [
    "项目概况",
    "工程概况",
    "编制依据",
    "施工部署",
    "总体施工部署",
    "施工方案总体安排",
    "主要施工方法",
    "施工工艺",
    "工艺流程",
    "质量管理体系",
    "安全管理体系",
    "文明施工管理体系",
]
_DOMAIN_MATCH_TERMS = [
    "测量",
    "控制网",
    "轴线",
    "标高",
    "监测",
    "土方",
    "基坑",
    "支护",
    "降水",
    "钢筋",
    "直螺纹",
    "套筒",
    "箍筋",
    "模板",
    "支撑",
    "混凝土",
    "浇筑",
    "温控",
    "防水",
    "屋面",
    "地下室",
    "脚手架",
    "连墙件",
    "剪刀撑",
    "砌体",
    "防裂",
    "后浇带",
    "变形缝",
    "成品保护",
    "环境保护",
    "扬尘",
    "噪声",
    "电梯",
    "井道",
    "导轨",
    "轿厢",
    "层门",
    "预埋件",
    "工期",
    "计划",
    "进度",
    "纠偏",
    "总平面",
    "部署",
    "流程",
    "流水段",
    "临边",
    "洞口",
    "用电",
    "消防",
    "塔吊",
    "机械",
    "配电箱",
    "开关箱",
    "防护",
    "环保",
    "扬尘",
    "噪声",
    "噪音",
    "大气",
    "污染",
    "水污染",
    "光污染",
    "固体废弃物",
    "绿色",
    "节能",
    "沉淀池",
    "洗车槽",
]
_GENERIC_MATCH_TOKENS = {
    "施工",
    "工程",
    "方案",
    "技术",
    "措施",
    "控制",
    "做法",
    "示意",
    "管理",
    "质量",
    "安全",
}
_WEAK_IMAGE_SEMANTIC_TOKENS = {
    "图片",
    "图示",
    "示意图",
    "照片",
    "现场图",
    "效果图",
    "方法",
    "做法",
    "施工方法",
    "施工做法",
    "做法示意",
    "做法示意图",
    "方法示意图",
    "方法做法示意图",
    "控制方法",
    "主要方法",
    "序号",
    "项目",
    "内容",
    "措施",
}
_PRIMARY_TOPIC_TERMS = {
    "测量": ["测量", "控制网", "轴线", "标高", "放线", "铅垂仪", "内控点", "监测"],
    "土方基坑": ["土方", "基坑", "开挖", "支护", "降水", "边坡"],
    "钢筋": ["钢筋", "箍筋", "套筒", "马凳筋", "梯子筋", "直螺纹", "绑扎"],
    "模板": ["模板", "支模", "木方", "对拉螺栓", "覆膜板", "满堂架"],
    "混凝土": ["混凝土", "浇筑", "振捣", "测温", "养护", "温控"],
    "防水": ["防水", "卷材", "涂膜", "止水", "屋面防水", "地下室防水"],
    "脚手架": ["脚手架", "连墙件", "剪刀撑", "立杆", "横杆", "安全网", "悬挑"],
    "砌体": ["砌体", "砌筑", "砖", "加气块", "构造柱", "拉结筋"],
    "后浇带变形缝": ["后浇带", "变形缝", "施工缝"],
    "电梯": ["电梯", "井道", "导轨", "轿厢", "层门", "预埋件", "机房"],
    "进度计划": ["工期", "进度", "计划", "关键线路", "网络图", "横道图", "纠偏", "赶工"],
    "安全防护": ["安全", "防护", "临边", "洞口", "用电", "消防", "塔吊", "机械", "防护棚", "安全网", "配电箱", "开关箱"],
    "环境保护": [
        "环境保护",
        "环保",
        "扬尘",
        "噪声",
        "噪音",
        "大气",
        "污染",
        "水污染",
        "光污染",
        "固体废弃物",
        "绿色",
        "节能",
        "沉淀池",
        "洗车槽",
        "围挡",
        "垃圾",
        "危废",
        "污水",
        "降尘",
    ],
}
_SECTION_SPECIFIC_REQUIRED_TERMS = {
    "混凝土": ["浇筑", "振捣", "测温", "温控", "养护", "裂缝", "大体积"],
    "防水": ["防水", "卷材", "涂膜", "止水", "阴角", "屋面", "地下室"],
    "后浇带变形缝": ["后浇带", "变形缝", "止水", "施工缝"],
    "电梯": ["电梯", "井道", "导轨", "轿厢", "层门", "预埋件"],
    "安全防护": ["安全", "防护", "临边", "洞口", "用电", "消防", "塔吊", "机械"],
    "环境保护": ["环保", "环境保护", "扬尘", "噪声", "噪音", "大气", "污染", "水污染", "光污染", "固体废弃物", "绿色", "节能", "沉淀池", "洗车槽", "垃圾", "污水", "降尘"],
}
_SECTION_TOPIC_EXCLUSION_TERMS = {
    "模板": ["钢筋", "箍筋", "马凳筋", "梯子筋", "直螺纹", "绑扎", "套筒"],
    "钢筋": ["模板", "支模", "木方", "对拉螺栓", "覆膜板", "满堂架"],
    "混凝土": ["预制块", "预留洞口", "门窗洞口", "电箱", "套管", "构造柱", "马牙槎", "砌筑", "砌体"],
    "电梯": ["钢筋", "箍筋", "马凳筋", "梯子筋", "模板", "木方", "支模", "脚手架", "砌体", "防水", "控制网", "内控点", "铅垂仪", "混凝土浇筑", "振捣"],
    "安全防护": ["钢筋", "箍筋", "模板", "混凝土浇筑", "振捣", "防水", "砌体", "后浇带"],
    "环境保护": ["钢筋", "箍筋", "模板", "支模", "混凝土浇筑", "振捣", "防水", "砌体", "后浇带", "电梯"],
}
_GENERAL_ANALYSIS_SECTION_TERMS = {
    "工程重点",
    "重点难点",
    "难点分析",
    "重点、难点",
    "施工条件",
    "现场环境",
    "环境分析",
    "特点分析",
    "现状分析",
}
_GENERAL_ANALYSIS_EXACT_TERMS = {
    "项目概况",
    "工程概况",
    "编制依据",
}
_STRICT_SUBTOPIC_NAMES = {
    "临边洞口",
    "临时用电",
    "消防",
    "机械塔吊",
    "个人防护",
    "噪声",
    "水污染",
    "光污染",
    "固废",
    "扬尘大气",
    "绿色节能",
}
_SPECIFIC_PROCESS_TOPIC_NAMES = set(_PRIMARY_TOPIC_TERMS) - {"安全防护", "环境保护", "进度计划"}
_BASIS_SECTION_TERMS = {"编制依据", "工程施工图纸及技术规范", "技术规范", "规范标准"}
_MANAGEMENT_CONTEXT_TERMS = {
    "项目管理目标",
    "管理目标",
    "目标及承诺",
    "质量目标",
    "安全目标",
    "环保目标",
    "组织机构",
    "管理体系",
    "责任分工",
    "检查闭环",
    "保证体系",
}
_MANAGEMENT_IMAGE_TERMS = {
    "流程",
    "组织",
    "架构",
    "体系",
    "闭环",
    "责任",
    "分工",
    "检查",
    "验收",
    "制度",
    "目标",
    "管理",
    "网络",
}
_DEPLOYMENT_CONTEXT_TERMS = {
    "施工方案总体安排",
    "总体施工部署",
    "施工总体部署",
    "总体安排",
    "施工部署",
    "部署及流程",
    "流水段",
    "专业穿插",
}
_DEPLOYMENT_IMAGE_TERMS = {
    "部署",
    "流程",
    "流水",
    "流水段",
    "区段",
    "穿插",
    "总体",
    "组织",
    "平面布置",
}
EXPANDED_RETRY_PROMPT = """你是房建工程技术标编制专家。
任务：对已生成的章节 JSON 进行 expanded 详稿补强。

硬性规则：
1. 必须保持 schema_version、unit_id、target_node_id、chapter_path 不变。
2. 只允许在原 sections 内扩写正文、补充 rich_table，或新增与当前章节直接相关的小节。
3. 针对 validation_issues 中的 expanded 体量问题补强：补足每小节段落、总段落、rich_table 数量和表格行数。
4. 不得删除原有有效内容，不得引入历史项目名称、历史建设单位、历史地址、历史楼栋号、人员姓名电话等项目专属信息。
5. 不要出现“优秀标书”“参考素材”“AI生成”“模型”等过程性表述。
6. 只输出完整的 technical_bid_chapter_draft_v1 JSON 对象，不要输出解释文字。
"""
JSON_REPAIR_PROMPT = """你是 JSON 语法修复助手。
任务：将输入中的 broken_json 修复为合法 JSON 对象。

硬性规则：
1. 只修复 JSON 语法错误，例如缺少逗号、字段名双引号、尾逗号、字符串转义错误、代码块包裹或 JSON 外多余文字。
2. 不得新增、删除、改写正文语义，不得扩写章节，不得替换图片 ID。
3. 输出必须是完整 JSON 对象，不要输出解释文字、Markdown 代码块或修复说明。
"""
DEFAULT_PROMPT = """你是房建工程技术标编制专家。
任务：根据输入包，为当前章节生成技术标 Word 初稿的结构化中间稿。

硬性规则：
1. 只生成当前 generation_unit 对应章节，不扩写其他一级目录。
2. chapter_path 必须与输入 generation_unit.chapter_path 完全一致。
3. 若 chapter_path 含一级目录，一级目录必须保持招标文件评分点原文，不得改写。
4. 正文必须围绕 score_point.score_standard_raw 响应评分要求，并结合 project_info。
5. 一期只采用 expanded 详稿模式。生成结果必须接近可编辑 Word 初稿，不得写成摘要、提纲、简版说明。
6. 必须遵守 expanded_generation_policy.targets 的最低体量要求，包括 min_sections、min_paragraphs_total、min_rich_tables、min_rows_per_rich_table、min_image_refs。
7. 若 generation_unit.child_headings 不为空，优先使用这些标题组织内部小节；若数量不足，应补充编标人员习惯中自然需要的小节。
8. 可参考 excellent_bid_references、table_references、image_candidates 的结构和素材，但必须按 reuse_level 使用：
   - direct_reuse：可高强度吸收通用管理表达、成熟措施和表格结构，但必须替换当前项目字段。
   - rewrite_reuse：只参考结构、措施点和表格列结构，正文必须重新组织语言。
   - parameterized_reuse：可参考工艺流程和控制点，必须结合当前项目参数改写；参数缺失时输出 review_items，不得编造。
   - manual_review：不得作为正文主素材自动写入，只能作为占位、候选说明或人工复核项。
9. 必须同时遵守 generation_constraints.chapter_reuse_profile：
   - direct_reuse_preferred：通用管理类章节可充分吸收优秀标书成熟表达、管理制度表格和通用图块，但必须替换项目字段。
   - parameterized_reuse_preferred：施工工艺、工期、资源、部署等章节只能复用流程、控制点和表格结构，必须结合当前项目参数改写。
   - rewrite_reuse_preferred：只借鉴结构和措施点，避免大段照搬。
   - manual_review：项目概况、总平面、进度计划、踏勘现状等强项目事实不得自动复用历史正文或图片。
10. 通用管理类章节可以充分吸收 direct_reuse 素材，输出制度流程、责任分工、检查频次、整改闭环和表格化措施。
11. 施工工艺类章节必须展开施工准备、工艺流程、操作要点、质量控制、安全控制、成品保护、检查验收。
12. 表格优先用 rich_table 表达，表格行数不得少于 expanded_generation_policy.targets.min_rows_per_rich_table；可输出多个 rich_table。
13. 每个 section 至少输出 expanded_generation_policy.targets.min_paragraphs_per_section 个 paragraph；不足时应补充“执行方法、检查验收、问题整改”类正文。
14. 若 rich_table 数量不足 min_rich_tables，应补充综合控制表、责任分工表、检查频次表、风险与纠偏表等编标常用表格。
15. 每个 paragraph 应为正式正文，一般不少于 80 个汉字，避免一句话短段。
16. 可复用图片处理：
   - 模型不直接决定图片文件，不输出 image_ref，也不输出 image_placeholder。
   - image_candidates 和 image_groups_slim 仅作为你理解章节素材类型、图文结构和正文详略的参考。
   - 需要配图时，在顶层 image_slots 中提出插图意图，包括 section_heading、anchor_text、intent、preferred_type、min_count、max_count、group_preferred。
   - 系统会在生成后根据 image_slots、章节类型、图片语义、套图关系和高置信匹配阈值自动插入可复用图片。
   - 无合适图片素材时静默跳过，不写“图片待补充”“此处插图”等占位表述。
   - 套图是否使用、使用哪些成员由系统后处理完整判定。
17. 没有必要配图的表格不要强行加图片占位。
18. 对施工总平面图、施工进度计划图等项目专属图纸，不复用历史图片，也不输出 image_placeholder。
19. 禁止写出历史项目名称、历史建设单位、历史地址、历史楼栋号、人员姓名电话等项目专属信息。
20. 当 generation_unit.domain 为 general，或 generation_unit.category 为“技术标完整性说明”，或 expanded_generation_policy.section_type 为 technical_bid_response_statement 时：
   - 本章只写技术标完整性说明，不写施工方案，不写内部检查表。
   - 必须围绕“技术标响应范围、章节完整性组织、响应依据与编制原则、技术标完整性承诺”组织内容。
   - 禁止输出“评分点响应汇总表”“章节完整性检查表”“评分点逐项响应说明”等表格式或清单式内部复核内容。
   - 禁止归纳评分点数量，禁止写“七个核心评分项”“七个强制性评分点”“十三个评分点”等数量表述。
   - 禁止写“强制性评分点”“不提供即不合格”“半数以上评委确认”等否决性结论；除非作为招标文件原文引用，但本章正文不应展开该类否决规则。
   - 禁止输出 rich_table；本章只输出 paragraph。
   - 禁止输出“项目概况、编制依据、施工部署、主要施工方法、施工方案总体安排、施工工艺流程、质量安全管理体系”等施工方案范式小节。
   - 禁止输出 image_ref 或 image_placeholder。
21. 不要出现“优秀标书”“参考素材”“AI生成”“模型”等过程性表述。
22. 只输出符合 JSON Schema 的 JSON 对象，不要输出解释文字。

输出 JSON Schema：
{
  "schema_version": "technical_bid_chapter_draft_v1",
  "unit_id": "必须等于输入 generation_unit.unit_id",
  "target_node_id": "必须等于输入 generation_unit.target_node_id",
  "chapter_path": ["必须等于输入 generation_unit.chapter_path"],
  "title": "当前生成单元标题",
  "sections": [
    {
      "heading": "二级或三级标题",
      "level": 2,
      "blocks": [
        {"type": "paragraph", "text": "正式技术标正文"},
        {
          "type": "rich_table",
          "title": "表格标题，可为空",
          "columns": [{"key": "col_1", "title": "序号"}, {"key": "col_2", "title": "内容"}],
          "rows": [
            {"cells": {"col_1": "1", "col_2": "措施内容"}}
          ],
          "style_hint": {"header_background": "light_orange", "border_style": "grid"}
        },
        {"type": "image_ref", "image_id": "兼容旧字段，模型不要主动输出；系统后处理自动插入", "caption": "图片题注"},
        {"type": "image_placeholder", "caption": "兼容旧字段，模型不要主动输出", "reason": "兼容旧字段"}
      ]
    }
  ],
  "image_slots": [
    {
      "section_heading": "适合插图的小节标题",
      "anchor_text": "插图应靠近的正文或表格主题",
      "intent": "插图意图，例如钢筋加工、连接、绑扎流程示意图",
      "preferred_type": "施工工艺示意图/管理流程图/标准化做法图",
      "min_count": 1,
      "max_count": 6,
      "group_preferred": true
    }
  ],
  "score_response_check": {
    "score_point_raw": "输入中的评分点原文",
    "response_summary": "本章如何响应评分点",
    "covered": true,
    "evidence_headings": ["对应章节标题"]
  },
  "source_usage": [
    {"ref_id": "素材ID", "usage": "结构参考/表格结构参考/图片候选引用", "rewrite_required": true}
  ],
  "review_items": [
    {"severity": "medium", "type": "manual_check", "message": "需人工复核的事项"}
  ]
}
"""


@dataclass(slots=True)
class ChapterGenerationTaskRun:
    unit_id: str
    target_node_id: str
    chapter_path: list[str]
    status: str
    duration_seconds: float
    started_at: str | None = None
    completed_at: str | None = None
    model: str | None = None
    output_text: str = ""
    parsed_json: dict[str, Any] | None = None
    validation: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    cache_status: str = "disabled"
    cache_key: str | None = None
    resume_action: str | None = None
    resume_reason: str | None = None
    failure_type: str | None = None
    failure_reason: str | None = None
    retry_attempt_count: int = 0
    retry_summary: dict[str, Any] = field(default_factory=dict)
    repair_attempt_count: int = 0
    repair_duration_seconds: float = 0.0
    repair_summary: dict[str, Any] = field(default_factory=dict)
    llm_input_schema_version: str | None = None
    llm_input_profile: str | None = None
    llm_input_char_count: int = 0
    full_package_char_count: int = 0
    llm_input_compression_ratio: float = 0.0
    llm_input_metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChapterGenerationRunResult:
    schema_version: str
    generated_at: str
    provider: str
    model: str
    base_url: str | None
    task_count: int
    completed_count: int
    skipped_count: int
    failed_count: int
    duration_seconds: float
    execution_mode: str
    max_workers: int
    chapters: list[dict[str, Any]] = field(default_factory=list)
    tasks: list[ChapterGenerationTaskRun] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


LlmCallable = Callable[[dict[str, Any], LlmClientConfig], str]


def run_chapter_generation_from_files(
    chapter_inputs_json: str | Path,
    *,
    prompt_path: str | Path | None = None,
    model: str | None = None,
    max_workers: int | None = None,
    max_packages: int | None = None,
    chapter_title_contains: str | None = None,
    llm_config_override: LlmClientConfig | None = None,
    llm_callable: LlmCallable | None = None,
) -> ChapterGenerationRunResult:
    """从章节输入包文件执行章节正文生成。"""

    load_dotenv(Path.cwd() / ".env")
    inputs_data = json.loads(Path(chapter_inputs_json).read_text(encoding="utf-8"))
    packages = inputs_data.get("packages") if isinstance(inputs_data, dict) else inputs_data
    if not isinstance(packages, list):
        raise ValueError("Chapter generation input JSON must contain a packages list.")
    packages = _filter_packages(packages, chapter_title_contains=chapter_title_contains)
    if max_packages is not None:
        packages = packages[:max_packages]
    prompt = Path(prompt_path).read_text(encoding="utf-8") if prompt_path else DEFAULT_PROMPT
    return run_chapter_generation(
        packages,
        prompt=prompt,
        model=model,
        max_workers=max_workers,
        llm_config_override=llm_config_override,
        llm_callable=llm_callable,
    )


def run_chapter_generation(
    packages: list[dict[str, Any]],
    *,
    prompt: str = DEFAULT_PROMPT,
    model: str | None = None,
    max_workers: int | None = None,
    llm_config_override: LlmClientConfig | None = None,
    llm_callable: LlmCallable | None = None,
) -> ChapterGenerationRunResult:
    """执行章节正文生成，可在测试中传入假 LLM。"""

    config = llm_config_override or llm_config(task_key=TASK_KEY, model_override=model)
    effective_max_workers = _effective_max_workers(max_workers, config)
    generated_at = _now_iso()
    started = time.monotonic()
    warnings: list[str] = []

    if not packages:
        warnings.append("没有需要生成正文的章节输入包。")
        tasks: list[ChapterGenerationTaskRun] = []
    elif not config.api_key and llm_callable is None:
        warnings.append("API_KEY 未配置，章节正文生成未调用 LLM。")
        tasks = [_skipped_task(package, config.model, "API_KEY 未配置。") for package in packages]
    else:
        tasks = _run_generation_tasks(
            packages,
            prompt=prompt,
            config=config,
            llm_callable=llm_callable,
            max_workers=effective_max_workers,
        )

    chapters = [task.parsed_json for task in tasks if task.status == "completed" and task.parsed_json]
    duration = time.monotonic() - started
    return ChapterGenerationRunResult(
        schema_version=RUN_SCHEMA_VERSION,
        generated_at=generated_at,
        provider=config.provider,
        model=config.model,
        base_url=config.base_url,
        task_count=len(tasks),
        completed_count=sum(1 for task in tasks if task.status == "completed"),
        skipped_count=sum(1 for task in tasks if task.status == "skipped"),
        failed_count=sum(1 for task in tasks if task.status == "failed"),
        duration_seconds=duration,
        execution_mode="parallel" if len(packages) > 1 and effective_max_workers > 1 else "serial",
        max_workers=effective_max_workers,
        chapters=chapters,
        tasks=tasks,
        warnings=warnings,
    )


def write_chapter_generation_outputs(
    result: ChapterGenerationRunResult,
    json_path: str | Path,
    report_path: str | Path,
) -> None:
    """写入章节正文生成结果 JSON 和 Markdown 报告。"""

    json_target = Path(json_path)
    report_target = Path(report_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    report_target.write_text(render_chapter_generation_report(result), encoding="utf-8")


def render_chapter_generation_report(result: ChapterGenerationRunResult) -> str:
    """渲染章节正文生成运行报告。"""

    lines = [
        "# 技术标章节正文生成报告",
        "",
        f"- 生成时间：{result.generated_at}",
        f"- 服务商：{result.provider}",
        f"- 模型：{result.model}",
        f"- Base URL：{result.base_url or '默认'}",
        f"- 总耗时秒：{result.duration_seconds:.2f}",
        f"- 执行方式：{result.execution_mode}",
        f"- 并发数：{result.max_workers}",
        f"- 任务数：{result.task_count}",
        f"- 完成：{result.completed_count}",
        f"- 跳过：{result.skipped_count}",
        f"- 失败：{result.failed_count}",
        "",
        "## 任务概览",
        "",
        "| 序号 | 章节路径 | 状态 | 耗时秒 | 校验问题 | 错误 |",
        "|---:|---|---|---:|---:|---|",
    ]
    for index, task in enumerate(result.tasks, start=1):
        lines.append(
            f"| {index} | {_cell(' > '.join(task.chapter_path))} | {_cell(task.status)} | "
            f"{task.duration_seconds:.2f} | {task.validation.get('issue_count', 0)} | {_cell(task.error)} |"
        )
    if not result.tasks:
        lines.append("| 1 | - | no_tasks | 0 | 0 | - |")

    lines.extend(["", "## 章节预览", ""])
    for chapter in result.chapters[:20]:
        lines.append(f"### {' > '.join(chapter.get('chapter_path') or [])}")
        check = chapter.get("score_response_check") or {}
        if check:
            lines.append(f"- 响应摘要：{_cell(check.get('response_summary'))}")
        for section in (chapter.get("sections") or [])[:5]:
            lines.append(f"- {section.get('heading')}：{_block_summary(section.get('blocks') or [])}")
        review_items = chapter.get("review_items") or []
        if review_items:
            lines.append("- 复核项：" + "；".join(_cell(item.get("message")) for item in review_items[:5] if isinstance(item, dict)))
        lines.append("")

    if result.warnings:
        lines.extend(["## 警告", ""])
        for warning in result.warnings:
            lines.append(f"- {warning}")
        lines.append("")
    return "\n".join(lines)


def validate_chapter_output(output: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    """校验 LLM 输出是否能进入后续 Word 渲染。"""

    validation_package = _completion_statement_package(package) if _is_technical_bid_completeness_package(package) else package
    issues: list[dict[str, Any]] = []
    unit = validation_package.get("generation_unit") or {}
    if output.get("schema_version") != OUTPUT_SCHEMA_VERSION:
        issues.append(_issue("blocking", "schema_version", "schema_version 不正确。"))
    if output.get("unit_id") != unit.get("unit_id"):
        issues.append(_issue("blocking", "unit_id_mismatch", "unit_id 与输入不一致。"))
    if output.get("target_node_id") != unit.get("target_node_id"):
        issues.append(_issue("blocking", "target_node_id_mismatch", "target_node_id 与输入不一致。"))
    if list(output.get("chapter_path") or []) != list(unit.get("chapter_path") or []):
        issues.append(_issue("blocking", "chapter_path_mismatch", "chapter_path 与输入不一致。"))
    sections = output.get("sections")
    if not isinstance(sections, list) or not sections:
        issues.append(_issue("blocking", "empty_sections", "sections 不能为空。"))
    elif not _has_text_block(sections):
        issues.append(_issue("blocking", "empty_content", "章节中缺少正文段落。"))
    _normalize_image_slots(output)
    _add_image_slot_issues(issues, output, validation_package)
    _add_expanded_volume_issues(issues, output, validation_package)
    _add_image_reference_issues(issues, output, validation_package)
    _add_technical_bid_completeness_issues(issues, output, validation_package)
    _add_history_trace_issues(issues, output, validation_package)
    _add_parameter_conflict_residual_issues(issues, output, validation_package)
    check = output.get("score_response_check")
    if not isinstance(check, dict):
        issues.append(_issue("warning", "missing_score_response_check", "缺少评分点响应检查。"))
    elif check.get("covered") is not True:
        issues.append(_issue("warning", "score_point_not_covered", "模型未确认覆盖评分点。"))
    forbidden_hits = _forbidden_hits(output, validation_package)
    for hit in forbidden_hits:
        issues.append(_issue("warning", "forbidden_content_risk", f"疑似包含禁用或历史专属内容：{hit}"))

    blocking = any(issue["severity"] == "blocking" for issue in issues)
    counted_issues = [issue for issue in issues if issue["severity"] != "advisory"]
    return {
        "valid": not blocking,
        "blocking": blocking,
        "issue_count": len(counted_issues),
        "warning_issue_count": sum(1 for issue in counted_issues if issue["severity"] == "warning"),
        "advisory_issue_count": sum(1 for issue in issues if issue["severity"] == "advisory"),
        "issues": issues,
    }


def _add_parameter_conflict_residual_issues(
    issues: list[dict[str, Any]],
    output: dict[str, Any],
    package: dict[str, Any],
) -> None:
    for residual in find_output_parameter_conflict_residuals(output, package):
        issues.append(
            _issue(
                "blocking",
                "parameter_conflict_residual",
                str(residual.get("message") or "正文中存在不满足招标硬约束的参数。"),
            )
        )


def _normalize_image_slots(output: dict[str, Any]) -> None:
    slots = output.get("image_slots")
    if slots is None:
        output["image_slots"] = []
        return
    if not isinstance(slots, list):
        return
    normalized_slots: list[Any] = []
    for slot in slots:
        if isinstance(slot, str):
            slot = {"intent": slot}
        if not isinstance(slot, dict):
            normalized_slots.append(slot)
            continue
        normalized = dict(slot)
        for key in ["section_heading", "anchor_text", "intent", "preferred_type"]:
            normalized[key] = str(normalized.get(key) or "").strip()
        normalized["min_count"] = _bounded_int(normalized.get("min_count"), default=1, lower=0, upper=12)
        normalized["max_count"] = _bounded_int(normalized.get("max_count"), default=max(1, normalized["min_count"]), lower=0, upper=12)
        if normalized["max_count"] < normalized["min_count"]:
            normalized["max_count"] = normalized["min_count"]
        normalized["group_preferred"] = bool(normalized.get("group_preferred"))
        normalized_slots.append(normalized)
    output["image_slots"] = normalized_slots


def _add_image_slot_issues(
    issues: list[dict[str, Any]],
    output: dict[str, Any],
    package: dict[str, Any],
) -> None:
    slots = output.get("image_slots")
    if not isinstance(slots, list):
        issues.append(_issue("warning", "image_slots_invalid", "image_slots 应为数组；系统将忽略插图意图。"))
        return
    if _is_technical_bid_completeness_package(package) and slots:
        issues.append(_issue("blocking", "technical_bid_completeness_image_slots", "技术标完整性说明不应输出插图意图。"))
        return
    for index, slot in enumerate(slots, start=1):
        if not isinstance(slot, dict):
            issues.append(_issue("warning", "image_slot_not_object", f"第 {index} 个 image_slot 不是对象；系统将忽略。"))
            continue
        if not str(slot.get("intent") or "").strip():
            issues.append(_issue("warning", "image_slot_missing_intent", f"第 {index} 个 image_slot 缺少 intent。"))
        if not str(slot.get("section_heading") or "").strip():
            issues.append(_issue("warning", "image_slot_missing_section_heading", f"第 {index} 个 image_slot 缺少 section_heading。"))
        min_count = _bounded_int(slot.get("min_count"), default=0, lower=0, upper=12)
        max_count = _bounded_int(slot.get("max_count"), default=0, lower=0, upper=12)
        if max_count < min_count:
            issues.append(_issue("warning", "image_slot_count_invalid", f"第 {index} 个 image_slot 的 max_count 小于 min_count。"))


def _add_technical_bid_completeness_issues(
    issues: list[dict[str, str]],
    output: dict[str, Any],
    package: dict[str, Any],
) -> None:
    if not _is_technical_bid_completeness_package(package):
        return
    sections = output.get("sections") or []
    headings = [
        _normalize_heading_text(section.get("heading"))
        for section in sections
        if isinstance(section, dict)
    ]
    hits = [
        forbidden
        for forbidden in TECHNICAL_BID_COMPLETENESS_FORBIDDEN_HEADINGS
        for heading in headings
        if heading == _normalize_heading_text(forbidden)
    ]
    if hits:
        issues.append(
            _issue(
                "blocking",
                "technical_bid_completeness_construction_template",
                "技术标完整性说明误写为施工方案范式，包含：" + "、".join(sorted(set(hits))),
            )
        )
    visual_blocks = [
        block
        for block in _iter_blocks(sections if isinstance(sections, list) else [])
        if block.get("type") in {"image_ref", "image_placeholder"}
    ]
    if visual_blocks:
        issues.append(
            _issue(
                "blocking",
                "technical_bid_completeness_visual_block",
                "技术标完整性说明不应输出图片引用或图片占位。",
            )
        )
    table_blocks = [
        block
        for block in _iter_blocks(sections if isinstance(sections, list) else [])
        if block.get("type") == "rich_table"
    ]
    if table_blocks:
        issues.append(
            _issue(
                "blocking",
                "technical_bid_completeness_table_block",
                "技术标完整性说明不应输出评分点响应表、章节完整性检查表或其他内部复核表格。",
            )
        )
    forbidden_text_patterns = [
        "七个核心评分项",
        "七个强制性评分点",
        "十三个评分点",
        "13个评分点",
        "强制性评分点",
        "不提供即不合格",
        "半数以上评委",
        "评分点响应汇总表",
        "章节完整性检查表",
        "评分点逐项响应说明",
    ]
    output_text = json.dumps(output, ensure_ascii=False)
    hits = [pattern for pattern in forbidden_text_patterns if pattern in output_text]
    if hits:
        issues.append(
            _issue(
                "blocking",
                "technical_bid_completeness_risky_statement",
                "技术标完整性说明包含高风险内部化或数量化表述：" + "、".join(hits),
            )
        )


def _add_history_trace_issues(
    issues: list[dict[str, str]],
    output: dict[str, Any],
    package: dict[str, Any],
) -> None:
    constraints = package.get("generation_constraints") or {}
    scan = constraints.get("history_trace_scan") or {}
    if scan.get("enabled") is False:
        return
    output_text = json.dumps(output, ensure_ascii=False)
    current_values = {_normalize_for_trace(item) for item in scan.get("current_project_values") or [] if str(item).strip()}
    candidate_terms = [
        str(item).strip()
        for item in scan.get("candidate_terms") or []
        if _normalize_for_trace(item) and _normalize_for_trace(item) not in current_values
    ]
    hits = [term for term in candidate_terms if term and term in output_text]
    if hits:
        issues.append(
            _issue(
                "warning",
                "history_trace_residual",
                "疑似残留历史优秀标书项目信息：" + "、".join(sorted(set(hits))[:8]),
            )
        )


def _normalize_for_trace(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _is_technical_bid_completeness_package(package: dict[str, Any]) -> bool:
    unit = package.get("generation_unit") or {}
    policy = package.get("expanded_generation_policy") or {}
    chapter_path = " ".join(str(part) for part in unit.get("chapter_path") or [])
    return (
        str(unit.get("domain") or "") == "general"
        or str(unit.get("category") or "") == TECHNICAL_BID_COMPLETENESS_CATEGORY
        or str(policy.get("section_type") or "") == TECHNICAL_BID_COMPLETENESS_SECTION_TYPE
        or "内容完整性" in chapter_path
    )


def _normalize_heading_text(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    return re.sub(r"^\d+(?:\.\d+)*[.．、]?", "", text)


def normalize_chapter_identity(output: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    """用输入包覆盖任务身份字段，避免模型改写 unit_id 或章节路径。"""

    unit = package.get("generation_unit") or {}
    output["schema_version"] = OUTPUT_SCHEMA_VERSION
    output["unit_id"] = unit.get("unit_id")
    output["target_node_id"] = unit.get("target_node_id")
    output["chapter_path"] = list(unit.get("chapter_path") or [])
    if not output.get("title") and unit.get("chapter_path"):
        output["title"] = list(unit.get("chapter_path") or [])[-1]
    return output


def enrich_image_refs(output: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    """补全模型已引用图片的来源信息，方便后续 Word 渲染定位素材。"""

    candidates = _image_candidate_lookup(package)
    enriched = 0
    for block in _iter_blocks(output.get("sections") or []):
        if block.get("type") != "image_ref":
            continue
        candidate = _candidate_for_image_ref(block, candidates)
        if not candidate:
            continue
        if candidate.get("part_name") and _should_replace_image_source_part_name(block.get("source_part_name")):
            block["source_part_name"] = candidate.get("part_name")
        if candidate.get("source_id") and not block.get("source_id"):
            block["source_id"] = candidate.get("source_id")
        if not block.get("source_bid_id"):
            source_bid_id = candidate.get("source_bid_id") or candidate.get("source_id")
            if source_bid_id:
                block["source_bid_id"] = source_bid_id
        if candidate.get("image_id") and block.get("image_id") != candidate.get("image_id"):
            block["image_id"] = candidate.get("image_id")
        semantic_caption = _trusted_image_caption(candidate)
        if semantic_caption:
            old_caption = str(block.get("caption") or "")
            if old_caption != semantic_caption:
                block["caption"] = semantic_caption
                block["original_caption"] = old_caption
                block["caption_source"] = "excellent_bid_image_semantic"
        for field in [
            "image_asset_id",
            "canonical_image_id",
            "sha256",
            "perceptual_hash",
            "material_slice_id",
            "source_bid_id",
            "source_id",
            "source_slice_id",
            "bound_table_id",
            "bound_row_id",
            "bound_cell_key",
            "image_group_id",
            "group_title",
            "group_semantic_text",
            "group_member_index",
            "group_member_count",
            "must_keep_with_group",
            "semantic_text",
            "semantic_confidence",
            "semantic_sources",
            "caption_candidates",
        ]:
            if candidate.get(field) is not None and block.get(field) is None:
                block[field] = candidate.get(field)
        if block.get("reuse_level") is None:
            block["reuse_level"] = candidate.get("reuse_level")
        enriched += 1
    if enriched:
        output.setdefault("image_ref_enrichment", {})
        output["image_ref_enrichment"].update(
            {
                "enabled": True,
                "enriched_count": enriched,
                "source": "image_candidate_pool",
            }
        )
    return output


def _should_replace_image_source_part_name(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if _is_renderable_image_part({"part_name": text}):
        return False
    return "/" not in text and "\\" not in text


def clean_image_captions(output: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    """把弱题注、表格残片题注改写为正式图片题注。"""

    candidates = _image_candidate_lookup(package)
    rewritten: list[dict[str, Any]] = []
    for section in output.get("sections") or []:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or "")
        for block in section.get("blocks") or []:
            if not isinstance(block, dict) or block.get("type") != "image_ref":
                continue
            candidate = _candidate_for_image_ref(block, candidates) or block
            current = str(block.get("caption") or "").strip()
            new_caption = _clean_image_caption(current, candidate, heading)
            if not new_caption or new_caption == current:
                continue
            block["caption"] = new_caption
            block["caption_before_cleanup"] = current
            block["caption_source"] = "image_caption_cleanup"
            rewritten.append(
                {
                    "image_id": block.get("image_id"),
                    "image_asset_id": block.get("image_asset_id"),
                    "from": current,
                    "to": new_caption,
                    "section_heading": heading,
                }
            )
    if rewritten:
        output.setdefault("image_caption_cleanup", {})
        output["image_caption_cleanup"].update(
            {
                "enabled": True,
                "rewritten_count": len(rewritten),
                "rewritten": rewritten[:50],
            }
        )
    return output


def filter_mismatched_image_refs(output: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    """剔除未知、需复核或语义不匹配的 image_ref。"""

    candidates = _image_candidate_lookup(package)
    reusable_lookup = _reusable_image_lookup(package)
    review_only_keys = _review_only_image_keys(package)
    removed: list[dict[str, Any]] = []
    for section in output.get("sections") or []:
        if not isinstance(section, dict):
            continue
        blocks = section.get("blocks")
        if not isinstance(blocks, list):
            continue
        heading = str(section.get("heading") or "")
        kept_blocks: list[Any] = []
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "image_ref":
                kept_blocks.append(block)
                continue
            candidate = _candidate_for_image_ref(block, candidates)
            if any(key in review_only_keys for key in _image_ref_keys(block)):
                removed.append(
                    {
                        "image_id": block.get("image_id"),
                        "source_part_name": block.get("source_part_name") or block.get("part_name"),
                        "caption": block.get("caption"),
                        "section_heading": heading,
                        "reason": "manual_review_image_ref",
                    }
                )
                continue
            if not _image_ref_allowed_for_auto_render(block, reusable_lookup):
                removed.append(
                    {
                        "image_id": block.get("image_id"),
                        "source_part_name": block.get("source_part_name") or block.get("part_name"),
                        "caption": block.get("caption"),
                        "section_heading": heading,
                        "reason": "unknown_or_manual_review_image_ref",
                    }
                )
                continue
            candidate_for_filter = candidate or block
            if _text_match_score(_candidate_match_text(candidate_for_filter), heading) <= 0:
                removed.append(
                    {
                        "image_id": block.get("image_id"),
                        "source_part_name": block.get("source_part_name") or block.get("part_name"),
                        "caption": block.get("caption"),
                        "section_heading": heading,
                        "candidate_bound_section": candidate_for_filter.get("bound_section")
                        or candidate_for_filter.get("caption"),
                        "material_slice_id": candidate_for_filter.get("material_slice_id"),
                        "reason": "semantic_mismatch",
                    }
                )
                continue
            if not _image_candidate_compatible_with_section(candidate_for_filter, section):
                removed.append(
                    {
                        "image_id": block.get("image_id"),
                        "source_part_name": block.get("source_part_name") or block.get("part_name"),
                        "caption": block.get("caption"),
                        "section_heading": heading,
                        "candidate_bound_section": candidate_for_filter.get("bound_section")
                        or candidate_for_filter.get("caption"),
                        "material_slice_id": candidate_for_filter.get("material_slice_id"),
                        "reason": "section_image_topic_incompatible",
                    }
                )
                continue
            if _is_general_analysis_section(heading) and _candidate_primary_topics(candidate_for_filter):
                removed.append(
                    {
                        "image_id": block.get("image_id"),
                        "source_part_name": block.get("source_part_name") or block.get("part_name"),
                        "caption": block.get("caption"),
                        "section_heading": heading,
                        "candidate_bound_section": candidate_for_filter.get("bound_section")
                        or candidate_for_filter.get("caption"),
                        "material_slice_id": candidate_for_filter.get("material_slice_id"),
                        "reason": "general_analysis_section_blocks_process_image",
                    }
                )
                continue
            kept_blocks.append(block)
        section["blocks"] = kept_blocks
    if removed:
        output.setdefault("image_ref_filter", {})
        output["image_ref_filter"].update(
            {
                "enabled": True,
                "removed_count": len(removed),
                "reason": "image_candidate_semantic_mismatch",
                "removed": removed[:30],
            }
        )
    return output


def strip_disallowed_image_placeholders(output: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    """移除正文中的图片占位。

    一期策略是不向编标人员暴露补图占位；无高置信素材时静默跳过。
    """

    policy = package.get("auto_image_reuse_policy") or {}
    allow_placeholders = bool(policy.get("allow_placeholders"))
    if allow_placeholders:
        return output
    removed: list[dict[str, Any]] = []
    for section in output.get("sections") or []:
        if not isinstance(section, dict):
            continue
        blocks = section.get("blocks")
        if not isinstance(blocks, list):
            continue
        kept_blocks: list[Any] = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "image_placeholder":
                removed.append(
                    {
                        "caption": block.get("caption"),
                        "reason": block.get("reason"),
                        "section_heading": section.get("heading"),
                    }
                )
                continue
            kept_blocks.append(block)
        section["blocks"] = kept_blocks
    if removed:
        output.setdefault("image_placeholder_filter", {})
        output["image_placeholder_filter"].update(
            {
                "enabled": True,
                "removed_count": len(removed),
                "missing_image_behavior": policy.get("missing_image_behavior") or "silent_skip",
                "removed": removed[:30],
            }
        )
    return output


def dedupe_images_across_chapters(chapters: list[dict[str, Any]]) -> dict[str, Any]:
    """在批量章节结果范围内去重图片，并优先保留完整套图。"""

    changed_chapter_indexes: set[int] = set()
    for chapter_index, chapter in enumerate(chapters):
        if isinstance(chapter, dict) and chapter.pop("cross_chapter_image_dedup", None) is not None:
            changed_chapter_indexes.add(chapter_index)

    refs = _collect_cross_chapter_image_refs(chapters)
    remove_block_ids: set[int] = set()
    removed: list[dict[str, Any]] = []
    kept_group_keys: set[str] = set()
    kept_group_asset_keys: set[str] = set()
    group_units = _cross_chapter_group_units(refs)

    for unit in group_units:
        group_key = unit["group_key"]
        unit_refs = unit["refs"]
        asset_keys = set().union(*[set(ref["asset_keys"]) for ref in unit_refs])
        if group_key in kept_group_keys:
            _mark_cross_chapter_refs_removed(
                unit_refs,
                "duplicate_image_group",
                remove_block_ids,
                removed,
                changed_chapter_indexes,
            )
            continue
        if asset_keys and asset_keys & kept_group_asset_keys:
            _mark_cross_chapter_refs_removed(
                unit_refs,
                "duplicate_image_group_asset_overlap",
                remove_block_ids,
                removed,
                changed_chapter_indexes,
            )
            continue
        kept_group_keys.add(group_key)
        kept_group_asset_keys.update(asset_keys)

    kept_single_asset_keys: set[str] = set()
    for ref in refs:
        if id(ref["block"]) in remove_block_ids or ref["group_key"]:
            continue
        asset_keys = set(ref["asset_keys"])
        if asset_keys and asset_keys & kept_group_asset_keys:
            _mark_cross_chapter_refs_removed(
                [ref],
                "single_image_covered_by_group",
                remove_block_ids,
                removed,
                changed_chapter_indexes,
            )
            continue
        if asset_keys and asset_keys & kept_single_asset_keys:
            _mark_cross_chapter_refs_removed(
                [ref],
                "duplicate_image_asset",
                remove_block_ids,
                removed,
                changed_chapter_indexes,
            )
            continue
        kept_single_asset_keys.update(asset_keys)

    if remove_block_ids:
        _remove_cross_chapter_image_blocks(chapters, remove_block_ids)
    removed_by_chapter: dict[int, list[dict[str, Any]]] = {}
    for item in removed:
        removed_by_chapter.setdefault(int(item["chapter_index"]), []).append(
            {key: value for key, value in item.items() if key != "chapter_index"}
        )
    for chapter_index, chapter_removed in removed_by_chapter.items():
        chapter = chapters[chapter_index]
        chapter["cross_chapter_image_dedup"] = {
            "enabled": True,
            "strategy": "batch_postprocess_group_first",
            "removed_count": len(chapter_removed),
            "removed_duplicate_asset_count": sum(
                1 for item in chapter_removed if item["reason"] == "duplicate_image_asset"
            ),
            "removed_duplicate_group_count": sum(
                1
                for item in chapter_removed
                if item["reason"] in {"duplicate_image_group", "duplicate_image_group_asset_overlap"}
            ),
            "removed_single_covered_by_group_count": sum(
                1 for item in chapter_removed if item["reason"] == "single_image_covered_by_group"
            ),
            "removed": chapter_removed[:50],
        }

    reason_counts = {
        reason: sum(1 for item in removed if item["reason"] == reason)
        for reason in sorted({item["reason"] for item in removed})
    }
    return {
        "enabled": True,
        "strategy": "batch_postprocess_group_first",
        "removed_count": len(removed),
        "removed_duplicate_asset_count": reason_counts.get("duplicate_image_asset", 0),
        "removed_duplicate_group_count": reason_counts.get("duplicate_image_group", 0)
        + reason_counts.get("duplicate_image_group_asset_overlap", 0),
        "removed_single_covered_by_group_count": reason_counts.get("single_image_covered_by_group", 0),
        "reason_counts": reason_counts,
        "affected_chapter_count": len(removed_by_chapter),
        "changed_chapter_indexes": sorted(changed_chapter_indexes),
        "removed": [
            {key: value for key, value in item.items() if key != "chapter_index"}
            for item in removed[:100]
        ],
    }


def apply_auto_image_reuse(output: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    """在 LLM 生成后自动插入可复用图片，避免让模型承担大批量选图工作。"""

    policy = package.get("auto_image_reuse_policy") or {}
    if policy and policy.get("enabled") is False:
        return output
    sections = output.get("sections")
    if not isinstance(sections, list) or not sections:
        return output
    enrich_image_refs(output, package)
    completed_existing_groups = _complete_existing_split_image_groups(sections, package)
    _remove_same_material_single_images_after_group(sections)
    candidates = _auto_reusable_image_candidates(package)
    targets = (package.get("expanded_generation_policy") or {}).get("targets") or {}
    min_refs_value = policy.get("min_image_refs") if policy and policy.get("min_image_refs") is not None else targets.get("min_image_refs")
    min_refs = int(min_refs_value or 0)
    target_refs = int(policy.get("target_image_refs") or min_refs)
    max_refs = int(policy.get("max_image_refs_total") or policy.get("max_auto_image_refs") or target_refs or max(min_refs, 6))
    max_per_section = int(policy.get("max_images_per_section") or 4)
    slot_inserted = 0
    slot_group_inserted = 0
    slot_skipped = 0
    coverage_inserted = 0
    coverage_skipped = 0
    sparse_inserted = 0
    sparse_skipped = 0
    image_groups = _auto_reusable_image_group_candidates(package)
    if candidates or image_groups:
        candidate_lookup = _image_candidate_lookup(package)
        existing_keys = _existing_image_keys(sections, candidate_lookup)
        existing_group_material_ids = _existing_group_material_ids(sections)
        section_image_counts = _section_image_counts(sections)
        slot_inserted, slot_group_inserted, slot_skipped = _apply_image_slot_reuse(
            output,
            package,
            candidates,
            image_groups,
            existing_keys,
            existing_group_material_ids,
            section_image_counts,
            max_per_section=max_per_section,
            max_refs=max_refs,
        )
        if slot_inserted or slot_group_inserted:
            _remove_same_material_single_images_after_group(sections)
    if candidates:
        candidate_lookup = _image_candidate_lookup(package)
        existing_keys = _existing_image_keys(sections, candidate_lookup)
        existing_group_material_ids = _existing_group_material_ids(sections)
        section_image_counts = _section_image_counts(sections)
        coverage_inserted, coverage_skipped = _apply_auto_image_empty_section_coverage(
            sections,
            candidates,
            existing_keys,
            existing_group_material_ids,
            section_image_counts,
            max_per_section=max_per_section,
            max_refs=max_refs,
        )
        if coverage_inserted:
            _remove_same_material_single_images_after_group(sections)
            candidate_lookup = _image_candidate_lookup(package)
            existing_keys = _existing_image_keys(sections, candidate_lookup)
            existing_group_material_ids = _existing_group_material_ids(sections)
            section_image_counts = _section_image_counts(sections)
        sparse_section_target = int(policy.get("sparse_section_image_refs") or min(max_per_section, 3))
        sparse_min_candidates = int(policy.get("sparse_section_min_candidates") or 2)
        sparse_inserted, sparse_skipped = _apply_auto_image_sparse_section_expansion(
            sections,
            candidates,
            existing_keys,
            existing_group_material_ids,
            section_image_counts,
            max_per_section=max_per_section,
            max_refs=max_refs,
            target_per_section=sparse_section_target,
            min_candidate_count=sparse_min_candidates,
        )
        if sparse_inserted:
            _remove_same_material_single_images_after_group(sections)
    group_inserted, group_skipped = _apply_auto_image_group_reuse(output, package, image_groups)
    _remove_same_material_single_images_after_group(sections)
    deduped_equivalent_count = _dedupe_equivalent_images_within_sections(sections)
    deduped_caption_count = _dedupe_same_caption_single_images(sections)
    if not candidates:
        if group_inserted or completed_existing_groups or slot_inserted or slot_group_inserted:
            output.setdefault("auto_image_reuse", {})
            output["auto_image_reuse"].update(
                {
                    "enabled": True,
                    "strategy": "system_insert_after_llm_generation",
                    "inserted_count": slot_inserted + slot_group_inserted,
                    "slot_inserted_count": slot_inserted,
                    "slot_group_inserted_count": slot_group_inserted,
                    "completed_existing_group_count": completed_existing_groups,
                    "inserted_group_count": group_inserted,
                    "deduped_same_caption_count": deduped_caption_count,
                    "deduped_equivalent_image_count": deduped_equivalent_count,
                    "candidate_pool_count": len(candidates),
                    "skipped_unmatched_count": slot_skipped,
                    "skipped_unmatched_group_count": group_skipped,
                    "final_image_refs": _count_blocks(output.get("sections") or [], "image_ref"),
                }
        )
        return output
    candidate_lookup = _image_candidate_lookup(package)
    existing_keys = _existing_image_keys(sections, candidate_lookup)
    existing_group_material_ids = _existing_group_material_ids(sections)
    existing_ref_count = _count_blocks(sections, "image_ref")
    section_image_counts = _section_image_counts(sections)
    desired_total = min(max(target_refs, min_refs), max_refs, existing_ref_count + len(candidates))
    needed = max(0, desired_total - existing_ref_count)
    inserted = 0
    skipped_unmatched = coverage_skipped + sparse_skipped
    for candidate in _rank_auto_image_candidates(candidates, sections, existing_keys):
        if inserted >= needed or existing_ref_count + inserted >= max_refs:
            break
        image_id = str(candidate.get("image_id") or "")
        candidate_keys = _stable_image_keys(candidate)
        if not image_id or (candidate_keys & existing_keys):
            continue
        material_id = str(candidate.get("material_slice_id") or "")
        if material_id and not candidate.get("image_group_id") and material_id in existing_group_material_ids:
            continue
        section = _best_section_for_image(candidate, sections, section_image_counts, max_per_section=max_per_section)
        if not isinstance(section, dict):
            skipped_unmatched += 1
            continue
        blocks = section.setdefault("blocks", [])
        if not isinstance(blocks, list):
            continue
        section_key = _section_key(section)
        inserted_here = _insert_image_ref_near_context(
            blocks,
            _auto_image_ref_block(candidate, section),
            candidate,
            section,
        )
        if not inserted_here:
            skipped_unmatched += 1
            continue
        existing_keys.update(candidate_keys)
        section_image_counts[section_key] = section_image_counts.get(section_key, 0) + 1
        inserted += 1
    inserted_total = slot_inserted + slot_group_inserted + coverage_inserted + sparse_inserted + inserted
    fallback_inserted, fallback_skipped = 0, 0
    if inserted_total <= 0 and group_inserted <= 0 and min_refs > 0 and (candidates or image_groups):
        candidate_lookup = _image_candidate_lookup(package)
        existing_keys = _existing_image_keys(sections, candidate_lookup)
        existing_group_material_ids = _existing_group_material_ids(sections)
        section_image_counts = _section_image_counts(sections)
        fallback_inserted, fallback_skipped = _apply_auto_image_process_fallback(
            sections,
            candidates,
            image_groups,
            existing_keys,
            existing_group_material_ids,
            section_image_counts,
            max_per_section=max_per_section,
            max_refs=max_refs,
            min_refs=min_refs,
        )
        inserted_total += fallback_inserted
    if inserted_total:
        completed_existing_groups += _complete_existing_split_image_groups(sections, package)
        _remove_same_material_single_images_after_group(sections)
        deduped_equivalent_count += _dedupe_equivalent_images_within_sections(sections)
        deduped_caption_count += _dedupe_same_caption_single_images(sections)
        output.setdefault("auto_image_reuse", {})
        output["auto_image_reuse"].update(
            {
                "enabled": True,
                "strategy": "system_insert_after_llm_generation",
                "inserted_count": inserted_total,
                "slot_inserted_count": slot_inserted,
                "slot_group_inserted_count": slot_group_inserted,
                "completed_existing_group_count": completed_existing_groups,
                "coverage_inserted_count": coverage_inserted,
                "sparse_inserted_count": sparse_inserted,
                "fallback_inserted_count": fallback_inserted,
                "inserted_group_count": group_inserted,
                "deduped_same_caption_count": deduped_caption_count,
                "deduped_equivalent_image_count": deduped_equivalent_count,
                "min_image_refs": min_refs,
                "target_image_refs": target_refs,
                "final_image_refs": _count_blocks(sections, "image_ref"),
                "candidate_pool_count": len(candidates),
                "skipped_unmatched_count": skipped_unmatched + slot_skipped + fallback_skipped,
                "skipped_unmatched_group_count": group_skipped,
            }
        )
    elif group_inserted:
        completed_existing_groups += _complete_existing_split_image_groups(sections, package)
        _remove_same_material_single_images_after_group(sections)
        deduped_equivalent_count += _dedupe_equivalent_images_within_sections(sections)
        deduped_caption_count += _dedupe_same_caption_single_images(sections)
        output.setdefault("auto_image_reuse", {})
        output["auto_image_reuse"].update(
            {
                "enabled": True,
                "strategy": "system_insert_after_llm_generation",
                "inserted_count": 0,
                "completed_existing_group_count": completed_existing_groups,
                "inserted_group_count": group_inserted,
                "deduped_same_caption_count": deduped_caption_count,
                "deduped_equivalent_image_count": deduped_equivalent_count,
                "final_image_refs": _count_blocks(output.get("sections") or [], "image_ref"),
                "candidate_pool_count": len(candidates),
                "skipped_unmatched_count": skipped_unmatched,
                "skipped_unmatched_group_count": group_skipped,
            }
        )
    elif completed_existing_groups:
        _remove_same_material_single_images_after_group(sections)
        deduped_equivalent_count += _dedupe_equivalent_images_within_sections(sections)
        deduped_caption_count += _dedupe_same_caption_single_images(sections)
        output.setdefault("auto_image_reuse", {})
        output["auto_image_reuse"].update(
            {
                "enabled": True,
                "strategy": "system_insert_after_llm_generation",
                "inserted_count": 0,
                "completed_existing_group_count": completed_existing_groups,
                "deduped_same_caption_count": deduped_caption_count,
                "deduped_equivalent_image_count": deduped_equivalent_count,
                "final_image_refs": _count_blocks(sections, "image_ref"),
                "candidate_pool_count": len(candidates),
            }
        )
    return output


def _apply_auto_image_process_fallback(
    sections: list[Any],
    candidates: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    existing_keys: set[str],
    existing_group_material_ids: set[str],
    section_image_counts: dict[str, int],
    *,
    max_per_section: int,
    max_refs: int,
    min_refs: int,
) -> tuple[int, int]:
    """LLM 只提出弱插图意图时，系统按章节主题自动兜底插入高置信图文块/图片。"""

    inserted = 0
    skipped = 0
    target = min(max(min_refs, 1), max_refs)
    for section in sections:
        if inserted >= target or _count_blocks(sections, "image_ref") >= max_refs:
            break
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or "")
        if _is_general_analysis_section(heading):
            continue
        blocks = section.setdefault("blocks", [])
        if not isinstance(blocks, list):
            continue
        section_key = _section_key(section)
        if section_image_counts.get(section_key, 0) >= max_per_section:
            continue
        group = _best_fallback_group_for_section(groups, section, existing_keys)
        if group:
            group_blocks = _auto_image_group_ref_blocks(group, section)
            if group_blocks:
                if _insert_image_group_near_context(blocks, group_blocks[:max_per_section], group, section):
                    existing_keys.update(_stable_image_group_keys(group))
                    inserted += len(group_blocks[:max_per_section])
                    section_image_counts[section_key] = section_image_counts.get(section_key, 0) + len(group_blocks[:max_per_section])
                    continue
        section_candidates = _rank_auto_image_candidates_for_section(
            candidates,
            section,
            existing_keys,
            existing_group_material_ids,
        )
        if not section_candidates:
            skipped += 1
            continue
        candidate = section_candidates[0]
        candidate_keys = _stable_image_keys(candidate)
        if not candidate_keys or candidate_keys & existing_keys:
            skipped += 1
            continue
        if _insert_image_ref_near_context(blocks, _auto_image_ref_block(candidate, section), candidate, section):
            existing_keys.update(candidate_keys)
            inserted += 1
            section_image_counts[section_key] = section_image_counts.get(section_key, 0) + 1
        else:
            skipped += 1
    return inserted, skipped


def _best_fallback_group_for_section(
    groups: list[dict[str, Any]],
    section: dict[str, Any],
    existing_keys: set[str],
) -> dict[str, Any] | None:
    ranked = _rank_auto_image_groups(groups, [section], existing_keys)
    return ranked[0] if ranked else None


def _remove_same_material_single_images_after_group(sections: list[Any]) -> None:
    """同一小节已有套图时，移除同素材散图，避免套图后又重复插一张孤图。"""

    for section in sections:
        if not isinstance(section, dict):
            continue
        blocks = section.get("blocks")
        if not isinstance(blocks, list):
            continue
        group_material_ids = {
            str(block.get("material_slice_id") or "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "image_ref" and block.get("image_group_id")
        }
        if not group_material_ids:
            continue
        kept: list[Any] = []
        for block in blocks:
            if (
                isinstance(block, dict)
                and block.get("type") == "image_ref"
                and not block.get("image_group_id")
                and str(block.get("material_slice_id") or "") in group_material_ids
            ):
                continue
            kept.append(block)
        section["blocks"] = kept


def _apply_image_slot_reuse(
    output: dict[str, Any],
    package: dict[str, Any],
    candidates: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    existing_keys: set[str],
    existing_group_material_ids: set[str],
    section_image_counts: dict[str, int],
    *,
    max_per_section: int,
    max_refs: int,
) -> tuple[int, int, int]:
    slots = output.get("image_slots")
    sections = output.get("sections")
    if not isinstance(slots, list) or not slots or not isinstance(sections, list):
        return 0, 0, 0
    inserted = 0
    inserted_groups = 0
    skipped = 0
    for slot in slots:
        if _count_blocks(sections, "image_ref") >= max_refs:
            break
        if not isinstance(slot, dict) or not str(slot.get("intent") or "").strip():
            skipped += 1
            continue
        section = _section_for_image_slot(slot, sections)
        if not isinstance(section, dict):
            skipped += 1
            continue
        section_key = _section_key(section)
        blocks = section.setdefault("blocks", [])
        if not isinstance(blocks, list):
            skipped += 1
            continue
        max_count = max(0, min(int(slot.get("max_count") or 1), max_refs - _count_blocks(sections, "image_ref")))
        if max_count <= 0:
            continue
        if bool(slot.get("group_preferred")):
            group = _best_image_group_for_slot(slot, groups, section, existing_keys)
            if group:
                group_blocks = _auto_image_group_ref_blocks(group, section)
                if group_blocks and len(group_blocks) <= max(max_per_section, max_count):
                    if _insert_image_group_near_context(blocks, group_blocks, group, section):
                        existing_keys.update(_stable_image_group_keys(group))
                        section_image_counts[section_key] = section_image_counts.get(section_key, 0) + len(group_blocks)
                        inserted_groups += len(group_blocks)
                        continue
        ranked = _rank_image_candidates_for_slot(
            slot,
            candidates,
            section,
            existing_keys,
            existing_group_material_ids,
        )
        if not ranked:
            skipped += 1
            continue
        inserted_for_slot = 0
        for candidate in ranked:
            if inserted_for_slot >= max_count or _count_blocks(sections, "image_ref") >= max_refs:
                break
            if section_image_counts.get(section_key, 0) >= max_per_section:
                break
            candidate_keys = _stable_image_keys(candidate)
            if candidate_keys & existing_keys:
                continue
            if not _insert_image_ref_near_context(blocks, _auto_image_ref_block(candidate, section), candidate, section):
                skipped += 1
                continue
            existing_keys.update(candidate_keys)
            section_image_counts[section_key] = section_image_counts.get(section_key, 0) + 1
            inserted += 1
            inserted_for_slot += 1
        if inserted_for_slot <= 0:
            skipped += 1
    if inserted or inserted_groups:
        output.setdefault("image_slot_reuse", {})
        output["image_slot_reuse"].update(
            {
                "enabled": True,
                "slot_count": len(slots),
                "inserted_count": inserted,
                "inserted_group_member_count": inserted_groups,
                "skipped_count": skipped,
            }
        )
    return inserted, inserted_groups, skipped


def _section_for_image_slot(slot: dict[str, Any], sections: list[Any]) -> dict[str, Any] | None:
    slot_text = _image_slot_text(slot)
    best_section: dict[str, Any] | None = None
    best_score = -1
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or "")
        if _is_general_analysis_section(heading):
            continue
        section_text = " ".join([heading, _section_block_text(section)])
        score = max(
            _text_match_score(str(slot.get("section_heading") or ""), heading),
            _text_match_score(slot_text, heading),
            _text_match_score(slot_text, section_text),
        )
        if _primary_topics(slot_text) & _primary_topics(section_text):
            score += 2
        if score > best_score:
            best_score = score
            best_section = section
    return best_section if best_score > 0 else None


def _rank_image_candidates_for_slot(
    slot: dict[str, Any],
    candidates: list[dict[str, Any]],
    section: dict[str, Any],
    existing_keys: set[str],
    existing_group_material_ids: set[str],
) -> list[dict[str, Any]]:
    scored: list[tuple[int, int, dict[str, Any]]] = []
    slot_text = _image_slot_text(slot)
    for index, candidate in enumerate(candidates):
        image_id = str(candidate.get("image_id") or "")
        if not image_id or _stable_image_keys(candidate) & existing_keys:
            continue
        material_id = str(candidate.get("material_slice_id") or "")
        if material_id and not candidate.get("image_group_id") and material_id in existing_group_material_ids:
            continue
        score = _image_slot_candidate_score(slot_text, candidate, section)
        if score < 4:
            continue
        scored.append((-score, index, candidate))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [candidate for _, _, candidate in scored]


def _best_image_group_for_slot(
    slot: dict[str, Any],
    groups: list[dict[str, Any]],
    section: dict[str, Any],
    existing_keys: set[str],
) -> dict[str, Any] | None:
    slot_text = _image_slot_text(slot)
    best_group: dict[str, Any] | None = None
    best_score = -1
    for group in groups:
        if _stable_image_group_keys(group) & existing_keys:
            continue
        score = _image_slot_candidate_score(slot_text, group, section)
        member_count = int(group.get("member_count") or len(group.get("members") or []) or 0)
        if member_count >= 2:
            score += 2
        if score > best_score:
            best_score = score
            best_group = group
    return best_group if best_score >= 5 else None


def _image_slot_candidate_score(slot_text: str, candidate: dict[str, Any], section: dict[str, Any]) -> int:
    if not _image_candidate_compatible_with_section(candidate, section):
        return 0
    heading = str(section.get("heading") or "")
    if not _has_topic_match(candidate, " ".join([heading, slot_text])):
        return 0
    score = max(
        _text_match_score(_candidate_primary_text(candidate), slot_text),
        _text_match_score(_candidate_match_text(candidate), slot_text),
        _text_match_score(_candidate_match_text(candidate), " ".join([heading, _section_block_text(section)])),
    )
    if _primary_topics(slot_text) & _candidate_primary_topics(candidate):
        score += 2
    score += _section_heading_specificity_bonus(candidate, heading)
    score += _section_subtopic_match_bonus(candidate, heading)
    confidence = float(candidate.get("semantic_confidence") or 0)
    if confidence >= 0.8:
        score += 2
    elif 0 < confidence < 0.55:
        score -= 3
    if str(candidate.get("material_quality") or "") == "high":
        score += 1
    score += _text_image_block_candidate_priority(candidate)
    return score


def _text_image_block_candidate_priority(candidate: dict[str, Any]) -> int:
    if str(candidate.get("source_reuse_mode") or "") != "text_image_block":
        return 0
    if str(candidate.get("text_image_block_match_level") or "") != "strong":
        return 0
    confidence = float(candidate.get("text_image_block_match_confidence") or 0)
    if confidence < 0.75:
        return 0
    priority = 12
    if candidate.get("members") or candidate.get("must_keep_together"):
        priority += 8
    if str(candidate.get("reuse_priority") or "") == "text_image_block_strong":
        priority += 4
    return priority


def _image_slot_text(slot: dict[str, Any]) -> str:
    return " ".join(
        str(slot.get(key) or "")
        for key in ["section_heading", "anchor_text", "intent", "preferred_type"]
        if str(slot.get(key) or "").strip()
    )


def _dedupe_same_caption_single_images(sections: list[Any]) -> int:
    """同一小节内移除同题注散图，套图成员保留完整。"""

    removed_count = 0
    for section in sections:
        if not isinstance(section, dict):
            continue
        blocks = section.get("blocks")
        if not isinstance(blocks, list):
            continue
        seen_captions: set[str] = set()
        kept: list[Any] = []
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "image_ref":
                kept.append(block)
                continue
            if block.get("image_group_id") or block.get("must_keep_with_group"):
                kept.append(block)
                continue
            caption_key = _image_caption_source_dedupe_key(block)
            if caption_key and caption_key in seen_captions:
                removed_count += 1
                continue
            if caption_key:
                seen_captions.add(caption_key)
            kept.append(block)
        section["blocks"] = kept
    return removed_count


def _dedupe_equivalent_images_within_sections(sections: list[Any]) -> int:
    """同一小节内移除等价套图，避免跨优秀标书同款素材重复出现。"""

    removed_count = 0
    for section in sections:
        if not isinstance(section, dict):
            continue
        blocks = section.get("blocks")
        if not isinstance(blocks, list):
            continue
        group_blocks_by_id: dict[str, list[dict[str, Any]]] = {}
        first_index_by_group: dict[str, int] = {}
        duplicate_block_ids: set[int] = set()
        for index, block in enumerate(blocks):
            if not isinstance(block, dict) or block.get("type") != "image_ref":
                continue
            group_id = str(block.get("image_group_id") or "").strip()
            if not group_id:
                continue
            group_blocks_by_id.setdefault(group_id, []).append(block)
            first_index_by_group.setdefault(group_id, index)
        seen_group_keys: set[str] = set()
        for group_id in sorted(group_blocks_by_id, key=lambda item: first_index_by_group.get(item, 0)):
            group_blocks = group_blocks_by_id[group_id]
            removed_count += _mark_duplicate_group_members(group_blocks, duplicate_block_ids)
            group_key = _image_group_equivalence_key_from_blocks(group_blocks)
            if not group_key:
                continue
            if group_key in seen_group_keys:
                for block in group_blocks:
                    if id(block) not in duplicate_block_ids:
                        duplicate_block_ids.add(id(block))
                        removed_count += 1
                continue
            seen_group_keys.add(group_key)
        kept: list[Any] = []
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "image_ref":
                kept.append(block)
                continue
            if id(block) in duplicate_block_ids:
                continue
            kept.append(block)
        section["blocks"] = kept
    return removed_count


def _mark_duplicate_group_members(group_blocks: list[dict[str, Any]], duplicate_block_ids: set[int]) -> int:
    removed_count = 0
    seen_member_indexes: set[int] = set()
    seen_asset_keys: set[str] = set()
    for block in group_blocks:
        member_index = int(block.get("group_member_index") or 0)
        asset_key = _image_asset_equivalence_key(block)
        if member_index and member_index in seen_member_indexes:
            duplicate_block_ids.add(id(block))
            removed_count += 1
            continue
        if not member_index and asset_key and asset_key in seen_asset_keys:
            duplicate_block_ids.add(id(block))
            removed_count += 1
            continue
        if member_index:
            seen_member_indexes.add(member_index)
        if asset_key:
            seen_asset_keys.add(asset_key)
    return removed_count


def _image_group_equivalence_key_from_blocks(group_blocks: list[dict[str, Any]]) -> str:
    if not group_blocks:
        return ""
    first = group_blocks[0]
    title_key = _image_text_equivalence_key(
        first.get("group_semantic_text") or first.get("group_title") or first.get("caption")
    )
    member_keys = sorted(
        {
            key
            for key in (_image_equivalence_key(block) for block in group_blocks)
            if key
        }
    )
    if title_key and member_keys:
        return f"group:{title_key}|members:{'|'.join(member_keys[:16])}"
    if title_key:
        return f"group:{title_key}"
    if len(member_keys) >= 2:
        return f"group_members:{'|'.join(member_keys[:16])}"
    return ""


def _image_group_candidate_equivalence_key(group: dict[str, Any]) -> str:
    title_key = _image_text_equivalence_key(
        group.get("semantic_text") or group.get("group_semantic_text") or group.get("group_title") or group.get("caption")
    )
    member_keys = sorted(
        {
            key
            for member in group.get("members") or []
            if isinstance(member, dict)
            for key in [_image_equivalence_key(member)]
            if key
        }
    )
    if title_key and member_keys:
        return f"group:{title_key}|members:{'|'.join(member_keys[:16])}"
    if title_key:
        return f"group:{title_key}"
    if len(member_keys) >= 2:
        return f"group_members:{'|'.join(member_keys[:16])}"
    return ""


def _existing_section_group_equivalence_keys(section: dict[str, Any]) -> set[str]:
    blocks = section.get("blocks")
    if not isinstance(blocks, list):
        return set()
    group_blocks_by_id: dict[str, list[dict[str, Any]]] = {}
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "image_ref":
            continue
        group_id = str(block.get("image_group_id") or "").strip()
        if group_id:
            group_blocks_by_id.setdefault(group_id, []).append(block)
    return {
        key
        for key in (_image_group_equivalence_key_from_blocks(group_blocks) for group_blocks in group_blocks_by_id.values())
        if key
    }


def _image_asset_equivalence_key(block: dict[str, Any]) -> str:
    return next(
        (
            str(value).strip()
            for value in [
                block.get("canonical_image_id"),
                block.get("sha256"),
                block.get("perceptual_hash"),
                block.get("image_asset_id"),
                block.get("source_part_name") or block.get("part_name"),
                block.get("target"),
                block.get("image_id"),
            ]
            if str(value or "").strip()
        ),
        "",
    )


def _image_equivalence_key(block: dict[str, Any]) -> str:
    for value in [
        block.get("semantic_text"),
        _best_image_semantic_text(block),
        block.get("caption"),
    ]:
        key = _image_text_equivalence_key(value)
        if key:
            return key
    return ""


def _image_text_equivalence_key(value: Any) -> str:
    text = _strip_heading_number(str(value or ""))
    text = _normalize_caption_text(text)
    text = re.sub(r"[（(]\d+[）)]", "", text)
    text = re.sub(r"(做法)?(示意图|示意|图片|照片|图)$", "", text)
    text = re.sub(r"[\s，,。.;；：:（）()【】\[\]、]+", "", text)
    if not text or _is_weak_image_semantic_text(text):
        return ""
    return text.lower()


def _image_caption_dedupe_key(block: dict[str, Any]) -> str:
    text = str(block.get("caption") or "").strip()
    if not text:
        return ""
    return "".join(char for char in text.lower() if not char.isspace() and char not in "，,。.;；：:（）()[]【】")


def _image_caption_source_dedupe_key(block: dict[str, Any]) -> str:
    caption_key = _image_caption_dedupe_key(block)
    if not caption_key:
        return ""
    source_key = next(
        (
            str(value).strip()
            for value in [
                block.get("image_asset_id"),
                block.get("source_part_name") or block.get("part_name"),
                block.get("target"),
                block.get("image_id"),
            ]
            if str(value or "").strip()
        ),
        "",
    )
    if not source_key:
        return ""
    return f"{caption_key}|{source_key}"


def _collect_cross_chapter_image_refs(chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for chapter_index, chapter in enumerate(chapters):
        if not isinstance(chapter, dict):
            continue
        chapter_path = [str(part) for part in chapter.get("chapter_path") or []]
        for section_index, section in enumerate(chapter.get("sections") or []):
            if not isinstance(section, dict):
                continue
            blocks = section.get("blocks")
            if not isinstance(blocks, list):
                continue
            for block_index, block in enumerate(blocks):
                if not isinstance(block, dict) or block.get("type") != "image_ref":
                    continue
                refs.append(
                    {
                        "chapter_index": chapter_index,
                        "section_index": section_index,
                        "block_index": block_index,
                        "chapter_path": chapter_path,
                        "section_heading": section.get("heading"),
                        "block": block,
                        "group_key": _cross_chapter_image_group_key(block),
                        "asset_keys": _cross_chapter_image_asset_keys(block),
                    }
                )
    return refs


def _cross_chapter_group_units(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, int, int], dict[str, Any]] = {}
    for ref in refs:
        group_key = ref["group_key"]
        if not group_key:
            continue
        location_key = (group_key, int(ref["chapter_index"]), int(ref["section_index"]))
        unit = groups.setdefault(
            location_key,
            {
                "group_key": group_key,
                "chapter_index": int(ref["chapter_index"]),
                "section_index": int(ref["section_index"]),
                "first_block_index": int(ref["block_index"]),
                "refs": [],
            },
        )
        unit["first_block_index"] = min(int(unit["first_block_index"]), int(ref["block_index"]))
        unit["refs"].append(ref)
    return sorted(
        groups.values(),
        key=lambda unit: (
            int(unit["chapter_index"]),
            int(unit["section_index"]),
            int(unit["first_block_index"]),
        ),
    )


def _mark_cross_chapter_refs_removed(
    refs: list[dict[str, Any]],
    reason: str,
    remove_block_ids: set[int],
    removed: list[dict[str, Any]],
    changed_chapter_indexes: set[int],
) -> None:
    for ref in refs:
        block = ref["block"]
        block_id = id(block)
        if block_id in remove_block_ids:
            continue
        remove_block_ids.add(block_id)
        changed_chapter_indexes.add(int(ref["chapter_index"]))
        removed.append(_removed_cross_chapter_image_ref(ref, reason))


def _remove_cross_chapter_image_blocks(chapters: list[dict[str, Any]], remove_block_ids: set[int]) -> None:
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        for section in chapter.get("sections") or []:
            if not isinstance(section, dict):
                continue
            blocks = section.get("blocks")
            if not isinstance(blocks, list):
                continue
            section["blocks"] = [block for block in blocks if id(block) not in remove_block_ids]


def _removed_cross_chapter_image_ref(ref: dict[str, Any], reason: str) -> dict[str, Any]:
    block = ref["block"]
    return {
        "reason": reason,
        "chapter_index": int(ref["chapter_index"]),
        "chapter_path": " > ".join(ref["chapter_path"]),
        "section_heading": ref["section_heading"],
        "image_id": block.get("image_id"),
        "image_asset_id": block.get("image_asset_id"),
        "canonical_image_id": block.get("canonical_image_id"),
        "source_bid_id": block.get("source_bid_id") or block.get("source_id"),
        "source_part_name": block.get("source_part_name") or block.get("part_name"),
        "image_group_id": block.get("image_group_id"),
        "caption": block.get("caption"),
    }


def _cross_chapter_image_group_key(block: dict[str, Any]) -> str:
    group_id = str(block.get("image_group_id") or "").strip()
    if group_id:
        return group_id
    return ""


def _cross_chapter_image_asset_keys(block: dict[str, Any]) -> set[str]:
    source_id = str(block.get("source_bid_id") or block.get("source_id") or "").strip()
    part_name = str(block.get("source_part_name") or block.get("part_name") or "").strip()
    keys = {
        _lookup_key("canonical_image_id", block.get("canonical_image_id")),
        _lookup_key("sha256", block.get("sha256")),
        _lookup_key("perceptual_hash", block.get("perceptual_hash")),
        _lookup_key("image_asset_id", block.get("image_asset_id")),
        _lookup_key("image_id", block.get("image_id")),
    }
    if source_id and part_name:
        keys.add(_lookup_key("source_part", f"{source_id}|{part_name}"))
    elif part_name:
        keys.add(_lookup_key("part_name", part_name))
    return {key for key in keys if key}


def _complete_existing_split_image_groups(sections: list[Any], package: dict[str, Any]) -> int:
    """LLM 若已选中套图成员，自动补齐同组其余成员，保证套图不被拆开使用。"""

    groups_by_id = {
        str(group.get("image_group_id") or ""): group
        for group in _all_image_group_candidates(package)
        if isinstance(group, dict) and str(group.get("image_group_id") or "").strip()
    }
    inserted = 0
    for section in sections:
        if not isinstance(section, dict):
            continue
        blocks = section.setdefault("blocks", [])
        if not isinstance(blocks, list):
            continue
        used_by_group: dict[str, set[int]] = {}
        first_index_by_group: dict[str, int] = {}
        for index, block in enumerate(blocks):
            if not isinstance(block, dict) or block.get("type") != "image_ref":
                continue
            group_id = str(block.get("image_group_id") or "")
            if not group_id:
                continue
            member_index = int(block.get("group_member_index") or 0)
            if member_index:
                used_by_group.setdefault(group_id, set()).add(member_index)
            first_index_by_group.setdefault(group_id, index)
        for group_id, used_indexes in list(used_by_group.items()):
            group = groups_by_id.get(group_id)
            if not group:
                continue
            members = [member for member in group.get("members") or [] if isinstance(member, dict)]
            expected = int(group.get("member_count") or len(members) or 0)
            if expected <= 1 or len(used_indexes) >= expected:
                continue
            first_index = first_index_by_group.get(group_id, len(blocks) - 1)
            insert_at = _after_group_block_index(blocks, group_id, first_index)
            for index, member in enumerate(members, start=1):
                if index in used_indexes:
                    continue
                block = _auto_image_ref_block(member, section)
                block["image_group_id"] = group_id
                block["group_title"] = group.get("group_title") or block.get("group_title")
                block["group_semantic_text"] = group.get("semantic_text") or block.get("group_semantic_text")
                block["group_member_index"] = index
                block["group_member_count"] = expected
                block["must_keep_with_group"] = True
                block["auto_completed_group"] = True
                caption = _group_member_caption(group, member, index)
                if caption:
                    block["caption"] = caption
                blocks.insert(insert_at, block)
                insert_at += 1
                inserted += 1
    return inserted


def _after_group_block_index(blocks: list[Any], group_id: str, first_index: int) -> int:
    index = max(first_index, 0)
    cursor = index
    while cursor < len(blocks):
        block = blocks[cursor]
        if not isinstance(block, dict) or block.get("type") != "image_ref":
            if cursor == index:
                cursor += 1
                continue
            break
        if str(block.get("image_group_id") or "") != group_id:
            if cursor == index:
                cursor += 1
                continue
            break
        cursor += 1
    return cursor


def _add_expanded_volume_issues(
    issues: list[dict[str, str]],
    output: dict[str, Any],
    package: dict[str, Any],
) -> None:
    policy = package.get("expanded_generation_policy") or {}
    targets = policy.get("targets") or (package.get("generation_constraints") or {}).get("expanded_targets") or {}
    if not isinstance(targets, dict):
        return
    sections = output.get("sections") or []
    if not isinstance(sections, list):
        return
    section_count = len([section for section in sections if isinstance(section, dict)])
    paragraph_count = _count_blocks(sections, "paragraph")
    table_count = _count_blocks(sections, "rich_table")
    image_placeholder_count = _count_blocks(sections, "image_placeholder")
    if section_count < int(targets.get("min_sections") or 0):
        issues.append(_issue("warning", "expanded_min_sections_not_met", "小节数量低于 expanded 详稿目标。"))
    min_paragraphs_total = int(targets.get("min_paragraphs_total") or 0)
    if _paragraph_total_material_gap(paragraph_count, min_paragraphs_total, section_count):
        issues.append(_issue("advisory", "expanded_min_paragraphs_soft_gap", "正文段落总量略低于 expanded 目标，建议人工复核内容密度。"))
    min_paragraphs_per_section = int(targets.get("min_paragraphs_per_section") or 0)
    if min_paragraphs_per_section and _has_low_density_section(sections, min_paragraphs_per_section):
        issues.append(_issue("warning", "expanded_section_paragraphs_not_met", "存在小节正文或配套措施密度低于 expanded 目标。"))
    if table_count < int(targets.get("min_rich_tables") or 0):
        issues.append(_issue("warning", "expanded_min_tables_not_met", "rich_table 数量低于 expanded 详稿目标。"))
    image_policy = package.get("auto_image_reuse_policy") or {}
    if image_policy.get("allow_placeholders") and image_placeholder_count < int(targets.get("min_image_placeholders") or 0):
        issues.append(_issue("warning", "expanded_min_image_placeholders_not_met", "项目专属图纸章节缺少图片占位。"))
    min_rows = int(targets.get("min_rows_per_rich_table") or 0)
    if min_rows and _has_short_rich_table(sections, min_rows):
        issues.append(_issue("warning", "expanded_table_rows_not_met", "存在行数低于 expanded 目标的 rich_table。"))
    reusable_images = _reusable_image_candidates(package)
    min_image_refs = int(targets.get("min_image_refs") or 0)
    if reusable_images and _count_blocks(sections, "image_ref") < min_image_refs:
        issues.append(_issue("warning", "expanded_reusable_images_not_used", "可复用图片引用数量低于 expanded 目标。"))


def _add_image_reference_issues(
    issues: list[dict[str, str]],
    output: dict[str, Any],
    package: dict[str, Any],
) -> None:
    valid_lookup = _reusable_image_lookup(package)
    review_only_keys = _review_only_image_keys(package)
    for block in _iter_blocks(output.get("sections") or []):
        if block.get("type") != "image_ref":
            continue
        image_id = str(block.get("image_id") or "")
        ref_keys = set(_image_ref_keys(block))
        if ref_keys & review_only_keys:
            issues.append(_issue("warning", "image_ref_requires_manual_review", f"image_ref 引用了需人工复核的图片：{image_id}"))
        elif not (ref_keys & set(valid_lookup)):
            issues.append(_issue("warning", "image_ref_unknown", f"image_ref 未来自可复用图片候选：{image_id}"))
    _add_split_image_group_issues(issues, output)


def _add_split_image_group_issues(
    issues: list[dict[str, str]],
    output: dict[str, Any],
) -> None:
    group_members: dict[str, set[int]] = {}
    expected_counts: dict[str, int] = {}
    for block in _iter_blocks(output.get("sections") or []):
        if block.get("type") != "image_ref":
            continue
        group_id = str(block.get("image_group_id") or "")
        if not group_id:
            continue
        member_index = int(block.get("group_member_index") or 0)
        member_count = int(block.get("group_member_count") or 0)
        if member_index:
            group_members.setdefault(group_id, set()).add(member_index)
        if member_count:
            expected_counts[group_id] = max(expected_counts.get(group_id, 0), member_count)
    for group_id, member_indexes in group_members.items():
        expected = expected_counts.get(group_id, 0)
        if expected and len(member_indexes) < expected:
            issues.append(_issue("warning", "image_group_split", f"套图未完整引用：{group_id}，已引用 {len(member_indexes)}/{expected}。"))


def _run_single_generation(
    package: dict[str, Any],
    *,
    prompt: str,
    config: LlmClientConfig,
    llm_callable: LlmCallable | None,
) -> ChapterGenerationTaskRun:
    unit = package.get("generation_unit") or {}
    unit_id = str(unit.get("unit_id") or "")
    target_node_id = str(unit.get("target_node_id") or "")
    chapter_path = [str(part) for part in unit.get("chapter_path") or []]
    started_at = _now_iso()
    start = time.monotonic()
    response_text = ""
    llm_input: dict[str, Any] = {}
    llm_input_metrics: dict[str, Any] = {}
    try:
        llm_input = _llm_input(package)
        llm_input_metrics = llm_input.get("llm_input_metrics") if isinstance(llm_input.get("llm_input_metrics"), dict) else {}
        llm_call_input = _llm_call_payload(llm_input)
        response_text = (
            llm_callable(llm_call_input, config)
            if llm_callable is not None
            else call_openai_json(
                config=config,
                system_prompt=prompt,
                user_input=json.dumps(llm_call_input, ensure_ascii=False, indent=2),
            )
        )
        parsed_json, response_text, repair_summary = _parse_or_repair_json_response(
            response_text,
            config=config,
            llm_callable=llm_callable,
            package=package,
            prompt=prompt,
        )
        parsed_json = postprocess_chapter_images(normalize_chapter_identity(parsed_json, package), package)
        validation = validate_chapter_output(parsed_json, package)
        if repair_summary.get("applied"):
            validation = _mark_json_repair_warning(validation, repair_summary)
        if _should_retry_expanded(validation):
            try:
                retry_input = _expanded_retry_input(package, parsed_json, validation)
                retry_call_input = _llm_call_payload(retry_input)
                retry_text = (
                    llm_callable(retry_call_input, config)
                    if llm_callable is not None
                    else call_openai_json(
                        config=config,
                        system_prompt=EXPANDED_RETRY_PROMPT,
                        user_input=json.dumps(retry_call_input, ensure_ascii=False, indent=2),
                    )
                )
                retry_json, retry_text, retry_repair_summary = _parse_or_repair_json_response(
                    retry_text,
                    config=config,
                    llm_callable=llm_callable,
                    package=package,
                    prompt=EXPANDED_RETRY_PROMPT,
                )
                retry_json = postprocess_chapter_images(normalize_chapter_identity(retry_json, package), package)
                retry_validation = validate_chapter_output(retry_json, package)
                if retry_repair_summary.get("applied"):
                    retry_validation = _mark_json_repair_warning(retry_validation, retry_repair_summary)
                parsed_json, validation, response_text = _choose_better_generation(
                    original_json=parsed_json,
                    original_validation=validation,
                    original_text=response_text,
                    retry_json=retry_json,
                    retry_validation=retry_validation,
                    retry_text=retry_text,
                )
                repair_summary = _merge_repair_summaries(repair_summary, retry_repair_summary)
            except Exception as retry_exc:
                validation = _mark_retry_error(validation, str(retry_exc))
        status = "failed" if validation.get("blocking") else "completed"
        failure_type = _failure_type_from_validation(validation) if status == "failed" else None
        failure_reason = _failure_reason_from_validation(validation) if status == "failed" else None
        return ChapterGenerationTaskRun(
            unit_id=unit_id,
            target_node_id=target_node_id,
            chapter_path=chapter_path,
            status=status,
            duration_seconds=time.monotonic() - start,
            started_at=started_at,
            completed_at=_now_iso(),
            model=config.model,
            output_text=response_text,
            parsed_json=parsed_json,
            validation=validation,
            failure_type=failure_type,
            failure_reason=failure_reason,
            repair_attempt_count=int(repair_summary.get("attempt_count") or 0),
            repair_duration_seconds=float(repair_summary.get("duration_seconds") or 0.0),
            repair_summary=repair_summary,
            llm_input_schema_version=str(llm_input.get("llm_input_schema_version") or ""),
            llm_input_profile=str(llm_input.get("llm_input_profile") or ""),
            llm_input_char_count=int(llm_input_metrics.get("llm_input_char_count") or 0),
            full_package_char_count=int(llm_input_metrics.get("full_package_char_count") or 0),
            llm_input_compression_ratio=float(llm_input_metrics.get("compression_ratio") or 0.0),
            llm_input_metrics=llm_input_metrics,
        )
    except Exception as exc:  # pragma: no cover - 真实网络调用和异常兜底
        failure_type = _classify_generation_exception(exc)
        return ChapterGenerationTaskRun(
            unit_id=unit_id,
            target_node_id=target_node_id,
            chapter_path=chapter_path,
            status="failed",
            duration_seconds=time.monotonic() - start,
            started_at=started_at,
            completed_at=_now_iso(),
            model=config.model,
            output_text=response_text,
            validation={"valid": False, "blocking": True, "issue_count": 1, "issues": []},
            error=str(exc),
            failure_type=failure_type,
            failure_reason=str(exc),
            llm_input_schema_version=str(llm_input.get("llm_input_schema_version") or ""),
            llm_input_profile=str(llm_input.get("llm_input_profile") or ""),
            llm_input_char_count=int(llm_input_metrics.get("llm_input_char_count") or 0),
            full_package_char_count=int(llm_input_metrics.get("full_package_char_count") or 0),
            llm_input_compression_ratio=float(llm_input_metrics.get("compression_ratio") or 0.0),
            llm_input_metrics=llm_input_metrics,
        )


def postprocess_chapter_images(output: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    output = strip_disallowed_image_placeholders(output, package)
    output = enrich_image_refs(output, package)
    output = filter_mismatched_image_refs(output, package)
    output = apply_auto_image_reuse(output, package)
    output = enrich_image_refs(output, package)
    output = clean_image_captions(output, package)
    return output


def _run_generation_tasks(
    packages: list[dict[str, Any]],
    *,
    prompt: str,
    config: LlmClientConfig,
    llm_callable: LlmCallable | None,
    max_workers: int,
) -> list[ChapterGenerationTaskRun]:
    if len(packages) <= 1 or max_workers <= 1:
        return [
            _run_single_generation(package, prompt=prompt, config=config, llm_callable=llm_callable)
            for package in packages
        ]
    workers = max(1, min(max_workers, len(packages)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                copy_context().run,
                _run_single_generation,
                package,
                prompt=prompt,
                config=config,
                llm_callable=llm_callable,
            )
            for package in packages
        ]
        return [future.result() for future in futures]


def _effective_max_workers(max_workers: int | None, config: LlmClientConfig) -> int:
    return max(1, int(max_workers if max_workers is not None else config.max_workers))


def _should_retry_expanded(validation: dict[str, Any]) -> bool:
    if validation.get("blocking"):
        return False
    retryable_types = {
        "expanded_min_sections_not_met",
        "expanded_section_paragraphs_not_met",
        "expanded_min_tables_not_met",
        "expanded_table_rows_not_met",
        "expanded_reusable_images_not_used",
    }
    return any(
        isinstance(issue, dict) and str(issue.get("type") or "") in retryable_types
        for issue in validation.get("issues") or []
    )


def _parse_or_repair_json_response(
    response_text: str,
    *,
    config: LlmClientConfig,
    llm_callable: LlmCallable | None,
    package: dict[str, Any],
    prompt: str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    repair_started = time.monotonic()
    try:
        parsed, rule_metadata = parse_json_response_with_repair_info(response_text)
        if rule_metadata.get("method") == "rule":
            return parsed, response_text, {
                "applied": True,
                "method": "rule",
                "attempt_count": 1,
                "duration_seconds": time.monotonic() - repair_started,
                "original_error": rule_metadata.get("original_error"),
                "model_repair_used": False,
            }
        return parsed, response_text, {
            "applied": False,
            "method": "none",
            "attempt_count": 0,
            "duration_seconds": 0.0,
            "model_repair_used": False,
        }
    except Exception as parse_exc:
        repair_input = {
            "task_type": "repair_json_syntax_only",
            "expected_schema": OUTPUT_SCHEMA_VERSION,
            "generation_unit": package.get("generation_unit") or {},
            "original_prompt_hint": _clip(prompt, 1200),
            "parse_error": str(parse_exc),
            "broken_json": response_text,
        }
        repaired_text = (
            llm_callable(repair_input, config)
            if llm_callable is not None
            else call_openai_json(
                config=config,
                system_prompt=JSON_REPAIR_PROMPT,
                user_input=json.dumps(repair_input, ensure_ascii=False, indent=2),
            )
        )
        parsed, model_repair_metadata = parse_json_response_with_repair_info(repaired_text)
        return parsed, repaired_text, {
            "applied": True,
            "method": "model",
            "attempt_count": 1 + int(model_repair_metadata.get("repair_count") or 0),
            "duration_seconds": time.monotonic() - repair_started,
            "original_error": str(parse_exc),
            "model_repair_used": True,
            "model_repair_rule_cleanup": model_repair_metadata.get("method") == "rule",
        }


def _mark_json_repair_warning(validation: dict[str, Any], repair_summary: dict[str, Any]) -> dict[str, Any]:
    result = dict(validation)
    issues = list(result.get("issues") or [])
    method = str(repair_summary.get("method") or "unknown")
    method_label = "规则修复" if method == "rule" else "模型修复"
    error = str(repair_summary.get("original_error") or "")
    issues.append(_issue("warning", "json_repair_applied", f"模型返回 JSON 首次解析失败，已通过{method_label}自动修复：{error}"))
    result["issues"] = issues
    result["issue_count"] = len(issues)
    result["warning_issue_count"] = sum(1 for issue in issues if issue.get("severity") == "warning")
    result["json_repair_applied"] = True
    result["json_repair_method"] = method
    result["json_repair_attempt_count"] = int(repair_summary.get("attempt_count") or 0)
    result["json_repair_duration_seconds"] = round(float(repair_summary.get("duration_seconds") or 0.0), 3)
    return result


def _merge_repair_summaries(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    if not first.get("applied") and not second.get("applied"):
        return first
    methods = [
        str(summary.get("method"))
        for summary in (first, second)
        if summary.get("applied") and summary.get("method")
    ]
    return {
        "applied": bool(first.get("applied") or second.get("applied")),
        "method": "+".join(methods) if methods else "none",
        "attempt_count": int(first.get("attempt_count") or 0) + int(second.get("attempt_count") or 0),
        "duration_seconds": float(first.get("duration_seconds") or 0.0) + float(second.get("duration_seconds") or 0.0),
        "original_error": first.get("original_error") or second.get("original_error"),
        "model_repair_used": bool(first.get("model_repair_used") or second.get("model_repair_used")),
    }


def _classify_generation_exception(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}".lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "rate limit" in text or "429" in text or "too many requests" in text:
        return "rate_limited"
    if "json" in text or "expecting" in text or "delimiter" in text:
        return "json_parse_failed"
    if "image" in text and ("path" in text or "file" in text):
        return "image_path_missing"
    return "generation_exception"


def _failure_type_from_validation(validation: dict[str, Any]) -> str:
    for issue in validation.get("issues") or []:
        if not isinstance(issue, dict) or issue.get("severity") != "blocking":
            continue
        issue_type = str(issue.get("type") or "")
        if issue_type in {"schema_version", "unit_id_mismatch", "target_node_id_mismatch", "chapter_path_mismatch"}:
            return "schema_validation_failed"
        if issue_type.startswith("technical_bid_completeness_"):
            return "content_policy_failed"
        if issue_type in {"empty_sections", "empty_content"}:
            return "empty_content"
        return "validation_failed"
    return "validation_failed"


def _failure_reason_from_validation(validation: dict[str, Any]) -> str | None:
    for issue in validation.get("issues") or []:
        if isinstance(issue, dict) and issue.get("severity") == "blocking":
            return str(issue.get("message") or issue.get("type") or "")
    return None


def _expanded_retry_input(
    package: dict[str, Any],
    draft: dict[str, Any],
    validation: dict[str, Any],
) -> dict[str, Any]:
    source = _llm_input(package)
    retry_input = {
        "task_type": "expand_existing_technical_bid_chapter",
        "llm_input_schema_version": source.get("llm_input_schema_version"),
        "llm_input_profile": source.get("llm_input_profile"),
        "project_info": source.get("project_info") or {},
        "generation_unit": source.get("generation_unit") or {},
        "score_point": source.get("score_point") or {},
        "expanded_generation_policy": source.get("expanded_generation_policy") or {},
        "generation_constraints": source.get("generation_constraints") or {},
        "technical_requirements": source.get("technical_requirements") or [],
        "excellent_bid_references": source.get("excellent_bid_references") or [],
        "table_references": source.get("table_references") or [],
        "table_references_slim": source.get("table_references_slim") or [],
        "image_candidates": source.get("image_candidates") or [],
        "image_candidates_slim": source.get("image_candidates_slim") or [],
        "image_groups_slim": source.get("image_groups_slim") or [],
        "reuse_warnings": _limited_reuse_warnings(package.get("reuse_warnings") or []),
        "validation_issues": [
            issue
            for issue in validation.get("issues") or []
            if isinstance(issue, dict) and str(issue.get("type") or "").startswith("expanded_")
        ],
        "current_draft": draft,
    }
    retry_input["llm_input_metrics"] = _llm_input_metrics(package, retry_input)
    _refresh_llm_input_sent_metrics(retry_input)
    return retry_input


def _llm_call_payload(llm_input: dict[str, Any]) -> dict[str, Any]:
    """生成实际发给模型的载荷，去掉后端统计和重复别名。"""

    payload = {
        key: value
        for key, value in llm_input.items()
        if key
        not in {
            "llm_input_metrics",
            "table_references_slim",
            "image_candidates_slim",
        }
    }
    return payload


def _choose_better_generation(
    *,
    original_json: dict[str, Any],
    original_validation: dict[str, Any],
    original_text: str,
    retry_json: dict[str, Any],
    retry_validation: dict[str, Any],
    retry_text: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if retry_validation.get("blocking"):
        return original_json, _mark_retry_result(original_validation, accepted=False), original_text
    original_expanded = _expanded_issue_count(original_validation)
    retry_expanded = _expanded_issue_count(retry_validation)
    if retry_expanded <= original_expanded:
        return retry_json, _mark_retry_result(retry_validation, accepted=True), retry_text
    return original_json, _mark_retry_result(original_validation, accepted=False), original_text


def _expanded_issue_count(validation: dict[str, Any]) -> int:
    return sum(
        1
        for issue in validation.get("issues") or []
        if isinstance(issue, dict) and str(issue.get("type") or "").startswith("expanded_")
    )


def _mark_retry_result(validation: dict[str, Any], *, accepted: bool) -> dict[str, Any]:
    result = dict(validation)
    result["expanded_retry_attempted"] = True
    result["expanded_retry_accepted"] = accepted
    return result


def _mark_retry_error(validation: dict[str, Any], error: str) -> dict[str, Any]:
    result = dict(validation)
    result["expanded_retry_attempted"] = True
    result["expanded_retry_accepted"] = False
    result["expanded_retry_error"] = error
    issues = list(result.get("issues") or [])
    issues.append(_issue("warning", "expanded_retry_failed", f"expanded 补写重试失败，已保留首次生成稿：{error}"))
    result["issues"] = issues
    result["issue_count"] = len(issues)
    result["warning_issue_count"] = sum(1 for issue in issues if issue.get("severity") == "warning")
    return result


def _llm_input(package: dict[str, Any]) -> dict[str, Any]:
    """压缩输入包，避免把调试级素材元数据原样送入模型。

    完整 package 仍保留给后处理、图片补全、去重和渲染使用；这里仅控制真正送入 LLM 的内容。
    """

    slim_package = _completion_statement_package(package) if _is_technical_bid_completeness_package(package) else package
    image_groups = _limited_image_groups(_all_image_group_candidates(slim_package))
    grouped_image_keys = _grouped_image_keys(image_groups)
    image_candidates = _limited_images(_all_image_candidates(slim_package), excluded_image_keys=grouped_image_keys)
    table_references = _limited_tables(slim_package.get("table_references") or [])
    text_image_blocks = _limited_text_image_blocks(slim_package.get("text_image_block_candidates") or [])
    slim_input = {
        "task_type": slim_package.get("task_type"),
        "schema_version": slim_package.get("schema_version"),
        "llm_input_schema_version": LLM_INPUT_SCHEMA_VERSION,
        "llm_input_profile": LLM_INPUT_PROFILE,
        "project_info": slim_package.get("project_info") or {},
        "generation_unit": slim_package.get("generation_unit") or {},
        "score_point": slim_package.get("score_point") or {},
        "technical_requirements": _limited_requirements(slim_package.get("technical_requirements") or []),
        "excellent_bid_references": _limited_references(slim_package.get("excellent_bid_references") or []),
        "table_references": table_references,
        "table_references_slim": table_references,
        "text_image_block_candidates": text_image_blocks,
        "image_candidates": _image_semantic_guides(image_candidates),
        "image_candidates_slim": image_candidates,
        "image_groups_slim": _image_group_semantic_guides(image_groups),
        "reuse_warnings": _limited_reuse_warnings(slim_package.get("reuse_warnings") or []),
        "chapter_reuse_profile": slim_package.get("chapter_reuse_profile") or (slim_package.get("generation_constraints") or {}).get("chapter_reuse_profile") or {},
        "expanded_generation_policy": slim_package.get("expanded_generation_policy") or {},
        "generation_constraints": _limited_generation_constraints(slim_package.get("generation_constraints") or {}),
    }
    slim_input["llm_input_metrics"] = _llm_input_metrics(package, slim_input)
    _refresh_llm_input_sent_metrics(slim_input)
    return slim_input


def _completion_statement_package(package: dict[str, Any]) -> dict[str, Any]:
    slim = copy.deepcopy(package)
    unit = slim.setdefault("generation_unit", {})
    unit["child_headings"] = [
        "技术标响应范围",
        "章节完整性组织",
        "响应依据与编制原则",
        "技术标完整性承诺",
    ]
    policy = slim.setdefault("expanded_generation_policy", {})
    policy["section_type"] = TECHNICAL_BID_COMPLETENESS_SECTION_TYPE
    policy["preferred_section_headings"] = unit["child_headings"]
    policy["targets"] = {
        "min_sections": 4,
        "min_paragraphs_per_section": 2,
        "min_paragraphs_total": 8,
        "min_rich_tables": 0,
        "min_rows_per_rich_table": 0,
        "min_image_refs": 0,
        "min_image_placeholders": 0,
    }
    policy["writing_requirements"] = [
        "本章是技术标完整性说明，不是施工方案章节，也不是评分点响应检查表。",
        "只输出正式正文段落，禁止输出 rich_table、image_ref、image_placeholder。",
        "不得写评分点数量，不得写七个核心评分项、七个强制性评分点、十三个评分点等数量表述。",
        "不得写强制性评分点、不提供即不合格、半数以上评委确认等否决性结论。",
        "不得输出评分点响应汇总表、章节完整性检查表、评分点逐项响应说明等内部复核内容。",
    ]
    constraints = slim.setdefault("generation_constraints", {})
    constraints["expanded_targets"] = policy["targets"]
    constraints["completion_statement_only"] = True
    slim["table_references"] = []
    slim["image_candidates"] = []
    slim["image_candidate_pool"] = []
    slim["image_group_candidates"] = []
    slim["image_group_candidate_pool"] = []
    slim["text_image_block_candidates"] = []
    slim["excellent_bid_references"] = []
    return slim


def _limited_requirements(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in items[:8]:
        result.append(
            {
                "requirement_id": item.get("requirement_id"),
                "type": item.get("type"),
                "category": item.get("category"),
                "raw_clause": _clip(item.get("raw_clause"), 900),
                "priority": item.get("priority"),
            }
        )
    return result


def _limited_references(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in items[:6]:
        result.append(
            {
                "ref_id": item.get("ref_id"),
                "title": item.get("title"),
                "section_path": item.get("section_path") or [],
                "retrieval_score": item.get("retrieval_score"),
                "material_quality": item.get("material_quality"),
                "primary_material_source": item.get("primary_material_source"),
                "reuse_level": item.get("reuse_level"),
                "reference_excerpt": _clip(item.get("reference_excerpt"), _reference_excerpt_limit(item)),
                "do_not_copy": (item.get("do_not_copy") or [])[:6],
            }
        )
    return result


def _limited_tables(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in items[:6]:
        result.append(
            {
                "table_id": item.get("table_id"),
                "title": item.get("title"),
                "table_type": item.get("table_type"),
                "columns": _limited_table_columns(item.get("columns") or []),
                "row_count": item.get("row_count"),
                "image_count": item.get("image_count"),
                "style_hint": _limited_table_style_hint(item.get("style_hint") or {}),
                "use_policy": item.get("use_policy"),
                "material_quality": item.get("material_quality"),
            }
        )
    return result


def _limited_text_image_blocks(items: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    result = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "block_id": item.get("block_id"),
                "block_type": item.get("block_type"),
                "title": item.get("title"),
                "section_path": item.get("section_path") or [],
                "topics": (item.get("topics") or [])[:6],
                "primary_topic": item.get("primary_topic"),
                "secondary_topics": (item.get("secondary_topics") or [])[:5],
                "match_level": item.get("match_level"),
                "match_confidence": item.get("match_confidence"),
                "match_reasons": (item.get("match_reasons") or [])[:6],
                "risk_flags": (item.get("risk_flags") or [])[:6],
                "summary": _clip(item.get("summary"), 520),
                "image_count": item.get("image_count"),
                "image_group_count": item.get("image_group_count"),
                "table_count": item.get("table_count"),
                "captions": [_clip(caption, 80) for caption in (item.get("captions") or [])[:8]],
                "reuse_level": item.get("reuse_level"),
                "project_specific_risk": item.get("project_specific_risk"),
                "use_policy": item.get("use_policy"),
                "render_policy": item.get("render_policy") or {},
                "retrieval_score": item.get("retrieval_score"),
                "usage_note": "仅供理解成熟图文块的主题、图表密度和套图关系；如需采用，只在 source_usage 中记录 block_id，不要输出 image_ref 或编造图片 ID。",
            }
        )
    return result


def _limited_images(
    items: list[dict[str, Any]],
    *,
    excluded_image_keys: set[str] | None = None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    result = []
    excluded = excluded_image_keys or set()
    for item in items:
        if len(result) >= limit:
            break
        if not isinstance(item, dict):
            continue
        if _slim_image_keys(item) & excluded:
            continue
        result.append(
            {
                "image_id": item.get("image_id"),
                "image_asset_id": item.get("image_asset_id"),
                "canonical_image_id": item.get("canonical_image_id"),
                "caption": item.get("caption"),
                "semantic_text": _clip(item.get("semantic_text"), 320),
                "tags": (item.get("tags") or [])[:4],
                "bound_section": item.get("bound_section"),
                "reuse_level": item.get("reuse_level"),
                "risk_level": item.get("risk_level"),
                "image_group_id": item.get("image_group_id"),
                "group_title": item.get("group_title"),
                "group_member_index": item.get("group_member_index"),
                "group_member_count": item.get("group_member_count"),
                "must_keep_with_group": bool(item.get("must_keep_with_group")),
                "image_form": item.get("image_form"),
                "fit_level": item.get("fit_level"),
            }
        )
    return result


def _image_semantic_guides(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给 LLM 的图片语义提示，不包含图片 ID，避免模型直接选图。"""

    guides: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        guide = {
            "caption": item.get("caption"),
            "semantic_text": item.get("semantic_text"),
            "tags": item.get("tags") or [],
            "bound_section": item.get("bound_section"),
            "reuse_level": item.get("reuse_level"),
            "risk_level": item.get("risk_level"),
            "image_group": "grouped" if item.get("image_group_id") else None,
            "must_keep_with_group": bool(item.get("must_keep_with_group")),
            "fit_level": item.get("fit_level"),
            "usage_note": "仅供理解素材语义和图文密度，模型不要输出 image_ref；系统后处理自动插图。",
        }
        guides.append({key: value for key, value in guide.items() if value not in (None, "", [], {})})
    return guides


def _image_group_semantic_guides(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给 LLM 的套图语义提示，不暴露成员图片 ID。"""

    guides: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        guide = {
            "group_title": item.get("group_title"),
            "group_semantic_text": item.get("group_semantic_text"),
            "image_count": item.get("image_count"),
            "captions": item.get("captions") or [],
            "reuse_level": item.get("reuse_level"),
            "risk_level": item.get("risk_level"),
            "fit_level": item.get("fit_level"),
            "usage_note": "仅供理解套图主题；系统会决定是否完整插入套图，模型不要输出 image_ref。",
        }
        guides.append({key: value for key, value in guide.items() if value not in (None, "", [], {})})
    return guides


def _limited_image_groups(items: list[dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if len(result) >= limit:
            break
        if not isinstance(item, dict):
            continue
        group_id = str(item.get("image_group_id") or "")
        members = [member for member in item.get("members") or [] if isinstance(member, dict)]
        if not group_id or group_id in seen or len(members) < 2:
            continue
        seen.add(group_id)
        result.append(
            {
                "image_group_id": group_id,
                "group_title": item.get("group_title") or item.get("title"),
                "group_semantic_text": _clip(item.get("semantic_text") or item.get("group_semantic_text"), 420),
                "image_count": int(item.get("member_count") or len(members)),
                "captions": [_clip(caption, 80) for caption in (item.get("captions") or [])[:12]],
                "members": _limited_group_members(members),
                "reuse_level": item.get("reuse_level"),
                "risk_level": item.get("risk_level"),
                "fit_level": item.get("fit_level"),
                "reuse_policy": "use_as_group",
            }
        )
    return result


def _limited_group_members(members: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, member in enumerate(members[:limit], start=1):
        if not isinstance(member, dict):
            continue
        result.append(
            {
                "image_id": member.get("image_id"),
                "image_asset_id": member.get("image_asset_id"),
                "canonical_image_id": member.get("canonical_image_id"),
                "caption": member.get("caption"),
                "semantic_text": _clip(member.get("semantic_text"), 220),
                "group_member_index": int(member.get("group_member_index") or index),
                "group_member_count": int(member.get("group_member_count") or len(members)),
            }
        )
    return result


def _grouped_image_keys(groups: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        for member in group.get("members") or []:
            if isinstance(member, dict):
                keys.update(_slim_image_keys(member))
    return keys


def _slim_image_keys(item: dict[str, Any]) -> set[str]:
    return {
        key
        for key in [
            _lookup_key("image_id", item.get("image_id")),
            _lookup_key("image_asset_id", item.get("image_asset_id")),
            _lookup_key("canonical_image_id", item.get("canonical_image_id")),
        ]
        if key
    }


def _limited_table_columns(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for column in columns[:6]:
        if not isinstance(column, dict):
            continue
        result.append(
            {
                "key": column.get("key"),
                "title": column.get("title"),
            }
        )
    return result


def _limited_table_style_hint(style_hint: dict[str, Any]) -> dict[str, Any]:
    return {
        "has_image_column": style_hint.get("has_image_column"),
        "border_style": style_hint.get("border_style"),
    }


def _limited_reuse_warnings(items: list[Any]) -> list[str]:
    return [_clip(item, 160) for item in items[:5]]


def _limited_generation_constraints(constraints: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(constraints, dict):
        return {}
    result: dict[str, Any] = {
        "generation_mode": constraints.get("generation_mode"),
        "style": constraints.get("style"),
        "must_keep_level1_heading_raw": constraints.get("must_keep_level1_heading_raw"),
        "allow_generic_measures_when_missing_detail": constraints.get("allow_generic_measures_when_missing_detail"),
        "domain_generation_independent": constraints.get("domain_generation_independent"),
        "completion_statement_only": constraints.get("completion_statement_only"),
    }
    forbidden = [str(item) for item in constraints.get("forbidden_content") or [] if str(item).strip()]
    if forbidden:
        result["forbidden_content"] = forbidden[:8]
    trace_scan = _limited_history_trace_scan(constraints.get("history_trace_scan") or {})
    if trace_scan:
        result["history_trace_scan"] = trace_scan
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _limited_history_trace_scan(scan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(scan, dict) or scan.get("enabled") is False:
        return {"enabled": False} if isinstance(scan, dict) and scan.get("enabled") is False else {}
    candidate_terms = [str(item) for item in scan.get("candidate_terms") or [] if str(item).strip()]
    current_values = [str(item) for item in scan.get("current_project_values") or [] if str(item).strip()]
    result: dict[str, Any] = {"enabled": bool(scan.get("enabled", True))}
    if candidate_terms:
        result["candidate_terms"] = candidate_terms[:8]
    if current_values:
        result["current_project_values"] = current_values[:8]
    return result


def _llm_input_metrics(full_package: dict[str, Any], slim_input: dict[str, Any]) -> dict[str, Any]:
    measured_slim = {key: value for key, value in slim_input.items() if key != "llm_input_metrics"}
    full_chars = _json_char_count(full_package)
    slim_chars = _json_char_count(measured_slim)
    dropped_fields = [
        field
        for field in [
            "image_candidate_pool",
            "image_group_candidate_pool",
            "image_group_candidates",
            "table_references.rows",
            "image_candidates.part_name",
            "image_candidates.sha256",
            "image_candidates.perceptual_hash",
            "image_candidates.nearby_text",
            "image_candidates.caption_candidates",
            "image_candidates.notes",
            "text_image_block_candidates.image_asset_ids",
            "text_image_block_candidates.image_group_ids",
            "text_image_block_candidates.full_blocks",
        ]
        if _full_package_has_field(full_package, field)
    ]
    return {
        "full_package_char_count": full_chars,
        "llm_input_char_count": slim_chars,
        "saved_char_count": max(0, full_chars - slim_chars),
        "compression_ratio": round(slim_chars / full_chars, 4) if full_chars else 0.0,
        "dropped_fields": dropped_fields,
        "table_reference_count": len(measured_slim.get("table_references_slim") or []),
        "text_image_block_count": len(measured_slim.get("text_image_block_candidates") or []),
        "image_candidate_count": len(measured_slim.get("image_candidates_slim") or []),
        "image_group_count": len(measured_slim.get("image_groups_slim") or []),
    }


def _refresh_llm_input_sent_metrics(llm_input: dict[str, Any]) -> None:
    metrics = llm_input.get("llm_input_metrics")
    if not isinstance(metrics, dict):
        return
    full_chars = int(metrics.get("full_package_char_count") or 0)
    sent_chars = _json_char_count(_llm_call_payload(llm_input))
    metrics["llm_input_char_count"] = sent_chars
    metrics["saved_char_count"] = max(0, full_chars - sent_chars)
    metrics["compression_ratio"] = round(sent_chars / full_chars, 4) if full_chars else 0.0
    metrics["omitted_call_fields"] = ["llm_input_metrics", "table_references_slim", "image_candidates_slim"]


def _json_char_count(value: Any) -> int:
    if not value:
        return 0
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _full_package_has_field(package: dict[str, Any], field: str) -> bool:
    if "." not in field:
        return bool(package.get(field))
    head, tail = field.split(".", 1)
    value = package.get(head)
    if isinstance(value, list):
        return any(isinstance(item, dict) and _full_package_has_field(item, tail) for item in value)
    if isinstance(value, dict):
        return _full_package_has_field(value, tail)
    return False


def _filter_packages(packages: list[dict[str, Any]], *, chapter_title_contains: str | None) -> list[dict[str, Any]]:
    if not chapter_title_contains:
        return packages
    keyword = chapter_title_contains.strip()
    if not keyword:
        return packages
    result = []
    for package in packages:
        unit = package.get("generation_unit") or {}
        text = " > ".join(str(part) for part in unit.get("chapter_path") or [])
        if keyword in text:
            result.append(package)
    return result


def _skipped_task(package: dict[str, Any], model: str, error: str) -> ChapterGenerationTaskRun:
    unit = package.get("generation_unit") or {}
    now = _now_iso()
    return ChapterGenerationTaskRun(
        unit_id=str(unit.get("unit_id") or ""),
        target_node_id=str(unit.get("target_node_id") or ""),
        chapter_path=[str(part) for part in unit.get("chapter_path") or []],
        status="skipped",
        duration_seconds=0,
        started_at=now,
        completed_at=now,
        model=model,
        validation={"valid": False, "blocking": False, "issue_count": 0, "issues": []},
        error=error,
        failure_type="configuration_skipped",
        failure_reason=error,
    )


def _has_text_block(sections: list[Any]) -> bool:
    for section in sections:
        if not isinstance(section, dict):
            continue
        for block in section.get("blocks") or []:
            if isinstance(block, dict) and block.get("type") == "paragraph" and str(block.get("text") or "").strip():
                return True
    return False


def _iter_blocks(sections: list[Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        for block in section.get("blocks") or []:
            if isinstance(block, dict):
                blocks.append(block)
    return blocks


def _count_blocks(sections: list[Any], block_type: str) -> int:
    return sum(1 for block in _iter_blocks(sections) if block.get("type") == block_type)


def _section_image_counts(sections: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for section in sections:
        if not isinstance(section, dict):
            continue
        section_key = _section_key(section)
        counts[section_key] = sum(
            1
            for block in section.get("blocks") or []
            if isinstance(block, dict) and block.get("type") == "image_ref"
        )
    return counts


def _section_key(section: dict[str, Any]) -> str:
    return f"{section.get('level') or ''}:{section.get('heading') or ''}"


def _reusable_image_candidates(package: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in _all_image_candidates(package)
        if isinstance(item, dict) and _image_auto_reuse_allowed(item) and item.get("image_id")
    ]


def _reusable_image_lookup(package: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for candidate in _reusable_image_candidates(package):
        for key in _image_candidate_keys(candidate):
            lookup.setdefault(key, candidate)
    for group in _auto_reusable_image_group_candidates(package):
        for member in group.get("members") or []:
            if not isinstance(member, dict):
                continue
            for key in _image_candidate_keys(member):
                lookup.setdefault(key, member)
    return lookup


def _review_only_image_keys(package: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for item in _all_image_candidates(package):
        if not isinstance(item, dict):
            continue
        if _image_auto_reuse_allowed(item):
            continue
        keys.update(_image_candidate_keys(item))
    return keys


def _image_ref_allowed_for_auto_render(
    block: dict[str, Any],
    reusable_lookup: dict[str, dict[str, Any]],
) -> bool:
    return any(key in reusable_lookup for key in _image_ref_keys(block))


def _auto_reusable_image_candidates(package: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _all_text_image_block_image_candidates(package) + _all_image_candidates(package):
        if not isinstance(item, dict):
            continue
        image_id = str(item.get("image_id") or "")
        if not image_id or image_id in seen:
            continue
        if not _image_auto_reuse_allowed(item):
            continue
        if str(item.get("risk_level") or "") == "high":
            continue
        if bool(item.get("must_keep_with_group")) or item.get("image_group_id"):
            continue
        if not _is_renderable_image_part(item):
            continue
        if not _is_auto_reuse_semantically_stable(item):
            continue
        seen.add(image_id)
        result.append(item)
    return result


def _auto_reusable_image_group_candidates(package: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _all_text_image_block_image_group_candidates(package) + _all_image_group_candidates(package):
        if not isinstance(item, dict):
            continue
        group_id = str(item.get("image_group_id") or "")
        if not group_id or group_id in seen:
            continue
        if not _image_auto_reuse_allowed(item):
            continue
        if str(item.get("risk_level") or "") == "high":
            continue
        if bool(item.get("review_required")):
            continue
        members = [member for member in item.get("members") or [] if isinstance(member, dict)]
        if len(members) < 2:
            continue
        if not all(_is_renderable_image_part(member) for member in members):
            continue
        if not _is_auto_reuse_semantically_stable(item):
            continue
        seen.add(group_id)
        result.append(item)
    return result


def _is_renderable_image_part(item: dict[str, Any]) -> bool:
    part_name = str(item.get("part_name") or item.get("target") or "").lower()
    return part_name.endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff"))


def _image_auto_reuse_allowed(item: dict[str, Any]) -> bool:
    reuse_level = str(item.get("reuse_level") or item.get("use_policy") or "")
    if reuse_level not in {"candidate_reuse", "direct_reuse"}:
        return False
    if str(item.get("risk_level") or "").lower() == "high":
        return False
    if bool(item.get("review_required")):
        return False
    return True


def _all_text_image_block_image_candidates(package: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for block in _auto_reusable_text_image_blocks(package):
        for item in block.get("image_candidates") or []:
            if isinstance(item, dict):
                candidates.append(item)
    return candidates


def _all_text_image_block_image_group_candidates(package: dict[str, Any]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for block in _auto_reusable_text_image_blocks(package):
        for item in block.get("image_group_candidates") or []:
            if isinstance(item, dict):
                groups.append(item)
    return groups


def _auto_reusable_text_image_blocks(package: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        block
        for block in package.get("text_image_block_reuse_candidates") or []
        if isinstance(block, dict) and _text_image_block_auto_reuse_allowed(block)
    ]


def _text_image_block_auto_reuse_allowed(block: dict[str, Any]) -> bool:
    if str(block.get("match_level") or "") != "strong":
        return False
    if float(block.get("match_confidence") or 0) < 0.75:
        return False
    if str(block.get("project_specific_risk") or "").lower() == "high":
        return False
    if str(block.get("reuse_level") or "") == "manual_review":
        return False
    risk_flags = {str(flag) for flag in block.get("risk_flags") or []}
    blocked_flags = {
        "general_analysis",
        "manual_review",
        "primary_topic_only_from_caption",
        "subtopic_only_from_caption",
        "missing_target_subtopic",
        "target_topic_is_secondary",
    }
    if risk_flags & blocked_flags:
        return False
    return not any(flag.startswith(("primary_topic_mismatch", "other_process_primary_topic")) for flag in risk_flags)


def _all_image_candidates(package: dict[str, Any]) -> list[dict[str, Any]]:
    pool = package.get("image_candidate_pool")
    if isinstance(pool, list) and pool:
        return pool
    return package.get("image_candidates") or []


def _all_image_group_candidates(package: dict[str, Any]) -> list[dict[str, Any]]:
    pool = package.get("image_group_candidate_pool")
    if isinstance(pool, list) and pool:
        return pool
    return package.get("image_group_candidates") or []


def _image_candidate_lookup(package: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """建立图片候选多键索引，兼容历史 image_id 与新图片资产 ID。"""

    lookup: dict[str, dict[str, Any]] = {}
    for candidate in _all_image_candidates(package):
        if not isinstance(candidate, dict):
            continue
        for key in _image_candidate_keys(candidate):
            lookup.setdefault(key, candidate)
    for group in _all_image_group_candidates(package):
        if not isinstance(group, dict):
            continue
        for member in group.get("members") or []:
            if not isinstance(member, dict):
                continue
            for key in _image_candidate_keys(member):
                lookup.setdefault(key, member)
    return lookup


def _candidate_for_image_ref(
    block: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    for key in _image_ref_keys(block):
        candidate = lookup.get(key)
        if candidate:
            return candidate
    return None


def _image_candidate_keys(candidate: dict[str, Any]) -> list[str]:
    keys = [
        _lookup_key("image_id", candidate.get("image_id")),
        _lookup_key("image_id", _legacy_image_id(candidate.get("image_id"))),
        _lookup_key("image_asset_id", candidate.get("image_asset_id")),
        _lookup_key("canonical_image_id", candidate.get("canonical_image_id")),
        _lookup_key("sha256", candidate.get("sha256")),
        _lookup_key("perceptual_hash", candidate.get("perceptual_hash")),
        _lookup_key("part_name", candidate.get("part_name")),
        _lookup_key("target", candidate.get("target")),
    ]
    return [key for key in keys if key]


def _image_ref_keys(block: dict[str, Any]) -> list[str]:
    part_name = block.get("source_part_name") or block.get("part_name")
    keys = [
        _lookup_key("image_id", block.get("image_id")),
        _lookup_key("image_id", _legacy_image_id(block.get("image_id"))),
        _lookup_key("image_asset_id", block.get("image_asset_id")),
        _lookup_key("canonical_image_id", block.get("canonical_image_id")),
        _lookup_key("sha256", block.get("sha256")),
        _lookup_key("perceptual_hash", block.get("perceptual_hash")),
        _lookup_key("part_name", part_name),
        _lookup_key("target", block.get("target")),
    ]
    return [key for key in keys if key]


def _existing_image_keys(
    sections: list[Any],
    lookup: dict[str, dict[str, Any]],
) -> set[str]:
    keys: set[str] = set()
    for block in _iter_blocks(sections):
        if block.get("type") != "image_ref":
            continue
        candidate = _candidate_for_image_ref(block, lookup)
        if candidate:
            keys.update(_stable_image_keys(candidate))
        keys.update(_image_ref_keys(block))
    return keys


def _stable_image_keys(candidate: dict[str, Any]) -> set[str]:
    return {
        key
        for key in [
            _lookup_key("image_asset_id", candidate.get("image_asset_id")),
            _lookup_key("canonical_image_id", candidate.get("canonical_image_id")),
            _lookup_key("sha256", candidate.get("sha256")),
            _lookup_key("perceptual_hash", candidate.get("perceptual_hash")),
            _lookup_key("image_id", candidate.get("image_id")),
            _lookup_key("image_id", _legacy_image_id(candidate.get("image_id"))),
            _lookup_key("part_name", candidate.get("part_name")),
            _lookup_key("target", candidate.get("target")),
        ]
        if key
    }


def _stable_image_group_keys(group: dict[str, Any]) -> set[str]:
    keys = {_lookup_key("image_group_id", group.get("image_group_id"))}
    for member in group.get("members") or []:
        if isinstance(member, dict):
            keys.update(_stable_image_keys(member))
    return {key for key in keys if key}


def _existing_image_semantic_keys(sections: list[Any]) -> set[str]:
    keys: set[str] = set()
    for block in _iter_blocks(sections):
        if block.get("type") != "image_ref":
            continue
        key = _semantic_dedupe_key(block)
        if key:
            keys.add(key)
    return keys


def _existing_group_material_ids(sections: list[Any]) -> set[str]:
    material_ids: set[str] = set()
    for block in _iter_blocks(sections):
        if block.get("type") != "image_ref":
            continue
        if not block.get("image_group_id"):
            continue
        material_id = str(block.get("material_slice_id") or "")
        if material_id:
            material_ids.add(material_id)
    return material_ids


def _lookup_key(kind: str, value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return f"{kind}:{text}"


def _legacy_image_id(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    prefix = "EBIMG_SRC0001_SRC0001_M"
    if prefix in text:
        return text.replace(prefix, "EBIMG_SRC0001_M", 1)
    return None


def _rank_auto_image_candidates(
    candidates: list[dict[str, Any]],
    sections: list[Any],
    existing_ids: set[str],
) -> list[dict[str, Any]]:
    section_text = " ".join(str(section.get("heading") or "") for section in sections if isinstance(section, dict))
    scored: list[tuple[int, int, dict[str, Any]]] = []
    material_counts: dict[str, int] = {}
    semantic_counts: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        image_id = str(candidate.get("image_id") or "")
        if image_id in existing_ids or (_stable_image_keys(candidate) & existing_ids):
            continue
        if not _has_topic_match(candidate, section_text):
            continue
        material_id = str(candidate.get("material_slice_id") or "")
        material_counts[material_id] = material_counts.get(material_id, 0) + 1
        score = _text_match_score(_candidate_match_text(candidate), section_text)
        semantic_key = _semantic_dedupe_key(candidate)
        if semantic_key:
            semantic_counts[semantic_key] = semantic_counts.get(semantic_key, 0) + 1
            score -= max(0, semantic_counts[semantic_key] - 1) * 3
        confidence = float(candidate.get("semantic_confidence") or 0)
        if confidence >= 0.8:
            score += 2
        elif 0 < confidence < 0.6:
            score -= 2
        if str(candidate.get("material_quality") or "") == "high":
            score += 2
        if material_counts[material_id] == 1:
            score += 1
        score += _text_image_block_candidate_priority(candidate)
        scored.append((-score, index, candidate))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [candidate for _, _, candidate in scored]


def _apply_auto_image_empty_section_coverage(
    sections: list[Any],
    candidates: list[dict[str, Any]],
    existing_keys: set[str],
    existing_group_material_ids: set[str],
    section_image_counts: dict[str, int],
    *,
    max_per_section: int,
    max_refs: int,
) -> tuple[int, int]:
    """优先补齐有明确匹配素材但尚未配图的工艺小节。"""

    inserted = 0
    skipped = 0
    for section in sections:
        if _count_blocks(sections, "image_ref") >= max_refs:
            break
        if not isinstance(section, dict):
            continue
        section_key = _section_key(section)
        if section_image_counts.get(section_key, 0) > 0:
            continue
        blocks = section.setdefault("blocks", [])
        if not isinstance(blocks, list):
            continue
        section_candidates = _rank_auto_image_candidates_for_section(
            candidates,
            section,
            existing_keys,
            existing_group_material_ids,
        )
        if not section_candidates:
            continue
        for candidate in section_candidates:
            if section_image_counts.get(section_key, 0) >= max_per_section:
                break
            image_id = str(candidate.get("image_id") or "")
            candidate_keys = _stable_image_keys(candidate)
            if not image_id or candidate_keys & existing_keys:
                continue
            inserted_here = _insert_image_ref_near_context(
                blocks,
                _auto_image_ref_block(candidate, section),
                candidate,
                section,
            )
            if not inserted_here:
                skipped += 1
                continue
            existing_keys.update(candidate_keys)
            section_image_counts[section_key] = section_image_counts.get(section_key, 0) + 1
            inserted += 1
            break
    return inserted, skipped


def _apply_auto_image_sparse_section_expansion(
    sections: list[Any],
    candidates: list[dict[str, Any]],
    existing_keys: set[str],
    existing_group_material_ids: set[str],
    section_image_counts: dict[str, int],
    *,
    max_per_section: int,
    max_refs: int,
    target_per_section: int,
    min_candidate_count: int,
) -> tuple[int, int]:
    """对候选图充足但已插图偏少的小节继续补图，避免工艺详稿视觉素材过薄。"""

    inserted = 0
    skipped = 0
    target_count = max(1, min(target_per_section, max_per_section))
    for section in sections:
        if _count_blocks(sections, "image_ref") >= max_refs:
            break
        if not isinstance(section, dict):
            continue
        section_key = _section_key(section)
        current_count = section_image_counts.get(section_key, 0)
        if current_count <= 0 or current_count >= target_count or current_count >= max_per_section:
            continue
        blocks = section.setdefault("blocks", [])
        if not isinstance(blocks, list):
            continue
        section_candidates = _rank_auto_image_candidates_for_section(
            candidates,
            section,
            existing_keys,
            existing_group_material_ids,
        )
        if len(section_candidates) < min_candidate_count:
            continue
        for candidate in section_candidates:
            if _count_blocks(sections, "image_ref") >= max_refs:
                break
            if section_image_counts.get(section_key, 0) >= target_count:
                break
            if section_image_counts.get(section_key, 0) >= max_per_section:
                break
            image_id = str(candidate.get("image_id") or "")
            candidate_keys = _stable_image_keys(candidate)
            if not image_id or candidate_keys & existing_keys:
                continue
            inserted_here = _insert_image_ref_near_context(
                blocks,
                _auto_image_ref_block(candidate, section),
                candidate,
                section,
            )
            if not inserted_here:
                skipped += 1
                continue
            existing_keys.update(candidate_keys)
            section_image_counts[section_key] = section_image_counts.get(section_key, 0) + 1
            inserted += 1
    return inserted, skipped


def _rank_auto_image_candidates_for_section(
    candidates: list[dict[str, Any]],
    section: dict[str, Any],
    existing_keys: set[str],
    existing_group_material_ids: set[str],
) -> list[dict[str, Any]]:
    scored: list[tuple[int, int, dict[str, Any]]] = []
    heading = str(section.get("heading") or "")
    if _is_general_analysis_section(heading):
        return []
    section_text = " ".join([heading, _section_block_text(section)])
    for index, candidate in enumerate(candidates):
        image_id = str(candidate.get("image_id") or "")
        if not image_id or _stable_image_keys(candidate) & existing_keys:
            continue
        material_id = str(candidate.get("material_slice_id") or "")
        if material_id and not candidate.get("image_group_id") and material_id in existing_group_material_ids:
            continue
        if not _has_topic_match(candidate, heading):
            continue
        if not _image_candidate_compatible_with_section(candidate, section):
            continue
        score = max(
            _text_match_score(_candidate_primary_text(candidate), heading),
            _text_match_score(_candidate_primary_text(candidate), section_text),
            _text_match_score(_candidate_match_text(candidate), heading),
            _text_match_score(_candidate_match_text(candidate), section_text),
        )
        if not _has_required_section_specific_terms(candidate, heading):
            score -= 8
        if score <= 0:
            continue
        if not _can_cover_empty_section(candidate, section):
            continue
        confidence = float(candidate.get("semantic_confidence") or 0)
        if confidence >= 0.8:
            score += 2
        if str(candidate.get("material_quality") or "") == "high":
            score += 2
        if str(candidate.get("risk_level") or "") == "low":
            score += 1
        scored.append((-score, index, candidate))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [candidate for _, _, candidate in scored]


def _can_cover_empty_section(candidate: dict[str, Any], section: dict[str, Any]) -> bool:
    if not _has_required_section_specific_terms(candidate, str(section.get("heading") or "")):
        return False
    if _requires_specific_anchor(candidate):
        return _has_section_specific_context_match(candidate, section)
    return _has_section_topic_match(candidate, section)


def _has_required_section_specific_terms(candidate: dict[str, Any], heading: str) -> bool:
    heading_topics = _primary_topics(heading)
    if not heading_topics:
        return True
    text = _candidate_match_text(candidate)
    candidate_topics = _candidate_primary_topics(candidate)
    for topic, excluded_terms in _SECTION_TOPIC_EXCLUSION_TERMS.items():
        if topic in heading_topics and topic not in candidate_topics and any(term in text for term in excluded_terms):
            return False
    for topic, required_terms in _SECTION_SPECIFIC_REQUIRED_TERMS.items():
        if topic not in heading_topics:
            continue
        heading_requires_detail = any(term in heading for term in required_terms)
        if heading_requires_detail and not any(term in text for term in required_terms):
            return False
    return True


def _image_candidate_compatible_with_section(candidate: dict[str, Any], section: dict[str, Any]) -> bool:
    """判断图片是否允许进入当前小节。

    这是图片语义治理的硬门禁：先判断章节类型和图片主题是否兼容，再让文本相似度参与排序。
    """

    heading = str(section.get("heading") or "")
    section_text = " ".join([heading, _section_block_text(section)])
    candidate_text = _candidate_match_text(candidate)
    candidate_topics = _candidate_primary_topics(candidate)
    if not candidate_topics and candidate.get("members"):
        candidate_topics = _primary_topics(
            " ".join(_candidate_match_text(member) for member in candidate.get("members") or [] if isinstance(member, dict))
        )
    if not _single_image_has_section_topic_evidence(candidate, heading, candidate_topics):
        return False
    if _is_basis_section(heading):
        return not candidate_topics and not _contains_process_terms(candidate_text)
    if _is_general_analysis_section(heading):
        return not candidate_topics
    if _is_management_context_section(heading):
        return _candidate_is_management_image(candidate) and not _candidate_is_construction_process_image(candidate)
    if _is_deployment_context_section(heading):
        return _candidate_is_deployment_image(candidate) and not _candidate_is_specific_process_image(candidate)
    heading_topics = _primary_topics(heading)
    section_topics = heading_topics or _primary_topics(section_text)
    if not _candidate_subtopic_allows_heading(candidate, heading):
        return False
    if "电梯" in section_topics:
        return "电梯" in candidate_topics and _has_required_section_specific_terms(candidate, heading)
    if not section_topics:
        return bool(candidate_topics) and _candidate_bound_section_allows_heading(candidate, heading)
    specific_section_topics = _specific_process_topics(section_topics)
    if specific_section_topics and not (_specific_process_topics(candidate_topics) & specific_section_topics):
        return False
    if not (candidate_topics & section_topics):
        return False
    if not _candidate_bound_section_allows_heading(candidate, heading):
        return False
    return _has_required_section_specific_terms(candidate, heading)


def _single_image_has_section_topic_evidence(
    candidate: dict[str, Any],
    heading: str,
    candidate_topics: set[str],
) -> bool:
    """普通散图必须有当前小节主题证据；强图文块和套图成员沿用高置信来源。"""

    if candidate.get("members") or candidate.get("image_group_id") or candidate.get("must_keep_with_group"):
        return True
    if _text_image_block_candidate_priority(candidate) > 0:
        return True
    heading_topics = _specific_process_topics(_primary_topics(heading))
    if not heading_topics:
        return True
    if not (candidate_topics & heading_topics):
        return False
    if _has_strong_image_self_topic_evidence(candidate, heading_topics):
        return True
    return not _ordinary_single_image_is_weakly_described(candidate)


def _has_strong_image_self_topic_evidence(candidate: dict[str, Any], required_topics: set[str]) -> bool:
    primary_text = " ".join(
        str(part)
        for part in [
            _best_image_semantic_text(candidate),
            candidate.get("semantic_text"),
            candidate.get("caption"),
            candidate.get("group_semantic_text"),
            candidate.get("group_title"),
            " ".join(str(item) for item in candidate.get("caption_candidates") or []),
        ]
        if part
    )
    return bool(_primary_topics(primary_text) & required_topics) and not _is_weak_image_semantic_text(primary_text)


def _ordinary_single_image_is_weakly_described(candidate: dict[str, Any]) -> bool:
    evidence_texts = [
        _best_image_semantic_text(candidate),
        candidate.get("semantic_text"),
        candidate.get("caption"),
        " ".join(str(item) for item in candidate.get("caption_candidates") or []),
    ]
    cleaned = [str(text).strip() for text in evidence_texts if str(text or "").strip()]
    if not cleaned:
        return True
    return all(_is_weak_image_semantic_text(text) for text in cleaned)


def _candidate_bound_section_allows_heading(candidate: dict[str, Any], heading: str) -> bool:
    heading_key = _semantic_compare_key(heading)
    if not heading_key:
        return True
    heading_topics = _primary_topics(heading)
    candidate_topics = _candidate_primary_topics(candidate)
    if heading_topics and candidate_topics & heading_topics and "进度计划" not in (heading_topics | candidate_topics):
        return True
    bound_keys = [_semantic_compare_key(candidate.get("bound_section"))]
    specific_bound_keys = [key for key in bound_keys if len(key) >= 6]
    if not specific_bound_keys:
        return True
    return any(key == heading_key or key in heading_key or heading_key in key for key in specific_bound_keys)


def _candidate_subtopic_allows_heading(candidate: dict[str, Any], heading: str) -> bool:
    strict_heading_subtopics = _strict_subtopic_terms(heading)
    if not strict_heading_subtopics:
        return True
    candidate_subtopics = _candidate_specific_subtopic_terms(candidate)
    if not candidate_subtopics:
        return False
    return bool(strict_heading_subtopics & candidate_subtopics)


def _strict_subtopic_terms(text: Any) -> set[str]:
    return _subtopic_terms(text) & _STRICT_SUBTOPIC_NAMES


def _specific_process_topics(topics: set[str]) -> set[str]:
    return topics & _SPECIFIC_PROCESS_TOPIC_NAMES


def _candidate_specific_subtopic_terms(candidate: dict[str, Any]) -> set[str]:
    primary_parts = [
        candidate.get("semantic_text"),
        candidate.get("caption"),
        candidate.get("group_semantic_text"),
        candidate.get("group_title"),
        " ".join(str(item) for item in candidate.get("caption_candidates") or []),
    ]
    primary_terms = _subtopic_terms(" ".join(str(part) for part in primary_parts if part))
    if primary_terms:
        return primary_terms
    fallback_parts: list[Any] = []
    for item in candidate.get("semantic_sources") or []:
        if not isinstance(item, dict):
            continue
        source_type = str(item.get("source_type") or "")
        confidence = float(item.get("confidence") or 0)
        if source_type.startswith("previous_row_") or source_type in {"section_heading", "section_path"}:
            continue
        if confidence >= 0.7:
            fallback_parts.append(item.get("text"))
    return _subtopic_terms(" ".join(str(part) for part in fallback_parts if part))


def _is_basis_section(text: str) -> bool:
    value = re.sub(r"\s+", "", str(text or ""))
    if "进度" in value or "计划" in value or "部署" in value:
        return False
    return value in _BASIS_SECTION_TERMS or value.startswith("编制依据")


def _is_management_context_section(text: str) -> bool:
    value = re.sub(r"\s+", "", str(text or ""))
    return any(term in value for term in _MANAGEMENT_CONTEXT_TERMS)


def _is_deployment_context_section(text: str) -> bool:
    value = re.sub(r"\s+", "", str(text or ""))
    return any(term in value for term in _DEPLOYMENT_CONTEXT_TERMS)


def _candidate_is_management_image(candidate: dict[str, Any]) -> bool:
    text = _candidate_match_text(candidate)
    return any(term in text for term in _MANAGEMENT_IMAGE_TERMS)


def _candidate_is_deployment_image(candidate: dict[str, Any]) -> bool:
    text = _candidate_match_text(candidate)
    return any(term in text for term in _DEPLOYMENT_IMAGE_TERMS)


def _candidate_is_construction_process_image(candidate: dict[str, Any]) -> bool:
    return bool(_candidate_primary_topics(candidate) & {
        "测量",
        "土方基坑",
        "钢筋",
        "模板",
        "混凝土",
        "防水",
        "脚手架",
        "砌体",
        "后浇带变形缝",
        "电梯",
    })


def _candidate_is_specific_process_image(candidate: dict[str, Any]) -> bool:
    topics = _candidate_primary_topics(candidate)
    if not topics:
        return False
    return bool(topics - {"进度计划"})


def _contains_process_terms(text: str) -> bool:
    value = str(text or "")
    return any(
        term in value
        for topic, terms in _PRIMARY_TOPIC_TERMS.items()
        if topic not in {"进度计划"}
        for term in terms
    )


def _apply_auto_image_group_reuse(
    output: dict[str, Any],
    package: dict[str, Any],
    groups: list[dict[str, Any]],
) -> tuple[int, int]:
    sections = output.get("sections")
    if not isinstance(sections, list) or not sections or not groups:
        return 0, 0
    policy = package.get("auto_image_reuse_policy") or {}
    max_per_section = int(policy.get("max_images_per_section") or 4)
    candidate_lookup = _image_candidate_lookup(package)
    existing_keys = _existing_image_keys(sections, candidate_lookup)
    existing_semantic_keys = _existing_image_semantic_keys(sections)
    section_image_counts = _section_image_counts(sections)
    inserted = 0
    skipped = 0
    for group in _rank_auto_image_groups(groups, sections, existing_keys):
        _remove_same_material_single_images_for_group(sections, group)
        candidate_lookup = _image_candidate_lookup(package)
        existing_keys = _existing_image_keys(sections, candidate_lookup)
        existing_semantic_keys = _existing_image_semantic_keys(sections)
        section_image_counts = _section_image_counts(sections)
        group_keys = _stable_image_group_keys(group)
        if group_keys & existing_keys:
            continue
        semantic_key = _semantic_dedupe_key(group)
        if semantic_key and semantic_key in existing_semantic_keys:
            continue
        section = _best_section_for_image(group, sections, section_image_counts, max_per_section=max_per_section)
        if not isinstance(section, dict):
            skipped += 1
            continue
        group_equivalence_key = _image_group_candidate_equivalence_key(group)
        if group_equivalence_key and group_equivalence_key in _existing_section_group_equivalence_keys(section):
            skipped += 1
            continue
        blocks = section.setdefault("blocks", [])
        if not isinstance(blocks, list):
            continue
        group_blocks = _auto_image_group_ref_blocks(group, section)
        if not group_blocks:
            continue
        section_key = _section_key(section)
        section_image_counts = _section_image_counts(sections)
        current_count = section_image_counts.get(section_key, 0)
        if current_count + len(group_blocks) > max_per_section:
            if current_count > 0:
                skipped += 1
                continue
            if not _should_allow_complete_group_over_section_limit(group, section):
                skipped += 1
                continue
            if len(group_blocks) > _max_complete_group_images_for_empty_section(group, max_per_section):
                skipped += 1
                continue
        if not _insert_image_group_near_context(blocks, group_blocks, group, section):
            skipped += 1
            continue
        existing_keys.update(group_keys)
        if semantic_key:
            existing_semantic_keys.add(semantic_key)
        section_image_counts[section_key] = section_image_counts.get(section_key, 0) + len(group_blocks)
        inserted += 1
    return inserted, skipped


def _remove_same_material_single_images_for_group(sections: list[Any], group: dict[str, Any]) -> None:
    """准备插入套图前，先移除同素材散图，避免散图占用小节容量。"""

    group_material_id = str(group.get("material_slice_id") or "")
    if not group_material_id:
        return
    for section in sections:
        if not isinstance(section, dict):
            continue
        blocks = section.get("blocks")
        if not isinstance(blocks, list):
            continue
        kept: list[Any] = []
        for block in blocks:
            if (
                isinstance(block, dict)
                and block.get("type") == "image_ref"
                and not block.get("image_group_id")
                and str(block.get("material_slice_id") or "") == group_material_id
            ):
                continue
            kept.append(block)
        section["blocks"] = kept


def _should_allow_complete_group_over_section_limit(group: dict[str, Any], section: dict[str, Any]) -> bool:
    members = [member for member in group.get("members") or [] if isinstance(member, dict)]
    if len(members) < 2:
        return False
    if not bool(group.get("must_keep_together", True)):
        return False
    if not _has_topic_match(group, str(section.get("heading") or "")):
        return False
    if not _has_required_section_specific_terms(group, str(section.get("heading") or "")):
        return False
    score = max(
        _text_match_score(_candidate_primary_text(group), str(section.get("heading") or "")),
        _text_match_score(_candidate_match_text(group), " ".join([str(section.get("heading") or ""), _section_block_text(section)])),
    )
    return score > 0


def _max_complete_group_images_for_empty_section(group: dict[str, Any], max_per_section: int) -> int:
    member_count = int(group.get("member_count") or len(group.get("members") or []) or 0)
    if member_count <= 0:
        return max_per_section
    return max(max_per_section, 12)


def _rank_auto_image_groups(
    groups: list[dict[str, Any]],
    sections: list[Any],
    existing_keys: set[str],
) -> list[dict[str, Any]]:
    section_text = " ".join(str(section.get("heading") or "") for section in sections if isinstance(section, dict))
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, group in enumerate(groups):
        if _stable_image_group_keys(group) & existing_keys:
            continue
        if not _has_topic_match(group, section_text):
            continue
        score = _text_match_score(_candidate_match_text(group), section_text)
        confidence = float(group.get("semantic_confidence") or 0)
        if confidence >= 0.8:
            score += 2
        if int(group.get("member_count") or 0) >= 4:
            score += 1
        if str(group.get("material_quality") or "") == "high":
            score += 2
        score += _text_image_block_candidate_priority(group)
        scored.append((-score, index, group))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [group for _, _, group in scored]


def _best_section_for_image(
    candidate: dict[str, Any],
    sections: list[Any],
    section_image_counts: dict[str, int] | None = None,
    *,
    max_per_section: int = 4,
) -> dict[str, Any] | None:
    best_section: dict[str, Any] | None = None
    best_score = -1
    candidate_text = _candidate_primary_text(candidate) or _candidate_match_text(candidate)
    for section in sections:
        if not isinstance(section, dict):
            continue
        section_key = _section_key(section)
        current_count = (section_image_counts or {}).get(section_key, 0)
        if current_count >= max_per_section and not candidate.get("image_group_id"):
            continue
        heading = str(section.get("heading") or "")
        if _is_general_analysis_section(heading):
            continue
        if not _image_candidate_compatible_with_section(candidate, section):
            continue
        if not _has_topic_match(candidate, heading):
            continue
        if not _has_required_section_specific_terms(candidate, heading):
            continue
        section_text = " ".join([heading, _section_block_text(section)])
        score = max(
            _text_match_score(candidate_text, heading),
            _text_match_score(candidate_text, section_text),
        )
        if score <= 0 and (_candidate_primary_topics(candidate) & _primary_topics(section_text)):
            score = 2
        score += _section_heading_specificity_bonus(candidate, heading)
        score += _section_subtopic_match_bonus(candidate, heading)
        if candidate.get("image_group_id") and current_count <= 0:
            score += 8
        score += _text_image_block_candidate_priority(candidate)
        score -= current_count * 6
        if score > best_score:
            best_score = score
            best_section = section
    return best_section if best_score > 0 else None


def _section_heading_specificity_bonus(candidate: dict[str, Any], heading: str) -> int:
    heading_key = _semantic_compare_key(heading)
    candidate_keys = {
        _semantic_compare_key(part)
        for part in [
            _best_image_semantic_text(candidate),
            candidate.get("semantic_text"),
            candidate.get("bound_section"),
            candidate.get("caption"),
        ]
        if part
    }
    if heading_key and heading_key in candidate_keys:
        return 80
    if heading_key and any(heading_key in key or key in heading_key for key in candidate_keys if len(key) >= 6):
        return 40
    candidate_terms = _specific_detail_terms(
        " ".join(
            str(part)
            for part in [
                _best_image_semantic_text(candidate),
                candidate.get("semantic_text"),
                candidate.get("bound_section"),
                candidate.get("caption"),
            ]
            if part
        )
    )
    heading_terms = _specific_detail_terms(heading)
    if not candidate_terms or not heading_terms:
        return 0
    return len(candidate_terms & heading_terms) * 12


def _section_subtopic_match_bonus(candidate: dict[str, Any], heading: str) -> int:
    heading_terms = _subtopic_terms(heading)
    if not heading_terms:
        return 0
    candidate_terms = _candidate_specific_subtopic_terms(candidate)
    matched = heading_terms & candidate_terms
    if matched:
        return len(matched) * 40
    if _primary_topics(heading) & _candidate_primary_topics(candidate):
        return 2
    return 0


def _subtopic_terms(text: Any) -> set[str]:
    value = str(text or "")
    groups = {
        "临边洞口": ["临边", "洞口", "防护栏杆", "楼层边", "预留洞"],
        "临时用电": ["临时用电", "施工用电", "配电箱", "开关箱", "TN-S", "漏电保护", "三级配电"],
        "消防": ["消防", "灭火器", "消防泵", "消防管", "动火"],
        "机械塔吊": ["机械", "塔吊", "起重", "吊装", "设备"],
        "个人防护": ["安全帽", "安全带", "防护用品", "劳保"],
        "噪声": ["噪声", "噪音", "声屏障", "扰民"],
        "水污染": ["水污染", "污水", "沉淀池", "洗车槽", "排水", "废水"],
        "光污染": ["光污染", "照明", "眩光"],
        "固废": ["固体废弃物", "垃圾", "危废", "分类"],
        "扬尘大气": ["扬尘", "大气", "雾炮", "喷淋", "降尘", "围挡"],
        "绿色节能": ["绿色", "节能", "四节一环保", "节水", "节材", "节地"],
    }
    return {name for name, terms in groups.items() if any(term in value for term in terms)}


def _semantic_compare_key(value: Any) -> str:
    return re.sub(r"[\s，,。.;；：:（）()【】\[\]、]+", "", str(value or ""))


def _auto_image_ref_block(candidate: dict[str, Any], section: dict[str, Any]) -> dict[str, Any]:
    caption = _auto_image_caption(candidate, section)
    return {
        "type": "image_ref",
        "image_id": candidate.get("image_id"),
        "image_asset_id": candidate.get("image_asset_id"),
        "canonical_image_id": candidate.get("canonical_image_id"),
        "sha256": candidate.get("sha256"),
        "perceptual_hash": candidate.get("perceptual_hash"),
        "caption": caption,
        "source_part_name": candidate.get("part_name"),
        "material_slice_id": candidate.get("material_slice_id"),
        "source_bid_id": candidate.get("source_bid_id") or candidate.get("source_id"),
        "source_id": candidate.get("source_id"),
        "source_slice_id": candidate.get("source_slice_id"),
        "bound_table_id": candidate.get("bound_table_id"),
        "bound_row_id": candidate.get("bound_row_id"),
        "bound_cell_key": candidate.get("bound_cell_key"),
        "image_group_id": candidate.get("image_group_id"),
        "group_title": candidate.get("group_title"),
        "group_semantic_text": candidate.get("group_semantic_text"),
        "group_member_index": candidate.get("group_member_index"),
        "group_member_count": candidate.get("group_member_count"),
        "must_keep_with_group": bool(candidate.get("must_keep_with_group")),
        "semantic_text": candidate.get("semantic_text"),
        "semantic_confidence": candidate.get("semantic_confidence"),
        "semantic_sources": candidate.get("semantic_sources") or [],
        "caption_candidates": candidate.get("caption_candidates") or [],
        "auto_inserted": True,
        "reuse_level": candidate.get("reuse_level"),
        "use_policy": candidate.get("use_policy"),
        "render_policy": candidate.get("render_policy") or {},
        "row_scope": candidate.get("row_scope") or {},
        "source_reuse_mode": candidate.get("source_reuse_mode"),
        "text_image_block_id": candidate.get("text_image_block_id"),
        "text_image_block_title": candidate.get("text_image_block_title"),
        "text_image_block_match_level": candidate.get("text_image_block_match_level"),
        "text_image_block_match_confidence": candidate.get("text_image_block_match_confidence"),
    }


def _auto_image_group_ref_blocks(group: dict[str, Any], section: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    group_id = group.get("image_group_id")
    group_title = _auto_image_caption(group, section)
    member_count = int(group.get("member_count") or len(group.get("members") or []) or 0)
    for index, member in enumerate(group.get("members") or [], start=1):
        if not isinstance(member, dict):
            continue
        block = _auto_image_ref_block(member, section)
        block["image_group_id"] = group_id
        block["group_title"] = group.get("group_title") or group_title
        block["group_semantic_text"] = group.get("semantic_text")
        block["group_member_index"] = index
        block["group_member_count"] = member_count
        block["must_keep_with_group"] = True
        block["auto_inserted_group"] = True
        caption = _group_member_caption(group, member, index)
        if caption:
            block["caption"] = caption
        blocks.append(block)
    return blocks


def _group_member_caption(group: dict[str, Any], member: dict[str, Any], index: int) -> str:
    caption = _trusted_image_caption(member)
    if caption:
        return caption
    captions = group.get("captions") or []
    if index - 1 < len(captions):
        return str(captions[index - 1] or "")
    group_title = str(group.get("group_title") or group.get("caption") or "施工做法套图").strip()
    return f"{group_title}（{index}）"


def _insert_image_ref_near_context(
    blocks: list[Any],
    image_block: dict[str, Any],
    candidate: dict[str, Any],
    section: dict[str, Any],
) -> bool:
    insert_at = _image_insert_index(blocks, candidate, section)
    if insert_at is None:
        return False
    blocks.insert(insert_at, image_block)
    return True


def _insert_image_group_near_context(
    blocks: list[Any],
    image_blocks: list[dict[str, Any]],
    group: dict[str, Any],
    section: dict[str, Any],
) -> bool:
    insert_at = _image_insert_index(blocks, group, section)
    if insert_at is None:
        return False
    for offset, block in enumerate(image_blocks):
        blocks.insert(insert_at + offset, block)
    return True


def _image_insert_index(
    blocks: list[Any],
    candidate: dict[str, Any],
    section: dict[str, Any],
) -> int | None:
    table_index = _best_table_anchor_index(blocks, candidate)
    if table_index is not None:
        return _after_existing_images(blocks, table_index + 1)
    paragraph_index = _best_paragraph_anchor_index(blocks, candidate, section)
    if paragraph_index is not None:
        return _after_existing_images(blocks, paragraph_index + 1)
    if _requires_specific_anchor(candidate) and not _has_section_specific_context_match(candidate, section):
        return None
    return len(blocks)


def _best_table_anchor_index(blocks: list[Any], candidate: dict[str, Any]) -> int | None:
    table_id = str(candidate.get("bound_table_id") or "")
    if table_id:
        for index, block in enumerate(blocks):
            if not isinstance(block, dict) or block.get("type") != "rich_table":
                continue
            if str(block.get("table_id") or "") == table_id:
                return index
    return None


def _best_paragraph_anchor_index(
    blocks: list[Any],
    candidate: dict[str, Any],
    section: dict[str, Any],
) -> int | None:
    candidate_text = _candidate_primary_text(candidate) or _candidate_match_text(candidate)
    section_topics = _primary_topics(str(section.get("heading") or ""))
    best_index: int | None = None
    best_score = -1
    for index, block in enumerate(blocks):
        if not isinstance(block, dict) or block.get("type") != "paragraph":
            continue
        anchor_image_count = _image_count_after_anchor(blocks, index)
        if anchor_image_count >= _max_images_per_anchor(candidate):
            continue
        block_text = _block_match_text(block)
        if section_topics and not (_primary_topics(block_text) & section_topics):
            continue
        if _requires_specific_anchor(candidate) and not _has_specific_semantic_match(candidate, block_text):
            continue
        score = _text_match_score(candidate_text, block_text) - anchor_image_count * 2
        if score > best_score:
            best_score = score
            best_index = index
    if best_index is not None and best_score > 0:
        return best_index
    return None


def _least_used_table_anchor_index(blocks: list[Any]) -> int | None:
    indexes = [
        index
        for index, block in enumerate(blocks)
        if isinstance(block, dict) and block.get("type") == "rich_table"
    ]
    if not indexes:
        return None
    return min(indexes, key=lambda index: _image_count_after_anchor(blocks, index))


def _after_existing_images(blocks: list[Any], index: int) -> int:
    while index < len(blocks) and isinstance(blocks[index], dict) and blocks[index].get("type") == "image_ref":
        index += 1
    return index


def _image_count_after_anchor(blocks: list[Any], anchor_index: int) -> int:
    count = 0
    cursor = anchor_index + 1
    while cursor < len(blocks):
        block = blocks[cursor]
        if isinstance(block, dict) and block.get("type") == "image_ref":
            count += 1
            cursor += 1
            continue
        break
    return count


def _max_images_per_anchor(candidate: dict[str, Any]) -> int:
    if _requires_specific_anchor(candidate):
        return 3
    return 2


def _block_match_text(block: dict[str, Any]) -> str:
    parts = [block.get("title"), block.get("caption"), block.get("text")]
    columns = block.get("columns") or []
    for column in columns:
        if isinstance(column, dict):
            parts.append(column.get("title"))
    rows = block.get("rows") or []
    for row in rows[:3]:
        if not isinstance(row, dict):
            continue
        cells = row.get("cells") or {}
        if isinstance(cells, dict):
            parts.extend(str(value) for value in cells.values())
    return " ".join(str(part) for part in parts if part)


def _section_block_text(section: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in section.get("blocks") or []:
        if isinstance(block, dict):
            parts.append(_block_match_text(block))
    return " ".join(part for part in parts if part)


def _auto_image_caption(candidate: dict[str, Any], section: dict[str, Any]) -> str:
    base = _trusted_image_caption(candidate) or _best_image_semantic_text(candidate) or str(candidate.get("caption") or "").strip()
    base = _strip_heading_number(base) or str(section.get("heading") or "").strip() or "施工做法"
    if any(word in base for word in ["图", "照片", "示意"]):
        return base
    return base


def _clean_image_caption(current: str, candidate: dict[str, Any], section_heading: str) -> str:
    current = _normalize_caption_text(current)
    if current and _usable_caption_phrase(current):
        return _formalize_image_caption(current, section_heading)
    for source in _caption_rewrite_sources(candidate, section_heading):
        text = _usable_caption_phrase(source)
        if not text:
            continue
        return _formalize_image_caption(text, section_heading)
    if current and _is_non_inventable_weak_image_caption(current):
        return ""
    return _fallback_caption_from_context(section_heading, candidate)


def _caption_rewrite_sources(candidate: dict[str, Any], section_heading: str) -> list[str]:
    sources: list[str] = []
    sources.append(str(candidate.get("group_semantic_text") or ""))
    sources.append(str(candidate.get("group_title") or ""))
    sources.append(_trusted_image_caption(candidate))
    sources.append(str(candidate.get("semantic_text") or ""))
    sources.append(_best_image_semantic_text(candidate))
    for item in candidate.get("semantic_sources") or []:
        if isinstance(item, dict):
            sources.append(str(item.get("text") or ""))
    sources.extend(str(item) for item in candidate.get("caption_candidates") or [])
    sources.append(str(candidate.get("bound_section") or ""))
    source_path = [str(part) for part in candidate.get("source_section_path") or [] if str(part).strip()]
    if source_path:
        sources.append(source_path[-1])
    return sources


def _is_non_inventable_weak_image_caption(text: str) -> bool:
    value = _normalize_caption_text(text)
    return value in {
        "序号",
        "项目",
        "名称",
        "原因",
        "内容",
        "措施",
        "具体措施",
        "做法说明",
        "约束条件",
        "水泥水化热",
    }


def _normalize_caption_text(text: str) -> str:
    value = re.sub(r"\s+", "", str(text or ""))
    value = value.strip("：:.-—_，,。.;；|、>＞/\\")
    if re.search(r"[>＞/\\]", value):
        parts = [part.strip("：:.-—_，,。.;；|、>＞/\\") for part in re.split(r"[>＞/\\]+", value)]
        parts = [part for part in parts if part]
        value = parts[-1] if parts else value
    value = re.sub(r"^\d+(?:\.\d+)*[.．、]?", "", value)
    value = re.sub(r"^\d+(?=[\u4e00-\u9fff])", "", value)
    value = re.sub(r"^(序号|编号)[；;|、:：]+", "", value)
    value = value.strip("：:.-—_，,。.;；|、>＞/\\")
    return _trim_caption_text(value)


def _trim_caption_text(value: str) -> str:
    if len(value) <= 70:
        return value
    for left, right in [("（", "）"), ("(", ")")]:
        open_index = value.find(left)
        close_index = value.rfind(right)
        if 0 <= open_index < close_index:
            prefix = value[:open_index]
            suffix = value[open_index : close_index + 1]
            if len(prefix) <= 42 and len(suffix) <= 42:
                return f"{prefix}{suffix}"
    for term in ["示意图", "流程图", "布置图", "节点图", "大样图", "详图", "平面图", "立面图", "剖面图", "照片"]:
        index = value.find(term)
        if index >= 0 and index + len(term) <= 70:
            return value[: index + len(term)]
    return value[:70]


def _usable_caption_phrase(text: str) -> str:
    value = _normalize_caption_text(text)
    if not value or _should_rewrite_image_caption(text):
        return ""
    if _is_sentence_like_caption(value, text):
        return ""
    return value


def _should_rewrite_image_caption(text: str) -> bool:
    value = _normalize_caption_text(text)
    if _is_weak_image_semantic_text(value):
        return True
    weak_fragments = {
        "序号",
        "设计说明",
        "主要内容",
        "控制内容",
        "检查内容",
        "项目",
        "名称",
        "原因",
        "内容",
        "措施",
        "具体措施",
        "做法说明",
        "施工图示",
        "约束条件",
        "水泥水化热",
    }
    if value in weak_fragments:
        return True
    raw = str(text or "")
    if any(sep in raw for sep in [">", "＞", "|"]):
        return True
    if any(sep in raw for sep in ["；", ";", "|"]) and len(value) <= 14:
        return True
    if re.search(r"[Kk]\d+\s*[～~\-至]\s*[Kk]?\d+", value):
        return True
    if "点位" in value and re.search(r"\d", value):
        return True
    if _looks_like_section_heading_caption(value):
        return True
    if _is_sentence_like_caption(value, raw):
        return True
    if len(value) >= 28 and not any(term in value for term in ["示意", "做法", "节点", "流程", "构造", "布设", "搭设"]):
        return True
    if re.search(r"[。！？]", raw):
        return True
    return False


def _looks_like_section_heading_caption(value: str) -> bool:
    if len(value) < 10:
        return False
    if any(term in value for term in ["图", "照片", "示意", "做法", "节点", "流程", "布设", "布置", "构造", "搭设"]):
        return False
    return value.endswith(("方案", "措施", "技术", "专项", "要求", "体系"))


def _is_sentence_like_caption(value: str, raw_text: str) -> bool:
    raw = str(raw_text or "")
    if re.search(r"[，,。；;：:！？]", raw) and len(value) >= 12:
        return True
    sentence_markers = [
        "本工程",
        "结合",
        "现场情况",
        "综合以上",
        "拟",
        "应",
        "宜",
        "可用",
        "采用",
        "设置",
        "保证",
        "确保",
        "进行",
        "当",
        "若",
        "如",
        "较大",
        "击入",
        "高出",
        "不小于",
        "不得",
        "必须",
    ]
    return len(value) >= 16 and any(marker in value for marker in sentence_markers)


def _formalize_image_caption(text: str, section_heading: str) -> str:
    value = _normalize_caption_text(text)
    if not value:
        return _fallback_caption_from_section(section_heading)
    if _is_weak_image_semantic_text(value):
        return _fallback_caption_from_section(section_heading)
    if any(term in value for term in ["图", "照片", "示意", "做法"]):
        return value
    topics = _primary_topics(" ".join([section_heading, value]))
    if any(topic in topics for topic in {"测量", "土方基坑", "钢筋", "模板", "混凝土", "防水", "脚手架", "砌体", "后浇带变形缝"}):
        return f"{value}做法示意图"
    if "流程" in value:
        return f"{value}图"
    if any(term in value for term in ["布设", "布置", "构造", "节点"]):
        return f"{value}示意图"
    return f"{value}示意图"


def _fallback_caption_from_context(section_heading: str, candidate: dict[str, Any]) -> str:
    context_parts = [
        section_heading,
        candidate.get("caption"),
        candidate.get("semantic_text"),
        _best_image_semantic_text(candidate),
        candidate.get("group_semantic_text"),
        candidate.get("group_title"),
        candidate.get("bound_section"),
        " ".join(str(part) for part in candidate.get("source_section_path") or []),
    ]
    context = " ".join(str(part) for part in context_parts if part)
    if any(term in context for term in ["控制网", "测量", "内控点", "轴线", "标高"]):
        return "测量控制网布设示意图"
    if any(term in context for term in ["引水", "排水", "降水"]):
        return "基坑排水引水做法示意图"
    if any(term in context for term in ["基坑", "土方", "支护", "边坡"]):
        return "基坑支护及土方施工做法示意图"
    if any(term in context for term in ["钢筋", "箍筋", "套筒", "绑扎", "马凳筋", "梯子筋"]):
        if "加工" in context:
            return "钢筋加工做法示意图"
        if "绑扎" in context:
            return "钢筋绑扎做法示意图"
        return "钢筋工程做法示意图"
    if any(term in context for term in ["模板", "支模", "木方", "对拉螺栓"]):
        return "模板支设做法示意图"
    if any(term in context for term in ["混凝土", "浇筑", "振捣", "测温", "养护", "温控"]):
        return "混凝土浇筑及温控做法示意图"
    if any(term in context for term in ["防水", "卷材", "涂膜", "止水", "屋面", "地下室"]):
        return "防水节点做法示意图"
    if any(term in context for term in ["脚手架", "连墙件", "剪刀撑", "立杆", "横杆", "悬挑"]):
        return "脚手架搭设做法示意图"
    if any(term in context for term in ["砌体", "砌筑", "加气块", "构造柱", "拉结筋"]):
        return "砌体砌筑防裂做法示意图"
    if any(term in context for term in ["后浇带", "变形缝", "施工缝"]):
        return "后浇带及变形缝节点做法示意图"
    return _fallback_caption_from_section(section_heading)


def _fallback_caption_from_section(section_heading: str) -> str:
    value = _strip_heading_number(str(section_heading or "")).strip() or "施工做法"
    value = re.sub(r"(方案|措施|技术|专项)$", "", value).strip() or value
    if any(term in value for term in ["图", "示意", "流程", "做法"]):
        return value
    return f"{value}示意图"


def _trusted_image_caption(candidate: dict[str, Any]) -> str:
    text = _best_image_semantic_text(candidate)
    if not text:
        return str(candidate.get("caption") or "").strip()
    confidence = float(candidate.get("semantic_confidence") or 0)
    trusted_text = _usable_caption_phrase(text)
    if confidence >= 0.6 and trusted_text:
        return _strip_heading_number(trusted_text) or trusted_text
    caption = str(candidate.get("caption") or "").strip()
    trusted_caption = _usable_caption_phrase(caption)
    if trusted_caption:
        return _strip_heading_number(trusted_caption) or trusted_caption
    return ""


def _candidate_match_text(candidate: dict[str, Any]) -> str:
    parts = [
        candidate.get("semantic_text"),
        " ".join(str(item.get("text") or "") for item in candidate.get("semantic_sources") or [] if isinstance(item, dict)),
        candidate.get("caption"),
        candidate.get("group_title"),
        candidate.get("group_semantic_text"),
        " ".join(str(item) for item in candidate.get("caption_candidates") or []),
        candidate.get("nearby_text"),
        " ".join(str(item) for item in candidate.get("tags") or []),
        candidate.get("bound_section"),
        " ".join(str(part) for part in candidate.get("source_section_path") or []),
        candidate.get("material_slice_id"),
        " ".join(_candidate_match_text(member) for member in candidate.get("members") or [] if isinstance(member, dict)),
    ]
    return " ".join(str(part) for part in parts if part)


def _best_image_semantic_text(candidate: dict[str, Any]) -> str:
    sources = [
        item
        for item in candidate.get("semantic_sources") or []
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    if sources:
        best = max(sources, key=lambda item: float(item.get("confidence") or 0))
        return str(best.get("text") or "").strip()
    return str(candidate.get("semantic_text") or "").strip()


def _candidate_primary_topics(candidate: dict[str, Any]) -> set[str]:
    semantic_topics = _primary_topics(_best_image_semantic_text(candidate))
    if semantic_topics:
        return semantic_topics
    source_topics = _primary_topics(" ".join(str(part) for part in candidate.get("source_section_path") or []))
    if source_topics:
        return source_topics
    title_topics = _primary_topics(
        " ".join(str(part) for part in [candidate.get("caption"), candidate.get("bound_section")] if part)
    )
    if title_topics:
        return title_topics
    context_topics = _primary_topics(
        " ".join(str(item) for item in candidate.get("caption_candidates") or [])
    )
    if context_topics:
        return context_topics
    tag_topics = _primary_topics(" ".join(str(item) for item in candidate.get("tags") or []))
    if tag_topics:
        return tag_topics
    return _primary_topics(
        " ".join(
            str(item)
            for item in [
                candidate.get("primary_category"),
                " ".join(str(tag) for tag in candidate.get("discipline_tags") or []),
                " ".join(str(tag) for tag in candidate.get("scene_tags") or []),
                " ".join(str(reason) for reason in candidate.get("fit_reasons") or []),
            ]
            if item
        )
    )


def _candidate_primary_text(candidate: dict[str, Any]) -> str:
    parts = [
        _best_image_semantic_text(candidate),
        candidate.get("semantic_text"),
        candidate.get("caption"),
        " ".join(str(item) for item in candidate.get("caption_candidates") or []),
        " ".join(str(item) for item in candidate.get("tags") or []),
        candidate.get("primary_category"),
        " ".join(str(tag) for tag in candidate.get("discipline_tags") or []),
        " ".join(str(tag) for tag in candidate.get("scene_tags") or []),
        " ".join(str(reason) for reason in candidate.get("fit_reasons") or []),
        candidate.get("bound_section"),
        " ".join(str(part) for part in candidate.get("source_section_path") or []),
    ]
    return " ".join(str(part) for part in parts if part)


def _semantic_dedupe_key(candidate: dict[str, Any]) -> str:
    text = _best_image_semantic_text(candidate) or str(candidate.get("caption") or "")
    tokens = [token for token in _keywords(text) if token not in _GENERIC_MATCH_TOKENS]
    return "|".join(tokens[:8])


def _requires_specific_anchor(candidate: dict[str, Any]) -> bool:
    sources = [
        item
        for item in candidate.get("semantic_sources") or []
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    if not sources:
        return False
    best = max(sources, key=lambda item: float(item.get("confidence") or 0))
    source_type = str(best.get("source_type") or "")
    confidence = float(best.get("confidence") or 0)
    return confidence >= 0.6 and source_type not in {"section_heading", "section_path"}


def _is_auto_reuse_semantically_stable(candidate: dict[str, Any]) -> bool:
    if bool(candidate.get("review_required")):
        return False
    if str(candidate.get("risk_level") or "") == "high":
        return False
    sources = [
        item
        for item in candidate.get("semantic_sources") or []
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    if not sources:
        return bool(_candidate_primary_topics(candidate)) and not _is_weak_image_semantic_text(
            str(candidate.get("semantic_text") or candidate.get("caption") or "")
        )
    best = max(sources, key=lambda item: float(item.get("confidence") or 0))
    source_type = str(best.get("source_type") or "")
    confidence = float(best.get("confidence") or 0)
    text = str(best.get("text") or "")
    if source_type in {"section_heading", "section_path"}:
        return bool(_candidate_primary_topics(candidate)) and not _is_weak_image_semantic_text(text)
    if confidence < 0.58:
        return False
    return not _is_weak_image_semantic_text(text)


def _has_specific_semantic_match(candidate: dict[str, Any], text: str) -> bool:
    detail_terms = _specific_detail_terms(_best_image_semantic_text(candidate))
    if not detail_terms:
        return _text_match_score(_best_image_semantic_text(candidate), text) > 0
    return any(term in (text or "") for term in detail_terms)


def _has_section_topic_match(candidate: dict[str, Any], section: dict[str, Any]) -> bool:
    heading_topics = _primary_topics(str(section.get("heading") or ""))
    if not heading_topics:
        return False
    return bool(_candidate_primary_topics(candidate) & heading_topics)


def _has_section_specific_context_match(candidate: dict[str, Any], section: dict[str, Any]) -> bool:
    if not _has_section_topic_match(candidate, section):
        return False
    heading_terms = _specific_detail_terms(str(section.get("heading") or ""))
    if not heading_terms:
        return False
    candidate_text = " ".join(
        str(part)
        for part in [
            _best_image_semantic_text(candidate),
            candidate.get("caption"),
            candidate.get("group_title"),
            candidate.get("group_semantic_text"),
            candidate.get("nearby_text"),
            candidate.get("bound_section"),
        ]
        if part
    )
    candidate_terms = _specific_detail_terms(candidate_text)
    return bool(heading_terms & candidate_terms)


def _specific_detail_terms(text: str) -> set[str]:
    value = str(text or "")
    stop_terms = _GENERIC_MATCH_TOKENS | {
        "施工",
        "工程",
        "方案",
        "措施",
        "技术",
        "控制",
        "正文",
        "本工程",
        "结合",
        "明确",
        "要求",
        "执行",
        "方法",
        "钢筋",
        "模板",
        "混凝土",
        "测量",
        "工期",
        "进度",
        "计划",
        "安全",
        "质量",
        "管理",
    }
    terms = {term for term in _keywords(value) if term not in stop_terms and len(term) >= 2}
    for segment in re.findall(r"[\u4e00-\u9fff]{3,}", value):
        for size in (2, 3, 4):
            for index in range(0, len(segment) - size + 1):
                term = segment[index : index + size]
                if term not in stop_terms:
                    terms.add(term)
    return terms


def _is_weak_image_semantic_text(text: str) -> bool:
    value = re.sub(r"\s+", "", str(text or "")).strip("：:.-—_")
    if not value:
        return True
    weak_values = _WEAK_IMAGE_SEMANTIC_TOKENS | {
        "模板设计",
        "模板选型及设计",
        "原因",
        "说明",
        "具体措施",
        "做法说明",
        "约束条件",
        "水泥水化热",
        "材料选择",
        "施工方法",
        "控制方法",
    }
    if value in weak_values:
        return True
    if set(re.split(r"[;；,，、/|]+", value)) <= weak_values:
        return True
    if re.fullmatch(r"(第?[一二三四五六七八九十百\d]+步)+", value):
        return True
    if re.fullmatch(r"(步骤[一二三四五六七八九十百\d]+)+", value):
        return True
    if re.fullmatch(r"\d+[\u4e00-\u9fff]{2,8}", value):
        stripped = re.sub(r"^\d+", "", value)
        return stripped in weak_values
    return False


def _has_topic_match(candidate: dict[str, Any], section_text: str) -> bool:
    section_topics = _primary_topics(section_text)
    if not section_topics:
        return True
    candidate_topics = _candidate_primary_topics(candidate)
    return bool(candidate_topics & section_topics)


def _primary_topics(text: str) -> set[str]:
    value = _domain_match_text(text)
    return {
        topic
        for topic, terms in _PRIMARY_TOPIC_TERMS.items()
        if any(term in value for term in terms)
    }


def _is_general_analysis_section(text: str) -> bool:
    value = re.sub(r"\s+", "", str(text or ""))
    if any(term in value for term in _GENERAL_ANALYSIS_SECTION_TERMS):
        return True
    return value in _GENERAL_ANALYSIS_EXACT_TERMS


def _text_match_score(left: str, right: str) -> int:
    left_tokens = set(_keywords(left))
    right_tokens = set(_keywords(right))
    left_domain_text = _domain_match_text(left)
    right_domain_text = _domain_match_text(right)
    left_domain = {term for term in _DOMAIN_MATCH_TERMS if term in left_domain_text}
    right_domain = {term for term in _DOMAIN_MATCH_TERMS if term in right_domain_text}
    if left_domain and right_domain and not (left_domain & right_domain):
        return 0
    intersection = left_tokens & right_tokens
    if not intersection:
        if _primary_topics(left_domain_text) & _primary_topics(right_domain_text):
            return 1
        return 0
    specific_hits = intersection - _GENERIC_MATCH_TOKENS
    if not specific_hits:
        if _primary_topics(left_domain_text) & _primary_topics(right_domain_text):
            return 1
        return 0
    return len(specific_hits) * 3 + len(intersection & _GENERIC_MATCH_TOKENS)


def _domain_match_text(text: str) -> str:
    value = str(text or "")
    return re.sub(r"钢筋\s*轴线", "钢筋中线", value)


def _keywords(text: str) -> list[str]:
    value = text or ""
    tokens: list[str] = []
    tokens.extend(word.lower() for word in re.findall(r"[A-Za-z0-9]+", value) if len(word) >= 2)
    tokens.extend(term for term in _DOMAIN_MATCH_TERMS if term in value)
    for segment in re.findall(r"[\u4e00-\u9fff]{2,}", value):
        if len(segment) <= 4:
            tokens.append(segment)
            continue
        for size in (2, 3, 4):
            tokens.extend(segment[index : index + size] for index in range(0, len(segment) - size + 1))
    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result[:120]


def _strip_heading_number(value: str) -> str:
    return re.sub(r"^\s*\d+(?:\.\d+)*[.．、]?\s*", "", value).strip()


def _has_short_rich_table(sections: list[Any], min_rows: int) -> bool:
    for section in sections:
        if not isinstance(section, dict):
            continue
        for block in section.get("blocks") or []:
            if not isinstance(block, dict) or block.get("type") != "rich_table":
                continue
            rows = block.get("rows") or []
            if len(rows) < min_rows:
                return True
    return False


def _has_short_paragraph_section(sections: list[Any], min_paragraphs: int) -> bool:
    for section in sections:
        if not isinstance(section, dict):
            continue
        count = sum(
            1
            for block in section.get("blocks") or []
            if isinstance(block, dict) and block.get("type") == "paragraph" and str(block.get("text") or "").strip()
        )
        if count < min_paragraphs:
            return True
    return False


def _has_low_density_section(sections: list[Any], min_paragraphs: int) -> bool:
    """判断小节是否明显过薄。

    施工方案常用“正文 + 控制表 + 做法图片”组织内容，不能只按段落数判定质量。
    少一段但已有表格或图片时视为可接受；只有正文明显不足且缺少表格/图片支撑时才触发 warning。
    """

    for section in sections:
        if not isinstance(section, dict):
            continue
        paragraph_count = 0
        has_table = False
        has_visual = False
        for block in section.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "paragraph" and str(block.get("text") or "").strip():
                paragraph_count += 1
            elif block_type == "rich_table":
                has_table = True
            elif block_type in {"image_ref", "image_placeholder"}:
                has_visual = True
        if paragraph_count <= 0:
            return True
        if paragraph_count < max(1, min_paragraphs - 1):
            return True
        if paragraph_count < min_paragraphs and not (has_table or has_visual):
            return True
    return False


def _paragraph_total_material_gap(
    paragraph_count: int,
    min_paragraphs_total: int,
    section_count: int,
) -> bool:
    if min_paragraphs_total <= 0 or paragraph_count >= min_paragraphs_total:
        return False
    gap = min_paragraphs_total - paragraph_count
    tolerated_gap = max(1, min(2, section_count // 2))
    return gap > tolerated_gap


def _forbidden_hits(output: dict[str, Any], package: dict[str, Any]) -> list[str]:
    text = json.dumps(output, ensure_ascii=False)
    forbidden = ["优秀标书", "参考素材", "AI生成", "语言模型", "大模型", "模型生成", "模型输出"]
    constraints = package.get("generation_constraints") or {}
    forbidden.extend(str(item) for item in constraints.get("forbidden_content") or [] if str(item).strip())
    hits = []
    for item in forbidden:
        if item and item in text:
            hits.append(item)
    return sorted(set(hits))


def _issue(severity: str, type_: str, message: str) -> dict[str, str]:
    return {"severity": severity, "type": type_, "message": message}


def _bounded_int(value: Any, *, default: int, lower: int, upper: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(lower, min(upper, number))


def _clip(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    return text[:limit]


def _reference_excerpt_limit(item: dict[str, Any]) -> int:
    reuse_level = str(item.get("reuse_level") or "")
    if reuse_level == "direct_reuse":
        return 1200
    if reuse_level == "parameterized_reuse":
        return 900
    if reuse_level == "manual_review":
        return 300
    return 700


def _block_summary(blocks: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for block in blocks:
        type_ = str(block.get("type") or "unknown") if isinstance(block, dict) else "unknown"
        counts[type_] = counts.get(type_, 0) + 1
    if not counts:
        return "无内容块"
    return "，".join(f"{key}={value}" for key, value in sorted(counts.items()))


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def _now_iso() -> str:
    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).isoformat(timespec="seconds")
