# Quy trình đánh giá & version hoá system prompt

Tài liệu này mô tả **toàn bộ vòng lặp** từ lúc chạy memory eval đến lúc kết luận một
phiên bản prompt — như nó sẽ trông ra sao **khi SPEC hoàn tất**, chứ không chỉ phần đã
xây. Mục đích không phải là thiết kế (thiết kế nằm ở SPEC), mà là để **quyết định xây gì
trước, hoãn gì, bỏ gì**.

Nguyên tắc chọn lọc: ưu tiên 20% công việc bắt được 80% lỗi thường gặp mà memory eval
sinh ra. Bằng chứng thực nghiệm hiện có (xem §4) nói rằng lỗi thường gặp nhất **không
phải** lỗi prompt, và điều đó thay đổi thứ tự ưu tiên.

Nguồn tham chiếu, không lặp lại ở đây:

- Thiết kế đầy đủ: [SPEC-prompt-versioning-and-performance-tracking.md](../../tasks/specs/SPEC-prompt-versioning-and-performance-tracking.md)
- Bảng fault class + danh sách field của ledger: [prompt-versioning/README.md](../../evaluations/MEMORIES/prompt-versioning/README.md)
- Thủ tục chạy và các luật không được phá: [RUNBOOK.md](../../evaluations/MEMORIES/RUNBOOK.md)
- Cấu trúc báo cáo: [REPORT_FORMAT.md](../../evaluations/MEMORIES/reports/REPORT_FORMAT.md)

---

## 1. Vòng lặp

```
  (1) Run eval  ->  (2) Report  ->  (3) Fault attribution  ->  (4) Triage issues
        ^                                                             |
        |                                                             v
  (8) Rerun đã duyệt  <-  (7) Ledger verdict  <-  (6) Prompt hypothesis  <-  (5) Agent diagnosis
```

Vòng lặp chỉ đóng khi bước (8) sinh ra một run mới, và run đó lại đi qua (1)–(7). Một
chu kỳ không có bước (7) là một chu kỳ không để lại gì: số liệu vẫn còn trong artifact,
nhưng **lý do** thay đổi được kỳ vọng có tác dụng thì mất.

---

## 2. Từng bước: hiện có gì, còn thiếu gì

| # | Bước | Hiện tại | Còn thiếu |
|---|---|---|---|
| 1 | **Run eval** — `scripts/evaluate_memory.py`, ba arm `full`/`ablated`/`control`, ghi `baselines/*.json` + `runs/*-detail.json` | Đã có; baseline đã ghi `system_prompt_sha` | — |
| 2 | **Report** — `scripts/build_memory_evaluation_report.py`, tiếng Việt, theo REPORT_FORMAT | Đã có | Fault class chưa hiện trong report (đã quyết: **không cần**, harness triage đã phủ) |
| 3 | **Fault attribution** — `memory_eval/fault.py`, suy ra prompt/memory từ verdict + ba arm | Đã có, 12 unit test; `run_failed` tách khỏi `not_attributable` | — |
| 4 | **Triage issues** — `scripts/triage_memory_evaluation.py`, mỗi probe còn mở nguyên nhân → một file `.md` kèm seed, expectation, reply ba arm | Đã có; baseline abort/rỗng nay exit non-zero | Không có test tự động |
| 5 | **Agent diagnosis** — coding agent đọc `ISSUES.md` từ trên xuống, điền triage record | Đã có (template nằm trong file issue) | — (cố ý thủ công, không tự động hoá) |
| 6 | **Prompt hypothesis** — viết file version trong `prompt-versioning/<slot>/vN-<date>.md` **trước khi chạy** | Chưa có file nào | Registry prompt, định danh version, scaffold ledger |
| 7 | **Ledger verdict** — `confirmed`/`refuted`/`inconclusive` + cái gì hiệu quả, cái gì không | Chưa có | Như trên |
| 8 | **Rerun đã duyệt** — chỉ chạy khi được duyệt chi phí | Đã có (chính là bước 1) | — |

Seam của prompt là **một chỗ duy nhất**: `_request_payload` trong `chat_reply.py` set
`payload["system"]`. Cả bốn provider đọc từ đó. Bất kỳ cơ chế version hoá nào cũng chỉ
cần chạm vào seam này.

