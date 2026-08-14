from __future__ import annotations

import zipfile
from base64 import b64decode
from io import BytesIO
from pathlib import Path
from typing import Any

CONTENT_TYPES_ENTRY = "[Content_Types].xml"
OOXML_SUFFIXES = (".docx", ".pptx", ".xlsx")


class MistralOcrExtractor:
    """Advanced extraction mode using Mistral OCR API (`mistral-ocr-latest`).

    - Handles OOXML zip repacking so [Content_Types].xml is the first entry.
    - Extracts layout-aware Markdown from PDFs and Word documents.
    - Saves figure images under `image_dir` and updates image markdown links.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "mistral-ocr-latest",
        timeout_seconds: float = 120.0,
        image_dir: Path | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Advanced extraction mode requires MISTRAL_API_KEY")
        self._api_key = api_key
        self.model = model
        self.timeout_ms = int(timeout_seconds * 1000)
        self.image_dir = image_dir
        self._client: Any | None = None

    def client(self) -> Any:
        if self._client is None:
            from mistralai import Mistral

            self._client = Mistral(api_key=self._api_key)
        return self._client

    def extract(self, filename: str, content: bytes) -> str:
        client = self.client()
        images = (
            {"include_image_base64": True}
            if self.image_dir is not None
            else {"image_limit": 0}
        )
        try:
            uploaded = client.files.upload(
                file={"file_name": filename, "content": normalize_ooxml(filename, content)},
                purpose="ocr",
            )
            signed_url = client.files.get_signed_url(file_id=uploaded.id).url
            response = client.ocr.process(
                model=self.model,
                document={"type": "document_url", "document_url": signed_url},
                extract_header=True,
                extract_footer=True,
                timeout_ms=self.timeout_ms,
                **images,
            )
        except Exception as exc:
            raise RuntimeError(f"Mistral OCR failed for {filename}: {exc}") from exc
        pages = [self._page_markdown(filename, page) for page in response.pages]
        return "\n\n".join(page for page in pages if page)

    def _page_markdown(self, filename: str, page: Any) -> str:
        markdown = (getattr(page, "markdown", "") or "").strip()
        if self.image_dir is None or not markdown:
            return markdown
        page_images = getattr(page, "images", None) or []
        for image in page_images:
            payload = getattr(image, "image_base64", None)
            image_id = getattr(image, "id", None)
            if not payload or not image_id:
                continue
            name = f"{Path(filename).stem}-{image_id}"
            self.image_dir.mkdir(parents=True, exist_ok=True)
            (self.image_dir / name).write_bytes(b64decode(payload.split(",", 1)[-1]))
            markdown = markdown.replace(f"]({image_id})", f"]({self.image_dir.name}/{name})")
        return markdown


def normalize_ooxml(filename: str, content: bytes) -> bytes:
    """Repack an OOXML zip so `[Content_Types].xml` is the first entry.

    Mistral sniffs magic bytes and ignores the declared content type. If
    `[Content_Types].xml` is stored late in the archive, Mistral returns 422.
    """
    if not filename.lower().endswith(OOXML_SUFFIXES):
        return content
    source = BytesIO(content)
    if not zipfile.is_zipfile(source):
        return content
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if CONTENT_TYPES_ENTRY not in names or names[0] == CONTENT_TYPES_ENTRY:
            return content
        ordered = [CONTENT_TYPES_ENTRY] + [name for name in names if name != CONTENT_TYPES_ENTRY]
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as repacked:
            for name in ordered:
                repacked.writestr(archive.getinfo(name), archive.read(name))
    return buffer.getvalue()
