"""技术标目录人工复核界面数据。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "outline_review_view_v0.1"


def build_outline_review_view(outline: dict[str, Any]) -> dict[str, Any]:
    confirmation = outline.get("confirmation") or {}
    flat_nodes = confirmation.get("flat_nodes") if isinstance(confirmation.get("flat_nodes"), list) else []
    node_by_id = {
        str(node.get("node_id")): node
        for node in flat_nodes
        if isinstance(node, dict) and node.get("node_id")
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "outline_id": outline.get("outline_id"),
        "project_type": outline.get("project_type"),
        "status": confirmation.get("status") or outline.get("status"),
        "summary": _summary(outline, confirmation),
        "rules": confirmation.get("rules") or {},
        "domain_tabs": _domain_tabs(outline, confirmation, node_by_id),
        "review_queue": _review_queue(confirmation, node_by_id),
        "tree": _tree(outline),
    }


def write_outline_review_view(view: dict[str, Any], json_path: str | Path) -> None:
    target = Path(json_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(view, ensure_ascii=False, indent=2), encoding="utf-8")


def _summary(outline: dict[str, Any], confirmation: dict[str, Any]) -> dict[str, Any]:
    summary = confirmation.get("summary") if isinstance(confirmation.get("summary"), dict) else {}
    refinement = outline.get("refinement") if isinstance(outline.get("refinement"), dict) else {}
    return {
        "level_1_count": summary.get("level_1_count") or outline.get("level_1_count") or 0,
        "node_count": summary.get("node_count") or 0,
        "pending_review_count": summary.get("pending_review_count") or 0,
        "blocking_count": summary.get("blocking_count") or 0,
        "domain_counts": summary.get("domain_counts") or {},
        "refinement": {
            "status": refinement.get("status"),
            "task_count": refinement.get("task_count") or 0,
            "applied_count": refinement.get("applied_count") or 0,
            "failed_count": refinement.get("failed_count") or 0,
            "skipped_count": refinement.get("skipped_count") or 0,
        },
    }


def _domain_tabs(
    outline: dict[str, Any],
    confirmation: dict[str, Any],
    node_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    groups = confirmation.get("domain_groups") if isinstance(confirmation.get("domain_groups"), list) else []
    tabs: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        node_ids = [node_id for node_id in group.get("level_1_node_ids") or [] if isinstance(node_id, str)]
        tabs.append(
            {
                "domain": group.get("domain"),
                "label": group.get("label"),
                "level_1_node_ids": node_ids,
                "level_1_count": len(node_ids),
                "node_count": _domain_node_count(node_ids, node_by_id),
                "pending_review_count": _domain_pending_count(node_ids, node_by_id),
                "can_generate_chapters": group.get("can_generate_chapters"),
                "can_export_word": group.get("can_export_word"),
            }
        )
    if tabs:
        return tabs
    domain_counts = (confirmation.get("summary") or {}).get("domain_counts") or {}
    return [
        {
            "domain": domain,
            "label": _domain_label(domain),
            "level_1_node_ids": [],
            "level_1_count": 0,
            "node_count": count,
            "pending_review_count": 0,
            "can_generate_chapters": domain != "design",
            "can_export_word": domain == "construction",
        }
        for domain, count in domain_counts.items()
    ]


def _tree(outline: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _review_node(node, parent_node_id=None)
        for node in outline.get("nodes") or []
        if isinstance(node, dict)
    ]


def _review_node(node: dict[str, Any], *, parent_node_id: str | None) -> dict[str, Any]:
    state = node.get("confirmation_state") if isinstance(node.get("confirmation_state"), dict) else {}
    return {
        "node_id": node.get("node_id"),
        "parent_node_id": parent_node_id,
        "level": node.get("level"),
        "number": node.get("number"),
        "title": node.get("title"),
        "domain": node.get("domain"),
        "category": node.get("category"),
        "title_source": node.get("title_source"),
        "source_label": state.get("source_label"),
        "review_status": state.get("review_status"),
        "risk_level": state.get("risk_level"),
        "title_locked": state.get("title_locked"),
        "order_locked": state.get("order_locked"),
        "delete_forbidden": state.get("delete_forbidden"),
        "editable_fields": state.get("editable_fields") or [],
        "allowed_actions": state.get("allowed_actions") or [],
        "review_reason": state.get("review_reason"),
        "score": node.get("score"),
        "score_rule": node.get("score_rule"),
        "children": [
            _review_node(child, parent_node_id=str(node.get("node_id") or ""))
            for child in node.get("children") or []
            if isinstance(child, dict)
        ],
    }


def _review_queue(
    confirmation: dict[str, Any],
    node_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    queue = []
    for item in confirmation.get("review_queue") or []:
        if not isinstance(item, dict):
            continue
        target_node_id = str(item.get("target_node_id") or "")
        node = node_by_id.get(target_node_id, {})
        queue.append(
            {
                "review_id": item.get("review_id"),
                "target_node_id": target_node_id or None,
                "target_number": node.get("number"),
                "target_title": node.get("title"),
                "priority": item.get("priority"),
                "item": item.get("item"),
                "reason": item.get("reason"),
                "suggested_action": item.get("suggested_action"),
                "status": item.get("status"),
            }
        )
    return queue


def _domain_node_count(node_ids: list[str], node_by_id: dict[str, dict[str, Any]]) -> int:
    domains = {
        node_by_id[node_id].get("domain")
        for node_id in node_ids
        if node_id in node_by_id
    }
    return sum(1 for node in node_by_id.values() if node.get("domain") in domains)


def _domain_pending_count(node_ids: list[str], node_by_id: dict[str, dict[str, Any]]) -> int:
    domains = {
        node_by_id[node_id].get("domain")
        for node_id in node_ids
        if node_id in node_by_id
    }
    return sum(
        1
        for node in node_by_id.values()
        if node.get("domain") in domains
        and node.get("confirmation_state", {}).get("review_status") == "pending_review"
    )


def _domain_label(domain: str) -> str:
    labels = {
        "construction": "施工方案",
        "design": "设计方案",
        "management": "综合管理",
        "unknown": "待确认",
    }
    return labels.get(domain, domain)
