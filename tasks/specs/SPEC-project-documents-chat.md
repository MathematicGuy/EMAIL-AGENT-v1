# Technical Spec — Project Documents và AI Chat với tài liệu

| Trường | Giá trị |
|---|---|
| Nguồn yêu cầu | [PRD-v3](../prds/PRD-v3-project-documents-chat.md) |
| Thẩm quyền kiến trúc | [ADR-005](../../docs/adr/ADR-005-project-scoped-chat-documents.md), [TARGET-ARCHITECTURE §21](../../docs/architectures/TARGET-ARCHITECTURE.md) |
| Trạng thái | Ready for implementation |
| Ngày | 2026-08-12 |
| Baseline kỹ thuật | Python 3.11+, FastAPI, PostgreSQL, Redis stream, Qdrant |

---

## 1. Mục đích

Tài liệu này chuyển PRD-v3 thành thiết kế triển khai được: hợp đồng dữ liệu,
port, adapter, pipeline ingestion, schema Qdrant, migration PostgreSQL, API,
cấu hình, telemetry và kế hoạch kiểm thử.

## 2. Phạm vi kỹ thuật

Trong phạm vi:

- Project container và `project_id` trong chat session scope.
- Ingestion runtime: validate → extract → OCR → chunk theo trang → embed →
  index.
- Collection Qdrant riêng cho project document, ACL-first.
- Port truy hồi và tích hợp vào context assembler của Chat Controller.
- API project và document.
- Migration PostgreSQL, retention, xoá lan truyền.
- Telemetry metadata-only và safety counters.

Ngoài phạm vi: mọi mục ở PRD-v3 §5.

## 3. Cấu trúc module

```text
src/cowork_agent/
├── domain/
│   └── project_contracts.py            # MỚI: Project, ProjectDocument, chunk, query/response
├── features/
│   ├── ai_chat/
│   │   ├── generation_context.py       # SỬA: thêm PROJECT_DOCUMENT_EVIDENCE
│   │   ├── retrieval_policy.py         # SỬA: trigger tất định theo project
│   │   ├── controller.py               # SỬA: đọc plane tài liệu qua Memory Gateway
│   │   ├── memory_gateway.py           # SỬA: namespace mang project_id + document_scope
│   │   └── ports.py                    # SỬA: thêm ProjectDocumentPort
│   └── project_documents/              # MỚI
│       ├── __init__.py
│       ├── ports.py                    # ObjectStorePort, OcrPort, IngestionQueuePort
│       ├── validation.py               # sniff media type, size, page, quota, encryption
│       ├── chunking.py                 # chunk theo trang
│       ├── ingestion.py                # ProjectDocumentIngestionService (state machine)
│       └── retention.py                # tính expires_at, lọc hết hạn, purge
├── integrations/
│   ├── knowledge_ingestion/
│   │   └── mistral_ocr.py              # MỚI: MistralOcrClient (điền chỗ trống hiện tại)
│   └── rag/
│       └── project_documents.py        # MỚI: QdrantProjectDocumentMemory
├── persistence/
│   ├── migrations/
│   │   ├── 005_projects.sql(.down)     # MỚI
│   │   ├── 006_project_documents.sql(.down)  # MỚI
│   │   └── 007_episode_project_scope.sql(.down)  # MỚI
│   └── repositories/
│       └── postgres.py                 # SỬA: ProjectRepository, ProjectDocumentRepository
├── orchestration/
│   └── ingestion_queue.py              # MỚI: stream Redis riêng + fallback in-process
└── api/
    ├── projects.py                     # MỚI: router project + document
    └── chat.py                         # SỬA: POST /sessions nhận project_id
```

## 4. Hợp đồng miền

`src/cowork_agent/domain/project_contracts.py`, theo phong cách hiện có:
`@dataclass(frozen=True, slots=True)`, `StrEnum`, `from_dict`/`to_dict` cho
biên ngoài.

