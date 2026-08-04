from pathlib import Path

import yaml
from pydantic import ValidationError

from app.core.exceptions import IngestionError
from app.ingestion.models import PageOCR
from app.ingestion.structure_models import (
    AppliedCorrection,
    CorrectionAction,
    CorrectionSet,
)


def load_corrections(path: Path | None) -> CorrectionSet:
    if path is None or not path.exists():
        return CorrectionSet()
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {"corrections": []}
        return CorrectionSet.model_validate(payload)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise IngestionError(f"无法读取人工修订文件：{path}", details={"reason": str(exc)}) from exc


def apply_corrections(
    pages: list[PageOCR], correction_set: CorrectionSet, document_id: str
) -> tuple[list[PageOCR], list[AppliedCorrection]]:
    """在内存副本应用补丁，永不覆盖 M1 原始 OCR JSONL。"""
    copied = [page.model_copy(deep=True) for page in pages]
    audits: list[AppliedCorrection] = []
    page_map = {page.page_number: page for page in copied}

    for correction in correction_set.corrections:
        if correction.document_id and correction.document_id != document_id:
            continue
        page = page_map.get(correction.page_number)
        if page is None:
            audits.append(
                AppliedCorrection(
                    correction_id=correction.correction_id,
                    applied=False,
                    message="目标页面不存在",
                )
            )
            continue
        if correction.action == CorrectionAction.EXCLUDE_PAGE:
            page.error = f"人工排除：{correction.reason}"
            page.blocks = []
            page.raw_text = ""
            page.cleaned_text = ""
            audits.append(
                AppliedCorrection(
                    correction_id=correction.correction_id,
                    applied=True,
                    message="页面已从结构解析中排除",
                )
            )
            continue

        block = next((item for item in page.blocks if item.block_id == correction.block_id), None)
        if block is None:
            audits.append(
                AppliedCorrection(
                    correction_id=correction.correction_id,
                    applied=False,
                    message="目标 OCR 块不存在",
                )
            )
            continue
        if correction.old_text is not None and block.text != correction.old_text:
            audits.append(
                AppliedCorrection(
                    correction_id=correction.correction_id,
                    applied=False,
                    message="原文校验不匹配，未应用补丁",
                )
            )
            continue
        if correction.action == CorrectionAction.DROP_BLOCK:
            page.blocks = [item for item in page.blocks if item.block_id != block.block_id]
            message = "OCR 块已删除"
        else:
            block.text = correction.new_text or ""
            message = "OCR 块文本已替换"
        page.raw_text = "\n".join(item.text for item in page.blocks)
        page.cleaned_text = page.raw_text
        audits.append(
            AppliedCorrection(
                correction_id=correction.correction_id,
                applied=True,
                message=message,
            )
        )
    return copied, audits
