"""基于现有优秀标书库的轻量 RAG 检索封装。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from construction_bidding_agent.backend.knowledge_base import search_excellent_bid_slices


def search_project_rag_materials(
    *,
    project_root: Path,
    storage_root: Path,
    workflow_summary: Mapping[str, Any],
    query: str = "",
    chapter: str = "",
    limit: int = 5,
) -> dict[str, Any]:
    resolved_query = _build_query(workflow_summary, query=query, chapter=chapter)
    result = search_excellent_bid_slices(
        project_root=project_root,
        storage_root=storage_root,
        query=resolved_query,
        limit=limit,
    )
    safe_results = []
    for item in result.get("results") or []:
        if not isinstance(item, dict):
            continue
        safe_results.append(
            {
                "title": item.get("title") or item.get("section_title") or item.get("heading") or "未命名素材",
                "source_bid_id": item.get("source_bid_id"),
                "source_title": item.get("source_title"),
                "knowledge_type": item.get("knowledge_type"),
                "knowledge_type_label": item.get("knowledge_type_label"),
                "source_type_label": item.get("source_type_label"),
                "score": item.get("score"),
                "summary": item.get("summary") or item.get("text_preview") or item.get("content_preview"),
                "chapter_path": item.get("chapter_path"),
                "image_count": item.get("image_count"),
                "table_count": item.get("table_count"),
                "reason": _material_reason(chapter, resolved_query),
            }
        )
    return {
        "query": resolved_query,
        "chapter": chapter,
        "total": result.get("total", 0),
        "limit": limit,
        "results": safe_results,
        "note": "仅展示素材摘要和来源，不输出优秀标书大段原文。",
    }


def _build_query(workflow_summary: Mapping[str, Any], *, query: str, chapter: str) -> str:
    if query.strip():
        return query.strip()
    if chapter.strip():
        return chapter.strip()
    score_points = workflow_summary.get("score_points")
    if isinstance(score_points, list):
        titles = []
        for item in score_points[:3]:
            if isinstance(item, Mapping) and item.get("title"):
                titles.append(str(item["title"]))
        if titles:
            return " ".join(titles)
    project = workflow_summary.get("project")
    if isinstance(project, Mapping):
        return str(project.get("name") or "")
    return ""


def _material_reason(chapter: str, query: str) -> str:
    if chapter:
        return f"与章节“{chapter}”的标题或写作主题相关。"
    if query:
        return f"与关键词“{query}”相关。"
    return "根据当前项目评分点自动召回。"