```python
class DocumentStatus(StrEnum):
    RECEIVED = "received"
    EXTRACTING = "extracting"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"
    DELETED = "deleted"


class DocumentReasonCode(StrEnum):
    FILE_TOO_LARGE = "file_too_large"
    PDF_PAGE_LIMIT_EXCEEDED = "pdf_page_limit_exceeded"
    EMPTY_EXTRACTION = "empty_extraction"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    ENCRYPTED_DOCUMENT = "encrypted_document"
    OCR_PAGE_LIMIT_EXCEEDED = "ocr_page_limit_exceeded"
    OCR_FAILED = "ocr_failed"
    QUOTA_EXCEEDED = "quota_exceeded"
    EMBEDDING_UNAVAILABLE = "embedding_unavailable"
    INDEX_UNAVAILABLE = "index_unavailable"


class DocumentScope(StrEnum):
    COMPANY = "company"
    PROJECT_DOCUMENT = "project_document"


@dataclass(frozen=True, slots=True)
class Project:
    project_id: str
    tenant_id: str
    user_id: str
    name: str
    is_default: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectDocument:
    document_id: str
    tenant_id: str
    user_id: str
    project_id: str
    filename: str
    media_type: str
    byte_size: int
    content_sha256: str
    status: DocumentStatus
    reason_code: DocumentReasonCode | None
    page_count: int | None
    ocr_page_count: int | None
    chunk_count: int | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectDocumentChunk:
    chunk_id: str
    document_id: str
    document_title: str
    section: str | None
    page_start: int
    page_end: int
    text: str
    tenant_id: str
    user_id: str
    project_id: str


@dataclass(frozen=True, slots=True)
class ProjectDocumentQuery:
    query: str
    document_ids: tuple[str, ...]          # rỗng = toàn bộ tài liệu ready của project
    top_k: int
    min_score: float
    timeout_ms: int


@dataclass(frozen=True, slots=True)
class ProjectDocumentChunkHit:
    chunk: ProjectDocumentChunk
    relevance_score: float
    rerank_score: float | None


@dataclass(frozen=True, slots=True)
class ProjectDocumentRetrievalResponse:
    hits: tuple[ProjectDocumentChunkHit, ...]
    retrieval_status: RetrievalStatus       # dùng lại enum của target_contracts
    degraded: bool
    latency_ms: int
```

Mở rộng hợp đồng có sẵn:

- `ChatMemoryScope` thêm `project_id: str` (bắt buộc).
- `MemoryNamespace` thêm `document_scope: DocumentScope | None`; chỉ hợp lệ khi
  `memory_type is MemoryType.SEMANTIC`, các trường hợp khác phải là `None`.
- `EpisodeCitation` thêm `citation_scope: DocumentScope`, `page_start: int | None`,
  `page_end: int | None`; `source_url` trở thành optional.
- `TaskEpisode` thêm `project_id: str`.

### 4.1 Suy dẫn `document_id`

```python
def derive_document_id(tenant_id: str, user_id: str, project_id: str, sha256: str) -> str:
    return f"doc_{uuid5(NAMESPACE_URL, f'cowork-agent/project-doc/{tenant_id}/{user_id}/{project_id}/{sha256}').hex}"
```

Không mã hoá tên file hay nội dung. Upload lại đúng byte trong cùng project trả
về bản ghi cũ (`200` thay vì `202`) nếu trạng thái là `ready` hoặc đang xử lý.

## 5. Port

`src/cowork_agent/features/project_documents/ports.py`:

```python
class DocumentObjectStorePort(Protocol):
    async def put(self, document_id: str, payload: bytes) -> None: ...
    async def get(self, document_id: str) -> bytes | None: ...
    async def delete(self, document_id: str) -> bool: ...


class OcrPort(Protocol):
    async def extract_pages(
        self, payload: bytes, pages: Sequence[int]
    ) -> tuple[OcrPage, ...]: ...


class IngestionQueuePort(Protocol):
    async def enqueue(self, document_id: str) -> None: ...
```

`src/cowork_agent/features/ai_chat/ports.py` thêm:

```python
class ProjectDocumentPort(Protocol):
    async def retrieve(
        self, namespace: MemoryNamespace, query: ProjectDocumentQuery
    ) -> ProjectDocumentRetrievalResponse: ...
```

