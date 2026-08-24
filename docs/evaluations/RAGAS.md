# RAGAS — Hướng dẫn đánh giá độ bám dẫn chứng

| Trường | Giá trị |
|---|---|
| Phạm vi | Chỉ đánh giá **phía sinh câu trả lời** của RAG (faithfulness, response relevancy) |
| Ngoài phạm vi | Đánh giá retrieval — thuộc về `scripts/evaluate_retrieval.py` với 100 case gán nhãn tay |
| Trạng thái | **Chưa kích hoạt.** Package chưa cài, chưa có baseline nào trong `evaluations/CHAT-RAGAS/baselines/` |
| Chỗ gọi | `scripts/evaluate_chat_rag.py` → `run_ragas()`, bật bằng cờ `--ragas` |
| Hợp đồng bắt buộc | `evaluations/CHAT-RAGAS/README.md` § "RAGAS Adoption Gate" |
| Kế hoạch triển khai | `docs/superpowers/plans/2026-08-20-rag-procedure-document-quality.md` § 4.3 |

---

## 1. RAGAS dùng để làm gì ở dự án này

Hệ thống hiện đo **retrieval** rất tốt và **không đo gì** về chất lượng câu trả lời. RAGAS lấp đúng khoảng trống đó, và chỉ khoảng trống đó.

### 1.1 Hai metric được dùng

| Metric | Trả lời câu hỏi gì | Cần dữ liệu gì |
|---|---|---|
| `faithfulness` | Mỗi mệnh đề trong câu trả lời có được context bảo chứng không? Đây là thước đo bịa đặt. | question, answer, contexts |
| `answer_relevancy` | Câu trả lời có đúng trọng tâm câu hỏi không, hay lạc đề/lan man? | question, answer + embedding |

`faithfulness` hoạt động hai bước: tách câu trả lời thành các mệnh đề nguyên tử, rồi chạy suy luận NLI từng mệnh đề đối chiếu với context đã truy xuất. Điểm là tỉ lệ mệnh đề được bảo chứng.

### 1.2 Hai metric **không** được dùng, và vì sao

`context_precision` và `context_recall` là bản xấp xỉ bằng LLM của đúng thứ mà [`scripts/evaluate_retrieval.py`](../../scripts/evaluate_retrieval.py) đã đo **chính xác, tất định, offline và miễn phí** trên 100 case gán nhãn tay, lại còn tách slice theo probe (`lexical` / `mixed` / `semantic`).

Thay nhãn người bằng phán đoán máy ở chỗ đã có nhãn người là đi lùi: trả tiền cho LLM để nó đoán lại thứ mình đã biết chắc, rồi nhận về con số dao động giữa các lần chạy. **Harness gán nhãn là nguồn chân lý cho retrieval.**

`answer_correctness` cũng chưa dùng: nó đòi reference answer viết tay cho từng case, chi phí cao, để giai đoạn sau.

---

## 2. Chọn model — quy tắc quan trọng nhất

| Vai trò | Model | Lý do |
|---|---|---|
| **LLM chấm điểm** | Model mạnh nhất trong `OPENROUTER_ALLOWED_MODELS` mà **không phải** model đã sinh ra câu trả lời đang chấm. | Cùng key, cùng nhà cung cấp đã có sẵn. Model cụ thể là cấu hình runtime, không phải dữ liệu commit trong repo. |
| **Embedding chấm điểm** | Embedder được chọn và ghi trong report; `gemini-embedding-2` qua `GEMINI_EMBEDDING_MODEL` là lựa chọn dự kiến cho evaluator/project documents. | `answer_relevancy` chỉ cần độ tương đồng câu-hỏi-với-câu-hỏi. Không được khẳng định đây là embedder company-RAG runtime, vì runtime đó hiện dùng Jina. |
| **Không được làm judge** | `gemini-3.5-flash-lite` (`GEMINI_MODEL`) | Tier throughput để sinh câu trả lời hàng loạt; quá yếu cho tách mệnh đề + NLI |

### 2.1 Không bao giờ để một model tự chấm chính mình

Nếu một model sinh câu trả lời **rồi chính nó chấm faithfulness cho mình**, điểm sẽ chịu **thiên lệch tự ưu ái** (self-preference bias): mô hình có xu hướng đánh giá cao output của chính nó.

Thiên lệch này lệch về đúng hướng tệ nhất — **che giấu bịa đặt**, tức là che giấu chính thứ mà metric này sinh ra để bắt. Điểm cao sẽ trấn an bạn một cách sai lầm.

