from construction_bidding_agent.assistant.response_builder import build_assistant_chat_response


def test_score_point_answer_links_outline_and_generation_units():
    workflow_summary = {
        "project": {"name": "覆盖测试项目", "project_type": "construction"},
        "stats": {"score_points": 2, "review_items": 0, "tender_files": 1, "estimated_chapters": 2},
        "score_points": [
            {"title": "施工组织设计方案完整性", "status": "已识别"},
            {"title": "质量安全文明施工措施", "status": "已识别"},
        ],
        "outline_preview": [
            {
                "node_id": "N1",
                "title": "施工组织设计方案完整性",
                "children": [{"node_id": "N1-1", "title": "施工部署", "children": []}],
            },
            {
                "node_id": "N2",
                "title": "质量安全文明施工措施",
                "children": [{"node_id": "N2-1", "title": "质量安全措施", "children": []}],
            },
        ],
        "generation_units": [
            {
                "unit_id": "GU-N1-1",
                "target_node_id": "N1-1",
                "chapter": "施工部署",
                "chapter_path": ["施工组织设计方案完整性", "施工部署"],
                "status": "已生成",
            },
            {
                "unit_id": "GU-N2-1",
                "target_node_id": "N2-1",
                "chapter": "质量安全措施",
                "chapter_path": ["质量安全文明施工措施", "质量安全措施"],
                "status": "待生成",
            },
        ],
        "review_items": [],
        "artifacts": {},
    }

    response = build_assistant_chat_response(
        message="评分点响应情况怎么样？",
        workflow_summary=workflow_summary,
    )
    answer = response["answer"]

    assert response["intent"] == "score_points"
    assert "目录已承接 2 个评分点" in answer
    assert "正文已覆盖 1 个" in answer
    assert "质量安全文明施工措施" in answer


def test_review_report_answer_includes_score_coverage_summary():
    workflow_summary = {
        "project": {"name": "复核测试项目", "project_type": "construction"},
        "stats": {"score_points": 1, "review_items": 0, "tender_files": 1, "estimated_chapters": 1},
        "score_points": [{"title": "施工方案响应情况", "status": "已识别"}],
        "outline_preview": [{"node_id": "N1", "title": "施工方案响应情况", "children": []}],
        "generation_units": [
            {
                "unit_id": "GU-N1",
                "target_node_id": "N1",
                "chapter": "施工方案响应情况",
                "chapter_path": ["施工方案响应情况"],
                "status": "已生成",
            }
        ],
        "review_items": [],
        "artifacts": {},
    }

    response = build_assistant_chat_response(
        message="生成一份 AI 复核报告",
        workflow_summary=workflow_summary,
    )

    assert response["intent"] == "review_report"
    assert "评分点覆盖：1/1" in response["answer"]
    assert "目录已承接 1 个" in response["answer"]


def test_score_point_answer_prefers_backend_coverage_contract():
    workflow_summary = {
        "project": {"name": "契约优先项目", "project_type": "construction"},
        "stats": {"score_points": 3, "review_items": 0, "tender_files": 1, "estimated_chapters": 3},
        "score_points": [
            {"title": "评分点一", "status": "已识别"},
            {"title": "评分点二", "status": "已识别"},
            {"title": "评分点三", "status": "已识别"},
        ],
        "outline_preview": [],
        "generation_units": [],
        "score_point_coverage": {
            "schema_version": "score_point_coverage_v1",
            "summary": {
                "total": 3,
                "covered": 2,
                "pending": 1,
                "risk": 0,
                "outline_covered": 3,
                "has_outline": True,
                "has_generation_units": True,
            },
            "items": [
                {
                    "title": "评分点三",
                    "status_key": "pending",
                    "status_label": "待生成",
                    "outline_text": "评分点三",
                    "generation_text": "已生成 0/1",
                }
            ],
        },
        "review_items": [],
        "artifacts": {},
    }

    response = build_assistant_chat_response(
        message="评分点响应情况怎么样？",
        workflow_summary=workflow_summary,
    )

    assert "目录已承接 3 个评分点" in response["answer"]
    assert "正文已覆盖 2 个" in response["answer"]
    assert "评分点三" in response["answer"]


