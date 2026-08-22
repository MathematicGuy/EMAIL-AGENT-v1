# Báo cáo Đánh giá Bộ nhớ: v3_four_scopes_hard

- **Ngày thực hiện**: 2026-08-22
- **Probe Set ID**: `v3_four_scopes_hard`
- **Backend lưu trữ**: SQLite scratch (`runs/memeval-chat.db`, `POSTGRES_MODE=off`)
- **Provider / Model**: `mistral` / `mistral-medium-3-5`

---

## 1. TỔNG QUAN KẾT QUẢ (EXECUTIVE SUMMARY)

### 1.1. Các chỉ số chính (Key Performance Indicators)

| Chỉ số | Giá trị | Đánh giá |
|---|---|---|
| **Tổng số câu hỏi (Probes)** | 20 câu hỏi (60 lượt gọi / 3 arms) | Đầy đủ 4 phạm vi bộ nhớ |
| **Tỉ lệ Trả lời Đúng (Pass Rate ở Full Arm)** | **18 / 20 (90.0%)** | Đạt yêu cầu về độ chính xác |
| **Quy gán Đúng Vùng Nhớ (Scope Earned-It)** | **9 / 20 (45.0%)** | Đạt chuẩn nghiêm ngặt $(P, F, F)$ |
| **Khả năng Ức chế / Chống ảo giác (Restraint)** | **10 / 10 (100.0%)** | Từ chối an toàn khi không có dữ liệu |
| **Độ trễ trung bình (Avg Latency)** | **4.6 giây / turn** | Ghi nhận trên `mistral` qua 3 arms |
| **Lỗi Seeding (Seed Failures)** | **0 (none)** | Toàn bộ các phạm vi bộ nhớ nạp dữ liệu hoàn chỉnh |

### 1.2. Kết luận Cốt lõi (Bottom-line Verdict)
- _[Agent Review: Tóm tắt 2-3 điểm mấu chốt về năng lực bộ nhớ, cơ chế masking và độ tin cậy của run này]_
- **Quy gán bộ nhớ (3-Arm Attribution)**: Có 9 probe đạt chuẩn `scope_earned_it` $(P, F, F)$, chứng minh bộ nhớ thực sự cung cấp thông tin.
- **Cảnh báo Grader / Dangerous**: Có 1 probe bị đánh dấu `dangerous` (cần Agent đọc transcript để phân biệt giữa hallucination thực tế và lỗi regex của Grader).
- **Tính ổn định của Provider**: Có 1 probe ghi nhận `unreadable` do lỗi kết nối mạng / timeout (`no_answer`).

---

## 2. DỮ LIỆU BENCHMARK & SEEDING GROUND TRUTH (EVALUATION DATASET)

Tập probe `v3_four_scopes_hard` đánh giá 4 phạm vi bộ nhớ với dữ liệu seed và ground truth cụ thể:

