"""Local retrieval context for the platform assistant."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from construction_bidding_agent.assistant.retrieval import build_lightweight_retrieval_context


PLATFORM_CAPABILITIES = (
    {
        "type": "platform_capability",
        "title": "智能招投标平台能力",
        "content": "平台聚合智能问答、标讯情报、企业风险、供应商推荐、投标知识库、投标模板和技术标编制。当前最完整能力是技术标编制，平台级问答能力正在逐步接入。",
        "source": "平台能力说明",
    },
    {
        "type": "platform_capability",
        "title": "编标流程独立运行",
        "content": "技术标编制继续走上传资料、解析确认、生成目录、生成正文、Word 复核五步流程。平台助手只读取上下文并给建议，不接管编标工作流。",
        "source": "系统边界",
    },
    {
        "type": "bid_intelligence",
        "title": "标讯情报接入状态",
        "content": "实时标讯计划接入剑鱼 API。当前未配置时只提供能力说明和降级提示，不影响技术标编制。",
        "source": "数据源接入计划",
    },
    {
        "type": "enterprise_risk",
        "title": "企业风险接入状态",
        "content": "企业风险计划接入企查查 MCP。当前未配置时只提供风险检查方向和降级提示，不输出未经核验的企业结论。",
        "source": "数据源接入计划",
    },
    {
        "type": "supplier_recommendation",
        "title": "供应商推荐接入状态",
        "content": "供应商推荐依赖历史项目、企业库、产品库和供应商数据。数据未接入前只说明推荐边界，不虚构供应商名单。",
        "source": "数据源接入计划",
    },
    {
        "type": "knowledge_base",
        "title": "投标知识库资料范围",
        "content": "投标知识库可沉淀优秀标书、法律法规、技术规范、企业制度、评审办法和其他资料。平台助手和编标生成都可以引用摘要与来源。",
        "source": "投标知识库",
    },
    {
        "type": "template_candidate",
        "title": "投标模板使用边界",
        "content": "投标模板用于预览章节结构、写作重点和表格清单，不自动覆盖已确认目录或正文。",
        "source": "投标模板",
    },
)


def build_platform_retrieval_context(
    *,
    message: str,
    intent: str,
    active_view: str,
    knowledge_manifest: Mapping[str, Any] | None = None,
    bid_templates: list[Mapping[str, Any]] | None = None,
    rag_preview: Mapping[str, Any] | None = None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    base_context: list[dict[str, Any]] = [dict(item) for item in PLATFORM_CAPABILITIES]
    base_context.append(_module_context(active_view))
    base_context.extend(_knowledge_manifest_context(knowledge_manifest))
    base_context.extend(_template_context(bid_templates or []))
    return build_lightweight_retrieval_context(
        message=message,
        intent=_retrieval_intent(intent),
        base_context=base_context,
        rag_preview=rag_preview,
        limit=limit,
    )


def _module_context(active_view: str) -> dict[str, Any]:
    mapping = {
        "home": ("平台首页", "平台首页用于查看平台能力入口、当前可用模块和后续接入方向。"),
        "qa": ("智能问答", "智能问答后续承接问题理解、拆分、多源检索和带依据回答。"),
        "bid-intel": ("标讯情报", "标讯情报模块后续接入实时招标公告、机会筛选和公告追踪。"),
        "risk": ("企业风险", "企业风险模块后续接入企业风险、资质核验和合规判断。"),
        "suppliers": ("供应商推荐", "供应商推荐模块后续基于历史项目和企业数据给出推荐理由。"),
        "rag": ("投标知识库", "投标知识库管理优秀标书、法规、规范、企业制度和评审办法。"),
        "templates": ("投标模板", "投标模板管理企业标准模板、章节结构、写作重点和表格清单。"),
        "projects": ("技术标编制", "技术标编制模块保留独立五步流程，小助手只读取上下文辅助解释。"),
    }
    title, content = mapping.get(active_view, ("平台模块", "当前模块属于智能招投标平台。"))
    return {"type": "page_advice", "title": title, "content": content, "source": "当前模块"}


def _knowledge_manifest_context(manifest: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(manifest, Mapping):
        return []
    quality = manifest.get("quality_summary") if isinstance(manifest.get("quality_summary"), Mapping) else {}
    type_counts = quality.get("knowledge_type_counts") if isinstance(quality.get("knowledge_type_counts"), Mapping) else {}
    content = (
        f"已入库资料 {int(manifest.get('source_count') or 0)} 份，"
        f"章节切片 {int(manifest.get('slice_count') or 0)} 条，"
        f"表格 {int(manifest.get('table_count') or 0)} 个，"
        f"图片 {int(manifest.get('image_count') or 0)} 张。"
    )
    if type_counts:
        content += " 类型分布：" + "、".join(f"{key}{value}份" for key, value in type_counts.items())
    return [{"type": "knowledge_base", "title": "投标知识库状态", "content": content, "source": "投标知识库清单"}]


def _template_context(templates: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for template in templates[:3]:
        name = str(template.get("name") or template.get("template_name") or "投标模板")
        project_type = str(template.get("project_type") or "通用")
        section_count = len(template.get("standard_sections") or template.get("sections") or [])
        items.append(
            {
                "type": "template_candidate",
                "title": name,
                "content": f"项目类型：{project_type}；章节数：{section_count}。模板仅供预览和参考，不自动覆盖用户成果。",
                "source": "投标模板库",
            }
        )
    return items


def _retrieval_intent(intent: str) -> str:
    if intent in {"knowledge_base", "platform_qa"}:
        return "materials"
    if intent == "template":
        return "template"
    if intent in {"bid_intelligence", "enterprise_risk", "supplier_recommendation"}:
        return "risk"
    if intent == "bid_drafting":
        return "context_help"
    return "context_help"