Adapter cục bộ mặc định: object store ghi file mã hoá Fernet dưới
`.data/project_documents/<tenant>/<project>/<document_id>.bin`, dùng lại lớp
`TokenCipher` của `integrations/gmail/auth.py` với khoá riêng
`DOCUMENT_ENCRYPTION_KEY`. Khoá lấy từ `.env`, không hardcode trong source,
test hay `.env.example`.

## 6. Pipeline ingestion

### 6.1 Trình tự

```text
POST .../documents (multipart)
  → validate đồng bộ (media type, size, quota)
  → tính sha256 → derive document_id
  → nếu đã tồn tại: trả bản ghi cũ
  → object store.put(bytes đã mã hoá)
  → insert row status=received, expires_at=now+retention
  → queue.enqueue(document_id)
  → 202 {document_id, status}

Job (worker):
  status=extracting
  → PdfInspector.inspect() hoặc DocxExtractor.extract()
  → kiểm tra page_count ≤ max_pdf_pages
  → pages_needing_ocr:
       len > max_ocr_pages → failed(ocr_page_limit_exceeded)
       ngược lại → MistralOcrClient.extract_pages()
  → gộp native markdown + OCR markdown theo số trang
  → nếu tổng text rỗng → failed(empty_extraction)
  status=indexing
  → chunk theo trang
  → embed theo lô
  → upsert Qdrant
  status=ready, cập nhật page_count, ocr_page_count, chunk_count
```

Mọi bước chạy trong worker vì `PdfInspector` gọi `subprocess` (`detect-pdf`,
`pdf2md`). Không bao giờ chạy trên request path.

### 6.2 Validation

`features/project_documents/validation.py`:

- sniff magic bytes: `%PDF-` cho PDF, `PK\x03\x04` + kiểm tra
  `[Content_Types].xml` cho DOCX; phần mở rộng chỉ để hiển thị;
- `byte_size > KNOWLEDGE_INGEST_MAX_BYTES` → `file_too_large`;
- PDF có `/Encrypt` hoặc `PdfInspector` báo lỗi mở khoá → `encrypted_document`;
- quota: `PROJECT_DOCUMENT_MAX_COUNT` và `PROJECT_DOCUMENT_MAX_TOTAL_BYTES` tính
  trên các document chưa `deleted` của project → `quota_exceeded`.

### 6.3 OCR client

`integrations/knowledge_ingestion/mistral_ocr.py` thay chỗ hiện đang raise
`mistral_not_configured`:

```python
class MistralOcrClient:
    def __init__(self, settings: KnowledgeIngestionSettings, transport: HttpTransport | None = None) -> None: ...

    async def extract_pages(self, payload: bytes, pages: Sequence[int]) -> tuple[OcrPage, ...]:
        """Trả markdown cho đúng các trang yêu cầu, giữ nguyên thứ tự."""
```

Ràng buộc:

- chỉ gửi các trang trong `pages`; trang có native text không bao giờ được gửi;
- timeout `KNOWLEDGE_INGEST_TIMEOUT_SECONDS`, retry `KNOWLEDGE_INGEST_MAX_ATTEMPTS`
  với backoff, chỉ retry lỗi tạm thời (`429`, `5xx`, timeout);
- đặt `User-Agent` tường minh — xem bài học đã ghi nhận với Jina reranker khi
  User-Agent mặc định của urllib bị chặn;
- trả thiếu trang hoặc markdown rỗng → coi là thất bại, không index một phần;
- không log nội dung trang, chỉ log số trang và mã lỗi.

`KnowledgeIngestionService._extract` giữ nguyên hành vi CLI hiện tại; client OCR
được inject để cả CLI và job runtime dùng chung.

### 6.4 Chunk theo trang

`features/project_documents/chunking.py`:

```python
def chunk_pages(
    markdown_by_page: Mapping[int, str],
    *,
    document_id: str,
    document_title: str,
    tenant_id: str,
    user_id: str,
    project_id: str,
    max_chars: int = 1200,
) -> tuple[ProjectDocumentChunk, ...]:
```

Quy tắc:

- xử lý theo thứ tự trang tăng dần;
- trong một trang, tách theo heading `#`/`##` như `knowledge_base._split_sections`,
  sau đó tách theo đoạn văn dưới `max_chars`;
