"""Bounded Mistral OCR adapter which returns only requested PDF pages."""

from __future__ import annotations

import asyncio
import base64

from mistralai import Mistral

from cowork_agent.integrations.knowledge_ingestion.models import OcrPage


class MistralOcrClient:
    def __init__(
        self, *, api_key: str, model: str, timeout_seconds: int, max_attempts: int
    ) -> None:
        if not api_key:
            raise ValueError("MISTRAL_API_KEY must be configured")
        self._client = Mistral(api_key=api_key)
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts

    async def extract_pages(self, pdf_bytes: bytes, pages: tuple[int, ...]) -> tuple[OcrPage, ...]:
        if not pages:
            return ()
        document_url = "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode("ascii")
        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    response = await self._client.ocr.process_async(
                        model=self._model,
                        document={"type": "document_url", "document_url": document_url},
                        pages=[page - 1 for page in pages],
                        include_image_base64=False,
                    )
                result = tuple(
                    OcrPage(number=page.index + 1, markdown=page.markdown.strip())
                    for page in response.pages
                )
                expected = set(pages)
                if {page.number for page in result} != expected or any(
                    not page.markdown for page in result
                ):
                    raise ValueError("OCR response is incomplete")
                return result
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self._max_attempts:
                    await asyncio.sleep(min(2**attempt, 4))
        raise RuntimeError("Mistral OCR failed") from last_error