Quy tắc: **model chấm ≠ model sinh**, và cả hai đều phải được ghi vào báo cáo.

### 2.2 Cấu hình bắt buộc

- `temperature = 0` cho lời gọi judge.
- Truyền `llm=` và `embeddings=` **tường minh** vào `evaluate()`. Nếu bỏ trống, RAGAS rơi về mặc định OpenAI — tự phát sinh chi phí và **gửi context + câu trả lời sang một nhà cung cấp thứ ba** mà bạn không chủ ý.

---

## 3. Cài đặt và ghim phiên bản

Code hiện tại trong `run_ragas()` nhắm API legacy và chưa ghim dependency:

```python
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy   # object mức module
result = evaluate(dataset=Dataset.from_list(records), metrics=[...])
```

### 3.1 Hướng port khi chọn API hiện đại

| Hạng mục | Code hiện tại | API hiện đại cần xác nhận theo phiên bản đã ghim |
|---|---|---|
| Import metric | `from ragas.metrics import faithfulness` | Khởi tạo metric với evaluator LLM/embedding tường minh; API hiện hành có các metric collection và scorer single-turn |
| Dataset | `datasets.Dataset.from_list(...)` | `EvaluationDataset(samples=[SingleTurnSample(...)])` nếu phiên bản đã ghim hỗ trợ đường này |
| Tên trường | `question`, `answer`, `contexts`, `ground_truth` | `user_input`, `response`, `retrieved_contexts`, `reference` |
| Chuyển ngữ prompt | Chưa triển khai | Dùng API chuyển ngữ được tài liệu hoá cho phiên bản đã ghim, theo từng prompt khi API yêu cầu |
| Wrapper LLM | Chưa cấu hình evaluator | Dùng factory/interface native được tài liệu hoá cho phiên bản đã ghim |

Lưu ý: `run_ragas()` hiện giữ `reference_answer` trong record local-only. Khi port phải ánh xạ nó sang tên trường mà phiên bản RAGAS đã ghim yêu cầu và kiểm tra bằng fixture.

### 3.2 Quyết định phải chốt

Chọn một phiên bản RAGAS được hỗ trợ và **ghim chính xác** trong `pyproject.toml`, sau đó port code và test theo tài liệu của đúng phiên bản đó. Không để phiên bản thả nổi — một lần nâng dependency có thể làm hỏng chỗ gọi mà không ai nhận ra cho tới lúc chạy đánh giá.

Cũng cần package `datasets` nếu đi đường cũ.

### 3.3 Dọn dẹp

Thư mục `.deepeval/` đang rỗng và không file `.py` nào import deepeval. Xoá nó — đừng nuôi hai framework đánh giá song song.

---

## 4. Cách chạy

### 4.1 Dataset

File dataset là **local-only, không bao giờ commit**. Schema (theo `evaluations/CHAT-RAGAS/README.md`):

```json
{
  "dataset_version": "local-v1",
  "provider": "openrouter",
  "model": "deepseek/deepseek-r1-0528",
  "cases": [
    {
      "id": "case-001",
      "expected_document_ids": ["dang-ky-tam-tru"],
      "retrieved_document_ids": ["dang-ky-tam-tru", "dang-ky-xe"],
      "citation_document_ids": ["dang-ky-tam-tru"],
      "should_abstain": false,
      "abstained": false,
      "latency_ms": {"retrieval": 120, "generation": 900, "evaluator": null},

      "question": "CHỈ LOCAL — bắt buộc khi dùng --ragas",
      "answer": "CHỈ LOCAL — bắt buộc khi dùng --ragas",
      "contexts": ["CHỈ LOCAL — bắt buộc khi dùng --ragas"],
      "reference_answer": "CHỈ LOCAL — bắt buộc khi dùng --ragas"
    }
  ]
}
```

Bốn trường văn bản cuối là tuỳ chọn ở chế độ tất định, **bắt buộc cho mọi case** khi bật `--ragas`, và bị loại khỏi báo cáo theo thiết kế — có unit test khẳng định chúng không xuất hiện ở bất kỳ độ sâu nào của JSON đầu ra.

### 4.2 Lệnh

```powershell
# Chế độ tất định: không cần judge, không cần key, chạy được ngay
uv run python scripts/evaluate_chat_rag.py --input <local-only>.json

# Bật judge, ghi báo cáo vào baselines
uv run python scripts/evaluate_chat_rag.py --input <local-only>.json --ragas `
  --output evaluations/CHAT-RAGAS/baselines/chat-rag-eval-YYYY-MM-DD-<dataset>-<model>.json
