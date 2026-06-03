"""Built-in bidding experience knowledge for the assistant."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AssistantKnowledgeItem:
    id: str
    title: str
    category: str
    content: str
    tags: tuple[str, ...]
    intents: tuple[str, ...]
    source: str = "内置编标经验库"
    risk_level: str = "low"


BUILTIN_KNOWLEDGE: tuple[AssistantKnowledgeItem, ...] = (
    AssistantKnowledgeItem(
        id="exp-score-coverage-001",
        title="评分点响应应先进入目录再落到正文",
        category="评分点响应",
        content="评分点不能只在正文里零散出现。更稳的做法是先让目录标题或二三级小节承接评分点，再在对应正文中写清措施、资源、流程和验收标准。若评分点有分值，应优先复核高分项和容易遗漏的扣分条件。",
        tags=("评分点", "目录", "正文", "响应", "扣分"),
        intents=("score_points", "outline", "generation", "review_report"),
        risk_level="medium",
    ),
    AssistantKnowledgeItem(
        id="exp-dark-bid-001",
        title="暗标编制重点检查可识别信息",
        category="暗标风险",
        content="暗标或匿名评审场景下，重点检查封面、页眉页脚、图片水印、文件属性、项目团队姓名、企业名称、过往项目名称、联系方式和特殊地址。系统可以提示风险，但最终应由编标人员按招标文件暗标要求逐项人工确认。",
        tags=("暗标", "匿名", "废标", "页眉页脚", "水印"),
        intents=("risk", "word", "review_report"),
        risk_level="high",
    ),
    AssistantKnowledgeItem(
        id="exp-empty-chapter-001",
        title="章节偏空时优先补措施链条",
        category="正文生成",
        content="章节偏空通常不是单纯字数问题，而是缺少措施链条。可按目标、组织、资源、工艺流程、检查验收、风险应对、资料归档的顺序补齐。优先结合评分点、技术规范和企业制度，不建议堆砌无项目特征的通用套话。",
        tags=("正文", "偏空", "措施", "补充", "章节"),
        intents=("generation", "review_report"),
        risk_level="medium",
    ),
    AssistantKnowledgeItem(
        id="exp-rag-scope-001",
        title="投标智库适合增强依据和企业表达",
        category="智库依据",
        content="投标智库更适合提供优秀标书写法、法规规范依据、企业制度口径和评审办法解释。项目评分点、目录结构和生成状态仍应以当前项目解析结果为准，不能因为相似标书内容好看就覆盖当前项目要求。",
        tags=("投标智库", "依据", "优秀标书", "法规", "企业制度"),
        intents=("materials", "generation", "risk", "queue_preflight"),
        risk_level="medium",
    ),
    AssistantKnowledgeItem(
        id="exp-template-boundary-001",
        title="模板只能推荐和预览，不能自动覆盖用户成果",
        category="投标模板",
        content="投标模板适合统一目录骨架、常用章节和企业标准表达。若用户已经人工调整目录或正文，模板只能作为参考，套用前必须确认项目类型、评分点、章节数量和版本，不能自动覆盖已确认内容。",
        tags=("模板", "套用", "覆盖", "目录", "人工确认"),
        intents=("template", "template_boundary", "outline"),
        risk_level="medium",
    ),
    AssistantKnowledgeItem(
        id="exp-word-review-001",
        title="Word 初稿复核先看结构再看格式",
        category="Word 复核",
        content="Word 初稿复核建议先看标题层级、目录页码和评分点响应位置，再看表格跨页、图片缺失、题注、页眉页脚和空章节。OnlyOffice 在线查看只是复核入口，最终成稿确认和对外提交必须人工完成。",
        tags=("Word", "OnlyOffice", "目录", "页眉页脚", "图片", "表格"),
        intents=("word", "review_report"),
        risk_level="medium",
    ),
    AssistantKnowledgeItem(
        id="exp-queue-preflight-001",
        title="批量生成前先小批量试跑",
        category="任务提交前检查",
        content="正文批量生成前建议先选择一到三个典型章节试跑，确认评分点、智库依据、语气和格式都正常后再扩大范围。重复点击生成容易造成排队混乱、重复扣费和状态不同步，应优先复用活跃任务。",
        tags=("批量生成", "试跑", "队列", "成本", "重复任务"),
        intents=("queue_preflight", "generation", "model_ops"),
        risk_level="medium",
    ),
    AssistantKnowledgeItem(
        id="exp-compliance-001",
        title="法规规范回答只做风险提示",
        category="合规风险",
        content="法律法规、技术规范和企业制度可以帮助识别合规风险、禁止性要求和复核重点，但不能替代法务、合约、总工或技术负责人结论。回答应引用摘要和来源，避免输出未经核对的大段条文。",
        tags=("法规", "规范", "合规", "风险", "人工复核"),
        intents=("risk", "materials", "review_report"),
        risk_level="high",
    ),
    AssistantKnowledgeItem(
        id="exp-system-workflow-001",
        title="五步编标流程的主线不能被助手绕开",
        category="系统使用",
        content="智标工坊主线是上传资料、解析确认、生成目录、生成正文、Word 复核。小助手可以解释状态、提示风险和引用依据，但不应绕过确认点自动删除、覆盖、重跑或提交成稿。",
        tags=("五步流程", "小助手", "人工确认", "系统边界"),
        intents=("context_help", "next_action", "progress"),
        risk_level="medium",
    ),
)


def builtin_knowledge_items() -> list[dict[str, object]]:
    return [
        {
            "id": item.id,
            "title": item.title,
            "category": item.category,
            "content": item.content,
            "tags": list(item.tags),
            "intents": list(item.intents),
            "source": item.source,
            "risk_level": item.risk_level,
        }
        for item in BUILTIN_KNOWLEDGE
    ]
