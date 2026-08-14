from __future__ import annotations

import zipfile
from base64 import b64encode
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from cowork_agent.integrations.knowledge_ingestion.ocr import (
    CONTENT_TYPES_ENTRY,
    MistralOcrExtractor,
    normalize_ooxml,
)


def test_normalize_ooxml_reorders_content_types_entry() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "<document/>")
        archive.writestr(CONTENT_TYPES_ENTRY, "<Types/>")
    raw_zip = buffer.getvalue()

    repacked = normalize_ooxml("policy.docx", raw_zip)

    with zipfile.ZipFile(BytesIO(repacked)) as archive:
        names = archive.namelist()
        assert names[0] == CONTENT_TYPES_ENTRY
        assert names[1] == "word/document.xml"


def test_normalize_ooxml_ignores_non_ooxml_files() -> None:
    data = b"regular-content"
    assert normalize_ooxml("file.txt", data) == data


def test_mistral_ocr_requires_api_key() -> None:
    with pytest.raises(ValueError, match="MISTRAL_API_KEY"):
        MistralOcrExtractor(api_key="")


class _FakeMistralFiles:
    def upload(self, file: dict[str, object], purpose: str) -> SimpleNamespace:
        return SimpleNamespace(id="file-123")

    def get_signed_url(self, file_id: str) -> SimpleNamespace:
        return SimpleNamespace(url=f"https://signed.example.com/{file_id}")


class _FakeMistralOcr:
    def __init__(self, pages: list[object]) -> None:
        self.pages = pages
        self.last_call: dict[str, object] | None = None

    def process(self, **kwargs: object) -> SimpleNamespace:
        self.last_call = kwargs
        return SimpleNamespace(pages=self.pages)


class _FakeMistralClient:
    def __init__(self, pages: list[object]) -> None:
        self.files = _FakeMistralFiles()
        self.ocr = _FakeMistralOcr(pages)


def test_mistral_ocr_extracts_markdown_and_images(tmp_path: Path) -> None:
    img_data = b"fake-png-data"
    img_b64 = b64encode(img_data).decode("ascii")
    pages = [
        SimpleNamespace(
            markdown="# Page 1\n\n![Figure](img-1)",
            images=[SimpleNamespace(id="img-1", image_base64=f"data:image/png;base64,{img_b64}")],
        ),
        SimpleNamespace(markdown="## Page 2\n\nText on page 2", images=[]),
    ]
    extractor = MistralOcrExtractor(api_key="test-key", image_dir=tmp_path / "images")
    extractor._client = _FakeMistralClient(pages)

    markdown = extractor.extract("document.pdf", b"pdf-bytes")

    assert "# Page 1" in markdown
    assert "## Page 2" in markdown
    assert "![Figure](images/document-img-1)" in markdown
    saved_img = tmp_path / "images" / "document-img-1"
    assert saved_img.exists()
    assert saved_img.read_bytes() == img_data


def test_mistral_ocr_raises_on_failure() -> None:
    extractor = MistralOcrExtractor(api_key="test-key")

    class _FailingClient:
        @property
        def files(self) -> object:
            raise RuntimeError("API connection timeout")

    extractor._client = _FailingClient()

    with pytest.raises(RuntimeError, match="Mistral OCR failed for doc.pdf"):
        extractor.extract("doc.pdf", b"pdf-bytes")