def test_project_overview_intent_answers_project_industry():
    workflow_summary = {
        "project": {"name": "行业测试项目", "project_type": "construction"},
        "stats": {"score_points": 2, "review_items": 1, "tender_files": 1, "estimated_chapters": 5},
        "parse_review_summary": {
            "project_info": [
                {"label": "项目名称", "value": "某医院综合楼施工总承包"},
                {"label": "项目类型", "value": "施工项目"},
                {"label": "建设地点", "value": "成都市"},
                {"label": "建设规模", "value": "建筑面积约 30000 平方米"},
            ]
        },
        "score_points": [],
        "outline_preview": [],
        "generation_units": [],
        "review_items": [],
        "artifacts": {},
    }

    response = build_assistant_chat_response(
        message="这个项目是什么行业？",
        workflow_summary=workflow_summary,
    )

    assert response["intent"] == "project_overview"
    assert response["intent_label"] == "项目基本信息"
    assert "建设工程 - 房屋建筑" in response["answer"]
    assert "某医院综合楼施工总承包" in response["answer"]
    assert "成都市" in response["answer"]


def test_project_overview_rule_matches_natural_question():
    workflow_summary = {
        "project": {"name": "自然问法项目", "project_type": "construction"},
        "stats": {"score_points": 1, "review_items": 0, "tender_files": 1, "estimated_chapters": 1},
        "parse_review_summary": {
            "project_info": [
                {"label": "项目名称", "value": "某学校宿舍楼施工总承包"},
                {"label": "项目类型", "value": "施工项目"},
            ]
        },
        "score_points": [],
        "outline_preview": [],
        "generation_units": [],
        "review_items": [],
        "artifacts": {},
    }

    response = build_assistant_chat_response(
        message="这是个什么行业的项目？",
        workflow_summary=workflow_summary,
    )

    assert response["intent"] == "project_overview"
    assert response["intent_source"] in {"rule", "llm"}
    assert "房屋建筑" in response["answer"]


def test_llm_answer_resolver_can_rewrite_project_answer():
    workflow_summary = {
        "project": {"name": "检索增强项目", "project_type": "construction"},
        "stats": {"score_points": 1, "review_items": 0, "tender_files": 1, "estimated_chapters": 1},
        "parse_review_summary": {
            "project_info": [
                {"label": "项目名称", "value": "某医院综合楼施工总承包"},
                {"label": "项目类型", "value": "施工项目"},
                {"label": "建设地点", "value": "成都市"},
            ]
        },
        "score_points": [],
        "outline_preview": [],
        "generation_units": [],
        "review_items": [],
        "artifacts": {},
    }

    def fake_answer_resolver(context):
        assert context["intent"] == "project_overview"
        assert context["retrieval_bundle"]["retrieved_context"]
        return {
            "answer": "AI 检索后：这是一个建设工程 - 房屋建筑项目，位于成都市。",
            "confidence": 0.92,
            "reason": "基于项目摘要和检索上下文。",
        }

    response = build_assistant_chat_response(
        message="这个项目是什么行业？",
        workflow_summary=workflow_summary,
        answer_resolver=fake_answer_resolver,
    )

    assert response["answer_source"] == "llm"
    assert response["answer"] == "AI 检索后：这是一个建设工程 - 房屋建筑项目，位于成都市。"
    assert response["answer_confidence"] == 0.92


def test_out_of_scope_question_is_blocked():
    workflow_summary = {
        "project": {"name": "边界测试项目", "project_type": "construction"},
        "stats": {"score_points": 0, "review_items": 0, "tender_files": 0, "estimated_chapters": 0},
        "score_points": [],
        "outline_preview": [],
        "generation_units": [],
        "review_items": [],
        "artifacts": {},
    }

    response = build_assistant_chat_response(
        message="今天星期几？",
        workflow_summary=workflow_summary,
    )

    assert response["intent"] == "fallback"
    assert "只围绕当前编标项目回答" in response["answer"]


