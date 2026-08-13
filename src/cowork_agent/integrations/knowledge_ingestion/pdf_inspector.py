"""Safe local adapter for PDF classification and native Markdown extraction."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TypeAlias

from .models import PdfInspection, PdfKind

CommandRunner: TypeAlias = Callable[[Sequence[str]], str]


class PdfInspector:
    """Classify a PDF and extract Markdown for its native-text pages."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or _run_command

    def inspect(self, path: Path) -> PdfInspection:
        detection = _load_json(self._run(("detect-pdf", str(path), "--json")), "detection")
        kind, page_count, ocr_pages = _parse_detection(detection)
        native_pages = tuple(
            number for number in range(1, page_count + 1) if number not in ocr_pages
        )
        markdown_by_page = self._extract_native_markdown(path, native_pages)
        return PdfInspection(
            kind=kind,
            page_count=page_count,
            pages_needing_ocr=ocr_pages,
            native_markdown_by_page=markdown_by_page,
        )

    def _extract_native_markdown(self, path: Path, pages: tuple[int, ...]) -> Mapping[int, str]:
        if not pages:
            return {}
        page_argument = ",".join(str(page) for page in pages)
        payload = _load_json(
            self._run(("pdf2md", str(path), "--json", "--pages", "--select-pages", page_argument)),
            "Markdown",
        )
        return _parse_markdown_pages(payload, pages)

    def _run(self, command: Sequence[str]) -> str:
        try:
            return self._runner(command)
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError("Local PDF inspection command failed") from error


def _run_command(command: Sequence[str]) -> str:
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout


def _load_json(output: str, description: str) -> object:
    try:
        return json.loads(output)
    except json.JSONDecodeError as error:
        raise ValueError(f"PDF {description} output is invalid") from error


def _parse_detection(payload: object) -> tuple[PdfKind, int, tuple[int, ...]]:
    if not isinstance(payload, dict):
        raise ValueError("PDF detection output is invalid")
    raw_kind = payload.get("pdf_type")
    raw_page_count = payload.get("page_count")
    raw_ocr_pages = payload.get("pages_needing_ocr")
    if not isinstance(raw_kind, str):
        raise ValueError("PDF detection output has an invalid type")
    try:
        kind = PdfKind(raw_kind)
    except ValueError as error:
        raise ValueError("PDF detection output has an invalid type") from error
    if (
        not isinstance(raw_page_count, int)
        or isinstance(raw_page_count, bool)
        or raw_page_count < 1
    ):
        raise ValueError("PDF detection output has an invalid page count")
    if not isinstance(raw_ocr_pages, list) or any(
        not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= raw_page_count
        for page in raw_ocr_pages
    ):
        raise ValueError("PDF detection output has invalid OCR pages")
    if len(set(raw_ocr_pages)) != len(raw_ocr_pages):
        raise ValueError("PDF detection output has invalid OCR pages")
    return kind, raw_page_count, tuple(raw_ocr_pages)


def _parse_markdown_pages(payload: object, expected_pages: tuple[int, ...]) -> Mapping[int, str]:
    if not isinstance(payload, dict) or not isinstance(markdown := payload.get("markdown"), str):
        raise ValueError("PDF Markdown output is invalid")
    parts = re.split(r"<!-- Page ([1-9][0-9]*) -->\s*", markdown)
    if len(parts) < 3 or parts[0].strip():
        raise ValueError("PDF Markdown output is invalid")
    markdown_by_page: dict[int, str] = {}
    for index in range(1, len(parts), 2):
        number = int(parts[index])
        page_markdown = parts[index + 1].strip()
        if number not in expected_pages or number in markdown_by_page:
            raise ValueError("PDF Markdown output is invalid")
        markdown_by_page[number] = page_markdown
    for number in expected_pages:
        markdown_by_page.setdefault(
            number,
            f"<!-- Page {number} has no standalone marker in extractor output. -->",
        )
    return markdown_by_page
