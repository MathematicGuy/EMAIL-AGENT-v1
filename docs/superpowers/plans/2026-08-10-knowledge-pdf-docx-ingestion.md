# Kế hoạch triển khai ingest PDF/DOCX cho Knowledge RAG

> **Dành cho agentic workers:** BẮT BUỘC dùng `superpowers:subagent-driven-development` (khuyến nghị) hoặc `superpowers:executing-plans` để triển khai từng task. Các bước dùng checkbox (`- [ ]`) để theo dõi.

**Mục tiêu:** Thêm CLI chuyển PDF/DOCX trong `data/raw/` thành Markdown trong `data/extracted/`, dùng Mistral OCR đúng khi PDF cần OCR và tạo manifest an toàn.

**Kiến trúc:** Một package ingestion tách khỏi email/RAG runtime. `pdf-inspector` CLI phân loại/trích native PDF local; adapter Mistral được inject qua port cho test không cần mạng. Service điều phối hash, manifest và ghi output nguyên tử; RAG hiện hữu tiếp tục chỉ đọc Markdown.

**Công nghệ:** Python 3.11+, `python-docx`, `mistralai`, CLI Rust `pdf-inspector`, pytest, ruff, mypy strict.

## Ràng buộc toàn cục

- Chỉ ingest tài liệu knowledge do quản trị viên đặt trong `data/raw/`; không tải/OCR Gmail attachment.
- Không log bytes PDF/DOCX, Base64, Markdown/OCR output, API key hoặc raw response.
- Chỉ nhận `.pdf`/`.docx`; size tối đa 25 MiB, tối đa 100 trang PDF và 100 trang OCR/file.
- `MISTRAL_API_KEY` chỉ lấy từ environment. Mistral chỉ nhận trang PDF cần OCR.
- Chỉ ghi Markdown không-rỗng qua atomic rename; output cũ hợp lệ không bị xóa khi lần ingest mới thất bại.
- Manifest `.data/knowledge-ingestion-manifest.json` không chứa text/secret.
- Operator tự reindex Qdrant sau ingest; CLI không ghi Qdrant.

## Cấu trúc file

| File | Trách nhiệm |
| --- | --- |
| `pyproject.toml` | Dependency `python-docx`, `mistralai`; entry point `mail-todo-ingest-knowledge`. |
| `src/cowork_agent/config.py` | `KnowledgeIngestionSettings` cho secret, model, timeout, retry, limits. |
| `src/cowork_agent/integrations/knowledge_ingestion/models.py` | Contract dữ liệu bất biến. |
| `.../manifest.py` | SHA-256, skip, JSON atomic và Markdown atomic. |
| `.../pdf_inspector.py` | Adapter `detect-pdf --json` và `pdf2md --json`, không log output. |
| `.../mistral_ocr.py` | Gọi Mistral chỉ cho các trang OCR. |
| `.../docx_extractor.py` | DOCX local thành Markdown. |
| `.../service.py` | Điều phối, merge theo trang, reason code và report. |
| `src/cowork_agent/ingestion_cli.py` | CLI `--source`, `--output`, `--force`, `--dry-run`. |
| `tests/unit/integrations/knowledge_ingestion/` | Tests fake adapters/service/manifest. |

## Task 1: Contract, dependency và cấu hình

**Files:** Modify `pyproject.toml`, `src/cowork_agent/config.py`; create `src/cowork_agent/integrations/knowledge_ingestion/{__init__,models}.py`, `tests/unit/test_knowledge_ingestion_config.py`.

**Produces:** `KnowledgeIngestionSettings`, `PdfKind`, `PdfInspection`, `OcrPage`, `ExtractionResult`, `ManifestEntry`, `IngestionOutcome`.

- [ ] **Step 1: Viết test fail cho settings**

```python
def test_settings_require_key_only_when_ocr_enabled() -> None:
    assert (
        KnowledgeIngestionSettings.from_env(
            {"KNOWLEDGE_INGEST_OCR_ENABLED": "false"}, load_env_file=False
        ).ocr_enabled
        is False
    )
    with pytest.raises(ValueError, match="MISTRAL_API_KEY"):
        KnowledgeIngestionSettings.from_env({}, load_env_file=False)


def test_settings_hide_secret() -> None:
    settings = KnowledgeIngestionSettings.from_env(
        {"MISTRAL_API_KEY": "secret"}, load_env_file=False
    )
    assert "secret" not in repr(settings)
```

