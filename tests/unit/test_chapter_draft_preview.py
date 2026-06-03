from construction_bidding_agent.chapter_generator.draft_preview import render_chapter_draft_preview


def test_render_chapter_draft_preview_expands_paragraph_table_and_placeholder():
    report = render_chapter_draft_preview(
        {
            "model": "test-model",
            "generated_at": "2026-05-05T00:00:00+08:00",
            "chapters": [
                {
                    "chapter_path": ["施工进度表"],
                    "score_response_check": {"response_summary": "响应关键线路要求。"},
                    "sections": [
                        {
                            "heading": "施工进度计划编制依据",
                            "level": 2,
                            "blocks": [
                                {"type": "paragraph", "text": "本工程按365日历天组织施工。"},
                                {
                                    "type": "rich_table",
                                    "title": "节点控制表",
                                    "columns": [{"key": "col_1", "title": "序号"}, {"key": "col_2", "title": "节点"}],
                                    "rows": [{"cells": {"col_1": "1", "col_2": "主体结构完成"}}],
                                },
                                {
                                    "type": "image_placeholder",
                                    "caption": "施工进度网络图",
                                    "reason": "需结合本项目计划补充。",
                                },
                            ],
                        }
                    ],
                    "review_items": [{"severity": "medium", "message": "复核节点工期。"}],
                }
            ],
        }
    )

    assert "# 技术标章节正文预览稿" in report
    assert "本工程按365日历天组织施工。" in report
    assert "| 序号 | 节点 |" in report
    assert "主体结构完成" in report
    assert "【图片占位】施工进度网络图" in report
    assert "复核节点工期" not in report