> **Bẫy đặt tên.** `task_proposal.prompt_version` đã tồn tại trong chat contract với ý
> nghĩa khác và luật `null` cứng. Field mới **luôn** là `system_prompt_version`.

---

## 3. Ưu tiên — giữ / hoãn / bỏ

Thứ tự dưới đây là theo *giá trị trên mỗi đơn vị công sức*, không theo thứ tự vòng lặp.

| Ưu tiên | Việc | Công sức | Mất gì nếu không có | Quyết |
|---|---|---|---|---|
| **1** | Ghi `system_prompt_sha` (hash `_SYSTEM_INSTRUCTION`) vào baseline | ~10 dòng | Mọi run từ giờ đến lúc có nó **vĩnh viễn không so sánh được** — không backfill được | ✅ **Đã làm** |
| **2** | Fail lớn tiếng khi baseline `verdicts: []` hoặc `aborted: true` | ~10 dòng | Run hỏng đọc y hệt run sạch. Một file như vậy đang nằm sẵn trong `baselines/` | ✅ **Đã làm** |
| **3** | Tách `inconclusive` → `run_failed` (từ `unreadable`) và `not_attributable` | Nhỏ | Agent tốn thời gian đọc timeout; probe mơ hồ thật bị chôn giữa chúng | ✅ **Đã làm** |
| **4** | Ledger: viết **tay** file version đầu tiên | Một chu kỳ thật | Không có ledger thì mỗi chu kỳ mất phần "vì sao kỳ vọng hiệu quả" | **Giữ (viết tay trước)** |
| **5** | Generator/scaffold cho file ledger | Trung bình | Không mất gì khi mới có 1–2 version | **Hoãn** đến sau mục 4 |
| **6** | Prompt registry + version identity (label thay vì commit) | Lớn | Chỉ đau khi đang lặp nhiều version; hiện worklist dài đúng một probe | **Hoãn** |
| **7** | Langfuse trace metadata (`probe_id`, `arm`, `system_prompt_sha`) | Trung bình | So sánh version là việc dán bảng tính thay vì lọc | **Hoãn, phạm vi hẹp** |
| **8** | Fault class hiện trong report | Nhỏ | Không mất gì — harness triage đã phủ nhu cầu này | **Bỏ** |
| **9** | Langfuse Prompt Management làm nguồn sự thật | Lớn | Không mất gì cho đến khi tồn tại vài version | **Hoãn vô thời hạn** |
| **10** | Test tự động cho harness triage | Nhỏ–TB | Harness sinh ra tài liệu để người đọc, không sinh ra điểm số | **Hoãn** |

**Ba mục đầu là "20% bắt 80%".** Cả ba đều nhỏ, đều chống lại việc *đọc sai một kết quả*,
và đó mới là chế độ hỏng hay gặp — không phải việc thiếu hạ tầng version hoá.

---

## 4. Bằng chứng đứng sau thứ tự này

Từ run đã triage thật (`mistral-medium-latest`, probe set `v3_four_scopes_hard`,
`run_key 25bc26852ead`):

- 5 issue được sinh ra.
- **4/5 là `unreadable`** — lúc đó được đọc là provider rớt kết nối.
  **Cách đọc đó sai** — xem đính chính bên dưới.
- **1/5 là `prompt_fault` thật**: `ep_update_01` — arm `full` trả lời "5 tháng 9" trong khi
  cả hai arm mù đều từ chối. Trùng khớp với chẩn đoán tay trong báo cáo 2026-08-21.

**Đính chính (2026-08-23).** Các row `unreadable` đó không phải provider rớt. Trong
`chat_reply.stream_reply`, một `except` duy nhất bọc cả transport lẫn kiểm tra hợp đồng phản
hồi, nên một phản hồi bị từ chối vì sai hợp đồng cũng mang mã `chat_provider_unavailable`.
Sau khi tách hai loại lỗi và ghi log lý do, chạy lại `sem_recall_03` cho đúng một dòng:
`ValueError('citation_ids must match current project evidence')` — một **lỗi prompt**, sửa
trong `chat_reply` v2.

