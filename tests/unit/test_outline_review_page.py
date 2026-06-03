from construction_bidding_agent.outline_generator.review_page import render_outline_review_page


def test_render_outline_review_page_embeds_tree_data_and_controls():
    html = render_outline_review_page(
        [
            {
                "outline_id": "outline_test",
                "status": "ready",
                "summary": {
                    "level_1_count": 1,
                    "node_count": 3,
                    "pending_review_count": 0,
                    "refinement": {"applied_count": 1, "task_count": 1},
                },
                "domain_tabs": [{"domain": "construction", "label": "施工方案", "level_1_count": 1}],
                "review_queue": [],
                "tree": [
                    {
                        "node_id": "N1",
                        "level": 1,
                        "number": "1",
                        "title": "安全管理体系与措施",
                        "domain": "construction",
                        "review_status": "auto_checked",
                        "title_locked": True,
                        "source_label": "评分点原文",
                        "children": [
                            {
                                "node_id": "N1_001",
                                "level": 2,
                                "number": "1.1",
                                "title": "安全管理目标",
                                "domain": "construction",
                                "review_status": "auto_checked",
                                "title_locked": False,
                                "source_label": "系统规则生成",
                                "children": [
                                    {
                                        "node_id": "N1_001_001",
                                        "level": 3,
                                        "number": "1.1.1",
                                        "title": "安全目标分解",
                                        "domain": "construction",
                                        "review_status": "auto_checked",
                                        "title_locked": False,
                                        "source_label": "系统规则生成",
                                        "children": [],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    )

    assert "技术标目录人工复核" in html
    assert "projectSelect" in html
    assert "安全目标分解" in html
    assert "level-3" in html
