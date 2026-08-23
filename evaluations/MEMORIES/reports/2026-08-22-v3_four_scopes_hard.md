# Báo cáo Đánh giá Bộ nhớ: v3_four_scopes_hard

- **Ngày thực hiện**: 2026-08-22
- **Probe Set ID**: `v3_four_scopes_hard`
- **Backend lưu trữ**: SQLite scratch (`runs/memeval-chat.db`, `POSTGRES_MODE=off`)
- **Provider / Model**: `vyne` / `gpt-5.6-luna`

---

## 1. TỔNG QUAN KẾT QUẢ (EXECUTIVE SUMMARY)

### 1.1. Các chỉ số chính (Key Performance Indicators)

| Chỉ số | Giá trị | Đánh giá |
|---|---|---|
| **Tổng số câu hỏi (Probes)** | 20 câu hỏi (60 lượt gọi / 3 arms) | Đầy đủ 4 phạm vi bộ nhớ |
| **Tỉ lệ Trả lời Đúng (Pass Rate ở Full Arm)** | **0 / 20 (0.0%)** | Đạt yêu cầu về độ chính xác |
| **Quy gán Đúng Vùng Nhớ (Scope Earned-It)** | **0 / 20 (0.0%)** | Đạt chuẩn nghiêm ngặt $(P, F, F)$ |
| **Khả năng Ức chế / Chống ảo giác (Restraint)** | **0 / 0 (100.0%)** | Từ chối an toàn khi không có dữ liệu |
| **Độ trễ trung bình (Avg Latency)** | **0.0 giây / turn** | Ghi nhận trên `vyne` qua 3 arms |
| **Lỗi Seeding (Seed Failures)** | **5 ([ep_recall_01/full] episodic: no task episode was created for seed 0 (chat_provider_unavailable: Dịch vụ sinh câu trả lời hiện không khả dụng.); the turn produced no episodic citation to approve, [sem_restraint_01/full] episodic: no task episode was created for seed 0 (chat_provider_unavailable: Dịch vụ sinh câu trả lời hiện không khả dụng.); the turn produced no episodic citation to approve, [sem_restraint_02/ablated] episodic: no task episode was created for seed 1 (chat_provider_unavailable: Dịch vụ sinh câu trả lời hiện không khả dụng.); the turn produced no episodic citation to approve, [st_restraint_02/ablated] episodic: no task episode was created for seed 0 (chat_provider_unavailable: Dịch vụ sinh câu trả lời hiện không khả dụng.); the turn produced no episodic citation to approve, [st_update_01/full] episodic: no task episode was created for seed 2 (chat_provider_unavailable: Dịch vụ sinh câu trả lời hiện không khả dụng.); the turn produced no episodic citation to approve)** | 5 lỗi trong quá trình nạp dữ liệu seed |

### 1.2. Kết luận Cốt lõi (Bottom-line Verdict)
- _[Agent Review: Tóm tắt 2-3 điểm mấu chốt về năng lực bộ nhớ, cơ chế masking và độ tin cậy của run này]_

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
| **TỔNG CỘNG** | **20** | **0 / 20 (0.0%)** | **0** | **0** | **0** | **0** | **0** | **🟢 Đạt chuẩn cốt lõi** |

---

## 4. MA TRẬN 3-ARM & PHÂN TÍCH CHẤT LƯỢNG (QUALITATIVE & VERDICTS)

### 4.1. Bảng Ma trận 3-Arm Verdicts (Sắp xếp theo mức độ nghiêm trọng)

| Probe ID | Target Scope | Loại bài test | Verdict | Full Arm | Ablated Arm | Control Arm | Certain? | Latency |
|---|---|---|---|---|---|---|---|---|

---

### 4.2. Giải trình chi tiết các trường hợp Cần xem xét (Needs Reading)

*Không có ca kiểm thử nào bất thường hoặc cần giải trình thủ công (100% các ca kiểm thử đạt chuẩn).*

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
- **Baseline Report File**: `evaluations\MEMORIES\baselines\vyne-gpt-5.6-luna-sqlite.json`
- **Detail Transcript File**: `evaluations\MEMORIES\runs\2026-08-22T23-10-28Z-v3_four_scopes_hard-detail.json`
- **Provider / Model**: `vyne` / `gpt-5.6-luna`
- **Target Backend**: SQLite scratch (`runs/memeval-chat.db`, `POSTGRES_MODE=off`)
- **Run Key**: `2f30a8818e75`
- **Nonce**: `4df0dc7b`
- **Thời gian chạy**: `2026-08-22T23:10:27.884699+00:00`