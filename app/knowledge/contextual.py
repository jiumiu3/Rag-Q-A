import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Protocol

from app.domain.models import UnitType
from app.knowledge.models import StoredUnit

TYPE_LABELS = {
    UnitType.CLAUSE: "规范条款",
    UnitType.TABLE: "规范表格",
    UnitType.TABLE_ROW: "表格行",
    UnitType.TERM: "术语定义",
    UnitType.FIGURE_NOTE: "图注",
    UnitType.APPENDIX: "附录",
}


class ContextProvider(Protocol):
    strategy: str
    version: str

    def build(
        self,
        units: Sequence[StoredUnit],
        tables: Mapping[str, Mapping[str, object]],
    ) -> list[StoredUnit]: ...


class ContextualTextBuilder:
    """根据结构化字段生成可复现的索引前缀，不修改规范原文。"""

    strategy = "deterministic"

    def __init__(self, version: str = "v1", enabled: bool = True) -> None:
        self.version = version
        self.enabled = enabled

    @property
    def config_hash(self) -> str:
        payload = json.dumps(
            {"enabled": self.enabled, "strategy": self.strategy, "version": self.version},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def build(
        self,
        units: Sequence[StoredUnit],
        tables: Mapping[str, Mapping[str, object]],
    ) -> list[StoredUnit]:
        by_id = {item.unit.unit_id: item for item in units}
        table_units = {
            item.unit.table_id: item for item in units if item.unit.unit_type == UnitType.TABLE
        }
        output: list[StoredUnit] = []
        for stored in units:
            effective = self._with_validated_table_status(stored, tables)
            prefix = self._prefix(effective, by_id, table_units, tables) if self.enabled else ""
            retrieval_text = "\n".join(filter(None, (prefix, stored.unit.content)))
            output.append(
                effective.model_copy(
                    update={"context_prefix": prefix, "retrieval_text": retrieval_text}
                )
            )
        return output

    @staticmethod
    def _with_validated_table_status(
        stored: StoredUnit, tables: Mapping[str, Mapping[str, object]]
    ) -> StoredUnit:
        if stored.unit.unit_type != UnitType.TABLE_ROW:
            return stored
        table = tables.get(stored.unit.table_id or "", {})
        headers = ContextualTextBuilder._strings(table.get("headers"))
        cells = ContextualTextBuilder._row_cells(stored.unit.content)
        if headers and len(headers) != len(cells):
            return stored.model_copy(update={"parse_status": "NEEDS_MANUAL_ANNOTATION"})
        return stored

    def _prefix(
        self,
        stored: StoredUnit,
        by_id: Mapping[str, StoredUnit],
        table_units: Mapping[str | None, StoredUnit],
        tables: Mapping[str, Mapping[str, object]],
    ) -> str:
        unit = stored.unit
        lines = [f"[标准：{stored.standard_code}]", f"[文件：{stored.file_name}]"]
        path = self._path(stored, by_id, table_units)
        if path:
            lines.append(f"[位置：{' > '.join(path)}]")
        if unit.unit_type == UnitType.CLAUSE:
            current = " ".join(filter(None, (stored.clause_number, unit.title)))
            lines.append(f"[当前条款：{current}]")
            parent = by_id.get(unit.parent_id or "")
            if parent:
                label = " ".join(filter(None, (parent.clause_number, parent.unit.title)))
                lines.append(f"[父条款：{label}]")
        elif unit.unit_type in {UnitType.TABLE, UnitType.TABLE_ROW}:
            table = tables.get(unit.table_id or "", {})
            number = stored.table_number or str(table.get("table_number") or "")
            title = unit.title or str(table.get("title") or "")
            lines.append(f"[表格：表 {number} {title}]".replace("  ", " "))
            headers = ContextualTextBuilder._strings(table.get("headers"))
            if headers:
                lines.append(f"[表头：{' | '.join(headers)}]")
            lines.append(f"[解析状态：{stored.parse_status or 'UNKNOWN'}]")
            if unit.unit_type == UnitType.TABLE_ROW:
                cells = self._row_cells(unit.content)
                if headers and len(headers) == len(cells):
                    lines.extend(
                        f"{header}：{cell}" for header, cell in zip(headers, cells, strict=True)
                    )
                elif headers:
                    lines.append(
                        f"[列映射：NEEDS_MANUAL_ANNOTATION；原始单元格：{' | '.join(cells)}]"
                    )
        lines.append(f"[类型：{TYPE_LABELS[unit.unit_type]}]")
        return "\n".join(lines)

    @staticmethod
    def _path(
        stored: StoredUnit,
        by_id: Mapping[str, StoredUnit],
        table_units: Mapping[str | None, StoredUnit],
    ) -> list[str]:
        def readable(items: Sequence[str], current_id: str) -> list[str]:
            return [
                " ".join(filter(None, (by_id[item].clause_number, by_id[item].unit.title)))
                for item in items
                if item != current_id and item in by_id
            ]

        if stored.unit.chapter_path:
            return readable(stored.unit.chapter_path, stored.unit.unit_id)
        table = table_units.get(stored.unit.table_id)
        if table and table.unit.chapter_path:
            return readable(table.unit.chapter_path, table.unit.unit_id)
        parent = by_id.get(stored.unit.parent_id or "")
        return readable(parent.unit.chapter_path, stored.unit.unit_id) if parent else []

    @staticmethod
    def _row_cells(content: str) -> list[str]:
        return [item.strip() for item in content.split("|")]

    @staticmethod
    def _strings(value: object) -> list[str]:
        return [str(item) for item in value] if isinstance(value, list) else []