- [ ] **Step 2: Xác nhận test fail**

Run: `python -m pytest tests/unit/test_knowledge_ingestion_config.py -q`  
Expected: FAIL vì `KnowledgeIngestionSettings` chưa tồn tại.

- [ ] **Step 3: Cài đặt contract/config tối thiểu**

Thêm `python-docx`, `mistralai` và entry point `mail-todo-ingest-knowledge = "cowork_agent.ingestion_cli:main"`. Settings default chính xác: model `mistral-ocr-latest`, timeout 60, attempts 3, max bytes `26_214_400`, page/OCR limit 100. `api_key` dùng `field(repr=False)`.

```python
class PdfKind(StrEnum):
    TEXT_BASED = "text_based"
    SCANNED = "scanned"
    IMAGE_BASED = "image_based"
    MIXED = "mixed"


@dataclass(frozen=True, slots=True)
class PdfInspection:
    kind: PdfKind
    page_count: int
    pages_needing_ocr: tuple[int, ...]
    native_markdown_by_page: Mapping[int, str]
```

- [ ] **Step 4: Verify và commit**

Run: `python -m pytest tests/unit/test_knowledge_ingestion_config.py -q; python -m ruff check src/cowork_agent/config.py src/cowork_agent/integrations/knowledge_ingestion tests/unit/test_knowledge_ingestion_config.py; python -m mypy src`  
Expected: PASS.

```powershell
git add pyproject.toml src/cowork_agent/config.py src/cowork_agent/integrations/knowledge_ingestion tests/unit/test_knowledge_ingestion_config.py
git commit -m "feat: add knowledge ingestion configuration"
```

## Task 2: Manifest và ghi atomic

**Files:** Create `src/cowork_agent/integrations/knowledge_ingestion/manifest.py`, `tests/unit/integrations/knowledge_ingestion/test_manifest.py`.

**Produces:** `ManifestStore.load()`, `should_skip(source, sha256)`, `record(entry)`, `write_markdown_atomically(path, markdown)`.

- [ ] **Step 1: Viết test fail**

```python
def test_manifest_skips_only_successful_unchanged_source(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "manifest.json")
    store.record(ManifestEntry(source="a.pdf", sha256="abc", status="succeeded", output="a.md"))
    assert store.should_skip("a.pdf", "abc") is True
    assert store.should_skip("a.pdf", "different") is False


def test_empty_markdown_does_not_replace_existing(tmp_path: Path) -> None:
    destination = tmp_path / "policy.md"
    destination.write_text("# Existing\n", encoding="utf-8")
    with pytest.raises(ValueError, match="non-empty"):
        write_markdown_atomically(destination, " \n")
    assert destination.read_text(encoding="utf-8") == "# Existing\n"
```

- [ ] **Step 2: Xác nhận fail, rồi cài đặt**

Run: `python -m pytest tests/unit/integrations/knowledge_ingestion/test_manifest.py -q`  
Expected: FAIL trước implementation.

Hash file bằng SHA-256 block 64 KiB. Chỉ skip `succeeded` với hash trùng. Ghi manifest và Markdown vào sibling temporary file, `flush`/`fsync`, sau đó `Path.replace`. Manifest chỉ lưu source/output/hash/extractor/status/page count/time/reason code.

- [ ] **Step 3: Verify và commit**

Run: `python -m pytest tests/unit/integrations/knowledge_ingestion/test_manifest.py -q; python -m ruff check src/cowork_agent/integrations/knowledge_ingestion/manifest.py tests/unit/integrations/knowledge_ingestion/test_manifest.py; python -m mypy src`  
Expected: PASS.

```powershell
git add src/cowork_agent/integrations/knowledge_ingestion/manifest.py tests/unit/integrations/knowledge_ingestion/test_manifest.py
git commit -m "feat: add safe knowledge ingestion manifest"
```

## Task 3: DOCX local và PDF Inspector

**Files:** Create `.../docx_extractor.py`, `.../pdf_inspector.py`, `tests/unit/integrations/knowledge_ingestion/test_docx_extractor.py`, `tests/unit/integrations/knowledge_ingestion/test_pdf_inspector.py`.

**Produces:** `DocxExtractor.extract(path) -> ExtractionResult`; `PdfInspector.inspect(path) -> PdfInspection`. Task 5 dùng chúng.

- [ ] **Step 1: Viết test fail**