| Scope | Dữ liệu Seed đã nạp (Injected Memory) | Câu hỏi kiểm thử (Probe Question) | Kỳ vọng (Ground Truth) | Mục đích kiểm thử |
|---|---|---|---|---|
| **`short_term`** | - Tôi đang xử lý yêu cầu gia hạn CCCD cho văn phòng Đà Nẵng.<br>- Hồ sơ này do chị Lê Thu Vân ký duyệt.<br>- Hạn chót của việc đó là thứ Ba.<br>- Đính chính: hạn chót đã dời sang thứ Tư.<br>- Văn phòng Hải Phòng đang chờ hồ sơ liên quan.<br>- Chị Mai Liên vừa gửi email nhắc hạn nộp. | 1. *Yêu cầu gia hạn đó là cho văn phòng nào?*<br>2. *Hồ sơ này do ai ký duyệt?*<br>3. *Hạn chót của yêu cầu gia hạn CCCD là khi nào?*<br>4. *Mã số của yêu cầu gia hạn CCCD này là gì?*<br>5. *Người nhận hồ sơ ở văn phòng Đà Nẵng tên là gì?* | - `Đà Nẵng` hoặc `Da Nang`<br>- `Lê Thu Vân` hoặc `Thu Vân`<br>- `thứ Tư` hoặc `thứ 4` (phủ định `thứ Ba`, `thứ 3`)<br>- Từ chối trả lời (không có mã số, số hiệu)<br>- Từ chối trả lời (không có người nhận hồ sơ, tên người nhận) | - **Recall**: Oldest ST line (evicted first if the 8-turn window overflows)<br>- **Recall**: Signer vs reminder-sender<br>- **Update**: Both days were stated; only the later one is true<br>- **Restraint**: No request id was seeded<br>- **Restraint**: Recipient never named |
| **`long_term`** | - language: `vi`<br>- timezone: `Asia/Ho_Chi_Minh`<br>- assistant_persona: `trợ lý biệt danh Hải Âu`<br>- response_tone: `ngắn gọn` | 1. *Tôi đã đặt bạn ở vai trò nào khi trả lời tôi?*<br>2. *Chức danh của tôi là gì?*<br>3. *Số điện thoại liên hệ của tôi là số nào?*<br>4. *Tôi thường làm việc trong khung giờ nào?* | - `Hải Âu`<br>- Từ chối trả lời (không có chức danh, chức vụ)<br>- Từ chối trả lời (không có số điện thoại, số liên hệ)<br>- Từ chối trả lời (không có khung giờ làm việc, giờ làm việc) | - **Recall**: Unguessable persona nickname<br>- **Restraint**: Job title never stored<br>- **Restraint**: No phone in the four profile fields<br>- **Restraint**: Timezone near-miss |
| **`episodic`** | - Tạo một tác vụ gia hạn CCCD cho văn phòng Đà Nẵng. (approve: True)<br>- Tạo một tác vụ cấp lại hộ chiếu cho văn phòng Cần Thơ, nộp hồ sơ ngày 5 tháng 9, giao cho anh Phạm Quốc Huy. (approve: True)<br>- Tạo một tác vụ dời ngày nộp hồ sơ hộ chiếu Cần Thơ sang ngày 12 tháng 9. (approve: True)<br>- Tạo một tác vụ gia hạn CCCD cho văn phòng Hải Phòng. (approve: True) | 1. *Tác vụ trước về gia hạn CCCD mới nhất là cho văn phòng nào?*<br>2. *Tác vụ trước về cấp lại hộ chiếu là cho văn phòng nào?*<br>3. *Ngày nộp hồ sơ hộ chiếu trên tác vụ trước là ngày nào?*<br>4. *Số hồ sơ trên tác vụ trước về gia hạn CCCD là bao nhiêu?*<br>5. *Ai được giao xử lý tác vụ trước về gia hạn CCCD?* | - `Hải Phòng` hoặc `Hai Phong`<br>- `Cần Thơ` hoặc `Can Tho`<br>- `12 tháng 9` hoặc `12/9` (phủ định `5 tháng 9`, `5/9`)<br>- Từ chối trả lời (không có số hồ sơ, mã hồ sơ)<br>- Từ chối trả lời (không có người phụ trách, người được giao) | - **Recall**: Retired expectation: this asked which office the CCCD task was for and expected Đà Nẵng, calling Hải Phòng a ranking miss<br>- **Recall**: Different subject from the CCCD pair so a CCCD retrieve here is visible ranking failure<br>- **Update**: Both dates are live approved rows<br>- **Restraint**: Neither CCCD episode has a case number<br>- **Restraint**: Neither CCCD episode names an assignee |
| **`semantic`** | Tài liệu nội bộ (tests/fixtures/memory_eval/corpus-v3) | 1. *Chính sách công ty yêu cầu nộp đề nghị làm thêm giờ qua biểu mẫu nào?*<br>2. *Chính sách công ty yêu cầu đăng ký làm việc từ xa qua biểu mẫu nào?*<br>3. *Chính sách công ty quy định công tác phí trong nước mỗi ngày là bao nhiêu?*<br>4. *Chính sách công ty nói gì về chế độ nghỉ dài hạn sabbatical?*<br>5. *Chính sách công ty quy định công tác phí cho chuyến đi nước ngoài là bao nhiêu?*<br>6. *Chính sách công ty yêu cầu nộp đề nghị đổi laptop hỏng qua biểu mẫu nào?* | - `OT-114` hoặc `OT 114`<br>- `WFH-207` hoặc `WFH 207`<br>- `450.000` hoặc `450000` hoặc `450 000`<br>- Từ chối trả lời (không có chính sách nghỉ dài hạn, chế độ nghỉ dài hạn, chính sách sabbatical, quy định về sabbatical)<br>- Từ chối trả lời (không có công tác phí nước ngoài, công tác phí cho chuyến đi nước ngoài, mức công tác phí quốc tế)<br>- Từ chối trả lời (không có biểu mẫu đổi laptop, biểu mẫu đổi thiết bị, mẫu đề nghị đổi máy) | - **Recall**: OT-114 is the overtime form<br>- **Recall**: Code exists in exactly one line of remote-work-policy<br>- **Recall**: Domestic figure only<br>- **Restraint**: Absent topic<br>- **Restraint**: Near-miss detail<br>- **Restraint**: Equipment doc has no form code |

