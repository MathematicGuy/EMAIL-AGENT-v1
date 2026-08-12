# Technical Spec — Chat with User Documents ("Chat with the PDF")

| Trường | Giá trị |
|---|---|
| Trạng thái | Draft — chờ xác nhận trước khi viết PRD |
| Ngày | 2026-08-12 |
| Nguồn yêu cầu | [PRD-v4](../prds/PRD-v4-chat-with-user-documents.md), `docs/references/user_preference.md` |
| Thẩm quyền kiến trúc | [ADR-006](../adr/ADR-006-user-document-plane-and-classifier-routing.md), [TARGET-ARCHITECTURE §21](../../docs/architectures/TARGET-ARCHITECTURE.md) |
| Thay thế | Thiết kế Project-scoped documents trước đây ở TARGET-ARCHITECTURE §21 |
| Baseline kỹ thuật | Python 3.11+, FastAPI, PostgreSQL, Qdrant, Mistral OCR |
| Feature owner | AI Chat Controller (`feature: ai_chat`) |

---

## 1. Mục đích

Biến yêu cầu trong `user_preference.md` thành thiết kế triển khai được: người
dùng tải PDF/DOCX lên, hỏi trong chat, hệ thống **tự quyết định** có cần truy hồi
tài liệu hay không, rồi trả lời có trích dẫn tới từng trang.

Hai thành phần kỹ thuật chính:

1. **User RAG plane** — một mặt phẳng truy hồi ngữ nghĩa duy nhất chứa tài liệu
   của người dùng, khoá theo `tenant_id` + `user_id` + `document_id`.
2. **Intent classifier + deterministic resolver** — cổng quyết định `needs_rag` /
   `needs_tool`, thay cho cơ chế cue-phrase hiện tại.

## 2. Khác biệt so với thiết kế đã bị thay thế

`user_preference.md` mâu thuẫn với thiết kế Project-scoped documents ở §21 hiện
tại ở ba điểm. SPEC này chọn phương án của `user_preference.md`; §21 sẽ được viết
lại theo SPEC này.

| Chủ đề | §21 hiện tại (bị thay thế) | SPEC này |
|---|---|---|
| Container | **Project** bắt buộc; session thuộc đúng một project | **Không có Project.** Tài liệu thuộc về người dùng; scope = `tenant/user/document` |
| Số plane ngữ nghĩa trong chat | Hai: `company` + `project_document`, phân biệt bằng `document_scope` | **Một: User RAG.** Company RAG chỉ còn phục vụ standalone Email Agent (PRD-v1) |
| Kích hoạt truy hồi | Tất định: có tài liệu `ready` thì truy hồi mọi lượt | **Classifier là thẩm quyền duy nhất** quyết định `needs_rag`; không có lớp keyword nào kết luận thay |
| Trục hành động | Không có | `needs_tool` có trong contract nhưng **luôn `false`** ở MVP |

Lý do bỏ Project: với một corpus tài liệu của một người dùng, project chỉ thêm
một tầng khoá, một API surface, một migration và một nhánh lỗi mà không thay đổi
chất lượng câu trả lời. Việc thu hẹp phạm vi truy hồi được xử lý bằng tham số
`document_ids` tuỳ chọn trong request, không cần container bền vững.

Lý do bỏ phân biệt company/user trong classifier: classifier chỉ cần trả lời một
câu hỏi — *chất lượng câu trả lời có phụ thuộc vào tài liệu của người dùng
không?* Việc chọn store là chi tiết hạ tầng phía sau port.

## 3. Phạm vi

Trong phạm vi:

- Upload, validate, extract (native + OCR), chunk theo trang, embed, index.
- Qdrant collection riêng cho tài liệu người dùng, ACL-first.
- Intent classifier phân tầng làm cổng định tuyến duy nhất, resolver tất định, 5 route.
- Graph orchestration cho lượt chat (xem §10, quyết định D-01).
- Context assembly có section `user_document_evidence` và thứ tự ưu tiên.
- API tài liệu, migration PostgreSQL, retention 30 ngày, xoá lan truyền.
- Telemetry metadata-only, safety counters, bộ đo classifier.

Ngoài phạm vi: xem §17.

## 4. Kiến trúc tính năng