- chunk không vượt ranh giới tài liệu; được phép gộp trang liền kề khi trang quá
  ngắn (`< max_chars / 4`), khi đó `page_start != page_end`;
- `chunk_id = f"{document_id}#{index}"`;
- `document_title` = heading H1 đầu tiên, fallback là tên file đã chuẩn hoá.

### 6.5 Hàng đợi

`orchestration/ingestion_queue.py` phản chiếu `RedisRunQueue`/`RedisRunConsumer`
trên stream riêng `cowork:ingestion`, có consumer group, `claim_stale` và DLQ.
Khi Redis không cấu hình, dùng fallback in-process `asyncio` như
`persistence/local.py` để demo chạy được, và ghi log rõ chế độ đang dùng.

Idempotency: `document_id` là khoá; job chạy lại trên cùng document phải cho kết
quả như nhau và upsert đè lên đúng point cũ.

## 7. Qdrant collection

Tên: `QDRANT_PROJECT_DOCUMENT_COLLECTION`, mặc định `project_documents`. Tách
hoàn toàn khỏi `company_knowledge`.

Payload và index:

| Trường payload | Kiểu | Index |
|---|---|---|
| `tenant_id` | keyword | có |
| `user_id` | keyword | có |
| `project_id` | keyword | có |
| `document_id` | keyword | có |
| `expires_at` | integer (epoch giây) | có |
| `document_title` | keyword | không |
| `section` | keyword | không |
| `page_start` / `page_end` | integer | không |
| `text` | text | không |

Point id: `uuid5(_PROJECT_POINT_NAMESPACE, chunk_id)` để re-ingest ghi đè.

Bộ lọc dựng **trước** khi embed:

```python
Filter(must=[
    FieldCondition(key="tenant_id", match=MatchValue(value=namespace.tenant_id)),
    FieldCondition(key="user_id", match=MatchValue(value=namespace.user_id)),
    FieldCondition(key="project_id", match=MatchValue(value=namespace.project_id)),
    FieldCondition(key="expires_at", range=Range(gt=int(now.timestamp()))),
    *( [FieldCondition(key="document_id", match=MatchAny(any=list(query.document_ids)))]
       if query.document_ids else [] ),
])
```

Từ chối trước khi tốn embedding call khi thiếu bất kỳ trường scope nào, giống
`QdrantSemanticMemory.retrieve` hiện tại → `RetrievalStatus.AUTHORIZATION_DENIED`.

Xoá: `delete` theo filter `document_id` (xoá tài liệu), `project_id` (xoá
project), `user_id` (xoá theo user). Lặp lại được; xoá điểm không tồn tại không
phải lỗi.

## 8. Tích hợp Chat Controller

### 8.1 Chính sách kích hoạt

`retrieval_policy.select_memory_reads` nhận thêm số tài liệu `ready` của project:

```python
project_documents = (
    ProjectDocumentQuery(
        query=_normalized_query(request.user_message),
        document_ids=(),
        top_k=PROJECT_DOCUMENT_TOP_K,          # mặc định 5
        min_score=PROJECT_DOCUMENT_MIN_SCORE,  # mặc định 0.35, chốt lại sau đo đạc
        timeout_ms=PROJECT_DOCUMENT_TIMEOUT_MS # mặc định 800
    )
    if ready_document_count > 0
    else None
)
```

Không dùng cue phrase. Chính sách cue của company RAG giữ nguyên.

### 8.2 Lắp ráp ngữ cảnh

`generation_context.py`:

```python
class ContextSource(StrEnum):
    CURRENT_INSTRUCTION = "current_instruction"
    ACTIVE_SESSION_TURNS = "active_session_turns"
    PROJECT_DOCUMENT_EVIDENCE = "project_document_evidence"   # MỚI
    CURRENT_COMPANY_EVIDENCE = "current_company_evidence"
    STORED_PREFERENCE = "stored_preference"
    ADVISORY_EPISODE = "advisory_episode"


_CONFLICT_PRECEDENCE = (
    ContextSource.CURRENT_INSTRUCTION,
    ContextSource.PROJECT_DOCUMENT_EVIDENCE,
    ContextSource.CURRENT_COMPANY_EVIDENCE,
    ContextSource.STORED_PREFERENCE,
    ContextSource.ADVISORY_EPISODE,
)
```

