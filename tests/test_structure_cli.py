from pathlib import Path

import pytest

from scripts.build_structure import resolve_ocr_path


def test_resolve_existing_relative_ocr_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data" / "ocr" / "doc-test"
    target.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert resolve_ocr_path(Path("data/ocr/doc-test")) == target.resolve()


def test_resolve_missing_path_keeps_original_for_clear_parser_error(tmp_path: Path) -> None:
    path = tmp_path / "missing"

    assert resolve_ocr_path(path) == path