Kết luận rút ra vẫn giữ nguyên, nhưng vì một lý do khác: **chế độ hỏng hay gặp nhất là đọc
sai một kết quả, không phải bản thân kết quả.** Một mã lỗi gọi sai tên đã gửi việc chẩn đoán
đi nhầm hệ thống suốt hai run. Vì vậy ưu tiên 2 và 3 (nhận diện run hỏng, tách nó khỏi hàng
đợi chẩn đoán) vẫn đứng trên toàn bộ phần version hoá — cộng thêm một luật mới: **một mã lỗi
gộp hai nguyên nhân là một lỗi cần sửa, không phải một chi tiết cài đặt.**

Hai case tham chiếu đã được pin trong unit test:

- `ep_recall_01` — miss ở cả ba arm → `memory_fault`.
- `ep_update_01` / `ep_restraint_02` — arm `full` sai, hai arm mù sạch → `prompt_fault`.

Chi phí một run `v3`: 20 probe × 3 arm = 60 lượt gọi provider thật (~280 turn). Đây là lý
do bước (8) cần duyệt, và là lập luận chính trong mọi quyết định giữ/hoãn/bỏ ở trên.

---

## 5. Những gì đã cố ý **không** làm

Ghi lại để không bị đề xuất lại mỗi tháng.

- **LLM-as-a-Judge.** Judge chấm điểm một câu trả lời. Triage ở đây phải phân biệt hai cơ
  chế cho ra câu trả lời *trông giống hệt nhau*: retrieval đưa nhầm episode, so với
  generation đọc sai episode đúng. Bằng chứng phân biệt chúng nằm trong file (seed, mục
  đích probe, retrieval context), không nằm trong reply. Coding agent mở được file đó;
  judge thì đoán.
- **Langfuse Experiments / Datasets làm orchestrator.** Harness ablation vẫn là runner.
- **CI gating theo kết quả experiment.** Chưa có giá trị cho tới khi một version chứng
  minh được sự ổn định bằng tay.
- **Publish issue lên tracker ngoài (Linear, GitHub Issues).** Dev local; spec ở
  `tasks/specs/`, issue ở `runs/triage/<run_key>/`.
- **Tự động promote một prompt version lên label production.**
- **Chấm điểm / benchmark các triage agent với nhau.** Triage là *đầu vào* của giả thuyết;
  giả thuyết mới là thứ được kiểm chứng bằng run. Triage không sinh ra điểm số, nên việc
  nó không tái lập được là chấp nhận được.
- **Đổi bất cứ thứ gì trong retrieval, memory gateway, arm, hay probe set.** Toàn bộ công
  việc này là một *cách đọc* kết quả sẵn có cộng với một định danh version — nó không đổi
  cái đang được đo.

---

## 6. Hai mục [ASSUMED] — đã chốt

1. **Ngôn ngữ của ledger** — **văn xuôi tiếng Việt**, field key giữ tiếng Anh vì chúng là
   identifier. Cùng quy ước với `REPORT_FORMAT.md`.
2. **Holdout probe split** — **không làm.** v3 có 20 probe trên 4 scope, tức 5 probe mỗi
   scope; tách holdout còn 2–3 probe mỗi scope, không đỡ nổi bất kỳ khẳng định per-scope
   nào, trong khi hai run cùng cấu hình đã từng bất đồng 2/8 câu. Holdout cỡ đó đo nhiễu
   chứ không đo overfit.

   Thay thế, luật chống overfit là: ledger chỉ được ghi `confirmed` khi giả thuyết **lặp
   lại trên một run thứ hai** cùng cấu hình. Xem lại quyết định này khi có ≥3 prompt
   version hoặc probe set ≥40 probe.

---

## 7. Luật ràng buộc vòng lặp

Trích từ RUNBOOK, áp dụng nguyên vẹn cho mọi bước ở trên:

- Không bao giờ trỏ harness vào database remote hay production.
- Không bao giờ sửa code production để làm report xanh. Sửa một defect thật mà harness tìm
  ra là công việc; làm xanh bằng cách sửa thứ đang bị đo thì không.
- `runs/` (gồm `runs/triage/`) và `reports/*.md` đều gitignored vì chứa nguyên văn reply và
  seed. `baselines/` chỉ chứa metadata và được commit — có test bắt buộc điều này.
- Một run tại một thời điểm.
- Verdict tất định là thứ có thẩm quyền. Agent bất đồng thì **ghi lại** bất đồng, không sửa
  điểm.
- Mọi luận điểm trong triage phải trích dẫn được: một dòng seed, một expectation của probe,
  hoặc một reply của arm cụ thể.
