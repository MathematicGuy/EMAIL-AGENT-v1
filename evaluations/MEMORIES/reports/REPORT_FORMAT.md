# ĐỊNH DẠNG BÁO CÁO ĐÁNH GIÁ BỘ NHỚ (MEMORY EVALUATION REPORT FORMAT)

Tài liệu này định nghĩa cấu trúc chuẩn cho các báo cáo đánh giá bộ nhớ được lưu trữ tại `evaluations/MEMORIES/reports/<YYYY-MM-DD>-<probe-set>.md`. Báo cáo áp dụng mô hình Kim tự tháp (Pyramid Principle): ưu tiên kết quả cấp cao, bảng điểm định lượng và dữ liệu benchmark lên đầu; thông số kỹ thuật và nhật ký kiểm tra đưa về phụ lục cuối.

---

## Mẫu Cấu trúc Chuẩn (Standard Report Template)

```markdown
# Báo cáo Đánh giá Bộ nhớ: [Tên Probe Set / Mục tiêu đánh giá]

- **Ngày thực hiện**: YYYY-MM-DD
- **Probe Set ID**: `v1_four_scopes` (hoặc tên tập probe)
- **Backend lưu trữ**: SQLite scratch (`POSTGRES_MODE=off`) / PostgreSQL (`POSTGRES_MODE=local`)
- **Provider / Model**: `provider/model-name`

---

## 1. TỔNG QUAN KẾT QUẢ (EXECUTIVE SUMMARY)

### 1.1. Các chỉ số chính (Key Performance Indicators)
| Chỉ số | Giá trị | Đánh giá |
|---|---|---|
| **Tổng số câu hỏi (Probes)** | 8 probes (24 lượt gọi / 3 arms) | Đầy đủ 4 phạm vi bộ nhớ |
| **Tỉ lệ Trả lời Đúng (Pass Rate)** | X/8 (XX%) | Đo lường trên nhánh Full Context |
| **Quy gán Đúng Vùng Nhớ (Scope Earned-It)** | X/8 (XX%) | Chứng minh bộ nhớ thực sự cung cấp thông tin (P, F, F) |
| **Khả năng Kiềm chế / Chống ảo giác (Restraint)** | X/X (100%) | Từ chối trả lời khi không có dữ liệu |
| **Độ trễ trung bình (Avg Latency)** | XX.X giây / turn | Thời gian phản hồi trung bình |
| **Lỗi Seeding (Seed Failures)** | 0 | Tất cả vùng nhớ được nạp thành công |

### 1.2. Kết luận Cốt lõi (Bottom-line Verdict)
- [Tóm tắt 2-3 gạch đầu dòng về điểm mạnh của hệ thống bộ nhớ trong lần chạy này]
- [Tóm tắt các điểm bất thường, lỗi kỹ thuật hoặc độ lệch phân loại nếu có]

---

## 2. DỮ LIỆU BENCHMARK & SEEDING GROUND TRUTH (EVALUATION DATASET)

Mô tả tập dữ liệu mẫu được nạp vào hệ thống (Seeded State) và các câu hỏi kiểm thử (Golden Questions):

| Scope | Dữ liệu Seed đã nạp (Injected Memory) | Câu hỏi kiểm thử (Probe Question) | Kỳ vọng (Ground Truth) | Mục đích đo |
|---|---|---|---|---|
| **`short_term`** | Gia hạn CCCD Đà Nẵng, hạn đổi từ T3 $\to$ T4 | *Yêu cầu gia hạn đó là cho văn phòng nào?*<br>*Hạn chót là khi nào?* | Đà Nẵng<br>thứ Tư | Recall (lấy lại)<br>Update (cập nhật mới) |
| **`long_term`** | Profile: Biệt danh *Hải Âu*, tone *ngắn gọn* | *Tôi đã đặt bạn ở vai trò nào khi trả lời?*<br>*Chức danh của tôi là gì?* | Hải Âu<br>Từ chối (không có chức danh) | Recall (profile)<br>Restraint (chống bịa) |
| **`episodic`** | Task episode: Gia hạn CCCD Đà Nẵng (đã duyệt) | *Tác vụ trước về CCCD là cho văn phòng nào?*<br>*Số hồ sơ trên tác vụ trước là bao nhiêu?* | Đà Nẵng<br>Từ chối (không có số hồ sơ) | Recall (lịch sử task)<br>Restraint (chống bịa) |
| **`semantic`** | Corpus tài liệu công ty (OT-114, Nghỉ phép) | *Đề nghị làm thêm giờ qua biểu mẫu nào?*<br>*Chính sách nghỉ dài hạn sabbatical?* | OT-114<br>Từ chối (không có sabbatical) | Recall (vector RAG)<br>Restraint (chống bịa) |

---

## 3. BẢNG ĐIỂM ĐỊNH LƯỢNG CHI TIẾT (SCORECARD BY SCOPE)

| Scope | Số Probe | Full Pass | Earned It (P,F,F) | Did Nothing (P,P,P) | Unreadable | Dangerous | Đánh giá Trạng thái |
|---|---|---|---|---|---|---|---|
| **`short_term`** | 2 | 2/2 (100%) | X | X | X | 0 | 🟢 / 🟡 / 🔴 |
| **`long_term`** | 2 | 2/2 (100%) | X | X | X | 0 | 🟢 / 🟡 / 🔴 |
| **`episodic`** | 2 | 2/2 (100%) | X | X | X | 0 | 🟢 / 🟡 / 🔴 |
| **`semantic`** | 2 | X/2 (XX%) | X | X | X | X | 🟢 / 🟡 / 🔴 |
| **TỔNG CỘNG** | **8** | **X/8 (XX%)** | **X** | **X** | **X** | **X** | **Ổn định / Cần tối ưu** |

---

## 4. MA TRẬN 3-ARM & PHÂN TÍCH CHẤT LƯỢNG (QUALITATIVE & VERDICTS)

### 4.1. Bảng Ma trận 3-Arm Verdicts (Worst First)
| Probe ID | Target Scope | Loại bài test | Verdict | Full Arm | Ablated Arm | Control Arm | Certain? | Latency |
|---|---|---|---|---|---|---|---|---|
| `...` | `...` | recall / restraint / update | `...` | pass / miss / invented / no_answer | pass / miss | pass / miss / no_answer | true / false | XXs |

### 4.2. Giải trình chi tiết các trường hợp Bất thường / Cần xem xét (Needs Reading)
*Trích dẫn nguyên văn phản hồi của các nhánh Full, Ablated, Control và chẩn đoán tự động 2 tầng.*

#### Probe `[Tên Probe]` (`targets: [scope]`, `verdict: [verdict]`, `certain: [true/false]`)
- **Câu hỏi**: *"[Nội dung câu hỏi]"*
- **Phản hồi Full Arm**:
  > *"[Trích dẫn câu trả lời]"*
- **Phản hồi Ablated Arm**:
  > *"[Trích dẫn]"*
- **Phản hồi Control Arm**:
  > *"[Trích dẫn]"*
- **Chẩn đoán (Deterministic Diagnosis)**: 🔴/🟡/🟢 `[Concern / Trạng thái]`
  - *Tổng quan*: [Giải thích ngắn gọn 1 câu bằng tiếng Việt dễ hiểu cho người mới]
  - *Chi tiết kỹ thuật*: [Giải thích cụ thể trace kỹ thuật cho kỹ sư và coding agent]

---

## 5. PHÂN LOẠI LỖI & ĐỀ XUẤT HÀNH ĐỘNG (DEFECTS & ACTION ITEMS)

Phân loại theo 4 nhóm nguyên nhân tại RUNBOOK §5:
- **Concern A (The Grader)**: Cải tiến logic chấm điểm, mở rộng regex từ chối.
- **Concern B (The Question)**: Điều chỉnh câu hỏi nếu quá dễ đoán (guessable).
- **Concern C (Plumbing / Harness)**: Kiểm tra cơ chế mask, seeding hoặc lỗi kết nối mạng Provider.
- **Concern D (Product)**: Sửa đổi logic sản xuất nếu bộ nhớ không tìm thấy dữ liệu hoặc bị ảo giác.

---

## PHỤ LỤC: THÔNG SỐ KỸ THUẬT & KIỂM TRA MÔI TRƯỜNG (TECHNICAL APPENDIX)

### A.1. Thông số Thực thi (Run Artifacts)
- **Baseline JSON**: `evaluations/MEMORIES/baselines/...`
- **Detail Transcript**: `evaluations/MEMORIES/runs/...`
- **Provider / Model**: `...`
- **Target Backend**: `SQLite scratch` / `PostgreSQL`
- **Run Key**: `...`
- **Nonce**: `...`
- **Thời gian chạy**: `...`
```
