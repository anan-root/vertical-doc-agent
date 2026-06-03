"""技术标目录树与章节正文生成单元一致性检查。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CHECK_SCHEMA_VERSION = "outline_package_consistency_v0.1"


def build_outline_package_consistency_from_files(
    outline_json: str | Path,
    chapter_inputs_json: str | Path,
) -> dict[str, Any]:
    """从文件检查目录树与章节正文生成输入包是否一致。"""

    outline = _load_json(outline_json)
    chapter_inputs = _load_json(chapter_inputs_json)
    return build_outline_package_consistency(outline, chapter_inputs)


def build_outline_package_consistency(outline: dict[str, Any], chapter_inputs: dict[str, Any]) -> dict[str, Any]:
    """检查目录节点和正文生成单元之间的对应关系。"""

    nodes = [node for node in outline.get("nodes") or [] if isinstance(node, dict)]
    packages = [package for package in chapter_inputs.get("packages") or [] if isinstance(package, dict)]
    node_index = _node_index(nodes)
    children_by_parent = _children_by_parent(nodes)
    packages_by_parent = _packages_by_parent(packages)
    package_target_counts = _package_target_counts(packages)
    issues: list[dict[str, Any]] = []

    for package in packages:
        _check_package(package, node_index, children_by_parent, package_target_counts, issues)

    top_mappings = [
        _top_level_mapping(node, packages_by_parent.get(str(node.get("node_id") or ""), []), issues)
        for node in nodes
    ]
    issue_counts = _issue_counts(issues)
    status = "pass"
    if issue_counts.get("error", 0):
        status = "fail"
    elif issue_counts.get("warning", 0):
        status = "warning"
    return {
        "schema_version": CHECK_SCHEMA_VERSION,
        "status": status,
        "outline_id": outline.get("outline_id"),
        "outline_level1_count": len(nodes),
        "package_count": len(packages),
        "packaged_level1_count": sum(1 for item in top_mappings if item["package_count"] > 0),
        "unpackaged_level1_count": sum(1 for item in top_mappings if item["package_count"] == 0),
        "issue_counts": issue_counts,
        "top_level_mappings": top_mappings,
        "issues": issues,
    }


def write_outline_package_consistency_outputs(
    result: dict[str, Any],
    json_out: str | Path,
    report_out: str | Path | None = None,
) -> None:
    """写出一致性检查 JSON 和 Markdown 报告。"""

    json_target = Path(json_out)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if report_out:
        report_target = Path(report_out)
        report_target.parent.mkdir(parents=True, exist_ok=True)
        report_target.write_text(render_outline_package_consistency_report(result), encoding="utf-8")


def render_outline_package_consistency_report(result: dict[str, Any]) -> str:
    """渲染给编标/技术人员可读的 Markdown 检查报告。"""

    lines = [
        "# 技术标目录与正文生成单元一致性检查报告",
        "",
        "## 检查结论",
        "",
        f"- 检查状态：{_status_text(result.get('status'))}",
        f"- 目录一级节点数：{result.get('outline_level1_count')}",
        f"- 正文生成单元数：{result.get('package_count')}",
        f"- 已进入正文生成的一级目录：{result.get('packaged_level1_count')}",
        f"- 未进入正文生成的一级目录：{result.get('unpackaged_level1_count')}",
        f"- 问题统计：{_format_issue_counts(result.get('issue_counts') or {})}",
        "",
        "## 关键说明",
        "",
        "- 正文生成单元不是目录行的一一映射；长章节会按二级目录拆成多个生成单元，三级目录通常作为 `child_headings` 进入对应生成单元。",
        "- 若一级目录领域不是当前生成范围，可能不会进入正文生成输入包；这类目录会在下表标为“未调度”。",
        "- 一级目录标题必须保持招标文件评分点原文表述；报告会检查生成单元 `chapter_path[0]` 与 `score_point_raw` 是否一致。",
        "",
        "## 一级目录映射",
        "",
        "| 序号 | 一级目录 | 领域 | 目录二级数 | 生成单元数 | 调度方式 | 状态 | 说明 |",
        "|---:|---|---|---:|---:|---|---|---|",
    ]
    for index, item in enumerate(result.get("top_level_mappings") or [], start=1):
        lines.append(
            f"| {index} | {_cell(item.get('title'))} | {_cell(item.get('domain'))} | "
            f"{item.get('outline_child_count')} | {item.get('package_count')} | "
            f"{_cell(item.get('schedule_mode'))} | {_cell(_mapping_status_text(item.get('status')))} | "
            f"{_cell('; '.join(item.get('notes') or []))} |"
        )
    lines.extend(["", "## 问题清单", ""])
    issues = result.get("issues") or []
    if not issues:
        lines.append("未发现目录与正文生成单元之间的结构性问题。")
    else:
        lines.extend(
            [
                "| 严重级别 | 类型 | 位置 | 说明 |",
                "|---|---|---|---|",
            ]
        )
        for issue in issues:
            location = issue.get("chapter_path") or issue.get("node_title") or issue.get("unit_id") or "-"
            if isinstance(location, list):
                location = " > ".join(str(part) for part in location)
            lines.append(
                f"| {_cell(issue.get('severity'))} | {_cell(issue.get('type'))} | "
                f"{_cell(location)} | {_cell(issue.get('message'))} |"
            )
    lines.append("")
    return "\n".join(lines)


def _check_package(
    package: dict[str, Any],
    node_index: dict[str, dict[str, Any]],
    children_by_parent: dict[str, list[dict[str, Any]]],
    package_target_counts: dict[str, int],
    issues: list[dict[str, Any]],
) -> None:
    unit = package.get("generation_unit") or {}
    score_point = package.get("score_point") or {}
    unit_id = str(unit.get("unit_id") or "")
    target_id = str(unit.get("target_node_id") or "")
    parent_id = str(unit.get("parent_level_1_node_id") or "")
    chapter_path = [str(part) for part in unit.get("chapter_path") or [] if str(part).strip()]
    target_node = node_index.get(target_id)
    parent_node = node_index.get(parent_id)

    if package_target_counts.get(target_id, 0) > 1:
        _issue(
            issues,
            "error",
            "duplicate_target_package",
            f"目录节点 {target_id} 被多个正文生成单元重复调度。",
            unit_id=unit_id,
            target_node_id=target_id,
            chapter_path=chapter_path,
        )
    if not target_node:
        _issue(
            issues,
            "error",
            "target_node_missing",
            "正文生成单元的 target_node_id 在目录树中不存在。",
            unit_id=unit_id,
            target_node_id=target_id,
            chapter_path=chapter_path,
        )
        return
    if not parent_node:
        _issue(
            issues,
            "error",
            "parent_node_missing",
            "正文生成单元的 parent_level_1_node_id 在目录树中不存在。",
            unit_id=unit_id,
            target_node_id=target_id,
            chapter_path=chapter_path,
        )
        return

    expected_path = _expected_package_path(parent_node, target_node)
    if chapter_path != expected_path:
        _issue(
            issues,
            "warning",
            "chapter_path_mismatch",
            f"正文生成单元章节路径与目录树标题不一致；目录树为“{' > '.join(expected_path)}”，输入包为“{' > '.join(chapter_path)}”。",
            unit_id=unit_id,
            target_node_id=target_id,
            chapter_path=chapter_path,
        )
    score_point_raw = str(score_point.get("score_point_raw") or "").strip()
    if chapter_path and score_point_raw and chapter_path[0] != score_point_raw:
        _issue(
            issues,
            "error",
            "score_point_heading_mismatch",
            f"一级目录标题未保持评分点原文；chapter_path[0] 为“{chapter_path[0]}”，score_point_raw 为“{score_point_raw}”。",
            unit_id=unit_id,
            target_node_id=target_id,
            chapter_path=chapter_path,
        )

    expected_children = [str(child.get("title") or "") for child in children_by_parent.get(target_id, [])]
    child_headings = [str(item) for item in unit.get("child_headings") or [] if str(item).strip()]
    if child_headings != expected_children:
        _issue(
            issues,
            "warning",
            "child_headings_mismatch",
            f"`child_headings` 与目录树子节点不一致；目录树 {len(expected_children)} 个，输入包 {len(child_headings)} 个。",
            unit_id=unit_id,
            target_node_id=target_id,
            chapter_path=chapter_path,
            expected_child_headings=expected_children,
            actual_child_headings=child_headings,
        )


def _top_level_mapping(
    node: dict[str, Any],
    packages: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    child_count = len([child for child in node.get("children") or [] if isinstance(child, dict)])
    unit_types = sorted({str((package.get("generation_unit") or {}).get("unit_type") or "") for package in packages})
    notes: list[str] = []
    if not packages:
        status = "not_scheduled"
        notes.append("未进入当前正文生成输入包")
        if str(node.get("domain") or "") == "construction":
            _issue(
                issues,
                "warning",
                "construction_level1_not_scheduled",
                "施工领域一级目录未进入正文生成输入包。",
                node_id=str(node.get("node_id") or ""),
                node_title=str(node.get("title") or ""),
            )
    else:
        status = "scheduled"
        if unit_types == ["level1_chapter"]:
            notes.append("按一级目录整体生成")
        elif unit_types == ["level2_section_group"]:
            notes.append("按二级目录拆分生成")
            _check_split_children_coverage(node, packages, issues)
        else:
            notes.append("存在混合调度颗粒度，需复核")
            status = "review"
    if str(node.get("domain") or "") != "construction" and not packages:
        notes.append("非 construction 领域，疑似被当前生成范围排除")
    return {
        "node_id": node.get("node_id"),
        "title": node.get("title"),
        "domain": node.get("domain") or "",
        "category": node.get("category") or "",
        "outline_child_count": child_count,
        "package_count": len(packages),
        "schedule_mode": ", ".join(unit_types) if unit_types else "none",
        "status": status,
        "notes": notes,
        "package_paths": [
            (package.get("generation_unit") or {}).get("chapter_path") or [] for package in packages
        ],
    }


def _check_split_children_coverage(node: dict[str, Any], packages: list[dict[str, Any]], issues: list[dict[str, Any]]) -> None:
    child_ids = [str(child.get("node_id") or "") for child in node.get("children") or [] if isinstance(child, dict)]
    child_titles = {str(child.get("node_id") or ""): str(child.get("title") or "") for child in node.get("children") or [] if isinstance(child, dict)}
    package_target_ids = [str((package.get("generation_unit") or {}).get("target_node_id") or "") for package in packages]
    missing = [child_id for child_id in child_ids if child_id not in package_target_ids]
    extra = [target_id for target_id in package_target_ids if target_id not in child_ids]
    for child_id in missing:
        _issue(
            issues,
            "warning",
            "split_child_package_missing",
            f"二级目录“{child_titles.get(child_id) or child_id}”未生成对应正文输入包。",
            node_id=str(node.get("node_id") or ""),
            node_title=str(node.get("title") or ""),
        )
    for target_id in extra:
        _issue(
            issues,
            "error",
            "split_child_package_extra",
            f"存在不属于该一级目录二级子节点的正文生成单元：{target_id}。",
            node_id=str(node.get("node_id") or ""),
            node_title=str(node.get("title") or ""),
        )


def _node_index(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}

    def walk(node: dict[str, Any]) -> None:
        node_id = str(node.get("node_id") or "")
        if node_id:
            index[node_id] = node
        for child in node.get("children") or []:
            if isinstance(child, dict):
                walk(child)

    for node in nodes:
        walk(node)
    return index


def _children_by_parent(nodes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    children: dict[str, list[dict[str, Any]]] = {}

    def walk(node: dict[str, Any]) -> None:
        node_id = str(node.get("node_id") or "")
        node_children = [child for child in node.get("children") or [] if isinstance(child, dict)]
        children[node_id] = node_children
        for child in node_children:
            walk(child)

    for node in nodes:
        walk(node)
    return children


def _packages_by_parent(packages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for package in packages:
        unit = package.get("generation_unit") or {}
        parent_id = str(unit.get("parent_level_1_node_id") or "")
        grouped.setdefault(parent_id, []).append(package)
    return grouped


def _package_target_counts(packages: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for package in packages:
        target_id = str((package.get("generation_unit") or {}).get("target_node_id") or "")
        counts[target_id] = counts.get(target_id, 0) + 1
    return counts


def _expected_package_path(parent_node: dict[str, Any], target_node: dict[str, Any]) -> list[str]:
    parent_title = str(parent_node.get("title") or "")
    target_title = str(target_node.get("title") or "")
    if str(parent_node.get("node_id") or "") == str(target_node.get("node_id") or ""):
        return [parent_title] if parent_title else []
    return [part for part in [parent_title, target_title] if part]


def _issue(issues: list[dict[str, Any]], severity: str, issue_type: str, message: str, **extra: Any) -> None:
    issue = {"severity": severity, "type": issue_type, "message": message}
    issue.update({key: value for key, value in extra.items() if value not in (None, "", [])})
    issues.append(issue)


def _issue_counts(issues: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"error": 0, "warning": 0, "info": 0}
    for issue in issues:
        severity = str(issue.get("severity") or "info")
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def _status_text(status: Any) -> str:
    return {"pass": "通过", "warning": "有警告", "fail": "未通过"}.get(str(status), str(status or "未知"))


def _mapping_status_text(status: Any) -> str:
    return {"scheduled": "已调度", "not_scheduled": "未调度", "review": "需复核"}.get(str(status), str(status or "未知"))


def _format_issue_counts(counts: dict[str, int]) -> str:
    return f"error={counts.get('error', 0)}，warning={counts.get('warning', 0)}，info={counts.get('info', 0)}"


def _cell(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", "<br>")


def _load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