```mermaid
flowchart TB
    subgraph INGEST["INGESTION PLANE (off request path)"]
        UP["POST /documents<br/>multipart"]
        VALID["Validator<br/>sniffed type · size · pages · quota"]
        OBJ[("Object store<br/>encrypted · TTL")]
        JOB["Ingestion job"]
        DETECT["PdfInspector · DocxExtractor"]
        OCR["MistralOcrClient"]
        CHUNK["Page-aware chunker"]
        EMBED["Embedding service"]
        QIDX[("Qdrant user_documents")]
        FAIL["failed(reason_code)"]
    end

    UP --> VALID --> OBJ --> JOB --> DETECT
    DETECT -->|native pages| CHUNK
    DETECT -->|pages_needing_ocr| OCR --> CHUNK
    CHUNK --> EMBED --> QIDX
    VALID -. rejected .-> FAIL
    DETECT -. encrypted · no text .-> FAIL
    OCR -. cap · attempts .-> FAIL
    EMBED -. attempts .-> FAIL

    subgraph TURN["CHAT TURN"]
        MSG["User message"]
        CLS["LLM intent classifier<br/>layered prompt · structured output<br/>SOLE routing authority"]
        RES["Deterministic resolver<br/>truth table only"]
        ROUTE{"route"}
        RETR["UserDocumentRetrievalPort<br/>ACL filter before embedding"]
        CTX["Context assembler"]
        GEN["Generation + citations"]
        CLARIFY["Clarify question"]
    end

    MSG --> CLS --> RES --> ROUTE
    ROUTE -->|chat| CTX
    ROUTE -->|rag| RETR --> QIDX
    RETR --> CTX --> GEN
    ROUTE -->|clarify| CLARIFY
```

## 5. Cấu trúc module

```text
src/cowork_agent/
├── domain/
│   ├── _chat_contracts_memory.py       # SỬA: UserDocumentRead/Query, MemoryReadOptions.user_document
│   ├── _chat_contracts_common.py       # SỬA: MemoryCitationType/DocumentScope
│   └── user_document_contracts.py      # MỚI: UserDocument, chunk, retrieval response, IntentDecision
├── features/
│   ├── ai_chat/
│   │   ├── intent/
│   │   │   ├── __init__.py             # MỚI
│   │   │   ├── prompt.py               # MỚI: template phân tầng + prompt_version
│   │   │   ├── classifier.py           # MỚI: gọi LLM + parse structured output
│   │   │   └── resolver.py             # MỚI: bảng chân trị → ChatRoute
│   │   ├── graph/
│   │   │   ├── __init__.py             # MỚI
│   │   │   ├── state.py                # MỚI: ChatGraphState (lean)
│   │   │   ├── nodes.py                # MỚI: node functions thuần, không phụ thuộc framework
│   │   │   └── runner.py               # MỚI: lắp graph (xem D-01)
│   │   ├── generation_context.py       # SỬA: USER_DOCUMENT_EVIDENCE + precedence
│   │   ├── retrieval_policy.py         # SỬA: cue-only → classifier-driven
│   │   ├── controller.py               # SỬA: gọi graph runner
│   │   ├── memory_gateway.py           # SỬA: đọc plane tài liệu người dùng
│   │   └── ports.py                    # SỬA: IntentClassifierPort, UserDocumentRetrievalPort
│   └── user_documents/                 # MỚI
│       ├── __init__.py
│       ├── ports.py                    # ObjectStorePort, OcrPort, IngestionQueuePort, DocumentRepositoryPort
│       ├── validation.py               # sniff media type, size, page, quota
│       ├── chunking.py                 # chunk theo trang
│       ├── ingestion.py                # UserDocumentIngestionService (state machine)
│       └── retention.py                # expires_at, lọc hết hạn, purge
├── integrations/
│   ├── knowledge_ingestion/
│   │   └── mistral_ocr.py              # MỚI: MistralOcrClient (lấp chỗ trống hiện tại)
│   └── rag/
│       └── user_documents.py           # MỚI: QdrantUserDocumentMemory
├── persistence/
│   ├── migrations/005_user_documents.sql (+ .down.sql)   # MỚI
│   └── repositories/                   # SỬA: UserDocumentRepository (local + postgres)
└── api/
    └── documents.py                    # MỚI: router tài liệu
```

Ràng buộc tái sử dụng: `PdfInspector`, `DocxExtractor`, `PdfInspection`,
`OcrPage` được dùng lại nguyên trạng. `KnowledgeIngestionService` **không** được
sửa — nó là CLI của quản trị viên cho company corpus, vòng đời khác.

## 6. Hợp đồng dữ liệu

### 6.1 UserDocument

```yaml
document_id: string        # opaque; derive(tenant_id, user_id, content_sha256)
tenant_id: string
user_id: string

filename: string
media_type: application/pdf | application/vnd.openxmlformats-officedocument.wordprocessingml.document
byte_size: integer
page_count: integer | null
ocr_page_count: integer | null
content_sha256: string

status: received | extracting | indexing | ready | failed | deleted
reason_code: string | null
chunk_count: integer | null

created_at: datetime
updated_at: datetime
expires_at: datetime       # created_at + USER_DOCUMENTS_RETENTION_DAYS
```