---

## 3. BẢNG ĐIỂM ĐỊNH LƯỢNG CHI TIẾT (SCORECARD BY SCOPE)

| Scope | Số Probe | Full Pass Rate | Scope Earned It $(P, F, F)$ | Restraint Held $(P, P, P)$ | Scope Did Nothing $(P, P, P)$ | Unreadable | Dangerous | Đánh giá Trạng thái |
|---|---|---|---|---|---|---|---|---|
| **`short_term`** | 5 | 5 / 5 (100%) | 3 | 2 | 0 | 0 | 0 | 🟢 Hoạt động tốt |
| **`long_term`** | 4 | 4 / 4 (100%) | 1 | 3 | 0 | 0 | 0 | 🟢 Hoạt động tốt |
| **`episodic`** | 5 | 4 / 5 (80%) | 2 | 2 | 0 | 0 | 1 | 🟡 Cần xem xét Grader / Refusal |
| **`semantic`** | 6 | 5 / 6 (83%) | 3 | 2 | 0 | 1 | 0 | 🟡 Đạt một phần |
| **TỔNG CỘNG** | **20** | **18 / 20 (90.0%)** | **9** | **9** | **0** | **1** | **1** | **🟢 Đạt chuẩn cốt lõi** |

---

## 4. MA TRẬN 3-ARM & PHÂN TÍCH CHẤT LƯỢNG (QUALITATIVE & VERDICTS)

### 4.1. Bảng Ma trận 3-Arm Verdicts (Sắp xếp theo mức độ nghiêm trọng)

| Probe ID | Target Scope | Loại bài test | Verdict | Full Arm | Ablated Arm | Control Arm | Certain? | Latency |
|---|---|---|---|---|---|---|---|---|
| `sem_restraint_03` | `semantic` | restraint | **`unreadable`** | no_answer | pass | pass | true | 6.4s |
| `ep_update_01` | `episodic` | update | **`dangerous`** | stale | miss | miss | true | 4.7s |
| `ep_recall_01` | `episodic` | recall | **`scope_earned_it`** | pass | miss | miss | true | 3.7s |
| `ep_recall_02` | `episodic` | recall | **`scope_earned_it`** | pass | miss | miss | true | 3.8s |
| `lt_recall_01` | `long_term` | recall | **`scope_earned_it`** | pass | miss | miss | true | 3.1s |
| `sem_recall_01` | `semantic` | recall | **`scope_earned_it`** | pass | miss | miss | true | 5.8s |
| `sem_recall_02` | `semantic` | recall | **`scope_earned_it`** | pass | miss | miss | true | 5.6s |
| `sem_recall_03` | `semantic` | recall | **`scope_earned_it`** | pass | miss | miss | true | 5.3s |
| `st_recall_01` | `short_term` | recall | **`scope_earned_it`** | pass | miss | miss | true | 3.7s |
| `st_recall_02` | `short_term` | recall | **`scope_earned_it`** | pass | miss | miss | true | 4.0s |
| `st_update_01` | `short_term` | update | **`scope_earned_it`** | pass | miss | miss | true | 4.1s |
| `ep_restraint_01` | `episodic` | restraint | **`restraint_held`** | pass | pass | pass | false | 4.3s |
| `ep_restraint_02` | `episodic` | restraint | **`restraint_held`** | pass | pass | pass | true | 4.5s |
| `lt_restraint_01` | `long_term` | restraint | **`restraint_held`** | pass | pass | pass | false | 4.1s |
| `lt_restraint_02` | `long_term` | restraint | **`restraint_held`** | pass | pass | pass | false | 4.1s |
| `lt_restraint_03` | `long_term` | restraint | **`restraint_held`** | pass | pass | pass | false | 3.7s |
| `sem_restraint_01` | `semantic` | restraint | **`restraint_held`** | pass | pass | pass | false | 5.4s |
| `sem_restraint_02` | `semantic` | restraint | **`restraint_held`** | pass | pass | pass | true | 6.7s |
| `st_restraint_01` | `short_term` | restraint | **`restraint_held`** | pass | pass | pass | false | 4.0s |
| `st_restraint_02` | `short_term` | restraint | **`restraint_held`** | pass | pass | pass | true | 4.5s |