```

Chế độ tất định luôn tính sẵn: Hit@1/Hit@5/MRR/Recall@5 theo document ID, tỉ lệ citation hợp lệ (ID trích dẫn ⊆ ID đã truy xuất), độ chính xác từ chối trả lời, và p50/p95 cho từng chặng độ trễ. **Chạy chế độ này trước** — nếu các chỉ số tất định đã tệ thì chưa cần đến judge.

### 4.3 Chạy song song bằng batch module của dự án (đích triển khai)

`--max-workers` là giới hạn của **custom batch scheduler**, không phải cờ chuyển
thẳng vào cơ chế song song nội bộ của RAGAS. Mỗi worker giữ một lease trên một
Mistral key và chấm các case được giao tuần tự. RAGAS phải chạy concurrency `1`
bên trong worker để tránh nhân đôi mức song song.

```text
effective_workers = min(
  requested_max_workers,
  active_key_count,
  ready_case_count,
  chat_ragas_plugin_limit,
)
```

Nếu không truyền `--max-workers`, `requested_max_workers` mặc định bằng số key
đang hoạt động. Nếu yêu cầu `5` nhưng chỉ có `3` key, job chạy `3` worker và ghi
cả `requested_max_workers=5` lẫn `effective_workers=3` vào manifest. Khi có
`MISTRAL_API_KEY` đến `MISTRAL_API_KEY5` và ít nhất năm case sẵn sàng, cùng lệnh
sẽ chạy năm worker mà không sửa code.

```powershell
# Sau khi custom batch integration được triển khai
uv run python scripts/evaluate_chat_rag.py --input <local-only>.json --ragas `
  --evaluator-provider mistral --max-workers 5
```

**Trạng thái hiện tại:** CLI chưa nhận `--max-workers`; `run_ragas()` vẫn gửi cả
dataset vào một lần `evaluate()`. Vì vậy đoạn này là hợp đồng triển khai, không
phải tuyên bố rằng chạy song song đã hoạt động hôm nay.

---

## 5. Hiệu chuẩn tiếng Việt — bước không được bỏ

Nếu phiên bản RAGAS đã ghim mang prompt và few-shot bằng tiếng Anh, phải kiểm tra hiệu chuẩn tiếng Việt trước khi dùng điểm làm evidence. Không giả định API hay prompt của một phiên bản khác.

### 5.1 Chuyển ngữ prompt

```python
metric.prompt = await metric.prompt.adapt(...)  # chỉ minh hoạ: dùng API của phiên bản đã ghim
```

Một số metric có nhiều hơn một prompt (ví dụ prompt tách mệnh đề và prompt NLI) — phải chuyển ngữ **từng cái** nếu API của phiên bản đã ghim phơi bày chúng.

**Commit prompt đã chuyển ngữ vào repo.** Nếu không, mỗi lần chạy lại sinh ra một bản dịch khác và các con số giữa các lần chạy không so sánh được với nhau.

### 5.2 Hiệu chuẩn với người chấm

Lấy khoảng **30 case tự chấm tay** (bám dẫn chứng: có / không), rồi so với điểm của judge.

- Đồng thuận tốt → dùng được, ghi mức đồng thuận vào báo cáo.
- Đồng thuận yếu → **ghi nhận metric là chưa hiệu chuẩn và không được dùng để gate bất cứ thứ gì.** Một con số không hiệu chuẩn còn tệ hơn không có số, vì nó tạo cảm giác an toàn giả.

---

## 6. Đọc điểm cho đúng

**Kết luận được:**

- `faithfulness` thấp → câu trả lời chứa nội dung không có trong context. Bịa đặt, hoặc prompt sinh đang cho phép mô hình "bổ sung kiến thức".
- `answer_relevancy` thấp → trả lời lan man hoặc lệch trọng tâm, dù có thể vẫn bám context.
- `faithfulness` cao mà người dùng vẫn than sai → vấn đề gần như chắc chắn nằm ở **retrieval**: context được bảo chứng nhưng context vốn đã sai. Quay lại `evaluate_retrieval.py`, đừng chỉnh prompt sinh.

**Không kết luận được:**

