"""Response builder for the platform-level assistant.

The platform assistant is an adapter around existing capabilities. It can read
project context, but it must not execute or replace the independent bid drafting
workflow.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from construction_bidding_agent.llm_client import call_openai_json, parse_json_response
from construction_bidding_agent.llm_config import llm_config

from .intent_classifier import classify_platform_intent
from .retriever import build_platform_retrieval_context
from .schemas import PlatformAssistantContext


PLATFORM_ASSISTANT_ANSWER_TASK_KEY = "platform_assistant_answer"
PLATFORM_INTENT_LABELS = {
    "daily_chat": "日常问候",
    "platform_capability": "平台能力",
    "platform_qa": "智能问答",
    "bid_intelligence": "标讯情报",
    "enterprise_risk": "企业风险",
    "supplier_recommendation": "供应商推荐",
    "knowledge_base": "投标知识库",
    "template": "投标模板",
    "bid_drafting": "技术标编制",
    "fallback": "能力边界",
}


def build_platform_assistant_response(context: PlatformAssistantContext) -> dict[str, Any]:
    if context.project_answer:
        return _project_delegated_response(context)

    intent_meta = classify_platform_intent(context.message, active_view=context.active_view)
    intent = intent_meta.intent
    retrieved_context = build_platform_retrieval_context(
        message=context.message,
        intent=intent,
        active_view=context.active_view,
        knowledge_manifest=context.knowledge_manifest,
        bid_templates=context.bid_templates,
        rag_preview=context.rag_preview,
        limit=6,
    )
    base_answer = _rule_answer(context, intent, retrieved_context)
    answer_meta = _maybe_rewrite_with_llm(
        message=context.message,
        intent=intent,
        base_answer=base_answer,
        active_view=context.active_view,
        retrieved_context=retrieved_context,
    )
    answer = str(answer_meta.get("answer") or base_answer).strip()
    answer_source = str(answer_meta.get("source") or "rule")
    fallback_reason = _fallback_reason(intent, context)
    return {
        "answer": answer,
        "intent": intent,
        "intent_label": PLATFORM_INTENT_LABELS.get(intent, intent),
        "intent_source": intent_meta.source,
        "intent_confidence": intent_meta.confidence,
        "intent_reason": intent_meta.reason,
        "answer_source": answer_source,
        "answer_confidence": answer_meta.get("confidence"),
        "answer_reason": answer_meta.get("reason") or "平台助手基于本地上下文生成。",
        "context_scope": _context_scope(context),
        "intent_scope": "平台问答：能力说明、投标知识库、模板、标讯/风险/供应商接入状态；编标问题只读取项目上下文",
        "evidence": [],
        "retrieved_context": _public_retrieved_context(retrieved_context),
        "blocked_actions": _blocked_actions(),
        "suggested_actions": _suggested_actions(intent, context),
        "fallback_reason": fallback_reason,
    }


def _project_delegated_response(context: PlatformAssistantContext) -> dict[str, Any]:
    result = dict(context.project_answer or {})
    result["context_scope"] = "技术标编制模块：读取当前项目上下文"
    result["platform_delegated"] = True
    result["assistant_route"] = "bid_drafting_project_context"
    blocked = list(result.get("blocked_actions") or [])
    blocked.append("编标流程保持独立，小助手只读取项目状态和依据，不执行上传、解析、生成、导出或覆盖操作。")
    result["blocked_actions"] = _dedupe_strings(blocked)
    if not result.get("fallback_reason"):
        result["fallback_reason"] = None
    return result


def _rule_answer(context: PlatformAssistantContext, intent: str, retrieved_context: list[Mapping[str, Any]]) -> str:
    account = context.account_context or {}
    user_name = _assistant_user_name(account)
    prefix = f"{user_name}，" if user_name else ""
    if intent == "daily_chat":
        return (
            f"你好，{user_name or '我是小智'}！我是智标工坊的平台 AI 助手小智。"
            "我可以回答平台能力、投标知识库、模板、标讯情报、企业风险和供应商推荐的接入状态；"
            "进入技术标编制并选择项目后，我会读取当前项目上下文，回答进度、下一步、评分点、目录、正文生成和 Word 复核。"
        )
    if intent == "platform_capability":
        return (
            f"{prefix}当前平台定位是智能招投标工作台，能力包括智能问答、标讯情报、企业风险、供应商推荐、投标知识库、投标模板和技术标编制。"
            "其中技术标编制是独立流程模块，继续按五步推进；平台助手只负责解释、检索依据和提示风险。"
        )
    if intent == "knowledge_base":
        manifest_line = _knowledge_status_line(context.knowledge_manifest)
        return (
            f"{prefix}投标知识库可以放优秀标书、法律法规、技术规范、企业制度、评审办法和其他资料。"
            f"{manifest_line} 页面只展示摘要、来源、类型和使用建议，不展示大段敏感原文。"
        )
    if intent == "template":
        template_line = _template_status_line(context.bid_templates)
        return (
            f"{prefix}投标模板用于预览企业标准目录、章节写作重点、表格清单和适用场景。"
            f"{template_line} 模板不会自动覆盖已确认目录或正文。"
        )
    if intent == "bid_intelligence":
        return (
            f"{prefix}标讯情报入口已预留，后续接剑鱼 API 做实时公告检索、机会筛选和公告追踪。"
            "当前未配置实时标讯数据源，所以我不能编造最新公告；这不会影响技术标编制主流程。"
        )
    if intent == "enterprise_risk":
        return (
            f"{prefix}企业风险入口已预留，后续接企查查 MCP 做企业风险、资质核验和合规判断。"
            "当前未配置风险数据源时，我只能给风险检查方向，不能输出未经核验的企业结论。"
        )
    if intent == "supplier_recommendation":
        return (
            f"{prefix}供应商推荐需要历史项目、企业库、产品库和供应商数据支撑。"
            "当前入口已预留，数据未接入前不生成供应商名单，避免黑盒推荐。"
        )
    if intent == "bid_drafting":
        return (
            f"{prefix}技术标编制仍然走独立五步流程：上传资料、解析确认、生成目录、生成正文、Word 复核。"
            "选择具体项目后，我可以读取项目上下文回答下一步、评分点响应、目录、正文生成和 Word 复核问题。"
        )
    if intent == "platform_qa":
        return (
            f"{prefix}智能问答模块后续会接入问题理解、拆分、多源检索和带依据生成。"
            "当前我先基于本地平台说明、投标知识库摘要、模板和编标上下文回答；没有可靠来源时会明确说明。"
        )
    return (
        f"{prefix}这个问题我暂时不能可靠回答。当前平台助手优先覆盖平台能力、投标知识库、模板、标讯/风险/供应商接入状态；"
        "编标细节请进入技术标编制并选择项目后提问。"
    )


def _maybe_rewrite_with_llm(
    *,
    message: str,
    intent: str,
    base_answer: str,
    active_view: str,
    retrieved_context: list[Mapping[str, Any]],
) -> dict[str, Any]:
    if not _platform_llm_answer_enabled():
        return {"answer": base_answer, "source": "rule", "confidence": 1.0, "reason": "平台助手 LLM 回答增强未开启。"}
    config = llm_config(task_key=PLATFORM_ASSISTANT_ANSWER_TASK_KEY)
    if not config.api_key:
        return {"answer": base_answer, "source": "rule", "confidence": 1.0, "reason": "未配置平台助手模型 Key，使用规则降级回答。"}
    system_prompt = (
        "你是智能招投标平台的 AI 助手小智。"
        "请只基于给定上下文和基础回答，输出自然、简洁、专业的中文答案。"
        "技术标编制流程是独立模块，你只能解释和读取上下文，不能声称自己会执行上传、解析、生成、导出、删除或覆盖。"
        "如果实时标讯、企查查、Web Search 等外部源未接入，要明确降级，不要编造最新信息。"
        "返回 JSON：{\"answer\":\"...\",\"confidence\":0.0-1.0,\"reason\":\"一句话\"}。"
    )
    payload = {
        "message": message,
        "intent": intent,
        "active_view": active_view,
        "base_answer": base_answer,
        "retrieved_context": _public_retrieved_context(retrieved_context)[:4],
    }
    try:
        raw = call_openai_json(
            config=config,
            task_key=PLATFORM_ASSISTANT_ANSWER_TASK_KEY,
            system_prompt=system_prompt,
            user_input=json.dumps(payload, ensure_ascii=False),
        )
        parsed = parse_json_response(raw)
    except Exception:
        return {"answer": base_answer, "source": "rule", "confidence": 1.0, "reason": "平台助手模型调用失败，使用规则降级回答。"}
    answer = str(parsed.get("answer") or "").strip()
    if not answer:
        return {"answer": base_answer, "source": "rule", "confidence": 1.0, "reason": "平台助手模型返回为空，使用规则降级回答。"}
    return {
        "answer": answer,
        "source": "llm",
        "confidence": _confidence(parsed.get("confidence")),
        "reason": str(parsed.get("reason") or "基于平台上下文生成。").strip(),
    }


def _public_retrieved_context(items: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    public = []
    for item in items:
        public.append(
            {
                "type": item.get("type"),
                "title": item.get("title"),
                "content": item.get("content"),
                "source": item.get("source"),
                "category": item.get("category"),
                "risk_level": item.get("risk_level"),
            }
        )
    return public


def _context_scope(context: PlatformAssistantContext) -> str:
    if context.project_id:
        return "平台助手 + 技术标编制项目上下文"
    return f"平台助手 · {context.active_view or 'home'}"


def _fallback_reason(intent: str, context: PlatformAssistantContext) -> str | None:
    if intent in {"bid_intelligence", "enterprise_risk", "supplier_recommendation"}:
        return "外部数据源尚未接入或未配置，当前只提供能力说明和降级提示。"
    if intent == "fallback":
        return "问题未命中平台助手当前能力范围。"
    return None


def _suggested_actions(intent: str, context: PlatformAssistantContext) -> list[dict[str, str]]:
    if intent == "bid_drafting" and not context.project_id:
        return [{"title": "进入技术标编制", "target_view": "projects", "reason": "选择项目后可读取编标上下文。"}]
    if intent == "knowledge_base":
        return [{"title": "查看投标知识库", "target_view": "rag", "reason": "管理资料摘要、来源和类型。"}]
    if intent == "template":
        return [{"title": "查看投标模板", "target_view": "templates", "reason": "预览模板结构和适配说明。"}]
    return []


def _blocked_actions() -> list[str]:
    return [
        "小助手不执行删除、覆盖、发布或最终成稿确认。",
        "编标流程保持独立，助手只读取上下文并给出建议。",
    ]


def _knowledge_status_line(manifest: Mapping[str, Any] | None) -> str:
    if not isinstance(manifest, Mapping):
        return "当前还没有读取到知识库清单。"
    return f"当前已记录 {int(manifest.get('source_count') or 0)} 份资料、{int(manifest.get('slice_count') or 0)} 条切片。"


def _template_status_line(templates: list[Mapping[str, Any]]) -> str:
    if not templates:
        return "当前还没有读取到模板。"
    return f"当前模板库约 {len(templates)} 套模板可用于预览和适配分析。"


def _assistant_user_name(account: Mapping[str, Any]) -> str:
    role = str(account.get("role") or "")
    if role == "admin":
        return "神"
    name = str(account.get("display_name") or account.get("username") or "").strip()
    return name or ""


def _dedupe_strings(items: list[Any]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(number, 0.0), 1.0)


def _platform_llm_answer_enabled() -> bool:
    raw = os.getenv("PLATFORM_ASSISTANT_ENABLE_LLM", "1")
    return str(raw).strip().lower() not in {"", "0", "false", "no", "off"}