def test_daily_chat_greeting_uses_polite_identity():
    workflow_summary = {
        "project": {"name": "问候项目", "project_type": "construction"},
        "stats": {"score_points": 0, "review_items": 0, "tender_files": 0, "estimated_chapters": 0},
        "score_points": [],
        "outline_preview": [],
        "generation_units": [],
        "review_items": [],
        "artifacts": {},
    }

    response = build_assistant_chat_response(
        message="你好",
        workflow_summary=workflow_summary,
        account_context={
            "display_name": "张三",
            "role": "bid_staff",
        },
    )

    assert response["intent"] == "daily_chat"
    assert "你好，张三先生" in response["answer"]
    assert "小智" in response["answer"]
    assert "能力边界" in response["answer"]


def test_daily_chat_admin_is_called_shen():
    workflow_summary = {
        "project": {"name": "管理员项目", "project_type": "construction"},
        "stats": {"score_points": 0, "review_items": 0, "tender_files": 0, "estimated_chapters": 0},
        "score_points": [],
        "outline_preview": [],
        "generation_units": [],
        "review_items": [],
        "artifacts": {},
    }

    response = build_assistant_chat_response(
        message="你好",
        workflow_summary=workflow_summary,
        account_context={
            "display_name": "系统管理员",
            "role": "admin",
        },
    )

    assert response["intent"] == "daily_chat"
    assert "你好，神！" in response["answer"]


def test_daily_chat_keeps_rule_intent_even_when_llm_resolver_is_available():
    workflow_summary = {
        "project": {"name": "问候兜底项目", "project_type": "construction"},
        "stats": {"score_points": 0, "review_items": 0, "tender_files": 0, "estimated_chapters": 0},
        "score_points": [],
        "outline_preview": [],
        "generation_units": [],
        "review_items": [],
        "artifacts": {},
    }

    def fake_intent_resolver(_context):
        return {
            "intent": "fallback",
            "confidence": 0.99,
            "reason": "模型误判为超出范围。",
        }

    response = build_assistant_chat_response(
        message="你好",
        workflow_summary=workflow_summary,
        account_context={"display_name": "张三", "role": "bid_staff"},
        intent_resolver=fake_intent_resolver,
    )

    assert response["intent"] == "daily_chat"
    assert response["intent_source"] == "rule"
    assert "小智" in response["answer"]


def test_template_boundary_answer_keeps_manual_confirmation():
    workflow_summary = {
        "project": {"name": "模板边界项目", "project_type": "construction"},
        "stats": {"score_points": 1, "review_items": 0, "tender_files": 1, "estimated_chapters": 2},
        "score_points": [{"title": "质量安全文明施工措施", "status": "已识别"}],
        "outline_preview": [],
        "generation_units": [],
        "review_items": [],
        "artifacts": {},
    }
    template_recommendation = {
        "recommendations": [
            {
                "name": "施工总承包技术标通用模板",
                "reason": "项目类型匹配",
                "usage_boundary": "模板只做推荐和预览，不自动覆盖已确认目录或正文。",
            }
        ]
    }

    response = build_assistant_chat_response(
        message="这个模板能不能直接覆盖目录？",
        workflow_summary=workflow_summary,
        template_recommendation=template_recommendation,
    )

    assert response["intent"] == "template_boundary"
    assert "不会自动覆盖" in response["answer"]
    assert "人工确认" in response["answer"]


def test_risk_question_retrieves_bidding_experience_with_rerank_scores():
    workflow_summary = {
        "project": {"name": "暗标测试项目", "project_type": "construction"},
        "stats": {"score_points": 1, "review_items": 0, "tender_files": 1, "estimated_chapters": 1},
        "score_points": [{"title": "暗标编制要求响应", "status": "已识别"}],
        "outline_preview": [],
        "generation_units": [],
        "review_items": [],
        "artifacts": {},
    }

    response = build_assistant_chat_response(
        message="这个项目暗标有哪些风险？",
        workflow_summary=workflow_summary,
    )

    assert response["intent"] == "risk"
    retrieved = response["retrieved_context"]
    assert retrieved
    assert any(item["type"] == "assistant_knowledge" and "暗标" in item["title"] for item in retrieved)
    assert all("score" in item and "rerank_score" in item for item in retrieved)