- Điểm **không** nói context có đầy đủ không (đó là recall — đo bằng harness gán nhãn).
- Điểm **không** ổn định tuyệt đối giữa các lần chạy. Chênh lệch nhỏ giữa hai lần là nhiễu, không phải hồi quy.
- Điểm **không** so sánh được qua các mốc nếu đã đổi model chấm, phiên bản prompt, hoặc phiên bản RAGAS. Đổi bất kỳ thứ nào trong ba thứ đó là bắt đầu một chuỗi đo mới.

**Chỉ tiêu dự án:** `faithfulness ≥ 0,95` trên bộ golden, đo bằng judge tiếng Việt đã hiệu chuẩn và không phải model sinh.

---

## 7. Vận hành

### 7.1 Đẩy điểm sang Langfuse

`evaluate()` nhận tham số `callbacks=`, và repo đã chạy Langfuse 3.15 (xem [`docs/observability/LANGFUSE.md`](../observability/LANGFUSE.md)). Phát điểm dưới dạng Langfuse score để theo dõi faithfulness theo thời gian, thay vì để nó nằm chết trong một file JSON.

### 7.2 Không đặt vào CI gate

Điểm judge **không tất định** và **tốn tiền mỗi lần chạy**. Vì vậy:

- Chạy theo mốc (trước release, sau mỗi phase của kế hoạch), **không** làm bước chặn trong CI.
- CI phải xanh được khi **không có** key của evaluator.
- **Không bao giờ** dùng ngưỡng metric để chặn câu trả lời của người dùng ở runtime, cho tới khi nó được hiệu chuẩn trên một tập người-chấm đủ đại diện.

### 7.3 Thời điểm chạy lần đầu

Lấy phép đo **"trước"** ngay khi bật được, **trước** lần re-ingest corpus ở task 1.3 của kế hoạch. Sau khi extraction và cách gán nhãn đổi, baseline này không dựng lại được, và nó là cách duy nhất để quy phần tăng faithfulness cho việc sửa pipeline thay vì cho công sức chỉnh prompt.

---

## 8. Quyền riêng tư

Báo cáo được commit **đã** loại bỏ nội dung văn bản. Nhưng bản thân lời gọi judge **gửi context và câu trả lời tới nhà cung cấp chấm điểm**. Đây là hai chuyện khác nhau, và cái thứ hai mới là chuyện phải xin phép.

| Nguồn dữ liệu | Trạng thái |
|---|---|
| Corpus thủ tục hành chính công khai (`data/extracted`) | Chấp nhận được |
| `project_documents` của tenant | **Cần quyết định tường minh, ghi vào `evaluations/CHAT-RAGAS/README.md` trước lần chạy đầu.** Chạy khi chưa có quyết định này bị coi là lỗi |

---

## 9. Checklist trước khi lưu một báo cáo

Adoption gate yêu cầu mỗi lần chạy phải ghi lại:

- [ ] Phiên bản RAGAS
- [ ] Model chấm điểm **và** model sinh câu trả lời (phải khác nhau)
- [ ] Model embedding của evaluator
- [ ] Phiên bản prompt đã chuyển ngữ
- [ ] Phiên bản dataset, số case và số tài liệu chính xác
- [ ] Số liệu tổng hợp từng metric, số ca lỗi, số ca không chấm được
- [ ] Độ trễ retrieval / sinh / chấm **tách riêng**
- [ ] Context đến từ user documents, company knowledge, hay cả hai
- [ ] Mức đồng thuận với người chấm (từ § 5.2)

Đường dẫn báo cáo: `evaluations/CHAT-RAGAS/baselines/chat-rag-eval-YYYY-MM-DD-<dataset>-<model>.json`

---

## 10. Lỗi thường gặp

| Triệu chứng | Nguyên nhân |
|---|---|
| `--ragas requires the optional ragas and datasets packages` | Chưa cài. Đây là trạng thái mặc định hiện nay — xem § 3 |
| Chạy được nhưng phát sinh chi phí OpenAI ngoài dự kiến | Quên truyền `llm=` / `embeddings=`, RAGAS rơi về mặc định |
| `ImportError` sau khi nâng cấp ragas | Đã lên v0.4, code vẫn dùng API cũ — xem bảng § 3.1 |
| Điểm faithfulness cao bất thường | Kiểm tra model chấm có trùng model sinh không (§ 2.1) |
| Điểm nhảy lung tung giữa các lần chạy | `temperature` chưa về 0, hoặc prompt chuyển ngữ chưa được commit nên mỗi lần dịch một kiểu |
| `RAGAS cases require string question, answer, reference_answer, and contexts` | Dataset thiếu một trong bốn trường văn bản ở ít nhất một case |