Máy trạng thái:

```text
received → extracting → indexing → ready
any → failed(reason_code)
ready | failed → deleted
```

`document_id` dẫn xuất tất định từ `tenant_id`, `user_id`, `content_sha256`:
upload lại đúng byte đó trả về bản ghi cũ, không index bản sao. Dẫn xuất không
mã hoá tên file và không mã hoá nội dung tài liệu.

Reason codes:

```text
file_too_large · pdf_page_limit_exceeded · empty_extraction
unsupported_media_type · encrypted_document
ocr_page_limit_exceeded · ocr_failed
quota_exceeded · embedding_unavailable · index_unavailable
```

### 6.2 UserDocumentChunk

```yaml
chunk_id: string           # document_id + ordinal
document_id: string
tenant_id: string
user_id: string
ordinal: integer
page_start: integer
page_end: integer
section: string | null
text: string
```

### 6.3 IntentDecision — output của classifier

```yaml
intent: chat | knowledge_query | action_request
needs_rag: boolean
needs_tool: boolean
tool_name: string | null
needs_clarification: boolean
retrieval_query: string | null
confidence: number          # 0.0..1.0
reason_codes:
  - general_chat
  - user_document_required
  - explicit_document_reference
  - external_action_requested
  - missing_information
```

Ràng buộc validate:

- `needs_rag = true` **bắt buộc** kèm `retrieval_query` không rỗng, ≤
  `MAX_RETRIEVAL_QUERY_LENGTH`.
- `needs_tool = false` bắt buộc kèm `tool_name = null`.
- Ở MVP, `needs_tool = true` bị resolver hạ xuống `false` và ghi counter
  `chat.intent.tool_requested_but_disabled`.
- `intent` chỉ là nhãn quan sát; **không** dùng để định tuyến. Định tuyến chỉ
  dựa vào ba boolean.

### 6.4 ChatRoute — output của resolver

```python
class ChatRoute(StrEnum):
    CHAT = "chat"
    RAG = "rag"
    TOOL = "tool"
    RAG_TOOL = "rag_tool"
    CLARIFY = "clarify"
```

Bảng chân trị (thứ tự đánh giá từ trên xuống):

| Điều kiện | Route |
|---|---|
| `needs_clarification` | `CLARIFY` |
| `needs_rag and needs_tool` | `RAG_TOOL` |
| `needs_rag` | `RAG` |
| `needs_tool` | `TOOL` |
| còn lại | `CHAT` |

MVP hiện thực `CHAT`, `RAG`, `CLARIFY`. `TOOL` và `RAG_TOOL` có trong enum và
trong test bảng chân trị, nhưng node thực thi chưa tồn tại; resolver không bao
giờ trả về chúng khi `USER_DOCUMENTS_TOOL_AXIS_ENABLED=false`.

Cổng chặn cứng, chạy **sau** resolver, **trước** node retrieve: nếu người dùng
không có tài liệu `ready` nào thì `RAG → CHAT`, ghi
`reason_codes += no_ready_documents`. Không gọi embedding, không gọi Qdrant.

### 6.5 UserDocumentQuery / Response

Query kế thừa `_QueryScopedMemoryRead` như `SemanticMemoryQuery`, với fixed
filter riêng:

```python
class UserDocumentQuery(_QueryScopedMemoryRead):
    _max_items = MAX_USER_DOCUMENT_RETRIEVAL_ITEMS   # 8
    _fixed_filter_name = "document_scope"
    _fixed_filter_value = "user_document"
```

`MemoryReadOptions` nhận thêm một trường, có mặc định để không phá caller cũ:

```python
user_document: UserDocumentRead | UserDocumentQuery = UserDocumentRead(enabled=False)
```

Response:

```yaml
chunks:
  - chunk_id: string
    document_id: string
    document_title: string
    section: string | null
    page_start: integer
    page_end: integer
    text: string
    relevance_score: number
    rerank_score: number | null

retrieval_status: success | no_results | timeout | authorization_denied | partial
degraded: boolean
latency_ms: integer
```

### 6.6 Citation

`EpisodeCitation` nhận thêm ba trường, mặc định giữ nguyên hành vi cũ:

```yaml
citation_scope: company | user_document   # default company
page_start: integer | null
page_end: integer | null
```

Episode vẫn chỉ lưu **toạ độ**. Văn bản chunk, văn bản trang đã trích xuất và
transcript đầy đủ tiếp tục bị cấm khỏi episode, log, telemetry, fixture.

## 7. Pipeline ingestion