def test_generation_question_retrieves_empty_chapter_experience():
    workflow_summary = {
        "project": {"name": "正文测试项目", "project_type": "construction"},
        "stats": {"score_points": 1, "review_items": 0, "tender_files": 1, "estimated_chapters": 2},
        "score_points": [{"title": "施工方案完整性", "status": "已识别"}],
        "outline_preview": [],
        "generation_units": [
            {"unit_id": "GU-1", "chapter": "施工部署", "status": "待生成"},
        ],
        "review_items": [],
        "artifacts": {},
    }

    response = build_assistant_chat_response(
        message="这个章节太空了怎么补？",
        workflow_summary=workflow_summary,
    )

    assert response["intent"] == "generation"
    assert any("章节偏空" in item.get("title", "") for item in response["retrieved_context"])


def test_rag_evidence_is_reranked_for_material_question():
    workflow_summary = {
        "project": {"name": "依据测试项目", "project_type": "construction"},
        "stats": {"score_points": 1, "review_items": 0, "tender_files": 1, "estimated_chapters": 1},
        "score_points": [],
        "outline_preview": [],
        "generation_units": [],
        "review_items": [],
        "artifacts": {},
    }
    rag_preview = {
        "results": [
            {
                "title": "安全文明施工技术规范摘要",
                "source_title": "企业技术规范库",
                "knowledge_type": "technical_standard",
                "knowledge_type_label": "技术规范",
                "summary": "安全文明施工章节应说明临边防护、扬尘治理和现场检查制度。",
                "score": 0.7,
            }
        ]
    }

    response = build_assistant_chat_response(
        message="安全文明施工有什么规范依据？",
        workflow_summary=workflow_summary,
        rag_preview=rag_preview,
    )

    assert response["intent"] == "materials"
    assert response["retrieved_context"][0]["type"] in {"rag_evidence", "assistant_knowledge"}
    assert any(item["type"] == "rag_evidence" for item in response["retrieved_context"])


def test_llm_intent_fallback_can_classify_uncertain_question():
    workflow_summary = {
        "project": {"name": "意图兜底项目", "project_type": "construction"},
        "stats": {"score_points": 1, "review_items": 0, "tender_files": 1, "estimated_chapters": 2},
        "score_points": [{"title": "施工方案完整性", "status": "已识别"}],
        "outline_preview": [],
        "generation_units": [],
        "review_items": [],
        "artifacts": {},
    }

    def fake_intent_resolver(context):
        assert context["rule_intent"]["intent"] == "fallback"
        return {
            "intent": "generation",
            "confidence": 0.88,
            "reason": "用户在问章节内容补强。",
        }

    response = build_assistant_chat_response(
        message="这块内容有点飘，怎样变得更内行？",
        workflow_summary=workflow_summary,
        intent_resolver=fake_intent_resolver,
    )

    assert response["intent"] == "generation"
    assert response["intent_source"] == "llm"
    assert "章节" in response["answer"] or "正文" in response["answer"]


def test_low_confidence_llm_intent_fallback_is_blocked():
    workflow_summary = {
        "project": {"name": "低置信度项目", "project_type": "construction"},
        "stats": {"score_points": 0, "review_items": 0, "tender_files": 0, "estimated_chapters": 0},
        "score_points": [],
        "outline_preview": [],
        "generation_units": [],
        "review_items": [],
        "artifacts": {},
    }

    def fake_intent_resolver(context):
        return {
            "intent": "generation",
            "confidence": 0.3,
            "reason": "不确定。",
        }

    response = build_assistant_chat_response(
        message="随便说两句吧",
        workflow_summary=workflow_summary,
        intent_resolver=fake_intent_resolver,
    )

    assert response["intent"] == "fallback"
    assert response["intent_source"] == "llm"
    assert "只围绕当前编标项目回答" in response["answer"]