Thêm `ProjectDocumentEvidence` (song song `CompanyEvidence`) mang
`retrieval_status`, `degraded`, `chunks`, `citations`, `scores`; citation gồm
`document_id`, `document_title`, `section`, `page_start`, `page_end`.

Prompt phải nêu rõ: tài liệu project có thẩm quyền về nội dung của chính nó;
company evidence có thẩm quyền về quy trình công ty; mâu thuẫn thì nêu cả hai.

### 8.3 Sự kiện SSE

Không thêm event type. `memory_citation` mang thêm `citation_scope`,
`page_start`, `page_end`. Client cũ bỏ qua trường lạ; client demo hiển thị chip
có nhãn nguồn.

## 9. API

Router mới `api/projects.py`, prefix `/v1/cowork/chat`, cùng cơ chế
`_verified_principal` như `api/chat.py`.

| Method | Path | Body | Trả về |
|---|---|---|---|
| POST | `/projects` | `{name}` | `201 {project_id, name, is_default}` |
| GET | `/projects` | — | `200 {projects: [...]}` |
| DELETE | `/projects/{project_id}` | — | `204` (cascade document + session) |
| POST | `/projects/{project_id}/documents` | multipart `file` | `202 {document_id, status}` hoặc `200` nếu trùng nội dung |
| GET | `/projects/{project_id}/documents` | — | `200 {documents: [...]}` |
| GET | `/projects/{project_id}/documents/{document_id}` | — | `200 {status, reason_code, page_count, ocr_page_count, chunk_count, expires_at}` |
| DELETE | `/projects/{project_id}/documents/{document_id}` | — | `204` |

`POST /v1/cowork/chat/sessions` nhận thêm `project_id` tuỳ chọn; thiếu thì phân
giải về default project. `GET /sessions` nhận query `project_id` tuỳ chọn.

Mã lỗi: `404` khi project/document không thuộc principal (không phân biệt
"không tồn tại" và "không có quyền"), `413` khi vượt kích thước, `415` khi sai
media type, `422` khi payload sai, `503` khi store không khả dụng.

Giới hạn upload: `Content-Length` bắt buộc; stream ghi thẳng ra file tạm với
ngưỡng cứng, không đọc toàn bộ vào RAM.

## 10. PostgreSQL

### 10.1 `005_projects.sql`

```sql
CREATE TABLE chat_projects (
    project_id   TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    name         TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    is_default   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX chat_projects_owner_idx ON chat_projects (tenant_id, user_id);
CREATE UNIQUE INDEX chat_projects_default_idx
    ON chat_projects (tenant_id, user_id) WHERE is_default;
```

### 10.2 `006_project_documents.sql`

```sql
CREATE TABLE chat_project_documents (
    document_id     TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES chat_projects(project_id) ON DELETE CASCADE,
    tenant_id       TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    filename        TEXT NOT NULL,
    media_type      TEXT NOT NULL,
    byte_size       BIGINT NOT NULL CHECK (byte_size > 0),
    content_sha256  TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN
                      ('received','extracting','indexing','ready','failed','deleted')),
    reason_code     TEXT,
    page_count      INTEGER,
    ocr_page_count  INTEGER,
    chunk_count     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    CONSTRAINT reason_only_when_failed
        CHECK ((status = 'failed') = (reason_code IS NOT NULL))
);
CREATE UNIQUE INDEX chat_project_documents_content_idx
    ON chat_project_documents (project_id, content_sha256)
    WHERE status <> 'deleted';
CREATE INDEX chat_project_documents_scope_idx
    ON chat_project_documents (tenant_id, user_id, project_id, status);
CREATE INDEX chat_project_documents_expiry_idx ON chat_project_documents (expires_at);
```

### 10.3 `007_episode_project_scope.sql`