```text
POST /documents (202)
→ validate      : sniffed media type, byte_size, quota/user, sha256
→ persist       : bản ghi status=received + object store (encrypted, TTL)
→ enqueue job   : off request path
→ extracting    : PdfInspector.inspect | DocxExtractor.extract
                  pages_needing_ocr → MistralOcrClient, giới hạn max_ocr_pages
                  trang native không bao giờ OCR lại
→ indexing      : chunk theo trang → embed → upsert Qdrant
→ ready         : chunk_count, page_count, ocr_page_count
```

Quy tắc:

- Validate dựa trên **content sniffing**, không dựa vào phần mở rộng tên file.
- `PdfInspector` shell ra lệnh cục bộ ⇒ extraction **chỉ** chạy trong job, không
  bao giờ trên request path.
- Đầu ra trích xuất rỗng hoặc một phần **không bao giờ** được index; document
  chuyển `failed(empty_extraction)`.
- Vượt `max_ocr_pages` → `failed(ocr_page_limit_exceeded)`. Provider lỗi sau
  `max_attempts` → `failed(ocr_failed)`.
- Chunk theo trang: đọc marker `<!-- Page N -->` do extractor sinh ra, cắt theo
  ranh giới đoạn văn dưới ngưỡng kích thước hiện hành, mỗi chunk mang
  `page_start` / `page_end`. Chunk không được bắc cầu qua ranh giới trang trừ
  khi một đoạn văn liên tục vắt qua trang, khi đó `page_end > page_start`.

`MistralOcrClient` lấp đúng chỗ trống `mistral_not_configured` hiện có tại
`integrations/knowledge_ingestion/service.py:130`, dùng lại
`KnowledgeIngestionSettings` (`MISTRAL_API_KEY`, `KNOWLEDGE_INGEST_MODEL`,
`KNOWLEDGE_INGEST_TIMEOUT_SECONDS`, `KNOWLEDGE_INGEST_MAX_ATTEMPTS`,
`KNOWLEDGE_INGEST_MAX_OCR_PAGES`). Client là adapter thuần: nhận ảnh trang, trả
`tuple[OcrPage, ...]`, không ghi file, không giữ trạng thái.

## 8. Qdrant schema và ACL

Collection riêng: `USER_DOCUMENTS_QDRANT_COLLECTION` (mặc định `user_documents`).
Không dùng chung collection với company corpus.

Payload mỗi point:

```yaml
tenant_id · user_id · document_id · chunk_id · ordinal
document_title · section · page_start · page_end · text
status: ready
expires_at: epoch_seconds
```

Payload index: `tenant_id`, `user_id`, `document_id`, `status`, `expires_at`.

**ACL-first**: filter (`tenant_id`, `user_id`, `status=ready`,
`expires_at > now`) được dựng **trước khi** query được embed. Chunk của người
dùng khác không bao giờ được chấm điểm. Nếu request thiếu `tenant_id` hoặc
`user_id`, port raise trước mọi I/O — fail closed.

Không có fallback in-repo cho plane này: tài liệu người dùng chỉ tồn tại trong
Qdrant. Qdrant chết ⇒ trả kết quả rỗng với `degraded: true`, không thay thế bằng
bằng chứng khác.

## 9. Router — classifier là thẩm quyền định tuyến duy nhất

### 9.1 Nguyên tắc

Định tuyến tập trung ở **một lời gọi LLM structured output** mỗi lượt. Không có
lớp keyword/regex nào được phép kết luận thay nó, kể cả kết luận "có".

Lý do: cue chỉ nhìn thấy chuỗi ký tự, còn quyết định thật là một phán đoán về
**phụ thuộc tri thức**. Cùng một từ "PDF" xuất hiện trong câu cần truy hồi và
trong câu không cần. Bất kỳ lớp rules nào đủ mạnh để bắt đúng trường hợp bẫy thì
cũng đã là một bộ phân loại — chỉ là bộ phân loại tệ hơn, không kiểm thử được
bằng bộ fixture gán nhãn, và không cải thiện được bằng prompt.

Hệ quả: trường hợp bẫy và các trường hợp mơ hồ được giải bằng **prompt phân
tầng** (§9.2), không bằng danh sách cụm từ.

### 9.2 Prompt phân tầng

Prompt được lắp theo năm tầng, thứ tự cố định, mỗi tầng có một việc:

**Tầng 1 — Nguyên tắc quyết định.** Một câu hỏi duy nhất, không mở rộng:

> Would the quality or correctness of the requested answer depend on retrieving
> information from the user's own documents?

**Tầng 2 — Quy tắc ưu tiên khi tín hiệu xung đột.** Đây là nơi giải trường hợp
bẫy, xếp theo thứ tự áp dụng:

