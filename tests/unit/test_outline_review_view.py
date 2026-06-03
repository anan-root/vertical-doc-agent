from construction_bidding_agent.outline_generator.generator import refresh_outline_confirmation
from construction_bidding_agent.outline_generator.review_view import build_outline_review_view


def test_build_outline_review_view_includes_level_3_tree_and_domain_tabs():
    outline = _outline()
    refresh_outline_confirmation(outline)

    view = build_outline_review_view(outline)

    assert view["schema_version"] == "outline_review_view_v0.1"
    assert view["status"] == "ready"
    assert view["summary"]["level_1_count"] == 1
    assert view["summary"]["node_count"] == 3
    assert view["domain_tabs"][0]["domain"] == "construction"
    assert view["tree"][0]["title_locked"] is True
    assert view["tree"][0]["children"][0]["title_locked"] is False
    assert view["tree"][0]["children"][0]["children"][0]["number"] == "1.1.1"
    assert view["review_queue"] == []


def test_build_outline_review_view_keeps_pending_review_queue():
    outline = _outline()
    outline["nodes"][0]["children"][0]["requires_review"] = True
    outline["nodes"][0]["children"][0]["review_reason"] = "二级目录需人工确认。"
    refresh_outline_confirmation(outline)

    view = build_outline_review_view(outline)

    assert view["status"] == "pending_review"
    assert view["summary"]["pending_review_count"] == 1
    assert len(view["review_queue"]) == 1
    assert view["review_queue"][0]["target_title"] == "安全管理目标"


def _outline():
    return {
        "schema_version": "technical_bid_outline_v0.1",
        "outline_id": "outline_test",
        "project_type": "construction",
        "status": "completed",
        "level_1_count": 1,
        "nodes": [
            {
                "node_id": "N1",
                "level": 1,
                "number": "1",
                "title": "安全管理体系与措施",
                "title_source": "score_point_raw",
                "domain": "construction",
                "category": "安全管理",
                "children": [
                    {
                        "node_id": "N1_001",
                        "level": 2,
                        "number": "1.1",
                        "title": "安全管理目标",
                        "title_source": "generated",
                        "domain": "construction",
                        "category": "安全管理",
                        "children": [
                            {
                                "node_id": "N1_001_001",
                                "level": 3,
                                "number": "1.1.1",
                                "title": "安全目标分解",
                                "title_source": "generated",
                                "domain": "construction",
                                "category": "安全管理",
                                "children": [],
                                "requires_review": False,
                            }
                        ],
                        "requires_review": False,
                    }
                ],
                "requires_review": False,
            }
        ],
        "review_items": [],
        "quality_checks": [],
        "refinement": {
            "status": "completed",
            "task_count": 1,
            "applied_count": 1,
            "failed_count": 0,
            "skipped_count": 0,
        },
    }
