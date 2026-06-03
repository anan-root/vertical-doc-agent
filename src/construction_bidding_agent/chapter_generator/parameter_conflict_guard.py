"""招标硬约束与历史素材参数冲突校验。"""

from __future__ import annotations

import re
from typing import Any


NUMBER_PATTERN = r"\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百]+"
PREFIX_OPERATORS = {
    "不少于": ">=",
    "不低于": ">=",
    "不小于": ">=",
    "至少": ">=",
    "满": ">=",
    "不超过": "<=",
    "不高于": "<=",
    "不大于": "<=",
    "不多于": "<=",
    "最多": "<=",
    "等于": "==",
}
SUFFIX_OPERATORS = {
    "及以上": ">=",
    "以上": ">=",
    "及其以上": ">=",
    "及以下": "<=",
    "以下": "<=",
    "以内": "<=",
}
GRADE_RANKS = {"特级": 0, "一级": 1, "二级": 2, "三级": 3, "四级": 4}


def build_parameter_conflict_scan(
    *,
    parse_result: dict[str, Any] | None = None,
    target_section: dict[str, Any] | None = None,
    conflicts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构建生成包可携带的参数冲突扫描摘要。"""

    hard_constraints = extract_hard_constraints(parse_result=parse_result, target_section=target_section)
    return {
        "enabled": bool(hard_constraints),
        "blocking_on_output": True,
        "hard_constraints": hard_constraints,
        "conflicts": list(conflicts or []),
    }


def apply_parameter_conflict_guard(
    materials: list[dict[str, Any]],
    *,
    parse_result: dict[str, Any] | None = None,
    target_section: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对素材候选执行硬参数冲突校验，并就地降级冲突素材。"""

    scan = build_parameter_conflict_scan(parse_result=parse_result, target_section=target_section)
    constraints = scan.get("hard_constraints") or []
    if not constraints:
        return scan

    conflicts: list[dict[str, Any]] = []
    for material in materials:
        if not isinstance(material, dict):
            continue
        material_conflicts = find_material_parameter_conflicts(material, constraints)
        if not material_conflicts:
            continue
        material["parameter_conflicts"] = material_conflicts
        material["review_required"] = True
        material["original_reuse_level_before_parameter_conflict"] = material.get("reuse_level")
        material["reuse_level"] = "manual_review"
        conflicts.extend(material_conflicts)

    scan["conflicts"] = conflicts
    return scan


def find_material_parameter_conflicts(
    material: dict[str, Any],
    hard_constraints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """查找单个素材与招标硬约束之间的明确冲突。"""

    claims = extract_parameter_claims_from_text(_material_text(material), source="material_claim")
    conflicts: list[dict[str, Any]] = []
    for constraint in hard_constraints:
        for claim in claims:
            if _claim_violates_constraint(claim, constraint):
                conflicts.append(_conflict(material, constraint, claim))
                break
    return conflicts


def find_output_parameter_conflict_residuals(output: dict[str, Any], package: dict[str, Any]) -> list[dict[str, Any]]:
    """检查最终正文中是否残留低于招标硬约束的参数。"""

    scan = (package.get("generation_constraints") or {}).get("parameter_conflict_scan") or {}
    if scan.get("blocking_on_output") is False:
        return []
    constraints = scan.get("hard_constraints") or []
    if not constraints:
        return []

    claims = extract_parameter_claims_from_text(_collect_text(output), source="chapter_output")
    residuals: list[dict[str, Any]] = []
    for constraint in constraints:
        for claim in claims:
            if _claim_violates_constraint(claim, constraint):
                residuals.append(
                    {
                        "type": "parameter_conflict_residual",
                        "risk_level": "high",
                        "category": constraint.get("category"),
                        "requirement": constraint,
                        "material_claim": claim,
                        "action": "blocking_output",
                        "message": _message(constraint, claim),
                    }
                )
                break
    return residuals


def extract_hard_constraints(
    *,
    parse_result: dict[str, Any] | None = None,
    target_section: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """从当前招标解析结果和目标章节中抽取高置信硬约束。"""

    items: list[tuple[str, str]] = []
    if target_section:
        items.extend(
            [
                ("score_point", str(target_section.get("score_rule") or "")),
                ("score_point", str(target_section.get("original_text") or "")),
                ("score_point", str(target_section.get("query") or "")),
            ]
        )
    if parse_result:
        for point in parse_result.get("technical_score_points") or []:
            if not isinstance(point, dict):
                continue
            items.extend(
                [
                    ("score_point", str(point.get("score_rule") or point.get("score_standard_raw") or "")),
                    ("score_point", str(point.get("original_text") or point.get("catalog_level_1_title") or "")),
                ]
            )
        for requirement in parse_result.get("technical_bid_requirements") or []:
            if not isinstance(requirement, dict):
                continue
            items.append(("technical_requirement", str(requirement.get("raw_clause") or requirement.get("content") or "")))

    constraints: list[dict[str, Any]] = []
    seen: set[tuple[str, str, float, str, str]] = set()
    for source, text in items:
        for item in extract_parameter_claims_from_text(text, source=source):
            key = (
                str(item.get("category")),
                str(item.get("operator")),
                float(item.get("value") or 0),
                str(item.get("unit")),
                str(item.get("text")),
            )
            if key in seen:
                continue
            seen.add(key)
            constraints.append(item)
    return constraints


def extract_parameter_claims_from_text(text: str, *, source: str) -> list[dict[str, Any]]:
    """从文本中抽取年限、数量、工期、等级等高置信参数。"""

    normalized = _normalize_text(text)
    if not normalized:
        return []
    claims: list[dict[str, Any]] = []
    claims.extend(_extract_year_claims(normalized, source=source))
    claims.extend(_extract_duration_claims(normalized, source=source))
    claims.extend(_extract_count_claims(normalized, source=source))
    claims.extend(_extract_grade_claims(normalized, source=source))
    return _dedupe_claims(claims)


def material_has_parameter_conflict(material: dict[str, Any]) -> bool:
    return bool(material.get("parameter_conflicts"))


def parameter_conflict_warnings(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for material in materials:
        for conflict in material.get("parameter_conflicts") or []:
            warnings.append(
                {
                    "material_slice_id": material.get("material_slice_id"),
                    "risk_level": "high",
                    "reason_type": "parameter_conflict",
                    "category": conflict.get("category"),
                    "reason": conflict.get("message") or "素材参数不满足当前招标硬约束，已降级为人工复核。",
                    "conflict": conflict,
                }
            )
    return warnings


def _extract_year_claims(text: str, *, source: str) -> list[dict[str, Any]]:
    if not _has_any(text, ["经验", "经历", "年限", "从业", "项目负责人", "技术负责人", "施工管理"]):
        return []
    return _extract_numeric_claims(
        text,
        source=source,
        category="experience_years",
        unit_pattern=r"年",
        unit="year",
        context_terms=["经验", "经历", "年限", "从业", "项目负责人", "技术负责人", "施工管理"],
    )


def _extract_duration_claims(text: str, *, source: str) -> list[dict[str, Any]]:
    if not _has_any(text, ["工期", "日历天", "计划开工", "计划竣工", "节点工期"]):
        return []
    return _extract_numeric_claims(
        text,
        source=source,
        category="duration_days",
        unit_pattern=r"(?:日历天|天)",
        unit="day",
        context_terms=["工期", "日历天", "计划开工", "计划竣工", "节点工期"],
    )


def _extract_count_claims(text: str, *, source: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    count_specs = [
        ("similar_performance_count", r"(?:个|项)", "count", ["类似业绩", "业绩"]),
        ("personnel_count", r"人", "count", ["人员", "管理人员", "技术人员", "项目团队", "项目班子"]),
        ("equipment_count", r"(?:台|套)", "count", ["机械", "设备", "塔吊", "施工电梯", "机具"]),
    ]
    for category, unit_pattern, unit, terms in count_specs:
        if not _has_any(text, terms):
            continue
        result.extend(
            _extract_numeric_claims(
                text,
                source=source,
                category=category,
                unit_pattern=unit_pattern,
                unit=unit,
                context_terms=terms,
            )
        )
    return result


def _extract_grade_claims(text: str, *, source: str) -> list[dict[str, Any]]:
    if not _has_any(text, ["资质", "施工总承包", "专业承包"]):
        return []
    claims: list[dict[str, Any]] = []
    pattern = re.compile(r"(特级|一级|二级|三级|四级).{0,12}(资质|施工总承包|专业承包)|(资质|施工总承包|专业承包).{0,12}(特级|一级|二级|三级|四级)")
    for match in pattern.finditer(text):
        grade = match.group(1) or match.group(4)
        if not grade:
            continue
        claims.append(
            {
                "source": source,
                "text": _window(text, match.start(), match.end()),
                "operator": _operator_near(text, match.start(), match.end()) or "==",
                "value": GRADE_RANKS[grade],
                "unit": "grade_rank",
                "category": "qualification_grade",
                "label": grade,
            }
        )
    return claims


def _extract_numeric_claims(
    text: str,
    *,
    source: str,
    category: str,
    unit_pattern: str,
    unit: str,
    context_terms: list[str],
) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    number_unit = re.compile(rf"({NUMBER_PATTERN})\s*{unit_pattern}")
    for match in number_unit.finditer(text):
        start, end = match.span()
        local = _window(text, start, end)
        if context_terms and not _has_any(local, context_terms):
            continue
        value = _number_value(match.group(1))
        if value is None:
            continue
        operator = _operator_near(text, start, end) or "=="
        claims.append(
            {
                "source": source,
                "text": local,
                "operator": operator,
                "value": value,
                "unit": unit,
                "category": category,
            }
        )
    return claims


def _claim_violates_constraint(claim: dict[str, Any], constraint: dict[str, Any]) -> bool:
    if claim.get("category") != constraint.get("category"):
        return False
    if claim.get("unit") != constraint.get("unit"):
        return False
    claim_value = claim.get("value")
    required_value = constraint.get("value")
    if claim_value is None or required_value is None:
        return False
    try:
        claim_number = float(claim_value)
        required_number = float(required_value)
    except (TypeError, ValueError):
        return False

    operator = str(constraint.get("operator") or "==")
    if operator == ">=":
        return claim_number < required_number
    if operator == "<=":
        return claim_number > required_number
    return claim_number != required_number


def _conflict(material: dict[str, Any], constraint: dict[str, Any], claim: dict[str, Any]) -> dict[str, Any]:
    material_claim = dict(claim)
    material_claim["material_slice_id"] = material.get("material_slice_id")
    return {
        "type": "parameter_conflict",
        "risk_level": "high",
        "category": constraint.get("category"),
        "requirement": constraint,
        "material_claim": material_claim,
        "action": "downgraded_to_manual_review",
        "message": _message(constraint, claim),
    }


def _message(constraint: dict[str, Any], claim: dict[str, Any]) -> str:
    category = str(constraint.get("category") or "")
    if category == "experience_years":
        return f"素材年限 {claim.get('value'):g} 年不满足招标要求（{constraint.get('text')}），已降级为人工复核，不得作为正文主素材。"
    if category == "duration_days":
        return f"素材工期 {claim.get('value'):g} 天不满足招标要求（{constraint.get('text')}），已降级为人工复核。"
    if category.endswith("_count"):
        return f"素材数量 {claim.get('value'):g} 不满足招标要求（{constraint.get('text')}），已降级为人工复核。"
    if category == "qualification_grade":
        return f"素材资质等级“{claim.get('label') or claim.get('value')}”不满足招标要求（{constraint.get('text')}），已降级为人工复核。"
    return "素材参数不满足当前招标硬约束，已降级为人工复核。"


def _operator_near(text: str, start: int, end: int) -> str | None:
    before = text[max(0, start - 8) : start]
    after = text[end : min(len(text), end + 8)]
    for raw, operator in PREFIX_OPERATORS.items():
        if raw in before:
            return operator
    for raw, operator in SUFFIX_OPERATORS.items():
        if raw in after:
            return operator
    return None


def _material_text(material: dict[str, Any]) -> str:
    parts: list[str] = []
    parts.append(str(material.get("title") or ""))
    parts.extend(str(item) for item in material.get("section_path") or [])
    for paragraph in material.get("paragraphs") or []:
        if isinstance(paragraph, dict):
            parts.append(str(paragraph.get("text_preview") or ""))
    for table in material.get("tables") or []:
        if not isinstance(table, dict):
            continue
        parts.extend(str(item) for item in table.get("header_preview") or [])
        for row in table.get("row_previews") or []:
            if not isinstance(row, dict):
                continue
            for cell in row.get("cells") or []:
                if isinstance(cell, dict):
                    parts.append(str(cell.get("text_preview") or ""))
    return " ".join(part for part in parts if part.strip())


def _collect_text(value: Any) -> str:
    parts: list[str] = []
    if isinstance(value, dict):
        for child in value.values():
            parts.append(_collect_text(child))
    elif isinstance(value, list):
        for child in value:
            parts.append(_collect_text(child))
    elif isinstance(value, str):
        parts.append(value)
    return " ".join(part for part in parts if part)


def _window(text: str, start: int, end: int, *, size: int = 24) -> str:
    return text[max(0, start - size) : min(len(text), end + size)].strip()


def _normalize_text(text: str) -> str:
    table = str.maketrans("０１２３４５６７８９，。；：！（）", "0123456789,。；:!()")
    return str(text or "").translate(table).replace(" ", "")


def _has_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def _number_value(raw: str) -> float | None:
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return _chinese_number_value(raw)


def _chinese_number_value(raw: str) -> float | None:
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if raw in digits:
        return float(digits[raw])
    if "百" in raw:
        left, _, right = raw.partition("百")
        hundreds = digits.get(left, 1 if not left else 0)
        tail = int(_chinese_number_value(right) or 0) if right else 0
        return float(hundreds * 100 + tail)
    if "十" in raw:
        left, _, right = raw.partition("十")
        tens = digits.get(left, 1 if not left else 0)
        ones = digits.get(right, 0) if right else 0
        return float(tens * 10 + ones)
    total = 0
    for char in raw:
        if char not in digits:
            return None
        total = total * 10 + digits[char]
    return float(total)


def _dedupe_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, float, str, str]] = set()
    for claim in claims:
        key = (
            str(claim.get("category")),
            str(claim.get("operator")),
            float(claim.get("value") or 0),
            str(claim.get("unit")),
            str(claim.get("text")),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(claim)
    return result