- `ALTER TABLE task_episodes ADD COLUMN project_id TEXT;`
- backfill về default project của user;
- `ALTER ... SET NOT NULL` sau backfill;
- mở rộng JSON citation để chấp nhận `citation_scope`, `page_start`, `page_end`.

Mọi migration có file `.down.sql` tương ứng theo quy ước hiện có.

## 11. Cấu hình

Biến mới, đọc qua `config.py` theo đúng khuôn `from_env`:

```text
QDRANT_PROJECT_DOCUMENT_COLLECTION   mặc định project_documents
PROJECT_DOCUMENT_RETENTION_DAYS      mặc định 30
PROJECT_DOCUMENT_MAX_COUNT           mặc định 50
PROJECT_DOCUMENT_MAX_TOTAL_BYTES     mặc định 524_288_000
PROJECT_DOCUMENT_TOP_K               mặc định 5
PROJECT_DOCUMENT_MIN_SCORE           mặc định 0.35
PROJECT_DOCUMENT_TIMEOUT_MS          mặc định 800
DOCUMENT_ENCRYPTION_KEY              bắt buộc, Fernet key
INGESTION_QUEUE_STREAM               mặc định cowork:ingestion
```

Tái sử dụng: `KNOWLEDGE_INGEST_OCR_ENABLED=true`, `MISTRAL_API_KEY`,
`KNOWLEDGE_INGEST_MAX_BYTES`, `KNOWLEDGE_INGEST_MAX_PDF_PAGES`,
`KNOWLEDGE_INGEST_MAX_OCR_PAGES`, `KNOWLEDGE_INGEST_TIMEOUT_SECONDS`,
`KNOWLEDGE_INGEST_MAX_ATTEMPTS`, `QDRANT_URL`, `QDRANT_API_KEY`,
`QDRANT_VECTOR_SIZE`.

`.env.example` chỉ chứa placeholder; không ghi khoá thật hay hostname thật.

Bootstrap: `QDRANT_ENABLED` false → plane tài liệu tắt hẳn, API upload trả `503`
với thông điệp rõ ràng thay vì nhận file rồi không index được.

## 12. Retry, timeout, idempotency

| Thao tác | Timeout | Retry | Khoá idempotency |
|---|---|---|---|
| Upload | request timeout mặc định | không | `document_id` |
| Extract | theo subprocess, cứng 120s | không | `document_id` |
| OCR mỗi lô trang | `KNOWLEDGE_INGEST_TIMEOUT_SECONDS` | `KNOWLEDGE_INGEST_MAX_ATTEMPTS`, backoff | trang đã OCR không gửi lại |
| Embed lô | 30s | 3 lần, backoff | `chunk_id` |
| Upsert Qdrant | 30s | 3 lần | point id từ `chunk_id` |
| Truy hồi | `PROJECT_DOCUMENT_TIMEOUT_MS` | 1 lần | — |
| Xoá | 15s mỗi store | lặp tới khi mọi store xác nhận | `document_id` |

## 13. Telemetry

Sự kiện metadata-only:

```text
project_document.uploaded        {project_id, document_id, media_type, byte_size}
project_document.ingest_stage    {document_id, stage, duration_ms}
project_document.ocr             {document_id, ocr_page_count, attempts}
project_document.indexed         {document_id, chunk_count}
project_document.failed          {document_id, reason_code}
project_document.retrieval       {project_id, status, result_count, latency_ms, degraded}
project_document.deleted         {document_id, stores_confirmed}
```

Safety counter phải bằng không: `cross_tenant_document_hit`,
`cross_user_document_hit`, `cross_project_document_hit`,
`expired_document_hit`, `deleted_document_hit`.

Cấm ghi: văn bản tài liệu, nội dung truy vấn người dùng, tên file đầy đủ.

## 14. Kế hoạch kiểm thử

Theo TDD, test đi trước code trong từng milestone.

### Unit

- `derive_document_id` tất định và không lộ tên file/nội dung.
- Validation: từng reason code, gồm file `.pdf` giả mạo phần mở rộng.
- `chunk_pages`: giữ thứ tự trang, gộp trang ngắn, `page_start ≤ page_end`,
  không vượt ranh giới tài liệu.
- `MistralOcrClient`: thiếu trang → lỗi; retry đúng số lần; không gửi trang
  native; không log nội dung.