1. **Chủ thể của yêu cầu cuối cùng quyết định.** Chỉ mệnh lệnh hoặc câu hỏi cuối
   cùng của người dùng mới định nghĩa nhiệm vụ. Câu dẫn, lời kể, bối cảnh không
   định nghĩa nhiệm vụ.
2. **Nhắc tới tài liệu ≠ cần tài liệu.** Nếu tài liệu được nhắc tới nhưng không
   phải là đối tượng của yêu cầu cuối cùng thì `needs_rag = false`.
   → *"I uploaded a PDF yesterday. Anyway, explain Python decorators."* = `false`.
3. **Dấu chuyển chủ đề đặt lại chủ thể** (`anyway`, `by the way`, `btw`,
   `nhân tiện`, `à mà`, `quay lại`, `chuyển sang`).
4. **Chỉ định trực chỉ không có tiền lệ trong hội thoại thì trỏ về tài liệu.**
   *"What does it say about checkpointing?"* khi hội thoại chưa nêu chủ đề nào =
   `needs_rag = true`.
5. **Câu hỏi hồi tưởng mơ hồ ưu tiên truy hồi.** *"What were the requirements
   again?"* = `true`, vì trả lời sai do thiếu bằng chứng tốn kém hơn một lần truy
   hồi thừa.
6. **Kiến thức chung, định nghĩa, khái niệm phổ quát** = `false`, trừ khi người
   dùng hỏi tài liệu của họ nói gì về khái niệm đó.
7. **Khi vẫn không quyết được và người dùng có tài liệu `ready`** = `true`. Quy
   tắc chốt, khớp với chính sách ưu tiên recall ở §9.4.

**Tầng 3 — Bằng chứng có giới hạn.** Classifier chỉ được nhìn: câu hỏi hiện tại,
tối đa `_MAX_ACTIVE_SESSION_TURNS = 8` lượt gần nhất, và **danh sách tiêu đề tài
liệu `ready`** (chỉ tên, không nội dung, không chunk). Tiêu đề là thứ cho phép
phân biệt "tài liệu của tôi có nói không" với kiến thức chung mà không tốn một
vòng truy hồi.

**Tầng 4 — Ví dụ hiệu chuẩn.** Bốn nhóm, cân bằng số lượng, phản chiếu đúng bốn
nhóm trong bộ fixture ở §17: obvious RAG · obvious chat · ambiguous · distractor
(nhắc tài liệu nhưng không cần). Ví dụ trong prompt và ví dụ trong fixture
**không được trùng nhau** — nếu trùng thì bộ đo chỉ đo trí nhớ của prompt.

**Tầng 5 — Schema đầu ra và ràng buộc.** `IntentDecision` ở §6.3, kèm yêu cầu
`reason_codes` phải giải thích được quyết định.

Toàn bộ prompt nằm trong repo dưới dạng template có `prompt_version`. Đổi prompt
là một thay đổi có kiểm soát: phải chạy lại bộ fixture §17 và không được tụt
ngưỡng §15.

### 9.3 Các lớp tất định còn lại (không phải định tuyến)

Ba cơ chế tất định vẫn tồn tại, nhưng **chỉ được phép thu hẹp năng lực, không bao
giờ được tự sinh ra một route**:

| Cơ chế | Tác dụng |
|---|---|
| Precondition gate | không có tài liệu `ready` ⇒ `RAG → CHAT`, không gọi embedding/Qdrant |
| Schema validation | output sai schema ⇒ kích hoạt §9.4 |
| Tool axis downgrade | `needs_tool = true` khi trục tool tắt ⇒ hạ về `false` |

### 9.4 Chính sách lỗi

```text
Classifier timeout hoặc schema không hợp lệ
→ retry một lần
→ vẫn hỏng: fail-open sang needs_rag = true (khi user có tài liệu ready)
→ ghi reason_codes += classifier_unavailable
```

Fail-open sang RAG là có chủ ý: sai sót nguy hiểm nhất là bỏ sót truy hồi và trả
lời không có bằng chứng. Với trục tool thì ngược lại — mọi lỗi đều hạ
`needs_tool` về `false` (fail-closed), vì hành động sai tốn kém hơn.

Nguyên tắc: **RAG routing ưu tiên recall, tool routing ưu tiên precision.**

## 10. Graph orchestration

### 10.1 State (lean)

```python
class ChatGraphState(TypedDict):
    messages: Annotated[list[ChatTurn], add]

    tenant_id: str
    user_id: str
    session_id: str
    query: str

    needs_rag: bool
    needs_tool: bool
    needs_clarification: bool
    route: str | None
    retrieval_query: str | None

    citation_ids: Annotated[list[str], add]
    errors: Annotated[list[str], add]
    final_answer: str | None
```