```python
def test_docx_extractor_preserves_structure(tmp_path: Path) -> None:
    path = make_docx_with_heading_list_and_table(tmp_path / "policy.docx")
    markdown = DocxExtractor().extract(path).markdown
    assert "# Quy định" in markdown
    assert "- Hồ sơ" in markdown
    assert "| Giấy tờ | Bắt buộc |" in markdown


def test_inspector_maps_mixed_pages(tmp_path: Path) -> None:
    inspection = PdfInspector(FakeRunner('{"pdf_type":"mixed","pages_needing_ocr":[2]}')).inspect(
        tmp_path / "a.pdf"
    )
    assert inspection.kind is PdfKind.MIXED
    assert inspection.pages_needing_ocr == (2,)
```

- [ ] **Step 2: Xác nhận fail, rồi cài đặt**

Run: `python -m pytest tests/unit/integrations/knowledge_ingestion/test_docx_extractor.py tests/unit/integrations/knowledge_ingestion/test_pdf_inspector.py -q`  
Expected: FAIL trước implementation.

DOCX map Heading 1/2/3 thành `#`/`##`/`###`, giữ thứ tự paragraph/list/table và escape `|` trong cell. PDF adapter inject runner để test, chạy `detect-pdf --json`/`pdf2md --json --pages`, reject JSON/page number invalid và ném exception không chứa stdout/stderr.

- [ ] **Step 3: Verify và commit**

Run: `python -m pytest tests/unit/integrations/knowledge_ingestion/test_docx_extractor.py tests/unit/integrations/knowledge_ingestion/test_pdf_inspector.py -q; python -m ruff check src/cowork_agent/integrations/knowledge_ingestion tests/unit/integrations/knowledge_ingestion; python -m mypy src`  
Expected: PASS.

```powershell
git add src/cowork_agent/integrations/knowledge_ingestion/docx_extractor.py src/cowork_agent/integrations/knowledge_ingestion/pdf_inspector.py tests/unit/integrations/knowledge_ingestion/test_docx_extractor.py tests/unit/integrations/knowledge_ingestion/test_pdf_inspector.py
git commit -m "feat: extract DOCX and inspect PDF locally"
```

## Task 4: Mistral OCR theo trang

**Files:** Create `.../mistral_ocr.py`, `tests/unit/integrations/knowledge_ingestion/test_mistral_ocr.py`.

**Produces:** `MistralOcrClient.ocr_pdf(path, pages) -> tuple[OcrPage, ...]`; Task 5 chỉ gọi cho `SCANNED`, `IMAGE_BASED`, hoặc trang `MIXED`.

- [ ] **Step 1: Viết test fail**

```python
def test_ocr_sends_only_requested_zero_based_pages(tmp_path: Path) -> None:
    client = FakeMistralClient(return_pages=[{"index": 1, "markdown": "Trang 2"}])
    result = MistralOcrClient(client, settings()).ocr_pdf(tmp_path / "scan.pdf", (2,))
    assert result == (OcrPage(number=2, markdown="Trang 2"),)
    assert client.calls[0]["pages"] == [1]


def test_ocr_exhausted_retry_raises_safe_reason() -> None:
    client = FakeMistralClient(side_effects=[TimeoutError(), TimeoutError(), TimeoutError()])
    with pytest.raises(OcrUnavailableError, match="mistral_ocr_unavailable"):
        MistralOcrClient(client, settings(max_attempts=3)).ocr_pdf(Path("scan.pdf"), (1,))
```

- [ ] **Step 2: Xác nhận fail, rồi cài đặt**

Run: `python -m pytest tests/unit/integrations/knowledge_ingestion/test_mistral_ocr.py -q`  
Expected: FAIL trước implementation.

Mã hóa file vào biến local `data:application/pdf;base64,...`; call `ocr.process` với `pages=[page - 1]`, `table_format="markdown"`, `include_blocks=False`, page confidence. Retry hữu hạn lỗi transient; map mọi lỗi cuối thành `OcrUnavailableError("mistral_ocr_unavailable")`. Không đưa payload/response vào repr/log.

- [ ] **Step 3: Verify và commit**

Run: `python -m pytest tests/unit/integrations/knowledge_ingestion/test_mistral_ocr.py -q; python -m ruff check src/cowork_agent/integrations/knowledge_ingestion/mistral_ocr.py tests/unit/integrations/knowledge_ingestion/test_mistral_ocr.py; python -m mypy src`  
Expected: PASS và không gọi API thật.