- Retention: tính `expires_at`, lọc hết hạn trước khi xếp hạng.

### ACL và bảo mật

- Truy hồi với `project_id` của người khác → `AUTHORIZATION_DENIED`, không có
  embedding call (assert bằng fake embedder đếm lần gọi).
- Thiếu `project_id` hoặc `document_scope` → fail closed.
- Project không có tài liệu `ready` → không gửi truy vấn tới Qdrant.
- Snapshot payload episode/log/telemetry không chứa văn bản tài liệu.

### Tích hợp

- Vòng đời đầy đủ: upload → job → ready → hỏi → có trích dẫn trang.
- Upload trùng nội dung → không tạo point thứ hai.
- Xoá tài liệu → truy hồi tiếp theo không trả chunk đó; episode cũ vẫn còn.
- Qdrant sập → `degraded: true`, câu trả lời nêu rõ, không bịa.
- Ngưỡng không đạt → `no_results`, câu trả lời nêu thiếu thông tin.

### Fixture

Đặt tại `tests/fixtures/project_documents/`: một PDF native nhiều trang, một PDF
scan ngắn (OCR được mock ở test), một DOCX, một PDF mã hoá, một file sai media
type. Không dùng tài liệu chứa dữ liệu thật.

**Lưu ý về embedder giả**: không assert tài liệu nào xếp hạng nhất khi chạy với
embedder hash; chỉ assert bộ lọc, trạng thái và cấu trúc kết quả.

### Đánh giá

Tập nhãn câu hỏi/trang trả lời cho 3–5 tài liệu mẫu, đo: tỉ lệ trích dẫn đúng
trang, tỉ lệ trả lời "không có trong tài liệu" đúng, so sánh bật/tắt plane.

## 15. Thứ tự triển khai

| Milestone | Nội dung | Cổng nghiệm thu |
|---|---|---|
| V3-M1 | Project contract, migration 005, default project, `project_id` trong session scope + namespace, API project | test fail-closed xanh, session cũ vẫn chạy |
| V3-M2 | Document contract, migration 006, validation, object store, queue, extract + OCR + chunk | vòng đời tới `ready` chạy được với Qdrant mock |
| V3-M3 | Collection Qdrant, ACL-first, xoá lan truyền | test ACL và test không-embedding-khi-từ-chối xanh |
| V3-M4 | Chính sách kích hoạt, `ProjectDocumentPort`, context assembler, thứ tự ưu tiên | test tích hợp hỏi–đáp có trích dẫn xanh |
| V3-M5 | Trích dẫn trang trên SSE/UI, `citation_scope` + `project_id` trong episode, migration 007 | snapshot không rò văn bản, UI hiển thị đủ 4 trạng thái |
| V3-M6 | Retention, purge, audit xoá, safety counters, đánh giá | counters bằng không, báo cáo đánh giá có số liệu |

## 16. Rủi ro kỹ thuật

| Rủi ro | Giảm thiểu |
|---|---|
| `detect-pdf`/`pdf2md` không có trên máy chạy worker | Kiểm tra khi khởi động worker, báo lỗi cấu hình rõ ràng thay vì lỗi runtime mơ hồ |
| Subprocess treo | Timeout cứng + kill, đánh dấu `failed` |
| Chi phí OCR tăng đột biến | Giới hạn trang, quota theo project, telemetry số trang OCR |
| `min_score` chưa hiệu chỉnh gây bịa hoặc luôn no-results | Đo trên tập nhãn ở V3-M6 trước khi chốt số |
| Migration `NOT NULL` cho `project_id` trên episode cũ | Backfill về default project trước khi siết ràng buộc, có `.down.sql` |
| Qdrant thành phụ thuộc cứng | Trạng thái degraded hiển thị rõ; upload trả `503` khi Qdrant tắt |

## 17. Câu hỏi còn mở

- Con số quota cuối cùng theo project.
- `min_score` và `top_k` sau đo đạc.
- Có cho người dùng đặt retention riêng theo tài liệu hay không.
- Có bật project-scoped episodic retrieval ở phiên bản sau hay không.