Cấm tuyệt đối trong state: `pdf_bytes`, toàn văn tài liệu, danh sách chunk, prompt
đã lắp. Chunk truy hồi thuộc mặt phẳng ngữ cảnh của một lượt, không thuộc state
bền vững. Vi phạm làm checkpoint phình và lộ nội dung tài liệu vào nơi lưu trữ.
Có một test khẳng định mọi giá trị trong state đều là scalar, id, hoặc danh sách
id.

### 10.2 Node và edge

```text
classify → (conditional) → retrieve → assemble → generate → persist
                        ↘ assemble (route=chat)
                        ↘ clarify (route=clarify)
```

| Node | Trách nhiệm | Không được làm |
|---|---|---|
| `classify` | LLM classifier → resolver → 3 boolean + route | gọi Qdrant |
| `retrieve` | `UserDocumentRetrievalPort` với ACL filter | quyết định route |
| `assemble` | dựng `GenerationContext` có section mới | gọi LLM sinh câu trả lời |
| `generate` | stream reply + citation | ghi bộ nhớ |
| `clarify` | phát một câu hỏi làm rõ, kết thúc lượt | truy hồi |
| `persist` | ghi turn, chat summary, episode khi được yêu cầu rõ ràng | ghi văn bản tài liệu |

Node là **hàm thuần**, nhận state và các port, trả về state delta. Chúng phải
unit-test được mà không cần dựng graph.

### 10.3 D-01 — Quyết định cần xác nhận: LangGraph

`user_preference.md` §7 dùng cú pháp reducer của LangGraph
(`Annotated[list[Message], add]`), nên SPEC này **mặc định chọn LangGraph** làm
lớp lắp graph, thêm dependency `langgraph`, đặt trong `graph/runner.py`.

Ràng buộc để chi phí đảo ngược thấp:

- `state.py` và `nodes.py` không import `langgraph`.
- `runner.py` là module duy nhất biết tới framework.
- `ChatController` hiện tại giữ nguyên trách nhiệm HTTP/SSE và gọi runner; không
  viết lại controller.
- Checkpointer của LangGraph **không** được dùng làm nguồn sự thật cho session
  buffer — `ChatSessionBufferPort` vẫn là nơi lưu trạng thái phiên. Checkpointer
  nếu bật chỉ phục vụ debug trong môi trường dev.

Nếu bạn muốn tránh dependency mới, chỉ `runner.py` bị thay bằng một vòng lặp
tất định ~40 dòng; phần còn lại của SPEC không đổi.

## 11. Context assembly

`ContextSource` nhận thêm `USER_DOCUMENT_EVIDENCE = "user_document_evidence"`.
`GenerationContext` nhận thêm `user_document_evidence: LabeledSection[...] | None`.

Thứ tự ưu tiên khi xung đột:

```text
current_instruction
> user_document_evidence
> current_company_evidence
> stored_preference
> advisory_episode
```

Phạm vi thẩm quyền, vì thứ hạng không phải toàn bộ quy tắc:

- Tài liệu người dùng có thẩm quyền về **nội dung của chính nó** — nó nói gì, ở
  trang nào.
- Company RAG (nếu bật) giữ thẩm quyền về **quy trình và chính sách công ty**.
- Khi hai nguồn mâu thuẫn, cả hai được nêu kèm trích dẫn và mâu thuẫn được nói
  rõ. Không im lặng chọn bên thứ hạng cao hơn.
- Khi không chunk nào vượt ngưỡng điểm, câu trả lời phải nói rằng tài liệu không
  chứa thông tin đó và liệt kê phần còn thiếu. Bịa từ kiến thức tham số là lỗi
  validation.

Company RAG trong chat được đưa sau cờ `CHAT_COMPANY_RAG_ENABLED`, mặc định
`false` ở MVP. Đường dẫn cue-phrase hiện tại trong `retrieval_policy.py` được giữ
lại nhưng chỉ chạy khi cờ bật.

## 12. API

```text
POST   /v1/cowork/chat/documents                → 202 {document_id, status}
GET    /v1/cowork/chat/documents                → danh sách + status + reason_code
GET    /v1/cowork/chat/documents/{document_id}  → status + reason_code + counts
DELETE /v1/cowork/chat/documents/{document_id}  → 204, purge index + object + text
```

- Cùng prefix `/v1/cowork/chat` và cùng `VerifiedPrincipal` với router chat hiện
  có; không có `user_id` trong query param.
- Upload là multipart, trả `202`; tiến trình ingestion **poll** qua endpoint
  status, không stream.