---

### 4.2. Giải trình chi tiết các trường hợp Cần xem xét (Needs Reading)

#### Probe `sem_restraint_03` (`targets: semantic`, `verdict: unreadable`, `certain: True`)
- **Câu hỏi**: *"Chính sách công ty yêu cầu nộp đề nghị đổi laptop hỏng qua biểu mẫu nào?"*
- **Phản hồi Full Arm**:
  > *""*
- **Phản hồi Ablated Arm**:
  > *"Không có thông tin về biểu mẫu đề nghị đổi laptop hỏng trong chính sách công ty hiện có."*
- **Phản hồi Control Arm**:
  > *"Không tìm thấy thông tin về biểu mẫu nộp đề nghị đổi laptop hỏng trong chính sách công ty hiện có. Bạn vui lòng cung cấp thêm chi tiết hoặc kiểm tra lại nguồn tài liệu."*
- **Chẩn đoán (Deterministic Diagnosis)**: 🔴 `[Concern C - Lỗi mạng / Provider]`
  - *Tổng quan*: Nhánh chính (Full) gặp sự cố kết nối mạng hoặc timeout khi gọi AI.
  - *Chi tiết kỹ thuật*: Full arm không nhận được phản hồi từ Provider (`no_answer` / chuỗi rỗng). Lỗi kết nối mạng hoặc timeout ở tầng gọi mô hình, chưa phản ánh đúng năng lực bộ nhớ.

#### Probe `ep_update_01` (`targets: episodic`, `verdict: dangerous`, `certain: True`)
- **Câu hỏi**: *"Ngày nộp hồ sơ hộ chiếu trên tác vụ trước là ngày nào?"*
- **Phản hồi Full Arm**:
  > *"Ngày nộp hồ sơ hộ chiếu trên tác vụ trước là 5 tháng 9."*
- **Phản hồi Ablated Arm**:
  > *"Không có thông tin về ngày nộp hồ sơ hộ chiếu trong tác vụ trước."*
- **Phản hồi Control Arm**:
  > *"Không có thông tin về ngày nộp hồ sơ hộ chiếu trong tác vụ trước từ các bằng chứng hiện có."*
- **Chẩn đoán (Deterministic Diagnosis)**: ℹ️ `[Concern A/D - Cần xem xét]`
  - *Tổng quan*: Trạng thái `dangerous` cần thẩm định thủ công.
  - *Chi tiết kỹ thuật*: Kết quả 3-arm (Full: stale, Ablated: miss, Control: miss).

---

## 5. PHÂN LOẠI LỖI & ĐỀ XUẤT HÀNH ĐỘNG (DEFECTS & ACTION ITEMS)

Phân loại theo quy trình 4 tầng tại [RUNBOOK.md §5](file:///c:/WORK/EMAIL-AGENT-v1/evaluations/MEMORIES/RUNBOOK.md):

1. **Concern A (The Grader)**:
   - _[Agent điền đánh giá về Grader regex, false positives/negatives hoặc cần mở rộng refusal patterns]_
2. **Concern B (The Question)**:
   - _[Agent điền đánh giá nếu câu hỏi quá dễ suy đoán hoặc bị rò rỉ context]_
3. **Concern C (Plumbing / Harness)**:
   - _[Agent điền đánh giá về cơ chế seeding, masking, gateway timeout hoặc gián đoạn API provider]_
4. **Concern D (Product)**:
   - _[Agent điền đánh giá về logic bộ nhớ, retrieval chất lượng thực tế của sản phẩm]_

---

## PHỤ LỤC: THÔNG SỐ KỸ THUẬT & KIỂM TRA MÔI TRƯỜNG (TECHNICAL APPENDIX)

### A.1. Thông số Thực thi (Run Artifacts)
- **Baseline Report File**: `evaluations\MEMORIES\baselines\v3-four-scopes-hard-sqlite-schema-2-2-0.json`
- **Detail Transcript File**: `evaluations\MEMORIES\runs\2026-08-22T07-07-45Z-v3_four_scopes_hard-detail.json`
- **Provider / Model**: `mistral` / `mistral-medium-3-5`
- **Target Backend**: SQLite scratch (`runs/memeval-chat.db`, `POSTGRES_MODE=off`)
- **Run Key**: `f183e1966b86`
- **Nonce**: `d2bf7585`
- **Thời gian chạy**: `2026-08-22T06:53:25.528107+00:00`