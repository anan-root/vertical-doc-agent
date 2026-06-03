"""Intent classification for the bidding assistant."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class AssistantIntent:
    intent: str
    confidence: float
    source: str = "rule"
    reason: str = ""


DAILY_CHAT_GREETING_TOKENS = (
    "你好",
    "您好",
    "嗨",
    "早上好",
    "下午好",
    "晚上好",
    "hello",
    "hi",
)
DAILY_CHAT_THANKS_TOKENS = ("谢谢", "辛苦了", "多谢")
DAILY_CHAT_FAREWELL_TOKENS = ("再见", "拜拜", "回头见", "下次聊")
DAILY_CHAT_SELF_INTRO_TOKENS = (
    "你是谁",
    "介绍一下你",
    "介绍一下自己",
    "你能做什么",
    "能力边界",
    "边界",
)


def classify_assistant_intent(message: str) -> AssistantIntent:
    text = _normalize_query(message)
    if not text:
        return AssistantIntent("fallback", 0.0, reason="问题为空。")
    if _contains(text, DAILY_CHAT_GREETING_TOKENS + DAILY_CHAT_THANKS_TOKENS + DAILY_CHAT_FAREWELL_TOKENS + DAILY_CHAT_SELF_INTRO_TOKENS):
        return AssistantIntent("daily_chat", 1.0, reason="日常问候或身份介绍。")
    if _contains(text, ["这里", "这个页面", "这个模块", "当前页面", "当前步骤", "注意什么", "讲解"]):
        return AssistantIntent("context_help", 0.92, reason="询问当前页面或步骤说明。")
    if _contains(text, ["下一步", "建议", "该做", "做什么"]):
        return AssistantIntent("next_action", 0.95, reason="询问下一步操作。")
    if _is_project_overview_question(text):
        return AssistantIntent("project_overview", 0.92, reason="询问项目概况、行业或基础信息。")
    if _contains(text, ["进度", "状态", "到哪", "当前", "阶段"]):
        return AssistantIntent("progress", 0.82, reason="询问项目流程状态。")
    if _contains(text, ["复核报告", "质检", "质量报告", "ai 复核", "ai复核", "检查报告"]):
        return AssistantIntent("review_report", 0.93, reason="询问 AI 复核摘要。")
    if _contains(text, ["生成小结", "本次生成", "耗时", "token", "tokens", "模型调用", "失败项"]):
        return AssistantIntent("generation_summary", 0.93, reason="询问生成耗时、token 或任务小结。")
    if _contains(text, ["排队前", "提交前", "入队前", "开跑前", "队列前"]):
        return AssistantIntent("queue_preflight", 0.9, reason="询问任务提交前检查。")
    if _contains(text, ["模板", "范本", "套用"]):
        if _contains(text, ["覆盖", "边界", "直接", "能不能"]):
            return AssistantIntent("template_boundary", 0.9, reason="询问模板套用边界。")
        return AssistantIntent("template", 0.86, reason="询问投标模板。")
    if _contains(text, ["评分", "得分", "分值", "评审", "扣分", "响应"]):
        return AssistantIntent("score_points", 0.86, reason="询问评分点或评审响应。")
    if _contains(text, ["目录", "大纲"]):
        return AssistantIntent("outline", 0.86, reason="询问技术标目录。")
    if _contains(text, ["word", "成稿", "初稿", "onlyoffice", "复核"]):
        return AssistantIntent("word", 0.82, reason="询问 Word 初稿或复核。")
    if _contains(text, ["入库", "脱敏", "补充哪些", "上传参考资料", "上传资料"]):
        return AssistantIntent("material_ingestion", 0.86, reason="询问投标智库入库。")
    if _contains(text, ["模型配置", "速度", "成本", "失败排查", "调用失败", "质量之间取舍", "max_tokens", "并发"]):
        return AssistantIntent("model_ops", 0.86, reason="询问模型配置、成本或失败排查。")
    if _contains(text, ["评审办法", "法律法规", "技术规范", "企业制度", "依据", "素材", "rag", "优秀标书", "引用", "来源", "法规", "规范", "制度"]):
        return AssistantIntent("materials", 0.82, reason="询问智库依据或资料来源。")
    if _contains(text, ["正文", "生成", "重试", "章节", "偏空", "写法", "怎么写", "补充"]):
        return AssistantIntent("generation", 0.82, reason="询问正文生成或章节写法。")
    if _contains(text, ["风险", "问题", "缺口", "亮点", "合规", "法律", "条款", "暗标", "废标"]):
        return AssistantIntent("risk", 0.82, reason="询问风险、合规或编制注意事项。")
    return AssistantIntent("fallback", 0.0, reason="规则未命中。")


def _normalize_query(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _contains(text: str, tokens: tuple[str, ...] | list[str]) -> bool:
    return any(token.lower() in text for token in tokens)


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