- **Không thêm SSE event type mới.** Bằng chứng tài liệu được công bố qua
  `memory_citation` sẵn có, phân biệt bằng `citation_scope`.
- Không có endpoint session mới: session hiện tại không đổi hình dạng.

## 13. Persistence

`005_user_documents.sql`:

```sql
CREATE TABLE user_documents (
    document_id      TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL,
    user_id          TEXT NOT NULL,
    filename         TEXT NOT NULL,
    media_type       TEXT NOT NULL,
    byte_size        BIGINT NOT NULL,
    content_sha256   TEXT NOT NULL,
    page_count       INTEGER,
    ocr_page_count   INTEGER,
    chunk_count      INTEGER,
    status           TEXT NOT NULL,
    reason_code      TEXT,
    created_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL,
    expires_at       TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX user_documents_content_key
    ON user_documents (tenant_id, user_id, content_sha256);
CREATE INDEX user_documents_owner_status
    ON user_documents (tenant_id, user_id, status);
CREATE INDEX user_documents_expiry ON user_documents (expires_at);
```

Bảng không lưu văn bản tài liệu. Văn bản đã trích xuất nằm ở object store có mã
hoá; chunk nằm ở Qdrant.

## 14. Cấu hình

```text
USER_DOCUMENTS_ENABLED=true
USER_DOCUMENTS_QDRANT_COLLECTION=user_documents
USER_DOCUMENTS_MAX_BYTES=26214400
USER_DOCUMENTS_MAX_PAGES=100
USER_DOCUMENTS_MAX_PER_USER=50
USER_DOCUMENTS_RETENTION_DAYS=30
USER_DOCUMENTS_TOP_K=8
USER_DOCUMENTS_MIN_SCORE=0.6
USER_DOCUMENTS_TIMEOUT_MS=3000
USER_DOCUMENTS_TOOL_AXIS_ENABLED=false

CHAT_INTENT_CLASSIFIER_ENABLED=true
CHAT_INTENT_CLASSIFIER_MODEL=<provider model id>
CHAT_INTENT_CLASSIFIER_TIMEOUT_MS=10000
CHAT_COMPANY_RAG_ENABLED=false
```

Bí mật đọc từ `.env`; `.env.example` chỉ chứa placeholder, không hostname thật,
không key thật. OCR dùng lại `MISTRAL_API_KEY` và nhóm `KNOWLEDGE_INGEST_*` hiện
có.

## 15. Telemetry và đo lường

Sự kiện (metadata-only):

```text
user_document.upload.accepted · user_document.upload.rejected
user_document.ingestion.started · user_document.ocr.invoked
user_document.ingestion.completed · user_document.ingestion.failed
user_document.deleted · user_document.expired

chat.intent.classified · chat.intent.precondition_downgraded
chat.intent.classifier_retried · chat.intent.fallback_to_rag
chat.route.decided
user_document.retrieval.requested · .completed · .empty · .degraded
```

Trường được phép: `tenant_id`, `user_id`, `session_id`, `document_id`, `route`,
`reason_codes`, `confidence`, `chunk_count`, `retrieval_status`, `degraded`,
`latency_ms`, `token_usage`. Bị cấm: văn bản truy vấn thô, văn bản chunk, văn bản
trang, prompt đã lắp.

Bộ đo classifier (chỉ số quan trọng nhất của tính năng):

| Chỉ số | Định nghĩa | Ngưỡng gate đề xuất |
|---|---|---|
| Retrieval recall | trong các câu **thật sự** cần tài liệu, tỉ lệ được truy hồi | ≥ 0.95 |
| Retrieval precision | trong các câu đã truy hồi, tỉ lệ **thật sự** cần | ≥ 0.75 |
| Missed-RAG rate | cần tài liệu nhưng route = CHAT | ≤ 0.05 |
| Tool precision | (khi bật trục tool) | ≥ 0.95 |
| Classifier p95 latency | | ≤ 1500 ms |

Safety counters phải bằng 0 trong test: truy hồi chéo tenant, truy hồi chéo
user, truy hồi tài liệu đã hết hạn hoặc đã xoá, văn bản tài liệu xuất hiện trong
episode/log/telemetry.

## 16. Lỗi và fallback