```powershell
git add src/cowork_agent/integrations/knowledge_ingestion/mistral_ocr.py tests/unit/integrations/knowledge_ingestion/test_mistral_ocr.py
git commit -m "feat: add selected-page Mistral OCR adapter"
```

## Task 5: Service, CLI và RAG smoke test

**Files:** Create `.../service.py`, `src/cowork_agent/ingestion_cli.py`, `tests/unit/integrations/knowledge_ingestion/test_service.py`, `tests/unit/test_ingestion_cli.py`, `tests/integration/test_knowledge_ingestion_to_rag.py`; modify `README.md`, `docs/evaluations/email-rag/EMAIL-RAG-STATUS.md`.

**Produces:** `KnowledgeIngestionService.ingest(source_dir, output_dir, force) -> tuple[IngestionOutcome, ...]`; CLI exit 0 (succeeded/skipped), 1 (file failure), 2 (arguments/config invalid).

- [ ] **Step 1: Viết test fail cho routing và RAG loader**

```python
def test_text_pdf_never_calls_ocr(tmp_path: Path) -> None:
    service, ocr = build_service(pdf_kind=PdfKind.TEXT_BASED, native={1: "Nội dung"})
    assert (
        service.ingest(tmp_path / "raw", tmp_path / "extracted", force=False)[0].status
        == "succeeded"
    )
    assert ocr.calls == []


def test_mixed_pdf_merges_pages_in_order(tmp_path: Path) -> None:
    service, _ = build_service(pdf_kind=PdfKind.MIXED, native={1: "Một", 3: "Ba"}, ocr={2: "Hai"})
    service.ingest(tmp_path / "raw", tmp_path / "extracted", force=False)
    text = (tmp_path / "extracted" / "source.md").read_text(encoding="utf-8")
    assert text.index("Một") < text.index("Hai") < text.index("Ba")


def test_ingested_markdown_is_loadable_by_rag(tmp_path: Path) -> None:
    service, _ = build_service_for_docx(tmp_path)
    service.ingest(tmp_path / "raw", tmp_path / "extracted", force=False)
    assert load_corpus(tmp_path / "extracted", tenant_id="local")[0].chunks
```

- [ ] **Step 2: Xác nhận fail, rồi cài đặt orchestration**

Run: `python -m pytest tests/unit/integrations/knowledge_ingestion/test_service.py tests/unit/test_ingestion_cli.py tests/integration/test_knowledge_ingestion_to_rag.py -q`  
Expected: FAIL trước implementation.

Discovery chỉ nhận regular file `.pdf`/`.docx`, sorted relative path; reject symlink, source/output lồng nhau, oversized file/page limit. Slug output ổn định, collision là `output_name_collision`. PDF merge page 1..N với marker `<!-- Page N -->`; native PDF không gọi OCR, scanned gọi full pages, mixed gọi `pages_needing_ocr`. CLI không in content/traceback mặc định. README nêu `cargo install pdf-inspector`, `MISTRAL_API_KEY`, dry-run/force và reindex. Status doc ghi ingest admin CLI đã có, upload/Gmail attachment vẫn chưa có.

- [ ] **Step 3: Verify toàn bộ và commit**

Run: `python -m pytest tests/unit/integrations/knowledge_ingestion tests/unit/test_knowledge_ingestion_config.py tests/unit/test_ingestion_cli.py tests/integration/test_knowledge_ingestion_to_rag.py -q; python -m ruff check .; python -m mypy src; python -m pytest -q`  
Expected: tất cả PASS.

```powershell
git add src/cowork_agent/ingestion_cli.py src/cowork_agent/integrations/knowledge_ingestion tests/unit/integrations/knowledge_ingestion tests/unit/test_ingestion_cli.py tests/integration/test_knowledge_ingestion_to_rag.py README.md docs/evaluations/email-rag/EMAIL-RAG-STATUS.md
git commit -m "feat: add PDF and DOCX knowledge ingestion"
```

## Rà soát kế hoạch

- Task 1–5 bao phủ toàn bộ spec: contract/config, hash/manifest, extraction local, Mistral OCR, CLI/reindex docs/RAG compatibility.
- Không thay đổi `knowledge_base.py`, bootstrap Qdrant hay Gmail workflow vì contract Markdown corpus hiện có đã đáp ứng scope.
- Mọi test Mistral dùng fake client; smoke API thật chỉ chạy khi operator chủ động cung cấp key.
