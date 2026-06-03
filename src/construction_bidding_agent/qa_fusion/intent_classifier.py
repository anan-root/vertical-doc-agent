"""Intent classification for the platform-level bidding assistant."""

from __future__ import annotations

import re

from .schemas import PlatformAssistantIntent


def classify_platform_intent(message: str, *, active_view: str | None = None) -> PlatformAssistantIntent:
    text = _normalize(message)
    view = str(active_view or "").strip()
    if not text:
        return PlatformAssistantIntent("fallback", 0.0, reason="问题为空。")
    if _contains(text, ["你好", "您好", "hello", "hi", "你是谁", "介绍一下", "你能做什么", "能力边界"]):
        return PlatformAssistantIntent("daily_chat", 1.0, reason="日常问候或助手介绍。")
    if _contains(text, ["平台", "系统", "能做什么", "有哪些功能", "工作台", "模块"]):
        return PlatformAssistantIntent("platform_capability", 0.92, reason="询问平台能力或模块边界。")
    if _contains(text, ["标讯", "公告", "招标信息", "剑鱼", "实时", "机会"]):
        return PlatformAssistantIntent("bid_intelligence", 0.88, reason="询问标讯情报。")
    if _contains(text, ["企业风险", "企查查", "风险", "失信", "诉讼", "经营异常", "资质核验"]):
        return PlatformAssistantIntent("enterprise_risk", 0.86, reason="询问企业风险。")
    if _contains(text, ["供应商", "供应单位", "推荐供应", "分包", "材料商"]):
        return PlatformAssistantIntent("supplier_recommendation", 0.86, reason="询问供应商推荐。")
    if _contains(text, ["投标知识库", "知识库", "智库", "资料库", "优秀标书", "法规", "规范", "制度", "评审办法", "依据", "入库"]):
        return PlatformAssistantIntent("knowledge_base", 0.9, reason="询问投标知识库或资料依据。")
    if _contains(text, ["模板", "范本", "投标模板", "套用"]):
        return PlatformAssistantIntent("template", 0.86, reason="询问投标模板。")
    if _contains(text, ["编标", "技术标", "评分点", "目录", "正文", "word", "复核", "生成", "项目下一步"]):
        return PlatformAssistantIntent("bid_drafting", 0.86, reason="询问技术标编制。")
    if view in {"qa"}:
        return PlatformAssistantIntent("platform_qa", 0.65, reason="当前处于智能问答模块。")
    if view in {"rag"}:
        return PlatformAssistantIntent("knowledge_base", 0.65, reason="当前处于投标知识库模块。")
    if view in {"templates"}:
        return PlatformAssistantIntent("template", 0.65, reason="当前处于投标模板模块。")
    if view in {"bid-intel"}:
        return PlatformAssistantIntent("bid_intelligence", 0.65, reason="当前处于标讯情报模块。")
    if view in {"risk"}:
        return PlatformAssistantIntent("enterprise_risk", 0.65, reason="当前处于企业风险模块。")
    if view in {"suppliers"}:
        return PlatformAssistantIntent("supplier_recommendation", 0.65, reason="当前处于供应商推荐模块。")
    return PlatformAssistantIntent("fallback", 0.0, reason="平台规则未命中。")


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _contains(text: str, tokens: list[str]) -> bool:
    return any(token.lower() in text for token in tokens)

