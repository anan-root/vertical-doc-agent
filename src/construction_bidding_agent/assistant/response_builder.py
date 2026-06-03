"""项目智能摘要和简单对话回答构造。"""

from __future__ import annotations

import re
import json
import os
from collections.abc import Callable, Mapping
from typing import Any

from construction_bidding_agent.llm_client import call_openai_json, parse_json_response
from construction_bidding_agent.llm_config import llm_config
from .intent_classifier import (
    DAILY_CHAT_FAREWELL_TOKENS,
    DAILY_CHAT_GREETING_TOKENS,
    DAILY_CHAT_SELF_INTRO_TOKENS,
    DAILY_CHAT_THANKS_TOKENS,
    classify_assistant_intent,
)
from .retrieval import build_lightweight_retrieval_context


RAG_EVIDENCE_PRIORITY = {
    "law_regulation": 0,
    "technical_standard": 1,
    "enterprise_policy": 2,
    "review_rule": 3,
    "excellent_bid": 4,
    "other": 5,
}

ASSISTANT_INTENT_LABELS = {
    "daily_chat": "日常问候",
    "project_overview": "项目基本信息",
    "context_help": "当前页面说明",
    "progress": "项目进度",
    "next_action": "下一步建议",
    "score_points": "评分点响应",
    "review_report": "AI 复核摘要",
    "outline": "技术标目录",
    "generation_summary": "生成小结",
    "generation": "正文生成",
    "queue_preflight": "任务提交前检查",
    "word": "Word 复核",
    "template": "投标模板",
    "template_boundary": "模板使用边界",
    "materials": "智库依据",
    "material_ingestion": "智库资料入库",
    "model_ops": "模型配置排障",
    "risk": "风险提示",
    "fallback": "超出助手范围",
}

ASSISTANT_SCOPE_TEXT = "当前编标项目：项目进度、下一步、评分点、目录、正文生成、Word 复核、模板和投标智库"
ASSISTANT_LLM_TASK_KEY = "assistant_chat_intent"
ASSISTANT_LLM_ANSWER_TASK_KEY = "assistant_chat_answer"
ASSISTANT_RULE_INTENT_MIN_CONFIDENCE = 0.85
ASSISTANT_LLM_INTENT_MIN_CONFIDENCE = 0.55
ASSISTANT_ALLOWED_INTENTS = frozenset(ASSISTANT_INTENT_LABELS)
ASSISTANT_LLM_ANSWER_INTENTS = frozenset(
    {
        "project_overview",
        "context_help",
        "progress",
        "next_action",
        "score_points",
        "review_report",
        "outline",
        "generation_summary",
        "generation",
        "queue_preflight",
        "word",
        "template",
        "materials",
        "risk",
    }
)

AssistantIntentResolver = Callable[[Mapping[str, Any]], Mapping[str, Any] | None]
AssistantAnswerResolver = Callable[[Mapping[str, Any]], Mapping[str, Any] | None]


