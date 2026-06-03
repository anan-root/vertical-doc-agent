"""受控 Agent tool 的元数据定义。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ToolRiskLevel(str, Enum):
    READ_ONLY = "read_only"
    GENERATE_ARTIFACT = "generate_artifact"
    OVERWRITE_ARTIFACT = "overwrite_artifact"
    EXTERNAL_CALL = "external_call"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    title: str
    description: str
    risk_levels: tuple[ToolRiskLevel, ...]
    requires_approval: bool
    existing_anchor: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()
    idempotent: bool = False
    can_retry: bool = False
    rollback_strategy: str | None = None
    audit_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "risk_levels": [level.value for level in self.risk_levels],
            "requires_approval": self.requires_approval,
            "existing_anchor": self.existing_anchor,
            "input_schema": self.input_schema or {},
            "output_schema": self.output_schema or {},
            "preconditions": list(self.preconditions),
            "postconditions": list(self.postconditions),
            "idempotent": self.idempotent,
            "can_retry": self.can_retry,
            "rollback_strategy": self.rollback_strategy,
            "audit_fields": list(self.audit_fields),
        }

    @property
    def is_read_only(self) -> bool:
        return set(self.risk_levels) == {ToolRiskLevel.READ_ONLY}