| Sự cố | Hành vi |
|---|---|
| Validate từ chối | `failed(reason_code)` ngay tại upload, không tạo job, không giữ byte ngoài bản ghi lỗi |
| Extraction lỗi | `failed`, không index, chat không bị ảnh hưởng |
| OCR provider chết | retry có giới hạn → `failed(ocr_failed)`; không index riêng phần trang native |
| Embedding chết | giữ `indexing`, backoff → `failed(embedding_unavailable)` |
| Qdrant chết lúc truy vấn | một retry → kết quả rỗng + `degraded: true`; lượt chat nói rõ bằng chứng tài liệu không khả dụng |
| Retrieval timeout | một retry → `timeout` + `degraded: true` |
| Tài liệu bị xoá/hết hạn giữa phiên | bị loại bởi filter, lượt chat tiếp tục không có nó |
| Không chunk nào vượt ngưỡng | `no_results`; câu trả lời nói tài liệu không đề cập |
| Classifier chết | §9.3 |

Plane suy giảm **không bao giờ** rơi về sinh câu trả lời không nguồn, và không
bao giờ ảnh hưởng standalone Email Agent PRD-v1.

## 17. Kiểm thử

| Lớp | Nội dung |
|---|---|
| Unit — resolver | bảng chân trị 5 route, thứ tự đánh giá, hạ cấp tool khi tắt trục |
| Unit — precondition | không có tài liệu `ready` ⇒ `RAG → CHAT`, không gọi embedding/Qdrant |
| Unit — prompt | template lắp đủ 5 tầng; `prompt_version` đổi khi nội dung đổi; ví dụ trong prompt không trùng fixture |
| Unit — chunking | `page_start`/`page_end` đúng theo marker; đoạn vắt trang |
| Unit — validation | sniff type, size, quota, encrypted |
| Unit — state | mọi trường trong `ChatGraphState` là scalar/id/list id |
| Contract | `UserDocumentQuery` fixed filter; `MemoryReadOptions` tương thích ngược; `EpisodeCitation` mặc định `company` |
| Integration — ingestion | received → ready; mọi nhánh `failed(reason_code)`; idempotent theo sha256 |
| Integration — retrieval | ACL dựng trước embedding (assert bằng fake port ghi lại thứ tự); chéo tenant/user trả rỗng |
| Privacy | không có văn bản tài liệu trong episode, log, telemetry, fixture |
| Eval — classifier | fixture gán nhãn ≥ 60 câu, theo mẫu `tests/fixtures/routing/`, chia đều bốn nhóm: obvious RAG / obvious chat / ambiguous / distractor. Nhóm distractor là bài kiểm tra chính của tầng 2 trong §9.2 |
| Eval — grounded answer | tập vàng câu hỏi có trích dẫn trang đúng |

Fixture classifier là tài sản mới, phải có trước khi bật classifier ở demo; không
có nó thì §15 không đo được.

## 18. Thứ tự triển khai

1. Contracts: `UserDocument`, chunk, `IntentDecision`, `ChatRoute`,
   `UserDocumentQuery`, mở rộng `EpisodeCitation` — kèm test contract.
2. Ingestion: validation → extraction → `MistralOcrClient` → chunk theo trang →
   state machine + migration. Chưa có truy hồi.
3. Qdrant collection + `QdrantUserDocumentMemory` ACL-first + xoá lan truyền.
4. Router: prompt phân tầng → classifier → resolver + fixture gán nhãn + bộ đo §15.
5. Graph + context assembly + trích dẫn theo trang trong câu trả lời.
6. Retention, xoá, safety counters, gate đánh giá.

Mốc 1–3 không đổi hành vi chat hiện tại; chat chỉ đổi từ mốc 4.

## 19. Ngoài phạm vi

- Project container, chia sẻ tài liệu giữa người dùng, chia sẻ ở mức workspace.
- Đưa tài liệu người dùng vào company corpus (cấm vĩnh viễn, không phải hoãn).
- Hiểu ảnh, biểu đồ, cấu trúc bảng vượt quá văn bản OCR.
- Sửa, chú thích, tái sinh tài liệu; tái ingest tự động hoặc theo lịch.
- Ingest attachment Gmail (vẫn ngoài phạm vi theo ADR-003).
- Bất kỳ tool thực thi nào trong chat, bao gồm `@Email`.
- Reflexion, multi-agent, ReAct loop tự trị.
- Episodic retrieval theo tài liệu.

## 20. Quyết định cần bạn xác nhận

| # | Quyết định | Mặc định trong SPEC |
|---|---|---|
| D-01 | LangGraph làm lớp lắp graph | **Có**, cô lập trong `graph/runner.py` |
| D-02 | Bỏ hẳn Project container | **Có** |
| D-03 | Company RAG trong chat tắt ở MVP | **Có**, sau cờ `CHAT_COMPANY_RAG_ENABLED` |
| D-04 | Classifier thay cue-phrase làm cổng truy hồi | **Có**, fail-open sang RAG |
| D-05 | Trục tool có trong contract, tắt khi chạy | **Có** |