def build_project_ai_summary(
    workflow_summary: Mapping[str, Any],
    agent_recommendation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """基于工作流摘要生成管理者可读的项目摘要。

    第一版只使用结构化项目状态，不调用外部模型。
    """

    project = _dict(workflow_summary.get("project"))
    stats = _dict(workflow_summary.get("stats"))
    recommendation = _dict(agent_recommendation)
    action = _dict(recommendation.get("recommended_next_action"))
    current_state = str(recommendation.get("state_label") or "未明确")
    project_name = str(project.get("name") or "当前项目")
    project_type = _project_type_label(project.get("project_type"))
    score_points = int(stats.get("score_points") or 0)
    review_items = int(stats.get("review_items") or 0)
    tender_files = int(stats.get("tender_files") or 0)
    chapters = int(stats.get("estimated_chapters") or 0)
    next_title = str(action.get("title") or "查看项目状态")
    next_reason = str(action.get("reason") or "建议先读取项目状态，确认当前流程位置。")
    risks = _risk_lines(workflow_summary, agent_recommendation)
    highlights = _highlight_lines(workflow_summary)
    ai_cards = _ai_cards(workflow_summary, agent_recommendation)
    text = (
        f"{project_name} 当前处于“{current_state}”阶段，项目类型为{project_type}。"
        f"系统已记录 {tender_files} 份招标文件，识别到 {score_points} 个技术评分点，"
        f"预计正文生成单元约 {chapters} 个，当前待复核项 {review_items} 个。"
        f"建议下一步：{next_title}。{next_reason}"
    )
    return {
        "summary": text,
        "project_name": project_name,
        "current_state": recommendation.get("current_state"),
        "state_label": current_state,
        "next_action": action,
        "highlights": highlights,
        "risks": risks,
        "ai_cards": ai_cards,
        "assistant_scope": "当前项目摘要、下一步建议、评分点风险、模板推荐和参考资料提示",
        "sources": [
            {"type": "workflow_summary", "label": "项目流程摘要"},
            {"type": "assistant_rule", "label": "编标助手下一步建议"},
        ],
    }


def build_assistant_chat_response(
    *,
    message: str,
    workflow_summary: Mapping[str, Any],
    agent_recommendation: Mapping[str, Any] | None = None,
    template_recommendation: Mapping[str, Any] | None = None,
    rag_preview: Mapping[str, Any] | None = None,
    active_view: str | None = None,
    active_step: str | None = None,
    account_context: Mapping[str, Any] | None = None,
    intent_resolver: AssistantIntentResolver | None = None,
    answer_resolver: AssistantAnswerResolver | None = None,
) -> dict[str, Any]:
    """围绕当前项目进行固定意图问答。

    第一版不做开放式聊天，避免无边界回答和敏感原文泄露。
    """

    summary = build_project_ai_summary(workflow_summary, agent_recommendation)
    recommendation = _dict(agent_recommendation)
    action = _dict(recommendation.get("recommended_next_action"))
    stats = _dict(workflow_summary.get("stats"))
    score_points = _list(workflow_summary.get("score_points"))
    review_items = _list(workflow_summary.get("review_items"))
    context_advice = _context_advice(active_view, active_step, workflow_summary)
    evidence = _assistant_rag_evidence_items(rag_preview)
    rule_intent_meta = classify_assistant_intent(message)
    rule_intent = rule_intent_meta.intent
    intent_meta = _resolve_assistant_intent(
        message=message,
        workflow_summary=workflow_summary,
        agent_recommendation=agent_recommendation,
        template_recommendation=template_recommendation,
        rag_preview=rag_preview,
        active_view=active_view,
        active_step=active_step,
        summary=summary,
        rule_intent=rule_intent,
        rule_confidence=rule_intent_meta.confidence,
        rule_reason=rule_intent_meta.reason,
        intent_resolver=intent_resolver,
    )
    intent = str(intent_meta.get("intent") or rule_intent or "fallback")
    intent_source = str(intent_meta.get("source") or "rule")
    intent_confidence = intent_meta.get("confidence")
    intent_reason = str(intent_meta.get("reason") or "")

    if intent == "daily_chat":
        answer = _daily_chat_answer(message, account_context, workflow_summary)
        return {
            "answer": answer,
            "intent": intent,
            "intent_label": ASSISTANT_INTENT_LABELS.get(intent, intent),
            "intent_source": "rule",
            "intent_confidence": 1.0,
            "intent_reason": intent_reason or "日常问候或身份介绍。",
            "answer_source": "rule",
            "answer_confidence": 1.0,
            "answer_reason": "日常问候或身份介绍。",
            "intent_scope": ASSISTANT_SCOPE_TEXT,
            "context_advice": context_advice,
            "sources": summary["sources"],
            "evidence": [],
            "retrieved_context": [],
            "blocked_actions": [],
            "suggested_actions": [],
        }

    retrieval_bundle = _build_assistant_retrieval_bundle(
        message=message,
        workflow_summary=workflow_summary,
        agent_recommendation=agent_recommendation,
        template_recommendation=template_recommendation,
        rag_preview=rag_preview,
        active_view=active_view,
        active_step=active_step,
        summary=summary,
        intent=intent,
    )

    if intent == "project_overview":
        answer = _project_overview_answer(workflow_summary, summary)
    elif intent == "context_help":
        answer = f"{context_advice['title']}：{context_advice['text']}"
    elif intent == "progress":
        answer = summary["summary"]
    elif intent == "next_action":
        answer = _next_action_answer(action, active_step, workflow_summary)
    elif intent == "score_points":
        answer = _score_points_answer(stats, score_points, review_items, workflow_summary)
    elif intent == "review_report":
        answer = _review_report_answer(workflow_summary)
    elif intent == "outline":
        answer = _outline_answer(workflow_summary)
    elif intent == "generation_summary":
        answer = _generation_summary_answer(workflow_summary)
    elif intent == "generation":
        answer = _generation_answer(workflow_summary, rag_preview)
    elif intent == "queue_preflight":
        answer = _queue_preflight_answer(workflow_summary, active_step, rag_preview)
    elif intent == "word":
        answer = _word_answer(workflow_summary)
    elif intent == "template":
        templates = _list(_dict(template_recommendation).get("recommendations"))
        if templates:
            best = _dict(templates[0])
            answer = f"当前优先推荐模板：{best.get('name')}。推荐原因：{best.get('reason') or '项目类型和评分点较匹配'}。第一版只建议预览，不会自动覆盖目录。"
        else:
            answer = "当前还没有匹配到明确模板。可以先使用通用施工总承包或 EPC 模板作为参考，但套用前需要人工确认。"
    elif intent == "materials":
        materials = _list(_dict(rag_preview).get("results"))
        if materials:
            best = _dict(materials[0])
            answer = f"当前可参考的资料包括：{best.get('title') or best.get('section_title') or '未命名资料'}，资料类型：{best.get('knowledge_type_label') or '优秀标书'}，来源：{best.get('source_title') or '投标智库'}。页面只展示摘要和来源，不输出大段原文。"
        else:
            answer = "当前没有检索到明确参考资料。可以换一个章节名、评分点关键词，或先补充优秀标书、法规规范、企业制度等资料。"
    elif intent == "material_ingestion":
        answer = "投标智库建议至少沉淀六类资料：优秀标书用于写法和企业风格，法律法规用于合规风险，技术规范用于质量安全和工艺依据，企业制度用于内部管理口径，评审办法用于评分响应，其他资料用于项目特殊要求。上传前请先做脱敏确认，页面只展示摘要和来源。"
    elif intent == "template_boundary":
        answer = "投标模板第一版只做推荐和预览，不会自动覆盖你已经编辑过的目录或正文。适合用它来统一章节结构、常用表格和企业表达；真正套用前应人工确认项目类型、评分点和版本。"
    elif intent == "model_ops":
        answer = "模型配置会影响招标解析、目录补强和正文生成。想提速可以降低并发以外的重试浪费、先小批量生成、控制 max_tokens；想提高质量则优先保证评分点输入、参考资料质量和超时时间。失败排查先看 API Key、base_url、模型名、任务日志和 LLM 审计摘要。"
    elif intent == "risk":
        answer = _risk_answer(summary, rag_preview)
    else:
        answer = "我目前只围绕当前编标项目回答：项目进度、下一步、评分点、目录、正文生成、Word 复核、模板和参考资料。"

    answer_meta = _maybe_rewrite_assistant_answer_with_llm(
        message=message,
        intent=intent,
        base_answer=answer,
        retrieval_bundle=retrieval_bundle,
        active_view=active_view,
        active_step=active_step,
        workflow_summary=workflow_summary,
        summary=summary,
        answer_resolver=answer_resolver,
    )
    if isinstance(answer_meta, Mapping) and str(answer_meta.get("answer") or "").strip():
        answer = str(answer_meta["answer"]).strip()
    answer_source = str(answer_meta.get("source") or "rule") if isinstance(answer_meta, Mapping) else "rule"
    answer_confidence = _coerce_confidence(answer_meta.get("confidence")) if isinstance(answer_meta, Mapping) else None
    answer_reason = str(answer_meta.get("reason") or "") if isinstance(answer_meta, Mapping) else ""

    if answer_source != "llm" and evidence and intent in {"generation_summary", "generation", "materials", "risk", "queue_preflight", "score_points", "review_report"}:
        answer = _append_evidence_clause(answer, evidence)

    return {
        "answer": answer,
        "intent": intent,
        "intent_label": ASSISTANT_INTENT_LABELS.get(intent, intent),
        "intent_source": intent_source,
        "intent_confidence": intent_confidence,
        "intent_reason": intent_reason,
        "answer_source": answer_source,
        "answer_confidence": answer_confidence,
        "answer_reason": answer_reason,
        "intent_scope": ASSISTANT_SCOPE_TEXT,
        "context_advice": context_advice,
        "sources": summary["sources"],
        "evidence": evidence,
        "retrieved_context": _list(retrieval_bundle.get("retrieved_context")),
        "blocked_actions": [
            item.get("reason")
            for item in _list(recommendation.get("blocked_tools"))
            if isinstance(item, Mapping) and item.get("reason")
        ],
        "suggested_actions": [action] if action else [],
    }


def _daily_chat_answer(
    message: str,
    account_context: Mapping[str, Any] | None,
    workflow_summary: Mapping[str, Any],
) -> str:
    text = message.strip().lower()
    user_name = _assistant_user_name(account_context)
    project = _dict(workflow_summary.get("project"))
    project_name = str(project.get("name") or "当前项目")
    project_type = _project_type_label(project.get("project_type"))
    greeting_prefix = f"你好，{user_name}！" if user_name else "你好！"
    intro = (
        f"{greeting_prefix}我是智标工坊的 AI 助手小智。"
        f"我主要陪你处理 {project_name} 这类{project_type}编标项目。"
        "我的能力边界主要是：项目进度、下一步、评分点、目录、正文生成、Word 复核、模板和参考资料。"
    )

    if any(token in text for token in DAILY_CHAT_THANKS_TOKENS):
        return f"{greeting_prefix}不客气，我会继续围着当前编标项目帮你。"
    if any(token in text for token in DAILY_CHAT_FAREWELL_TOKENS):
        return f"{greeting_prefix}我先待命，{user_name}需要时随时叫我。"
    if any(token in text for token in DAILY_CHAT_SELF_INTRO_TOKENS) or any(token in text for token in DAILY_CHAT_GREETING_TOKENS):
        return (
            f"{intro}"
            "如果你想继续，我可以先帮你看项目进度、下一步或评分点响应情况。"
        )
    return (
        f"{intro}"
        "如果你愿意，我也可以顺着当前步骤给你讲讲这一页该看什么。"
    )


def _resolve_assistant_intent(
    *,
    message: str,
    workflow_summary: Mapping[str, Any],
    agent_recommendation: Mapping[str, Any] | None,
    template_recommendation: Mapping[str, Any] | None,
    rag_preview: Mapping[str, Any] | None,
    active_view: str | None,
    active_step: str | None,
    summary: Mapping[str, Any],
    rule_intent: str,
    rule_confidence: float = 0.0,
    rule_reason: str = "",
    intent_resolver: AssistantIntentResolver | None = None,
) -> dict[str, Any]:
    """先走规则，规则拿不准时再让模型判一次意图。"""

    if rule_intent == "daily_chat" and rule_confidence >= ASSISTANT_RULE_INTENT_MIN_CONFIDENCE:
        return {
            "intent": rule_intent,
            "source": "rule",
            "confidence": rule_confidence,
            "reason": rule_reason or "日常问候或身份介绍由本地规则直接处理。",
        }

    if (
        rule_intent != "fallback"
        and rule_confidence >= ASSISTANT_RULE_INTENT_MIN_CONFIDENCE
        and not _assistant_llm_intent_force_enabled()
    ):
        return {
            "intent": rule_intent,
            "source": "rule",
            "confidence": rule_confidence,
            "reason": rule_reason or "规则已直接命中。",
        }

    if not _assistant_llm_intent_enabled():
        return {
            "intent": rule_intent if rule_intent in ASSISTANT_ALLOWED_INTENTS else "fallback",
            "source": "rule",
            "confidence": rule_confidence,
            "reason": rule_reason or "规则意图识别已命中，LLM 意图兜底已关闭。",
        }

    context = _build_assistant_intent_context(
        message=message,
        workflow_summary=workflow_summary,
        agent_recommendation=agent_recommendation,
        template_recommendation=template_recommendation,
        rag_preview=rag_preview,
        active_view=active_view,
        active_step=active_step,
        summary=summary,
    )
    context["rule_intent"] = {
        "intent": rule_intent,
        "confidence": rule_confidence,
        "reason": rule_reason,
        "min_confidence": ASSISTANT_RULE_INTENT_MIN_CONFIDENCE,
    }
    try:
        resolved = intent_resolver(context) if intent_resolver else _classify_assistant_intent_with_llm(context)
    except Exception:
        resolved = None
    if not isinstance(resolved, Mapping):
        if rule_intent != "fallback":
            return {
                "intent": rule_intent,
                "source": "rule",
                "confidence": rule_confidence,
                "reason": f"{rule_reason or '规则已命中'}；LLM 意图兜底未返回可用结果。",
            }
        return {
            "intent": "fallback",
            "source": "rule",
            "confidence": 0.0,
            "reason": "规则未命中，模型也未返回可用结果。",
        }

    intent = str(resolved.get("intent") or "fallback").strip()
    if intent not in ASSISTANT_ALLOWED_INTENTS:
        intent = "fallback"
    llm_confidence = _coerce_confidence(resolved.get("confidence")) or 0.0
    if llm_confidence < ASSISTANT_LLM_INTENT_MIN_CONFIDENCE:
        if rule_intent != "fallback":
            return {
                "intent": rule_intent,
                "source": "rule",
                "confidence": rule_confidence,
                "reason": f"{rule_reason or '规则已命中'}；LLM 意图置信度较低，保留规则结果。",
            }
        intent = "fallback"
    return {
        "intent": intent,
        "source": "llm",
        "confidence": llm_confidence,
        "reason": str(resolved.get("reason") or "模型辅助识别意图。").strip(),
    }


def _build_assistant_retrieval_bundle(
    *,
    message: str,
    workflow_summary: Mapping[str, Any],
    agent_recommendation: Mapping[str, Any] | None,
    template_recommendation: Mapping[str, Any] | None,
    rag_preview: Mapping[str, Any] | None,
    active_view: str | None,
    active_step: str | None,
    summary: Mapping[str, Any],
    intent: str,
) -> dict[str, Any]:
    project = _dict(workflow_summary.get("project"))
    stats = _dict(workflow_summary.get("stats"))
    context_advice = _context_advice(active_view, active_step, workflow_summary)
    project_overview = _dict(_build_assistant_intent_context(
        message=message,
        workflow_summary=workflow_summary,
        agent_recommendation=agent_recommendation,
        template_recommendation=template_recommendation,
        rag_preview=rag_preview,
        active_view=active_view,
        active_step=active_step,
        summary=summary,
    ).get("project_overview"))

    retrieved_context: list[dict[str, Any]] = []
    retrieved_context.append(
        {
            "type": "project_summary",
            "title": str(project.get("name") or summary.get("project_name") or "当前项目"),
            "content": summary.get("summary"),
        }
    )
    if project_overview:
        retrieved_context.append(
            {
                "type": "project_overview",
                "title": "项目基本信息",
                "content": "；".join(f"{key}：{value}" for key, value in project_overview.items() if value),
            }
        )
    outline_preview = _list(workflow_summary.get("outline_preview"))
    if outline_preview:
        outline_nodes = _flatten_outline_preview(outline_preview)[:5]
        if outline_nodes:
            outline_lines = []
            for node in outline_nodes:
                outline_lines.append(f"{node.get('path_text') or node.get('title')}")
            retrieved_context.append(
                {
                    "type": "outline_preview",
                    "title": "目录预览",
                    "content": "；".join(str(line) for line in outline_lines if line),
                }
            )
    generation_units = _list(workflow_summary.get("generation_units"))
    if generation_units:
        generated_count = sum(1 for item in generation_units if _generation_unit_is_generated(item))
        failed_count = sum(1 for item in generation_units if _generation_unit_is_failed(item))
        retrieved_context.append(
            {
                "type": "generation_status",
                "title": "正文生成状态",
                "content": f"生成单元 {len(generation_units)} 个，已生成 {generated_count} 个，失败 {failed_count} 个。",
            }
        )
    if context_advice:
        retrieved_context.append(
            {
                "type": "page_advice",
                "title": context_advice.get("title") or "当前页面提示",
                "content": context_advice.get("text") or "",
            }
        )
    if int(stats.get("score_points") or 0) > 0:
        retrieved_context.append(
            {
                "type": "score_points",
                "title": "评分点概况",
                "content": f"识别到 {int(stats.get('score_points') or 0)} 个评分点，复核项 {int(stats.get('review_items') or 0)} 个，招标文件 {int(stats.get('tender_files') or 0)} 份。",
            }
        )
    for item in _assistant_rag_evidence_items(rag_preview, limit=3):
        retrieved_context.append(
            {
                "type": "rag_evidence",
                "title": item.get("title"),
                "content": item.get("citation") or item.get("preview"),
            }
        )
    if template_recommendation:
        templates = _list(_dict(template_recommendation).get("recommendations"))
        if templates:
            best = _dict(templates[0])
            retrieved_context.append(
                {
                    "type": "template_candidate",
                    "title": str(best.get("name") or "推荐模板"),
                    "content": str(best.get("reason") or "项目类型和评分点较匹配"),
                }
            )
    materials = _list(_dict(rag_preview).get("results"))
    if intent in {"generation", "generation_summary", "review_report", "risk"} and materials:
        retrieved_context.append(
            {
                "type": "rag_result_count",
                "title": "参考资料命中",
                "content": f"命中 {len(materials)} 条参考资料，可用于写法增强和风险复核。",
            }
        )
    retrieved_context = build_lightweight_retrieval_context(
        message=message,
        intent=intent,
        base_context=retrieved_context,
        rag_preview=rag_preview,
        limit=8,
    )
    return {
        "retrieved_context": retrieved_context,
        "retrieval_strategy": "project_context + assistant_knowledge_bm25 + rag_preview + rule_rerank",
        "intent": intent,
        "project_name": str(project.get("name") or summary.get("project_name") or "当前项目"),
        "project_type": _project_type_label(project.get("project_type")),
        "project_summary": summary.get("summary"),
    }


def _build_assistant_intent_context(
    *,
    message: str,
    workflow_summary: Mapping[str, Any],
    agent_recommendation: Mapping[str, Any] | None,
    template_recommendation: Mapping[str, Any] | None,
    rag_preview: Mapping[str, Any] | None,
    active_view: str | None,
    active_step: str | None,
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    project = _dict(workflow_summary.get("project"))
    stats = _dict(workflow_summary.get("stats"))
    parse_review = _dict(workflow_summary.get("parse_review_summary"))
    project_rows = [_dict(item) for item in _list(parse_review.get("project_info")) if isinstance(item, Mapping)]
    project_info = {
        str(item.get("label") or "").strip(): str(item.get("value") or "").strip()
        for item in project_rows
        if item.get("label") and item.get("value")
    }
    top_rag = []
    for item in _assistant_rag_evidence_items(rag_preview, limit=3):
        top_rag.append(
            {
                "title": item.get("title"),
                "knowledge_type_label": item.get("knowledge_type_label"),
                "citation": item.get("citation"),
            }
        )
    templates = []
    for item in _list(_dict(template_recommendation).get("recommendations"))[:3]:
        data = _dict(item)
        templates.append(
            {
                "name": data.get("name"),
                "reason": data.get("reason"),
                "project_type": data.get("project_type"),
            }
        )
    return {
        "message": message,
        "project_name": str(project.get("name") or summary.get("project_name") or "当前项目"),
        "project_type": _project_type_label(project.get("project_type")),
        "project_overview": {
            "项目名称": project_info.get("项目名称") or "",
            "项目类型": project_info.get("项目类型") or "",
            "建设地点": project_info.get("建设地点") or "",
            "建设规模": project_info.get("建设规模") or "",
            "招标范围": project_info.get("招标范围") or "",
            "工期要求": project_info.get("工期要求") or "",
            "质量要求": project_info.get("质量要求") or "",
            "安全文明要求": project_info.get("安全文明要求") or "",
        },
        "stats": {
            "score_points": int(stats.get("score_points") or 0),
            "review_items": int(stats.get("review_items") or 0),
            "tender_files": int(stats.get("tender_files") or 0),
            "estimated_chapters": int(stats.get("estimated_chapters") or 0),
            "excellent_bid_files": int(stats.get("excellent_bid_files") or 0),
        },
        "active_view": active_view or "projects",
        "active_step": active_step or "",
        "assistant_scope": ASSISTANT_SCOPE_TEXT,
        "assistant_pages": {
            "title": _context_advice(active_view, active_step, workflow_summary).get("title"),
            "text": _context_advice(active_view, active_step, workflow_summary).get("text"),
        },
        "allowed_intents": sorted(ASSISTANT_ALLOWED_INTENTS),
        "candidate_templates": templates,
        "candidate_rag": top_rag,
        "project_sources": summary.get("sources", []),
        "has_rag_preview": bool(_list(_dict(rag_preview).get("results"))),
    }


def _classify_assistant_intent_with_llm(context: Mapping[str, Any]) -> dict[str, Any] | None:
    config = llm_config(task_key=ASSISTANT_LLM_TASK_KEY)
    if not config.api_key:
        return None
    system_prompt = (
        "你是建设工程技术标助手的意图识别器。"
        "只需要在给定的意图里选择一个，并返回 JSON。"
        "判断范围仅限当前编标项目：项目概况/行业/类型、项目进度、下一步、评分点、目录、正文生成、Word 复核、模板、参考资料、风险提示、当前页面说明、模型配置排障。"
        "如果问题是项目外闲聊、日常问答或与编标无关，输出 fallback。"
        "注意：像“这个项目是什么行业”“这个项目属于什么类型”“这是个什么项目”都应归为 project_overview。"
        "返回格式：{\"intent\":\"...\",\"confidence\":0.0-1.0,\"reason\":\"一句话\"}。"
    )
    raw = call_openai_json(
        config=config,
        task_key=ASSISTANT_LLM_TASK_KEY,
        system_prompt=system_prompt,
        user_input=json.dumps(context, ensure_ascii=False),
    )
    parsed = parse_json_response(raw)
    if not isinstance(parsed, Mapping):
        return None
    return {
        "intent": parsed.get("intent"),
        "confidence": parsed.get("confidence"),
        "reason": parsed.get("reason"),
    }


def _maybe_rewrite_assistant_answer_with_llm(
    *,
    message: str,
    intent: str,
    base_answer: str,
    retrieval_bundle: Mapping[str, Any],
    active_view: str | None,
    active_step: str | None,
    workflow_summary: Mapping[str, Any],
    summary: Mapping[str, Any],
    answer_resolver: AssistantAnswerResolver | None = None,
) -> dict[str, Any] | None:
    if intent not in ASSISTANT_LLM_ANSWER_INTENTS:
        return {"answer": base_answer, "source": "rule", "confidence": 1.0, "reason": "当前意图使用固定模板回答。"}
    if _safe_truthy(os.getenv("ASSISTANT_ENABLE_LLM_ANSWER", "1")) is False:
        return {"answer": base_answer, "source": "rule", "confidence": 1.0, "reason": "LLM 回答增强已关闭。"}
    context = {
        "message": message,
        "intent": intent,
        "base_answer": base_answer,
        "retrieval_bundle": retrieval_bundle,
        "project_context": {
            "active_view": active_view or "projects",
            "active_step": active_step or "",
            "workflow_summary": {
                "project": _dict(workflow_summary.get("project")),
                "stats": _dict(workflow_summary.get("stats")),
                "project_name": summary.get("project_name"),
                "state_label": summary.get("state_label"),
            },
        },
    }
    try:
        resolved = answer_resolver(context) if answer_resolver else _rewrite_assistant_answer_with_llm(context)
    except Exception:
        resolved = None
    if not isinstance(resolved, Mapping):
        return {"answer": base_answer, "source": "rule", "confidence": 1.0, "reason": "LLM 回答增强未返回有效结果。"}
    answer = str(resolved.get("answer") or "").strip()
    if not answer:
        return {"answer": base_answer, "source": "rule", "confidence": 1.0, "reason": "LLM 回答为空。"}
    return {
        "answer": answer,
        "source": "llm",
        "confidence": _coerce_confidence(resolved.get("confidence")) or 0.0,
        "reason": str(resolved.get("reason") or "基于项目上下文和智库依据生成。").strip(),
    }


def _rewrite_assistant_answer_with_llm(context: Mapping[str, Any]) -> dict[str, Any] | None:
    config = llm_config(task_key=ASSISTANT_LLM_ANSWER_TASK_KEY)
    if not config.api_key:
        return None
    system_prompt = (
        "你是建设工程技术标项目小助手的回答器。"
        "只能围绕当前编标项目回答，不要扩展到闲聊或通用百科。"
        "请根据给定的项目上下文、检索到的内容和基础回答，输出更自然、更简洁的中文答案。"
        "如果问题超出编标项目范围，就直接输出边界提示。"
        "如果是项目概况问题，优先回答项目行业、类型、地点、规模、评分点、流程状态等。"
        "不要编造未给出的项目事实，不要泄露长段原文。"
        "返回格式：{\"answer\":\"...\",\"confidence\":0.0-1.0,\"reason\":\"一句话\"}。"
    )
    raw = call_openai_json(
        config=config,
        task_key=ASSISTANT_LLM_ANSWER_TASK_KEY,
        system_prompt=system_prompt,
        user_input=json.dumps(context, ensure_ascii=False),
    )
    parsed = parse_json_response(raw)
    if not isinstance(parsed, Mapping):
        return None
    return {
        "answer": parsed.get("answer"),
        "confidence": parsed.get("confidence"),
        "reason": parsed.get("reason"),
    }


def _coerce_confidence(value: Any) -> float | None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if confidence < 0:
        return 0.0
    if confidence > 1:
        return 1.0
    return confidence


def _safe_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text not in {"", "0", "false", "no", "off"}


def _assistant_llm_intent_enabled() -> bool:
    return _safe_truthy(os.getenv("ASSISTANT_ENABLE_LLM_INTENT", "1"))


def _assistant_llm_intent_force_enabled() -> bool:
    return _safe_truthy(os.getenv("ASSISTANT_FORCE_LLM_INTENT", "0"))


def _detect_intent(message: str) -> str:
    return classify_assistant_intent(message).intent


def _is_project_overview_question(text: str) -> bool:
    project_tokens = ["项目", "工程", "标书", "招标"]
    overview_tokens = [
        "是什么",
        "什么行业",
        "行业",
        "类型",
        "类别",
        "概况",
        "基本信息",
        "项目名称",
        "工程名称",
        "建设地点",
        "建设规模",
        "招标范围",
        "工期",
        "质量要求",
        "安全文明",
    ]
    if any(token in text for token in ["行业", "类型", "类别", "概况", "基本信息", "建设地点", "建设规模", "招标范围", "工期", "质量要求"]):
        return any(token in text for token in project_tokens)
    if any(
        phrase in text
        for phrase in [
            "这是什么项目",
            "这是啥项目",
            "这个项目是什么",
            "项目是什么",
            "这个项目是什么行业",
            "这个项目属于什么行业",
            "这是个什么行业的项目",
            "这个项目是什么类型",
            "这个项目属于什么类型",
        ]
    ):
        return True
    return any(token in text for token in project_tokens) and any(token in text for token in overview_tokens)


def _next_action_answer(action: Mapping[str, Any], active_step: str | None, workflow_summary: Mapping[str, Any]) -> str:
    stats = _dict(workflow_summary.get("stats"))
    base = f"建议下一步是：{action.get('title') or '查看项目状态'}。原因：{action.get('reason') or '需要先确认当前流程位置。'}"
    step_hint = _context_advice("projects", active_step, workflow_summary)
    extra = []
    if active_step == "parse" and int(stats.get("review_items") or 0) > 0:
        extra.append("当前有复核项，先确认评分点原文、分值和适用范围。")
    if active_step == "generate":
        extra.append("建议先生成 1-3 个典型章节，不要一上来全量重跑。")
    if active_step == "review":
        extra.append("最终 Word 成稿、对外提交和发布必须人工确认。")
    return f"{base} 当前步骤提示：{step_hint['text']}" + (f" {' '.join(extra)}" if extra else "")


def _project_overview_answer(workflow_summary: Mapping[str, Any], summary: Mapping[str, Any]) -> str:
    project = _dict(workflow_summary.get("project"))
    stats = _dict(workflow_summary.get("stats"))
    parse_review = _dict(workflow_summary.get("parse_review_summary"))
    project_rows = [_dict(item) for item in _list(parse_review.get("project_info")) if isinstance(item, Mapping)]
    project_info = {
        str(item.get("label") or "").strip(): str(item.get("value") or "").strip()
        for item in project_rows
        if item.get("label") and item.get("value")
    }
    project_name = project_info.get("项目名称") or str(project.get("name") or summary.get("project_name") or "当前项目")
    project_type = project_info.get("项目类型") or _project_type_label(project.get("project_type"))
    industry = _project_industry_label(project_type, project_info)
    details = [
        f"项目名称：{project_name}",
        f"行业/项目类型：{industry}（系统项目类型：{project_type}）",
    ]
    for label in ["建设地点", "建设规模", "招标范围", "工期要求", "质量要求", "安全文明要求"]:
        value = project_info.get(label)
        if value:
            details.append(f"{label}：{value}")
    if len(details) <= 2:
        details.append("招标解析还没有产出完整项目概况，当前只能根据项目创建信息判断。")
    details.append(
        f"当前流程数据：招标文件 {int(stats.get('tender_files') or 0)} 份，"
        f"评分点 {int(stats.get('score_points') or 0)} 个，"
        f"待复核项 {int(stats.get('review_items') or 0)} 个。"
    )
    return "当前项目概况：" + "；".join(details) + "。"


def _project_industry_label(
    project_type_label: str,
    project_info: Mapping[str, str],
    *extra_texts: object,
) -> str:
    combined = " ".join(str(value) for value in project_info.values())
    combined = " ".join([combined, *[str(value) for value in extra_texts if value]])
    if any(token in combined for token in ["EPC", "设计采购施工", "工程总承包"]):
        return "建设工程 EPC / 工程总承包"
    if any(token in combined for token in ["房建", "房屋建筑", "住宅", "办公楼", "综合楼", "厂房", "学校", "医院"]):
        return "建设工程 - 房屋建筑"
    if any(token in combined for token in ["市政", "道路", "桥梁", "管网", "排水", "给水"]):
        return "建设工程 - 市政工程"
    if any(token in combined for token in ["装饰", "装修", "幕墙"]):
        return "建设工程 - 装饰装修"
    if "EPC" in project_type_label:
        return "建设工程 EPC / 工程总承包"
    if any(token in project_type_label for token in ["施工", "总承包", "construction"]):
        return "建设工程 - 施工技术标"
    return "建设工程技术标"


def _score_points_answer(
    stats: Mapping[str, Any],
    score_points: list[Any],
    review_items: list[Any],
    workflow_summary: Mapping[str, Any],
) -> str:
    coverage = _score_coverage_summary(workflow_summary)
    confirmed = coverage["covered"] if coverage["has_outline"] else _confirmed_score_count(score_points)
    total = int(stats.get("score_points") or len(score_points) or 0)
    preview = "；".join(str(_dict(item).get("title") or "") for item in score_points[:5] if isinstance(item, Mapping))
    outline_count = len(_list(workflow_summary.get("outline_preview")))
    answer = f"当前识别到 {total} 个评分点，已确认或正文覆盖约 {confirmed} 个。"
    if preview:
        answer += f" 前几个重点包括：{preview}。"
    if outline_count or coverage["has_outline"]:
        answer += (
            f" 当前目录预览有 {outline_count} 个一级节点，目录已承接 {coverage['outline_covered']} 个评分点，"
            f"正文已覆盖 {coverage['covered']} 个，待确认 {coverage['pending']} 个，未覆盖或需复核 {coverage['risk']} 个。"
        )
        focus = coverage["focus_items"]
        if focus:
            answer += " 优先复核：" + "；".join(focus[:3]) + "。"
    if review_items:
        answer += f" 还有 {len(review_items)} 个复核项，建议先处理复核项，再进入批量正文生成。"
    else:
        answer += " 暂无明显阻塞复核项，但分值、原文和响应章节仍建议人工扫一遍。"
    return answer


def _review_report_answer(workflow_summary: Mapping[str, Any]) -> str:
    stats = _dict(workflow_summary.get("stats"))
    review_items = _list(workflow_summary.get("review_items"))
    generation_units = _list(workflow_summary.get("generation_units"))
    artifacts = _dict(workflow_summary.get("artifacts"))
    score_points = _list(workflow_summary.get("score_points"))
    coverage = _score_coverage_summary(workflow_summary)
    total_scores = int(stats.get("score_points") or len(score_points) or 0)
    confirmed_scores = coverage["covered"] if coverage["has_outline"] else _confirmed_score_count(score_points)
    failed_units = sum(1 for item in generation_units if _generation_unit_is_failed(item))
    weak_units = sum(1 for item in generation_units if _generation_unit_is_weak(item))
    word_ready = bool(_dict(artifacts.get("word_draft_docx")) or _dict(artifacts.get("word_draft_json")))
    lines = [
        f"评分点覆盖：{confirmed_scores}/{total_scores} 个已确认或正文覆盖，目录已承接 {coverage['outline_covered']} 个，未覆盖或需复核 {coverage['risk']} 个。",
        f"章节质量：发现 {weak_units} 个偏弱或待补齐小节包，{failed_units} 个失败小节包。",
        f"人工确认：当前复核项 {len(review_items)} 个，重点看评分点原文、章节承接和生成失败项。",
        "表格图片：生成 Word 后重点检查图片缺失、表格行高、题注和跨页显示。",
        f"Word 状态：{'已生成初稿，可进入 OnlyOffice 复核' if word_ready else '尚未生成 Word 初稿，先完成正文生成和刷新初稿'}。",
    ]
    if coverage["focus_items"]:
        lines.append("优先复核：" + "；".join(coverage["focus_items"][:3]))
    return "AI 复核报告摘要：" + "；".join(lines) + "。"


def _outline_answer(workflow_summary: Mapping[str, Any]) -> str:
    outline_nodes = _list(workflow_summary.get("outline_preview"))
    score_points = _list(workflow_summary.get("score_points"))
    answer = "目录复核建议重点看三件事：一级目录是否覆盖评分点，二三级目录是否能承接施工方案，用户已调整的目录不要被自动覆盖。"
    if outline_nodes or score_points:
        answer += f" 当前目录一级节点约 {len(outline_nodes)} 个，评分点 {len(score_points)} 个，建议逐项核对是否一一响应。"
    answer += " 如果一级标题来自评分点原文，二三级标题更适合让 AI 补强；如果一级标题本身不准，应先人工修正。"
    return answer


def _generation_answer(workflow_summary: Mapping[str, Any], rag_preview: Mapping[str, Any] | None) -> str:
    units = _list(workflow_summary.get("generation_units"))
    materials = _list(_dict(rag_preview).get("results"))
    total = len(units)
    failed = sum(1 for item in units if _generation_unit_is_failed(item))
    generated = sum(1 for item in units if str(_dict(item).get("status") or "").lower() in {"completed", "generated", "succeeded"})
    answer = (
        f"正文生成建议先选 1-3 个典型章节试跑，确认素材、语气和格式后再扩大范围。"
        f"当前生成单元 {total} 个，已生成约 {generated} 个，失败 {failed} 个。"
        "结构、评分点、表格图片由原流程稳住，参考资料更适合增强措施写法、法规依据和企业风格。"
    )
    if materials:
        answer += " 当前已有可用智库资料，可结合智库依据补强措施写法、法规依据和企业表达。"
    else:
        answer += " 当前没有明确命中的参考资料，建议补充优秀标书、法规规范、企业制度或评审办法后再做重点章节增强。"
    return answer


def _generation_summary_answer(workflow_summary: Mapping[str, Any]) -> str:
    report = _dict(workflow_summary.get("generation_report"))
    metrics = _dict(report.get("metrics"))
    latest_job = _dict(report.get("latest_job"))
    if not report.get("available"):
        return "当前还没有可汇总的正文生成或 Word 刷新记录。建议先确认目录，再选择 1-3 个典型章节试跑。"

    duration = _duration_text(metrics.get("duration_seconds")) if metrics.get("duration_seconds") else "暂未记录"
    stage_timing_text = _generation_stage_timing_text(_list(metrics.get("stage_timings")))
    token_text = (
        f"估算 token 约 {int(metrics.get('estimated_total_tokens') or 0)}"
        if metrics.get("token_estimate_available")
        else "token 暂无精确统计"
    )
    lines = [
        f"最近任务：{latest_job.get('job_label') or '生成任务'}，状态：{report.get('status_label') or '未明确'}",
        f"任务总耗时：{duration}",
        stage_timing_text,
        f"模型调用：{int(metrics.get('llm_call_count') or 0)} 次，模型失败 {int(metrics.get('llm_failed_count') or 0)} 次，{token_text}",
        f"正文小节：已生成 {int(metrics.get('chapters_generated') or 0)}/{int(metrics.get('chapters_total') or 0)}，失败 {int(metrics.get('chapters_failed') or 0)} 个",
        f"评分点：已覆盖 {int(metrics.get('score_points_covered') or 0)}/{int(metrics.get('score_points_total') or 0)}，需复核 {int(metrics.get('score_points_risk') or 0)} 个",
        f"Word 初稿：{'已就绪' if metrics.get('word_ready') else '待刷新'}",
    ]
    actions = _list(report.get("next_actions"))
    if actions:
        lines.append(f"下一步：{actions[0]}")
    return "生成小结：" + "；".join(line for line in lines if line) + "。"


def _generation_stage_timing_text(stage_timings: list[Any]) -> str:
    parts: list[str] = []
    for key, label in [
        ("tender_parse", "解析确认"),
        ("outline_generation", "生成目录"),
        ("chapter_llm_generation", "生成正文"),
        ("chapter_aggregate_refresh", "Word 整理"),
    ]:
        item = next((_dict(stage) for stage in stage_timings if _dict(stage).get("key") == key), {})
        duration = _duration_text(item.get("duration_seconds")) if item.get("duration_seconds") else ""
        if duration:
            parts.append(f"{label} {duration}")
    return "分步耗时：" + "，".join(parts) if parts else ""


def _word_answer(workflow_summary: Mapping[str, Any]) -> str:
    artifacts = _dict(workflow_summary.get("artifacts"))
    has_word = bool(_dict(artifacts.get("word_draft_docx")) or _dict(artifacts.get("word_draft_json")))
    prefix = "Word 初稿已生成。" if has_word else "Word 初稿尚未生成。"
    return (
        prefix
        + "复核建议按顺序检查：标题层级和目录页码、评分点响应位置、表格跨页和行高、图片缺失和题注、页眉页脚、OnlyOffice 保存结果。"
        + "最终稿确认、对外提交和共享必须人工完成。"
    )


def _risk_answer(summary: Mapping[str, Any], rag_preview: Mapping[str, Any] | None) -> str:
    risks = summary.get("risks") or ["当前暂无明显高风险项，但最终成稿仍需人工复核。"]
    materials = _list(_dict(rag_preview).get("results"))
    answer = "当前主要风险：" + "；".join(str(item) for item in risks[:4]) + "。"
    if materials:
        answer += " 当前已命中法规、规范或制度类参考资料，可按下方依据逐项复核。"
    else:
        answer += " 当前没有命中法规、规范或制度类智库资料，建议先补充后再做合规风险复核。"
    answer += " 该结论是编标风险提示，不替代法务、合约或专家最终审查。"
    return answer


def _duration_text(value: object) -> str:
    try:
        seconds = float(value or 0)
    except (TypeError, ValueError):
        return "暂未记录"
    if seconds <= 0:
        return "暂未记录"
    if seconds < 60:
        return f"{seconds:.1f} 秒" if seconds < 10 else f"{seconds:.0f} 秒"
    minutes = int(seconds // 60)
    remain = int(round(seconds % 60))
    return f"{minutes} 分 {remain} 秒"


def _highlight_lines(summary: Mapping[str, Any]) -> list[str]:
    stats = _dict(summary.get("stats"))
    highlights = []
    if int(stats.get("score_points") or 0) > 0:
        highlights.append(f"已识别 {stats.get('score_points')} 个技术评分点，可用于目录和正文约束。")
    if int(stats.get("estimated_chapters") or 0) > 0:
        highlights.append(f"已估算约 {stats.get('estimated_chapters')} 个正文生成单元。")
    if int(stats.get("excellent_bid_files") or 0) > 0:
        highlights.append(f"项目内已有 {stats.get('excellent_bid_files')} 份优秀标书资料。")
    return highlights[:4]


def _risk_lines(summary: Mapping[str, Any], recommendation: Mapping[str, Any] | None) -> list[str]:
    risks = []
    stats = _dict(summary.get("stats"))
    if int(stats.get("score_points") or 0) == 0 and int(stats.get("tender_files") or 0) > 0:
        risks.append("尚未识别到评分点，目录和正文生成缺少核心约束。")
    if int(stats.get("review_items") or 0) > 0:
        risks.append(f"当前存在 {stats.get('review_items')} 个复核项，建议处理后再扩大生成范围。")
    for item in _list(_dict(recommendation).get("required_approvals"))[:3]:
        if isinstance(item, Mapping) and item.get("title"):
            risks.append(str(item["title"]))
    return risks[:5]


def _ai_cards(summary: Mapping[str, Any], recommendation: Mapping[str, Any] | None) -> list[dict[str, str]]:
    stats = _dict(summary.get("stats"))
    action = _dict(_dict(recommendation).get("recommended_next_action"))
    cards = [
        {
            "title": "流程判断",
            "value": str(action.get("title") or "查看项目状态"),
            "hint": str(action.get("reason") or "结合当前项目产物判断下一步。"),
        },
        {
            "title": "评分点约束",
            "value": f"{int(stats.get('score_points') or 0)} 个评分点",
            "hint": "目录和正文应围绕技术标评分点展开。",
        },
        {
            "title": "素材增强",
            "value": f"{int(stats.get('excellent_bid_files') or 0)} 份参考资料",
            "hint": "生成正文和风险提示时引用标书、法规、规范、制度和评审办法摘要。",
        },
    ]
    return cards


def _assistant_rag_evidence_items(rag_preview: Mapping[str, Any] | None, limit: int = 3) -> list[dict[str, Any]]:
    materials = sorted(_list(_dict(rag_preview).get("results")), key=_rag_evidence_sort_key)
    items: list[dict[str, Any]] = []
    for index, material in enumerate(materials[: max(limit, 0)], start=1):
        item = _dict(material)
        title = str(item.get("title") or item.get("section_title") or "未命名资料")
        source_title = str(item.get("source_title") or "未标记来源")
        knowledge_label = str(item.get("knowledge_type_label") or "参考资料")
        page_range = _page_range_text(item.get("start_page"), item.get("end_page"))
        section_path = [str(value) for value in _list(item.get("section_path")) if value]
        citation_parts = [knowledge_label, f"《{source_title}》"]
        if page_range:
            citation_parts.append(page_range)
        citation = " · ".join(citation_parts)
        if section_path:
            citation = f"{citation} · {' > '.join(section_path)}"
        preview = str(
            item.get("reason")
            or item.get("summary")
            or item.get("text_preview")
            or "可作为当前章节写作或风险复核依据。"
        )
        items.append(
            {
                "index": index,
                "title": title,
                "source_title": source_title,
                "source_type_label": str(item.get("source_type_label") or "投标智库"),
                "knowledge_type_label": knowledge_label,
                "page_range": page_range,
                "section_path": section_path,
                "citation": citation,
                "preview": preview,
                "score": item.get("score"),
            }
        )
    return items


def _assistant_user_name(account_context: Mapping[str, Any] | None) -> str:
    account = _dict(account_context)
    role = str(account.get("role") or account.get("account_role") or "").strip().lower()
    display_name = str(account.get("display_name") or account.get("account_display_name") or "").strip()
    username = str(account.get("username") or "").strip()
    if role == "admin":
        return "神"
    if display_name:
        return _polite_name(display_name)
    if username:
        return _polite_name(username)
    return "您"


def _polite_name(value: str) -> str:
    name = value.strip()
    if not name:
        return "您"
    if name.endswith(("先生", "女士", "老师", "经理", "总")):
        return name
    if len(name) <= 2:
        return f"{name}先生"
    return name


def _rag_evidence_sort_key(material: Any) -> tuple[int, float]:
    item = _dict(material)
    knowledge_type = str(item.get("knowledge_type") or "other")
    try:
        score = float(item.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    return (RAG_EVIDENCE_PRIORITY.get(knowledge_type, 6), -score)


def _append_evidence_clause(answer: str, evidence: list[dict[str, Any]], limit: int = 2) -> str:
    citations = [
        str(item.get("citation") or item.get("title") or "").strip()
        for item in evidence[: max(limit, 0)]
        if isinstance(item, Mapping) and str(item.get("citation") or item.get("title") or "").strip()
    ]
    if not citations:
        return answer
    suffix = "智库依据：" + "；".join(citations)
    if answer.endswith(("。", "！", "？", ".", "!", "?")):
        return f"{answer}{suffix}"
    return f"{answer}。{suffix}"


def _queue_preflight_answer(
    workflow_summary: Mapping[str, Any],
    active_step: str | None,
    rag_preview: Mapping[str, Any] | None,
) -> str:
    units = _list(workflow_summary.get("generation_units"))
    unfinished = sum(1 for item in units if _generation_unit_is_pending(item))
    failed = sum(1 for item in units if _generation_unit_is_failed(item))
    evidence = _assistant_rag_evidence_items(rag_preview, limit=2)
    step = str(active_step or "")
    lines: list[str] = []
    if step == "parse":
        lines.append("先确认上传的是最新招标文件，避免重复解析旧版本。")
        if int(_dict(workflow_summary.get("stats")).get("review_items") or 0) > 0:
            lines.append("有复核项时先人工确认评分点原文和分值，再进入下一步。")
    elif step == "outline":
        lines.append("先确认评分点已对齐，目录不要覆盖已人工调整的内容。")
    elif step == "generate":
        lines.append(f"优先挑 {min(3, max(unfinished, 1))} 个典型小节试跑，不要一上来全量排队。")
        lines.append("结构、评分点和表格图片先用原流程稳住，施工措施、法规依据和企业表达再用智库依据补强。")
        if failed:
            lines.append(f"当前还有 {failed} 个失败小节，建议先重试失败项。")
    elif step == "review":
        lines.append("先刷新 Word 初稿，再检查目录页码、表格图片和页眉页脚。")
    else:
        lines.append("先上传招标文件，再进入解析和目录流程。")
    if evidence:
        lines.append(f"可先看：{evidence[0]['citation']}")
    return "排队前建议：" + "；".join(lines) + "。"


def _page_range_text(start_page: Any, end_page: Any) -> str:
    try:
        start = int(start_page or 0)
    except (TypeError, ValueError):
        start = 0
    try:
        end = int(end_page or 0)
    except (TypeError, ValueError):
        end = 0
    if start > 0 and end > 0 and end != start:
        return f"第 {start}-{end} 页"
    if start > 0:
        return f"第 {start} 页"
    return ""


def _confirmed_score_count(score_points: list[Any]) -> int:
    count = 0
    for item in score_points:
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or item.get("confirmation_status") or "").lower()
        if item.get("confirmed") is True or status in {"confirmed", "covered", "done", "已确认", "已覆盖"}:
            count += 1
    return count


def _score_coverage_summary(workflow_summary: Mapping[str, Any]) -> dict[str, Any]:
    backend_coverage = _dict(workflow_summary.get("score_point_coverage"))
    if backend_coverage.get("schema_version") == "score_point_coverage_v1":
        backend_summary = _dict(backend_coverage.get("summary"))
        backend_items = [_dict(item) for item in _list(backend_coverage.get("items")) if isinstance(item, Mapping)]
        focus_items = [
            f"{item.get('title') or '未命名评分点'}（{item.get('generation_text') or item.get('status_label') or '待复核'}）"
            for item in backend_items
            if str(item.get("status_key") or "") in {"risk", "uncovered", "pending"}
        ]
        return {
            "total": int(backend_summary.get("total") or len(backend_items) or 0),
            "covered": int(backend_summary.get("covered") or 0),
            "pending": int(backend_summary.get("pending") or 0),
            "risk": int(backend_summary.get("risk") or 0),
            "outline_covered": int(backend_summary.get("outline_covered") or 0),
            "has_outline": bool(backend_summary.get("has_outline")),
            "has_generation_units": bool(backend_summary.get("has_generation_units")),
            "focus_items": focus_items,
            "items": backend_items,
        }

    score_points = _list(workflow_summary.get("score_points"))
    outline_items = _flatten_outline_preview(_list(workflow_summary.get("outline_preview")))
    generation_units = _list(workflow_summary.get("generation_units"))
    review_count = int(_dict(workflow_summary.get("stats")).get("review_items") or 0)
    items = []
    for index, score_point in enumerate(score_points):
        data = _dict(score_point)
        outline_match = _find_outline_match(data, outline_items)
        generation_matches = _find_generation_matches(data, outline_match, generation_units)
        generated = sum(1 for item in generation_matches if _generation_unit_is_generated(item))
        failed = sum(1 for item in generation_matches if _generation_unit_is_failed(item))
        total_generation = len(generation_matches)
        review_required = "复核" in str(data.get("status") or "") or index < review_count
        if outline_items and not outline_match:
            status = "risk"
            reason = "未找到承接目录"
        elif failed:
            status = "risk"
            reason = f"正文生成失败 {failed} 个"
        elif outline_match and total_generation and generated == total_generation:
            status = "covered"
            reason = f"正文已生成 {generated}/{total_generation}"
        elif outline_match:
            status = "pending"
            reason = "目录已承接，正文待生成或待确认"
        elif review_required:
            status = "risk"
            reason = "解析结果需要人工复核"
        else:
            status = "pending"
            reason = "等待目录和正文承接"
        items.append(
            {
                "title": str(data.get("title") or f"评分点 {index + 1}"),
                "status": status,
                "reason": reason,
                "outline": outline_match.get("path_text") if outline_match else "",
            }
        )

    covered = sum(1 for item in items if item["status"] == "covered")
    pending = sum(1 for item in items if item["status"] == "pending")
    risk = sum(1 for item in items if item["status"] == "risk")
    outline_covered = sum(1 for item in items if item.get("outline"))
    focus_items = [
        f"{item['title']}（{item['reason']}）"
        for item in items
        if item["status"] in {"risk", "pending"}
    ]
    return {
        "total": len(score_points),
        "covered": covered,
        "pending": pending,
        "risk": risk,
        "outline_covered": outline_covered,
        "has_outline": bool(outline_items),
        "has_generation_units": bool(generation_units),
        "focus_items": focus_items,
        "items": items,
    }


def _flatten_outline_preview(nodes: list[Any], parent_titles: list[str] | None = None, depth: int = 1) -> list[dict[str, Any]]:
    parent_titles = parent_titles or []
    result: list[dict[str, Any]] = []
    for raw_node in nodes:
        node = _dict(raw_node)
        if not node:
            continue
        title = str(node.get("title") or "").strip()
        path_titles = [*parent_titles, title] if title else [*parent_titles]
        children = _list(node.get("children"))
        result.append(
            {
                "node_id": str(node.get("node_id") or ""),
                "title": title,
                "depth": depth,
                "path_titles": path_titles,
                "path_text": " > ".join(path_titles),
                "descendant_node_ids": _collect_outline_node_ids(node),
            }
        )
        result.extend(_flatten_outline_preview(children, path_titles, depth + 1))
    return result


def _collect_outline_node_ids(node: Mapping[str, Any]) -> list[str]:
    ids = [str(node.get("node_id") or "")] if node.get("node_id") else []
    for child in _list(node.get("children")):
        ids.extend(_collect_outline_node_ids(_dict(child)))
    return ids


def _find_outline_match(score_point: Mapping[str, Any], outline_items: list[dict[str, Any]]) -> dict[str, Any]:
    title = str(score_point.get("title") or "")
    best: dict[str, Any] = {}
    best_score = 0.0
    for item in outline_items:
        score = max(_text_match_score(title, item.get("title")), _text_match_score(title, item.get("path_text")) * 0.96)
        if item.get("depth") == 1:
            score += 0.08
        if score > best_score:
            best = item
            best_score = score
    return best if best_score >= 0.48 else {}


def _find_generation_matches(
    score_point: Mapping[str, Any],
    outline_match: Mapping[str, Any],
    generation_units: list[Any],
) -> list[Mapping[str, Any]]:
    matches: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    node_ids = {str(item) for item in _list(outline_match.get("descendant_node_ids")) if item}
    path_titles = _list(outline_match.get("path_titles"))
    outline_top_title = str(path_titles[0]) if path_titles else str(outline_match.get("title") or "")
    outline_text = str(outline_match.get("path_text") or "")
    for index, raw_unit in enumerate(generation_units):
        unit = _dict(raw_unit)
        if not unit:
            continue
        key = str(unit.get("unit_id") or f"{unit.get('target_node_id') or ''}:{index}")
        target_id = str(unit.get("target_node_id") or "")
        chapter_path = " > ".join(str(item) for item in _list(unit.get("chapter_path")) if item)
        chapter_text = f"{chapter_path} {unit.get('chapter') or ''}"
        node_matched = bool(target_id and target_id in node_ids)
        if outline_match:
            text_matched = _text_match_score(outline_top_title or outline_text, chapter_text) >= 0.48 or _text_match_score(outline_text, chapter_text) >= 0.48
        else:
            text_matched = _text_match_score(str(score_point.get("title") or ""), chapter_text) >= 0.5
        if (node_matched or text_matched) and key not in seen:
            seen.add(key)
            matches.append(unit)
    return matches


def _normalize_match_text(value: object) -> str:
    text = str(value or "").lower().strip()
    text = re.sub(r"^\s*\d+(\.\d+)*[、.．\s-]*", "", text)
    return re.sub(r"[（）()【】\[\]《》<>、，。；;：:！!？?\s\"'“”‘’\-_.·]", "", text)


def _match_segments(value: object) -> list[str]:
    return [
        segment
        for segment in (_normalize_match_text(item) for item in re.split(r"[，。；;：:、\s\"'“”‘’（）()【】\[\]《》<>]+", str(value or "")))
        if len(segment) >= 3
    ][:8]


def _text_match_score(source_text: object, target_text: object) -> float:
    source = _normalize_match_text(source_text)
    target = _normalize_match_text(target_text)
    if not source or not target:
        return 0.0
    if source == target:
        return 1.0
    if len(source) >= 4 and source in target:
        return 0.92
    if len(target) >= 4 and target in source:
        return 0.82

    source_segments = _match_segments(source_text)
    target_segments = _match_segments(target_text)
    segment_hits = sum(1 for segment in source_segments if segment in target) + sum(1 for segment in target_segments if segment in source)
    if segment_hits:
        return min(0.78, segment_hits / max(2, len(source_segments) + len(target_segments)) + 0.34)

    source_chars = {char for char in source if re.match(r"[\u4e00-\u9fa5a-z0-9]", char)}
    target_chars = {char for char in target if re.match(r"[\u4e00-\u9fa5a-z0-9]", char)}
    if len(source_chars) < 6 or len(target_chars) < 6:
        return 0.0
    overlap = len(source_chars & target_chars)
    return overlap / min(len(source_chars), len(target_chars)) * 0.56


def _generation_unit_is_generated(item: Any) -> bool:
    data = _dict(item)
    status = str(data.get("status") or "").lower()
    return any(token in status for token in ["completed", "generated", "succeeded", "已生成", "已完成"])


def _generation_unit_is_failed(item: Any) -> bool:
    data = _dict(item)
    status = str(data.get("status") or "").lower()
    return "failed" in status or "error" in status or "失败" in status


def _generation_unit_is_weak(item: Any) -> bool:
    data = _dict(item)
    status = str(data.get("status") or "").lower()
    if _generation_unit_is_failed(data):
        return True
    if any(token in status for token in ["weak", "warning", "review", "待复核", "偏弱"]):
        return True
    warning_count = data.get("warning_count") or data.get("review_item_count") or data.get("issue_count")
    try:
        return int(warning_count or 0) > 0
    except (TypeError, ValueError):
        return False


def _context_advice(active_view: str | None, active_step: str | None, workflow_summary: Mapping[str, Any]) -> dict[str, str]:
    if active_view and active_view != "projects":
        module_map = {
            "rag": {
                "title": "投标智库",
                "text": "这里用于沉淀优秀标书、法律法规、技术规范、企业制度和评审办法。风险评估时优先引用法规、规范和制度类资料摘要。",
            },
            "templates": {
                "title": "投标模板模块",
                "text": "这里用于沉淀企业标准目录和章节结构。模板只做推荐和预览，覆盖用户已编辑内容前必须人工确认。",
            },
            "model": {
                "title": "模型配置模块",
                "text": "这里会影响解析、目录补强和正文生成的速度、成本和稳定性。修改模型、并发和超时时间前建议先记录原配置。",
            },
        }
        return module_map.get(active_view, {"title": "工作台模块", "text": "当前模块以查看和辅助说明为主，高风险动作仍需人工确认。"})

    stats = _dict(workflow_summary.get("stats"))
    step_map = {
        "upload": {
            "title": "第 1 步 上传资料",
            "text": "重点确认招标文件版本正确、文件能打开。法规规范、企业制度、评审办法和优秀标书建议到投标智库入库。",
        },
        "parse": {
            "title": "第 2 步 解析确认",
            "text": f"重点检查评分点和复核项。当前评分点 {int(stats.get('score_points') or 0)} 个，复核项 {int(stats.get('review_items') or 0)} 个。",
        },
        "outline": {
            "title": "第 3 步 生成目录",
            "text": "重点看一级目录是否覆盖评分点，二三级目录是否符合技术标写作习惯。用户修改过的目录不要自动覆盖。",
        },
        "generate": {
            "title": "第 4 步 生成正文",
            "text": "建议先选典型章节试跑。原算法负责评分点、结构、表格图片，智库依据负责措施写法、法规规范、制度口径和企业风格增强。",
        },
        "review": {
            "title": "第 5 步 Word 复核",
            "text": "重点检查标题层级、目录页码、表格图片、评分点响应和 OnlyOffice 保存结果。最终稿必须人工确认。",
        },
    }
    return step_map.get(active_step or "", {"title": "编标主流程", "text": "主流程一共五步：上传资料、解析确认、生成目录、生成正文、Word 复核。"})


def _project_type_label(value: object) -> str:
    if value == "epc":
        return "EPC"
    if value == "construction":
        return "施工总承包"
    return "自动识别"


def _dict(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
